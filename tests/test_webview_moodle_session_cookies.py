import time
from http.cookiejar import Cookie

import pytest

from config import settings
from core.client import MoodleClient
from core.moodle_sites import COURSES_MOODLE_SITE
from core.quiz_player import acquire_quiz_session_lease, quiz_session_key
from core.quiz_web_session import warm_existing_client_from_quiz_login


def _make_cookie(
    name: str,
    value: str,
    domain: str = "courses.ut.edu.vn",
    *,
    domain_specified: bool = False,
    http_only: bool = True,
    same_site: str | None = "Lax",
    rest_override: dict[str, str | None] | None = None,
) -> Cookie:
    rest: dict[str, str | None] = {}
    if rest_override is not None:
        rest = dict(rest_override)
    else:
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
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest=rest,
        rfc2109=False,
    )


def test_get_session_cookies_preserves_httponly_none_flag_and_host_only_domain():
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sk1"
    client._session_owner = settings.UTH_USERNAME
    client._session_authenticated_at = time.monotonic()
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "fresh123", http_only=True))

    exported = client.get_session_cookies(ensure_login=False)
    assert len(exported) == 1
    assert exported[0]["name"] == "MoodleSession"
    assert exported[0]["value"] == "fresh123"
    assert exported[0]["httpOnly"] is True
    assert exported[0]["domain"] == "courses.ut.edu.vn"
    assert exported[0]["domain_specified"] is False
    assert exported[0]["sameSite"] == "Lax"


def test_get_session_cookies_verify_live_skips_network_when_fresh(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret")
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sk_fresh"
    client._session_owner = "2251120001"
    client._session_authenticated_at = time.monotonic()
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "alive_cookie"))

    ajax_calls = []
    monkeypatch.setattr(client, "call_ajax_api", lambda *a, **k: ajax_calls.append(a))
    cookies = client.get_session_cookies(ensure_login=True, verify_live=True)

    assert [c["value"] for c in cookies] == ["alive_cookie"]
    assert ajax_calls == []


def test_get_session_cookies_verify_live_relogins_when_stale_session_expired(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret")
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sk_old"
    client._session_owner = "2251120001"
    client._session_authenticated_at = time.monotonic() - 3600.0
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "dead_cookie"))

    monkeypatch.setattr(
        client,
        "call_ajax_api",
        lambda fn, **_kw: {"error": True, "errorcode": "requireloginerror", "message": "Session timed out"},
    )

    def _fake_login(username=None, password=None, force=False, **_kw):
        assert force is True
        client._cookie_jar.clear()
        client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "renewed_cookie"))
        client._sesskey = "sk_new"
        client._session_owner = "2251120001"
        client._session_authenticated_at = time.monotonic()
        return True

    monkeypatch.setattr(client, "login_session", _fake_login)
    cookies = client.get_session_cookies(ensure_login=True, verify_live=True)

    assert [c["value"] for c in cookies] == ["renewed_cookie"]


def test_get_session_cookies_verify_live_falls_back_to_html_probe_and_preserves_quiz_lease(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret")
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sk_quiz"
    client._session_owner = "2251120001"
    client._session_authenticated_at = 0.0
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "quiz_active_cookie"))

    skey = quiz_session_key(COURSES_MOODLE_SITE, "2251120001")
    lease = acquire_quiz_session_lease(skey)
    try:
        monkeypatch.setattr(client, "call_ajax_api", lambda *_a, **_kw: None)
        monkeypatch.setattr(
            client,
            "_open_html",
            lambda _path, **_kw: ("https://courses.ut.edu.vn/login/index.php", "<form/>"),
        )
        cookies = client.get_session_cookies(ensure_login=True, verify_live=True)
        # Active quiz lease prevents destructive cookie wipe; existing cookie remains intact
        assert [c["value"] for c in cookies] == ["quiz_active_cookie"]
    finally:
        lease.release()


def test_ensure_live_session_updates_timestamp_when_ajax_reports_healthy_session(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sk_stale"
    client._session_owner = "2251120001"
    stale_ts = time.monotonic() - 2000.0
    client._session_authenticated_at = stale_ts
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "alive_cookie"))

    monkeypatch.setattr(
        client,
        "call_ajax_api",
        lambda fn, **_kw: {"error": False, "data": {"userid": 99, "timeremaining": 1200}},
    )
    relogin_calls = []
    monkeypatch.setattr(client, "login_session", lambda **kw: relogin_calls.append(kw) or True)

    assert client.ensure_live_session() is True
    assert client._session_authenticated_at > stale_ts
    assert relogin_calls == []


@pytest.mark.parametrize(
    "ajax_data",
    [
        {"userid": 0, "timeremaining": 0},
        {"userid": 99, "timeremaining": 45},
    ],
)
def test_ensure_live_session_relogins_when_ajax_reports_guest_or_expiring(monkeypatch, ajax_data):
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sk_old"
    client._session_owner = "2251120001"
    client._session_authenticated_at = time.monotonic() - 2000.0
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "old_cookie"))

    monkeypatch.setattr(
        client,
        "call_ajax_api",
        lambda fn, **_kw: {"error": False, "data": ajax_data},
    )
    forced = []
    monkeypatch.setattr(
        client,
        "login_session",
        lambda *, force=False, **_kw: forced.append(force) or True,
    )

    assert client.ensure_live_session() is True
    assert forced == [True]


