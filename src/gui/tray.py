"""System tray icon with balloon notification support using pystray."""
import logging
import os
from collections.abc import Callable
from config import BASE_DIR
import threading

logger = logging.getLogger(__name__)


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

    def setup(self, ready_timeout_seconds: float = 3.0) -> bool:
        """Start the tray and report readiness within a finite deadline."""
        if self._ready_event.is_set():
            return True
        try:
            pystray, item, Image = _load_tray_dependencies()
            
            if self._icon is None:
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

                self._icon = pystray.Icon("uth_alert", img, title="UTHelper", menu=menu)
                self._setup_done.clear()
                self._setup_error = None

                def mark_ready(icon):
                    try:
                        icon.visible = True
                        self._ready_event.set()
                    finally:
                        self._setup_done.set()

                def run_icon():
                    try:
                        self._icon.run(setup=mark_ready)
                    except Exception as exc:
                        self._setup_error = exc
                        logger.warning("Tray icon event loop failed: %s", exc)
                    finally:
                        self._setup_done.set()

                self._thread = threading.Thread(target=run_icon, daemon=True)
                self._thread.start()
        except Exception as exc:
            logger.warning("Tray icon setup failed: %s", exc)
            self._setup_error = exc
            self._setup_done.set()
            return False

        self._setup_done.wait(timeout=max(0.0, ready_timeout_seconds))
        ready = self._ready_event.is_set()
        if not ready:
            logger.warning(
                "Tray icon was not ready within %.1f seconds%s",
                ready_timeout_seconds,
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
        if self._icon:
            self._icon.stop()

    def notify(self, title: str, message: str):
        """Send a Windows balloon notification via pystray, with fallback."""   
        if not self._icon:
            self.setup()
        
        try:
            if self._icon:
                self._icon.notify(message, title)
        except Exception as exc:
            logger.warning("Tray notification failed, falling back to log: %s", exc)
            logger.info("NOTIFICATION: %s - %s", title, message)
