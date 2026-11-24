from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import FrozenInstanceError
from http.cookiejar import Cookie
from typing import Any

import pytest

from config import settings
from core.client import MoodleClient
from core.moodle_sites import COURSES_MOODLE_SITE
from core.portal_client import PortalClient
from core.quiz_player import acquire_quiz_session_lease, quiz_session_key
from core.webview_auth import (
    UthWebViewAuthService,
    WebViewAuthBootstrap,
    WebViewRecoveryTracker,
    build_portal_local_storage_script,
    classify_uth_webview_url,
    is_uth_login_url,
    normalize_uth_webview_target_url,
)


class _FakeMoodleClient:
    def __init__(
        self,
        cookies: list[dict[str, Any]] | None = None,
        *,
        cached_cookies: list[dict[str, Any]] | None = None,
        raise_on_call: Exception | None = None,
        quiz_active_on_force: bool = False,
    ) -> None:
        self._cookies = list(cookies or [])
        self._cached_cookies = (
            list(cached_cookies) if cached_cookies is not None else list(self._cookies)
        )
        self._raise_on_call = raise_on_call
        self._quiz_active_on_force = quiz_active_on_force
        self._last_login_error = ""
        self.calls: list[dict[str, Any]] = []

    def get_session_cookies(
        self,
        *,
        ensure_login: bool = True,
        verify_live: bool = False,
        force_relogin: bool = False,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "ensure_login": ensure_login,
                "verify_live": verify_live,
                "force_relogin": force_relogin,
            }
        )
        if self._raise_on_call is not None:
            raise self._raise_on_call
        if force_relogin and self._quiz_active_on_force:
            self._last_login_error = "quiz_session_active"
            return list(self._cookies)
        self._last_login_error = ""
        if not ensure_login and not force_relogin:
            return list(self._cached_cookies)
        return list(self._cookies)


class _FakePortalClient:
    def __init__(
        self,
        token: str = "jwt.header.payload",
        role: str = "sv",
        *,
        raise_on_call: Exception | None = None,
    ) -> None:
        self._token = token
        self._role = role
        self._raise_on_call = raise_on_call
        self.calls: list[tuple[str, str, bool]] = []

    def ensure_web_session(
        self, username: str, password: str, *, force: bool = False
    ) -> tuple[str, str]:
        self.calls.append((username, password, force))
        if self._raise_on_call is not None:
            raise self._raise_on_call
        return self._token, self._role


def _moodle_cookie(
    value: str,
    domain: str,
    *,
    name: str = "MoodleSession",
) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "domain_specified": False,
        "path": "/",
        "secure": True,
        "expires": None,
        "httpOnly": True,
        "sameSite": "Lax",
    }


