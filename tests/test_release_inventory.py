from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.generate_release_manifest import generate_manifest_from_verified_inventory
from scripts.release_inventory import (
    InventoryError,
    REQUIRED_PACKAGE_NAMES,
    verify_release_inventory,
    write_sha256sums,
)


VERSION = "2.2.0"
REPOSITORY = "Chouwzi/UTHelper"
MAGIC = {
    ".ipa": b"PK\x03\x04ipa",
    ".apk": b"PK\x03\x04apk",
    ".exe": b"MZsigned-burn",
    ".msi": bytes.fromhex("D0CF11E0A1B11AE1") + b"msi",
}
PLATFORM = {".ipa": "ios", ".apk": "android", ".exe": "windows", ".msi": "windows"}
PRODUCT = {".ipa": "com.uthelper.UTHelper", ".apk": "com.uthelper.uthelper", ".exe": "UTHelper", ".msi": "UTHelper"}
ARCH = {".ipa": "arm64", ".apk": "universal", ".exe": "x64", ".msi": "x64"}
CHECKS = {
    ".ipa": [
        "arm64",
        "build_number",
        "bundle_id",
        "iphoneos",
        "ipa_container",
        "no_embedded_profile",
        "sha256",
        "version",
    ],
    ".apk": [
        "apk_signature",
        "notification_receivers",
        "package_id",
        "sha256",
        "version_code",
        "version_name",
    ],
    ".exe": ["authenticode", "burn_payload", "pe_header", "product_version", "timestamp"],
    ".msi": ["authenticode", "msi_ole", "product_version", "template", "timestamp", "upgrade_code"],
}
SIGNATURE_KIND = {
    ".ipa": "unsigned-resign-required",
    ".apk": "apk-pinned",
    ".exe": "self-signed-pinned",
    ".msi": "self-signed-pinned",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_valid_release(tmp_path, *, version=VERSION, with_manifest=False, with_checksums=False):
    release = tmp_path / "release"
    evidence = tmp_path / "evidence"
    release.mkdir()
    evidence.mkdir()
    for pattern in REQUIRED_PACKAGE_NAMES:
        name = pattern.format(version=version)
        path = release / name
        path.write_bytes(MAGIC[path.suffix])
        record = {
            "schema_version": 2,
            "platform": PLATFORM[path.suffix],
            "asset_name": name,
            "sha256": _sha(path),
            "version": version,
            "product_id": PRODUCT[path.suffix],
            "architecture": ARCH[path.suffix],
            "signature_kind": SIGNATURE_KIND[path.suffix],
            "signer_identity": "" if path.suffix == ".ipa" else PRODUCT[path.suffix],
            "certificate_fingerprint": "" if path.suffix == ".ipa" else "AB" * 32,
            "signature_valid": path.suffix != ".ipa",
            "timestamp_valid": True if path.suffix in {".exe", ".msi"} else None,
            "checks": CHECKS[path.suffix],
            "commit_sha": "1" * 40,
            "workflow_run_id": "12345",
        }
        (evidence / f"{name}.verification.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
    if with_manifest:
        manifest = generate_manifest_from_verified_inventory(
            release,
            evidence,
            version=version,
            repository=REPOSITORY,
            minimum_supported_version="2.1.0",
        )
        (release / "release-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    if with_checksums:
        inventory = verify_release_inventory(
            release,
            evidence,
            version,
            REPOSITORY,
            manifest_path=release / "release-manifest.json",
        )
        write_sha256sums(inventory, release / "release-manifest.json", release / "SHA256SUMS")
    return release, evidence


def test_exact_inventory_accepts_four_required_packages_and_matching_evidence(tmp_path):
    release, evidence = write_valid_release(tmp_path)
    inventory = verify_release_inventory(release, evidence, VERSION, REPOSITORY)

    assert [item.name for item in inventory.packages] == [
        "UTHelper-2.2.0.ipa",
        "UTHelper-2.2.0.apk",
        "UTHelper-Setup-2.2.0.exe",
        "UTHelper-2.2.0.msi",
    ]


@pytest.mark.parametrize("missing", ["ipa", "apk", "exe", "msi"])
def test_inventory_rejects_every_missing_required_format(tmp_path, missing):
    release, evidence = write_valid_release(tmp_path)
    next(release.glob(f"*.{missing}")).unlink()

    with pytest.raises(InventoryError, match="required inventory"):
        verify_release_inventory(release, evidence, VERSION, REPOSITORY)


@pytest.mark.parametrize("suffix", [".msi", ".exe"])
def test_inventory_rejects_zip_renamed_to_windows_container(tmp_path, suffix):
    release, evidence = write_valid_release(tmp_path)
    next(release.glob(f"*{suffix}")).write_bytes(b"PK\x03\x04renamed")

    with pytest.raises(InventoryError, match="MSI OLE header|EXE PE header"):
        verify_release_inventory(release, evidence, VERSION, REPOSITORY)


def test_inventory_rejects_evidence_bound_to_different_hash(tmp_path):
    release, evidence = write_valid_release(tmp_path)
    record = evidence / "UTHelper-2.2.0.apk.verification.json"
    value = json.loads(record.read_text(encoding="utf-8"))
    value["sha256"] = "0" * 64
    record.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(InventoryError, match="evidence hash"):
        verify_release_inventory(release, evidence, VERSION, REPOSITORY)


def test_unknown_evidence_key_and_unexpected_release_asset_fail_closed(tmp_path):
    release, evidence = write_valid_release(tmp_path)
    record = evidence / "UTHelper-2.2.0.apk.verification.json"
    value = json.loads(record.read_text(encoding="utf-8"))
    value["trusted"] = True
    record.write_text(json.dumps(value), encoding="utf-8")
    (release / "surprise.msix").write_bytes(b"fake")

    with pytest.raises(InventoryError, match="required inventory"):
        verify_release_inventory(release, evidence, VERSION, REPOSITORY)


def test_manifest_signer_fingerprint_must_equal_native_evidence(tmp_path):
    release, evidence = write_valid_release(tmp_path, with_manifest=True)
    path = release / "release-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["packages"][1]["certificate_fingerprint"] = "CD" * 32
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(InventoryError, match="certificate fingerprint evidence"):
        verify_release_inventory(
            release, evidence, VERSION, REPOSITORY, manifest_path=path
        )


def test_manifest_package_url_must_be_exact_canonical_asset_url(tmp_path):
    release, evidence = write_valid_release(tmp_path, with_manifest=True)
    path = release / "release-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    original = manifest["packages"][0]["url"]
    manifest["packages"][0]["url"] = original.rsplit("/", 1)[0] + "/stale/" + original.rsplit("/", 1)[1]
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(InventoryError, match="manifest package inventory"):
        verify_release_inventory(
            release, evidence, VERSION, REPOSITORY, manifest_path=path
        )


def test_manifest_architecture_must_equal_native_evidence(tmp_path):
    release, evidence = write_valid_release(tmp_path, with_manifest=True)
    path = release / "release-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["packages"][0]["architecture"] = "universal"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(InventoryError, match="architecture.*native evidence"):
        verify_release_inventory(
            release, evidence, VERSION, REPOSITORY, manifest_path=path
        )


def test_sha256sums_is_deterministic_and_covers_packages_plus_manifest(tmp_path):
    release, evidence = write_valid_release(tmp_path, with_manifest=True)
    manifest = release / "release-manifest.json"
    inventory = verify_release_inventory(
        release, evidence, VERSION, REPOSITORY, manifest_path=manifest
    )
    output = release / "SHA256SUMS"

    write_sha256sums(inventory, manifest, output)
    first = output.read_bytes()
    write_sha256sums(inventory, manifest, output)

    assert output.read_bytes() == first
    assert first.endswith(b"\n") and b"\r\n" not in first
    assert b"SHA256SUMS" not in first
    assert len(first.decode("ascii").splitlines()) == 5


@pytest.mark.parametrize("mutation", ["missing", "extra", "tampered", "traversal"])
def test_checksum_gate_rejects_invalid_entries(tmp_path, mutation):
    release, evidence = write_valid_release(tmp_path, with_manifest=True, with_checksums=True)
    checksum = release / "SHA256SUMS"
    lines = checksum.read_text(encoding="ascii").splitlines()
    if mutation == "missing":
        lines.pop()
    elif mutation == "extra":
        lines.append(f"{'0' * 64}  extra.apk")
    elif mutation == "tampered":
        lines[0] = f"{'0' * 64}  UTHelper-2.2.0.ipa"
    else:
        lines[0] = f"{'0' * 64}  ../UTHelper-2.2.0.ipa"
    checksum.write_text("\n".join(lines) + "\n", encoding="ascii")

    with pytest.raises(InventoryError, match="SHA256SUMS"):
        verify_release_inventory(
            release,
            evidence,
            VERSION,
            REPOSITORY,
            manifest_path=release / "release-manifest.json",
            checksums_path=checksum,
        )


def test_generator_emits_schema_three_with_manual_ios_sideload(tmp_path):
    release, evidence = write_valid_release(tmp_path)

    manifest = generate_manifest_from_verified_inventory(
        release,
        evidence,
        version=VERSION,
        repository=REPOSITORY,
        minimum_supported_version="2.1.0",
    )

    assert manifest["schema_version"] == 3
    assert len(manifest["packages"]) == 4
    ios = manifest["packages"][0]
    assert ios["install_channel"] == "sideload"
    assert ios["install_strategy"] == {"kind": "manual_sideload"}
    assert ios["signature_kind"] == "unsigned-resign-required"
    assert ios["signer_identity"] == ""
    assert ios["certificate_fingerprint"] == ""


def test_inventory_rejects_signed_identity_for_unsigned_ios(tmp_path):
    release, evidence = write_valid_release(tmp_path)
    record_path = evidence / "UTHelper-2.2.0.ipa.verification.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["signer_identity"] = "CN=Unexpected"
    record_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(InventoryError, match="unsigned iOS"):
        verify_release_inventory(release, evidence, VERSION, REPOSITORY)


@pytest.mark.parametrize("suffix", [".apk", ".exe", ".msi"])
def test_inventory_rejects_missing_pinned_identity(tmp_path, suffix):
    release, evidence = write_valid_release(tmp_path)
    name = next(path.name for path in release.iterdir() if path.suffix == suffix)
    record_path = evidence / f"{name}.verification.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["certificate_fingerprint"] = ""
    record_path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(InventoryError, match="pinned signature"):
        verify_release_inventory(release, evidence, VERSION, REPOSITORY)


@pytest.mark.parametrize(
    "script", ["scripts/release_inventory.py", "scripts/generate_release_manifest.py"]
)
def test_canonical_release_clis_bootstrap_in_isolated_python(script):
    completed = subprocess.run(
        [sys.executable, "-I", script, "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
