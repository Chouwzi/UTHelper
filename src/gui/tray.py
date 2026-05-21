"""System tray icon with balloon notification support using pystray."""
import logging
import os
from config import BASE_DIR
import threading

logger = logging.getLogger(__name__)

def _resolve_tray_icon_path() -> str:
    candidates = [
        os.path.join(BASE_DIR, "src", "assets", "icon.ico"),
        os.path.join(BASE_DIR, "assets", "icon.ico"),
        os.path.join(BASE_DIR, "src", "assets", "icon.png"),
        os.path.join(BASE_DIR, "assets", "icon.png"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[-1]

class TrayApp:
    """Minimal system-tray wrapper that exposes a .notify() method and context menu."""

    def __init__(self, page=None):
        self._icon = None
        self._page = page

    def setup(self):
        try:
            import pystray
            from pystray import MenuItem as item
            from PIL import Image
            
            if self._icon is None:
                # Thử tìm icon.ico trước (tốt nhất cho Windows Tray), sau đó mới tới icon.png
                icon_path = _resolve_tray_icon_path()
                
                try:
                    img = Image.open(icon_path).convert("RGBA")
                except Exception as e:
                    logger.warning("Không nạp được icon (%s), dùng icon mặc định: %s", icon_path, e)
                    img = Image.new("RGBA", (64, 64), color=(59, 130, 246, 255))
                
                menu = pystray.Menu(
                    item('Mở UTHelper', self.show_app, default=True),
                    item('Thoát', self.exit_app)
                )

                self._icon = pystray.Icon("uth_alert", img, title="UTHelper", menu=menu)
                threading.Thread(target=self._icon.run, daemon=True).start()
        except Exception as exc:
            logger.warning("Tray icon setup failed: %s", exc)

    def show_app(self, icon, item):
        if self._page:
            try:
                # Phải gán qua thread an toàn hoặc update page
                self._page.window.visible = True
                self._page.window.minimized = False
                self._page.update()
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
        import os
        os._exit(0)

    def notify(self, title: str, message: str):
        """Send a Windows balloon notification via pystray, with fallback."""   
        if not self._icon:
            self.setup()
        
        try:
            if self._icon:
                self._icon.notify(message, title)
        except Exception as exc:
            logger.warning("Tray notification failed, falling back to log: %s", exc)
            logger.info("NOTIFICATION: %s — %s", title, message)