def test_classify_and_login_url_host_scheme_and_port_discipline():
    assert classify_uth_webview_url("https://courses.ut.edu.vn/my/") == "courses"
    assert classify_uth_webview_url("https://elearning.ut.edu.vn/course/view.php?id=1") == "courses"
    assert classify_uth_webview_url("https://ELEARNING.UT.EDU.VN/login/index.php") == "courses"
    assert classify_uth_webview_url("https://thnn.ut.edu.vn/my/") == "thnn"
    assert classify_uth_webview_url("https://portal.ut.edu.vn/dashboard") == "portal"

    # Non-HTTPS, any explicit port (including :443 and :8443), lookalikes, credentials, loopback -> external
    for external_url in (
        "http://courses.ut.edu.vn/login/index.php",
        "http://thnn.ut.edu.vn/login/index.php",
        "http://portal.ut.edu.vn/",
        "https://courses.ut.edu.vn:443/my/",
        "https://courses.ut.edu.vn:443/login/index.php",
        "https://ELEARNING.UT.EDU.VN:443/login/index.php",
        "https://thnn.ut.edu.vn:443/login/",
        "https://portal.ut.edu.vn:443/",
        "https://courses.ut.edu.vn:8443/login/index.php",
        "https://elearning.ut.edu.vn:8443/course/view.php?id=1",
        "https://thnn.ut.edu.vn:8443/login/",
        "https://portal.ut.edu.vn:8443/",
        "https://courses.ut.edu.vn.evil.example/login/index.php",
        "https://user:pass@courses.ut.edu.vn/login/index.php",
        "http://127.0.0.1:54321/preview/index.html",
        "https://docs.example.com/guide",
        "",
        "not a url",
    ):
        assert classify_uth_webview_url(external_url) == "external"
        assert is_uth_login_url(external_url) is None

    # Exact UTH login URLs
    assert is_uth_login_url("https://courses.ut.edu.vn/login/index.php") == "courses"
    assert is_uth_login_url("https://courses.ut.edu.vn/login") == "courses"
    assert is_uth_login_url("https://courses.ut.edu.vn/login/") == "courses"
    assert is_uth_login_url("https://elearning.ut.edu.vn/login/index.php?token=x") == "courses"
    assert is_uth_login_url("https://thnn.ut.edu.vn/login/index.php") == "thnn"
    assert is_uth_login_url("https://thnn.ut.edu.vn/login") == "thnn"
    assert is_uth_login_url("https://thnn.ut.edu.vn/login/") == "thnn"
    assert is_uth_login_url("https://portal.ut.edu.vn") == "portal"
    assert is_uth_login_url("https://portal.ut.edu.vn/") == "portal"
    assert is_uth_login_url("https://portal.ut.edu.vn/login") == "portal"
    assert is_uth_login_url("https://portal.ut.edu.vn/login/") == "portal"

    # Non-login UTH paths must not trigger login detection
    assert is_uth_login_url("https://courses.ut.edu.vn/my/") is None
    assert is_uth_login_url("https://courses.ut.edu.vn/login/logout.php") is None
    assert is_uth_login_url("https://courses.ut.edu.vn/login/forgot_password.php") is None
    assert is_uth_login_url("https://thnn.ut.edu.vn/course/view.php?id=55") is None
    assert is_uth_login_url("https://portal.ut.edu.vn/dashboard") is None
    assert is_uth_login_url("https://portal.ut.edu.vn/tuition") is None


