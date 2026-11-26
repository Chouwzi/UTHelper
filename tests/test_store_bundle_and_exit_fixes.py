"""TDD regression suite for Windows Store bundle hygiene, native exit, and pipelines."""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

from core.distribution import DistributionChannel, build_distribution_profile
from scripts.prepare_windows_bundle import prepare_windows_bundle
from scripts.release_metadata import read_project_version
from scripts.verify_windows_bundle import (
    BundleVerificationError,
    inspect_bundle,
    verify_bundle,
)
import src.main as application


ROOT = Path(__file__).resolve().parents[1]
CURRENT_PROJECT_VERSION = read_project_version(ROOT / "pyproject.toml")
PACKAGE_MSIX_PS1 = ROOT / "scripts" / "package_msix.ps1"
VERIFY_MSIX_PS1 = ROOT / "scripts" / "verify_msix_release.ps1"
BUILD_ANDROID_PS1 = ROOT / "scripts" / "build_android.ps1"
SINGLE_INSTANCE_E2E_PS1 = ROOT / "scripts" / "test_windows_single_instance_e2e.ps1"
MSSTORE_WORKFLOW = ROOT / ".github" / "workflows" / "build-msstore.yml"


def _build_minimal_pe_with_imports(
    imported_dlls: list[str],
    *,
    pe32_plus: bool = True,
) -> bytes:
    """Construct a valid minimal PE32/PE32+ binary with an IMAGE_DIRECTORY_ENTRY_IMPORT table."""
    dos_header = bytearray(0x80)
    dos_header[0:2] = b"MZ"
    pe_offset = 0x80
    struct.pack_into("<I", dos_header, 0x3C, pe_offset)

    pe_sig = b"PE\x00\x00"
    machine = 0x8664 if pe32_plus else 0x014C
    num_sections = 1
    opt_header_size = 240 if pe32_plus else 224
    coff_header = struct.pack(
        "<HHIIIHH",
        machine,
        num_sections,
        0,
        0,
        0,
        opt_header_size,
        0x2022,
    )

    opt_header = bytearray(opt_header_size)
    struct.pack_into("<H", opt_header, 0, 0x20B if pe32_plus else 0x10B)
    num_rva_offset = 108 if pe32_plus else 92
    data_dir_offset = 112 if pe32_plus else 96
    struct.pack_into("<I", opt_header, num_rva_offset, 16)

    section_rva = 0x1000
    section_raw_ptr = 0x200

    # Layout inside .rdata section at RVA 0x1000:
    # - (len(imported_dlls) + 1) * 20-byte IMAGE_IMPORT_DESCRIPTORs
    # - 8-byte null thunk array per DLL
    # - null-terminated DLL name strings
    descriptors_size = (len(imported_dlls) + 1) * 20
    thunk_size = 8 if pe32_plus else 4
    thunks_offset = descriptors_size
    names_offset = thunks_offset + len(imported_dlls) * thunk_size

    rdata = bytearray(names_offset)
    for idx, dll_name in enumerate(imported_dlls):
        thunk_rva = section_rva + thunks_offset + idx * thunk_size
        name_rva = section_rva + len(rdata)
        rdata.extend(dll_name.encode("ascii") + b"\x00")
        struct.pack_into(
            "<IIIII",
            rdata,
            idx * 20,
            thunk_rva,
            0,
            0,
            name_rva,
            thunk_rva,
        )

    virtual_size = len(rdata)
    raw_size = max(0x200, ((virtual_size + 0x1FF) // 0x200) * 0x200)
    rdata_padded = bytes(rdata).ljust(raw_size, b"\x00")

    # DataDirectory[1] = Import Table (RVA, Size)
    struct.pack_into("<II", opt_header, data_dir_offset + 8, section_rva, descriptors_size)

    section_header = struct.pack(
        "<8sIIIIIIHHI",
        b".rdata\x00\x00",
        virtual_size,
        section_rva,
        raw_size,
        section_raw_ptr,
        0,
        0,
        0,
        0,
        0x40000040,
    )

    headers = bytes(dos_header) + pe_sig + coff_header + bytes(opt_header) + section_header
    assert len(headers) <= section_raw_ptr
    return headers.ljust(section_raw_ptr, b"\x00") + rdata_padded


def _write_valid_bundle(root: Path) -> None:
    release_pe = _build_minimal_pe_with_imports(
        ["KERNEL32.dll", "VCRUNTIME140.dll", "ucrtbase.dll"]
    )
    (root / "Lib" / "encodings").mkdir(parents=True)
    (root / "DLLs").mkdir(parents=True)
    (root / "app" / "assets" / "office_viewer").mkdir(parents=True)
    (root / "app" / "assets" / "pdf_viewer").mkdir(parents=True)
    (root / "site-packages" / "winrt").mkdir(parents=True)
    (root / "site-packages" / "win32" / "lib").mkdir(parents=True)
    (root / "site-packages" / "pywin32_system32").mkdir(parents=True)
    (root / "site-packages" / "flet_uth_quiz").mkdir(parents=True)

    (root / "UTHelper.exe").write_bytes(release_pe)
    (root / "UTHelperAutostart.exe").write_bytes(release_pe)
    (root / "python314.dll").write_bytes(release_pe)
    (root / "flutter_windows.dll").write_bytes(release_pe)
    (root / "flutter_inappwebview_windows_plugin.dll").write_bytes(release_pe)
    (root / "WebView2Loader.dll").write_bytes(release_pe)
    (root / "DLLs" / "_asyncio.pyd").write_bytes(release_pe)
    (root / "DLLs" / "sqlite3.dll").write_bytes(release_pe)

    (
        root
        / "site-packages"
        / "winrt"
        / "_winrt_windows_applicationmodel.cp314-win_amd64.pyd"
    ).write_bytes(release_pe)
    for module in ("win32api.pyd", "win32event.pyd", "win32security.pyd"):
        (root / "site-packages" / "win32" / module).write_bytes(release_pe)
    for module in ("win32con.pyc", "winerror.pyc", "pywintypes.pyc"):
        (root / "site-packages" / "win32" / "lib" / module).write_bytes(b"pyc")
    (root / "site-packages" / "pywin32_system32" / "pywintypes314.dll").write_bytes(
        release_pe
    )
    (root / "site-packages" / "flet_uth_quiz" / "__init__.pyc").write_bytes(b"pyc")
    (root / "site-packages" / "flet_uth_quiz" / "quiz_player.pyc").write_bytes(b"pyc")
    (root / "Lib" / "encodings" / "__init__.pyc").write_bytes(b"pyc")
    (root / "app" / "main.pyc").write_bytes(b"pyc")
    (root / "app" / "assets" / "release-version").write_bytes(
        f"{CURRENT_PROJECT_VERSION}\n".encode("ascii")
    )

    for asset_name in (
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
    ):
        (root / "app" / "assets" / "office_viewer" / asset_name).write_bytes(b"ok")
    for asset_name in (
        "pdf_host.html",
        "pdf_host.js",
        "pdf_viewer.css",
        "pdf.min.mjs",
        "pdf.worker.min.mjs",
    ):
        (root / "app" / "assets" / "pdf_viewer" / asset_name).write_bytes(b"ok")


# ---------------------------------------------------------------------------
# 1. MSIX-DBG-01: Debug binaries, PDBs, Tcl/Tk bloat, and PE Debug CRT scanner
# ---------------------------------------------------------------------------


def test_tkinter_is_never_imported_anywhere_in_src() -> None:
    forbidden = {"tkinter", "_tkinter"}
    violations: list[str] = []
    for py_file in sorted((ROOT / "src").rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    if top in forbidden:
                        violations.append(f"{py_file.relative_to(ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".", 1)[0]
                if top in forbidden:
                    violations.append(f"{py_file.relative_to(ROOT)}:{node.lineno}")
    assert violations == []


def test_prepare_windows_bundle_strips_debug_binaries_pdbs_and_tcltk(tmp_path: Path) -> None:
    _write_valid_bundle(tmp_path)
    (tmp_path / "UTHelperAutostart.exe").unlink()

    planted_bloat = [
        tmp_path / "DLLs" / "_asyncio_d.pyd",
        tmp_path / "DLLs" / "_ssl_d.pyd",
        tmp_path / "DLLs" / "sqlite3_d.dll",
        tmp_path / "DLLs" / "tcl90.dll",
        tmp_path / "DLLs" / "tcl9tk90.dll",
        tmp_path / "DLLs" / "tk86t.dll",
        tmp_path / "python314_d.pdb",
        tmp_path / "DLLs" / "_asyncio.pdb",
        tmp_path / "site-packages" / "win32" / "win32api.pdb",
    ]
    for path in planted_bloat:
        path.write_bytes(b"debug-artifact")

    alias = prepare_windows_bundle(tmp_path)

    assert alias == tmp_path / "UTHelperAutostart.exe"
    assert alias.is_file()
    for path in planted_bloat:
        assert not path.exists(), f"Expected bloat artifact to be stripped: {path}"

    # Release binaries must remain untouched
    assert (tmp_path / "DLLs" / "_asyncio.pyd").is_file()
    assert (tmp_path / "DLLs" / "sqlite3.dll").is_file()
    assert inspect_bundle(tmp_path, expected_version=CURRENT_PROJECT_VERSION) == ()


@pytest.mark.parametrize(
    "planted_relpath",
    [
        "DLLs/_ctypes_d.pyd",
        "DLLs/sqlite3_d.dll",
        "python314_d.dll",
        "site-packages/custom_ext_d.pyd",
        "DLLs/tcl90.dll",
        "DLLs/tcl9tk90.dll",
        "DLLs/tk86t.dll",
        "UTHelper.pdb",
        "DLLs/_ssl.pdb",
    ],
)
def test_verify_windows_bundle_fails_closed_on_debug_files_pdbs_or_tcltk(
    tmp_path: Path, planted_relpath: str
) -> None:
    _write_valid_bundle(tmp_path)
    target = tmp_path / planted_relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_build_minimal_pe_with_imports(["KERNEL32.dll"]))

    with pytest.raises(BundleVerificationError):
        verify_bundle(tmp_path, expected_version=CURRENT_PROJECT_VERSION)


@pytest.mark.parametrize(
    ("debug_crt_dll", "pe32_plus"),
    [
        ("vcruntime140d.dll", True),
        ("VCRUNTIME140_1D.dll", True),
        ("msvcp140d.dll", True),
        ("ucrtbased.dll", True),
        ("python314_d.dll", True),
        ("python3_d.dll", False),
    ],
)
def test_verify_windows_bundle_pe_scanner_rejects_debug_crt_imports(
    tmp_path: Path, debug_crt_dll: str, pe32_plus: bool
) -> None:
    _write_valid_bundle(tmp_path)
    # Disguised release filename importing a debug CRT DLL!
    bad_pe = _build_minimal_pe_with_imports(
        ["KERNEL32.dll", debug_crt_dll],
        pe32_plus=pe32_plus,
    )
    (tmp_path / "DLLs" / "disguised_release.pyd").write_bytes(bad_pe)

    with pytest.raises(BundleVerificationError) as exc_info:
        verify_bundle(tmp_path, expected_version=CURRENT_PROJECT_VERSION)

    msg = str(exc_info.value).lower()
    assert "disguised_release.pyd" in msg
    assert debug_crt_dll.lower() in msg


# ---------------------------------------------------------------------------
# 2. WIN-EXIT-01 & WIN-MUTEX-01: Packaged Windows exit & single-instance
# ---------------------------------------------------------------------------


def test_pyproject_windows_app_enables_hide_window_on_start() -> None:
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    win_app = (
        pyproject.get("tool", {})
        .get("flet", {})
        .get("windows", {})
        .get("app", {})
    )
    assert win_app.get("hide_window_on_start") is True


def test_is_embedded_windows_runner_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application, "_TEST_MODE", False)
    monkeypatch.setattr(application, "_is_source_checkout", lambda _p: False)
    monkeypatch.setenv("FLET_DART_BRIDGE_EXIT_PORT", "12345")

    assert application._is_embedded_windows_runner() is True

    # Test mode disables embedded hard-exit
    monkeypatch.setattr(application, "_TEST_MODE", True)
    assert application._is_embedded_windows_runner() is False

    # Development checkout disables embedded hard-exit
    monkeypatch.setattr(application, "_TEST_MODE", False)
    monkeypatch.setattr(application, "_is_source_checkout", lambda _p: True)
    assert application._is_embedded_windows_runner() is False

    # Standalone python.exe without FLET_DART_BRIDGE_* env vars disables hard-exit
    monkeypatch.setattr(application, "_is_source_checkout", lambda _p: False)
    monkeypatch.delenv("FLET_DART_BRIDGE_EXIT_PORT", raising=False)
    monkeypatch.delenv("FLET_DART_BRIDGE_PORT", raising=False)
    assert application._is_embedded_windows_runner() is False


@pytest.mark.parametrize("code", [0, 1, 2])
def test_exit_main_process_preserves_system_exit_for_cli_and_hard_exits_embedded(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    hard_exits: list[int] = []
    monkeypatch.setattr(
        application,
        "_terminate_embedded_windows_process",
        lambda exit_code: hard_exits.append(exit_code),
    )

    monkeypatch.setattr(application, "_is_embedded_windows_runner", lambda: False)
    with pytest.raises(SystemExit) as exc_info:
        application._exit_main_process(code)
    assert exc_info.value.code == code
    assert hard_exits == []

    monkeypatch.setattr(application, "_is_embedded_windows_runner", lambda: True)
    application._exit_main_process(code)
    assert hard_exits == [code]


def test_terminate_embedded_windows_process_binds_64bit_handle_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes
    from ctypes import wintypes

    terminate_calls: list[tuple[object, object]] = []

    class FakeFn:
        def __init__(self, impl):
            self._impl = impl
            self.argtypes = None
            self.restype = ctypes.c_int

        def __call__(self, *args):
            return self._impl(*args)

    def _get_current_process():
        assert fake_k32.GetCurrentProcess.restype == wintypes.HANDLE
        return (1 << 64) - 1

    def _terminate_process(handle, code):
        assert fake_k32.TerminateProcess.argtypes == [wintypes.HANDLE, wintypes.UINT]
        terminate_calls.append((handle, code))
        raise SystemExit("intercepted-after-terminate")

    fake_k32 = SimpleNamespace(
        GetCurrentProcess=FakeFn(_get_current_process),
        TerminateProcess=FakeFn(_terminate_process),
    )

    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application, "_hide_current_process_flutter_windows", lambda: None)
    monkeypatch.setattr(application.logging, "shutdown", lambda: None)
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda name, **_kw: fake_k32 if name == "kernel32" else SimpleNamespace(),
        raising=False,
    )

    with pytest.raises(SystemExit, match="intercepted-after-terminate"):
        application._terminate_embedded_windows_process(0)

    assert terminate_calls == [((1 << 64) - 1, 0)]


def test_embedded_windows_primary_destroy_cleans_up_before_hard_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeBroker:
        def close(self, *, timeout_seconds: float = 1.0) -> bool:
            events.append(f"broker.close:{timeout_seconds}")
            return True

    class FakeRuntime:
        def start(self) -> None:
            events.append("runtime.start")

        def attach_page(self, _page) -> None:
            pass

        def attach_asyncio_loop(self, _loop) -> None:
            pass

        def mark_phase(self, phase) -> None:
            events.append(f"runtime.phase:{phase.value}")

        def record_exception(self, _exc, _phase) -> str:
            return "ref"

        def close(self, *, clean: bool) -> None:
            events.append(f"runtime.close:{clean}")

    class FakeWindow:
        def __init__(self) -> None:
            self.prevent_close = False
            self.visible = True
            self.on_event = None

        async def destroy(self) -> None:
            events.append("original_window.destroy")

    class FakePage:
        def __init__(self) -> None:
            self.window = FakeWindow()
            self.controls = []

        def add(self, *_controls) -> None:
            pass

        def update(self) -> None:
            pass

    class FakeController:
        def _prepare_async_shutdown(self) -> None:
            events.append("controller.prepare_async_shutdown")

        def _shutdown_for_exit(self) -> None:
            events.append("controller.shutdown_for_exit")

    page = FakePage()

    def fake_run(**kwargs) -> None:
        async def _drive() -> None:
            coro = kwargs["main"](page)
            if inspect.isawaitable(coro):
                await coro
            await page.window.destroy()

        asyncio.run(_drive())

    fake_flet = SimpleNamespace(
        Page=FakePage,
        AppView=SimpleNamespace(
            WEB_BROWSER="web-browser",
            FLET_APP_HIDDEN="flet-app-hidden",
        ),
        run=fake_run,
    )
    monkeypatch.setitem(sys.modules, "flet", fake_flet)
    monkeypatch.setitem(
        sys.modules,
        "gui.compact_desktop",
        SimpleNamespace(main=lambda *_a, **_kw: FakeController()),
    )
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.setattr(application, "_is_source_checkout", lambda _p: False)
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False)
    monkeypatch.setattr(
        application,
        "bootstrap_windows_instance",
        lambda **_kw: SimpleNamespace(
            exit_code=None,
            broker=FakeBroker(),
            force_visible=False,
        ),
    )
    monkeypatch.setattr(application, "create_default_runtime", lambda *_a, **_kw: FakeRuntime())
    monkeypatch.setattr(application, "_show_startup_screen", lambda *_a, **_kw: None)
    monkeypatch.setattr(application, "_is_embedded_windows_runner", lambda **_kw: True)
    monkeypatch.setattr(
        application,
        "_hide_current_process_flutter_windows",
        lambda: events.append("hide_flutter_hwnd"),
    )
    monkeypatch.setattr(
        application,
        "_terminate_embedded_windows_process",
        lambda code: events.append(f"hard_exit:{code}"),
    )

    assert application.main() == 0
    assert "hide_flutter_hwnd" in events
    assert "controller.prepare_async_shutdown" in events
    assert "controller.shutdown_for_exit" in events
    assert "broker.close:1.0" in events
    assert "runtime.close:True" in events
    assert events[-1] == "hard_exit:0"
    assert "original_window.destroy" not in events


