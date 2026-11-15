from __future__ import annotations

import pytest


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
    assert control.url.startswith("https://moodle.example.edu/")
    assert control.session_key == "a1" * 32
    assert set(received) == set()


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
    assert "CookieManager.instance().deleteAllCookies()" in widget
    assert "session_key" in widget
    assert "window.location" not in widget
    assert "evaluateJavascript" not in widget
    assert "onConsoleMessage" not in widget
