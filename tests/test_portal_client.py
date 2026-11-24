from __future__ import annotations

import base64
import gzip
import json
import time
from datetime import date
from email.message import Message
from urllib.error import HTTPError

import pytest

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


def test_portal_client_fetches_current_semester_courses_with_fallback_and_401_retry():
    paths: list[str] = []
    login_count = 0

    def opener(request, timeout):
        del timeout
        nonlocal login_count
        paths.append(request.full_url)
        if request.full_url.endswith("/user/login"):
            login_count += 1
            return _Response({"success": True, "token": f"tok-{login_count}"})
        if request.full_url.endswith("/hoctap/hocky"):
            if login_count == 1:
                raise HTTPError(request.full_url, 401, "unauthorized", {}, None)
            return _Response(
                {
                    "success": True,
                    "body": [
                        {"id": 77, "tenDot": "Học kỳ 1 năm học 2026-2027"},
                        {"id": 78, "tenDot": "Học kỳ 2 năm học 2026-2027"},
                    ],
                }
            )
        if request.full_url.endswith("/hoctap/montheoky/78"):
            # Newest dot has no registered courses yet -> must fall back to id=77
            return _Response({"success": True, "body": []})
        if request.full_url.endswith("/hoctap/montheoky/77"):
            return _Response(
                {
                    "success": True,
                    "body": [
                        {
                            "id": 180519,
                            "maHocPhan": "0120121137",
                            "tenMonHoc": "Quản trị doanh nghiệp CNTT",
                            "soTinChi": 3,
                        },
                        {
                            "id": 180520,
                            "maHocPhan": "0120005107",
                            "tenMonHoc": "Chủ nghĩa xã hội khoa học",
                            "soTinChi": 2,
                        },
                    ],
                }
            )
        raise AssertionError(f"Unexpected URL: {request.full_url}")

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)
    ten_dot, courses = client.fetch_current_semester_courses("student", "secret")

    assert login_count == 2
    assert ten_dot == "Học kỳ 1 năm học 2026-2027"
    assert courses == [
        {
            "maHocPhan": "0120121137",
            "tenMonHoc": "Quản trị doanh nghiệp CNTT",
            "soTinChi": 3,
        },
        {
            "maHocPhan": "0120005107",
            "tenMonHoc": "Chủ nghĩa xã hội khoa học",
            "soTinChi": 2,
        },
    ]
    assert any(p.endswith("/hoctap/montheoky/78") for p in paths)
    assert any(p.endswith("/hoctap/montheoky/77") for p in paths)

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


def test_portal_client_fetch_week_and_credit_progress_isolate_tokens_per_user():
    tokens_by_user = {"student1": "tok-1", "student2": "tok-2"}
    auth_headers = []

    def opener(request, timeout):
        del timeout
        if request.full_url.endswith("/user/login"):
            body = json.loads(request.data)
            return _Response({"success": True, "token": tokens_by_user[body["username"]]})
        auth_headers.append((request.full_url, request.get_header("Authorization")))
        if "hoctap/tiendo" in request.full_url:
            return _Response({"success": True, "body": {"soTinChiDat": 90}})
        return _Response({"success": True, "body": []})

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)

    client.fetch_week(date(2026, 8, 11), "student1", "secret1")
    client.fetch_credit_progress("student1", "secret1")  # Reuses student1 token
    client.fetch_week(date(2026, 8, 11), "student2", "secret2")  # Must re-login for student2

    assert len(auth_headers) == 3
    assert auth_headers[0][1] == "Bearer tok-1"
    assert auth_headers[1][1] == "Bearer tok-1"
    assert auth_headers[2][1] == "Bearer tok-2"


def test_portal_client_fetch_week_and_progress_reauthenticate_once_on_401():
    login_count = 0

    def opener(request, timeout):
        del timeout
        nonlocal login_count
        if request.full_url.endswith("/user/login"):
            login_count += 1
            return _Response({"success": True, "token": f"tok-{login_count}"})
        if request.get_header("Authorization") == "Bearer tok-1":
            raise HTTPError(request.full_url, 401, "unauthorized", {}, None)
        if "hoctap/tiendo" in request.full_url:
            return _Response({"success": True, "body": {"soTinChiDat": 100}})
        return _Response({"success": True, "body": []})

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)

    assert client.fetch_week(date(2026, 8, 11), "student", "secret") == {}
    assert login_count == 2
    assert client.fetch_credit_progress("student", "secret") == {"soTinChiDat": 100}
    assert login_count == 2  # Still valid cached tok-2