def test_early_flutter_window_hide_guard_hides_owned_hwnd_and_hard_exits_on_premature_destroy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes
    import os
    import time

    current_pid = os.getpid()
    shown_calls: list[tuple[int, int]] = []
    hard_exits: list[int] = []
    hwnd_owned = 0x12340001
    hwnd_foreign = 0x12340002
    state = {"visible": 1, "alive": 1}

    class FakeFn:
        def __init__(self, impl):
            self._impl = impl
            self.argtypes = None
            self.restype = ctypes.c_int

        def __call__(self, *args):
            return self._impl(*args)

    def _find_window_ex(_parent, child_after, class_name, _title):
        assert class_name == "FLUTTER_RUNNER_WIN32_WINDOW"
        after_val = int(child_after or 0)
        if after_val == 0:
            return hwnd_foreign
        if after_val == hwnd_foreign and state["alive"]:
            return hwnd_owned
        return 0

    def _get_window_thread_pid(hwnd, pid_ptr):
        pid_obj = ctypes.cast(pid_ptr, ctypes.POINTER(ctypes.c_ulong)).contents
        pid_obj.value = current_pid if int(hwnd) == hwnd_owned else current_pid + 999
        return 1

    def _is_window_visible(hwnd):
        return state["visible"] if int(hwnd) == hwnd_owned else 1

    def _show_window(hwnd, cmd):
        shown_calls.append((int(hwnd), int(cmd)))
        if int(hwnd) == hwnd_owned and int(cmd) == 0:
            state["visible"] = 0
        return 1

    def _is_window(hwnd):
        return state["alive"] if int(hwnd) == hwnd_owned else 1

    fake_user32 = SimpleNamespace(
        FindWindowExW=FakeFn(_find_window_ex),
        GetWindowThreadProcessId=FakeFn(_get_window_thread_pid),
        IsWindowVisible=FakeFn(_is_window_visible),
        ShowWindow=FakeFn(_show_window),
        IsWindow=FakeFn(_is_window),
    )

    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda name, **_kw: fake_user32 if name == "user32" else SimpleNamespace(),
        raising=False,
    )
    monkeypatch.setattr(
        application,
        "_terminate_embedded_windows_process",
        lambda code: hard_exits.append(code),
    )

    application._release_early_flutter_window_hide_guard()
    application._start_early_flutter_window_hide_guard()
    try:
        deadline = time.monotonic() + 1.0
        while not shown_calls and time.monotonic() < deadline:
            time.sleep(0.005)
        assert shown_calls == [(hwnd_owned, 0)]
        assert hard_exits == []

        # Simulate premature native WM_CLOSE destroying the HWND while guard is active
        state["alive"] = 0
        deadline = time.monotonic() + 1.0
        while not hard_exits and time.monotonic() < deadline:
            time.sleep(0.005)
        assert hard_exits == [0]
    finally:
        application._release_early_flutter_window_hide_guard()


