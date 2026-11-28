import asyncio
import inspect
import logging
import os
import sys
import threading
import time
from pathlib import Path


_EARLY_HIDE_GUARD_LOCK = threading.Lock()
_EARLY_HIDE_GUARD_STOP = threading.Event()
_EARLY_HIDE_GUARD_THREAD: threading.Thread | None = None


def _is_source_checkout(module_path: Path) -> bool:
    try:
        return (module_path.resolve().parents[1] / "pyproject.toml").is_file()
    except IndexError:
        return False


def _detect_embedded_windows_runner_early() -> bool:
    if sys.platform != "win32" or os.environ.get("UTH_TEST_MODE") == "1":
        return False
    if not (
        os.environ.get("FLET_DART_BRIDGE_EXIT_PORT")
        or os.environ.get("FLET_DART_BRIDGE_PORT")
    ):
        return False
    return not _is_source_checkout(Path(__file__))


def _start_early_flutter_window_hide_guard() -> None:
    """Suppress premature 1280x720 runner visibility until prevent_close latches."""
    global _EARLY_HIDE_GUARD_THREAD
    if sys.platform != "win32":
        return
    with _EARLY_HIDE_GUARD_LOCK:
        if _EARLY_HIDE_GUARD_THREAD is not None and _EARLY_HIDE_GUARD_THREAD.is_alive():
            return
        _EARLY_HIDE_GUARD_STOP.clear()

        def _worker() -> None:
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.FindWindowExW.argtypes = [
                    wintypes.HWND,
                    wintypes.HWND,
                    wintypes.LPCWSTR,
                    wintypes.LPCWSTR,
                ]
                user32.FindWindowExW.restype = wintypes.HWND
                user32.GetWindowThreadProcessId.argtypes = [
                    wintypes.HWND,
                    ctypes.POINTER(wintypes.DWORD),
                ]
                user32.GetWindowThreadProcessId.restype = wintypes.DWORD
                user32.IsWindowVisible.argtypes = [wintypes.HWND]
                user32.IsWindowVisible.restype = wintypes.BOOL
                user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
                user32.ShowWindow.restype = wintypes.BOOL
                user32.IsWindow.argtypes = [wintypes.HWND]
                user32.IsWindow.restype = wintypes.BOOL

                current_pid = os.getpid()
                seen_hwnd = 0
                deadline = time.monotonic() + 20.0

                while not _EARLY_HIDE_GUARD_STOP.is_set() and time.monotonic() < deadline:
                    child_after = 0
                    visited: set[int] = set()
                    while not _EARLY_HIDE_GUARD_STOP.is_set():
                        hwnd = user32.FindWindowExW(
                            None,
                            child_after,
                            "FLUTTER_RUNNER_WIN32_WINDOW",
                            None,
                        )
                        hwnd_int = int(hwnd or 0)
                        if not hwnd_int or hwnd_int in visited:
                            break
                        visited.add(hwnd_int)
                        child_after = hwnd_int
                        owner_pid = wintypes.DWORD(0)
                        user32.GetWindowThreadProcessId(
                            hwnd_int, ctypes.byref(owner_pid)
                        )
                        if owner_pid.value == current_pid:
                            seen_hwnd = hwnd_int
                            if user32.IsWindowVisible(hwnd_int):
                                user32.ShowWindow(hwnd_int, 0)

                    if (
                        seen_hwnd != 0
                        and not _EARLY_HIDE_GUARD_STOP.is_set()
                        and not user32.IsWindow(seen_hwnd)
                    ):
                        _terminate_embedded_windows_process(0)
                        return

                    _EARLY_HIDE_GUARD_STOP.wait(0.001)
            except Exception:
                pass

        thread = threading.Thread(
            target=_worker,
            name="uth-early-window-hide-guard",
            daemon=True,
        )
        _EARLY_HIDE_GUARD_THREAD = thread
        thread.start()


def _release_early_flutter_window_hide_guard() -> None:
    """Stop and join the early runner hide-guard before publishing the splash UI."""
    global _EARLY_HIDE_GUARD_THREAD
    _EARLY_HIDE_GUARD_STOP.set()
    with _EARLY_HIDE_GUARD_LOCK:
        thread = _EARLY_HIDE_GUARD_THREAD
        _EARLY_HIDE_GUARD_THREAD = None
    if (
        thread is not None
        and thread.is_alive()
        and thread is not threading.current_thread()
    ):
        thread.join(timeout=0.5)


