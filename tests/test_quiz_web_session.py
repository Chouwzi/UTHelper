"""Saved-account Moodle cookie handoff policy for the quiz WebView."""

from __future__ import annotations

import os
import sys
from http.cookiejar import Cookie
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import core.quiz_web_session as web_session
from config import settings
from core.client import MoodleClient
from core.quiz_player import (
    QuizLaunchTarget,
    acquire_quiz_session_lease,
    build_quiz_launch_target,
    _begin_quiz_session_login,
    _end_quiz_session_login,
)
from core.quiz_web_session import (
    QuizWebCookie,
    QuizWebSession,
    QuizWebSessionError,
    prepare_quiz_web_session,
)


def _cookie(
    name: str,
    value: str,
    domain: str,
    *,
    path: str = "/",
    secure: bool = True,
    domain_specified: bool = False,
    http_only: bool = True,
    same_site: str | None = "None",
    expires: int | None = None,
) -> Cookie:
    rest = {}
    if http_only:
        rest["HttpOnly"] = None
    if same_site is not None:
        rest["SameSite"] = same_site
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=domain_specified,
        domain_initial_dot=domain.startswith("."),
        path=path,
        path_specified=True,
        secure=secure,
        expires=expires,
        discard=expires is None,
        comment=None,
        comment_url=None,
        rest=rest,
        rfc2109=False,
    )


def _install_mock_html_login(monkeypatch, *, extra_cookies=(), after_login=None):
    from core.moodle_sites import TRUSTED_MOODLE_SITES

    monkeypatch.setattr(settings, "UTH_USERNAME", "student-42")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "private-example-password")
    calls = []
    sites_by_origin = {site.origin: site for site in TRUSTED_MOODLE_SITES}

    def fake_open_html(self, path, *, data=None):
        calls.append((self.moodle_site_origin, path, data))
        site = sites_by_origin[self.moodle_site_origin]
        if path == "/login/index.php" and data is None:
            return (
                f"{site.origin}/login/index.php",
                '<form><input name="logintoken" value="one-time-token"></form>',
            )
        if path == "/login/index.php" and data is not None:
            assert data == {
                "anchor": "",
                "logintoken": "one-time-token",
                "username": "student-42",
                "password": "private-example-password",
            }
            self._cookie_jar.set_cookie(
                _cookie("MoodleSession", "private-session-cookie", site.origin.split("//", 1)[1])
            )
            for cookie in extra_cookies:
                self._cookie_jar.set_cookie(cookie)
            if after_login is not None:
                after_login(self, site)
            return f"{site.origin}/my/", ""
        if path == "/my/" and data is None:
            return f"{site.origin}/my/", '"sesskey":"privateSesskey42"'
        raise AssertionError("unexpected HTML login request")

    monkeypatch.setattr(MoodleClient, "_open_html", fake_open_html)
    return calls


@pytest.mark.parametrize(
    ("site_origin", "site_identity"),
    [
        ("https://courses.ut.edu.vn", "courses"),
        ("https://thnn.ut.edu.vn", "thnn"),
    ],
)
def test_prepare_quiz_web_session_performs_scoped_saved_credential_login(
    monkeypatch, site_origin, site_identity
):
    calls = _install_mock_html_login(monkeypatch)
    target = build_quiz_launch_target(
        f"{site_origin}/mod/quiz/view.php?id=52", "student-42"
    )
    lease = acquire_quiz_session_lease(target.session_key)

    try:
        session = prepare_quiz_web_session(target, lease)
    finally:
        lease.release()

    assert isinstance(session, QuizWebSession)
    assert session.site_identity == site_identity
    assert session.origin == site_origin
    assert [call[1] for call in calls] == [
        "/login/index.php",
        "/login/index.php",
        "/my/",
    ]
    assert calls[1][2]["username"] == "student-42"
    assert calls[1][2]["password"] == "private-example-password"
    assert len(session.cookies) == 1
    cookie = session.cookies[0]
    assert cookie.name == "MoodleSession"
    assert cookie.value == "private-session-cookie"
    assert cookie.domain is None  # Preserve the host-only scope.
    assert cookie.path == "/"
    assert cookie.secure is True
    assert cookie.http_only is True
    assert cookie.same_site == "None"
    assert cookie.expires is None


