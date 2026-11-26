"""Milestone 4 regression tests for F17-F19 WebView performance & concurrency."""

from __future__ import annotations

import gzip
import io
import os
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import settings
from core.client import MoodleClient
from core.courses.local_preview_server import LocalPreviewServer


class _FakeRawResponse:
    def __init__(
        self,
        url: str,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "text/plain",
        content_encoding: str = "",
        on_read=None,
    ):
        self._url = url
        self._stream = io.BytesIO(body)
        self.status = status
        headers = {"Content-Type": content_type}
        if content_encoding:
            headers["Content-Encoding"] = content_encoding
        self.headers = headers
        self._on_read = on_read

    def geturl(self) -> str:
        return self._url

    def info(self):
        return self.headers

    def read(self, n: int = -1) -> bytes:
        if self._on_read is not None:
            self._on_read()
        return self._stream.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_open_session_raw_releases_session_lock_during_io_and_supports_gzip(
    monkeypatch,
):
    monkeypatch.setattr(settings, "UTH_USERNAME", "student-42")
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sess-1"
    client._session_owner = "student-42"
    client._session_generation = 1

    first_started = threading.Event()
    release_first = threading.Event()
    seen_accept_encoding: list[str] = []

    class _FakeOpener:
        def open(self, request, timeout=20):
            del timeout
            seen_accept_encoding.append(request.headers.get("Accept-encoding", ""))
            url = request.full_url
            if "slow.css" in url:

                def wait_for_gate():
                    first_started.set()
                    assert release_first.wait(timeout=3)

                payload = gzip.compress(b"body{color:red;}")
                return _FakeRawResponse(
                    url,
                    payload,
                    content_type="text/css; charset=utf-8",
                    content_encoding="gzip",
                    on_read=wait_for_gate,
                )
            return _FakeRawResponse(
                url,
                gzip.compress(b"console.log('fast');"),
                content_type="application/javascript",
                content_encoding="gzip",
            )

    client._html_opener = _FakeOpener()

    slow_result: list[tuple[int, str, str, bytes]] = []
    slow_errors: list[BaseException] = []

    def run_slow():
        try:
            slow_result.append(
                client.open_session_raw("https://courses.ut.edu.vn/theme/slow.css")
            )
        except BaseException as exc:  # pragma: no cover - surfaced in assertion
            slow_errors.append(exc)

    t1 = threading.Thread(target=run_slow)
    t1.start()
    try:
        assert first_started.wait(timeout=2)
        # Lock must NOT be held while opener/read is blocking.
        acquired = client._session_lock.acquire(blocking=False)
        assert acquired is True
        client._session_lock.release()

        # Second concurrent subresource request completes without waiting for t1.
        st2, _u2, ct2, b2 = client.open_session_raw(
            "https://courses.ut.edu.vn/lib/fast.js"
        )
        assert st2 == 200
        assert "javascript" in ct2
        assert b2 == b"console.log('fast');"
    finally:
        release_first.set()
        t1.join(timeout=3)

    assert slow_errors == []
    assert len(slow_result) == 1
    assert slow_result[0][0] == 200
    assert slow_result[0][3] == b"body{color:red;}"
    assert seen_accept_encoding == ["gzip", "gzip"]


