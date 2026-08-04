import sys
from pathlib import Path
import threading
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


class _TrayIconSpy:
    def __init__(self, events):
        self.events = events

    def stop(self):
        self.events.append("stop")


class _OwnedDaemonThreadSpy:
    daemon = True

    def __init__(self, events, *, alive=True):
        self.events = events
        self.alive = alive

    def join(self, timeout):
        self.events.append(("join", timeout))
        self.alive = False

    def is_alive(self):
        return self.alive


class _StillAliveDaemonThreadSpy(_OwnedDaemonThreadSpy):
    def join(self, timeout):
        self.events.append(("join", timeout))


class _UnownedNonDaemonThreadSpy(_OwnedDaemonThreadSpy):
    daemon = False

    def join(self, timeout):
        raise AssertionError("an unowned non-daemon thread must not be joined")


class _FailingTrayIconSpy(_TrayIconSpy):
    def stop(self):
        self.events.append("stop")
        raise RuntimeError("native stop failed")


class _WaitSpy:
    def __init__(self):
        self.timeouts: list[float] = []

    def wait(self, timeout):
        self.timeouts.append(timeout)
        return False


def test_tray_setup_wait_clamps_non_finite_timeout(monkeypatch):
    import gui.tray as tray_module

    tray = tray_module.TrayApp()
    tray._icon = object()
    wait = _WaitSpy()
    tray._setup_done = wait
    monkeypatch.setattr(
        tray_module, "_load_tray_dependencies", lambda: (object(), object(), object())
    )

    assert tray.setup(ready_timeout_seconds=float("inf")) is False
    assert wait.timeouts == [3.0]


def test_tray_close_stops_once_then_joins_owned_daemon_with_finite_bound():
    from gui.tray import TrayApp

    events: list[object] = []
    tray = TrayApp()
    tray._icon = _TrayIconSpy(events)
    tray._thread = _OwnedDaemonThreadSpy(events)

    assert tray.close(timeout_seconds=float("inf")) is True
    assert tray.close(timeout_seconds=0.25) is True

    assert events == ["stop", ("join", 1.0)]


def test_tray_close_does_not_join_unowned_or_current_thread():
    from gui.tray import TrayApp

    events: list[object] = []
    tray = TrayApp()
    tray._icon = _TrayIconSpy(events)
    tray._thread = threading.current_thread()

    assert tray.close(timeout_seconds=0.1) is False
    assert events == ["stop"]

    other_tray = TrayApp()
    other_tray._icon = _TrayIconSpy(events)
    other_tray._thread = _UnownedNonDaemonThreadSpy(events)

    assert other_tray.close(timeout_seconds=0.1) is False
    assert events == ["stop", "stop"]


def test_tray_close_contains_stop_failure_and_reports_join_timeout():
    from gui.tray import TrayApp

    events: list[object] = []
    tray = TrayApp()
    tray._icon = _FailingTrayIconSpy(events)
    tray._thread = _StillAliveDaemonThreadSpy(events)

    assert tray.close(timeout_seconds=-1.0) is False
    assert tray.close(timeout_seconds=0.2) is False

    assert events == ["stop", ("join", 0.0), ("join", 0.2)]

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
