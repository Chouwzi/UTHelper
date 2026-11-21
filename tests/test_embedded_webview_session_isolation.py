"""Cookie refresh failures must not display a previous account's browser session."""

from types import SimpleNamespace
from unittest.mock import Mock

from gui.components.courses.embedded_webview_host import WindowsWebView2Dock


def _controller(monkeypatch, provider):
    dock = WindowsWebView2Dock(
        page_title="UTHelper", url="https://courses.ut.edu.vn/course/view.php?id=7",
        cookie_provider=provider,
    )
    dock._hwnd = 1
    dock._visible = False
    core, controller = Mock(), Mock()
    controller.core_webview2 = core
    operation = SimpleNamespace(get_results=lambda: controller)
    monkeypatch.setattr(dock, "_inject_cookies", Mock())
    monkeypatch.setattr(dock, "_bind_events", Mock())
    monkeypatch.setattr(dock, "_apply_window_bounds", Mock())
    return dock, core, operation


def test_failed_cookie_refresh_does_not_navigate_old_browser_session(monkeypatch):
    dock, core, operation = _controller(monkeypatch, Mock(side_effect=OSError("auth unavailable")))
    dock._handle_ctrl_done(operation)
    assert dock._failed is True
    assert dock._ready is False
    core.navigate.assert_not_called()


def test_successful_cookie_refresh_preserves_in_app_navigation(monkeypatch):
    cookie = {"name": "MoodleSession", "value": "synthetic", "domain": "courses.ut.edu.vn"}
    dock, core, operation = _controller(monkeypatch, lambda: [cookie])
    dock._handle_ctrl_done(operation)
    assert dock._ready is True
    assert dock._cookies == [cookie]
    dock._inject_cookies.assert_called_once_with(core)
    core.navigate.assert_called_once_with(dock._url)


def test_each_pane_uses_a_separate_private_browser_profile():
    profiles = []
    for _ in range(2):
        environment = Mock()
        options = SimpleNamespace(profile_name="", is_in_private_mode_enabled=False)
        environment.create_core_webview2_controller_options.return_value = options
        dock = WindowsWebView2Dock(page_title="UTHelper")
        window_ref = object()
        dock._create_private_controller(environment, window_ref)
        environment.create_core_webview2_controller_async.assert_called_once_with(window_ref, options)
        assert options.is_in_private_mode_enabled is True
        profiles.append(options.profile_name)
    assert profiles[0] and profiles[1] and profiles[0] != profiles[1]


def test_disposal_during_cookie_refresh_cannot_navigate_after_account_switch(monkeypatch):
    dock, core, operation = _controller(monkeypatch, None)

    def close_during_refresh():
        dock._stop_event.set()
        return []

    dock._cookie_provider = close_during_refresh
    dock._handle_ctrl_done(operation)
    assert dock._ready is False
    dock._inject_cookies.assert_not_called()
    core.navigate.assert_not_called()
