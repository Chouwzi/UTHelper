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

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = str(_REPOSITORY_ROOT / "src")
if _SOURCE_ROOT not in sys.path:
    sys.path.insert(0, _SOURCE_ROOT)

from diagnostics.release_config import (  # noqa: E402
    PUBLIC_CONFIG_FILENAME,
    PublicConfigError,
    validate_public_config_bytes,
)


_CPU_TYPE_ARM64 = 0x0100000C
_CPU_TYPE_X86_64 = 0x01000007
_MAX_PLIST_BYTES = 1024 * 1024
_MAX_IPA_BYTES = 4 * 1024 * 1024 * 1024
_MAX_IPA_MEMBERS = 100_000
_MAX_EXPANDED_BYTES = 8 * 1024 * 1024 * 1024
_HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
_REQUIRED_OFFICE_VIEWER_ASSETS = (
    "docx_host.html",
    "docx_host.js",
    "docx-preview.min.js",
    "jszip.min.js",
    "pptx_host.html",
    "pptx_host.js",
    "pptx_worker.js",
    "pptx_engine.js",
    "viewer.css",
    "viewer_common.js",
)
_REQUIRED_PDF_VIEWER_ASSETS = (
    "pdf_host.html",
    "pdf_host.js",
    "pdf_viewer.css",
    "pdf.min.mjs",
    "pdf.worker.min.mjs",
)


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


def verify_ios_app_office_viewer_assets(
    app: Path,
    *,
    require: bool = True,
) -> bool:
    """Verify bundled ``office_viewer/*`` files in an iOS ``.app`` bundle."""
    if not require:
        return False
    bundle = Path(app)
    app_zips = sorted(
        item
        for item in bundle.rglob("app.zip")
        if not item.is_symlink() and item.is_file()
    )
    if app_zips:
        for app_zip in app_zips:
            try:
                with zipfile.ZipFile(app_zip) as nested:
                    members = nested.infolist()
                    by_name: dict[str, list[zipfile.ZipInfo]] = {}
                    for member in members:
                        if stat.S_ISLNK(member.external_attr >> 16):
                            raise IpaError(
                                f"Nested app.zip ({app_zip.name}) contains unsafe symlink member: {member.filename}"
                            )
                        by_name.setdefault(member.filename, []).append(member)
                    for asset_name in _REQUIRED_OFFICE_VIEWER_ASSETS:
                        candidates = (
                            f"assets/office_viewer/{asset_name}",
                            f"app/assets/office_viewer/{asset_name}",
                        )
                        matched_infos: list[zipfile.ZipInfo] = []
                        for candidate in candidates:
                            matched_infos.extend(by_name.get(candidate, ()))
                        if not matched_infos:
                            raise IpaError(
                                f"Nested app.zip ({app_zip.name}) is missing office_viewer asset: {asset_name}"
                            )
                        for info in matched_infos:
                            if info.is_dir():
                                raise IpaError(
                                    f"Nested app.zip ({app_zip.name}) contains unsafe office_viewer asset: {asset_name}"
                                )
                            if info.file_size <= 0:
                                raise IpaError(
                                    f"Nested app.zip ({app_zip.name}) has empty office_viewer asset: {asset_name}"
                                )
                    for asset_name in _REQUIRED_PDF_VIEWER_ASSETS:
                        candidates = (
                            f"assets/pdf_viewer/{asset_name}",
                            f"app/assets/pdf_viewer/{asset_name}",
                        )
                        matched_infos = []
                        for candidate in candidates:
                            matched_infos.extend(by_name.get(candidate, ()))
                        if not matched_infos:
                            raise IpaError(
                                f"Nested app.zip ({app_zip.name}) is missing pdf_viewer asset: {asset_name}"
                            )
                        for info in matched_infos:
                            if info.is_dir():
                                raise IpaError(
                                    f"Nested app.zip ({app_zip.name}) contains unsafe pdf_viewer asset: {asset_name}"
                                )
                            if info.file_size <= 0:
                                raise IpaError(
                                    f"Nested app.zip ({app_zip.name}) has empty pdf_viewer asset: {asset_name}"
                                )
            except (OSError, zipfile.BadZipFile) as exc:
                raise IpaError("iOS nested app.zip is invalid") from exc
        return True

    viewer_dirs = sorted(
        item
        for item in bundle.rglob("office_viewer")
        if not item.is_symlink() and item.is_dir()
    )
    pdf_viewer_dirs = sorted(
        item
        for item in bundle.rglob("pdf_viewer")
        if not item.is_symlink() and item.is_dir()
    )
    has_office = any(
        all(
            not (viewer_dir / asset_name).is_symlink()
            and (viewer_dir / asset_name).is_file()
            and (viewer_dir / asset_name).stat().st_size > 0
            for asset_name in _REQUIRED_OFFICE_VIEWER_ASSETS
        )
        for viewer_dir in viewer_dirs
    )
    has_pdf = any(
        all(
            not (pdf_dir / asset_name).is_symlink()
            and (pdf_dir / asset_name).is_file()
            and (pdf_dir / asset_name).stat().st_size > 0
            for asset_name in _REQUIRED_PDF_VIEWER_ASSETS
        )
        for pdf_dir in pdf_viewer_dirs
    )
    if has_office and has_pdf:
        return True

    raise IpaError("iOS app bundle is missing required office_viewer/pdf_viewer assets")