def test_embedded_windows_app_target_latches_prevent_close_before_releasing_guard_or_publishing_splash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeWindow:
        def __init__(self) -> None:
            self.prevent_close = False
            self.visible = True
            self.width = 1280
            self.height = 720
            self.on_event = None

        async def wait_until_ready_to_show(self) -> None:
            assert self.prevent_close is True
            assert self.visible is False
            events.append("window.wait_until_ready_to_show")

        async def center(self) -> None:
            assert self.prevent_close is True
            assert self.visible is False
            events.append("window.center")

        async def destroy(self) -> None:
            events.append("original_window.destroy")

    class FakePage:
        def __init__(self) -> None:
            self.window = FakeWindow()
            self.controls = []

        def add(self, *_controls) -> None:
            pass

        def update(self) -> None:
            events.append(
                f"page.update:prevent_close={self.window.prevent_close},visible={self.window.visible}"
            )

    page = FakePage()

    def fake_run(**kwargs) -> None:
        coro = kwargs["main"](page)
        if inspect.isawaitable(coro):
            asyncio.run(coro)

    fake_flet = SimpleNamespace(
        Page=FakePage,
        AppView=SimpleNamespace(
            WEB_BROWSER="web-browser",
            FLET_APP_HIDDEN="flet-app-hidden",
        ),
        run=fake_run,
    )
    monkeypatch.setitem(sys.modules, "flet", fake_flet)
    monkeypatch.setitem(
        sys.modules,
        "gui.compact_desktop",
        SimpleNamespace(main=lambda *_a, **_kw: events.append("app_main") or SimpleNamespace()),
    )
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.setattr(application, "_is_source_checkout", lambda _p: False)
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False)
    monkeypatch.setattr(
        application,
        "bootstrap_windows_instance",
        lambda **_kw: SimpleNamespace(exit_code=None, broker=None, force_visible=False),
    )
    monkeypatch.setattr(application, "create_default_runtime", lambda *_a, **_kw: None)
    monkeypatch.setattr(application, "_is_embedded_windows_runner", lambda **_kw: True)
    monkeypatch.setattr(
        application,
        "_release_early_flutter_window_hide_guard",
        lambda: events.append("release_hide_guard"),
    )
    monkeypatch.setattr(
        application,
        "_show_startup_screen",
        lambda _p, _ft, *, publish, compact_desktop: events.append(
            f"show_startup_screen:publish={publish},compact={compact_desktop}"
        ),
    )

    assert application.main() == 0
    assert "page.update:prevent_close=True,visible=False" in events
    idx_update = events.index("page.update:prevent_close=True,visible=False")
    idx_ready = events.index("window.wait_until_ready_to_show")
    idx_center = events.index("window.center")
    idx_release = events.index("release_hide_guard")
    idx_splash = events.index("show_startup_screen:publish=True,compact=True")
    idx_app_main = events.index("app_main")
    assert idx_update < idx_ready < idx_center < idx_release < idx_splash < idx_app_main