def test_normalize_unwraps_same_origin_wantsurl_and_canonicalizes_elearning_alias():
    alias_url = "https://elearning.ut.edu.vn/course/view.php?id=121#section-2"
    assert classify_uth_webview_url(alias_url) == "courses"
    assert (
        normalize_uth_webview_target_url(alias_url)
        == "https://courses.ut.edu.vn/course/view.php?id=121#section-2"
    )
    explicit_443_alias = "https://elearning.ut.edu.vn:443/course/view.php?id=121"
    assert classify_uth_webview_url(explicit_443_alias) == "external"
    assert normalize_uth_webview_target_url(explicit_443_alias) == explicit_443_alias

    # Expired Portal SSO link -> unwrap same-site wantsurl
    sso_link = (
        "https://courses.ut.edu.vn/login/index.php"
        "?token=expired&wantsurl=https%3A%2F%2Fcourses.ut.edu.vn%2Fcourse%2Fview.php%3Fid%3D121"
    )
    assert (
        normalize_uth_webview_target_url(sso_link)
        == "https://courses.ut.edu.vn/course/view.php?id=121"
    )

    # Alias inside wantsurl on courses login -> canonicalized to courses.ut.edu.vn
    alias_wants = (
        "https://elearning.ut.edu.vn/login/index.php"
        "?wantsurl=https%3A%2F%2Felearning.ut.edu.vn%2Fmod%2Fassign%2Fview.php%3Fid%3D88"
    )
    assert (
        normalize_uth_webview_target_url(alias_wants)
        == "https://courses.ut.edu.vn/mod/assign/view.php?id=88"
    )

    # Off-origin, cross-site, login-loop, custom-port, non-HTTPS, and empty wantsurl -> rejected
    for bad_wants in (
        "https%3A%2F%2Fevil.example%2Fphish",
        "https%3A%2F%2Fthnn.ut.edu.vn%2Fcourse%2Fview.php%3Fid%3D55",
        "https%3A%2F%2Fportal.ut.edu.vn%2Fdashboard",
        "https%3A%2F%2Fcourses.ut.edu.vn%2Flogin%2Findex.php",
        "https%3A%2F%2Felearning.ut.edu.vn%2Flogin%2F",
        "https%3A%2F%2Fcourses.ut.edu.vn%3A443%2Fcourse%2Fview.php%3Fid%3D1",
        "https%3A%2F%2Fcourses.ut.edu.vn%3A8443%2Fcourse%2Fview.php%3Fid%3D1",
        "http%3A%2F%2Fcourses.ut.edu.vn%2Fcourse%2Fview.php%3Fid%3D1",
        "javascript%3Aalert(1)",
        "%2Fcourse%2Fview.php%3Fid%3D1",
        "",
    ):
        url = f"https://courses.ut.edu.vn/login/index.php?token=expired&wantsurl={bad_wants}"
        assert normalize_uth_webview_target_url(url) == "https://courses.ut.edu.vn/my/"

    # Cross-site wantsurl on THNN falls back to THNN dashboard
    thnn_bad = (
        "https://thnn.ut.edu.vn/login/index.php"
        "?wantsurl=https%3A%2F%2Fcourses.ut.edu.vn%2Fcourse%2Fview.php%3Fid%3D9"
    )
    assert normalize_uth_webview_target_url(thnn_bad) == "https://thnn.ut.edu.vn/my/"

    # Moodle login URL without wantsurl respects has_credentials
    assert (
        normalize_uth_webview_target_url(
            "https://courses.ut.edu.vn/login/index.php", has_credentials=True
        )
        == "https://courses.ut.edu.vn/my/"
    )
    assert (
        normalize_uth_webview_target_url(
            "https://elearning.ut.edu.vn/login/index.php", has_credentials=False
        )
        == "https://courses.ut.edu.vn/login/index.php"
    )
    assert (
        normalize_uth_webview_target_url(
            "https://thnn.ut.edu.vn/login/", has_credentials=True
        )
        == "https://thnn.ut.edu.vn/my/"
    )
    assert (
        normalize_uth_webview_target_url(
            "https://thnn.ut.edu.vn/login/", has_credentials=False
        )
        == "https://thnn.ut.edu.vn/login/"
    )

    # Portal root/login rewrites to /dashboard iff has_credentials=True
    for portal_login in (
        "https://portal.ut.edu.vn",
        "https://portal.ut.edu.vn/",
        "https://portal.ut.edu.vn/login",
        "https://portal.ut.edu.vn/login/",
    ):
        assert (
            normalize_uth_webview_target_url(portal_login, has_credentials=True)
            == "https://portal.ut.edu.vn/dashboard"
        )
        assert (
            normalize_uth_webview_target_url(portal_login, has_credentials=False)
            == portal_login
        )

    # External and loopback URLs remain untouched
    loopback = "http://127.0.0.1:41234/pdf-viewer/tok/index.html?page=3"
    external = "https://docs.example.com/Path?a=1#top"
    custom_port = "https://courses.ut.edu.vn:8443/login/index.php"
    assert normalize_uth_webview_target_url(loopback) == loopback
    assert normalize_uth_webview_target_url(external) == external
    assert normalize_uth_webview_target_url(custom_port) == custom_port


def test_build_portal_local_storage_script_is_origin_gated_and_escaped():
    weird_token = 'jwt.a"b\\c\nd\u2028e'
    script = build_portal_local_storage_script(weird_token, "gv")

    assert 'window.location.origin === "https://portal.ut.edu.vn"' in script
    assert '"account"' in script
    assert '"role"' in script
    assert json.dumps(weird_token) in script
    assert json.dumps("gv") in script

    default_role_script = build_portal_local_storage_script("tok123", "")
    assert json.dumps("sv") in default_role_script

    assert build_portal_local_storage_script("") == ""
    assert build_portal_local_storage_script("   ") == ""


