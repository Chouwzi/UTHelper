import sys, os, re
sys.path.append(os.path.abspath('src'))

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\tray.py'
new_tray = '''"""System tray icon with balloon notification support using pystray."""
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

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
                # icon mặc định
                img = Image.new("RGB", (64, 64), color=(59, 130, 246))
                
                menu = pystray.Menu(
                    item('Mở UTH Alert', self.show_app, default=True),
                    item('Thoát', self.exit_app)
                )

                self._icon = pystray.Icon("uth_alert", img, title="UTH Elearning Alert", menu=menu)
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
        if self._icon:
            self._icon.stop()
        if self._page:
            self._page.window.destroy()
        else:
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
'''

with open(path, 'w', encoding='utf-8') as f: f.write(new_tray)
print("done tray")
