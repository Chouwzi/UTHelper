import logging
import os
import sys
import traceback
from pathlib import Path

from diagnostics.logging_setup import configure_logging
from gui.controllers.startup_visibility import is_autostart_launch
from platform_utils.single_instance import bootstrap_windows_instance

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
_boot_log(f"Android: {hasattr(sys, '_ANDROID_')}")

# Platform-aware data directories
try:
    if sys.platform == 'win32':
        _APPDATA_DIR = Path(os.getenv("APPDATA", Path.home())) / "UTHelper"
    elif os.environ.get('FLET_APP_STORAGE_DATA'):
        _APPDATA_DIR = Path(os.environ['FLET_APP_STORAGE_DATA'])
    else:
        _APPDATA_DIR = Path.home() / ".uthelper"

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
try:
    _LOGGING_RUNTIME = configure_logging(_APPDATA_DIR, debug=True)
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

if _LOGGING_RUNTIME is None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
logging.getLogger("flet_core").setLevel(logging.DEBUG)
logging.getLogger("flet").setLevel(logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.WARNING)
# Suppress known harmless Flet web session teardown errors
# (NoneType.put_nowait race condition during disconnect)
# logging.getLogger("flet_core.session").setLevel(logging.CRITICAL)

logger = logging.getLogger(__name__)

# Log boot messages to file now that logging is set up
for msg in _BOOT_LOG:
    logger.info("[BOOT] %s", msg)


def _show_crash_screen(page, error_msg: str):
    """Show error details on screen instead of black screen."""
    import flet as ft
    page.bgcolor = "#0F172A"
    page.padding = 20
    page.scroll = ft.ScrollMode.AUTO
    page.controls.clear()
    page.controls.append(
        ft.Column([
            ft.Icon(ft.Icons.ERROR_OUTLINE, color="#EF4444", size=48),
            ft.Text("UTHelper - Khởi động lỗi", size=20, color="#F8FAFC",
                     weight=ft.FontWeight.BOLD),
            ft.Text("Ứng dụng gặp lỗi khi khởi động. Chi tiết:", 
                     size=14, color="#94A3B8"),
            ft.Container(
                content=ft.Text(
                    error_msg,
                    size=11,
                    color="#FCA5A5",
                    selectable=True,
                    font_family="monospace",
                ),
                bgcolor="#1E293B",
                border_radius=8,
                padding=12,
            ),
            ft.Text("Vui lòng chụp màn hình và gửi cho nhà phát triển.", 
                     size=12, color="#64748B"),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=12,
        )
    )
    page.update()


def _is_web_mode(argv, environ) -> bool:
    return environ.get("FLET_WEB") == "1" or "--web" in argv


def _is_source_checkout(module_path: Path) -> bool:
    try:
        return (module_path.resolve().parents[1] / "pyproject.toml").is_file()
    except IndexError:
        return False


def main() -> int:
    web_mode = _is_web_mode(sys.argv, os.environ)
    result = None
    if sys.platform == "win32" and not web_mode:
        result = bootstrap_windows_instance(
            autostart_launch=is_autostart_launch(),
            release_channel="stable",
            development=_is_source_checkout(Path(__file__)),
        )
        if result.exit_code is not None:
            return result.exit_code

    # Desktop ownership must be decided before loading the heavyweight Flet
    # runtime. A secondary process can then hand off and exit without starting
    # a second Flutter/Python UI stack.
    import flet as ft

    def _app_target(page: ft.Page):
        try:
            logger.info("Starting app imports...")
            
            # Flet compatibility shim (MUST run before any GUI imports)
            from gui.flet_compat import patch_flet
            patch_flet()
            logger.info("Flet compat patched OK")
            
            # Import config first (may fail on Android if keyring is missing)
            logger.info("Config loaded OK")
            
            # Import GUI 
            from gui.compact_desktop import main as app_main
            logger.info("GUI module imported OK")
            
            # Run the app
            app_main(
                page,
                activation_broker=result.broker if result else None,
                force_visible=result.force_visible if result else False,
            )
            logger.info("App started successfully")
            
        except Exception:
            error_msg = traceback.format_exc()
            logger.critical("App crashed during startup:\n%s", error_msg)
            try:
                _show_crash_screen(page, error_msg)
            except Exception as render_exc:
                logger.critical("Even crash screen failed: %s", render_exc)
    
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
    elif os.environ.get("FLET_SERVER_PORT"):
        # Flet >= 0.82 workaround removed to test if it's causing the issue in 0.85.3
        pass
    
    try:
        run_kwargs["main"] = _app_target
        run_kwargs.pop("target", None)
        ft.run(**run_kwargs)
        return 0
    finally:
        if result and result.broker:
            result.broker.close(timeout_seconds=1.0)


if __name__ == "__main__":
    # multiprocessing.freeze_support() chỉ cần cho Windows PyInstaller builds
    if sys.platform == 'win32':
        import multiprocessing
        multiprocessing.freeze_support()
    raise SystemExit(main())
