"""Security, concurrency, and mobile-vault regression tests for Phase 2 audit fixes.

Covers:
- VCS-03: Strict HTTPS Moodle origin validation & no token leak in description image caching.
- VCS-01: MoodleClient.login(persist=False) seam contract, non-blocking clear_session(),
  CookieJar swap, portal-token & user-id account isolation, and generation fencing.
- VCS-05: MoodleClient _session_lock released around network I/O, _throttle() & _get_ws_token()
  thread safety, bounded response reads, and cross-origin redirect rejection.
- VCS-13: RFC 6265 HttpOnly detection (case-insensitive key presence in cookie._rest).
- VCS-20: PortalClient._store_token() monotonic generation fencing against stale _discard_token().
- VCS-21: Update checker GitHub HTTPS redirect pinning against HTTP downgrades and off-GitHub redirects.
- VCS-06 & VCS-16: Android/iOS fail-closed secrets, lock-ordering deadlock elimination in save_settings(),
  atomic batch rollback avoidance, lazy recovery factory, and Linux XDG private data dir.
"""

from __future__ import annotations

import asyncio
from datetime import date
from email.message import Message
import gzip
from http.cookiejar import Cookie, CookieJar
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

import config
from core.client import (
    MoodleClient,
    _SameMoodleOriginRedirectHandler,
    _read_bounded_response_bytes,
)
from core.portal_client import PortalClient
from core.update_checker import (
    _ApprovedGitHubRedirectHandler,
    _is_approved_github_url,
    _open_github_url,
)
from gui.core.utils import (
    _parse_trusted_moodle_pluginfile_url,
    pre_cache_description_images,
)
from platform_utils.background_sync import AndroidBackgroundBridge, AndroidSecretBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _start_loopback_server(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, thread


class _FakeHttpResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        url: str = "https://courses.ut.edu.vn/webservice/rest/server.php",
        headers: dict[str, str] | None = None,
    ):
        self._buf = io.BytesIO(body)
        self.status = status
        self._url = url
        msg = Message()
        for key, value in (headers or {}).items():
            msg[key] = value
        self.headers = msg
        self.closed = False

    def info(self):
        return self.headers

    def geturl(self):
        return self._url

    def read(self, amt: int = -1):
        return self._buf.read(amt)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False


# ---------------------------------------------------------------------------
# VCS-03: Moodle WS Token Exfiltration in Description Image Pre-Caching
# ---------------------------------------------------------------------------


def test_parse_trusted_moodle_pluginfile_url_rejects_bypass_vectors():
    origin = "https://courses.ut.edu.vn"
    bad_urls = [
        "http://courses.ut.edu.vn/pluginfile.php/1/a.png",
        "http://127.0.0.1:8000/courses.ut.edu.vn/pluginfile.php/1/a.png",
        "https://evil.example/courses.ut.edu.vn/pluginfile.php/1/a.png",
        "https://courses.ut.edu.vn.evil.example/pluginfile.php/1/a.png",
        "https://courses.ut.edu.vn@evil.example/pluginfile.php/1/a.png",
        "https://user:pass@courses.ut.edu.vn/pluginfile.php/1/a.png",
        "https://courses.ut.edu.vn/evil/pluginfile.php/1/a.png",
        "https://courses.ut.edu.vn/pluginfile.php/1/../../secret.png",
        "https://courses.ut.edu.vn/pluginfile.php/1/a.png\r\nX-Inject: 1",
    ]
    for bad in bad_urls:
        assert _parse_trusted_moodle_pluginfile_url(bad, origin) is None

    assert _parse_trusted_moodle_pluginfile_url(
        "https://courses.ut.edu.vn/pluginfile.php/123/mod_assign/intro/fig.png",
        origin,
    ) == (
        "/webservice/pluginfile.php/123/mod_assign/intro/fig.png",
        "/pluginfile.php/123/mod_assign/intro/fig.png",
    )