def test_portal_client_clear_token_during_login_prevents_stale_token_resurrection():
    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1")

    def opener(request, timeout):
        del timeout
        if request.full_url.endswith("/user/login"):
            # Simulate account switch / reset mid-flight while login is running
            client.clear_token()
            return _Response({"success": True, "token": "stale-token"})
        return _Response({"success": True, "body": {"ok": True}})

    client._opener = opener
    progress = client.fetch_credit_progress("student", "secret")
    assert progress == {"ok": True}
    # Because generation incremented mid-login, stale-token must NOT be cached
    assert client._cached_token("student") == ""


def test_portal_client_rejects_gzip_bomb_incrementally_without_expanding_full_payload():
    import tracemalloc
    import pytest
    from core.portal_client import PortalProtocolError, _MAX_PORTAL_RESPONSE_BYTES

    bomb_payload = gzip.compress(b" " * (32 * 1024 * 1024), compresslevel=9)
    assert len(bomb_payload) < _MAX_PORTAL_RESPONSE_BYTES

    class _BombResponse:
        def __init__(self):
            self.headers = Message()
            self.headers["Content-Encoding"] = "gzip"

        def geturl(self):
            return "https://portal-publicapp.ut.edu.vn/api/v1/lichhoc/lichTuan"

        def read(self, amt=-1):
            del amt
            return bomb_payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def opener(request, timeout):
        del timeout
        if request.full_url.endswith("/user/login"):
            return _Response({"success": True, "token": "tok"})
        return _BombResponse()

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)

    tracemalloc.start()
    try:
        with pytest.raises(PortalProtocolError, match="maximum size"):
            client.fetch_week(date(2026, 8, 11), "student", "secret")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    # Bounded incremental decompression must abort around 8 MiB, nowhere near 32 MiB
    assert peak < 16 * 1024 * 1024


def test_portal_client_accepts_case_insensitive_gzip_content_encoding():
    class _UpperGzipResponse(_Response):
        def __init__(self, payload: dict):
            super().__init__(payload, compressed=True)
            del self.headers["Content-Encoding"]
            self.headers["Content-Encoding"] = "GZIP"

    def opener(request, timeout):
        del timeout
        if request.full_url.endswith("/user/login"):
            return _UpperGzipResponse({"success": True, "token": "tok"})
        return _UpperGzipResponse({"success": True, "body": {"canDat": 120, "hienTai": 90}})

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)
    assert client.fetch_credit_progress("student", "secret") == {"canDat": 120, "hienTai": 90}


def test_decompress_gzip_bounded_handles_multi_member_over_256kib():
    import time
    from core.portal_client import _MAX_PORTAL_RESPONSE_BYTES, _decompress_gzip_bounded

    member_a = b"A" * (300 * 1024)
    member_b = b"B" * 10
    payload = gzip.compress(member_a) + gzip.compress(member_b)

    t0 = time.perf_counter()
    result = _decompress_gzip_bounded(payload, _MAX_PORTAL_RESPONSE_BYTES)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.5
    assert result == member_a + member_b


def test_decompress_gzip_bounded_rejects_multi_member_gzip_bomb_without_oom():
    import time
    import tracemalloc
    import pytest
    from core.portal_client import (
        PortalProtocolError,
        _MAX_PORTAL_RESPONSE_BYTES,
        _decompress_gzip_bounded,
    )

    member_5m = gzip.compress(b"X" * (5 * 1024 * 1024), compresslevel=9)
    bomb = member_5m + member_5m

    tracemalloc.start()
    t0 = time.perf_counter()
    try:
        with pytest.raises(PortalProtocolError, match="maximum size"):
            _decompress_gzip_bounded(bomb, _MAX_PORTAL_RESPONSE_BYTES)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert (time.perf_counter() - t0) < 1.0
    assert peak < 24 * 1024 * 1024


