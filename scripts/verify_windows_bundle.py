"""Fail-closed integrity and PE Debug-CRT checks for a Flet Windows bundle."""

from __future__ import annotations

import argparse
import filecmp
import re
import struct
import sys
from collections.abc import Sequence
from pathlib import Path


class BundleVerificationError(RuntimeError):
    """Raised when a Windows bundle is missing a required runtime artifact or contains forbidden debug binaries."""


_NUMERIC_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_DEBUG_PYTHON_DLL_RE = re.compile(r"^python\d*_d\.dll$")
_RUNTIME_VERSION_PATH = Path("app") / "assets" / "release-version"
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
_FORBIDDEN_DEBUG_CRT_DLLS = frozenset(
    {
        "vcruntime140d.dll",
        "vcruntime140_1d.dll",
        "msvcp140d.dll",
        "msvcp140_1d.dll",
        "msvcp140_2d.dll",
        "ucrtbased.dll",
        "concrt140d.dll",
        "vccorlib140d.dll",
        "dart_bridge_d.dll",
        "python314_d.dll",
        "python3_d.dll",
    }
)
_PE_EXTENSIONS = frozenset({".exe", ".dll", ".pyd"})
_MAX_IMPORT_DESCRIPTORS = 4096


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


def _is_forbidden_debug_import(dll_name: str) -> bool:
    normalized = dll_name.strip().lower()
    if normalized in _FORBIDDEN_DEBUG_CRT_DLLS:
        return True
    return bool(_DEBUG_PYTHON_DLL_RE.fullmatch(normalized))


def _rva_to_file_offset(
    rva: int,
    sections: list[tuple[int, int, int, int]],
    file_len: int,
    headers_end: int,
) -> int | None:
    if rva <= 0:
        return None
    for virtual_address, virtual_size, raw_size, raw_ptr in sections:
        span = max(virtual_size, raw_size)
        if virtual_address <= rva < virtual_address + span:
            delta = rva - virtual_address
            if delta >= raw_size:
                return None
            offset = raw_ptr + delta
            return offset if 0 <= offset < file_len else None
    if rva < headers_end and rva < file_len:
        return rva
    return None


def _read_c_string(data: bytes, offset: int, max_len: int = 256) -> str | None:
    if offset < 0 or offset >= len(data):
        return None
    end = data.find(b"\x00", offset, min(len(data), offset + max_len))
    if end < 0:
        return None
    try:
        return data[offset:end].decode("ascii")
    except UnicodeDecodeError:
        return data[offset:end].decode("latin1")