_IOS_ALLOWED_PACKAGE_FORMATS = frozenset({"ipa", "ios_app"})
_MAX_NESTED_APP_ZIP_BYTES = 256 * 1024 * 1024


def _validate_ios_diagnostics_bytes(
    raw: bytes,
    *,
    source_label: str,
    require: bool,
    effective_build_env: str | None,
) -> None:
    try:
        cfg = validate_public_config_bytes(raw, development=False)
    except PublicConfigError as exc:
        raise IpaError(
            f"Embedded {PUBLIC_CONFIG_FILENAME} ({source_label}) is invalid: {exc}"
        ) from exc

    if require or cfg.package_format is not None:
        if cfg.package_format not in _IOS_ALLOWED_PACKAGE_FORMATS:
            raise IpaError(
                f"Embedded {PUBLIC_CONFIG_FILENAME} ({source_label}) has unsupported "
                f"iOS package_format {cfg.package_format!r}"
            )
    if effective_build_env is not None and cfg.build_environment != effective_build_env:
        raise IpaError(
            f"Embedded {PUBLIC_CONFIG_FILENAME} ({source_label}) build_environment "
            f"{cfg.build_environment!r} != expected {effective_build_env!r}"
        )


def verify_ios_app_diagnostics_config(
    app: Path,
    *,
    require: bool = False,
    expected_build_environment: str | None = None,
) -> bool:
    """Validate every embedded ``diagnostics-config.json`` in an iOS ``.app`` bundle fail-closed."""
    bundle = Path(app)
    if bundle.is_symlink() or not bundle.is_dir() or bundle.suffix != ".app":
        raise IpaError("exactly one application bundle is required")
    _ensure_safe_tree(bundle)

    effective_build_env = (
        expected_build_environment
        if expected_build_environment is not None
        else ("release" if require else None)
    )

    for item in bundle.rglob("*"):
        if item.is_symlink() and item.name in {"app.zip", PUBLIC_CONFIG_FILENAME}:
            raise IpaError(
                f"application contains unsafe symlink for {item.name}: {item}"
            )

    verified_count = 0
    found_primary = False
    app_zips = sorted(
        item
        for item in bundle.rglob("app.zip")
        if not item.is_symlink() and item.is_file()
    )
    for app_zip in app_zips:
        try:
            if app_zip.stat().st_size > _MAX_NESTED_APP_ZIP_BYTES:
                raise IpaError(f"Nested app.zip exceeds size limit: {app_zip.name}")
            with zipfile.ZipFile(app_zip) as nested:
                seen_names: set[str] = set()
                zip_has_primary = False
                for member in nested.infolist():
                    if member.filename in seen_names:
                        raise IpaError(
                            f"Nested app.zip ({app_zip.name}) contains duplicate member: "
                            f"{member.filename}"
                        )
                    seen_names.add(member.filename)
                    if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                        raise IpaError(
                            f"Nested app.zip ({app_zip.name}) contains unsafe symlink member: "
                            f"{member.filename}"
                        )
                    if (
                        member.filename == PUBLIC_CONFIG_FILENAME
                        or member.filename.endswith(f"/{PUBLIC_CONFIG_FILENAME}")
                    ):
                        if (
                            member.is_dir()
                            or member.file_size <= 0
                            or member.file_size > 4096
                        ):
                            raise IpaError(
                                f"Embedded {PUBLIC_CONFIG_FILENAME} inside "
                                f"{app_zip.name}:{member.filename} has invalid size"
                            )
                        raw = nested.read(member)
                        if len(raw) == 0 or len(raw) > 4096:
                            raise IpaError(
                                f"Embedded {PUBLIC_CONFIG_FILENAME} inside "
                                f"{app_zip.name}:{member.filename} has invalid byte length"
                            )
                        _validate_ios_diagnostics_bytes(
                            raw,
                            source_label=f"{app_zip.name}:{member.filename}",
                            require=require,
                            effective_build_env=effective_build_env,
                        )
                        verified_count += 1
                        if member.filename in {
                            f"assets/{PUBLIC_CONFIG_FILENAME}",
                            f"app/assets/{PUBLIC_CONFIG_FILENAME}",
                        }:
                            zip_has_primary = True
                if require and not zip_has_primary:
                    raise IpaError(
                        f"Nested app.zip ({app_zip.name}) is missing primary "
                        f"assets/{PUBLIC_CONFIG_FILENAME}"
                    )
                if zip_has_primary:
                    found_primary = True
        except (OSError, zipfile.BadZipFile) as exc:
            raise IpaError("iOS nested app.zip is invalid") from exc

    loose_configs = sorted(
        item
        for item in bundle.rglob(PUBLIC_CONFIG_FILENAME)
        if not item.is_symlink()
    )
    for cfg_path in loose_configs:
        rel = cfg_path.relative_to(bundle).as_posix()
        if cfg_path.is_dir():
            raise IpaError(
                f"Embedded {PUBLIC_CONFIG_FILENAME} ({rel}) must be a regular file"
            )
        try:
            st = cfg_path.stat()
            if st.st_size <= 0 or st.st_size > 4096:
                raise IpaError(
                    f"Embedded {PUBLIC_CONFIG_FILENAME} ({rel}) has invalid size"
                )
            raw = cfg_path.read_bytes()
        except OSError as exc:
            raise IpaError(
                f"Embedded {PUBLIC_CONFIG_FILENAME} ({rel}) cannot be read"
            ) from exc
        _validate_ios_diagnostics_bytes(
            raw,
            source_label=rel,
            require=require,
            effective_build_env=effective_build_env,
        )
        verified_count += 1
        if not app_zips and (
            rel == f"assets/{PUBLIC_CONFIG_FILENAME}"
            or rel.endswith(f"/assets/{PUBLIC_CONFIG_FILENAME}")
        ):
            found_primary = True

    if require and not found_primary:
        raise IpaError(f"iOS app bundle is missing embedded {PUBLIC_CONFIG_FILENAME}")
    return verified_count > 0