def test_embedded_windows_early_splash_close_event_aborts_before_app_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeBroker:
        def close(self, *, timeout_seconds: float = 1.0) -> bool:
            events.append(f"broker.close:{timeout_seconds}")
            return True

    class FakeWindow:
        def __init__(self) -> None:
            self.prevent_close = False
            self.visible = True
            self.on_event = None

        async def wait_until_ready_to_show(self) -> None:
            events.append("window.wait_until_ready_to_show")

        async def center(self) -> None:
            events.append("window.center")

        async def destroy(self) -> None:
            events.append("original_window.destroy")

    class FakePage:
        def __init__(self) -> None:
            self.window = FakeWindow()
            self.controls = []

        def add(self, *_controls) -> None:
            pass

        def update(self) -> None:
            pass

    page = FakePage()

    def _show_splash_and_schedule_close(_p, _ft, *, publish, compact_desktop) -> None:
        events.append(f"splash:{publish}:{compact_desktop}")

        async def _fire_close() -> None:
            assert callable(page.window.on_event)
            await page.window.on_event(SimpleNamespace(type="close", data="close"))

        asyncio.get_running_loop().create_task(_fire_close())

    def fake_run(**kwargs) -> None:
        coro = kwargs["main"](page)
        if inspect.isawaitable(coro):
            asyncio.run(coro)

    fake_flet = SimpleNamespace(
        Page=FakePage,
        WindowEventType=SimpleNamespace(CLOSE="close"),
        AppView=SimpleNamespace(
            WEB_BROWSER="web-browser",
            FLET_APP_HIDDEN="flet-app-hidden",
        ),
        run=fake_run,
    )
    monkeypatch.setitem(sys.modules, "flet", fake_flet)
    monkeypatch.setitem(
        sys.modules,
        "gui.compact_desktop",
        SimpleNamespace(main=lambda *_a, **_kw: events.append("app_main_should_not_run")),
    )
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.setattr(application, "_is_source_checkout", lambda _p: False)
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False)
    monkeypatch.setattr(
        application,
        "bootstrap_windows_instance",
        lambda **_kw: SimpleNamespace(exit_code=None, broker=FakeBroker(), force_visible=False),
    )
    monkeypatch.setattr(application, "create_default_runtime", lambda *_a, **_kw: None)
    monkeypatch.setattr(application, "_is_embedded_windows_runner", lambda **_kw: True)
    monkeypatch.setattr(application, "_release_early_flutter_window_hide_guard", lambda: None)
    monkeypatch.setattr(application, "_hide_current_process_flutter_windows", lambda: events.append("hide"))
    monkeypatch.setattr(application, "_show_startup_screen", _show_splash_and_schedule_close)
    monkeypatch.setattr(
        application,
        "_terminate_embedded_windows_process",
        lambda code: events.append(f"hard_exit:{code}"),
    )

    assert application.main() == 0
    assert "broker.close:1.0" in events
    assert "hard_exit:0" in events
    assert "app_main_should_not_run" not in events


