"""Cookie refresh failures must not display a previous account's browser session."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

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


@pytest.mark.parametrize("url", ["file:///C:/private.txt", "javascript:alert(1)", "data:text/html,private"])
def test_page_popup_cannot_navigate_to_a_local_or_executable_scheme(url):
    dock = WindowsWebView2Dock(page_title="UTHelper")
    core = Mock()
    dock._bind_events(core)
    popup = core.add_new_window_requested.call_args.args[0]
    event = SimpleNamespace(uri=url, handled=False)
    popup(core, event)
    assert event.handled is True
    core.navigate.assert_not_called()


def test_http_popup_stays_in_the_current_app_webview():
    dock = WindowsWebView2Dock(page_title="UTHelper")
    core = Mock()
    dock._bind_events(core)
    popup = core.add_new_window_requested.call_args.args[0]
    event = SimpleNamespace(uri="https://courses.ut.edu.vn/mod/page/view.php?id=7", handled=False)
    popup(core, event)
    assert event.handled is True
    core.navigate.assert_called_once_with(event.uri)


def test_webview_security_guards_schemes_viewer_popups_devtools_cookies_and_bounds(monkeypatch):
    from gui.components.courses import webview_pane as webview_pane_mod
    from gui.components.courses.embedded_webview_host import (
        _validate_webview_url,
        build_embedded_webview_host,
    )
    from gui.components.courses.webview_pane import CoursesWebViewPane
    from gui.view_models.courses_state import safe_http_url

    assert safe_http_url("http://evil.example/a") == ""
    assert safe_http_url("https://127.0.0.1/steal") == ""
    assert safe_http_url("https://courses.ut.edu.vn/mod/page/view.php?id=1")
    assert _validate_webview_url("http://127.0.0.1:45678/viewer/tok/docx_host.html", allowed_loopback_ports={45678})
    assert _validate_webview_url("http://127.0.0.1:99999/other", allowed_loopback_ports={45678}) == ""
    assert _validate_webview_url("http://127.0.0.1:45678/other", allowed_loopback_ports=set()) == ""
    assert build_embedded_webview_host("http://evil.example/plain") is None

    # Disallowed navigation schemes are cancelled; viewer popups are blocked; dev tools & host objects disabled
    dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="http://127.0.0.1:45678/viewer/tok/docx_host.html",
    )
    core = Mock()
    dock._bind_events(core)
    nav_start = core.add_navigation_starting.call_args.args[0]
    for bad_uri in ("file:///C:/secret.txt", "javascript:alert(1)", "data:text/html,x", "intent://foo", "https://evil.example/"):
        nav_ev = SimpleNamespace(uri=bad_uri, cancel=False)
        nav_start(core, nav_ev)
        assert nav_ev.cancel is True

    popup = core.add_new_window_requested.call_args.args[0]
    popup_ev = SimpleNamespace(uri="https://courses.ut.edu.vn/mod/page/view.php?id=7", handled=False)
    popup(core, popup_ev)
    assert popup_ev.handled is True
    core.navigate.assert_not_called()

    # Mid-init disposal closes newly created controller cleanly and HTTPS promotes secure cookies
    dock2 = WindowsWebView2Dock(
        page_title="UTHelper",
        url="https://courses.ut.edu.vn/course/view.php?id=1",
        cookies=[{"name": "MoodleSession", "value": "x", "domain": "courses.ut.edu.vn", "secure": False}],
    )
    dock2._hwnd = 1
    cm = Mock()
    cookie_obj = SimpleNamespace(is_secure=False, is_http_only=False)
    cm.create_cookie.return_value = cookie_obj
    core2 = SimpleNamespace(cookie_manager=cm)
    dock2._inject_cookies(core2)
    assert cookie_obj.is_secure is True

    ctrl_closed = Mock()
    ctrl_closed.core_webview2 = Mock()

    def _dispose_mid_init():
        dock2._stop_event.set()
        return ctrl_closed

    dock2._handle_ctrl_done(SimpleNamespace(get_results=_dispose_mid_init))
    ctrl_closed.close.assert_called_once()

    # CoursesWebViewPane.sync_bounds does not apply redundant 80px inset on narrow screens
    fake_host = Mock()
    fake_host.backend = "webview2"
    fake_host.dock = None
    fake_host.control.return_value = SimpleNamespace()
    monkeypatch.setattr(webview_pane_mod, "build_embedded_webview_host", lambda *_a, **_kw: fake_host)
    pane = CoursesWebViewPane(
        page=None,
        source="courses",
        title="Web",
        url="https://courses.ut.edu.vn/",
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    pane.sync_bounds(0.0, 36.0, 420.0, 700.0)
    _, _, _, _, passed_kwargs = (
        *fake_host.sync_bounds.call_args.args,
        fake_host.sync_bounds.call_args.kwargs,
    )
    assert passed_kwargs["bottom_inset"] == (44.0 if pane._mobile_nav_bar.visible else 0.0)
    pane.dispose()