def scan_pe_imported_dlls(data: bytes) -> tuple[str, ...]:
    """Extract imported DLL names from ``IMAGE_DIRECTORY_ENTRY_IMPORT`` (and delay imports).

    Non-PE stub files (such as tiny synthetic unit-test placeholders) return ``()``.
    """
    if len(data) < 0x40 or data[:2] != b"MZ":
        return ()
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if pe_offset < 0 or pe_offset + 24 > len(data):
        return ()
    if data[pe_offset : pe_offset + 4] != b"PE\x00\x00":
        return ()

    num_sections = struct.unpack_from("<H", data, pe_offset + 6)[0]
    opt_header_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
    opt_offset = pe_offset + 24
    if opt_header_size < 96 or opt_offset + opt_header_size > len(data):
        return ()

    magic = struct.unpack_from("<H", data, opt_offset)[0]
    if magic == 0x20B:  # PE32+ (64-bit)
        if opt_header_size < 112:
            return ()
        num_rva_and_sizes = struct.unpack_from("<I", data, opt_offset + 108)[0]
        data_dir_offset = opt_offset + 112
    elif magic == 0x10B:  # PE32 (32-bit)
        num_rva_and_sizes = struct.unpack_from("<I", data, opt_offset + 92)[0]
        data_dir_offset = opt_offset + 96
    else:
        return ()

    sec_table_offset = opt_offset + opt_header_size
    headers_end = sec_table_offset + num_sections * 40
    if headers_end > len(data):
        return ()

    sections: list[tuple[int, int, int, int]] = []
    for idx in range(num_sections):
        entry_off = sec_table_offset + idx * 40
        virtual_size, virtual_address, raw_size, raw_ptr = struct.unpack_from(
            "<IIII", data, entry_off + 8
        )
        sections.append((virtual_address, virtual_size, raw_size, raw_ptr))

    imported: list[str] = []

    # DataDirectory[1] == IMAGE_DIRECTORY_ENTRY_IMPORT
    if num_rva_and_sizes >= 2 and data_dir_offset + 16 <= opt_offset + opt_header_size:
        import_rva, import_size = struct.unpack_from("<II", data, data_dir_offset + 8)
        if import_rva > 0 and import_size >= 20:
            desc_offset = _rva_to_file_offset(
                import_rva, sections, len(data), headers_end
            )
            if desc_offset is not None:
                for idx in range(_MAX_IMPORT_DESCRIPTORS):
                    cur = desc_offset + idx * 20
                    if cur + 20 > len(data):
                        break
                    orig_thunk, _ts, _fwd, name_rva, first_thunk = struct.unpack_from(
                        "<IIIII", data, cur
                    )
                    if orig_thunk == 0 and name_rva == 0 and first_thunk == 0:
                        break
                    if name_rva != 0:
                        name_off = _rva_to_file_offset(
                            name_rva, sections, len(data), headers_end
                        )
                        if name_off is not None:
                            dll_name = _read_c_string(data, name_off)
                            if dll_name:
                                imported.append(dll_name)

    # DataDirectory[13] == IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT
    if (
        num_rva_and_sizes >= 14
        and data_dir_offset + 14 * 8 <= opt_offset + opt_header_size
    ):
        delay_rva, delay_size = struct.unpack_from(
            "<II", data, data_dir_offset + 13 * 8
        )
        if delay_rva > 0 and delay_size >= 32:
            delay_off = _rva_to_file_offset(
                delay_rva, sections, len(data), headers_end
            )
            if delay_off is not None:
                for idx in range(_MAX_IMPORT_DESCRIPTORS):
                    cur = delay_off + idx * 32
                    if cur + 32 > len(data):
                        break
                    attrs, dll_name_rva = struct.unpack_from("<II", data, cur)
                    if attrs == 0 and dll_name_rva == 0:
                        break
                    if dll_name_rva != 0 and (attrs & 1):
                        name_off = _rva_to_file_offset(
                            dll_name_rva, sections, len(data), headers_end
                        )
                        if name_off is not None:
                            dll_name = _read_c_string(data, name_off)
                            if dll_name:
                                imported.append(dll_name)

    return tuple(imported)


def _inspect_debug_binaries_and_pe_imports(root: Path, issues: list[str]) -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        lower_name = path.name.lower()

        if lower_name.endswith(".pdb"):
            issues.append(f"forbidden debug symbol file present: {rel}")
            continue

        if (
            lower_name.endswith(("_d.pyd", "_d.dll"))
            or (lower_name.endswith(".pyd") and "_d.cp" in lower_name)
        ):
            issues.append(f"forbidden debug binary present: {rel}")

        if (
            path.parent == root / "DLLs"
            and lower_name.endswith(".dll")
            and (lower_name.startswith("tcl") or lower_name.startswith("tk"))
        ):
            issues.append(f"unused Tcl/Tk runtime DLL present: {rel}")

        if path.suffix.lower() in _PE_EXTENSIONS:
            try:
                raw = path.read_bytes()
            except OSError as exc:
                issues.append(f"unreadable PE binary {rel}: {exc}")
                continue
            bad_imports = sorted(
                {
                    imp
                    for imp in scan_pe_imported_dlls(raw)
                    if _is_forbidden_debug_import(imp)
                }
            )
            if bad_imports:
                issues.append(
                    f"forbidden debug CRT import in {rel}: {', '.join(bad_imports)}"
                )


