"""Package and verify an unsigned iPhoneOS arm64 IPA for user-side re-signing."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import zipfile


_CPU_TYPE_ARM64 = 0x0100000C
_CPU_TYPE_X86_64 = 0x01000007
_MAX_PLIST_BYTES = 1024 * 1024
_MAX_IPA_BYTES = 4 * 1024 * 1024 * 1024
_MAX_IPA_MEMBERS = 100_000
_MAX_EXPANDED_BYTES = 8 * 1024 * 1024 * 1024
_HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")


class IpaError(ValueError):
    """The archive is not the exact unsigned iPhoneOS package we expect."""


@dataclass(frozen=True, slots=True)
class IosAppMetadata:
    bundle_id: str
    version: str
    build_number: str
    executable_name: str
    platforms: tuple[str, ...]
    architectures: tuple[str, ...]


def _bounded_text(value: object, field: str, *, limit: int = 256) -> str:
    if not isinstance(value, str):
        raise IpaError(f"{field} is invalid")
    text = value.strip()
    if not text or len(text) > limit or any(ord(char) < 32 for char in text):
        raise IpaError(f"{field} is invalid")
    return text


def _ensure_safe_tree(root: Path) -> None:
    resolved_root = root.resolve()
    for item in root.rglob("*"):
        if not item.is_symlink():
            continue
        target = os.readlink(item)
        if os.path.isabs(target):
            raise IpaError("application contains an absolute symlink")
        try:
            item.resolve(strict=False).relative_to(resolved_root)
        except ValueError as exc:
            raise IpaError("application symlink escapes its bundle") from exc


def _macho_architectures(path: Path) -> tuple[str, ...]:
    try:
        size = path.stat().st_size
        if size < 12 or size > _MAX_EXPANDED_BYTES:
            raise IpaError("application executable is invalid")
        with path.open("rb") as stream:
            header = stream.read(4096)
    except OSError as exc:
        raise IpaError("application executable cannot be read") from exc

    magic = header[:4]
    cpu_types: list[int]
    if magic == bytes.fromhex("cffaedfe"):
        cpu_types = [struct.unpack_from("<i", header, 4)[0]]
    elif magic == bytes.fromhex("feedfacf"):
        cpu_types = [struct.unpack_from(">i", header, 4)[0]]
    elif magic in {bytes.fromhex("cafebabe"), bytes.fromhex("cafebabf")}:
        is_64 = magic == bytes.fromhex("cafebabf")
        entry_size = 32 if is_64 else 20
        if len(header) < 8:
            raise IpaError("application executable is invalid")
        count = struct.unpack_from(">I", header, 4)[0]
        if count == 0 or count > 16 or len(header) < 8 + count * entry_size:
            raise IpaError("application executable is invalid")
        cpu_types = [
            struct.unpack_from(">i", header, 8 + index * entry_size)[0]
            for index in range(count)
        ]
    else:
        raise IpaError("application executable is not a supported Mach-O")

    if cpu_types != [_CPU_TYPE_ARM64]:
        labels = [
            "arm64" if value == _CPU_TYPE_ARM64 else "x86_64"
            if value == _CPU_TYPE_X86_64
            else f"cpu:{value}"
            for value in cpu_types
        ]
        raise IpaError(
            "application must be an iPhoneOS arm64 binary; found "
            + ", ".join(labels)
        )
    return ("arm64",)


def inspect_device_app(
    app: Path,
    *,
    version: str,
    build_number: str,
    bundle_id: str,
) -> IosAppMetadata:
    """Validate one unpacked, unsigned device application bundle."""
    bundle = Path(app)
    if bundle.is_symlink() or not bundle.is_dir() or bundle.suffix != ".app":
        raise IpaError("exactly one application bundle is required")
    _ensure_safe_tree(bundle)
    if any(item.name.lower() == "embedded.mobileprovision" for item in bundle.rglob("*")):
        raise IpaError("unsigned IPA must not contain an embedded provisioning profile")

    info_path = bundle / "Info.plist"
    try:
        if info_path.is_symlink() or info_path.stat().st_size > _MAX_PLIST_BYTES:
            raise IpaError("Info.plist is invalid")
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise IpaError("Info.plist is invalid") from exc
    if not isinstance(info, dict):
        raise IpaError("Info.plist is invalid")

    actual_bundle = _bounded_text(info.get("CFBundleIdentifier"), "bundle identifier")
    if actual_bundle != bundle_id:
        raise IpaError("bundle identifier does not match release metadata")
    actual_version = _bounded_text(
        info.get("CFBundleShortVersionString"),
        "short version",
    )
    if actual_version != version:
        raise IpaError("short version does not match release metadata")
    actual_build = _bounded_text(info.get("CFBundleVersion"), "build number")
    if actual_build != build_number:
        raise IpaError("build number does not match release metadata")
    executable_name = _bounded_text(info.get("CFBundleExecutable"), "executable name")
    if Path(executable_name).name != executable_name:
        raise IpaError("executable name is invalid")
    platforms = info.get("CFBundleSupportedPlatforms")
    if platforms != ["iPhoneOS"]:
        raise IpaError("application must target iPhoneOS arm64")
    executable = bundle / executable_name
    if executable.is_symlink() or not executable.is_file():
        raise IpaError("application executable is invalid")
    architectures = _macho_architectures(executable)
    return IosAppMetadata(
        bundle_id=actual_bundle,
        version=actual_version,
        build_number=actual_build,
        executable_name=executable_name,
        platforms=("iPhoneOS",),
        architectures=architectures,
    )


def _safe_ipa_members(
    archive: zipfile.ZipFile,
) -> tuple[list[zipfile.ZipInfo], str]:
    members = archive.infolist()
    if not members or len(members) > _MAX_IPA_MEMBERS:
        raise IpaError("IPA member inventory is invalid")
    names = [item.filename for item in members]
    if len(names) != len(set(names)):
        raise IpaError("IPA contains duplicate members")
    expanded = 0
    top_level: set[str] = set()
    for member in members:
        name = member.filename
        pure = PurePosixPath(name)
        unix_mode = member.external_attr >> 16
        expanded += member.file_size
        if (
            not name
            or "\\" in name
            or pure.is_absolute()
            or ".." in pure.parts
            or pure.parts[0] != "Payload"
            or stat.S_ISLNK(unix_mode)
            or member.file_size < 0
            or expanded > _MAX_EXPANDED_BYTES
        ):
            raise IpaError("unsafe IPA member")
        if len(pure.parts) > 1:
            top_level.add(pure.parts[1])
    applications = tuple(name for name in top_level if name.endswith(".app"))
    if len(applications) != 1 or top_level != {applications[0]}:
        raise IpaError("exactly one Payload application is required")
    return members, applications[0]


def inspect_ipa(
    ipa: Path,
    *,
    version: str,
    build_number: str,
    bundle_id: str,
) -> IosAppMetadata:
    """Reopen and independently validate a packaged IPA."""
    path = Path(ipa)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_IPA_BYTES:
            raise IpaError("IPA container is invalid")
        with zipfile.ZipFile(path) as archive:
            members, application_name = _safe_ipa_members(archive)
            with tempfile.TemporaryDirectory(prefix="uthelper-ipa-inspect-") as temp:
                root = Path(temp)
                for member in members:
                    archive.extract(member, root)
                return inspect_device_app(
                    root / "Payload" / application_name,
                    version=version,
                    build_number=build_number,
                    bundle_id=bundle_id,
                )
    except (OSError, zipfile.BadZipFile) as exc:
        raise IpaError("IPA container is invalid") from exc


def package_unsigned_ipa(
    archive: Path,
    output: Path,
    *,
    version: str,
    build_number: str,
    bundle_id: str,
) -> IosAppMetadata:
    """Package one no-codesign xcarchive using the native macOS ZIP tool."""
    archive_path = Path(archive)
    destination = Path(output)
    if sys.platform != "darwin":
        raise IpaError("unsigned IPA packaging requires macOS")
    if archive_path.is_symlink() or not archive_path.is_dir() or archive_path.suffix != ".xcarchive":
        raise IpaError("a valid xcarchive is required")
    if destination.exists():
        raise IpaError("refusing to overwrite an existing IPA")
    applications = tuple((archive_path / "Products" / "Applications").glob("*.app"))
    if len(applications) != 1:
        raise IpaError("xcarchive must contain exactly one application")
    inspect_device_app(
        applications[0],
        version=version,
        build_number=build_number,
        bundle_id=bundle_id,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="uthelper-ipa-package-") as temp:
        root = Path(temp)
        payload = root / "Payload"
        payload.mkdir()
        shutil.copytree(applications[0], payload / applications[0].name, symlinks=True)
        temporary_ipa = root / destination.name
        try:
            subprocess.run(
                [
                    "/usr/bin/ditto",
                    "-c",
                    "-k",
                    "--sequesterRsrc",
                    "--keepParent",
                    "Payload",
                    str(temporary_ipa),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise IpaError("ditto failed to package the IPA") from exc
        verified = inspect_ipa(
            temporary_ipa,
            version=version,
            build_number=build_number,
            bundle_id=bundle_id,
        )
        os.replace(temporary_ipa, destination)
    return verified


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_verification_evidence(
    ipa: Path,
    metadata: IosAppMetadata,
    *,
    commit_sha: str,
    workflow_run_id: str,
    output: Path,
) -> None:
    if not _HEX40.fullmatch(commit_sha) or not workflow_run_id.isdigit():
        raise IpaError("workflow provenance is invalid")
    path = Path(ipa)
    destination = Path(output)
    if destination.exists():
        raise IpaError("refusing to overwrite verification evidence")
    record = {
        "schema_version": 2,
        "platform": "ios",
        "asset_name": path.name,
        "sha256": _sha256(path),
        "version": metadata.version,
        "product_id": metadata.bundle_id,
        "architecture": "arm64",
        "signature_kind": "unsigned-resign-required",
        "signer_identity": "",
        "certificate_fingerprint": "",
        "signature_valid": False,
        "timestamp_valid": None,
        "checks": [
            "arm64",
            "build_number",
            "bundle_id",
            "iphoneos",
            "ipa_container",
            "no_embedded_profile",
            "sha256",
            "version",
        ],
        "commit_sha": commit_sha.lower(),
        "workflow_run_id": workflow_run_id,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build-number", required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    args = parser.parse_args()
    metadata = package_unsigned_ipa(
        args.archive,
        args.output,
        version=args.version,
        build_number=args.build_number,
        bundle_id=args.bundle_id,
    )
    write_verification_evidence(
        args.output,
        metadata,
        commit_sha=args.commit_sha,
        workflow_run_id=args.workflow_run_id,
        output=args.evidence,
    )


if __name__ == "__main__":
    main()
