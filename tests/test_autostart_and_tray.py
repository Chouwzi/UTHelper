import sys
from pathlib import Path
import threading
import time
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


class _BlockingTrayIconSpy:
    def __init__(self):
        self.stop_calls = 0
        self.stop_entered = threading.Event()
        self.release_stop = threading.Event()

    def stop(self):
        self.stop_calls += 1
        self.stop_entered.set()
        assert self.release_stop.wait(1.0)


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

    assert events[0] == "stop"
    assert events[1][0] == "join"
    assert 0.0 <= events[1][1] <= 1.0


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

    assert events[0:2] == ["stop", ("join", 0.0)]
    assert events[2][0] == "join"
    assert 0.0 <= events[2][1] <= 0.2


def test_tray_close_propagates_stop_failure_without_a_live_tray_thread():
    from gui.tray import TrayApp

    events: list[object] = []
    tray = TrayApp()
    tray._icon = _FailingTrayIconSpy(events)

    assert tray.close(timeout_seconds=0.1) is False
    assert tray.close(timeout_seconds=0.1) is False
    assert events == ["stop"]


def test_blocking_icon_stop_is_once_only_and_every_close_stays_bounded():
    from gui.tray import TrayApp

    icon = _BlockingTrayIconSpy()
    tray = TrayApp()
    tray._icon = icon
    first_result: list[bool] = []
    first_elapsed: list[float] = []
    first_finished = threading.Event()

    def close_once():
        started_at = time.monotonic()
        first_result.append(tray.close(timeout_seconds=0.02))
        first_elapsed.append(time.monotonic() - started_at)
        first_finished.set()

    caller = threading.Thread(target=close_once, daemon=True)
    caller.start()
    assert icon.stop_entered.wait(0.5)
    try:
        assert first_finished.wait(0.15)
        assert first_result == [False]
        assert first_elapsed[0] < 0.15
        assert tray._stop_thread.name == "tray-icon-stop"
        assert tray._stop_thread.daemon is True

        started_at = time.monotonic()
        assert tray.close(timeout_seconds=0.02) is False
        assert time.monotonic() - started_at < 0.15
        assert icon.stop_calls == 1
    finally:
        icon.release_stop.set()
        caller.join(0.5)

    assert not caller.is_alive()
    assert tray.close(timeout_seconds=0.1) is True
    assert icon.stop_calls == 1


def test_tray_close_reports_stop_helper_start_failure_without_retry(monkeypatch):
    import gui.tray as tray_module

    events: list[object] = []
    helper_constructions: list[str] = []

    class FailingStartThread:
        daemon = True

        def __init__(self, *args, **kwargs):
            helper_constructions.append(kwargs["name"])

        def start(self):
            raise RuntimeError("thread unavailable")

        def is_alive(self):
            return False

    monkeypatch.setattr(tray_module.threading, "Thread", FailingStartThread)
    tray = tray_module.TrayApp()
    tray._icon = _TrayIconSpy(events)

    assert tray.close(timeout_seconds=0.02) is False
    assert tray.close(timeout_seconds=0.02) is False
    assert helper_constructions == ["tray-icon-stop"]
    assert events == []


def test_close_winning_setup_race_prevents_tray_publish_and_thread_start(monkeypatch):
    import gui.tray as tray_module

    dependency_load_entered = threading.Event()
    release_dependency_load = threading.Event()
    icon_constructions: list[str] = []
    icon_runs: list[str] = []

    class Image:
        @staticmethod
        def open(path):
            raise OSError("use fallback")

        @staticmethod
        def new(*args, **kwargs):
            return object()

    class Icon:
        def __init__(self, *args, **kwargs):
            icon_constructions.append("constructed")

        def run(self, *, setup):
            icon_runs.append("run")

    Pystray = type(
        "Pystray",
        (),
        {"Menu": staticmethod(lambda *items: object()), "Icon": Icon},
    )

    def load_dependencies():
        dependency_load_entered.set()
        assert release_dependency_load.wait(0.5)
        return Pystray, lambda *args, **kwargs: object(), Image

    monkeypatch.setattr(tray_module, "_load_tray_dependencies", load_dependencies)
    tray = tray_module.TrayApp()
    setup_result: list[bool] = []
    setup_finished = threading.Event()

    def run_setup():
        setup_result.append(tray.setup(ready_timeout_seconds=0.01))
        setup_finished.set()

    setup_thread = threading.Thread(target=run_setup, daemon=True)
    setup_thread.start()
    assert dependency_load_entered.wait(0.5)
    assert tray.close(timeout_seconds=0.02) is True
    release_dependency_load.set()
    assert setup_finished.wait(0.5)
    setup_thread.join(0.5)

    assert setup_result == [False]
    assert icon_constructions == []
    assert icon_runs == []
    assert tray._icon is None
    assert tray._thread is None

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
