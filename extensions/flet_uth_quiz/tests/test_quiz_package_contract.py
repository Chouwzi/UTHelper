from __future__ import annotations

import asyncio
import json

import pytest


@pytest.mark.parametrize(
    ("event_name", "code"),
    [("ready", "ready"), ("close", "closed"), ("error", "load_timeout"),
     ("external_link", "external_link"),
     ("session_expired", "session_expired")],
)
def test_native_events_dispatch_through_flet(event_name, code, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from flet_uth_quiz import UthQuizPlayer

    received = []
    control = UthQuizPlayer(
        url="https://courses.ut.edu.vn/mod/quiz/view.php?id=42",
        session_key="a1" * 32,
        **{f"on_{event_name}": lambda event: received.append(event)},
    )
    session = SimpleNamespace(index={control._i: control}, after_event=AsyncMock())
    monkeypatch.setattr(type(control), "page", property(lambda self: SimpleNamespace(session=session)))

    asyncio.run(control._trigger_event(event_name, code))

    assert len(received) == 1
    assert received[0].data == code
    assert received[0].control is control
    session.after_event.assert_awaited_once_with(control)


def test_control_constructs_with_safe_event_handlers():
    from flet_uth_quiz import UthQuizPlayer

    received = {}
    control = UthQuizPlayer(
        url="https://moodle.example.edu/mod/quiz/attempt.php?attempt=42",
        session_key="a1" * 32,
        on_ready=lambda event: received.setdefault("ready", event.data),
        on_close=lambda event: received.setdefault("close", event.data),
        on_error=lambda event: received.setdefault("error", event.data),
        on_external_link=lambda event: received.setdefault("external", event.data),
    )

    assert control._c == "UthQuizPlayer"
    assert control.expand is True
    assert control.require_session is False
    assert control.url.startswith("https://moodle.example.edu/")
    assert control.session_key == "a1" * 32
    assert set(received) == set()


def test_bootstrap_sends_cookies_over_data_channel_not_invoke_arguments(monkeypatch):
    from flet_uth_quiz import UthQuizPlayer

    class FakeChannel:
        payload = None

        def send(self, payload):
            self.payload = payload

    control = UthQuizPlayer(
        url="https://courses.ut.edu.vn/mod/quiz/view.php?id=42",
        session_key="a1" * 32,
        require_session=True,
    )
    channel = FakeChannel()
    control._session_channel = channel
    control._session_channel_id = 17
    control._session_channel_ready.set()
    invocation = {}

    async def invoke(name, arguments, timeout=None):
        invocation.update(name=name, arguments=arguments, timeout=timeout)
        return True

    monkeypatch.setattr(control, "_invoke_method", invoke)
    secret_cookie = "test-session-cookie-4d932"
    cookies = [
        {
            "name": "MoodleSession",
            "value": secret_cookie,
            "domain": None,
            "path": "/",
            "secure": True,
            "http_only": True,
            "same_site": "Lax",
            "expires": None,
        }
    ]

    assert asyncio.run(control.bootstrap_session(cookies)) is True
    assert json.loads(channel.payload) == {"cookies": cookies}
    assert invocation == {
        "name": "bootstrap_session",
        "arguments": {"channel_id": 17},
        "timeout": 15,
    }
    assert secret_cookie not in repr(invocation)
    assert secret_cookie not in repr(control)
    assert "cookies" not in repr(control)


def test_bootstrap_failure_hides_runtime_details(monkeypatch):
    from flet_uth_quiz import UthQuizPlayer

    class FakeChannel:
        def send(self, _payload):
            pass

    control = UthQuizPlayer(
        url="https://courses.ut.edu.vn/mod/quiz/view.php?id=42",
        session_key="a1" * 32,
        require_session=True,
    )
    control._session_channel = FakeChannel()
    control._session_channel_id = 17
    control._session_channel_ready.set()

    async def invoke(*_args, **_kwargs):
        raise TimeoutError("timeout included secret-session-value")

    monkeypatch.setattr(control, "_invoke_method", invoke)
    cookies = [
        {
            "name": "MoodleSession",
            "value": "secret-session-value",
            "domain": None,
            "path": "/",
            "secure": True,
            "http_only": True,
            "same_site": None,
            "expires": None,
        }
    ]

    with pytest.raises(RuntimeError, match="^session_unavailable$") as error:
        asyncio.run(control.bootstrap_session(cookies))
    assert "secret-session-value" not in repr(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "url",
    ["", "moodle.example.edu/quiz", "javascript:alert(1)", "file:///tmp/quiz.html"],
)
def test_control_rejects_non_http_quiz_urls(url: str):
    from flet_uth_quiz import UthQuizPlayer

    with pytest.raises(ValueError, match="absolute HTTP or HTTPS"):
        UthQuizPlayer(url=url, session_key="a1" * 32)


@pytest.mark.parametrize("session_key", ["", "not-a-digest", "a1" * 31, "g1" * 32])
def test_control_requires_hashed_site_account_identity(session_key: str):
    from flet_uth_quiz import UthQuizPlayer

    with pytest.raises(ValueError, match="SHA-256 hex digest"):
        UthQuizPlayer(
            url="https://moodle.example.edu/mod/quiz/attempt.php",
            session_key=session_key,
        )


def test_flutter_extension_registers_native_widget_and_privacy_boundaries():
    from pathlib import Path

    flutter = Path(__file__).resolve().parents[1] / "flutter" / "flet_uth_quiz"
    pubspec = (flutter / "pubspec.yaml").read_text(encoding="utf-8")
    extension = (flutter / "lib/src/extension.dart").read_text(encoding="utf-8")
    widget = (flutter / "lib/src/quiz_player_control.dart").read_text(
        encoding="utf-8"
    )

    assert "flutter_inappwebview: 6.1.5" in pubspec
    assert "createWidget(Key? key, Control control)" in extension
    assert "'UthQuizPlayer'" in extension
    assert "WebViewEnvironmentSettings" in widget
    assert "shouldOverrideUrlLoading" in widget
    assert "onCreateWindow" in widget
    assert "onCloseWindow" in widget
    assert "load_timeout" in widget
    assert "_cookieManager().deleteAllCookies()" in widget
    assert "openDataChannel()" in widget
    assert "data_channel_open" in widget
    assert "channel.messages.listen(_onSessionPayload)" in widget
    assert "channel_id" in widget
    assert "setCookie(" in widget
    assert "session_key" in widget
    assert "window.location" not in widget
    assert "evaluateJavascript" not in widget
    assert "onConsoleMessage" not in widget