def test_open_session_raw_concurrent_expiry_performs_single_relogin(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "student-42")
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "old-sess"
    client._session_owner = "student-42"
    client._session_generation = 1

    login_calls = 0
    login_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def fake_login_session(
        username=None,
        password=None,
        force=False,
        *,
        quiz_lease=None,
        require_sesskey=True,
    ):
        del username, password, quiz_lease, require_sesskey
        nonlocal login_calls
        assert force is True
        with login_lock:
            login_calls += 1
            client._sesskey = "new-sess"
            client._session_owner = "student-42"
            client._session_generation += 1
        return True

    client.login_session = fake_login_session

    class _ExpiryOpener:
        def open(self, request, timeout=20):
            del timeout
            with client._session_lock:
                sess = client._sesskey
            if sess == "old-sess":
                barrier.wait(timeout=2)
                return _FakeRawResponse(
                    "https://courses.ut.edu.vn/login/index.php",
                    b"<form>login</form>",
                    content_type="text/html",
                )
            return _FakeRawResponse(
                request.full_url,
                b"ok-after-refresh",
                content_type="text/plain",
            )

    client._html_opener = _ExpiryOpener()

    results: list[bytes] = []
    errors: list[BaseException] = []

    def worker(path: str):
        try:
            _, _, _, body = client.open_session_raw(path)
            results.append(body)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("https://courses.ut.edu.vn/a.js",)),
        threading.Thread(target=worker, args=("https://courses.ut.edu.vn/b.css",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3)

    assert errors == []
    assert results == [b"ok-after-refresh", b"ok-after-refresh"]
    assert login_calls == 1


def test_open_session_raw_aborts_cleanly_on_midflight_clear_session(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "student-42")
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sess-1"
    client._session_owner = "student-42"
    client._session_generation = 1

    in_flight = threading.Event()
    proceed = threading.Event()

    class _MidFlightOpener:
        def open(self, request, timeout=20):
            del timeout
            in_flight.set()
            assert proceed.wait(timeout=2)
            return _FakeRawResponse(
                request.full_url,
                b"sensitive-old-account-bytes",
                content_type="text/html",
            )

    client._html_opener = _MidFlightOpener()

    caught: list[BaseException] = []

    def run_fetch():
        try:
            client.open_session_raw("https://courses.ut.edu.vn/my/")
        except BaseException as exc:
            caught.append(exc)

    thread = threading.Thread(target=run_fetch)
    thread.start()
    try:
        assert in_flight.wait(timeout=2)
        client.clear_session()
        proceed.set()
        thread.join(timeout=2)
    finally:
        proceed.set()

    assert len(caught) == 1
    assert isinstance(caught[0], RuntimeError)


def test_local_preview_server_caches_safe_static_assets_only(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))

    seen: list[tuple[str, str]] = []

    class _FakeClient:
        moodle_site_origin = "https://courses.ut.edu.vn"
        _session_generation = 1

        def open_session_raw(
            self,
            url: str,
            *,
            method: str = "GET",
            body: bytes | None = None,
            **_kwargs,
        ):
            del body
            seen.append((method, url))
            if url.endswith("styles.css"):
                return 200, url, "text/css; charset=utf-8", b"body{margin:0;}"
            if "pluginfile.php" in url:
                return 200, url, "application/pdf", b"%PDF-1.4"
            if "view.php" in url:
                return 200, url, "text/html; charset=utf-8", b"<html><body>Quiz</body></html>"
            return 200, url, "application/json", b'{"ok":true}'

    client = _FakeClient()
    server = LocalPreviewServer()
    try:
        server.register_session_provider("courses", client)

        css_url = server.build_session_bridge_url(
            "courses", "https://courses.ut.edu.vn/theme/styles.css"
        )
        plugin_url = server.build_session_bridge_url(
            "courses", "https://courses.ut.edu.vn/pluginfile.php/12/mod_resource/content/1/notes.pdf"
        )
        html_url = server.build_session_bridge_url(
            "courses", "https://courses.ut.edu.vn/mod/quiz/view.php?id=9"
        )
        assert css_url and plugin_url and html_url

        # 1st + 2nd static CSS fetch hits cache on 2nd request and sends private max-age.
        with urllib.request.urlopen(css_url, timeout=5) as r1:
            assert r1.headers.get("Cache-Control") == "private, max-age=300"
            assert r1.read() == b"body{margin:0;}"
        with urllib.request.urlopen(css_url, timeout=5) as r2:
            assert r2.headers.get("Cache-Control") == "private, max-age=300"
            assert r2.read() == b"body{margin:0;}"
        assert len([u for _m, u in seen if u.endswith("styles.css")]) == 1

        # Session generation bump invalidates static cache immediately.
        client._session_generation = 2
        with urllib.request.urlopen(css_url, timeout=5) as r3:
            assert r3.read() == b"body{margin:0;}"
        assert len([u for _m, u in seen if u.endswith("styles.css")]) == 2

        # pluginfile.php & HTML MUST remain no-store, private and never cached.
        for _ in range(2):
            with urllib.request.urlopen(plugin_url, timeout=5) as rp:
                assert rp.headers.get("Cache-Control") == "no-store, private"
            with urllib.request.urlopen(html_url, timeout=5) as rh:
                assert rh.headers.get("Cache-Control") == "no-store, private"
        assert len([u for _m, u in seen if "pluginfile.php" in u]) == 2
        assert len([u for _m, u in seen if "view.php" in u]) == 2
    finally:
        server.stop()