def test_native_cookie_schema_is_detached_and_secret_repr_is_redacted(monkeypatch):
    _install_mock_html_login(monkeypatch)
    target = build_quiz_launch_target(
        "https://courses.ut.edu.vn/mod/quiz/view.php?id=52", "student-42"
    )
    lease = acquire_quiz_session_lease(target.session_key)
    try:
        session = prepare_quiz_web_session(target, lease)
    finally:
        lease.release()

    assert "private-session-cookie" not in repr(session)
    assert "private-session-cookie" not in repr(session.cookies[0])
    native = session.to_native_cookies()
    assert native == [
        {
            "name": "MoodleSession",
            "value": "private-session-cookie",
            "domain": None,
            "path": "/",
            "secure": True,
            "http_only": True,
            "same_site": "None",
            "expires": None,
        }
    ]
    native[0]["value"] = "changed-copy"
    assert session.cookies[0].value == "private-session-cookie"


def test_export_omits_remember_user_and_non_session_or_other_site_cookies(monkeypatch):
    host = "courses.ut.edu.vn"
    calls = _install_mock_html_login(
        monkeypatch,
        extra_cookies=(
            _cookie("MOODLEID1_", "private-remember-user", host),
            _cookie("tracking", "private-tracking", host),
            _cookie("MoodleSession", "private-other-site", ".ut.edu.vn", domain_specified=True),
            _cookie("MoodleSession", "private-other-path", host, path="/course"),
        ),
    )
    target = build_quiz_launch_target(
        f"https://{host}/mod/quiz/view.php?id=52", "student-42"
    )
    lease = acquire_quiz_session_lease(target.session_key)
    try:
        session = prepare_quiz_web_session(target, lease)
    finally:
        lease.release()

    assert len(calls) == 3
    assert len(session.cookies) == 1
    assert session.cookies[0].name == "MoodleSession"
    assert session.cookies[0].value == "private-session-cookie"


def test_non_secure_https_session_cookie_is_preserved():
    from core.moodle_sites import COURSES_MOODLE_SITE

    client = MoodleClient(COURSES_MOODLE_SITE.origin)
    client._cookie_jar.set_cookie(
        _cookie(
            "MoodleSession",
            "private-session-cookie",
            "courses.ut.edu.vn",
            secure=False,
        )
    )
    target = build_quiz_launch_target(
        "https://courses.ut.edu.vn/mod/quiz/view.php?id=52", "student-42"
    )

    cookies = web_session._export_moodle_session_cookies(
        client, COURSES_MOODLE_SITE, target
    )

    assert cookies[0].secure is False


@pytest.mark.parametrize(
    ("target_factory", "expected_reason"),
    [
        (
            lambda: build_quiz_launch_target(
                "https://courses.ut.edu.vn/mod/quiz/view.php?id=52", "somebody-else"
            ),
            "account_mismatch",
        ),
        (
            lambda: QuizLaunchTarget(
                site_identity="courses",
                course_module_id=52,
                url="https://evil.example/mod/quiz/view.php?id=52",
                session_key="0" * 64,
            ),
            "session_site_mismatch",
        ),
    ],
    ids=["different-account", "untrusted-host"],
)
def test_rejects_account_or_site_mismatch_without_network(
    monkeypatch, target_factory, expected_reason
):
    monkeypatch.setattr(settings, "UTH_USERNAME", "student-42")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "private-example-password")
    monkeypatch.setattr(MoodleClient, "login_session", Mock())
    target = target_factory()
    lease = acquire_quiz_session_lease(target.session_key)
    try:
        with pytest.raises(QuizWebSessionError) as error:
            prepare_quiz_web_session(target, lease)
    finally:
        lease.release()

    assert error.value.reason == expected_reason
    MoodleClient.login_session.assert_not_called()


