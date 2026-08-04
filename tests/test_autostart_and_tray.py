import sys
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock

# Ensure src is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from platform_utils.autostart import add_to_startup, remove_from_startup


class _NoUiAccessPage:
    def __getattribute__(self, name):
        if name in {"window", "run_task", "update"}:
            raise AssertionError("tray Open must delegate to the supplied callback")
        return super().__getattribute__(name)


def test_tray_open_delegates_to_show_callback_without_touching_page():
    from gui.tray import TrayApp

    calls: list[str] = []
    tray = TrayApp(_NoUiAccessPage(), on_show=lambda: calls.append("show"))

    tray.show_app(None, None)

    assert calls == ["show"]


def test_tray_open_is_a_safe_noop_without_a_show_callback():
    from gui.tray import TrayApp

    TrayApp(_NoUiAccessPage()).show_app(None, None)

class TestAutostart(unittest.TestCase):
    def test_autostart_windows(self):
        # We only run actual registry test if on Windows
        if sys.platform != "win32":
            self.skipTest("Autostart tests are Windows-only")
            
        import winreg
        
        test_app_name = "UTHelper_Test_Autostart"
        
        # 1. Add to startup
        result = add_to_startup(test_app_name)
        self.assertTrue(result)
        
        # Verify registry key exists
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ
        )
        try:
            value, regtype = winreg.QueryValueEx(key, test_app_name)
            self.assertTrue("--autostart" in value)
        finally:
            winreg.CloseKey(key)
            
        # 2. Remove from startup
        result = remove_from_startup(test_app_name)
        self.assertTrue(result)
        
        # Verify registry key is gone
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ
        )
        with self.assertRaises(FileNotFoundError):
            winreg.QueryValueEx(key, test_app_name)
        winreg.CloseKey(key)

class TestTrayMinimize(unittest.IsolatedAsyncioTestCase):
    async def test_minimize_to_tray_on_close(self):
        from gui.app_controller import AppController
        from core.data_orchestrator import DataOrchestrator
        from config import settings
        import flet as ft
        
        # Mock Flet Page
        page = MagicMock()
        
        # Ensure MINIMIZE_TO_TRAY is True
        settings.MINIMIZE_TO_TRAY = True
        
        # Initialize controller
        controller = AppController(page)
        controller.tray = MagicMock()
        
        # Create a mock close event
        # In Flet 0.22+, event can be data="close" or type=WindowEventType.CLOSE
        class MockEvent:
            def __init__(self):
                self.data = "close"
                self.type = "close"
                
        event = MockEvent()
        
        # Trigger window event
        await controller._on_window_event(event)
        
        # Verify page window is hidden
        self.assertFalse(page.window.visible)
        page.update.assert_called()
        
        # Verify tray balloon was shown if it's the first time
        controller.tray._icon.notify.assert_called_once()

if __name__ == "__main__":
    unittest.main()