@pytest.mark.parametrize(
    ("development", "channel", "expected_release_channel"),
    [
        (True, DistributionChannel.SOURCE, "stable"),
        (False, DistributionChannel.EXE_LTS, "stable"),
        (False, DistributionChannel.MSSTORE, "msstore"),
    ],
)
def test_windows_single_instance_derives_msstore_channel_when_packaged_msstore(
    monkeypatch: pytest.MonkeyPatch,
    development: bool,
    channel: DistributionChannel,
    expected_release_channel: str,
) -> None:
    captured_kwargs: list[dict[str, object]] = []
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.setattr(application, "_is_source_checkout", lambda _p: development)
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False)
    monkeypatch.setattr(
        "core.distribution.resolve_distribution_channel",
        lambda: build_distribution_profile(channel),
    )
    monkeypatch.setattr(
        application,
        "bootstrap_windows_instance",
        lambda **kw: (
            captured_kwargs.append(kw)
            or SimpleNamespace(exit_code=0, broker=None, force_visible=False)
        ),
    )

    assert application.main() == 0
    assert captured_kwargs == [
        {
            "autostart_launch": False,
            "release_channel": expected_release_channel,
            "development": development,
        }
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Requires Windows PowerShell")
def test_single_instance_e2e_ps1_filters_flutter_class_zero_bounds_and_waits_for_exit() -> None:
    script = SINGLE_INSTANCE_E2E_PS1.read_text(encoding="utf-8")
    assert "FLUTTER_RUNNER_WIN32_WINDOW" in script
    assert "GetClassNameW" in script
    assert "GetWindowRect" in script
    assert "[Parameter(Mandatory = $true, ParameterSetName = \"BundleDir\")][string]$BundleDir" in script
    # Must call parameterless WaitForExit() after timed WaitForExit(...) before reading ExitCode
    assert "$Process.WaitForExit()" in script or "$process.WaitForExit()" in script

    # Verify PowerShell 5.1 AST + embedded C# compilation succeeds cleanly
    ps_check = (
        f"$p = '{SINGLE_INSTANCE_E2E_PS1}'; "
        "$tokens = $null; $errors = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$tokens, [ref]$errors); "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_.ToString() }; exit 1 }"
    )
    proc = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", ps_check],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=20,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


