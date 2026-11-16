"""Generate schema 3 only from the exact native-verified release inventory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.parse

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
for _import_root in reversed((_REPOSITORY_ROOT, _SOURCE_ROOT)):
    _import_path = str(_import_root)
    sys.path[:] = [entry for entry in sys.path if entry != _import_path]
    sys.path.insert(0, _import_path)

try:
    from scripts.release_inventory import InventoryError, verify_release_inventory
    from scripts.release_metadata import ReleaseMetadataError, release_build_number
except ModuleNotFoundError:  # Direct ``python scripts/generate_release_manifest.py``.
    from release_inventory import InventoryError, verify_release_inventory
    from release_metadata import ReleaseMetadataError, release_build_number


def generate_manifest_from_verified_inventory(
    release_dir: Path,
    evidence_dir: Path,
    *,
    version: str,
    repository: str = "Chouwzi/UTHelper",
    minimum_supported_version: str | None = None,
) -> dict:
    inventory = verify_release_inventory(
        release_dir,
        evidence_dir,
        version,
        repository,
    )
    minimum = minimum_supported_version or version
    try:
        release_build_number(minimum)
    except ReleaseMetadataError as exc:
        raise InventoryError("minimum supported version must be numeric X.Y.Z") from exc
    base = f"https://github.com/{repository}/releases/download/v{version}"
    packages = []
    for item in inventory.packages:
        evidence = item.evidence
        suffix = item.path.suffix.lower()
        if evidence.platform == "ios":
            package_type = "ipa"
            install_channel = "sideload"
            strategy = {"kind": "manual_sideload"}
        elif evidence.platform == "android":
            package_type = "apk"
            install_channel = "sideload"
            strategy = {"kind": "android_package_installer"}
        elif suffix == ".msi":
            package_type = "msi"
            install_channel = "msi"
            strategy = {"kind": "launch_msi"}
        else:
            package_type = "exe"
            install_channel = "bootstrapper"
            strategy = {"kind": "launch_bootstrapper"}
        packages.append(
            {
                "platform": evidence.platform,
                "architecture": evidence.architecture,
                "package_type": package_type,
                "install_channel": install_channel,
                "url": f"{base}/{urllib.parse.quote(item.name)}",
                "sha256": item.sha256,
                "size": item.size,
                "signature_kind": evidence.signature_kind,
                "signer_identity": evidence.signer_identity,
                "certificate_fingerprint": evidence.certificate_fingerprint,
                "install_strategy": strategy,
            }
        )
    return {
        "schema_version": 3,
        "release_version": version,
        "minimum_supported_version": minimum,
        "published_at": "1970-01-01T00:00:00Z",
        "release_notes_url": (
            f"https://github.com/{repository}/releases/tag/v{version}"
        ),
        "packages": packages,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--minimum-supported-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = generate_manifest_from_verified_inventory(
        args.release_dir,
        args.evidence_dir,
        version=args.version,
        repository=args.repository,
        minimum_supported_version=args.minimum_supported_version,
    )
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
