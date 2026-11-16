"""Prepare deterministic launch aliases required by a Flet Windows bundle."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path


class BundlePreparationError(RuntimeError):
    """Raised when a Windows bundle cannot be prepared safely."""


def prepare_windows_bundle(bundle_dir: Path) -> Path:
    """Create an argument-free autostart runner alias beside ``UTHelper.exe``."""
    root = bundle_dir.resolve()
    runner = root / "UTHelper.exe"
    alias = root / "UTHelperAutostart.exe"
    temporary_alias = root / ".UTHelperAutostart.exe.tmp"

    if not root.is_dir():
        raise BundlePreparationError(f"Bundle directory does not exist: {root}")
    if not runner.is_file():
        raise BundlePreparationError(f"Flet runner does not exist: {runner}")

    try:
        shutil.copy2(runner, temporary_alias)
        os.replace(temporary_alias, alias)
    except OSError as exc:
        raise BundlePreparationError(
            f"Could not create autostart runner alias: {exc}"
        ) from exc
    finally:
        temporary_alias.unlink(missing_ok=True)

    return alias


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a Flet Windows bundle")
    parser.add_argument("bundle_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        alias = prepare_windows_bundle(args.bundle_dir)
    except BundlePreparationError as exc:
        print(f"Windows bundle preparation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Windows bundle prepared: {alias}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
