"""Prepare deterministic launch aliases and strip debug/bloat artifacts from a Flet Windows bundle."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

try:
    from scripts.release_metadata import ReleaseMetadataError, write_runtime_version
except ModuleNotFoundError:
    from release_metadata import ReleaseMetadataError, write_runtime_version


_DEFAULT_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
_RUNTIME_VERSION_RELATIVE = Path("app") / "assets" / "release-version"


class BundlePreparationError(RuntimeError):
    """Raised when a Windows bundle cannot be prepared safely."""


def _unlink_bundle_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        try:
            os.chmod(path, stat.S_IWRITE)
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise BundlePreparationError(
                f"Could not remove forbidden bundle artifact {path}: {exc}"
            ) from exc
    except OSError as exc:
        raise BundlePreparationError(
            f"Could not remove forbidden bundle artifact {path}: {exc}"
        ) from exc


def _is_dlls_bloat_or_debug_binary(filename: str) -> bool:
    lower = filename.lower()
    if lower.endswith(("_d.pyd", "_d.dll")):
        return True
    if lower.endswith(".pyd") and "_d.cp" in lower:
        return True
    if lower.endswith(".dll") and (lower.startswith("tcl") or lower.startswith("tk")):
        return True
    return False


def _strip_debug_and_unused_artifacts(root: Path) -> None:
    """Remove CPython debug binaries, PDB symbols, and unused Tcl/Tk DLLs."""
    dlls_dir = root / "DLLs"
    if dlls_dir.is_dir():
        for entry in sorted(dlls_dir.iterdir()):
            if entry.is_symlink() or entry.is_file():
                if _is_dlls_bloat_or_debug_binary(entry.name):
                    _unlink_bundle_file(entry)

    for entry in sorted(root.rglob("*")):
        if not (entry.is_symlink() or entry.is_file()):
            continue
        lower = entry.name.lower()
        if (
            lower.endswith(".pdb")
            or lower.endswith(("_d.pyd", "_d.dll"))
            or (lower.endswith(".pyd") and "_d.cp" in lower)
        ):
            _unlink_bundle_file(entry)


def _sync_bundle_runtime_version(root: Path, pyproject: Path | None) -> None:
    """Keep packaged ``app/assets/release-version`` synchronized with ``pyproject.toml``."""
    target = root / _RUNTIME_VERSION_RELATIVE
    if pyproject is not None:
        source = Path(pyproject)
    elif (root / "app").is_dir() and _DEFAULT_PYPROJECT.is_file():
        source = _DEFAULT_PYPROJECT
    else:
        return
    try:
        write_runtime_version(source, target)
    except ReleaseMetadataError as exc:
        raise BundlePreparationError(
            f"Could not sync packaged release version from {source}: {exc}"
        ) from exc


def prepare_windows_bundle(
    bundle_dir: Path,
    *,
    pyproject: Path | None = None,
) -> Path:
    """Strip debug/bloat artifacts, sync runtime version, and create ``UTHelperAutostart.exe``."""
    root = bundle_dir.resolve()
    runner = root / "UTHelper.exe"
    alias = root / "UTHelperAutostart.exe"
    temporary_alias = root / ".UTHelperAutostart.exe.tmp"

    if not root.is_dir():
        raise BundlePreparationError(f"Bundle directory does not exist: {root}")
    if not runner.is_file():
        raise BundlePreparationError(f"Flet runner does not exist: {runner}")

    _strip_debug_and_unused_artifacts(root)
    _sync_bundle_runtime_version(root, pyproject)

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
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=None,
        help="Optional pyproject.toml path used to sync app/assets/release-version",
    )
    args = parser.parse_args(argv)
    try:
        alias = prepare_windows_bundle(args.bundle_dir, pyproject=args.pyproject)
    except BundlePreparationError as exc:
        print(f"Windows bundle preparation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Windows bundle prepared: {alias}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
