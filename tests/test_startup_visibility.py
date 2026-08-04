from pathlib import Path

import pytest

from gui.controllers.startup_visibility import (
    is_autostart_launch,
    should_hide_startup_window,
)


@pytest.mark.parametrize(
    (
        "executable",
        "argv",
        "start_minimized",
        "tray_ready",
        "is_mobile",
        "expected",
    ),
    [
        ("UTHelper.exe", ["UTHelper.exe"], True, True, False, False),
        ("UTHelperAutostart.exe", ["UTHelperAutostart.exe"], False, True, False, False),
        ("UTHelperAutostart.exe", ["UTHelperAutostart.exe"], True, True, False, True),
        ("UTHelperAutostart.exe", ["UTHelperAutostart.exe"], True, False, False, False),
        ("UTHelperAutostart.exe", ["UTHelperAutostart.exe"], True, True, True, False),
        ("python.exe", ["main.py", "--autostart"], True, True, False, True),
    ],
)
def test_startup_visibility_matrix(
    executable, argv, start_minimized, tray_ready, is_mobile, expected
):
    launch = is_autostart_launch(argv=argv, executable=Path(executable))

    assert (
        should_hide_startup_window(
            autostart_launch=launch,
            start_minimized=start_minimized,
            tray_ready=tray_ready,
            is_mobile=is_mobile,
        )
        is expected
    )


def test_flag_matching_is_exact():
    assert not is_autostart_launch(
        argv=["main.py", "--autostart-debug"], executable=Path("python.exe")
    )


def test_alias_matching_is_case_insensitive():
    assert is_autostart_launch(
        argv=["anything"], executable=Path("uthelperAUTOSTART.EXE")
    )


def test_tray_setup_failure_is_explicit(monkeypatch):
    import gui.tray as tray

    monkeypatch.setattr(tray, "_load_tray_dependencies", lambda: (_ for _ in ()).throw(ImportError("missing")))

    assert tray.TrayApp().setup(ready_timeout_seconds=0.01) is False


def test_compact_desktop_forwards_activation_dependencies(monkeypatch):
    import gui.compact_desktop as compact_desktop

    broker = object()
    page = object()
    created: list[tuple[object, object, bool]] = []
    controller = object()

    def create_controller(page, *, activation_broker, force_visible):
        created.append((page, activation_broker, force_visible))
        return controller

    monkeypatch.setattr(compact_desktop, "AppController", create_controller)

    assert compact_desktop.main(
        page, activation_broker=broker, force_visible=True
    ) is controller
    assert created == [(page, broker, True)]


def test_force_visible_startup_skips_minimize_policy_and_binds_activation(monkeypatch):
    import gui.app_controller as app_controller

    class Window:
        def __init__(self):
            self.visible = False
            self.width = None
            self.height = None
            self.max_width = None
            self.min_width = None
            self.always_on_top = None
            self.resizable = None
            self.icon = None
            self.prevent_close = None
            self.on_event = None

    class Page:
        def __init__(self):
            self.window = Window()
            self.title = ""
            self.bgcolor = ""
            self.padding = None
            self.spacing = None
            self.theme_mode = None
            self.update_calls = 0
            self.scheduled: list[object] = []

        def update(self):
            self.update_calls += 1

        def run_task(self, task, *args):
            self.scheduled.append(task)

    class Broker:
        def __init__(self):
            self.handler = None

        def bind_show_handler(self, handler):
            self.handler = handler

    class Tray:
        def __init__(self, page, *, on_show):
            self.page = page
            self.on_show = on_show

        def setup(self):
            return True

    class Notifier:
        def __init__(self, tray=None):
            self.tray = tray

        async def initialize(self, page):
            return None

    page = Page()
    broker = Broker()
    controller = app_controller.AppController.__new__(app_controller.AppController)
    controller.page = page
    controller.activation_broker = broker
    controller.force_visible = True
    controller.orchestrator = type("Orchestrator", (), {})()
    controller._android_background = None
    controller._safe_run_task = lambda task, *args: None
    controller._on_window_event = lambda event: None

    monkeypatch.setattr(app_controller, "detect_platform", lambda page: None)
    monkeypatch.setattr(app_controller.platform_utils, "IS_MOBILE", False)
    monkeypatch.setattr(app_controller.platform_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(app_controller.platform_utils, "IS_ANDROID", False)
    monkeypatch.setattr(app_controller.platform_utils, "IS_IOS", False)
    monkeypatch.setattr(app_controller, "NotificationManager", Notifier)
    monkeypatch.setattr(app_controller.settings, "START_MINIMIZED", True)
    monkeypatch.setattr(app_controller, "is_autostart_launch", lambda: True)
    monkeypatch.setattr(app_controller, "should_hide_startup_window", lambda **kwargs: pytest.fail("force-visible launch must not use minimize policy"))
    monkeypatch.setattr("gui.tray.TrayApp", Tray)
    monkeypatch.setattr("gui.core.theme.set_page_theme", lambda page: None)

    controller._init_window()

    assert page.window.visible is True
    assert page.scheduled == [controller.window_activator.show]
    assert controller.tray.on_show == controller.window_activator.request_show
    assert broker.handler == controller.window_activator.request_show


def test_disconnect_closes_activation_broker_before_page_resources():
    import gui.app_controller as app_controller

    events: list[str] = []

    class Broker:
        def close(self, *, timeout_seconds):
            events.append(f"broker:{timeout_seconds}")

    class Tray:
        def close(self, *, timeout_seconds):
            events.append(f"tray:{timeout_seconds}")

    class Closer:
        def __init__(self, name):
            self.name = name

        def clear(self):
            events.append(self.name)

        def set(self):
            events.append(self.name)

        def close(self):
            events.append(self.name)

    controller = app_controller.AppController.__new__(app_controller.AppController)
    controller.activation_broker = Broker()
    controller.tray = Tray()
    controller.view_manager = type(
        "ViewManager",
        (),
        {
            "cancel_pending_settings_navigation": lambda self: events.append(
                "settings-navigation"
            )
        },
    )()
    controller._page_alive = Closer("page-alive")
    controller._prefetch_cancel_event = Closer("prefetch")
    controller._sync_coordinator = Closer("coordinator")
    controller.orchestrator = type(
        "Orchestrator", (), {"client": Closer("client")}
    )()

    controller._on_disconnect(None)

    assert events == [
        "settings-navigation",
        "broker:1.0",
        "tray:1.0",
        "page-alive",
        "prefetch",
        "coordinator",
        "client",
    ]