def test_prepare_initial_auth_builds_origin_gated_portal_script_and_moodle_live_cookies():
    courses_client = _FakeMoodleClient(
        cookies=[_moodle_cookie("courses_live", "courses.ut.edu.vn")]
    )
    thnn_client = _FakeMoodleClient(
        cookies=[_moodle_cookie("thnn_fresh_network", "thnn.ut.edu.vn")],
        cached_cookies=[_moodle_cookie("thnn_cached", "thnn.ut.edu.vn")],
    )
    portal_client = _FakePortalClient(token="eyJhbGciOiJIUzI1NiJ9.payload.sig", role="sv")

    service = UthWebViewAuthService(
        courses_client=courses_client,
        thnn_client=thnn_client,
        portal_client=portal_client,
        credentials_provider=lambda: ("2251120001", "pw"),
    )

    assert service.client_for_site("courses") is courses_client
    assert service.client_for_site("thnn") is thnn_client
    assert service.client_for_site("portal") is portal_client
    assert service.client_for_site("external") is None

    # Portal initial auth
    portal_boot = service.prepare_initial_auth("https://portal.ut.edu.vn/tuition")
    assert isinstance(portal_boot, WebViewAuthBootstrap)
    assert portal_boot.site == "portal"
    assert portal_boot.target_url == "https://portal.ut.edu.vn/tuition"
    assert portal_boot.cookies == ()
    assert portal_client.calls == [("2251120001", "pw", False)]
    assert 'window.location.origin === "https://portal.ut.edu.vn"' in portal_boot.portal_init_script
    assert json.dumps("eyJhbGciOiJIUzI1NiJ9.payload.sig") in portal_boot.portal_init_script
    assert json.dumps("sv") in portal_boot.portal_init_script
    assert '"pw"' not in portal_boot.portal_init_script

    # Portal root rewrites to /dashboard when bootstrap succeeds
    portal_root = service.prepare_initial_auth("https://portal.ut.edu.vn/")
    assert portal_root.target_url == "https://portal.ut.edu.vn/dashboard"
    assert portal_root.portal_init_script != ""

    # Moodle courses initial auth: live-verifies courses and warms cached thnn without network login
    courses_boot = service.prepare_initial_auth(
        "https://courses.ut.edu.vn/course/view.php?id=99"
    )
    assert courses_boot.site == "courses"
    assert courses_boot.target_url == "https://courses.ut.edu.vn/course/view.php?id=99"
    assert courses_boot.portal_init_script == ""
    assert courses_client.calls == [
        {"ensure_login": True, "verify_live": True, "force_relogin": False}
    ]
    assert thnn_client.calls == [
        {"ensure_login": False, "verify_live": False, "force_relogin": False}
    ]
    by_domain = {c["domain"]: c["value"] for c in courses_boot.cookies}
    assert by_domain == {
        "courses.ut.edu.vn": "courses_live",
        "thnn.ut.edu.vn": "thnn_cached",
    }


def test_prepare_initial_auth_works_without_credentials_provider_for_moodle_and_skips_portal():
    courses_client = _FakeMoodleClient(
        cookies=[_moodle_cookie("moodle_self_managed", "courses.ut.edu.vn")]
    )
    portal_client = _FakePortalClient()
    service = UthWebViewAuthService(
        courses_client=courses_client,
        portal_client=portal_client,
        credentials_provider=None,
    )

    moodle_boot = service.prepare_initial_auth(
        "https://elearning.ut.edu.vn/course/view.php?id=12"
    )
    assert moodle_boot.site == "courses"
    assert moodle_boot.target_url == "https://courses.ut.edu.vn/course/view.php?id=12"
    assert len(moodle_boot.cookies) == 1
    assert moodle_boot.cookies[0]["value"] == "moodle_self_managed"

    portal_boot = service.prepare_initial_auth("https://portal.ut.edu.vn/")
    assert portal_boot.site == "portal"
    assert portal_boot.target_url == "https://portal.ut.edu.vn/"
    assert portal_boot.portal_init_script == ""
    assert portal_client.calls == []

    # External URL never calls any client
    ext_boot = service.prepare_initial_auth("https://docs.example.com/a")
    assert ext_boot == WebViewAuthBootstrap(
        target_url="https://docs.example.com/a",
        site="external",
        cookies=(),
        portal_init_script="",
    )