def test_decompress_gzip_bounded_rejects_truncated_and_trailing_garbage_streams():
    import pytest
    from core.portal_client import (
        PortalProtocolError,
        _MAX_PORTAL_RESPONSE_BYTES,
        _decompress_gzip_bounded,
    )

    ok_gz = gzip.compress(b'{"success": true, "ok": 1}')

    for bad_stream in (ok_gz[:-8], ok_gz[:-1], ok_gz + b"garbage"):
        with pytest.raises(PortalProtocolError, match="not valid gzip"):
            _decompress_gzip_bounded(bad_stream, _MAX_PORTAL_RESPONSE_BYTES)

    class _TruncatedResponse:
        def __init__(self):
            self.headers = Message()
            self.headers["Content-Encoding"] = "gzip"

        def geturl(self):
            return "https://portal-publicapp.ut.edu.vn/api/v1/user/login"

        def read(self, amt=-1):
            del amt
            return ok_gz[:-8]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    client = PortalClient(
        "https://portal-publicapp.ut.edu.vn/api/v1",
        opener=lambda *_args, **_kwargs: _TruncatedResponse(),
    )
    with pytest.raises(PortalProtocolError, match="not valid gzip"):
        client.fetch_day(date(2026, 8, 11), "student", "secret")


def _build_fake_jwt(*, exp: int, role: str = "sv") -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode("ascii")
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": "2251120001", "exp": exp, "data": {"role": role}}).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    return f"{header}.{payload}.sig"


def _build_raw_jwt(payload_obj: object) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode("ascii")
    payload = base64.urlsafe_b64encode(
        json.dumps(payload_obj).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    return f"{header}.{payload}.sig"


def test_ensure_web_session_replaces_expired_jwt_and_reuses_valid_token():
    expired_jwt = _build_fake_jwt(exp=int(time.time()) - 10, role="sv")
    fresh_jwt = _build_fake_jwt(exp=int(time.time()) + 86400, role="sv")
    forced_jwt = _build_fake_jwt(exp=int(time.time()) + 90000, role="sv")
    issued = [fresh_jwt, forced_jwt]
    login_calls = 0

    def opener(request, timeout):
        nonlocal login_calls
        del timeout
        assert request.full_url.endswith("/user/login")
        tok = issued[login_calls]
        login_calls += 1
        return _Response({"success": True, "body": "sv", "token": tok})

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)
    client._store_token(expired_jwt, "2251120001", role="sv")

    tok1, role1 = client.ensure_web_session("2251120001", "pass123")
    assert (tok1, role1) == (fresh_jwt, "sv")
    assert login_calls == 1

    tok2, role2 = client.ensure_web_session("2251120001", "pass123")
    assert (tok2, role2) == (fresh_jwt, "sv")
    assert login_calls == 1

    tok3, role3 = client.ensure_web_session("2251120001", "pass123", force=True)
    assert (tok3, role3) == (forced_jwt, "sv")
    assert login_calls == 2


def test_ensure_web_session_rejects_missing_credentials():
    calls = 0

    def opener(request, timeout):
        nonlocal calls
        del request, timeout
        calls += 1
        return _Response({"success": True, "body": "sv", "token": "tok"})

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)
    valid_jwt = _build_fake_jwt(exp=int(time.time()) + 86400, role="sv")
    client._store_token(valid_jwt, "2251120001")

    for username, password in (
        ("", "pass123"),
        ("   ", "pass123"),
        ("2251120001", ""),
        ("", ""),
    ):
        with pytest.raises(PortalAuthenticationError):
            client.ensure_web_session(username, password)

    assert calls == 0


def test_decode_jwt_payload_handles_valid_and_malformed_tokens():
    from core.portal_client import _decode_jwt_payload

    # Valid unpadded payloads spanning base64url length mod 4 in {0, 2, 3}
    for sub in ("a", "ab", "abc", "2251120001"):
        tok = _build_raw_jwt({"sub": sub, "exp": 2000000000})
        assert _decode_jwt_payload(tok) == {"sub": sub, "exp": 2000000000}

    non_utf8 = base64.urlsafe_b64encode(b"\xff\xfe\xfd").rstrip(b"=").decode("ascii")
    not_json = base64.urlsafe_b64encode(b"not-valid-json").rstrip(b"=").decode("ascii")

    for bad_token in (
        "",
        "tok",
        "header.payload",
        "a.b.c.d",
        "header..sig",
        "header.x.sig",
        "header.éééé.sig",
        f"header.{non_utf8}.sig",
        f"header.{not_json}.sig",
        _build_raw_jwt(["list", "not", "dict"]),
        _build_raw_jwt(12345),
        _build_raw_jwt("just-a-string"),
        _build_raw_jwt(None),
    ):
        assert _decode_jwt_payload(bad_token) == {}