def test_local_preview_server_head_does_not_poison_get_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))

    seen: list[tuple[str, str]] = []

    class _FakeClient:
        moodle_site_origin = "https://courses.ut.edu.vn"
        _session_generation = 1

        def open_session_raw(
            self,
            url: str,
            *,
            method: str = "GET",
            body: bytes | None = None,
            **_kwargs,
        ):
            del body
            seen.append((method, url))
            if method.upper() == "HEAD":
                return 200, url, "text/css; charset=utf-8", b""
            return 200, url, "text/css; charset=utf-8", b"body{color:blue;}"

    server = LocalPreviewServer()
    try:
        server.register_session_provider("courses", _FakeClient())
        css_url = server.build_session_bridge_url(
            "courses", "https://courses.ut.edu.vn/theme/styles.css"
        )
        assert css_url is not None

        head_req = urllib.request.Request(css_url, method="HEAD")
        with urllib.request.urlopen(head_req, timeout=5) as head_resp:
            assert head_resp.status == 200
            assert head_resp.headers.get("Cache-Control") == "no-store, private"
            assert head_resp.read() == b""

        with urllib.request.urlopen(css_url, timeout=5) as get_resp1:
            assert get_resp1.status == 200
            assert get_resp1.headers.get("Cache-Control") == "private, max-age=300"
            assert get_resp1.read() == b"body{color:blue;}"

        with urllib.request.urlopen(css_url, timeout=5) as get_resp2:
            assert get_resp2.status == 200
            assert get_resp2.headers.get("Cache-Control") == "private, max-age=300"
            assert get_resp2.read() == b"body{color:blue;}"

        assert seen == [
            ("HEAD", "https://courses.ut.edu.vn/theme/styles.css"),
            ("GET", "https://courses.ut.edu.vn/theme/styles.css"),
        ]
    finally:
        server.stop()