def test_ensure_live_session_html_fallback_alive_and_network_error(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sk_ok"
    client._session_owner = "2251120001"
    client._session_authenticated_at = 0.0
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "cookie_val"))

    monkeypatch.setattr(client, "call_ajax_api", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        client,
        "_open_html",
        lambda _path, **_kw: ("https://courses.ut.edu.vn/my/", "<html>Dashboard</html>"),
    )

    assert client.ensure_live_session() is True
    assert client._session_authenticated_at > 0.0

    # Reset timestamp to test network failure fallback preserving existing cookie
    client._session_authenticated_at = 0.0

    def _boom(_path, **_kw):
        raise OSError("network offline")

    monkeypatch.setattr(client, "_open_html", _boom)
    assert client.ensure_live_session() is True


def test_get_session_cookies_force_relogin_behavior(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret")
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sk_quiz"
    client._session_owner = "2251120001"
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "existing_cookie"))

    # Under active quiz lease, force_relogin preserves the existing cookie
    skey = quiz_session_key(COURSES_MOODLE_SITE, "2251120001")
    lease = acquire_quiz_session_lease(skey)
    try:
        cookies = client.get_session_cookies(force_relogin=True)
        assert client._last_login_error == "quiz_session_active"
        assert [c["value"] for c in cookies] == ["existing_cookie"]
    finally:
        lease.release()

    # Without a quiz lease, failed force_relogin returns []
    def _fail_login(*_a, force=False, **_kw):
        assert force is True
        client._last_login_error = "invalid_credentials"
        return False

    monkeypatch.setattr(client, "login_session", _fail_login)
    assert client.get_session_cookies(force_relogin=True) == []


def test_get_session_cookies_case_insensitive_rest_and_domain_rules():
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sk1"
    client._session_owner = settings.UTH_USERNAME
    client._cookie_jar.set_cookie(
        _make_cookie(
            "MoodleSession",
            "val1",
            domain=".courses.ut.edu.vn",
            domain_specified=False,
            rest_override={"httponly": None, "samesite": "Strict"},
        )
    )
    client._cookie_jar.set_cookie(
        _make_cookie(
            "SharedCookie",
            "val2",
            domain=".ut.edu.vn",
            domain_specified=True,
            rest_override={},
        )
    )
    client._cookie_jar.set_cookie(
        _make_cookie(
            "NoDomainCookie",
            "val3",
            domain="",
            domain_specified=False,
            rest_override={},
        )
    )

    exported = {c["name"]: c for c in client.get_session_cookies(ensure_login=False)}
    assert exported["MoodleSession"]["httpOnly"] is True
    assert exported["MoodleSession"]["sameSite"] == "Strict"
    assert exported["MoodleSession"]["domain"] == "courses.ut.edu.vn"
    assert exported["MoodleSession"]["domain_specified"] is False

    assert exported["SharedCookie"]["httpOnly"] is False
    assert exported["SharedCookie"]["sameSite"] is None
    assert exported["SharedCookie"]["domain"] == ".ut.edu.vn"
    assert exported["SharedCookie"]["domain_specified"] is True

    assert exported["NoDomainCookie"]["domain"] == "courses.ut.edu.vn"
    assert exported["NoDomainCookie"]["domain_specified"] is False


def test_session_authenticated_at_lifecycle_and_quiz_warm(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    client = MoodleClient("https://courses.ut.edu.vn")
    assert client._session_authenticated_at == 0.0

    # warm_existing_client_from_quiz_login sets _session_authenticated_at
    quiz_client = MoodleClient("https://courses.ut.edu.vn")
    quiz_client._sesskey = "quiz_sk"
    quiz_client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "quiz_val"))
    warm_existing_client_from_quiz_login(
        client,
        quiz_client,
        COURSES_MOODLE_SITE,
        "2251120001",
        '"sesskey":"quiz_sk"',
    )
    assert client._session_authenticated_at > 0.0

    # Account change resets _session_authenticated_at
    monkeypatch.setattr(settings, "UTH_USERNAME", "other_user")
    client._reset_session_if_account_changed()
    assert client._session_authenticated_at == 0.0

    # clear_session resets _session_authenticated_at
    client._session_authenticated_at = time.monotonic()
    client.clear_session()
    assert client._session_authenticated_at == 0.0

    # open_session_url updates _session_authenticated_at on non-login page
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    client._sesskey = "sk_live"
    client._session_owner = "2251120001"
    monkeypatch.setattr(
        client,
        "_open_html",
        lambda _path, **_kw: ("https://courses.ut.edu.vn/course/view.php?id=1", "<html/>"),
    )
    client.open_session_url("/course/view.php?id=1")
    assert client._session_authenticated_at > 0.0

    # submit_session_form updates _session_authenticated_at on non-login response
    client._session_authenticated_at = 0.0
    client.submit_session_form("/course/view.php?id=1", {"sesskey": "sk_live"})
    assert client._session_authenticated_at > 0.0

    # fetch_grade_report_html updates _session_authenticated_at on non-login response
    client._session_authenticated_at = 0.0
    client.fetch_grade_report_html(1)
    assert client._session_authenticated_at > 0.0