def test_is_jwt_expired_respects_opaque_tokens_and_skew_window():
    from core.portal_client import _is_jwt_expired

    now = int(time.time())

    # Opaque tokens and JWTs without numeric exp are treated as NOT expired
    assert _is_jwt_expired("tok") is False
    assert _is_jwt_expired("header.payload") is False
    assert _is_jwt_expired(_build_raw_jwt({"sub": "student"})) is False
    assert _is_jwt_expired(_build_raw_jwt({"exp": None})) is False
    assert _is_jwt_expired(_build_raw_jwt({"exp": "1999999999"})) is False
    assert _is_jwt_expired(_build_raw_jwt({"exp": True})) is False
    assert _is_jwt_expired(_build_raw_jwt({"exp": False})) is False
    nan_payload = base64.urlsafe_b64encode(b'{"exp": NaN}').rstrip(b"=").decode("ascii")
    inf_payload = base64.urlsafe_b64encode(b'{"exp": Infinity}').rstrip(b"=").decode("ascii")
    assert _is_jwt_expired(f"hdr.{nan_payload}.sig") is False
    assert _is_jwt_expired(f"hdr.{inf_payload}.sig") is False

    # Numeric exp=0 is expired (must not be treated as missing/falsy)
    assert _is_jwt_expired(_build_raw_jwt({"exp": 0})) is True

    # Default skew_seconds=300
    assert _is_jwt_expired(_build_fake_jwt(exp=now - 10)) is True
    assert _is_jwt_expired(_build_fake_jwt(exp=now + 299)) is True
    assert _is_jwt_expired(_build_fake_jwt(exp=now + 300)) is True
    assert _is_jwt_expired(_build_fake_jwt(exp=now + 310)) is False

    # Custom skew_seconds
    assert _is_jwt_expired(_build_fake_jwt(exp=now + 50), skew_seconds=60) is True
    assert _is_jwt_expired(_build_fake_jwt(exp=now + 75), skew_seconds=60) is False


def test_ensure_web_session_reuses_opaque_token_and_replaces_within_skew():
    now = int(time.time())
    fresh_jwt = _build_fake_jwt(exp=now + 86400, role="sv")
    login_calls = 0

    def opener(request, timeout):
        nonlocal login_calls
        del request, timeout
        login_calls += 1
        return _Response({"success": True, "body": "sv", "token": fresh_jwt})

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)

    # Opaque cached token is treated as NOT expired and reused without login
    client._store_token("tok", "2251120001", role="sv")
    assert client.ensure_web_session("2251120001", "pass123") == ("tok", "sv")
    assert login_calls == 0

    # Cached valid JWT stored without explicit role derives role from JWT without login
    client.clear_token()
    cached_gv_jwt = _build_fake_jwt(exp=now + 86400, role="gv")
    client._store_token(cached_gv_jwt, "2251120001")
    assert client.ensure_web_session("2251120001", "pass123") == (cached_gv_jwt, "gv")
    assert login_calls == 0

    # JWT expiring within 300s skew window forces re-login
    near_expiry_jwt = _build_fake_jwt(exp=now + 120, role="sv")
    client._store_token(near_expiry_jwt, "2251120001", role="sv")
    assert client.ensure_web_session("2251120001", "pass123") == (fresh_jwt, "sv")
    assert login_calls == 1