def test_local_preview_server_never_caches_upstream_set_cookie_static_assets(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(settings, "UTH_USERNAME", "student-42")

    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sess-1"
    client._session_owner = "student-42"
    client._session_generation = 1

    upstream_calls = 0

    class _FakeHeaders(dict):
        def get_all(self, name, failobj=None):
            matches = [v for k, v in self.items() if k.lower() == name.lower()]
            return matches if matches else failobj

    class _SetCookieResponse(_FakeRawResponse):
        def __init__(self, url: str, body: bytes):
            super().__init__(url, body, content_type="text/css; charset=utf-8")
            self.headers = _FakeHeaders(
                {
                    "Content-Type": "text/css; charset=utf-8",
                    "Set-Cookie": "MoodleSession=rotated; Path=/; HttpOnly",
                }
            )

    class _SetCookieOpener:
        def open(self, request, timeout=20):
            del timeout
            nonlocal upstream_calls
            upstream_calls += 1
            return _SetCookieResponse(request.full_url, b"body{background:#fff;}")

    client._html_opener = _SetCookieOpener()

    server = LocalPreviewServer()
    try:
        server.register_session_provider("courses", client)
        css_url = server.build_session_bridge_url(
            "courses", "https://courses.ut.edu.vn/theme/sc.css"
        )
        assert css_url is not None

        with urllib.request.urlopen(css_url, timeout=5) as r1:
            assert r1.status == 200
            assert r1.headers.get("Cache-Control") == "no-store, private"
            assert r1.read() == b"body{background:#fff;}"

        with urllib.request.urlopen(css_url, timeout=5) as r2:
            assert r2.status == 200
            assert r2.headers.get("Cache-Control") == "no-store, private"
            assert r2.read() == b"body{background:#fff;}"

        assert upstream_calls == 2
    finally:
        server.stop()


def test_moodle_client_open_session_raw_tracks_redirect_hop_set_cookie(monkeypatch):
    from core.client import _SameMoodleOriginRedirectHandler

    monkeypatch.setattr(settings, "UTH_USERNAME", "student-42")
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sess-1"
    client._session_owner = "student-42"
    client._session_generation = 1

    handler = _SameMoodleOriginRedirectHandler("https://courses.ut.edu.vn")

    class _RedirectThenOkOpener:
        def open(self, request, timeout=20):
            del timeout
            req_out = handler.redirect_request(
                request,
                io.BytesIO(b""),
                302,
                "Found",
                {"Set-Cookie": "MoodleSession=hop-cookie; Path=/"},
                "https://courses.ut.edu.vn/theme/final.css",
            )
            assert req_out is not None
            return _FakeRawResponse(
                req_out.full_url,
                b"body{color:green;}",
                content_type="text/css; charset=utf-8",
            )

    client._html_opener = _RedirectThenOkOpener()
    status, final_url, ct, raw = client.open_session_raw(
        "https://courses.ut.edu.vn/theme/start.css"
    )
    assert status == 200
    assert final_url == "https://courses.ut.edu.vn/theme/final.css"
    assert "text/css" in ct
    assert raw == b"body{color:green;}"
    assert client.pop_last_open_session_has_set_cookie() is True
    assert client.pop_last_open_session_has_set_cookie() is False


def test_prepare_quiz_entry_warms_existing_client_using_client_session_owner(
    monkeypatch,
):
    from http.cookiejar import Cookie

    from core import quiz_entry
    from core.moodle_sites import moodle_site_from_origin
    from core.quiz_player import acquire_quiz_session_lease, build_quiz_launch_target

    monkeypatch.setattr(settings, "UTH_USERNAME", "student-42")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "private-example-password")

    existing = MoodleClient("https://courses.ut.edu.vn")
    existing._session_owner = "student-42"
    existing._sesskey = ""
    existing._session_generation = 1

    target = build_quiz_launch_target(
        "https://courses.ut.edu.vn/mod/quiz/view.php?id=77", "student-42"
    )
    lease = acquire_quiz_session_lease(target.session_key)
    site = moodle_site_from_origin("https://courses.ut.edu.vn")
    assert site is not None

    fresh = MoodleClient(site.origin)
    fresh._session_owner = "student-42"
    fresh._cookie_jar.set_cookie(
        Cookie(
            version=0,
            name="MoodleSession",
            value="fresh-sess",
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
            rest={"HttpOnly": None},
            rfc2109=False,
        )
    )
    op_lease = acquire_quiz_session_lease(target.session_key)

    def fake_open_html(url, *, data=None):
        del url, data
        # Mutate global settings.UTH_USERNAME mid-I/O to simulate a concurrent switch.
        monkeypatch.setattr(settings, "UTH_USERNAME", "student-99")
        return target.url, (
            '<html><body><form method="post" '
            'action="https://courses.ut.edu.vn/mod/quiz/startattempt.php">'
            '<input type="hidden" name="cmid" value="77" />'
            '<input type="hidden" name="sesskey" value="fresh-sk" />'
            '<button type="submit">Start attempt</button></form></body></html>'
        )

    fresh._open_html = fake_open_html
    monkeypatch.setattr(
        quiz_entry,
        "_authenticated_quiz_client",
        lambda _t, _l, **_kw: (fresh, site, op_lease),
    )

    seen_warm_args: list[tuple[str, int | None]] = []
    orig_warm = quiz_entry.warm_existing_client_from_quiz_login

    def spy_warm(
        existing_client_arg,
        quiz_client_arg,
        site_arg,
        account_id,
        html,
        *,
        initial_gen=None,
        fallback_sesskey="",
        replace_stale=False,
    ):
        seen_warm_args.append((account_id, initial_gen))
        return orig_warm(
            existing_client_arg,
            quiz_client_arg,
            site_arg,
            account_id,
            html,
            initial_gen=initial_gen,
            fallback_sesskey=fallback_sesskey,
            replace_stale=replace_stale,
        )

    monkeypatch.setattr(quiz_entry, "warm_existing_client_from_quiz_login", spy_warm)

    prepared = quiz_entry.prepare_quiz_entry(target, lease, existing_client=existing)
    try:
        assert seen_warm_args == [("student-42", 1)]
    finally:
        prepared.close()
        lease.release()
