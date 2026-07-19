"""Generate the update manifest consumed by UTHelper clients."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _asset(path: Path, url: str, architecture: str) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return {
        "architecture": architecture,
        "name": path.name,
        "url": url,
        "size": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--android", type=Path)
    parser.add_argument("--windows", type=Path)
    parser.add_argument("--minimum-supported-version")
    parser.add_argument("--output", type=Path, default=Path("release-manifest.json"))
    args = parser.parse_args()

    version = args.version.lstrip("v")
    tag = f"v{version}"
    base_url = f"https://github.com/{args.repository}/releases/download/{tag}"
    assets = {}
    if args.android:
        assets["android"] = _asset(
            args.android, f"{base_url}/{args.android.name}", "universal"
        )
    if args.windows:
        assets["windows"] = _asset(
            args.windows, f"{base_url}/{args.windows.name}", "x64"
        )

    manifest = {
        "schema": 1,
        "version": version,
        "minimum_supported_version": (
            args.minimum_supported_version or version
        ).lstrip("v"),
        "assets": assets,
    }
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
