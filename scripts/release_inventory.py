"""Fail-closed validation for the exact four-platform release inventory."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Sequence
import urllib.parse

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
for _import_root in reversed((_REPOSITORY_ROOT, _SOURCE_ROOT)):
    _import_path = str(_import_root)
    sys.path[:] = [entry for entry in sys.path if entry != _import_path]
    sys.path.insert(0, _import_path)

from core.update_manifest import ManifestError, parse_manifest
try:
    from scripts.release_metadata import ReleaseMetadataError, release_build_number
except ModuleNotFoundError:  # Direct ``python scripts/release_inventory.py``.
    from release_metadata import ReleaseMetadataError, release_build_number


REQUIRED_PACKAGE_NAMES = (
    "UTHelper-{version}.ipa",
    "UTHelper-{version}.apk",
    "UTHelper-Setup-{version}.exe",
    "UTHelper-{version}.msi",
)
_EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        "platform",
        "asset_name",
        "sha256",
        "version",
        "product_id",
        "architecture",
        "signer_identity",
        "certificate_fingerprint",
        "signature_valid",
        "timestamp_valid",
        "checks",
        "commit_sha",
        "workflow_run_id",
    }
)
_EXPECTED = {
    ".ipa": (
        "ios",
        "com.uthelper.UTHelper",
        "arm64",
        frozenset(
            {
                "build_number",
                "bundle_id",
                "certificate_fingerprint",
                "codesign",
                "distribution_profile",
                "entitlements",
                "ipa_container",
                "sha256",
                "version",
            }
        ),
    ),
    ".apk": (
        "android",
        "com.uthelper.uthelper",
        "universal",
        frozenset(
            {
                "apk_signature",
                "notification_receivers",
                "package_id",
                "sha256",
                "version_code",
                "version_name",
            }
        ),
    ),
    ".exe": (
        "windows",
        "UTHelper",
        "x64",
        frozenset({"authenticode", "burn_payload", "pe_header", "product_version", "timestamp"}),
    ),
    ".msi": (
        "windows",
        "UTHelper",
        "x64",
        frozenset({"authenticode", "msi_ole", "product_version", "template", "timestamp", "upgrade_code"}),
    ),
}
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MSI_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


class InventoryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    schema_version: int
    platform: str
    asset_name: str
    sha256: str
    version: str
    product_id: str
    architecture: str
    signer_identity: str
    certificate_fingerprint: str
    signature_valid: bool
    timestamp_valid: bool | None
    checks: Sequence[str]
    commit_sha: str
    workflow_run_id: str


@dataclass(frozen=True, slots=True)
class InventoryPackage:
    name: str
    path: Path
    sha256: str
    size: int
    evidence: VerificationEvidence


@dataclass(frozen=True, slots=True)
class ReleaseInventory:
    release_dir: Path
    evidence_dir: Path
    version: str
    repository: str
    packages: tuple[InventoryPackage, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _direct_child(path: Path, directory: Path, expected_name: str) -> Path:
    resolved_dir = directory.resolve()
    resolved = path.resolve()
    if resolved.parent != resolved_dir or resolved.name != expected_name:
        raise InventoryError(f"{expected_name} must be a direct release child")
    return resolved


def _validate_container(path: Path) -> None:
    with path.open("rb") as stream:
        magic = stream.read(8)
    if path.suffix in {".ipa", ".apk"} and not magic.startswith(b"PK\x03\x04"):
        raise InventoryError(f"{path.suffix[1:].upper()} ZIP header mismatch")
    if path.suffix == ".msi" and magic != _MSI_MAGIC:
        raise InventoryError("MSI OLE header mismatch")
    if path.suffix == ".exe" and not magic.startswith(b"MZ"):
        raise InventoryError("EXE PE header mismatch")


def _read_evidence(path: Path, package: Path, version: str) -> VerificationEvidence:
    try:
        if path.stat().st_size > 64 * 1024:
            raise InventoryError("verification evidence exceeds size limit")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryError("verification evidence is invalid") from exc
    if not isinstance(raw, dict) or set(raw) != _EVIDENCE_KEYS:
        raise InventoryError("verification evidence fields are invalid")
    suffix = package.suffix.lower()
    platform_name, product_id, architecture, expected_checks = _EXPECTED[suffix]
    checks = raw["checks"]
    if (
        not isinstance(checks, list)
        or any(not isinstance(value, str) for value in checks)
        or len(checks) != len(set(checks))
        or frozenset(checks) != expected_checks
    ):
        raise InventoryError("verification evidence checks are invalid")
    actual_hash = _sha256(package)
    evidence_hash = raw["sha256"]
    if not isinstance(evidence_hash, str) or evidence_hash.lower() != actual_hash:
        raise InventoryError("evidence hash does not match package")
    fingerprint = raw["certificate_fingerprint"]
    if not isinstance(fingerprint, str) or not _HEX64.fullmatch(fingerprint):
        raise InventoryError("verification certificate fingerprint is invalid")
    timestamp = raw["timestamp_valid"]
    expected_timestamp = True if platform_name == "windows" else None
    if (
        raw["schema_version"] != 1
        or raw["platform"] != platform_name
        or raw["asset_name"] != package.name
        or raw["version"] != version
        or raw["product_id"] != product_id
        or raw["architecture"] != architecture
        or not isinstance(raw["signer_identity"], str)
        or not raw["signer_identity"].strip()
        or raw["signature_valid"] is not True
        or timestamp is not expected_timestamp
        or not isinstance(raw["commit_sha"], str)
        or not _COMMIT.fullmatch(raw["commit_sha"])
        or not isinstance(raw["workflow_run_id"], str)
        or not raw["workflow_run_id"].isdigit()
    ):
        raise InventoryError("verification evidence identity is invalid")
    return VerificationEvidence(
        schema_version=1,
        platform=platform_name,
        asset_name=package.name,
        sha256=actual_hash,
        version=version,
        product_id=product_id,
        architecture=architecture,
        signer_identity=raw["signer_identity"].strip(),
        certificate_fingerprint=fingerprint.upper(),
        signature_valid=True,
        timestamp_valid=expected_timestamp,
        checks=tuple(sorted(checks)),
        commit_sha=raw["commit_sha"].lower(),
        workflow_run_id=raw["workflow_run_id"],
    )


def _validate_manifest(path: Path, inventory: ReleaseInventory) -> None:
    try:
        if path.stat().st_size > 1024 * 1024:
            raise InventoryError("release manifest exceeds size limit")
        raw = json.loads(path.read_text(encoding="utf-8"))
        manifest = parse_manifest(raw, expected_release_version=inventory.version)
    except (OSError, UnicodeError, json.JSONDecodeError, ManifestError) as exc:
        raise InventoryError("release manifest is invalid") from exc
    if manifest.schema_version != 2 or len(manifest.packages) != 4:
        raise InventoryError("release manifest must contain schema 2 exact inventory")
    expected_by_name = {item.name: item for item in inventory.packages}
    seen = set()
    expected_prefix = (
        f"https://github.com/{inventory.repository}/releases/download/"
        f"v{inventory.version}/"
    )
    for package in manifest.packages:
        name = urllib.parse.unquote(Path(urllib.parse.urlsplit(package.url).path).name)
        item = expected_by_name.get(name)
        expected_url = (
            expected_prefix + urllib.parse.quote(item.name)
            if item is not None
            else ""
        )
        if item is None or name in seen or package.url != expected_url:
            raise InventoryError("manifest package inventory mismatch")
        seen.add(name)
        if package.size != item.size or package.sha256 != item.sha256:
            raise InventoryError("manifest package bytes do not match evidence")
        if package.architecture != item.evidence.architecture:
            raise InventoryError("manifest architecture does not match native evidence")
        if package.signer_identity != item.evidence.signer_identity:
            raise InventoryError("manifest signer identity does not match native evidence")
        if package.certificate_fingerprint.upper() != item.evidence.certificate_fingerprint:
            raise InventoryError("manifest certificate fingerprint evidence mismatch")
    if seen != set(expected_by_name):
        raise InventoryError("manifest package inventory mismatch")


def _validate_checksums(path: Path, inventory: ReleaseInventory, manifest_path: Path) -> None:
    try:
        raw = path.read_bytes()
        text = raw.decode("ascii")
    except (OSError, UnicodeError) as exc:
        raise InventoryError("SHA256SUMS is invalid") from exc
    if not raw.endswith(b"\n") or b"\r" in raw:
        raise InventoryError("SHA256SUMS line endings are invalid")
    expected_names = [item.name for item in inventory.packages] + [manifest_path.name]
    lines = text.splitlines()
    if len(lines) != len(expected_names):
        raise InventoryError("SHA256SUMS inventory mismatch")
    parsed: list[tuple[str, str]] = []
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        if match is None:
            raise InventoryError("SHA256SUMS entry is malformed")
        parsed.append((match.group(1), match.group(2)))
    if [name for _, name in parsed] != expected_names or len(set(expected_names)) != len(parsed):
        raise InventoryError("SHA256SUMS inventory mismatch")
    paths = [item.path for item in inventory.packages] + [manifest_path]
    if any(digest != _sha256(item) for (digest, _), item in zip(parsed, paths, strict=True)):
        raise InventoryError("SHA256SUMS hash mismatch")


def verify_release_inventory(
    release_dir: Path,
    evidence_dir: Path,
    version: str,
    repository: str,
    manifest_path: Path | None = None,
    checksums_path: Path | None = None,
) -> ReleaseInventory:
    try:
        release_build_number(version)
    except ReleaseMetadataError as exc:
        raise InventoryError("release version is invalid") from exc
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise InventoryError("repository must be owner/name")
    release = Path(release_dir).resolve()
    evidence = Path(evidence_dir).resolve()
    if not release.is_dir() or not evidence.is_dir():
        raise InventoryError("release and evidence directories are required")
    expected_names = tuple(pattern.format(version=version) for pattern in REQUIRED_PACKAGE_NAMES)
    allowed_release = set(expected_names)
    resolved_manifest = None
    resolved_checksums = None
    if manifest_path is not None:
        resolved_manifest = _direct_child(Path(manifest_path), release, "release-manifest.json")
        allowed_release.add("release-manifest.json")
    if checksums_path is not None:
        resolved_checksums = _direct_child(Path(checksums_path), release, "SHA256SUMS")
        allowed_release.add("SHA256SUMS")
    actual_release = {item.name for item in release.iterdir()}
    if actual_release != allowed_release:
        raise InventoryError("required inventory is missing or contains unexpected assets")
    expected_evidence = {f"{name}.verification.json" for name in expected_names}
    if {item.name for item in evidence.iterdir()} != expected_evidence:
        raise InventoryError("verification evidence inventory mismatch")
    packages = []
    for name in expected_names:
        path = release / name
        if path.is_symlink() or not path.is_file():
            raise InventoryError("required inventory package is invalid")
        _validate_container(path)
        record = _read_evidence(evidence / f"{name}.verification.json", path, version)
        packages.append(InventoryPackage(name, path, record.sha256, path.stat().st_size, record))
    inventory = ReleaseInventory(release, evidence, version, repository, tuple(packages))
    if resolved_manifest is not None:
        _validate_manifest(resolved_manifest, inventory)
    if resolved_checksums is not None:
        if resolved_manifest is None:
            raise InventoryError("SHA256SUMS requires a verified release manifest")
        _validate_checksums(resolved_checksums, inventory, resolved_manifest)
    return inventory


def write_sha256sums(inventory: ReleaseInventory, manifest_path: Path, output: Path) -> None:
    manifest = _direct_child(Path(manifest_path), inventory.release_dir, "release-manifest.json")
    destination = _direct_child(Path(output), inventory.release_dir, "SHA256SUMS")
    if not manifest.is_file():
        raise InventoryError("release manifest is required before SHA256SUMS")
    lines = [f"{item.sha256}  {item.name}" for item in inventory.packages]
    lines.append(f"{_sha256(manifest)}  {manifest.name}")
    destination.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--write-checksums", type=Path)
    parser.add_argument("--checksums", type=Path)
    args = parser.parse_args()
    if args.write_checksums and args.checksums:
        parser.error("choose either --write-checksums or --checksums")
    existing_checksum = args.write_checksums if args.write_checksums and args.write_checksums.exists() else args.checksums
    inventory = verify_release_inventory(
        args.release_dir,
        args.evidence_dir,
        args.version,
        args.repository,
        manifest_path=args.manifest,
        checksums_path=existing_checksum,
    )
    if args.write_checksums:
        if args.manifest is None:
            parser.error("--write-checksums requires --manifest")
        write_sha256sums(inventory, args.manifest, args.write_checksums)


if __name__ == "__main__":
    main()
