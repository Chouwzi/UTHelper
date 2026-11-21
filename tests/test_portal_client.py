from __future__ import annotations

import gzip
import json
from datetime import date
from email.message import Message
from urllib.error import HTTPError

from core.portal_client import PortalAuthenticationError, PortalClient


class _Response:
    def __init__(self, payload: dict, status: int = 200, *, compressed: bool = False):
        raw = json.dumps(payload).encode("utf-8")
        self._body = gzip.compress(raw) if compressed else raw
        self.status = status
        self.headers = Message()
        if compressed:
            self.headers["Content-Encoding"] = "gzip"

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_portal_client_logs_in_then_fetches_daily_schedule_with_bearer_only():
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        if request.full_url.endswith("/user/login"):
            return _Response({"success": True, "token": "jwt-token"}, compressed=True)
        return _Response(
            {
                "success": True,
                "body": [
                    {
                        "id": 1,
                        "tenMonHoc": "Cơ sở dữ liệu",
                        "tuGio": "07:00",
                        "denGio": "09:30",
                    }
                ],
            }
        )

    client = PortalClient(
        "https://portal-publicapp.ut.edu.vn/api/v1",
        opener=opener,
        timeout=9,
    )

    sessions = client.fetch_day(date(2026, 8, 11), "student", "secret")

    assert len(sessions) == 1
    login_request, login_timeout = requests[0]
    assert login_request.method == "POST"
    assert login_timeout == 9
    assert json.loads(login_request.data) == {
        "username": "student",
        "password": "secret",
    }
    schedule_request, schedule_timeout = requests[1]
    assert schedule_timeout == 9
    assert schedule_request.full_url.endswith("/lichhoc/ngay?date=2026-08-11")
    assert schedule_request.get_header("Authorization") == "Bearer jwt-token"
    assert schedule_request.get_header("Cookie") is None
    assert schedule_request.get_header("X-browser-token") is None


def test_portal_client_reauthenticates_once_after_unauthorized_schedule():
    paths = []
    login_count = 0

    def opener(request, timeout):
        del timeout
        nonlocal login_count
        paths.append(request.full_url)
        if request.full_url.endswith("/user/login"):
            login_count += 1
            return _Response({"success": True, "token": f"token-{login_count}"})
        if login_count == 1:
            raise HTTPError(request.full_url, 401, "unauthorized", {}, None)
        return _Response({"success": True, "body": []})

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)

    assert client.fetch_day(date(2026, 8, 11), "student", "secret") == ()
    assert login_count == 2
    assert sum(path.endswith("/user/login") for path in paths) == 2


def test_portal_client_rejects_login_without_token():
    client = PortalClient(
        "https://portal-publicapp.ut.edu.vn/api/v1",
        opener=lambda *_args, **_kwargs: _Response(
            {"success": False, "message": "invalid"}
        ),
    )

    try:
        client.fetch_day(date(2026, 8, 11), "student", "secret")
    except PortalAuthenticationError:
        pass
    else:
        raise AssertionError("missing Portal token was accepted")


def test_portal_client_caches_the_token_per_account_and_clear_token_drops_it():
    def opener(request, timeout):
        del timeout
        if request.full_url.endswith("/user/login"):
            return _Response({"success": True, "token": "jwt-token"})
        return _Response({"success": True, "body": []})

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)

    assert client._cached_token("student") == ""
    assert client.fetch_day(date(2026, 8, 11), "student", "secret") == ()

    # Same account replays the cached token...
    assert client._cached_token("student") == "jwt-token"
    # ...a different account never does.
    assert client._cached_token("other-student") == ""

    client.clear_token()
    assert client._cached_token("student") == ""


def test_portal_client_fetch_started_before_clear_token_never_restores_it():
    """A login finishing after clear_token() must not resurrect the session."""

    def opener(request, timeout):
        del timeout
        if request.full_url.endswith("/user/login"):
            # The account is switched/logged out while this login is on the wire.
            client.clear_token()
            return _Response({"success": True, "token": "late-token"})
        return _Response({"success": True, "body": []})

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)

    assert client.fetch_day(date(2026, 8, 11), "student", "secret") == ()
    assert client._cached_token("student") == ""