# ---------------------------------------------------------------------------
# 3. MOB-PIPELINE-01, MOB-PIPELINE-02 & MOB-PRIVACY-01
# ---------------------------------------------------------------------------


def test_msstore_workflow_wires_display_name_and_publisher_display_name_with_safe_defaults() -> None:
    workflow = MSSTORE_WORKFLOW.read_text(encoding="utf-8")
    assert "display_name:" in workflow
    assert "publisher_display_name:" in workflow
    assert "vars.MSSTORE_DISPLAY_NAME" in workflow
    assert "vars.MSSTORE_PUBLISHER_DISPLAY_NAME" in workflow
    assert workflow.count("DisplayName") >= 4
    assert workflow.count("PublisherDisplayName") >= 4


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX packaging requires Windows makeappx.exe")
def test_verify_msix_release_enforces_display_name_and_publisher_display_name(
    tmp_path: Path,
) -> None:
    kits_bin = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    if not kits_bin.is_dir() or not list(kits_bin.glob("**/x64/makeappx.exe")):
        pytest.skip("makeappx.exe not available on host")

    stage_root = ROOT / "build" / f"test-msix-names-{uuid.uuid4().hex[:8]}"
    bundle_dir = stage_root / "bundle"
    out_dir = stage_root / "out"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "UTHelper.exe").write_bytes(b"MZ" + b"\x00" * 126)
    (bundle_dir / "UTHelperAutostart.exe").write_bytes(b"MZ" + b"\x00" * 126)
    (bundle_dir / "app" / "assets").mkdir(parents=True, exist_ok=True)

    try:
        msix_path = out_dir / "UTHelper-custom-names.msix"
        pack_proc = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(PACKAGE_MSIX_PS1),
                "-BundleDir",
                str(bundle_dir),
                "-IdentityName",
                "12345Chouwzi.UTHelper",
                "-Publisher",
                "CN=12345678-90AB-CDEF-1234-567890ABCDEF",
                "-DisplayName",
                "UTHelper Study Companion",
                "-PublisherDisplayName",
                "Chouwzi Studio",
                "-Channel",
                "msstore",
                "-SignMode",
                "none",
                "-Output",
                str(msix_path),
                "-PythonExe",
                sys.executable,
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        assert pack_proc.returncode == 0, pack_proc.stderr or pack_proc.stdout

        ok_proc = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(VERIFY_MSIX_PS1),
                "-MsixPath",
                str(msix_path),
                "-Channel",
                "msstore",
                "-IdentityName",
                "12345Chouwzi.UTHelper",
                "-Publisher",
                "CN=12345678-90AB-CDEF-1234-567890ABCDEF",
                "-DisplayName",
                "UTHelper Study Companion",
                "-PublisherDisplayName",
                "Chouwzi Studio",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        assert ok_proc.returncode == 0, ok_proc.stderr or ok_proc.stdout

        bad_display = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(VERIFY_MSIX_PS1),
                "-MsixPath",
                str(msix_path),
                "-DisplayName",
                "Wrong Display Name",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        assert bad_display.returncode != 0
        assert "DisplayName" in (bad_display.stdout + bad_display.stderr)

        bad_pub_display = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(VERIFY_MSIX_PS1),
                "-MsixPath",
                str(msix_path),
                "-PublisherDisplayName",
                "Wrong Publisher Name",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        assert bad_pub_display.returncode != 0
        assert "PublisherDisplayName" in (bad_pub_display.stdout + bad_pub_display.stderr)
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def test_build_android_ps1_uses_sanitized_python_flet_cli_instead_of_uv_trampoline() -> None:
    script = BUILD_ANDROID_PS1.read_text(encoding="utf-8")
    assert "$fletCommand.Source build" not in script
    assert "flet_cli.cli" in script
    for env_key in ("PYTHONHOME", "UV_INTERNAL__PYTHONHOME", "PYTHONPATH"):
        assert env_key in script
    assert '"--yes"' in script or "'--yes'" in script

    if sys.platform == "win32":
        ps_check = (
            f"$p = '{BUILD_ANDROID_PS1}'; "
            "$tokens = $null; $errors = $null; "
            "[void][System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$tokens, [ref]$errors); "
            "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_.ToString() }; exit 1 }"
        )
        proc = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", ps_check],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=20,
            stdin=subprocess.DEVNULL,
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout


def test_privacy_and_store_submission_docs_cover_portal_sentry_opt_out_and_msstore_demo() -> None:
    privacy = (ROOT / "docs" / "PRIVACY.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs" / "guides" / "store-distribution-submission.md").read_text(
        encoding="utf-8"
    )

    assert "portal.ut.edu.vn" in privacy
    assert "portal.ut.edu.vn" in guide

    # Play Data Safety Sentry row must state Required (opt-out) rather than Optional
    sentry_rows = [
        line
        for line in guide.splitlines()
        if "Sanitized crash and performance diagnostics" in line
    ]
    assert len(sentry_rows) == 1
    assert "Required (opt-out)" in sentry_rows[0]
    assert "| Optional; users can opt out |" not in sentry_rows[0]

    # Microsoft Partner Center Notes for certification section must include Reviewer Demo credentials
    msstore_section = guide.split("## 3. Microsoft Store", 1)[1].split(
        "## 4. GitHub Releases", 1
    )[0]
    notes_section = msstore_section.split("Notes for", 1)[1]
    assert "uthelper_demo" in notes_section
    assert "UTH-Review-2026!" in notes_section
