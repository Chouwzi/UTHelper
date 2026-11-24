from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_app_webview_exported_and_registered_without_altering_quiz_player_privacy():
    from flet_uth_quiz import UthAppWebView

    flutter = Path(__file__).resolve().parents[1] / "flutter" / "flet_uth_quiz"
    extension = (flutter / "lib/src/extension.dart").read_text(encoding="utf-8")
    quiz_widget = (flutter / "lib/src/quiz_player_control.dart").read_text(
        encoding="utf-8"
    )
    app_widget = (flutter / "lib/src/app_webview_control.dart").read_text(
        encoding="utf-8"
    )

    assert UthAppWebView(url="https://courses.ut.edu.vn/")._c == "UthAppWebView"
    assert "'UthAppWebView'" in extension
    assert "evaluateJavascript" not in quiz_widget
    assert "onConsoleMessage" not in quiz_widget
    assert "openDataChannel()" in app_widget
    assert "if (!_requireInitialCookies || _cookieChannel != null) return;" in app_widget
    assert "app_webview_cookies" in app_widget
    assert "onConsoleMessage" in app_widget


@pytest.mark.parametrize(
    "bad_url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,<script>",
        "http://evil.example/phish",
        "https://user:pass@courses.ut.edu.vn/",
        "https://courses.ut.edu.vn/\n",
    ],
)
def test_app_webview_rejects_unsafe_initial_urls(bad_url: str):
    from flet_uth_quiz import UthAppWebView

    with pytest.raises(ValueError):
        UthAppWebView(url=bad_url)


def test_app_webview_accepts_https_and_loopback_and_bootstraps_cookies_via_data_channel(
    monkeypatch: pytest.MonkeyPatch,
):
    from flet_uth_quiz import UthAppWebView

    wv = UthAppWebView(
        url="https://courses.ut.edu.vn/mod/url/view.php?id=42",
        require_initial_cookies=True,
    )
    sent_payloads: list[bytes] = []
    invoke_calls: list[tuple[str, object, object]] = []

    fake_channel = SimpleNamespace(send=sent_payloads.append)
    monkeypatch.setattr(wv, "get_data_channel", lambda cid: fake_channel if cid == 9 else None)

    async def _invoke(name: str, arguments=None, timeout=None):
        invoke_calls.append((name, arguments, timeout))
        return True

    monkeypatch.setattr(wv, "_invoke_method", _invoke)
    wv.on_data_channel_open(
        SimpleNamespace(channel_name="app_webview_cookies", channel_id=9)
    )

    cookies = [
        {
            "name": "MoodleSession",
            "value": "super_secret_cookie_val",
            "domain": "courses.ut.edu.vn",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "expires": None,
        }
    ]
    assert asyncio.run(wv.bootstrap_cookies(cookies)) is True
    assert invoke_calls == [("bootstrap_cookies", {"channel_id": 9}, 10)]
    assert "super_secret_cookie_val" not in repr(invoke_calls)
    assert "super_secret_cookie_val" not in repr(wv)

    decoded = json.loads(sent_payloads[0].decode("utf-8"))
    assert decoded["target_url"] == "https://courses.ut.edu.vn/mod/url/view.php?id=42"
    assert decoded["cookies"][0]["name"] == "MoodleSession"

    # Loopback viewer URL is also accepted
    viewer_wv = UthAppWebView(
        url="http://127.0.0.1:51234/pdf-viewer/tok/pdf_host.html?src=x",
        viewer_locked=True,
        allowed_loopback_ports=[51234],
    )
    monkeypatch.setattr(viewer_wv, "_invoke_method", _invoke)
    assert asyncio.run(viewer_wv.run_javascript("window.setTheme('dark')")) is None
    assert asyncio.run(viewer_wv.go_back()) is True
    assert asyncio.run(viewer_wv.go_forward()) is True
    assert asyncio.run(viewer_wv.reload()) is True


def test_app_webview_flet_session_event_dispatch_resolves_all_event_types(
    monkeypatch: pytest.MonkeyPatch,
):
    from unittest.mock import AsyncMock

    from flet.controls.control_event import Event, get_event_field_type
    from flet_uth_quiz import UthAppWebView

    received: dict[str, list[Event]] = {}

    def _record(name: str):
        return lambda event: received.setdefault(name, []).append(event)

    wv = UthAppWebView(
        url="https://courses.ut.edu.vn/mod/url/view.php?id=42",
        require_initial_cookies=True,
        on_page_started=_record("page_started"),
        on_page_ended=_record("page_ended"),
        on_url_change=_record("url_change"),
        on_title_change=_record("title_change"),
        on_history_change=_record("history_change"),
        on_web_error=_record("web_error"),
        on_console_message=_record("console_message"),
        on_external_url=_record("external_url"),
    )

    event_fields = (
        "on_data_channel_open",
        "on_page_started",
        "on_page_ended",
        "on_url_change",
        "on_title_change",
        "on_history_change",
        "on_web_error",
        "on_console_message",
        "on_external_url",
    )
    for field_name in event_fields:
        event_cls = get_event_field_type(wv, field_name)
        assert event_cls is not None, field_name

    fake_channel = SimpleNamespace(send=lambda _b: None)
    monkeypatch.setattr(
        wv, "get_data_channel", lambda cid: fake_channel if cid == 11 else None
    )
    fake_page = SimpleNamespace(
        session=SimpleNamespace(after_event=AsyncMock(), index={wv._i: wv})
    )
    monkeypatch.setattr(UthAppWebView, "page", property(lambda _s: fake_page))

    asyncio.run(
        wv._trigger_event(
            "data_channel_open",
            {"channel_name": "app_webview_cookies", "channel_id": 11},
        )
    )
    asyncio.run(wv._trigger_event("console_message", '__UTH_MSG__:{"type":"ready"}'))
    asyncio.run(wv._trigger_event("page_started", "https://courses.ut.edu.vn/"))

    assert wv._cookie_channel_id == 11
    assert wv._cookie_channel is fake_channel
    assert wv._cookie_channel_ready.is_set()
    assert len(received["console_message"]) == 1
    assert isinstance(received["console_message"][0], Event)
    assert received["console_message"][0].control is wv
    assert received["console_message"][0].data == '__UTH_MSG__:{"type":"ready"}'
    assert len(received["page_started"]) == 1
    assert isinstance(received["page_started"][0], Event)
    assert received["page_started"][0].control is wv
    assert received["page_started"][0].data == "https://courses.ut.edu.vn/"