if _detect_embedded_windows_runner_early():
    _start_early_flutter_window_hide_guard()

from analytics.runtime import create_default_analytics_runtime  # noqa: E402
from diagnostics.logging_setup import (  # noqa: E402
    configure_dependency_logging,
    configure_logging,
    startup_debug_enabled,
)
from diagnostics.models import AppPhase  # noqa: E402
from diagnostics.release_config import load_runtime_public_config  # noqa: E402
from diagnostics.runtime import (  # noqa: E402
    _resolve_runtime_package_format,
    create_default_runtime,
)
from gui.controllers.startup_visibility import is_autostart_launch  # noqa: E402
from platform_utils import quiz_client_bootstrap  # noqa: E402
from platform_utils.single_instance import bootstrap_windows_instance  # noqa: E402

# Force stdout/stderr to be unbuffered/line-buffered
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

# iOS/mobile SSL fix: must be set BEFORE any httpx/ssl import
# Python in sandboxed iOS/Android apps cannot locate system CA certificates.
# Setting these env vars ensures SSL verification works everywhere.
try:
    import certifi
    os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
except ImportError:
    pass  # certifi not available on desktop (uses system certs)

from core.version import APP_VERSION

__version__ = APP_VERSION

# Crash-safe startup logging
# This MUST run before ANY project imports to capture import crashes.
_BOOT_LOG = []

def _boot_log(msg):
    """Log during boot phase before logging is configured."""
    _BOOT_LOG.append(msg)
    try:
        print(f"[BOOT] {msg}", file=sys.stderr, flush=True)
    except (OSError, ValueError, AttributeError):
        pass  # stderr unavailable (e.g. during circular import or packaged app)

_boot_log(f"Python {sys.version}")
_boot_log(f"Platform: {sys.platform}")
_boot_log(
    f"Android: {hasattr(sys, '_ANDROID_') or 'android' in sys.platform.lower() or 'ANDROID_BOOTLOGO' in os.environ}"
)

# Platform-aware data directories
def _resolve_appdata_dir(
    *,
    platform_name: str | None = None,
    environ=None,
    home_dir: Path | None = None,
) -> Path:
    resolved_platform = sys.platform if platform_name is None else platform_name
    resolved_home = Path.home() if home_dir is None else home_dir
    if environ is None:
        if resolved_platform == "win32":
            return Path(os.getenv("APPDATA") or resolved_home) / "UTHelper"
        storage_data = (os.environ.get("FLET_APP_STORAGE_DATA") or "").strip()
        if storage_data:
            return Path(storage_data)
        app_data = (os.environ.get("FLET_APP_DATA") or "").strip()
        if app_data:
            return Path(app_data)
        return resolved_home / ".uthelper"

    if resolved_platform == "win32":
        return Path(environ.get("APPDATA") or resolved_home) / "UTHelper"
    storage_data = (environ.get("FLET_APP_STORAGE_DATA") or "").strip()
    if storage_data:
        return Path(storage_data)
    app_data = (environ.get("FLET_APP_DATA") or "").strip()
    if app_data:
        return Path(app_data)
    return resolved_home / ".uthelper"


try:
    _APPDATA_DIR = _resolve_appdata_dir()

    _FLET_DATA_DIR = _APPDATA_DIR / "flet" / "data"
    _FLET_TEMP_DIR = _APPDATA_DIR / "flet" / "temp"
    _FLET_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _FLET_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("FLET_APP_STORAGE_DATA", str(_FLET_DATA_DIR))
    os.environ.setdefault("FLET_APP_STORAGE_TEMP", str(_FLET_TEMP_DIR))
    _boot_log(f"Data dir: {_APPDATA_DIR}")
except Exception as exc:
    _boot_log(f"Data dir init failed: {exc}")
    _APPDATA_DIR = Path.home()

# Set up the sole owned file logger after the platform data directory exists.
# Pytest imports this module while collecting startup tests; it must never bind
# the production AppData log and mix synthetic failures with real user events.
_TEST_MODE = os.environ.get("UTH_TEST_MODE") == "1"
if _TEST_MODE:
    _LOGGING_RUNTIME = None