def inspect_device_app(
    app: Path,
    *,
    version: str,
    build_number: str,
    bundle_id: str,
    require_office_viewer: bool = False,
    require_diagnostics_config: bool = False,
    expected_build_environment: str | None = None,
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
    verify_ios_app_office_viewer_assets(bundle, require=require_office_viewer)
    verify_ios_app_diagnostics_config(
        bundle,
        require=require_diagnostics_config,
        expected_build_environment=expected_build_environment,
    )
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
    require_office_viewer: bool = False,
    require_diagnostics_config: bool = False,
    expected_build_environment: str | None = None,
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
                    require_office_viewer=require_office_viewer,
                    require_diagnostics_config=require_diagnostics_config,
                    expected_build_environment=expected_build_environment,
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
    require_office_viewer: bool = False,
    require_diagnostics_config: bool = False,
    expected_build_environment: str | None = None,
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
        require_office_viewer=require_office_viewer,
        require_diagnostics_config=require_diagnostics_config,
        expected_build_environment=expected_build_environment,
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
                stdin=subprocess.DEVNULL,
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
            require_office_viewer=require_office_viewer,
            require_diagnostics_config=require_diagnostics_config,
            expected_build_environment=expected_build_environment,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-app", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--build-number")
    parser.add_argument("--bundle-id")
    parser.add_argument("--commit-sha")
    parser.add_argument("--workflow-run-id")
    parser.add_argument("--require-office-viewer", action="store_true")
    parser.add_argument("--require-diagnostics-config", action="store_true")
    parser.add_argument(
        "--expected-build-environment",
        choices=("release", "develop", "development"),
        default=None,
    )
    args = parser.parse_args(argv)
    if args.verify_app is not None:
        if any(
            opt is not None
            for opt in (
                args.archive,
                args.output,
                args.evidence,
                args.version,
                args.build_number,
                args.bundle_id,
                args.commit_sha,
                args.workflow_run_id,
            )
        ):
            parser.error("--verify-app cannot be combined with IPA packaging arguments")
        verify_ios_app_office_viewer_assets(
            args.verify_app,
            require=args.require_office_viewer,
        )
        verify_ios_app_diagnostics_config(
            args.verify_app,
            require=args.require_diagnostics_config,
            expected_build_environment=args.expected_build_environment,
        )
        return 0

    missing = [
        flag
        for flag, val in (
            ("--archive", args.archive),
            ("--output", args.output),
            ("--evidence", args.evidence),
            ("--version", args.version),
            ("--build-number", args.build_number),
            ("--bundle-id", args.bundle_id),
            ("--commit-sha", args.commit_sha),
            ("--workflow-run-id", args.workflow_run_id),
        )
        if val is None
    ]
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")

    metadata = package_unsigned_ipa(
        args.archive,
        args.output,
        version=args.version,
        build_number=args.build_number,
        bundle_id=args.bundle_id,
        require_office_viewer=args.require_office_viewer,
        require_diagnostics_config=args.require_diagnostics_config,
        expected_build_environment=args.expected_build_environment,
    )
    write_verification_evidence(
        args.output,
        metadata,
        commit_sha=args.commit_sha,
        workflow_run_id=args.workflow_run_id,
        output=args.evidence,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