def test_ensure_web_session_role_extraction_and_fallbacks():
    now = int(time.time())
    jwt_gv = _build_fake_jwt(exp=now + 86400, role="gv")
    jwt_sv = _build_fake_jwt(exp=now + 86400, role="sv")
    jwt_no_role = _build_raw_jwt({"sub": "2251120001", "exp": now + 86400, "data": {}})
    jwt_bad_data = _build_raw_jwt({"sub": "2251120001", "exp": now + 86400, "data": "not-a-dict"})

    cases = [
        ({"success": True, "body": " gv ", "token": jwt_sv}, (jwt_sv, "gv")),
        ({"success": True, "body": "   ", "token": jwt_gv}, (jwt_gv, "gv")),
        ({"success": True, "body": {"nested": True}, "token": jwt_gv}, (jwt_gv, "gv")),
        ({"success": True, "body": None, "token": jwt_no_role}, (jwt_no_role, "sv")),
        ({"success": True, "body": "", "token": jwt_bad_data}, (jwt_bad_data, "sv")),
        ({"success": True, "body": "", "token": "opaque-tok"}, ("opaque-tok", "sv")),
    ]

    for response_payload, expected in cases:
        client = PortalClient(
            "https://portal-publicapp.ut.edu.vn/api/v1",
            opener=lambda *_args, payload=response_payload, **_kwargs: _Response(payload),
        )
        assert client.ensure_web_session("2251120001", "pass123") == expected


def test_discard_token_clears_token_role_and_respects_generation():
    client = PortalClient(
        "https://portal-publicapp.ut.edu.vn/api/v1",
        opener=lambda *_args, **_kwargs: _Response({"success": False, "message": "bad"}),
    )

    gen0 = client._current_generation()
    client._store_token("tok-1", "student", gen0, role="gv")
    assert client._token_role == "gv"

    client.clear_token()
    assert client._cached_token("student") == ""
    assert client._token_role == ""

    # Newer token stored after clear must survive a stale _discard_token(gen0)
    gen1 = client._current_generation()
    client._store_token("tok-2", "student", gen1, role="sv")
    client._discard_token(gen0)
    assert client._cached_token("student") == "tok-2"
    assert client._token_role == "sv"

    # Rejected login with stale generation keeps newer token and role
    with pytest.raises(PortalAuthenticationError):
        client._login("student", "wrong", gen0)
    assert client._cached_token("student") == "tok-2"
    assert client._token_role == "sv"

    # Rejected login with current generation clears both token and role
    with pytest.raises(PortalAuthenticationError):
        client._login("student", "wrong", gen1)
    assert client._cached_token("student") == ""
    assert client._token_role == ""


def test_ensure_web_session_forces_relogin_for_different_username_and_isolates_role():
    now = int(time.time())
    jwt_u1 = _build_fake_jwt(exp=now + 86400, role="gv")
    jwt_u2 = _build_fake_jwt(exp=now + 86400, role="sv")
    jwt_u2_refreshed = _build_fake_jwt(exp=now + 90000, role="sv")
    login_users: list[str] = []

    def opener(request, timeout):
        del timeout
        assert request.full_url.endswith("/user/login")
        body = json.loads(request.data)
        user = body["username"]
        login_users.append(user)
        if user == "student1":
            return _Response({"success": True, "body": "gv", "token": jwt_u1})
        return _Response({"success": True, "body": "sv", "token": jwt_u2})

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)

    assert client.ensure_web_session("student1", "pass1") == (jwt_u1, "gv")
    assert client.ensure_web_session("student2", "pass2") == (jwt_u2, "sv")
    assert login_users == ["student1", "student2"]

    # Token refresh for the same user without explicit role preserves stored role
    client._store_token(jwt_u2_refreshed, "student2", client._current_generation())
    assert client._token_role == "sv"

    # Storing a token for a different user without explicit role never leaks prior user's role
    client._store_token("tok-u3", "student3", client._current_generation())
    assert client._token_role == ""


def test_ensure_web_session_and_store_token_respect_stale_generation():
    now = int(time.time())
    late_jwt = _build_fake_jwt(exp=now + 86400, role="gv")

    def opener(request, timeout):
        del request, timeout
        client.clear_token()
        return _Response({"success": True, "body": "gv", "token": late_jwt})

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)

    # Stale _store_token does not resurrect token or role
    stale_gen = client._current_generation()
    client.clear_token()
    client._store_token(late_jwt, "student", stale_gen, role="gv")
    assert client._cached_token("student") == ""
    assert client._token_role == ""

    # Mid-flight clear_token() during ensure_web_session returns session without caching it
    tok, role = client.ensure_web_session("student", "pass123")
    assert (tok, role) == (late_jwt, "gv")
    assert client._cached_token("student") == ""
    assert client._token_role == ""
