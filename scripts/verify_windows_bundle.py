"""Fail-closed integrity checks for a Flet Windows bundle."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


class BundleVerificationError(RuntimeError):
    """Raised when a Windows bundle is missing a required runtime artifact."""


def inspect_bundle(bundle_dir: Path) -> tuple[str, ...]:
    """Return every detected integrity issue without mutating the bundle."""
    root = bundle_dir.resolve()
    issues: list[str] = []

    if not root.is_dir():
        return (f"Bundle directory does not exist: {root}",)
    if not (root / "UTHelper.exe").is_file():
        issues.append("UTHelper.exe is missing")
    if not any(root.glob("python3*.dll")):
        issues.append("Python runtime DLL is missing")
    if not (root / "flutter_windows.dll").is_file():
        issues.append("Flutter runtime DLL is missing")

    encodings = root / "Lib" / "encodings"
    if not encodings.is_dir() or not any(encodings.glob("__init__.py*")):
        issues.append("Python filesystem encodings are missing")

    app_dir = root / "app"
    if not app_dir.is_dir() or not any(app_dir.glob("main.py*")):
        issues.append("compiled application entry is missing")

    if not (root / "site-packages").is_dir():
        issues.append("site-packages directory is missing")

    return tuple(issues)


def verify_bundle(bundle_dir: Path) -> None:
    """Raise with a complete diagnostic when the bundle is incomplete."""
    issues = inspect_bundle(bundle_dir)
    if issues:
        raise BundleVerificationError("; ".join(issues))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a Flet Windows bundle")
    parser.add_argument("bundle_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        verify_bundle(args.bundle_dir)
    except BundleVerificationError as exc:
        print(f"Windows bundle verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Windows bundle verified: {args.bundle_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
