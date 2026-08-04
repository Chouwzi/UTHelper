"""System tray icon with balloon notification support using pystray."""
import logging
import math
import os
import time
from collections.abc import Callable
from config import BASE_DIR
import threading

logger = logging.getLogger(__name__)
MAX_TRAY_SETUP_TIMEOUT_SECONDS = 3.0
MAX_TRAY_CLOSE_TIMEOUT_SECONDS = 1.0


def _load_tray_dependencies():
    import pystray
    from PIL import Image
    from pystray import MenuItem

    return pystray, MenuItem, Image

def _resolve_tray_icon_path() -> str:
    app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    candidates = [
        os.path.join(BASE_DIR, "src", "assets", "icon.ico"),
        os.path.join(BASE_DIR, "assets", "icon.ico"),
        os.path.join(BASE_DIR, "src", "assets", "icon.png"),
        os.path.join(BASE_DIR, "assets", "icon.png"),
        os.path.join(app_dir, "assets", "icon.ico"),
        os.path.join(app_dir, "assets", "icon.png"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[-1]

class TrayApp:
    """Minimal system-tray wrapper that exposes a .notify() method and context menu."""

    def __init__(self, page=None, *, on_show: Callable[[], None] | None = None):
        self._icon = None
        self._page = page
        self._on_show = on_show
        self._thread = None
        self._ready_event = threading.Event()
        self._setup_done = threading.Event()
        self._setup_error = None
        self._lifecycle_lock = threading.Lock()
        self._close_requested = False
        self._stop_thread = None
        self._stop_helper_failed = False

    def setup(self, ready_timeout_seconds: float = 3.0) -> bool:
        """Start the tray and report readiness within a finite deadline."""
        ready_timeout = _bounded_timeout_seconds(
            ready_timeout_seconds, maximum=MAX_TRAY_SETUP_TIMEOUT_SECONDS
        )
        with self._lifecycle_lock:
            if self._close_requested:
                return False
            if self._ready_event.is_set():
                return True
        try:
            pystray, item, Image = _load_tray_dependencies()

            # Dependency loading may yield long enough for close() to win.
            # Recheck before doing any candidate construction, then recheck
            # once more at the atomic publish/start point below.
            with self._lifecycle_lock:
                if self._close_requested:
                    return False
                needs_icon = self._icon is None

            if needs_icon:
                # Thử tìm icon.ico trước (tốt nhất cho Windows Tray), sau đó mới tới icon.png
                icon_path = _resolve_tray_icon_path()

                try:
                    img = Image.open(icon_path).convert("RGBA")
                except Exception as e:
                    logger.warning("Tray icon load failed (%s), using default icon: %r", icon_path, e)
                    img = Image.new("RGBA", (64, 64), color=(59, 130, 246, 255))

                menu = pystray.Menu(
                    item('Mở UTHelper', self.show_app, default=True),
                    item('Thoát', self.exit_app)
                )

                candidate_icon = pystray.Icon(
                    "uth_alert", img, title="UTHelper", menu=menu
                )

                def mark_ready(icon):
                    try:
                        icon.visible = True
                        self._ready_event.set()
                    finally:
                        self._setup_done.set()

                def run_icon():
                    try:
                        candidate_icon.run(setup=mark_ready)
                    except Exception as exc:
                        self._setup_error = exc
                        logger.warning("Tray icon event loop failed: %s", exc)
                    finally:
                        self._ready_event.clear()
                        self._setup_done.set()

                candidate_thread = threading.Thread(target=run_icon, daemon=True)
                with self._lifecycle_lock:
                    if self._close_requested:
                        return False
                    if self._icon is None:
                        self._setup_done.clear()
                        self._setup_error = None
                        self._icon = candidate_icon
                        self._thread = candidate_thread
                        # Publish and start are one lifecycle decision: close
                        # cannot win between them and miss the owned thread.
                        candidate_thread.start()
        except Exception as exc:
            logger.warning("Tray icon setup failed: %s", exc)
            self._setup_error = exc
            self._setup_done.set()
            return False

        self._setup_done.wait(timeout=ready_timeout)
        ready = self._ready_event.is_set()
        if not ready:
            logger.warning(
                "Tray icon was not ready within %.1f seconds%s",
                ready_timeout,
                f": {self._setup_error}" if self._setup_error else "",
            )
        return ready

    def show_app(self, icon, item):
        if self._on_show is not None:
            try:
                self._on_show()
            except Exception as e:
                logger.error(f"Lỗi khi phục hồi app: {e}")

    def exit_app(self, icon, item):
        if self._page:
            try:
                self._page.run_task(self._page.window.destroy)
            except Exception as e:
                logger.error(f"Error destroying window: {e}")
        self.close()

    def close(self, timeout_seconds: float = 1.0) -> bool:
        """Stop tray resources once within one shared monotonic deadline."""
        timeout = _bounded_timeout_seconds(
            timeout_seconds, maximum=MAX_TRAY_CLOSE_TIMEOUT_SECONDS
        )
        deadline = time.monotonic() + timeout
        with self._lifecycle_lock:
            first_request = not self._close_requested
            self._close_requested = True
            icon = self._icon
            tray_thread = self._thread
            if first_request and icon is not None:
                stop_thread = threading.Thread(
                    target=self._stop_icon,
                    args=(icon,),
                    name="tray-icon-stop",
                    daemon=True,
                )
                self._stop_thread = stop_thread
                try:
                    stop_thread.start()
                except Exception:
                    self._stop_helper_failed = True
                    logger.warning("Tray stop helper start failed", exc_info=True)
            stop_thread = self._stop_thread
            stop_helper_failed = self._stop_helper_failed
            self._setup_done.set()

        stop_finished = _join_owned_daemon_before_deadline(stop_thread, deadline)
        tray_finished = _join_owned_daemon_before_deadline(tray_thread, deadline)
        return not stop_helper_failed and stop_finished and tray_finished

    @staticmethod
    def _stop_icon(icon) -> None:
        try:
            icon.stop()
        except Exception:
            logger.warning("Tray icon stop failed", exc_info=True)

    def notify(self, title: str, message: str):
        """Send a Windows balloon notification via pystray, with fallback."""   
        with self._lifecycle_lock:
            if self._close_requested:
                return
        if not self._icon:
            self.setup()
        
        try:
            if self._icon:
                self._icon.notify(message, title)
        except Exception as exc:
            logger.warning("Tray notification failed, falling back to log: %s", exc)
            logger.info("NOTIFICATION: %s - %s", title, message)


def _bounded_timeout_seconds(timeout_seconds: float, *, maximum: float) -> float:
    """Normalize tray waits to a finite caller-specific maximum."""
    if not math.isfinite(timeout_seconds):
        return 0.0 if timeout_seconds < 0 else maximum
    return min(max(timeout_seconds, 0.0), maximum)


def _join_owned_daemon_before_deadline(thread, deadline: float) -> bool:
    """Join only an owned daemon and never exceed the shared close deadline."""
    if thread is None:
        return True
    if thread is threading.current_thread() or not thread.daemon:
        return not thread.is_alive()
    if not thread.is_alive():
        return True
    try:
        thread.join(max(deadline - time.monotonic(), 0.0))
    except RuntimeError:
        logger.warning("Tray thread join failed", exc_info=True)
    return not thread.is_alive()
