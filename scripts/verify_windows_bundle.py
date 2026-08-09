"""Fail-closed integrity checks for a Flet Windows bundle."""

from __future__ import annotations

import argparse
import filecmp
import re
import sys
from collections.abc import Sequence
from pathlib import Path


class BundleVerificationError(RuntimeError):
    """Raised when a Windows bundle is missing a required runtime artifact."""


_NUMERIC_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_RUNTIME_VERSION_PATH = Path("app") / "assets" / "release-version"


def _read_runtime_version(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > 64 or not raw.endswith(b"\n"):
        return None
    try:
        value = raw[:-1].decode("ascii")
    except UnicodeDecodeError:
        return None
    return value if _NUMERIC_VERSION.fullmatch(value) else None


def inspect_bundle(
    bundle_dir: Path,
    *,
    expected_version: str | None = None,
) -> tuple[str, ...]:
    """Return every detected integrity issue without mutating the bundle."""
    root = bundle_dir.resolve()
    issues: list[str] = []

    if not root.is_dir():
        return (f"Bundle directory does not exist: {root}",)
    if not (root / "UTHelper.exe").is_file():
        issues.append("UTHelper.exe is missing")
    autostart_runner = root / "UTHelperAutostart.exe"
    if not autostart_runner.is_file():
        issues.append("UTHelperAutostart.exe is missing")
    elif (root / "UTHelper.exe").is_file() and not filecmp.cmp(
        root / "UTHelper.exe", autostart_runner, shallow=False
    ):
        issues.append("UTHelperAutostart.exe is not byte-identical to UTHelper.exe")
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
    runtime_version = _read_runtime_version(root / _RUNTIME_VERSION_PATH)
    if runtime_version is None:
        issues.append("packaged release version is missing or invalid")
    elif expected_version is not None and runtime_version != expected_version:
        issues.append(
            f"packaged release version mismatch: {runtime_version} != {expected_version}"
        )

    site_packages = root / "site-packages"
    if not site_packages.is_dir():
        issues.append("site-packages directory is missing")
    else:
        if not any(
            (site_packages / "winrt").glob("_winrt_windows_applicationmodel*.pyd")
        ):
            issues.append("Windows.ApplicationModel projection is missing")
        win32 = site_packages / "win32"
        for module in ("win32api", "win32event", "win32security"):
            if not any(win32.glob(f"{module}*.pyd")):
                issues.append(f"packaged {module} extension is missing")
        for module, label in (
            ("win32con", "win32con"),
            ("winerror", "winerror"),
            ("pywintypes", "pywintypes loader"),
        ):
            if not any((win32 / "lib").glob(f"{module}.py*")):
                issues.append(f"packaged {label} module is missing")
        if not any((site_packages / "pywin32_system32").glob("pywintypes3*.dll")):
            issues.append("packaged pywintypes runtime is missing")

    return tuple(issues)


def verify_bundle(bundle_dir: Path, *, expected_version: str | None = None) -> None:
    """Raise with a complete diagnostic when the bundle is incomplete."""
    issues = inspect_bundle(bundle_dir, expected_version=expected_version)
    if issues:
        raise BundleVerificationError("; ".join(issues))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a Flet Windows bundle")
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("--expected-version")
    args = parser.parse_args(argv)
    try:
        verify_bundle(args.bundle_dir, expected_version=args.expected_version)
    except BundleVerificationError as exc:
        print(f"Windows bundle verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Windows bundle verified: {args.bundle_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