def test_portal_client_store_token_after_a_newer_clear_is_accepted():
    client = PortalClient(
        "https://portal-publicapp.ut.edu.vn/api/v1",
        opener=lambda *_args, **_kwargs: _Response({"success": True, "body": []}),
    )

    client.clear_token()
    client._store_token("fresh-token", "student", client._current_generation())

    assert client._cached_token("student") == "fresh-token"


def test_portal_client_rejected_stale_login_keeps_the_newer_token():
    client = PortalClient(
        "https://portal-publicapp.ut.edu.vn/api/v1",
        opener=lambda *_args, **_kwargs: _Response(
            {"success": False, "message": "invalid"}
        ),
    )
    stale_generation = client._current_generation()
    client.clear_token()
    client._store_token("fresh-token", "student", client._current_generation())

    try:
        client._login("student", "wrong", stale_generation)
    except PortalAuthenticationError:
        pass
    else:
        raise AssertionError("rejected login was accepted")

    assert client._cached_token("student") == "fresh-token"


def test_portal_client_rejects_non_https_and_invalid_base_urls():
    import pytest

    for bad_url in (
        "",
        "http://portal-publicapp.ut.edu.vn/api/v1",
        "https://user:pass@portal-publicapp.ut.edu.vn/api/v1",
        "https://portal-publicapp.ut.edu.vn/api/v1?foo=bar",
        "https://portal-publicapp.ut.edu.vn/api/v1#frag",
    ):
        with pytest.raises(ValueError):
            PortalClient(bad_url)


def test_portal_client_redirect_handler_blocks_cross_origin_and_http_downgrade():
    import urllib.request
    from email.message import Message
    from core.portal_client import _SamePortalOriginRedirectHandler

    handler = _SamePortalOriginRedirectHandler("https://portal-publicapp.ut.edu.vn")
    original_req = urllib.request.Request(
        "https://portal-publicapp.ut.edu.vn/api/v1/lichhoc/ngay",
        headers={"Authorization": "Bearer jwt-token"},
        method="GET",
    )

    for bad_target in (
        "https://evil.example/steal",
        "http://portal-publicapp.ut.edu.vn/api/v1/lichhoc/ngay",
        "https://user:pass@portal-publicapp.ut.edu.vn/api/v1/lichhoc/ngay",
    ):
        try:
            handler.redirect_request(
                original_req,
                None,
                302,
                "Found",
                Message(),
                bad_target,
            )
        except HTTPError as exc:
            assert exc.code == 302
            assert "Untrusted Portal redirect" in exc.msg
        else:
            raise AssertionError(f"Expected redirect rejection for {bad_target}")

    same_origin_req = handler.redirect_request(
        original_req,
        None,
        302,
        "Found",
        Message(),
        "https://portal-publicapp.ut.edu.vn/api/v2/lichhoc/ngay",
    )
    assert same_origin_req is not None
    assert (
        same_origin_req.full_url
        == "https://portal-publicapp.ut.edu.vn/api/v2/lichhoc/ngay"
    )


def test_portal_client_rejects_mismatched_response_geturl_and_oversized_payload():
    import pytest
    from core.portal_client import PortalProtocolError, _MAX_PORTAL_RESPONSE_BYTES

    class _RedirectedResponse(_Response):
        def geturl(self):
            return "https://evil.example/hijacked"

    client_redirect = PortalClient(
        "https://portal-publicapp.ut.edu.vn/api/v1",
        opener=lambda *_args, **_kwargs: _RedirectedResponse(
            {"success": True, "token": "jwt"}
        ),
    )
    with pytest.raises(PortalProtocolError, match="trusted origin"):
        client_redirect.fetch_day(date(2026, 8, 11), "student", "secret")

    class _HugeResponse:
        def __init__(self):
            self.headers = Message()

        def geturl(self):
            return "https://portal-publicapp.ut.edu.vn/api/v1/user/login"

        def read(self, amt=-1):
            del amt
            return b"x" * (_MAX_PORTAL_RESPONSE_BYTES + 1)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    client_huge = PortalClient(
        "https://portal-publicapp.ut.edu.vn/api/v1",
        opener=lambda *_args, **_kwargs: _HugeResponse(),
    )
    with pytest.raises(PortalProtocolError, match="maximum size"):
        client_huge.fetch_day(date(2026, 8, 11), "student", "secret")