def inspect_bundle(
    bundle_dir: Path,
    *,
    expected_version: str | None = None,
    strict_site_packages: bool = False,
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
    if not any(
        p.is_file() and not p.name.lower().endswith("_d.dll")
        for p in root.glob("python3*.dll")
    ):
        issues.append("Python runtime DLL is missing")
    if not (root / "flutter_windows.dll").is_file():
        issues.append("Flutter runtime DLL is missing")
    if not (root / "flutter_inappwebview_windows_plugin.dll").is_file():
        issues.append("flutter_inappwebview_windows_plugin.dll is missing")
    if not (root / "WebView2Loader.dll").is_file():
        issues.append("WebView2Loader.dll is missing")

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
    office_viewer_dir = app_dir / "assets" / "office_viewer"
    for asset_name in _REQUIRED_OFFICE_VIEWER_ASSETS:
        asset_path = office_viewer_dir / asset_name
        if not asset_path.is_file() or asset_path.stat().st_size <= 0:
            issues.append(f"packaged office_viewer asset is missing: {asset_name}")
    pdf_viewer_dir = app_dir / "assets" / "pdf_viewer"
    for asset_name in _REQUIRED_PDF_VIEWER_ASSETS:
        asset_path = pdf_viewer_dir / asset_name
        if not asset_path.is_file() or asset_path.stat().st_size <= 0:
            issues.append(f"packaged pdf_viewer asset is missing: {asset_name}")

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
        quiz_pkg = site_packages / "flet_uth_quiz"
        if (
            not quiz_pkg.is_dir()
            or not any(quiz_pkg.glob("__init__.py*"))
            or not any(quiz_pkg.glob("quiz_player.py*"))
        ):
            issues.append("packaged flet_uth_quiz extension is missing")

        # Enforce extended Windows runtime package checks when explicitly requested
        # or when inspecting a full Flet-packaged site-packages tree (.dist-info present).
        if strict_site_packages or any(site_packages.glob("*.dist-info")):
            if not any((site_packages / "webview2").rglob("_webview2_*.pyd")):
                issues.append("WebView2 WinRT projection is missing")
            if not any(
                (site_packages / "winrt").glob("_winrt_windows_foundation*.pyd")
            ):
                issues.append("Windows.Foundation projection is missing")
            for module in ("win32gui", "win32process"):
                if not any(win32.glob(f"{module}*.pyd")):
                    issues.append(f"packaged {module} extension is missing")
            if not any((site_packages / "pypdfium2").glob("__init__.py*")):
                issues.append("packaged pypdfium2 module is missing")
            if not any((site_packages / "lxml").glob("etree*.pyd")):
                issues.append("packaged lxml.etree extension is missing")
            if not any((site_packages / "filelock").glob("__init__.py*")):
                issues.append("packaged filelock module is missing")

    _inspect_debug_binaries_and_pe_imports(root, issues)
    return tuple(issues)


def verify_bundle(
    bundle_dir: Path,
    *,
    expected_version: str | None = None,
    strict_site_packages: bool = False,
) -> None:
    """Raise with a complete diagnostic when the bundle is incomplete or unsafe."""
    issues = inspect_bundle(
        bundle_dir,
        expected_version=expected_version,
        strict_site_packages=strict_site_packages,
    )
    if issues:
        raise BundleVerificationError("; ".join(issues))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a Flet Windows bundle")
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument(
        "--strict-site-packages",
        action="store_true",
        help="Verify all Windows runtime extensions (WebView2 WinRT, Foundation, win32gui/process, pypdfium2, lxml, filelock)",
    )
    args = parser.parse_args(argv)
    try:
        verify_bundle(
            args.bundle_dir,
            expected_version=args.expected_version,
            strict_site_packages=args.strict_site_packages,
        )
    except BundleVerificationError as exc:
        print(f"Windows bundle verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Windows bundle verified: {args.bundle_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
