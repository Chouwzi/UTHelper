"""Verify a signed Android release APK and emit native release evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Sequence
import xml.etree.ElementTree as ET

try:
    from scripts.release_metadata import ReleaseMetadataError, release_build_number
except ModuleNotFoundError:  # Direct ``python scripts/verify_android_release.py``.
    from release_metadata import ReleaseMetadataError, release_build_number


_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_HEX64 = re.compile(r"^[0-9A-F]{64}$")
_COMMIT = re.compile(r"^[0-9A-Fa-f]{40}$")
_CERTIFICATE_LINE = re.compile(
    r"^(?:Signer #\d+|V\d+(?:\.\d+)? Signer:)\s+"
    r"certificate SHA-256 digest:\s*(.+?)\s*$",
    re.MULTILINE,
)
_RECEIVERS = (
    "ScheduledNotificationReceiver",
    "ScheduledNotificationBootReceiver",
    "ActionBroadcastReceiver",
    "DeadlineAlarmReceiver",
    "RescheduleReceiver",
)


class AndroidVerificationError(ValueError):
    pass


def _normalize_fingerprint(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Fa-f]", "", value).upper()
    if not _HEX64.fullmatch(normalized):
        raise AndroidVerificationError("Android certificate fingerprint must be SHA-256")
    return normalized


def _version_key(path: Path) -> tuple[int, ...]:
    pieces = path.name.split(".")
    if not pieces or any(not piece.isdigit() for piece in pieces):
        return ()
    return tuple(int(piece) for piece in pieces)


def _resolve_tools(android_home: Path) -> tuple[Path, Path]:
    build_tools_root = android_home / "build-tools"
    versions = sorted(
        (path for path in build_tools_root.iterdir() if path.is_dir() and _version_key(path)),
        key=_version_key,
    ) if build_tools_root.is_dir() else []
    if not versions:
        raise AndroidVerificationError("Android build-tools directory is missing")
    apksigner = versions[-1] / ("apksigner.bat" if os.name == "nt" else "apksigner")

    command_line_root = android_home / "cmdline-tools"
    analyzer_candidates = [
        command_line_root / "latest" / "bin" / ("apkanalyzer.bat" if os.name == "nt" else "apkanalyzer")
    ]
    if command_line_root.is_dir():
        analyzer_candidates.extend(
            path / "bin" / ("apkanalyzer.bat" if os.name == "nt" else "apkanalyzer")
            for path in sorted(command_line_root.iterdir(), key=lambda item: item.name, reverse=True)
            if path.is_dir() and path.name != "latest"
        )
    apkanalyzer = next((path for path in analyzer_candidates if path.is_file()), None)
    if not apksigner.is_file() or apkanalyzer is None:
        raise AndroidVerificationError("apksigner or apkanalyzer is missing from Android SDK")
    return apksigner, apkanalyzer


def _run(command: Sequence[str | Path]) -> str:
    try:
        completed = subprocess.run(
            [str(part) for part in command],
            timeout=60,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AndroidVerificationError("Android native verifier command failed") from exc
    return completed.stdout.strip()


def verify_android_release(
    *,
    apk: Path,
    version: str,
    build_number: int,
    package_id: str,
    certificate_sha256: str,
    commit_sha: str,
    workflow_run_id: str,
    output: Path,
    android_home: Path | None = None,
) -> dict[str, object]:
    apk = apk.resolve()
    if not apk.is_file() or apk.name != f"UTHelper-{version}.apk":
        raise AndroidVerificationError("Android release APK filename is not canonical")
    if not _VERSION.fullmatch(version) or build_number <= 0:
        raise AndroidVerificationError("Android release version metadata is invalid")
    try:
        canonical_build_number = release_build_number(version)
    except ReleaseMetadataError as exc:
        raise AndroidVerificationError("Android release version metadata is invalid") from exc
    if build_number != canonical_build_number:
        raise AndroidVerificationError("Android build number is not canonical for version")
    if package_id != "com.uthelper.uthelper":
        raise AndroidVerificationError("Android package ID is not canonical")
    if not _COMMIT.fullmatch(commit_sha) or not workflow_run_id.strip():
        raise AndroidVerificationError("Android provenance metadata is invalid")
    expected_fingerprint = _normalize_fingerprint(certificate_sha256)
    with apk.open("rb") as stream:
        if stream.read(4) != b"PK\x03\x04":
            raise AndroidVerificationError("Android APK ZIP magic is invalid")

    if android_home is None:
        sdk_value = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
        if not sdk_value:
            raise AndroidVerificationError("ANDROID_HOME is not configured")
        android_home = Path(sdk_value)
    apksigner, apkanalyzer = _resolve_tools(Path(android_home))

    signature_output = _run((apksigner, "verify", "--verbose", "--print-certs", apk))
    signer_fingerprints = [
        _normalize_fingerprint(match.group(1))
        for match in _CERTIFICATE_LINE.finditer(signature_output)
    ]
    if not signer_fingerprints or set(signer_fingerprints) != {expected_fingerprint}:
        raise AndroidVerificationError("APK signing certificate does not match pinned identity")

    actual_package = _run((apkanalyzer, "manifest", "application-id", apk))
    actual_version = _run((apkanalyzer, "manifest", "version-name", apk))
    actual_build_number = _run((apkanalyzer, "manifest", "version-code", apk))
    manifest = _run((apkanalyzer, "manifest", "print", apk))
    if actual_package != package_id:
        raise AndroidVerificationError("APK package ID mismatch")
    if actual_version != version:
        raise AndroidVerificationError("APK versionName mismatch")
    if actual_build_number != str(build_number):
        raise AndroidVerificationError("APK versionCode mismatch")
    try:
        manifest_root = ET.fromstring(manifest)
    except ET.ParseError as exc:
        raise AndroidVerificationError("APK manifest XML is invalid") from exc
    android_name = "{http://schemas.android.com/apk/res/android}name"
    receiver_names = tuple(
        element.attrib.get(android_name, element.attrib.get("name", ""))
        for element in manifest_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "receiver"
    )
    invalid_receivers = [
        name
        for name in _RECEIVERS
        if sum(
            candidate == name or candidate.endswith(f".{name}")
            for candidate in receiver_names
        )
        != 1
    ]
    if invalid_receivers:
        raise AndroidVerificationError("APK notification receiver wiring is incomplete")

    with apk.open("rb") as stream:
        apk_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    evidence: dict[str, object] = {
        "schema_version": 2,
        "platform": "android",
        "asset_name": apk.name,
        "sha256": apk_sha256,
        "version": version,
        "product_id": package_id,
        "architecture": "universal",
        "signature_kind": "apk-pinned",
        "signer_identity": package_id,
        "certificate_fingerprint": expected_fingerprint,
        "signature_valid": True,
        "timestamp_valid": None,
        "checks": sorted(
            (
                "apk_signature",
                "package_id",
                "version_name",
                "version_code",
                "notification_receivers",
                "sha256",
            )
        ),
        "commit_sha": commit_sha.lower(),
        "workflow_run_id": workflow_run_id.strip(),
    }
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        temporary.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-number", required=True, type=int)
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--certificate-sha256", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    verify_android_release(
        apk=args.apk,
        version=args.version,
        build_number=args.build_number,
        package_id=args.package_id,
        certificate_sha256=args.certificate_sha256,
        commit_sha=args.commit_sha,
        workflow_run_id=args.workflow_run_id,
        output=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