def test_prepare_initial_auth_supports_legacy_doubles_and_handles_exceptions():
    class _LegacyNarrowClient:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        def get_session_cookies(self, *, ensure_login: bool = True) -> dict[str, str]:
            self.calls.append(ensure_login)
            return {"MoodleSession": "legacy_map_val"}

    class _BoomClient:
        def get_session_cookies(self, **_kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError("network offline")

    legacy = _LegacyNarrowClient()
    boom = _BoomClient()
    portal_boom = _FakePortalClient(raise_on_call=RuntimeError("portal down"))
    service = UthWebViewAuthService(
        courses_client=legacy,
        thnn_client=boom,
        portal_client=portal_boom,
        credentials_provider=lambda: ("2251120001", "pw"),
    )

    # Primary succeeds with legacy dict mapping, sibling failure is swallowed gracefully
    boot = service.prepare_initial_auth("https://courses.ut.edu.vn/my/")
    assert legacy.calls == [True]
    assert len(boot.cookies) == 1
    assert boot.cookies[0]["name"] == "MoodleSession"
    assert boot.cookies[0]["value"] == "legacy_map_val"
    assert boot.cookies[0]["domain"] == "courses.ut.edu.vn"

    # Portal exception degrades gracefully without crashing or rewriting root to /dashboard
    p_boot = service.prepare_initial_auth("https://portal.ut.edu.vn/")
    assert p_boot.site == "portal"
    assert p_boot.target_url == "https://portal.ut.edu.vn/"
    assert p_boot.portal_init_script == ""


def test_recover_from_login_redirect_cross_site_thnn_and_anti_loop_budget():
    courses_client = _FakeMoodleClient(
        cookies=[_moodle_cookie("courses_cookie", "courses.ut.edu.vn")],
        cached_cookies=[_moodle_cookie("courses_cached", "courses.ut.edu.vn")],
    )
    thnn_client = _FakeMoodleClient(
        cookies=[_moodle_cookie("thnn_renewed", "thnn.ut.edu.vn")]
    )
    service = UthWebViewAuthService(
        courses_client=courses_client,
        thnn_client=thnn_client,
    )
    tracker = WebViewRecoveryTracker()

    # Pane started on courses, user clicked a THNN course link -> THNN redirected to login
    tracker.record_navigation("https://courses.ut.edu.vn/course/view.php?id=10")
    tracker.record_navigation("https://thnn.ut.edu.vn/course/view.php?id=55")
    assert tracker.intended_url_for("thnn") == "https://thnn.ut.edu.vn/course/view.php?id=55"

    first = service.recover_from_login_redirect(
        "https://thnn.ut.edu.vn/login/index.php", tracker
    )
    assert first is not None
    assert first.site == "thnn"
    assert first.target_url == "https://thnn.ut.edu.vn/course/view.php?id=55"
    assert thnn_client.calls == [
        {"ensure_login": True, "verify_live": False, "force_relogin": True}
    ]
    assert courses_client.calls == [
        {"ensure_login": False, "verify_live": False, "force_relogin": False}
    ]
    by_domain = {c["domain"]: c["value"] for c in first.cookies}
    assert by_domain == {
        "thnn.ut.edu.vn": "thnn_renewed",
        "courses.ut.edu.vn": "courses_cached",
    }

    # Immediate second redirect on THNN -> budget exhausted, returns None with no new login call
    assert tracker.can_attempt_recovery("thnn") is False
    assert tracker.can_attempt_recovery("courses") is True
    second = service.recover_from_login_redirect(
        "https://thnn.ut.edu.vn/login/index.php", tracker
    )
    assert second is None
    assert len(thnn_client.calls) == 1

    # Landing on a login URL must NEVER reset the recovery budget
    tracker.note_authenticated_landing("https://thnn.ut.edu.vn/login/index.php")
    assert tracker.can_attempt_recovery("thnn") is False

    # Once user successfully lands on an authenticated THNN page, budget resets for future expiry
    tracker.note_authenticated_landing("https://thnn.ut.edu.vn/course/view.php?id=55")
    assert tracker.can_attempt_recovery("thnn") is True
    later = service.recover_from_login_redirect(
        "https://thnn.ut.edu.vn/login/index.php", tracker
    )
    assert later is not None
    assert later.target_url == "https://thnn.ut.edu.vn/course/view.php?id=55"
    assert len(thnn_client.calls) == 2


def test_recover_from_login_redirect_respects_quiz_lease_missing_session_and_wantsurl():
    # 1. Active quiz lease -> returns None even if client keeps existing cookie
    quiz_client = _FakeMoodleClient(
        cookies=[_moodle_cookie("active_quiz_cookie", "courses.ut.edu.vn")],
        quiz_active_on_force=True,
    )
    service_quiz = UthWebViewAuthService(courses_client=quiz_client)
    tracker_quiz = WebViewRecoveryTracker()
    assert (
        service_quiz.recover_from_login_redirect(
            "https://courses.ut.edu.vn/login/index.php", tracker_quiz
        )
        is None
    )

    # 2. Returned cookies with no MoodleSession (e.g. only MOODLEID1_) -> returns None
    no_session_client = _FakeMoodleClient(
        cookies=[_moodle_cookie("user123", "courses.ut.edu.vn", name="MOODLEID1_")]
    )
    service_no_sess = UthWebViewAuthService(courses_client=no_session_client)
    tracker_no_sess = WebViewRecoveryTracker()
    assert (
        service_no_sess.recover_from_login_redirect(
            "https://courses.ut.edu.vn/login/index.php", tracker_no_sess
        )
        is None
    )

    # 3. Valid same-site wantsurl on login redirect overrides older tracker URL;
    #    off-origin wantsurl falls back to tracker.intended_url_for
    courses_client = _FakeMoodleClient(
        cookies=[_moodle_cookie("renewed", "courses.ut.edu.vn")]
    )
    service = UthWebViewAuthService(courses_client=courses_client)
    tracker = WebViewRecoveryTracker()
    tracker.record_navigation("https://courses.ut.edu.vn/course/view.php?id=10")

    recovered = service.recover_from_login_redirect(
        "https://courses.ut.edu.vn/login/index.php"
        "?wantsurl=https%3A%2F%2Felearning.ut.edu.vn%2Fmod%2Fquiz%2Fview.php%3Fid%3D77",
        tracker,
    )
    assert recovered is not None
    assert recovered.target_url == "https://courses.ut.edu.vn/mod/quiz/view.php?id=77"

    tracker2 = WebViewRecoveryTracker()
    tracker2.record_navigation("https://courses.ut.edu.vn/course/view.php?id=10")
    recovered_bad_wants = service.recover_from_login_redirect(
        "https://courses.ut.edu.vn/login/index.php"
        "?wantsurl=https%3A%2F%2Fevil.example%2Fsteal",
        tracker2,
    )
    assert recovered_bad_wants is not None
    assert recovered_bad_wants.target_url == "https://courses.ut.edu.vn/course/view.php?id=10"

    # Non-login URL returns None without consuming budget
    tracker3 = WebViewRecoveryTracker()
    assert (
        service.recover_from_login_redirect(
            "https://courses.ut.edu.vn/course/view.php?id=10", tracker3
        )
        is None
    )
    assert tracker3.can_attempt_recovery("courses") is True


def test_recover_from_login_redirect_for_portal_and_failure_paths():
    portal_client = _FakePortalClient(token="fresh.portal.jwt", role="sv")
    service = UthWebViewAuthService(
        portal_client=portal_client,
        credentials_provider=lambda: ("2251120001", "secret"),
    )
    tracker = WebViewRecoveryTracker()
    tracker.record_navigation("https://portal.ut.edu.vn/tuition")

    recovered = service.recover_from_login_redirect("https://portal.ut.edu.vn/", tracker)
    assert recovered is not None
    assert recovered.site == "portal"
    assert recovered.target_url == "https://portal.ut.edu.vn/tuition"
    assert recovered.cookies == ()
    assert portal_client.calls == [("2251120001", "secret", True)]
    assert json.dumps("fresh.portal.jwt") in recovered.portal_init_script
    assert "secret" not in recovered.portal_init_script

    # Second immediate redirect hits budget cap
    assert service.recover_from_login_redirect("https://portal.ut.edu.vn/login", tracker) is None
    assert len(portal_client.calls) == 1

    # Blank token or exception returns None
    blank_portal = UthWebViewAuthService(
        portal_client=_FakePortalClient(token="   ", role="sv"),
        credentials_provider=lambda: ("2251120001", "secret"),
    )
    assert (
        blank_portal.recover_from_login_redirect(
            "https://portal.ut.edu.vn/", WebViewRecoveryTracker()
        )
        is None
    )

    err_portal = UthWebViewAuthService(
        portal_client=_FakePortalClient(raise_on_call=RuntimeError("401")),
        credentials_provider=lambda: ("2251120001", "secret"),
    )
    assert (
        err_portal.recover_from_login_redirect(
            "https://portal.ut.edu.vn/", WebViewRecoveryTracker()
        )
        is None
    )


def test_recovery_tracker_defaults_canonicalization_and_thread_safety():
    tracker = WebViewRecoveryTracker()
    assert tracker.intended_url_for("courses") == "https://courses.ut.edu.vn/my/"
    assert tracker.intended_url_for("thnn") == "https://thnn.ut.edu.vn/my/"
    assert tracker.intended_url_for("portal") == "https://portal.ut.edu.vn/dashboard"
    assert tracker.intended_url_for("external") == ""
    assert tracker.can_attempt_recovery("external") is False
    assert tracker.consume_recovery_attempt("external") is False

    # Alias canonicalizes on record_navigation; login & external URLs are ignored
    tracker.record_navigation("https://elearning.ut.edu.vn/course/view.php?id=33")
    tracker.record_navigation("https://courses.ut.edu.vn/login/index.php")
    tracker.record_navigation("https://docs.example.com/abc")
    assert tracker.intended_url_for("courses") == "https://courses.ut.edu.vn/course/view.php?id=33"

    # Concurrent recovery consumption allows exactly 1 winner
    barrier = threading.Barrier(16)
    results: list[bool] = []
    results_lock = threading.Lock()

    def _worker() -> None:
        barrier.wait()
        won = tracker.consume_recovery_attempt("thnn")
        with results_lock:
            results.append(won)

    threads = [threading.Thread(target=_worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in results if r) == 1
    assert len(results) == 16


def test_bootstrap_is_frozen_and_blank_or_failing_credentials_are_safe():
    boot = WebViewAuthBootstrap(
        target_url="https://courses.ut.edu.vn/my/",
        site="courses",
        cookies=(),
    )
    with pytest.raises(FrozenInstanceError):
        boot.target_url = "https://evil.example/"  # type: ignore[misc]

    courses_client = _FakeMoodleClient(
        cookies=[_moodle_cookie("should_not_be_called", "courses.ut.edu.vn")]
    )
    thnn_client = _FakeMoodleClient(
        cookies=[_moodle_cookie("thnn_live", "thnn.ut.edu.vn")]
    )
    service_blank = UthWebViewAuthService(
        courses_client=courses_client,
        thnn_client=thnn_client,
        credentials_provider=lambda: ("", ""),
    )

    # Explicit blank credentials skip Moodle network login and keep bare login URL
    boot_blank = service_blank.prepare_initial_auth(
        "https://courses.ut.edu.vn/login/index.php"
    )
    assert boot_blank.target_url == "https://courses.ut.edu.vn/login/index.php"
    assert boot_blank.cookies == ()
    assert courses_client.calls == []
    assert thnn_client.calls == []

    # Recovery with blank credentials does not call client and does not burn budget
    tracker = WebViewRecoveryTracker()
    assert (
        service_blank.recover_from_login_redirect(
            "https://courses.ut.edu.vn/login/index.php", tracker
        )
        is None
    )
    assert tracker.can_attempt_recovery("courses") is True
    assert courses_client.calls == []

    # Throwing credentials provider is swallowed safely
    def _boom_creds() -> tuple[str, str]:
        raise RuntimeError("keyring locked")

    service_err = UthWebViewAuthService(
        courses_client=courses_client,
        portal_client=_FakePortalClient(),
        credentials_provider=_boom_creds,
    )
    assert service_err.prepare_initial_auth("https://portal.ut.edu.vn/").portal_init_script == ""
    assert service_err.prepare_initial_auth("https://courses.ut.edu.vn/my/").cookies == ()


def test_prepare_initial_auth_thnn_primary_warms_cached_courses():
    courses_client = _FakeMoodleClient(
        cookies=[_moodle_cookie("courses_network", "courses.ut.edu.vn")],
        cached_cookies=[_moodle_cookie("courses_cached", "courses.ut.edu.vn")],
    )
    thnn_client = _FakeMoodleClient(
        cookies=[_moodle_cookie("thnn_live", "thnn.ut.edu.vn")]
    )
    service = UthWebViewAuthService(
        courses_client=courses_client,
        thnn_client=thnn_client,
    )

    boot = service.prepare_initial_auth("https://thnn.ut.edu.vn/course/view.php?id=55")
    assert boot.site == "thnn"
    assert boot.target_url == "https://thnn.ut.edu.vn/course/view.php?id=55"
    assert thnn_client.calls == [
        {"ensure_login": True, "verify_live": True, "force_relogin": False}
    ]
    assert courses_client.calls == [
        {"ensure_login": False, "verify_live": False, "force_relogin": False}
    ]
    by_domain = {c["domain"]: c["value"] for c in boot.cookies}
    assert by_domain == {
        "thnn.ut.edu.vn": "thnn_live",
        "courses.ut.edu.vn": "courses_cached",
    }


def _make_jwt(exp: int, role: str = "sv") -> str:
    def _b64(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64({'exp': exp, 'data': {'role': role}})}.sig"


def test_integration_with_real_moodle_client_quiz_lease_and_real_portal_client(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "test-pass")

    courses_client = MoodleClient("https://courses.ut.edu.vn")
    courses_client._sesskey = "sk_active"
    courses_client._session_owner = "2251120001"
    courses_client._session_authenticated_at = time.monotonic() - 3600.0
    courses_client._cookie_jar.set_cookie(
        Cookie(
            version=0,
            name="MoodleSession",
            value="quiz_session_cookie",
            port=None,
            port_specified=False,
            domain="courses.ut.edu.vn",
            domain_specified=False,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": None, "SameSite": "Lax"},
            rfc2109=False,
        )
    )
    # Simulate expired probe so MoodleClient tries to relogin and hits the active quiz lease
    monkeypatch.setattr(
        courses_client,
        "call_ajax_api",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("ajax unavailable")),
    )
    monkeypatch.setattr(
        courses_client,
        "_open_html",
        lambda *_a, **_kw: ("https://courses.ut.edu.vn/login/index.php", "<html>login</html>"),
    )

    portal_client = PortalClient("https://portal.ut.edu.vn/api/v1")
    future_jwt = _make_jwt(int(time.time()) + 7200, role="sv")
    portal_client._store_token(future_jwt, "2251120001", role="sv")

    service = UthWebViewAuthService(
        courses_client=courses_client,
        portal_client=portal_client,
        credentials_provider=lambda: ("2251120001", "test-pass"),
    )

    # Real PortalClient serves unexpired cached JWT + role without HTTP calls
    portal_boot = service.prepare_initial_auth("https://portal.ut.edu.vn/")
    assert portal_boot.target_url == "https://portal.ut.edu.vn/dashboard"
    assert json.dumps(future_jwt) in portal_boot.portal_init_script
    assert "test-pass" not in portal_boot.portal_init_script

    # Real MoodleClient under an active QuizSessionLease:
    # - prepare_initial_auth preserves the existing MoodleSession cookie (never wipes jar)
    # - recover_from_login_redirect returns None (never clobbers active quiz)
    lease_key = quiz_session_key(COURSES_MOODLE_SITE, "2251120001")
    with acquire_quiz_session_lease(lease_key):
        initial = service.prepare_initial_auth(
            "https://courses.ut.edu.vn/course/view.php?id=99"
        )
        assert len(initial.cookies) == 1
        assert initial.cookies[0]["name"] == "MoodleSession"
        assert initial.cookies[0]["value"] == "quiz_session_cookie"
        assert initial.cookies[0]["httpOnly"] is True

        tracker = WebViewRecoveryTracker()
        tracker.record_navigation("https://courses.ut.edu.vn/course/view.php?id=99")
        recovered = service.recover_from_login_redirect(
            "https://courses.ut.edu.vn/login/index.php", tracker
        )
        assert recovered is None
        assert courses_client._last_login_error == "quiz_session_active"