def test_pre_cache_description_images_never_sends_token_to_off_origin_or_leaks_on_fallback(
    monkeypatch, tmp_path
):
    received_paths: list[str] = []

    class _AttackerHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            received_paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"\x89PNG\r\n\x1a\nfake")

        def log_message(self, *_args):
            pass

    server, port, thread = _start_loopback_server(_AttackerHandler)
    try:
        monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
        monkeypatch.setattr(config.settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
        monkeypatch.setattr(config.settings, "MOODLE_WS_TOKEN", "SUPER-SECRET-WS-TOKEN")
        monkeypatch.setattr(
            config.settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn"
        )
        monkeypatch.setattr("gui.core.utils.cleanup_image_cache", lambda: None)

        seen_requests: list[str] = []

        def _failing_urlopen(req, timeout=8):
            seen_requests.append(req.full_url)
            raise urllib.error.URLError("simulated 404/network error")

        monkeypatch.setattr(urllib.request, "urlopen", _failing_urlopen)

        attacker_url = (
            f"http://127.0.0.1:{port}/courses.ut.edu.vn/pluginfile.php/999/exfil.png"
        )
        legit_url = "https://courses.ut.edu.vn/pluginfile.php/42/mod_assign/intro/pic.png"
        html = f'<img src="{attacker_url}"><img src="{legit_url}">'

        rewritten = pre_cache_description_images(html)

        # Off-origin URL must NEVER be fetched at all
        assert received_paths == []
        assert len(seen_requests) == 1
        assert seen_requests[0].startswith(
            "https://courses.ut.edu.vn/webservice/pluginfile.php/42/"
        )
        # Fallback rewrite MUST NOT embed the WS token in HTML
        assert "SUPER-SECRET-WS-TOKEN" not in rewritten
        assert "token=" not in rewritten
        assert (
            "https://courses.ut.edu.vn/webservice/pluginfile.php/42/mod_assign/intro/pic.png"
            in rewritten
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_pre_cache_description_images_does_not_attach_token_when_token_origin_mismatches(
    monkeypatch, tmp_path
):
    seen_requests: list[str] = []

    def _capture_urlopen(req, timeout=8):
        seen_requests.append(req.full_url)
        raise urllib.error.URLError("stop")

    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(config.settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(config.settings, "MOODLE_WS_TOKEN", "THNN-ONLY-TOKEN")
    monkeypatch.setattr(
        config.settings, "MOODLE_WS_TOKEN_ORIGIN", "https://thnn.ut.edu.vn"
    )
    monkeypatch.setattr("gui.core.utils.cleanup_image_cache", lambda: None)
    monkeypatch.setattr(urllib.request, "urlopen", _capture_urlopen)

    html = '<img src="https://courses.ut.edu.vn/pluginfile.php/10/mod_assign/intro/a.png">'
    rewritten = pre_cache_description_images(html)

    assert len(seen_requests) == 1
    assert "THNN-ONLY-TOKEN" not in seen_requests[0]
    assert "token=" not in seen_requests[0]
    assert "THNN-ONLY-TOKEN" not in rewritten


# ---------------------------------------------------------------------------
# VCS-01 / VCS-05 / VCS-13: MoodleClient Isolation, Locking, HttpOnly, Redirects
# ---------------------------------------------------------------------------


def test_moodle_client_login_persist_false_validates_without_mutating_global_settings(
    monkeypatch,
):
    monkeypatch.setattr(config.settings, "UTH_USERNAME", "userA")
    monkeypatch.setattr(config.settings, "UTH_PASSWORD", "passA")
    monkeypatch.setattr(config.settings, "MOODLE_WS_TOKEN", "tokA")
    monkeypatch.setattr(
        config.settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn"
    )
    save_calls: list[bool] = []
    monkeypatch.setattr(config, "save_settings", lambda: save_calls.append(True))

    client = MoodleClient()
    monkeypatch.setattr(
        client,
        "_post",
        lambda url, data, headers=None, timeout=15: (200, {"token": "tokB-probe"}),
    )

    ok = client.login(username="userB", password="passB", force=True, persist=False)
    assert ok is True
    assert save_calls == []
    assert config.settings.UTH_USERNAME == "userA"
    assert config.settings.UTH_PASSWORD == "passA"
    assert config.settings.MOODLE_WS_TOKEN == "tokA"

    # Contrast: persist=True updates settings and calls save_settings()
    ok_persist = client.login(
        username="userB", password="passB", force=True, persist=True
    )
    assert ok_persist is True
    assert len(save_calls) == 1
    assert config.settings.UTH_USERNAME == "userB"
    assert config.settings.UTH_PASSWORD == "passB"
    assert config.settings.MOODLE_WS_TOKEN == "tokB-probe"


def test_moodle_client_post_and_get_close_response_handles_and_enforce_size_limit(
    monkeypatch,
):
    client = MoodleClient()
    fake_post_resp = _FakeHttpResponse(b'{"ok": 1}')
    fake_get_resp = _FakeHttpResponse(b"<html>hello</html>")

    responses = [fake_post_resp, fake_get_resp]
    monkeypatch.setattr("core.client._urlopen", lambda req, timeout=15: responses.pop(0))

    status, parsed = client._post(
        "https://courses.ut.edu.vn/webservice/rest/server.php", {"a": "1"}
    )
    assert status == 200
    assert parsed == {"ok": 1}
    assert fake_post_resp.closed is True

    status_g, raw_g = client._get("https://courses.ut.edu.vn/my/")
    assert status_g == 200
    assert raw_g == b"<html>hello</html>"
    assert fake_get_resp.closed is True

    # Oversized response fails closed (returns None body) and handle still closed
    oversized_resp = _FakeHttpResponse(b"x" * 2048)
    monkeypatch.setattr("core.client._MAX_API_RESPONSE_BYTES", 512)
    monkeypatch.setattr("core.client._urlopen", lambda req, timeout=15: oversized_resp)
    status_big, raw_big = client._get("https://courses.ut.edu.vn/my/")
    assert status_big == 200
    assert raw_big is None
    assert oversized_resp.closed is True


def test_moodle_client_clear_session_is_non_blocking_swaps_jar_and_fences_in_flight_login(
    monkeypatch,
):
    client = MoodleClient()
    old_jar = client._cookie_jar

    in_flight_entered = threading.Event()
    release_network = threading.Event()

    def _slow_open_html(url, data=None, timeout=15, opener=None):
        in_flight_entered.set()
        release_network.wait(timeout=2.0)
        return (
            '<form action="https://courses.ut.edu.vn/login/index.php">'
            '<input name="logintoken" value="abc"></form>',
            "https://courses.ut.edu.vn/login/index.php",
        )

    monkeypatch.setattr(client, "_open_html", _slow_open_html)
    monkeypatch.setattr(config.settings, "UTH_USERNAME", "userA")
    monkeypatch.setattr(config.settings, "UTH_PASSWORD", "passA")

    login_result: list[bool] = []

    def _worker():
        login_result.append(client.login_session())

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    assert in_flight_entered.wait(timeout=2.0)

    # clear_session() must NOT block on the in-flight HTTP call
    start = time.monotonic()
    client.clear_session()
    elapsed_ms = (time.monotonic() - start) * 1000.0
    assert elapsed_ms < 100.0
    assert client._cookie_jar is not old_jar

    release_network.set()
    t.join(timeout=2.0)
    assert login_result == [False]
    assert client._sesskey == ""


def test_moodle_client_portal_token_and_user_id_isolated_across_account_switch(
    monkeypatch,
):
    client = MoodleClient()
    monkeypatch.setattr(config.settings, "UTH_USERNAME", "userA")
    monkeypatch.setattr(config.settings, "UTH_PASSWORD", "passA")
    monkeypatch.setattr(config.settings, "MOODLE_WS_TOKEN", "tokA")
    monkeypatch.setattr(
        config.settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn"
    )

    # Seed userA caches
    client._portal_token = "jwt-user-A"
    client._portal_token_username = "usera"
    client._portal_token_generation = client._session_generation
    client._cached_user_id = 101
    client._cached_user_id_username = "usera"

    assert client.get_portal_token("userA") == "jwt-user-A"

    # Switching username must NOT return userA's portal JWT
    monkeypatch.setattr(
        client,
        "_post_json",
        lambda *_args, **_kwargs: (401, json.dumps({"success": False})),
    )
    assert client.get_portal_token("userB") == ""

    # Switching settings.UTH_USERNAME must NOT return userA's cached user_id
    monkeypatch.setattr(config.settings, "UTH_USERNAME", "userB")
    monkeypatch.setattr(
        client,
        "call_ws_api",
        lambda fn, params=None: (
            {"userid": 202} if fn == "core_webservice_get_site_info" else {}
        ),
    )
    assert client.get_user_id() == 202


def test_moodle_client_get_session_cookies_detects_rfc6265_httponly_flag():
    client = MoodleClient()
    client._sesskey = "active-sesskey"
    jar = CookieJar()
    # RFC 6265 valueless `HttpOnly` attribute parses as `{"HttpOnly": None}` in cookie._rest
    jar.set_cookie(
        Cookie(
            version=0,
            name="MoodleSession",
            value="sess-val",
            port=None,
            port_specified=False,
            domain="courses.ut.edu.vn",
            domain_specified=True,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": None},
            rfc2109=False,
        )
    )
    jar.set_cookie(
        Cookie(
            version=0,
            name="MoodleSessionLower",
            value="sess-val-2",
            port=None,
            port_specified=False,
            domain="courses.ut.edu.vn",
            domain_specified=True,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={"httponly": None},
            rfc2109=False,
        )
    )
    jar.set_cookie(
        Cookie(
            version=0,
            name="PublicCookie",
            value="pub",
            port=None,
            port_specified=False,
            domain="courses.ut.edu.vn",
            domain_specified=True,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )
    )
    client._cookie_jar = jar

    cookies = {c["name"]: c for c in client.get_session_cookies(ensure_login=False)}
    assert cookies["MoodleSession"]["httpOnly"] is True
    assert cookies["MoodleSessionLower"]["httpOnly"] is True
    assert cookies["PublicCookie"]["httpOnly"] is False


def test_moodle_client_blocks_cross_origin_redirect_and_enforces_bounded_gzip_read():
    handler = _SameMoodleOriginRedirectHandler("https://courses.ut.edu.vn")
    orig_req = urllib.request.Request(
        "https://courses.ut.edu.vn/webservice/rest/server.php", method="POST"
    )
    with pytest.raises(urllib.error.HTTPError, match="Untrusted Moodle redirect"):
        handler.redirect_request(
            orig_req, None, 302, "Found", Message(), "https://evil.example/steal"
        )
    with pytest.raises(urllib.error.HTTPError, match="Untrusted Moodle redirect"):
        handler.redirect_request(
            orig_req, None, 302, "Found", Message(), "http://courses.ut.edu.vn/downgrade"
        )

    # Gzip decompression bomb must fail closed (return None) above max_bytes
    bomb = gzip.compress(b"A" * 4096)
    resp = _FakeHttpResponse(bomb, headers={"Content-Encoding": "gzip"})
    assert _read_bounded_response_bytes(resp, max_bytes=1024) is None

    # Normal gzip response within limit succeeds
    small = gzip.compress(b'{"ok":true}')
    resp_ok = _FakeHttpResponse(small, headers={"Content-Encoding": "gzip"})
    assert _read_bounded_response_bytes(resp_ok, max_bytes=1024) == b'{"ok":true}'


def test_moodle_client_throttle_and_ws_token_are_thread_safe(monkeypatch):
    client = MoodleClient()
    client._request_interval = 0.01

    timestamps: list[float] = []
    ts_lock = threading.Lock()

    def _run_throttle():
        client._throttle()
        with ts_lock:
            timestamps.append(time.monotonic())

    threads = [threading.Thread(target=_run_throttle) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)
    assert len(timestamps) == 5

    # Concurrent _get_ws_token(force=False) should serialize and share one login call
    monkeypatch.setattr(config.settings, "UTH_USERNAME", "student1")
    monkeypatch.setattr(config.settings, "UTH_PASSWORD", "pw1")
    monkeypatch.setattr(config.settings, "MOODLE_WS_TOKEN", "")
    monkeypatch.setattr(config.settings, "MOODLE_WS_TOKEN_ORIGIN", "")
    monkeypatch.setattr(config, "save_settings", lambda: None)

    post_calls = 0
    post_lock = threading.Lock()

    def _fake_post(*_args, **_kwargs):
        nonlocal post_calls
        time.sleep(0.03)
        with post_lock:
            post_calls += 1
        return (200, {"token": "shared-ws-tok"})

    monkeypatch.setattr(client, "_post", _fake_post)

    tokens: list[str] = []
    t_lock = threading.Lock()

    def _fetch():
        tok = client._get_ws_token(force=False)
        with t_lock:
            tokens.append(tok)

    workers = [threading.Thread(target=_fetch) for _ in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=2.0)
    assert tokens == ["shared-ws-tok"] * 4
    assert post_calls == 1


# ---------------------------------------------------------------------------
# VCS-20: PortalClient Monotonic Token Generation Fencing
# ---------------------------------------------------------------------------


def test_portal_client_stale_discard_never_wipes_newer_stored_token():
    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1")

    gen_a = client._store_token("tok-A", "studentA")
    assert gen_a is not None
    gen_b = client._store_token("tok-B", "studentB")
    assert gen_b is not None
    assert gen_b > gen_a

    # A stale failure from gen_a attempting to discard must NOT wipe studentB's token
    client._discard_token(gen_a)
    assert client._cached_token("studentB") == "tok-B"

    # Matching gen_b does discard it
    client._discard_token(gen_b)
    assert client._cached_token("studentB") == ""

    # clear_token() while login holds older generation prevents stale token resurrection
    pre_clear_gen = client._current_generation()
    client.clear_token()
    assert client._store_token("stale-tok", "studentA", pre_clear_gen) is None
    assert client._cached_token("studentA") == ""


def test_portal_client_concurrent_401_and_refresh_keep_newest_token():
    first_login_ready = threading.Event()
    proceed_stale_401 = threading.Event()

    class _Resp:
        def __init__(self, payload: dict):
            self._data = json.dumps(payload).encode("utf-8")
            self.headers = Message()

        def geturl(self):
            return "https://portal-publicapp.ut.edu.vn/api/v1/lichhoc/ngay"

        def read(self, amt=-1):
            return self._data if amt < 0 else self._data[:amt]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    login_seq = 0
    seq_lock = threading.Lock()

    def opener(req, timeout):
        del timeout
        nonlocal login_seq
        if req.full_url.endswith("/user/login"):
            with seq_lock:
                login_seq += 1
                tok = f"tok-{login_seq}"
            return _Resp({"success": True, "token": tok})
        auth = req.get_header("Authorization")
        if auth == "Bearer tok-1":
            first_login_ready.set()
            proceed_stale_401.wait(timeout=2.0)
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", Message(), None
            )
        return _Resp({"success": True, "body": []})

    client = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1", opener=opener)
    results: list[tuple] = []

    def _thread_one():
        results.append(client.fetch_day(date(2026, 8, 11), "student", "secret"))

    t1 = threading.Thread(target=_thread_one, daemon=True)
    t1.start()
    assert first_login_ready.wait(timeout=2.0)

    # Newer token stored before t1 receives its stale 401
    new_gen = client._store_token("tok-fresh", "student")
    assert new_gen is not None
    proceed_stale_401.set()
    t1.join(timeout=2.0)

    assert results == [()]
    # Fresh token must survive stale 401 discard or be replaced by a valid newer token
    assert client._cached_token("student") in {"tok-fresh", "tok-2"}


# ---------------------------------------------------------------------------
# VCS-21: Update Checker Redirect Pinning
# ---------------------------------------------------------------------------


def test_update_checker_is_approved_github_url_and_redirect_handler():
    assert _is_approved_github_url(
        "https://api.github.com/repos/Chouwzi/UTHelper/releases/latest"
    )
    assert _is_approved_github_url(
        "https://github.com/Chouwzi/UTHelper/releases/download/v2.0/app.exe"
    )
    assert _is_approved_github_url(
        "https://objects.githubusercontent.com/github-production-release-asset/1"
    )
    assert _is_approved_github_url("https://release-assets.githubusercontent.com/asset.zip")

    for bad in (
        "http://github.com/Chouwzi/UTHelper/releases/download/v2.0/app.exe",
        "http://objects.githubusercontent.com/asset.zip",
        "https://evil.example/asset.exe",
        "https://github.com.evil.example/asset.exe",
        "https://user:pass@github.com/Chouwzi/UTHelper/releases/latest",
        "https://github.com:8443/Chouwzi/UTHelper/releases/latest",
        "https://127.0.0.1/asset.exe",
    ):
        assert not _is_approved_github_url(bad), f"Should reject {bad}"

    handler = _ApprovedGitHubRedirectHandler()
    orig = urllib.request.Request(
        "https://github.com/Chouwzi/UTHelper/releases/download/v2.0/app.exe"
    )
    ok_redir = handler.redirect_request(
        orig,
        None,
        302,
        "Found",
        Message(),
        "https://objects.githubusercontent.com/signed-asset",
    )
    assert ok_redir is not None
    assert ok_redir.full_url == "https://objects.githubusercontent.com/signed-asset"

    with pytest.raises(urllib.error.URLError, match="Blocked untrusted update redirect"):
        handler.redirect_request(
            orig,
            None,
            302,
            "Found",
            Message(),
            "http://objects.githubusercontent.com/downgraded",
        )
    with pytest.raises(urllib.error.URLError, match="Blocked untrusted update redirect"):
        handler.redirect_request(
            orig,
            None,
            302,
            "Found",
            Message(),
            "https://evil.example/trojan.exe",
        )


def test_update_checker_open_github_url_blocks_live_loopback_302_redirect():
    target_hits: list[str] = []

    class _TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            target_hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hijacked")

        def log_message(self, *_args):
            pass

    target_srv, target_port, target_thread = _start_loopback_server(_TargetHandler)

    class _RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target_port}/hijack")
            self.end_headers()

        def log_message(self, *_args):
            pass

    redir_srv, redir_port, redir_thread = _start_loopback_server(_RedirectHandler)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{redir_port}/release")
        with pytest.raises(
            urllib.error.URLError, match="Blocked untrusted update redirect"
        ):
            _open_github_url(req, timeout=3)
        assert target_hits == []
    finally:
        redir_srv.shutdown()
        redir_srv.server_close()
        redir_thread.join(timeout=2)
        target_srv.shutdown()
        target_srv.server_close()
        target_thread.join(timeout=2)


# ---------------------------------------------------------------------------
# VCS-06 & VCS-16: Config Lock Ordering, Rollback Avoidance, iOS Fail-Closed, Linux Dir
# ---------------------------------------------------------------------------


def test_save_settings_and_hydrate_secrets_never_abba_deadlock(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_is_real_android", lambda: True)
    monkeypatch.setattr(config, "_is_real_ios", lambda: False)
    monkeypatch.setattr(config.settings, "UTH_USERNAME", "student_deadlock")
    monkeypatch.setattr(config.settings, "UTH_PASSWORD", "pw-123")

    write_started = threading.Event()
    hydrate_done = threading.Event()

    class _SlowAtomicBackend:
        atomic_batch_write = True

        def __init__(self):
            self.data = {"password": "old"}

        def read_secret(self, key: str) -> str:
            return self.data.get(key, "")

        def write_secret(self, key: str, value: str) -> bool:
            self.data[key] = value
            return True

        def snapshot_secrets(self, keys):
            return {k: self.data.get(k, "") for k in keys}

        def restore_secrets(self, snapshot) -> bool:
            self.data = dict(snapshot)
            return True

        def write_all(self, secrets: dict[str, str]) -> bool:
            write_started.set()
            # Wait for hydrate_secrets() on the other thread to acquire _settings_save_lock
            assert hydrate_done.wait(timeout=2.0), "AB-BA deadlock detected!"
            self.data = dict(secrets)
            return True

    backend = _SlowAtomicBackend()
    config.register_secret_backend(backend)
    try:
        save_errors: list[BaseException] = []

        def _saver():
            try:
                config.save_settings()
            except BaseException as exc:  # pragma: no cover
                save_errors.append(exc)

        t = threading.Thread(target=_saver, daemon=True)
        t.start()
        assert write_started.wait(timeout=2.0)

        # Concurrently call hydrate_secrets() (simulating event-loop thread)
        config.hydrate_secrets({"telegram_bot_token": "bot-tok"})
        hydrate_done.set()

        t.join(timeout=3.0)
        assert not t.is_alive(), "save_settings thread deadlocked"
        assert save_errors == []
        assert backend.data["password"] == "pw-123"
    finally:
        config.register_secret_backend(None)


def test_atomic_batch_backend_failure_skips_redundant_restore_write(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config, "_is_real_android", lambda: True)
    monkeypatch.setattr(config, "_is_real_ios", lambda: False)
    monkeypatch.setattr(config.settings, "UTH_PASSWORD", "new-pw")

    write_all_calls: list[dict[str, str]] = []
    restore_calls: list[dict[str, str]] = []

    class _FailingAtomicBackend:
        atomic_batch_write = True

        def read_secret(self, key: str) -> str:
            return "old-pw" if key == "password" else ""

        def write_secret(self, key: str, value: str) -> bool:
            return False

        def snapshot_secrets(self, keys):
            return {k: ("old-pw" if k == "password" else "") for k in keys}

        def restore_secrets(self, snapshot) -> bool:
            restore_calls.append(dict(snapshot))
            return False

        def write_all(self, secrets: dict[str, str]) -> bool:
            write_all_calls.append(dict(secrets))
            return False

    config.register_secret_backend(_FailingAtomicBackend())
    try:
        assert config.save_settings() is False
        # Atomic backend failed before committing -> MUST NOT issue a second rollback write
        assert len(write_all_calls) == 1
        assert restore_calls == []
    finally:
        config.register_secret_backend(None)


def test_atomic_batch_backend_still_rolls_back_when_json_write_fails_after_secret_commit(
    monkeypatch, tmp_path
):
    from core.safe_file_io import SafeFileIO

    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config, "_is_real_android", lambda: True)
    monkeypatch.setattr(config, "_is_real_ios", lambda: False)
    monkeypatch.setattr(config.settings, "UTH_PASSWORD", "new-pw")
    monkeypatch.setattr(SafeFileIO, "write_json_atomic", lambda *_args, **_kwargs: False)

    restored: list[dict[str, str]] = []

    class _OkBackend:
        atomic_batch_write = True

        def __init__(self):
            self.data = {"password": "old-pw"}

        def read_secret(self, key: str) -> str:
            return self.data.get(key, "")

        def write_secret(self, key: str, value: str) -> bool:
            self.data[key] = value
            return True

        def snapshot_secrets(self, keys):
            return {k: self.data.get(k, "") for k in keys}

        def restore_secrets(self, snapshot) -> bool:
            restored.append(dict(snapshot))
            self.data = dict(snapshot)
            return True

        def write_all(self, secrets: dict[str, str]) -> bool:
            self.data = dict(secrets)
            return True

    backend = _OkBackend()
    config.register_secret_backend(backend)
    try:
        assert config.save_settings() is False
        assert len(restored) == 1
        assert backend.data["password"] == "old-pw"
    finally:
        config.register_secret_backend(None)


def test_ios_fails_closed_without_native_backend_and_stashes_legacy_plaintext(
    monkeypatch, tmp_path
):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"UTH_USERNAME": "ios_user", "UTH_PASSWORD": "legacy-ios-password"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config, "_is_real_android", lambda: False)
    monkeypatch.setattr(config, "_is_real_ios", lambda: True)
    monkeypatch.setattr(config, "_pending_plaintext_migration", {})
    config.register_secret_backend(None)

    loaded = config.load_settings()
    assert loaded.UTH_PASSWORD == "legacy-ios-password"
    assert config._pending_plaintext_migration.get("password") == "legacy-ios-password"

    monkeypatch.setattr(config.settings, "UTH_USERNAME", "ios_user")
    monkeypatch.setattr(config.settings, "UTH_PASSWORD", "legacy-ios-password")
    assert config.save_settings() is False

    # Once a secure Keychain backend is registered, save succeeds without plaintext in JSON.
    stored: dict[str, str] = {}

    class _IosKeychainStub:
        atomic_batch_write = True

        def read_secret(self, key: str) -> str:
            return stored.get(key, "")

        def write_secret(self, key: str, value: str) -> bool:
            stored[key] = value
            return True

        def snapshot_secrets(self, keys):
            return {k: stored.get(k, "") for k in keys}

        def restore_secrets(self, snapshot) -> bool:
            stored.clear()
            stored.update(snapshot)
            return True

        def write_all(self, secrets: dict[str, str]) -> bool:
            stored.clear()
            stored.update(secrets)
            return True

    config.register_secret_backend(_IosKeychainStub())
    try:
        assert config.save_settings() is True
        raw_json = json.loads(settings_file.read_text(encoding="utf-8"))
        assert "UTH_PASSWORD" not in raw_json
        assert stored["password"] == "legacy-ios-password"
    finally:
        config.register_secret_backend(None)


