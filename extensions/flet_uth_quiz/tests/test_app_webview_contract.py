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