else:
    try:
        _LOGGING_RUNTIME = configure_logging(
            _APPDATA_DIR,
            debug=startup_debug_enabled(os.environ),
        )
        configure_dependency_logging()
        _boot_log(f"Log file: {_APPDATA_DIR / 'logs' / 'app.log'}")
    except Exception as exc:
        _LOGGING_RUNTIME = None
        _boot_log(f"Log setup failed: {exc}")

# Console encoding fix (Windows-only, harmless on other platforms)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        import logging as _fb_log
        _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

if _LOGGING_RUNTIME is None and not _TEST_MODE:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
configure_dependency_logging()
# Suppress known harmless Flet web session teardown errors
# (NoneType.put_nowait race condition during disconnect)
# logging.getLogger("flet_core.session").setLevel(logging.CRITICAL)

logger = logging.getLogger(__name__)

# Log boot messages to file now that logging is set up
for msg in _BOOT_LOG:
    logger.info("[BOOT] %s", msg)


def _show_crash_screen(page, ft, reference: str | None):
    """Show a reference-only failure screen without exception-owned text."""
    safe_reference = reference or "không khả dụng"
    page.bgcolor = "#0F172A"
    page.padding = 20
    page.scroll = ft.ScrollMode.AUTO
    page.controls.clear()
    page.controls.append(
        ft.Column([
            ft.Icon(ft.Icons.ERROR_OUTLINE, color="#EF4444", size=48),
            ft.Text("UTHelper - Khởi động lỗi", size=20, color="#F8FAFC",
                     weight=ft.FontWeight.BOLD),
            ft.Text("Ứng dụng gặp lỗi khi khởi động.",
                     size=14, color="#94A3B8"),
            ft.Container(
                content=ft.Text(
                    f"Mã tham chiếu: {safe_reference}",
                    size=11,
                    color="#FCA5A5",
                    selectable=True,
                    font_family="monospace",
                ),
                bgcolor="#1E293B",
                border_radius=8,
                padding=12,
            ),
            ft.Text("Vui lòng gửi mã tham chiếu cho nhà phát triển.",
                     size=12, color="#64748B"),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=12,
        )
    )
    page.update()


def _is_web_mode(argv, environ) -> bool:
    forced_web = str(environ.get("FLET_FORCE_WEB_SERVER", "")).casefold()
    return (
        environ.get("FLET_WEB") == "1"
        or "--web" in argv
        or forced_web in {"1", "true", "yes"}
    )


def _is_embedded_windows_runner(
    *,
    development: bool | None = None,
    environ=None,
) -> bool:
    """Return True when running inside the packaged Flet Windows dart_bridge runner."""
    if sys.platform != "win32" or _TEST_MODE:
        return False
    is_dev = (
        _is_source_checkout(Path(__file__))
        if development is None
        else bool(development)
    )
    if is_dev:
        return False
    env = os.environ if environ is None else environ
    return bool(
        env.get("FLET_DART_BRIDGE_EXIT_PORT") or env.get("FLET_DART_BRIDGE_PORT")
    )


def _resolve_windows_release_channel(*, development: bool) -> str:
    """Isolate Microsoft Store single-instance mutex names from sideload/LTS builds."""
    if development:
        return "stable"
    try:
        from core.distribution import DistributionChannel, resolve_distribution_channel

        profile = resolve_distribution_channel()
        channel = getattr(profile, "channel", profile)
        if channel == DistributionChannel.MSSTORE:
            return "msstore"
    except Exception:
        pass
    return "stable"


def _hide_current_process_flutter_windows() -> None:
    """Hide top-level FLUTTER_RUNNER_WIN32_WINDOW HWNDs owned by this process."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        enum_proc_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        user32.EnumWindows.argtypes = [enum_proc_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL

        current_pid = os.getpid()
        sw_hide = 0

        def _callback(hwnd: int, _lparam: int) -> bool:
            owner_pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
            if owner_pid.value == current_pid:
                class_buf = ctypes.create_unicode_buffer(128)
                if user32.GetClassNameW(hwnd, class_buf, 128) > 0:
                    if class_buf.value == "FLUTTER_RUNNER_WIN32_WINDOW":
                        user32.ShowWindow(hwnd, sw_hide)
            return True

        cb = enum_proc_type(_callback)
        user32.EnumWindows(cb, 0)
    except Exception:
        pass


def _terminate_embedded_windows_process(exit_code: int) -> None:
    """Terminate packaged Windows process cleanly without C++ static destructor crashes.

    In packaged ``UTHelper.exe``, ``flutter_inappwebview_windows_plugin.dll`` owns an
    ``inline static winrt::com_ptr<IDispatcherQueueController>`` bound to Win32 Thread 0.
    Calling ``ucrtbase!exit()`` or ``ExitProcess()`` runs ``DLL_PROCESS_DETACH`` static
    destructors either from the background Python worker thread or after Thread 0's
    ``CoUninitialize()``, faulting with ``0xC0000005`` inside ``coremessaging.dll``.
    Matching upstream ``dart_bridge``'s ``serious_python_hard_exit``, flush all Python
    streams/logs first and terminate the kernel process directly with ``exit_code``.
    """
    _EARLY_HIDE_GUARD_STOP.set()
    _hide_current_process_flutter_windows()
    try:
        logging.shutdown()
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            try:
                stream.flush()
            except Exception:
                pass
    normalized_code = int(exit_code) & 0xFFFFFFFF
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            # Explicit 64-bit HANDLE typing is mandatory: default restype c_int
            # truncates GetCurrentProcess() (-1 -> 0x00000000FFFFFFFF on x64),
            # causing TerminateProcess to fail with ERROR_INVALID_HANDLE (6).
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.argtypes = []
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.TerminateProcess.restype = wintypes.BOOL
            kernel32.TerminateProcess(
                kernel32.GetCurrentProcess(),
                normalized_code,
            )
        except Exception:
            pass
    os._exit(int(exit_code))


def _exit_main_process(exit_code: int) -> None:
    """Exit with exact code: ``TerminateProcess`` in embedded runner, ``SystemExit`` on CLI."""
    if _is_embedded_windows_runner():
        _terminate_embedded_windows_process(exit_code)
        return
    raise SystemExit(exit_code)


def _show_startup_screen(
    page,
    ft,
    *,
    publish: bool,
    compact_desktop: bool = True,
) -> None:
    """Publish the first frame only after it has final geometry and branding."""
    from gui.core.theme import C, set_page_theme

    if compact_desktop:
        # PC: cửa sổ co giãn được (bố cục 2 cột khi đủ rộng).
        page.window.width = 1120
        page.window.height = 760
        page.window.min_width = 420
        page.window.min_height = 560
        page.window.max_width = None
        page.window.max_height = None
        page.window.resizable = True
        page.window.maximizable = True
        page.window.minimizable = True
        page.window.title_bar_hidden = True
        page.window.title_bar_buttons_hidden = True
        page.window.icon = "icon.ico"
    page.title = "UTHelper"
    set_page_theme(page)
    page.bgcolor = C.BG
    page.padding = 0
    page.spacing = 0
    page.controls.clear()
    splash = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Image(
                        src="icon.png",
                        width=92,
                        height=92,
                        fit=ft.BoxFit.CONTAIN,
                    ),
                    ft.Text(
                        "UTHelper",
                        size=24,
                        weight=ft.FontWeight.BOLD,
                        color=C.TEXT_PRIMARY,
                    ),
                    ft.Text(
                        "Trợ lý học tập UTH",
                        size=12,
                        color=C.TEXT_SECONDARY,
                    ),
                    ft.Container(height=18),
                    ft.ProgressRing(
                        width=28,
                        height=28,
                        stroke_width=3,
                        color=C.ACCENT,
                    ),
                    ft.Text(
                        "Đang khởi động UTHelper...",
                        size=12,
                        color=C.TEXT_SECONDARY,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=8,
                expand=True,
            ),
            alignment=ft.Alignment(0, 0),
            expand=True,
            bgcolor=C.BG,
        )
    if compact_desktop:
        from gui.components.desktop_window_header import (
            build_desktop_window_header,
        )

        def minimize_window(_event):
            page.window.minimized = True
            page.update()

        def toggle_maximize(_event):
            page.window.maximized = not bool(getattr(page.window, "maximized", False))
            page.update()

        async def close_window(_event):
            await page.window.destroy()

        page.add(
            ft.Column(
                controls=[
                    build_desktop_window_header(
                        on_minimize=minimize_window,
                        on_maximize=toggle_maximize,
                        on_close=close_window,
                    ),
                    splash,
                ],
                spacing=0,
                expand=True,
            )
        )
    else:
        page.add(splash)
    if compact_desktop:
        page.window.visible = publish
    page.update()


def _normalize_packaged_windows_cwd(
    *,
    executable: Path | None = None,
    development: bool,
    test_mode: bool,
    chdir_fn=os.chdir,
) -> bool:
    """Ensure packaged Windows launches never run with System32 as cwd."""
    if development or test_mode:
        return False
    try:
        from pathlib import PureWindowsPath
        from platform_utils.autostart import get_current_process_executable

        exe = Path(executable or get_current_process_executable())
    except Exception:
        return False
    if PureWindowsPath(str(exe)).stem.casefold() in {"python", "pythonw"}:
        return False
    install_dir = exe.parent
    if not install_dir.is_dir():
        return False
    try:
        chdir_fn(install_dir)
        return True
    except OSError:
        return False


def main() -> int:
    web_mode = _is_web_mode(sys.argv, os.environ)
    result = None
    development = _is_source_checkout(Path(__file__))
    desktop_windows = sys.platform == "win32" and not web_mode
    if desktop_windows:
        _normalize_packaged_windows_cwd(
            development=development,
            test_mode=_TEST_MODE,
        )
    autostart_launch = desktop_windows and is_autostart_launch()
    if desktop_windows:
        result = bootstrap_windows_instance(
            autostart_launch=autostart_launch,
            release_channel=_resolve_windows_release_channel(development=development),
            development=development,
        )
        if result.exit_code is not None:
            return result.exit_code

    if desktop_windows and development and not _TEST_MODE:
        project_root = Path(__file__).resolve().parents[1]
        added_quiz_source: Path | None = None
        try:
            # Flet's Windows resolver searches ``cwd/build/windows`` for the
            # custom client. A direct ``python path\to\main.py`` launch may
            # start outside the checkout; Flet CLI child processes keep their
            # storage working directory and are already launched from the app
            # project by the CLI.
            if os.environ.get("FLET_DISPLAY_URL_PREFIX"):
                if Path.cwd().resolve() != project_root.resolve():
                    raise quiz_client_bootstrap.QuizClientBootstrapError(
                        "flet run was started outside the project root; "
                        "the custom Windows quiz client is unavailable."
                    )
            else:
                os.chdir(project_root)
            # Source checkouts are not necessarily installed with ``pip -e``.
            # Make their Python control available, then ensure Flet's desktop
            # resolver has a client compiled with the matching Dart extension.
            added_quiz_source = quiz_client_bootstrap.add_source_quiz_package_to_path(
                project_root
            )
            quiz_client_bootstrap.ensure_windows_quiz_client(project_root)
            quiz_client_bootstrap.set_source_quiz_client_ready(True)
        except Exception as exc:
            quiz_client_bootstrap.set_source_quiz_client_ready(False)
            quiz_client_bootstrap.remove_source_quiz_package_from_path(
                added_quiz_source
            )
            _boot_log(f"Windows quiz client bootstrap failed: {exc}")
            logger.error("Windows quiz client bootstrap failed: %s", exc)

    runtime = None
    try:
        runtime = create_default_runtime(
            _APPDATA_DIR,
            development=development,
        )
        runtime.start()
    except Exception:
        _boot_log("Diagnostic runtime unavailable")

    analytics_runtime = None
    try:
        public_config = load_runtime_public_config(development=development)
        import config as _config_mod
        from core.distribution import resolve_distribution_channel

        dist_channel = (
            "source"
            if development
            else resolve_distribution_channel(is_packaged=True).channel.value
        )
        pkg_format = _resolve_runtime_package_format(
            dist_channel,
            public_config.package_format,
            development=development,
            is_windows=desktop_windows,
        )
        analytics_runtime = create_default_analytics_runtime(
            _APPDATA_DIR,
            development=development,
            api_key=public_config.posthog_project_api_key,
            host=public_config.posthog_host,
            app_version=APP_VERSION,
            release_channel=(
                "development"
                if development
                else (public_config.build_environment or "release")
            ),
            distribution_channel=dist_channel,
            package_format=pkg_format,
            web_mode=web_mode,
            test_mode=_TEST_MODE,
            consent=getattr(_config_mod.settings, "ANALYTICS_CONSENT", "enabled"),
            consent_provider=lambda: getattr(
                _config_mod.settings, "ANALYTICS_CONSENT", "enabled"
            ),
            subscribe_consent_changes=_config_mod.subscribe_settings_saved,
        )
        analytics_runtime.start()
    except Exception:
        _boot_log("Analytics runtime unavailable")

    # Desktop ownership must be decided before loading the heavyweight Flet
    # runtime. A secondary process can then hand off and exit without starting
    # a second Flutter/Python UI stack.
    import flet as ft

    active_page = None
    active_controller = None
    embedded_windows_runner = desktop_windows and _is_embedded_windows_runner(
        development=development
    )
    broker_closed = False
    analytics_closed = False
    runtime_closed = False
    destroy_started = False

    def _close_broker_and_runtime(*, clean: bool) -> None:
        nonlocal broker_closed, analytics_closed, runtime_closed
        try:
            if not broker_closed and result and result.broker:
                broker_closed = True
                result.broker.close(timeout_seconds=1.0)
        finally:
            try:
                if not analytics_closed and analytics_runtime is not None:
                    analytics_closed = True
                    analytics_runtime.close()
            except Exception:
                pass
            if not runtime_closed and runtime is not None:
                runtime_closed = True
                runtime.mark_phase(AppPhase.SHUTDOWN)
                if clean:
                    runtime.close(clean=True)
                else:
                    runtime.close(clean=False)

    async def _app_target(page: ft.Page):
        nonlocal active_page, active_controller, destroy_started
        active_page = page
        if runtime is not None:
            runtime.attach_page(page)
            runtime.attach_asyncio_loop(asyncio.get_running_loop())
            runtime.mark_phase(AppPhase.GUI)

        if embedded_windows_runner:
            window_obj = getattr(page, "window", None)
            if window_obj is not None:
                async def _safe_embedded_destroy() -> None:
                    nonlocal destroy_started
                    if destroy_started:
                        return
                    destroy_started = True
                    _hide_current_process_flutter_windows()
                    if active_controller is not None:
                        prepare_fn = getattr(
                            active_controller, "_prepare_async_shutdown", None
                        )
                        if callable(prepare_fn):
                            try:
                                prepare_fn()
                            except Exception:
                                pass
                        shutdown_fn = getattr(
                            active_controller, "_shutdown_for_exit", None
                        )
                        if callable(shutdown_fn):
                            try:
                                await asyncio.wait_for(
                                    asyncio.to_thread(shutdown_fn),
                                    timeout=2.5,
                                )
                            except Exception:
                                pass
                    try:
                        _close_broker_and_runtime(clean=True)
                    except Exception:
                        pass
                    _terminate_embedded_windows_process(0)

                async def _early_splash_window_event(event) -> None:
                    event_type = getattr(event, "type", getattr(event, "data", ""))
                    close_type = getattr(
                        getattr(ft, "WindowEventType", None), "CLOSE", "close"
                    )
                    if event_type == close_type or getattr(event, "data", "") == "close":
                        await _safe_embedded_destroy()

                window_obj.destroy = _safe_embedded_destroy
                window_obj.prevent_close = True
                window_obj.visible = False
                window_obj.width = 1120
                window_obj.height = 760
                window_obj.on_event = _early_splash_window_event
                page.update()
                wait_ready = getattr(window_obj, "wait_until_ready_to_show", None)
                if callable(wait_ready):
                    try:
                        ready_res = wait_ready()
                        if inspect.isawaitable(ready_res):
                            await asyncio.wait_for(ready_res, timeout=3.0)
                    except Exception:
                        pass
                center_fn = getattr(window_obj, "center", None)
                if callable(center_fn):
                    try:
                        center_res = center_fn()
                        if inspect.isawaitable(center_res):
                            await asyncio.wait_for(center_res, timeout=3.0)
                    except Exception:
                        pass
            _release_early_flutter_window_hide_guard()
            if destroy_started:
                return

        try:
            # Load the persisted launch preference before deciding whether the
            # compact startup window should be published. New installations
            # open normally; only an explicit start-minimized preference keeps
            # an autostart launch hidden.
            from config import settings as app_settings

            publish_startup = (
                not autostart_launch
                or not app_settings.START_MINIMIZED
                or bool(result and result.force_visible)
            )
            _show_startup_screen(
                page,
                ft,
                publish=publish_startup,
                compact_desktop=desktop_windows,
            )
            if publish_startup:
                # Yield to the Flet transport so Flutter paints and animates the
                # branded first frame before synchronous GUI construction.
                # One short yield is enough to publish a branded frame; the
                # former 600 ms floor made every launch feel slow, especially
                # with embedded Python on older Android devices.
                await asyncio.sleep(0.05)
            if destroy_started:
                return
            logger.info("Starting app imports...")
            
            # Flet compatibility shim (MUST run before any GUI imports)
            from gui.flet_compat import patch_flet
            patch_flet()
            logger.info("Flet compat patched OK")
            
            # Config is loaded above so startup visibility uses persisted state.
            if not _TEST_MODE:
                configure_logging(
                    _APPDATA_DIR,
                    debug=(
                        bool(app_settings.DEBUG_MODE)
                        or startup_debug_enabled(os.environ)
                    ),
                )
            configure_dependency_logging()
            logger.info("Config loaded OK")
            
            # Import GUI 
            from gui.compact_desktop import main as app_main
            logger.info("GUI module imported OK")
            
            # Run the app
            app_options = {
                "activation_broker": result.broker if result else None,
                "force_visible": result.force_visible if result else False,
                "error_reporter": (
                    (lambda exc: runtime.record_exception(exc, AppPhase.GUI))
                    if runtime is not None
                    else None
                ),
            }
            app_main_params = inspect.signature(app_main).parameters
            if "diagnostic_recorder" in app_main_params:
                app_options["diagnostic_recorder"] = runtime
            if "analytics_runtime" in app_main_params:
                app_options["analytics_runtime"] = analytics_runtime
            active_controller = app_main(page, **app_options)
            logger.info("App started successfully")
            
        except Exception as exc:
            reference = None
            if runtime is not None:
                reference = runtime.record_exception(exc, AppPhase.GUI)
            logger.critical("App startup failed; reference=%s", reference or "none")
            try:
                _show_crash_screen(page, ft, reference)
            except Exception:
                logger.critical("Reference-only crash screen failed")
    
    # On Android, Flet bundles assets automatically and sets FLET_ASSETS_DIR
    _assets = os.environ.get("FLET_ASSETS_DIR") or os.path.abspath(
        os.path.join(os.path.dirname(__file__), "assets")
    )
    
    # Support web mode for testing: set FLET_WEB=1 or pass --web
    try:
        web_port = int(os.environ.get("FLET_WEB_PORT", "8561"))
        if not (1 <= web_port <= 65535):
            raise ValueError(f"Port {web_port} out of valid range 1-65535")
    except (ValueError, TypeError):
        web_port = 8561
    
    run_kwargs = dict(main=_app_target, assets_dir=_assets)
    if web_mode:
        run_kwargs["view"] = ft.AppView.WEB_BROWSER
        run_kwargs["port"] = web_port
    elif desktop_windows:
        # Keep Flutter's default 1280x720 host hidden until the first UTHelper
        # frame has its final compact geometry, logo, and progress indicator.
        run_kwargs["view"] = ft.AppView.FLET_APP_HIDDEN
    elif os.environ.get("FLET_SERVER_PORT"):
        # Flet >= 0.82 workaround removed to test if it's causing the issue in 0.85.3
        pass
    
    clean_exit = False
    exit_code = 0
    try:
        run_kwargs["main"] = _app_target
        run_kwargs.pop("target", None)
        ft.run(**run_kwargs)
        clean_exit = True
    except Exception as exc:
        reference = None
        if runtime is not None:
            reference = runtime.record_exception(exc, AppPhase.GUI)
        logger.critical("Flet runner failed; reference=%s", reference or "none")
        if active_page is not None:
            try:
                _show_crash_screen(active_page, ft, reference)
            except Exception:
                logger.critical("Reference-only crash screen failed")
        clean_exit = True
        exit_code = 1
    finally:
        _release_early_flutter_window_hide_guard()
        _close_broker_and_runtime(clean=clean_exit)
    return exit_code


if __name__ == "__main__":
    # multiprocessing.freeze_support() chỉ cần cho Windows PyInstaller builds
    if sys.platform == 'win32':
        import multiprocessing
        multiprocessing.freeze_support()
    _exit_main_process(main())