def test_lazy_recovery_factory_recovers_after_failed_mobile_startup_read(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config, "_is_real_android", lambda: True)
    monkeypatch.setattr(config, "_is_real_ios", lambda: False)
    monkeypatch.setattr(config.settings, "UTH_USERNAME", "student_recover")
    monkeypatch.setattr(config.settings, "UTH_PASSWORD", "pw-recover")
    config.register_secret_backend(None)

    class _FlakyService:
        def __init__(self):
            self.read_calls = 0
            self.written: dict[str, str] = {}

        async def read_app_secrets(self):
            self.read_calls += 1
            if self.read_calls == 1:
                raise TimeoutError("transient startup channel timeout")
            return dict(self.written)

        async def write_app_secrets(self, secrets: dict[str, str]):
            self.written = dict(secrets)
            return True

        async def clear_app_secrets(self):
            self.written.clear()
            return True

    service = _FlakyService()
    bridge = AndroidBackgroundBridge(SimpleNamespace(services=[]))
    bridge.service = service

    async def _scenario():
        # Startup read fails transiently -> bridge still registers lazy recovery factory
        with pytest.raises(TimeoutError):
            await bridge.read_app_secrets()
        assert config._secret_backend is None
        assert config._secret_backend_factory is not None

        # Later off-loop save_settings() invokes lazy recovery factory and persists secrets
        saved = await asyncio.to_thread(config.save_settings)
        assert saved is True
        assert service.written.get("password") == "pw-recover"
        assert isinstance(config._secret_backend, AndroidSecretBackend)

    try:
        asyncio.run(_scenario())
    finally:
        config.register_secret_backend(None)


def test_linux_data_dir_uses_xdg_or_home_never_world_writable_tmp(
    monkeypatch, tmp_path
):
    xdg_home = tmp_path / "xdg_data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_home))
    resolved = config._get_linux_data_dir()
    assert resolved == xdg_home / "UTHelper"
    config._ensure_private_data_dir(resolved)
    assert resolved.is_dir()

    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home_dir"))
    resolved_home = config._get_linux_data_dir()
    assert resolved_home == tmp_path / "home_dir" / ".local" / "share" / "UTHelper"
    config._ensure_private_data_dir(resolved_home)
    assert resolved_home.is_dir()
    assert os.path.normcase(str(resolved_home)) != os.path.normcase("/tmp/UTHelper")


def test_is_real_ios_detects_sys_platform_and_ios_marker(monkeypatch):
    monkeypatch.setattr(sys, "platform", "ios")
    assert config._is_real_ios() is True
    assert config._requires_native_secret_backend() is True

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(sys, "_IOS_", raising=False)
    assert config._is_real_ios() is False
