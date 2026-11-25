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
    assert "if (_viewerLocked || !_requireInitialCookies || _cookieChannel != null) return;" in app_widget
    assert "sharedMobileSessionQueue" in app_widget
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
        "https://127.0.0.1/admin",
        "https://localhost:8443/",
        "https://foo.localhost/",
        "https://printer.local/",
        "https://10.0.0.1/internal",
        "https://192.168.1.1/",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/admin",
        "https://[::ffff:127.0.0.1]/",
        "https://2130706433/",
        "https://0177.0.0.1/",
        "https://0x7f000001/",
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


def test_app_webview_oversized_cookie_payload_signals_abort_instead_of_hanging(
    monkeypatch: pytest.MonkeyPatch,
):
    from flet_uth_quiz import UthAppWebView

    wv = UthAppWebView(
        url="https://courses.ut.edu.vn/mod/url/view.php?id=42",
        require_initial_cookies=True,
    )
    sent: list[bytes] = []
    invoked: list[tuple[str, object]] = []
    fake_channel = SimpleNamespace(send=sent.append)
    monkeypatch.setattr(wv, "get_data_channel", lambda cid: fake_channel if cid == 7 else None)

    async def _invoke(name: str, arguments=None, timeout=None):
        invoked.append((name, arguments))
        return False

    monkeypatch.setattr(wv, "_invoke_method", _invoke)
    wv.on_data_channel_open(
        SimpleNamespace(channel_name="app_webview_cookies", channel_id=7)
    )

    huge_cookies = [
        {"name": f"c{i}", "value": "x" * 1024, "domain": "courses.ut.edu.vn"}
        for i in range(40)
    ]
    assert asyncio.run(wv.bootstrap_cookies(huge_cookies)) is False
    assert sent == []
    assert invoked == [("bootstrap_cookies", {"channel_id": 7, "abort": True})]


def test_app_webview_assigns_unique_default_key_and_resets_channel_on_unmount(
    monkeypatch: pytest.MonkeyPatch,
):
    from flet_uth_quiz import UthAppWebView

    wv1 = UthAppWebView(url="https://courses.ut.edu.vn/mod/url/view.php?id=1")
    wv2 = UthAppWebView(url="https://courses.ut.edu.vn/mod/url/view.php?id=2")
    wv_custom = UthAppWebView(
        url="https://courses.ut.edu.vn/mod/url/view.php?id=3",
        key="explicit-key",
    )

    assert isinstance(wv1.key, str) and wv1.key.startswith("uth-app-wv-")
    assert isinstance(wv2.key, str) and wv2.key.startswith("uth-app-wv-")
    assert wv1.key != wv2.key
    assert wv_custom.key == "explicit-key"

    fake_ch_1 = SimpleNamespace(send=lambda _b: None)
    fake_ch_2 = SimpleNamespace(send=lambda _b: None)
    monkeypatch.setattr(
        wv1,
        "get_data_channel",
        lambda cid: {1: fake_ch_1, 2: fake_ch_2}.get(cid),
    )
    wv1.on_data_channel_open(
        SimpleNamespace(channel_name="app_webview_cookies", channel_id=1)
    )
    assert wv1._cookie_channel_ready.is_set()
    assert wv1._cookie_channel_id == 1

    # Unmounting on tab switch must clear stale channel state so remount waits for channel 2
    wv1.will_unmount()
    assert not wv1._cookie_channel_ready.is_set()
    assert wv1._cookie_channel_id is None
    assert wv1._cookie_channel is None

    invoked: list[tuple[str, object]] = []

    async def _invoke(name: str, arguments=None, timeout=None):
        invoked.append((name, arguments))
        return True

    monkeypatch.setattr(wv1, "_invoke_method", _invoke)
    wv1.on_data_channel_open(
        SimpleNamespace(channel_name="app_webview_cookies", channel_id=2)
    )
    assert asyncio.run(wv1.abort_cookie_bootstrap()) is True
    assert invoked == [("bootstrap_cookies", {"channel_id": 2, "abort": True})]


