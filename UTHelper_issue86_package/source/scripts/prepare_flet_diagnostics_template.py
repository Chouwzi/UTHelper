"""Prepare the reviewed Flet 0.86.5 runner with a Windows crash bridge."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
import os
import stat
import sys
import tempfile
import zipfile


REVIEWED_OFFICIAL_TEMPLATE_SHA256 = (
    "8f95dc20ef6d901d9b5ee59f00e33d19f1d2bc6be8d6d3b800c4aab3d7315b73"
)
OFFICIAL_TEMPLATE_SHA256 = REVIEWED_OFFICIAL_TEMPLATE_SHA256
PATCHED_TEMPLATE_SHA256 = (
    "f44e29a58394f7e5a47a72bee1a54033106cef2a43b94e7b06e58bb464630a00"
)
TARGET_MEMBER = "build/{{cookiecutter.out_dir}}/lib/main.dart"
MAX_ARCHIVE_MEMBERS = 2048
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_ZIP_COMMENT = b"UTHelper diagnostics template v1\n"

_IMPORT_ANCHOR = "import 'dart:async';\nimport 'dart:io';\nimport 'dart:ui';\n"
_IMPORT_REPLACEMENT = (
    "import 'dart:async';\n"
    "import 'dart:convert';\n"
    "import 'dart:io';\n"
    "import 'dart:ui';\n"
)
_MAIN_ANCHOR = "void main(List<String> args) async {\n"
_PATCH_BEGIN = "// UTHELPER_DIAGNOSTICS_PATCH_BEGIN_V1"
_PATCH_END = "// UTHELPER_DIAGNOSTICS_PATCH_END_V1"
_INSTALL_CALL = "  _utHelperInstallFlutterDiagnostics();\n"
_PHASE_TIMER = "  Timer.run(() => _utHelperFlutterPhase = 'gui');\n"

_DART_HELPERS = r"""// UTHELPER_DIAGNOSTICS_PATCH_BEGIN_V1
const int _utHelperFlutterBridgeMaxBytes = 64 * 1024;
bool _utHelperFlutterDiagnosticsInstalled = false;
String _utHelperFlutterPhase = 'boot';

String _utHelperSafeSymbol(Object? value, {String fallback = 'unknown'}) {
  final raw = value?.toString() ?? '';
  final normalized = raw
      .replaceAll(RegExp(r'[^A-Za-z0-9_.<>-]+'), '_')
      .replaceAll(RegExp(r'^[._-]+|[._-]+$'), '');
  if (normalized.isEmpty) return fallback;
  final prefixed = RegExp(r'^[A-Za-z_]').hasMatch(normalized)
      ? normalized
      : '_$normalized';
  return prefixed.length <= 128 ? prefixed : prefixed.substring(0, 128);
}

List<String> _utHelperStackSymbols(StackTrace? stack) {
  if (stack == null) return const <String>[];
  final symbols = <String>[];
  final matcher = RegExp(r'^\s*#\d+\s+([A-Za-z0-9_.$<>-]+)');
  for (final line in stack.toString().split('\n')) {
    final match = matcher.firstMatch(line);
    if (match == null) continue;
    final symbol = _utHelperSafeSymbol(match.group(1));
    if (symbol != 'unknown') symbols.add(symbol);
    if (symbols.length == 16) break;
  }
  return symbols;
}

String _utHelperCoarseUtcMinute() {
  final now = DateTime.now().toUtc();
  return DateTime.utc(now.year, now.month, now.day, now.hour, now.minute)
      .toIso8601String();
}

void _utHelperWriteFlutterError(
  Object error,
  StackTrace? stack,
) {
  if (!Platform.isWindows) return;
  try {
    final appData = Platform.environment['APPDATA'];
    if (appData == null || appData.isEmpty) return;
    final appRoot = Directory('$appData\\UTHelper');
    appRoot.createSync(recursive: true);
    if (FileSystemEntity.typeSync(appRoot.path, followLinks: false) !=
        FileSystemEntityType.directory) {
      return;
    }
    final directory = Directory('${appRoot.path}\\diagnostics');
    directory.createSync(recursive: true);
    if (FileSystemEntity.typeSync(directory.path, followLinks: false) !=
        FileSystemEntityType.directory) {
      return;
    }
    final bridge = File('${directory.path}\\flutter-errors.jsonl');
    final bridgeType =
        FileSystemEntity.typeSync(bridge.path, followLinks: false);
    if (bridgeType != FileSystemEntityType.notFound &&
        bridgeType != FileSystemEntityType.file) {
      return;
    }
    final record = '${jsonEncode(<String, Object>{
      'runtime_type': _utHelperSafeSymbol(error.runtimeType),
      'symbols': _utHelperStackSymbols(stack),
      'phase': _utHelperFlutterPhase,
      'occurred_at': _utHelperCoarseUtcMinute(),
    })}\n';
    final encodedLength = utf8.encode(record).length;
    if (encodedLength > _utHelperFlutterBridgeMaxBytes) return;
    var currentLength = 0;
    if (bridge.existsSync()) currentLength = bridge.lengthSync();
    final append = currentLength + encodedLength <=
        _utHelperFlutterBridgeMaxBytes;
    bridge.writeAsStringSync(
      record,
      mode: append ? FileMode.append : FileMode.write,
      flush: true,
    );
  } catch (_) {
    // Diagnostics must never interfere with the Flutter runner.
  }
}