def test_rejects_inactive_or_other_site_player_lease(monkeypatch):
    _install_mock_html_login(monkeypatch)
    target = build_quiz_launch_target(
        "https://courses.ut.edu.vn/mod/quiz/view.php?id=52", "student-42"
    )
    wrong_target = build_quiz_launch_target(
        "https://thnn.ut.edu.vn/mod/quiz/view.php?id=52", "student-42"
    )
    lease = acquire_quiz_session_lease(wrong_target.session_key)
    try:
        with pytest.raises(QuizWebSessionError) as error:
            prepare_quiz_web_session(target, lease)
    finally:
        lease.release()
    assert error.value.reason == "session_site_mismatch"

    inactive = acquire_quiz_session_lease(target.session_key)
    inactive.release()
    with pytest.raises(QuizWebSessionError) as error:
        prepare_quiz_web_session(target, inactive)
    assert error.value.reason == "session_lease_inactive"


def test_rejects_missing_saved_credentials_without_network(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "student-42")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")
    open_html = Mock()
    monkeypatch.setattr(MoodleClient, "_open_html", open_html)
    target = build_quiz_launch_target(
        "https://thnn.ut.edu.vn/mod/quiz/view.php?id=52", "student-42"
    )
    lease = acquire_quiz_session_lease(target.session_key)
    try:
        with pytest.raises(QuizWebSessionError) as error:
            prepare_quiz_web_session(target, lease)
    finally:
        lease.release()
    assert error.value.reason == "missing_credentials"
    open_html.assert_not_called()


def test_missing_auth_cookie_is_a_concise_failure(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "student-42")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "private-example-password")

    def login_without_session_cookie(self, path, *, data=None):
        if path == "/login/index.php" and data is None:
            return f"{self.moodle_site_origin}/login/index.php", '<input name="logintoken" value="token">'
        if path == "/login/index.php" and data is not None:
            return f"{self.moodle_site_origin}/my/", ""
        if path == "/my/":
            return f"{self.moodle_site_origin}/my/", '"sesskey":"freshsesskey"'
        raise AssertionError("unexpected login request")

    monkeypatch.setattr(MoodleClient, "_open_html", login_without_session_cookie)
    target = build_quiz_launch_target(
        "https://courses.ut.edu.vn/mod/quiz/view.php?id=52", "student-42"
    )
    lease = acquire_quiz_session_lease(target.session_key)
    try:
        with pytest.raises(QuizWebSessionError) as error:
            prepare_quiz_web_session(target, lease)
    finally:
        lease.release()
    assert error.value.reason == "session_cookie_missing"


def test_temporary_lease_protects_login_export_after_ui_lease_releases(monkeypatch):
    calls = _install_mock_html_login(monkeypatch)
    target = build_quiz_launch_target(
        "https://courses.ut.edu.vn/mod/quiz/view.php?id=52", "student-42"
    )
    ui_lease = acquire_quiz_session_lease(target.session_key)
    original_export = web_session._export_moodle_session_cookies

    def release_ui_then_export(client, site, launch_target):
        ui_lease.release()
        # The login has completed, so this asserts the temporary player-owned
        # lease alone still prevents a background login during cookie export.
        assert _begin_quiz_session_login(target.session_key) is False
        return original_export(client, site, launch_target)

    monkeypatch.setattr(web_session, "_export_moodle_session_cookies", release_ui_then_export)
    try:
        session = prepare_quiz_web_session(target, ui_lease)
    finally:
        ui_lease.release()

    assert session.cookies[0].name == "MoodleSession"
    assert len(calls) == 3
    assert _begin_quiz_session_login(target.session_key) is True
    _end_quiz_session_login(target.session_key)