void _utHelperInstallFlutterDiagnostics() {
  if (!Platform.isWindows || _utHelperFlutterDiagnosticsInstalled) return;
  _utHelperFlutterDiagnosticsInstalled = true;
  final previousFlutterHandler = FlutterError.onError;
  FlutterError.onError = (FlutterErrorDetails details) {
    _utHelperWriteFlutterError(details.exception, details.stack);
    if (previousFlutterHandler != null) {
      previousFlutterHandler(details);
    } else {
      FlutterError.presentError(details);
    }
  };
  final previousPlatformHandler = PlatformDispatcher.instance.onError;
  PlatformDispatcher.instance.onError = (Object error, StackTrace stack) {
    _utHelperWriteFlutterError(error, stack);
    return previousPlatformHandler?.call(error, stack) ?? false;
  };
}
// UTHELPER_DIAGNOSTICS_PATCH_END_V1

"""


class UnknownRunnerTemplate(RuntimeError):
    """The input is not the exact reviewed Flet runner template."""


class InvalidPreparedTemplate(RuntimeError):
    """The prepared ZIP or generated project does not match the reviewed patch."""


@dataclass(frozen=True, slots=True)
class PreparedTemplate:
    official_template_sha256: str
    output_sha256: str
    target_member: str = TARGET_MEMBER


@dataclass(frozen=True, slots=True)
class _Member:
    name: str
    data: bytes
    is_directory: bool


def _digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _safe_member_name(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name:
        return False
    candidate = PurePosixPath(name)
    if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        return False
    return not candidate.parts[0].endswith(":")


def _read_members(source_zip: Path) -> list[_Member]:
    try:
        archive = zipfile.ZipFile(source_zip)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UnknownRunnerTemplate("official Flet template is not a valid ZIP") from exc
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_ARCHIVE_MEMBERS:
            raise UnknownRunnerTemplate("official Flet template has an unsafe member count")
        seen: set[str] = set()
        total = 0
        members: list[_Member] = []
        for info in infos:
            name = info.filename
            if not _safe_member_name(name):
                raise UnknownRunnerTemplate("official Flet template has an unsafe member")
            if name in seen:
                raise UnknownRunnerTemplate("official Flet template has a duplicate member")
            seen.add(name)
            mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            is_directory = info.is_dir()
            if info.flag_bits & 0x1:
                raise UnknownRunnerTemplate("official Flet template has an unsafe encrypted member")
            if file_type == stat.S_IFLNK or (
                file_type not in (0, stat.S_IFREG, stat.S_IFDIR)
            ):
                raise UnknownRunnerTemplate("official Flet template has an unsafe member type")
            if is_directory != (file_type == stat.S_IFDIR) and file_type != 0:
                raise UnknownRunnerTemplate("official Flet template has inconsistent member metadata")
            if info.file_size > MAX_MEMBER_BYTES:
                raise UnknownRunnerTemplate("official Flet template member is oversized")
            total += info.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise UnknownRunnerTemplate("official Flet template is oversized")
            try:
                data = b"" if is_directory else archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise UnknownRunnerTemplate("official Flet template member is unreadable") from exc
            if len(data) != info.file_size:
                raise UnknownRunnerTemplate("official Flet template member size changed")
            members.append(_Member(name=name, data=data, is_directory=is_directory))
    return members


def _patch_main(source: bytes) -> bytes:
    try:
        text = source.decode("utf-8")
    except UnicodeError as exc:
        raise UnknownRunnerTemplate("Flet runner source is not UTF-8") from exc
    if (
        text.count(_IMPORT_ANCHOR) != 1
        or text.count(_MAIN_ANCHOR) != 1
        or _PATCH_BEGIN in text
        or _PATCH_END in text
        or "import 'dart:convert';" in text
    ):
        raise UnknownRunnerTemplate("Flet runner anchor changed; refusing diagnostics patch")
    patched = text.replace(_IMPORT_ANCHOR, _IMPORT_REPLACEMENT, 1)
    patched = patched.replace(
        _MAIN_ANCHOR,
        f"{_DART_HELPERS}{_MAIN_ANCHOR}{_INSTALL_CALL}{_PHASE_TIMER}",
        1,
    )
    _verify_patched_main(patched)
    return patched.encode("utf-8")


def _verify_patched_main(text: str) -> None:
    required_once = (
        _PATCH_BEGIN,
        _PATCH_END,
        "import 'dart:convert';",
        _INSTALL_CALL.strip(),
        _PHASE_TIMER.strip(),
        "FlutterError.onError =",
        "PlatformDispatcher.instance.onError =",
        "flutter-errors.jsonl",
    )
    if any(text.count(marker) != 1 for marker in required_once):
        raise InvalidPreparedTemplate("prepared Flet runner hook content is invalid")


def _write_deterministic_zip(path: Path, members: list[_Member]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.comment = _ZIP_COMMENT
        for member in sorted(members, key=lambda item: item.name):
            info = zipfile.ZipInfo(member.name, _ZIP_TIMESTAMP)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            if member.is_directory:
                info.external_attr = (stat.S_IFDIR | 0o755) << 16 | 0x10
            else:
                info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, member.data)
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _prepared_from_zip(path: Path, official_hash: str) -> PreparedTemplate:
    output_hash = _digest(path)
    members = _read_members(path)
    targets = [member for member in members if member.name == TARGET_MEMBER]
    if len(targets) != 1 or targets[0].is_directory:
        raise InvalidPreparedTemplate("prepared Flet template target is invalid")
    try:
        main_source = targets[0].data.decode("utf-8")
    except UnicodeError as exc:
        raise InvalidPreparedTemplate("prepared Flet runner is not UTF-8") from exc
    _verify_patched_main(main_source)
    return PreparedTemplate(official_hash, output_hash)


def prepare_template(source_zip: Path, output_zip: Path) -> PreparedTemplate:
    """Patch exactly one reviewed template and replace the output atomically."""

    source = Path(source_zip)
    output = Path(output_zip)
    try:
        if source.resolve(strict=False) == output.resolve(strict=False):
            raise InvalidPreparedTemplate("source and output paths must differ")
        official_hash = _digest(source)
    except InvalidPreparedTemplate:
        raise
    except OSError as exc:
        raise InvalidPreparedTemplate("cannot read the Flet template input") from exc
    if official_hash != OFFICIAL_TEMPLATE_SHA256:
        raise UnknownRunnerTemplate("official Flet template hash changed")

    members = _read_members(source)
    targets = [index for index, member in enumerate(members) if member.name == TARGET_MEMBER]
    if len(targets) != 1 or members[targets[0]].is_directory:
        raise UnknownRunnerTemplate("official Flet runner target is missing or ambiguous")
    target_index = targets[0]
    target = members[target_index]
    members[target_index] = _Member(
        name=target.name,
        data=_patch_main(target.data),
        is_directory=False,
    )

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.",
            suffix=".tmp",
            dir=output.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            _write_deterministic_zip(temporary, members)
            prepared = _prepared_from_zip(temporary, official_hash)
            if (
                official_hash == REVIEWED_OFFICIAL_TEMPLATE_SHA256
                and prepared.output_sha256 != PATCHED_TEMPLATE_SHA256
            ):
                raise InvalidPreparedTemplate("prepared Flet template hash is invalid")
            os.replace(temporary, output)
            return prepared
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    except (UnknownRunnerTemplate, InvalidPreparedTemplate):
        raise
    except OSError as exc:
        raise InvalidPreparedTemplate("cannot write the prepared Flet template") from exc


def verify_template(path: Path) -> PreparedTemplate:
    """Verify the exact deterministic production template."""

    candidate = Path(path)
    try:
        prepared = _prepared_from_zip(candidate, REVIEWED_OFFICIAL_TEMPLATE_SHA256)
    except UnknownRunnerTemplate as exc:
        raise InvalidPreparedTemplate("prepared Flet template ZIP is invalid") from exc
    except OSError as exc:
        raise InvalidPreparedTemplate("cannot read the prepared Flet template") from exc
    if prepared.output_sha256 != PATCHED_TEMPLATE_SHA256:
        raise InvalidPreparedTemplate("prepared Flet template hash is invalid")
    return prepared


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        prepared = prepare_template(args.source, args.output)
    except UnknownRunnerTemplate:
        print("Unrecognized Flet runner template; refusing to patch.", file=sys.stderr)
        return 2
    except (InvalidPreparedTemplate, OSError):
        print("Could not create a verified diagnostics template.", file=sys.stderr)
        return 3
    print(f"Prepared Flet diagnostics template: {prepared.output_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
