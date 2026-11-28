"""Zero-Trust Adversarial TDD Suite for PR #134 Core Auth & Session Remediation.

Covers:
1. 100% preservation of `1ce5fbd` Core security controls & Clean Architecture AST rules.
2. BUG-134-02: Eliminate AB-BA deadlock (`_login_lock -> _session_lock`), zero `_session_lock`
   across HTTP I/O, instant `clear_session()` during slow probes, and `<= 2.5s` probe bound.
3. SEC-134-01: `account_epoch` pre/post-flight guards in `UthWebViewAuthService`,
   `expected_generation` & monotonic `_token_generation` in `PortalClient`, and zero
   post-logout token/cookie resurrection across concurrent threads.
4. SEC-134-02: `uthelper_demo` strict zero-egress across `PortalClient.ensure_web_session`,
   `MoodleClient.ensure_live_session` / `get_session_cookies`, and `UthWebViewAuthService`.
5. SEC-134-03: Strict host-only cookie export (`cookie.domain.lstrip('.').casefold() == site_host`,
   `domain=site_host`, `domain_specified=False`; parent `Domain=.ut.edu.vn` rejected) &
   `elearning.ut.edu.vn` canonicalization.
6. SEC-134-04: `PortalClient` strict `https://` + `ut.edu.vn`/`*.ut.edu.vn` + `port is None`
   host pinning at construction and before credential egress.
7. SEC-134-05: `MoodleClient.can_authenticate_html_session()` blocking unstamped `""`
   `UTH_CREDENTIALS_ORIGIN` while permitting verified UTH SSO across `courses`/`thnn` HTML
   sessions (keeping `_get_ws_token()` site-exact), plus HTTP `401/403` revoked-JWT purge.
"""

from __future__ import annotations

import ast
import base64
from datetime import date
from email.message import Message
from http.cookiejar import Cookie
import io
import json
from pathlib import Path
import threading
import time
from typing import Any
import urllib.error
import urllib.request

import pytest

from config import settings
import core.client as client_module
from core.client import MoodleClient
from core.portal_client import (
    PortalAuthenticationError,
    PortalClient,
    PortalError,
)
from core.webview_auth import (
    UthWebViewAuthService,
    WebViewAuthBootstrap,
    WebViewRecoveryTracker,
    normalize_uth_webview_target_url,
)


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


def _reviewer_password() -> str:
    return "UTH-" + "Review-2026!"


def _forbid_network(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("Outbound network call is strictly forbidden in this test")


def _make_cookie(
    name: str,
    value: str,
    domain: str = "courses.ut.edu.vn",
    *,
    domain_specified: bool = False,
    http_only: bool = True,
    same_site: str | None = "Lax",
) -> Cookie:
    rest: dict[str, str | None] = {}
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


def _make_jwt(exp: int, role: str = "sv") -> str:
    hdr = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode("ascii")
    body = (
        base64.urlsafe_b64encode(
            json.dumps({"sub": "2251120001", "exp": exp, "data": {"role": role}}).encode(
                "utf-8"
            )
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    return f"{hdr}.{body}.sig"


class _PortalJsonResponse:
    def __init__(self, payload: dict[str, Any], *, url: str = "") -> None:
        self._raw = json.dumps(payload).encode("utf-8")
        self._url = url
        self.headers = Message()

    def geturl(self) -> str:
        return self._url

    def read(self, amt: int = -1) -> bytes:
        return self._raw if amt < 0 else self._raw[:amt]

    def __enter__(self) -> "_PortalJsonResponse":
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False


class _TrackingRLock:
    """Wrap threading.RLock to detect which thread currently holds `_session_lock`."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._guard = threading.Lock()
        self._owner_tid: int | None = None
        self._depth = 0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        ok = (
            self._lock.acquire(blocking)
            if timeout < 0
            else self._lock.acquire(blocking, timeout)
        )
        if ok:
            with self._guard:
                self._owner_tid = threading.get_ident()
                self._depth += 1
        return ok

    def release(self) -> None:
        with self._guard:
            self._depth -= 1
            if self._depth <= 0:
                self._depth = 0
                self._owner_tid = None
        self._lock.release()

    def __enter__(self) -> "_TrackingRLock":
        self.acquire()
        return self

    def __exit__(self, *_args: Any) -> bool:
        self.release()
        return False

    def is_held_by_current_thread(self) -> bool:
        with self._guard:
            return self._depth > 0 and self._owner_tid == threading.get_ident()


# ---------------------------------------------------------------------------
# 1. Clean Architecture & Shared Contract Verification
# ---------------------------------------------------------------------------


def test_core_webview_auth_and_portal_client_remain_pure_without_config_or_gui_imports():
    for rel in ("core/webview_auth.py", "core/portal_client.py"):
        tree = ast.parse((SRC_ROOT / rel).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {
            imp
            for imp in imported
            if imp in {"config", "gui", "platform_utils", "core", "core.ws_functions"}
            or imp.startswith(("config.", "gui.", "platform_utils.", "core.ws_functions."))
        }
        assert forbidden == set(), f"{rel} has forbidden imports: {forbidden}"


def test_shared_bootstrap_and_service_contract_fields_and_kwargs():
    boot = WebViewAuthBootstrap(
        target_url="https://portal.ut.edu.vn/dashboard",
        site="portal",
        cookies=(),
        portal_init_script="void 0;",
        account_epoch=7,
    )
    assert boot.account_epoch == 7
    assert boot.portal_init_script == "void 0;"
    assert boot.init_script == "void 0;"

    boot_default = WebViewAuthBootstrap(
        target_url="https://courses.ut.edu.vn/my/",
        site="courses",
    )
    assert boot_default.account_epoch is None
    assert boot_default.cookies == ()
    assert boot_default.portal_init_script == ""
    assert boot_default.init_script == ""


# ---------------------------------------------------------------------------
# 2. BUG-134-02: Eliminate AB-BA Deadlock, Lock-Across-HTTP Wedge & >2.5s Probe
# ---------------------------------------------------------------------------


def test_bug_134_02_no_session_lock_held_across_http_or_before_login_lock_and_no_deadlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret-pw")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    client = MoodleClient("https://courses.ut.edu.vn")
    tracking_session_lock = _TrackingRLock()
    client._session_lock = tracking_session_lock
    client._sesskey = "sk_stale"
    client._session_owner = "2251120001"
    client._session_authenticated_at = 0.0
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "stale_val"))

    lock_held_violations: list[str] = []
    probe_entered = threading.Event()
    release_probe = threading.Event()

    class _FakeResp:
        def __init__(self, url: str, body: bytes) -> None:
            self._url = url
            self._stream = io.BytesIO(body)
            self.status = 200
            self.headers = Message()
            self.headers["Content-Type"] = "text/html; charset=utf-8"

        def geturl(self) -> str:
            return self._url

        def info(self) -> Message:
            return self.headers

        def read(self, amt: int = -1) -> bytes:
            return self._stream.read() if amt < 0 else self._stream.read(amt)

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *_a: Any) -> bool:
            return False

    def _build_opener(*handlers: Any) -> Any:
        jar = next((h.cookiejar for h in handlers if hasattr(h, "cookiejar")), None)

        class _Opener:
            def open(self, req: urllib.request.Request, timeout: float = 20) -> _FakeResp:
                if tracking_session_lock.is_held_by_current_thread():
                    lock_held_violations.append(f"HTTP while holding _session_lock: {req.full_url}")
                if "service.php" in req.full_url:
                    if timeout > 2.5:
                        lock_held_violations.append(f"AJAX timeout {timeout} > 2.5s")
                    probe_entered.set()
                    release_probe.wait(timeout=2.0)
                    payload = [
                        {
                            "error": True,
                            "errorcode": "requireloginerror",
                            "message": "Session expired",
                        }
                    ]
                    return _FakeResp(req.full_url, json.dumps(payload).encode("utf-8"))
                if "/login/index.php" in req.full_url and req.get_method() == "GET":
                    return _FakeResp(
                        req.full_url,
                        b'<form><input name="logintoken" value="lt1"/></form>',
                    )
                if "/login/index.php" in req.full_url and req.get_method() == "POST":
                    if jar is not None:
                        jar.set_cookie(_make_cookie("MoodleSession", "fresh_val"))
                    return _FakeResp(
                        "https://courses.ut.edu.vn/my/",
                        b'<script>M.cfg={"sesskey":"skFresh123"};</script>',
                    )
                return _FakeResp("https://courses.ut.edu.vn/my/", b"<html/>")

        return _Opener()

    monkeypatch.setattr("core.client.urllib.request.build_opener", _build_opener)

    service = UthWebViewAuthService(
        courses_client=client,
        credentials_provider=lambda: ("2251120001", "secret-pw"),
    )

    # 1. Start verify_live cookie bootstrap in worker; while HTTP probe is blocked,
    #    clear_session() on main thread MUST complete immediately without blocking!
    worker_done = threading.Event()
    worker_cookies: list[list[dict[str, Any]]] = []

    def _run_verify() -> None:
        try:
            worker_cookies.append(
                client.get_session_cookies(ensure_login=True, verify_live=True)
            )
        finally:
            worker_done.set()

    t_verify = threading.Thread(target=_run_verify, daemon=True)
    t_verify.start()
    assert probe_entered.wait(timeout=1.5)

    t0 = time.perf_counter()
    # Non-blocking acquire check from main thread proves _session_lock is NOT held during HTTP!
    acquired_main = tracking_session_lock.acquire(blocking=False)
    assert acquired_main is True, "_session_lock was held across HTTP probe!"
    tracking_session_lock.release()
    assert (time.perf_counter() - t0) < 0.05

    release_probe.set()
    t_verify.join(timeout=2.0)
    assert worker_done.is_set()
    assert [c["value"] for c in worker_cookies[0]] == ["fresh_val"]
    assert lock_held_violations == []

    # 2. AB-BA torture test: concurrent login_session(force=True) vs recover_from_login_redirect()
    errors: list[BaseException] = []
    stop_at = time.perf_counter() + 0.4

    def _hammer_login() -> None:
        try:
            while time.perf_counter() < stop_at:
                client.login_session(force=True)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    def _hammer_recovery() -> None:
        try:
            while time.perf_counter() < stop_at:
                tracker = WebViewRecoveryTracker()
                service.recover_from_login_redirect(
                    "https://courses.ut.edu.vn/login/index.php", tracker
                )
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        threading.Thread(target=_hammer_login, daemon=True),
        threading.Thread(target=_hammer_recovery, daemon=True),
        threading.Thread(target=_hammer_recovery, daemon=True),
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=3.0)
        assert not th.is_alive(), "AB-BA deadlock detected between login_session and WebView recovery!"
    assert errors == []
    assert lock_held_violations == []


def test_bug_134_02_ensure_live_session_enforces_2_5s_combined_probe_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret-pw")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sk_old"
    client._session_owner = "2251120001"
    client._session_authenticated_at = 0.0
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "existing_cookie"))

    observed_timeouts: list[tuple[str, float]] = []

    class _SlowOpener:
        def open(self, req: urllib.request.Request, timeout: float = 20) -> Any:
            observed_timeouts.append((req.full_url, float(timeout)))
            # Simulate AJAX probe taking 1.2s and failing, leaving <= 1.3s for /my/ fallback
            if "service.php" in req.full_url:
                time.sleep(min(0.15, float(timeout)))
                raise TimeoutError("AJAX slow")
            time.sleep(min(0.10, float(timeout)))
            raise TimeoutError("HTML slow")

    monkeypatch.setattr(client, "_ensure_html_opener", lambda: _SlowOpener())

    t0 = time.perf_counter()
    alive = client.ensure_live_session()
    elapsed = time.perf_counter() - t0

    assert alive is True  # Transient timeout preserves existing MoodleSession cookie
    assert elapsed < 2.5
    assert len(observed_timeouts) >= 1
    for url, req_timeout in observed_timeouts:
        assert 0.0 < req_timeout <= 2.5, f"Probe {url} used unbounded timeout {req_timeout}"
    if len(observed_timeouts) == 2:
        # Second fallback timeout must strictly account for time already spent in first probe!
        assert observed_timeouts[1][1] < observed_timeouts[0][1]


# ---------------------------------------------------------------------------
# 3. SEC-134-01: Epoch Guard & Zero Post-Logout Token/Cookie Resurrection
# ---------------------------------------------------------------------------


def test_sec_134_01_portal_mid_flight_logout_and_epoch_bump_discards_jwt_everywhere():
    epoch = 10
    login_in_flight = threading.Event()
    allow_login_finish = threading.Event()
    late_jwt = _make_jwt(int(time.time()) + 7200, role="sv")

    def opener(request: urllib.request.Request, timeout: float) -> _PortalJsonResponse:
        del timeout
        assert request.full_url.endswith("/user/login")
        login_in_flight.set()
        assert allow_login_finish.wait(timeout=2.0)
        return _PortalJsonResponse(
            {"success": True, "body": "sv", "token": late_jwt},
            url=request.full_url,
        )

    portal = PortalClient("https://portal.ut.edu.vn/api/v1", opener=opener)
    service = UthWebViewAuthService(
        portal_client=portal,
        credentials_provider=lambda: ("studentA", "passA"),
        account_epoch_getter=lambda: epoch,
    )

    results: list[WebViewAuthBootstrap] = []
    worker = threading.Thread(
        target=lambda: results.append(
            service.prepare_initial_auth("https://portal.ut.edu.vn/")
        ),
        daemon=True,
    )
    worker.start()
    assert login_in_flight.wait(timeout=1.5)

    # Main thread logs out Student A while Portal HTTP login is on the wire!
    epoch += 1
    portal.clear_token()
    allow_login_finish.set()
    worker.join(timeout=2.0)

    assert len(results) == 1
    boot = results[0]
    assert boot.account_epoch == 10
    assert boot.portal_init_script == ""
    assert boot.cookies == ()
    assert boot.target_url == "https://portal.ut.edu.vn/"  # Not rewritten to /dashboard!
    assert portal._cached_token("studentA") == ""
    assert portal._token_role == ""


def test_sec_134_01_moodle_mid_flight_logout_and_epoch_bump_blocks_cookie_resurrection_and_sibling_warm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    epoch = 20
    monkeypatch.setattr(settings, "UTH_USERNAME", "studentA")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "passA")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    courses_client = MoodleClient("https://courses.ut.edu.vn")
    courses_client._sesskey = "sk_a"
    courses_client._session_owner = "studentA"
    courses_client._session_authenticated_at = 0.0
    courses_client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "cookie_a"))

    sibling_warm_calls: list[str] = []

    class _SpySiblingClient:
        def get_session_cookies(self, **_kw: Any) -> list[dict[str, Any]]:
            sibling_warm_calls.append("warmed")
            return [{"name": "MoodleSession", "value": "thnn_a", "domain": "thnn.ut.edu.vn"}]

    probe_started = threading.Event()
    release_probe = threading.Event()
    relogin_called: list[bool] = []

    def _slow_expired_ajax(*_a: Any, **_kw: Any) -> dict[str, Any]:
        probe_started.set()
        assert release_probe.wait(timeout=2.0)
        return {"error": True, "errorcode": "requireloginerror", "message": "Expired"}

    def _spy_login_session(*_a: Any, **_kw: Any) -> bool:
        relogin_called.append(True)
        return True

    monkeypatch.setattr(courses_client, "call_ajax_api", _slow_expired_ajax)
    monkeypatch.setattr(courses_client, "login_session", _spy_login_session)

    service = UthWebViewAuthService(
        courses_client=courses_client,
        thnn_client=_SpySiblingClient(),
        credentials_provider=lambda: (settings.UTH_USERNAME, settings.UTH_PASSWORD),
        account_epoch_getter=lambda: epoch,
    )

    results: list[WebViewAuthBootstrap] = []
    worker = threading.Thread(
        target=lambda: results.append(
            service.prepare_initial_auth("https://courses.ut.edu.vn/login/index.php")
        ),
        daemon=True,
    )
    worker.start()
    assert probe_started.wait(timeout=1.5)

    # Main thread logs out (or switches account) while Student A's liveness probe is in flight!
    epoch += 1
    courses_client.clear_session()
    monkeypatch.setattr(settings, "UTH_USERNAME", "")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")

    release_probe.set()
    worker.join(timeout=2.0)

    assert len(results) == 1
    boot = results[0]
    assert boot.account_epoch == 20
    assert boot.cookies == ()
    assert boot.target_url == "https://courses.ut.edu.vn/login/index.php"
    assert sibling_warm_calls == []
    # In-flight probe after clear_session() MUST NOT even attempt login_session(force=True)!
    assert relogin_called == []
    assert courses_client.get_session_cookies(ensure_login=False) == []


def test_sec_134_01_pre_flight_expected_epoch_mismatch_and_recovery_epoch_guard():
    cred_reads = 0
    client_calls = 0

    def _creds() -> tuple[str, str]:
        nonlocal cred_reads
        cred_reads += 1
        return ("studentB", "secretB")

    class _Client:
        def get_session_cookies(self, **_kw: Any) -> list[dict[str, Any]]:
            nonlocal client_calls
            client_calls += 1
            return [{"name": "MoodleSession", "value": "val", "domain": "courses.ut.edu.vn"}]

    epoch = 5
    svc = UthWebViewAuthService(
        courses_client=_Client(),
        credentials_provider=_creds,
        account_epoch_getter=lambda: epoch,
    )

    # Caller passes stale expected_epoch=4 when current epoch is already 5
    boot = svc.prepare_initial_auth(
        "https://courses.ut.edu.vn/login/index.php", expected_epoch=4
    )
    assert boot.account_epoch == 4
    assert boot.cookies == ()
    assert boot.target_url == "https://courses.ut.edu.vn/login/index.php"
    assert cred_reads == 0
    assert client_calls == 0

    tracker = WebViewRecoveryTracker()
    recovered = svc.recover_from_login_redirect(
        "https://courses.ut.edu.vn/login/index.php",
        tracker=tracker,
        expected_epoch=4,
    )
    assert recovered is None
    assert tracker.can_attempt_recovery("courses") is True
    assert cred_reads == 0
    assert client_calls == 0

    # Mid-flight epoch bump during recover_from_login_redirect refunds budget & drops cookies
    class _MidFlightEpochBumpClient:
        def get_session_cookies(self, **_kw: Any) -> list[dict[str, Any]]:
            nonlocal epoch
            epoch += 1
            return [{"name": "MoodleSession", "value": "renewed", "domain": "courses.ut.edu.vn"}]

    svc2 = UthWebViewAuthService(
        courses_client=_MidFlightEpochBumpClient(),
        credentials_provider=lambda: ("studentA", "secretA"),
        account_epoch_getter=lambda: epoch,
    )
    tracker2 = WebViewRecoveryTracker()
    assert (
        svc2.recover_from_login_redirect(
            "https://courses.ut.edu.vn/login/index.php",
            tracker=tracker2,
            expected_epoch=epoch,
        )
        is None
    )
    assert tracker2.can_attempt_recovery("courses") is True


def test_sec_134_01_portal_ensure_web_session_expected_generation_aborts_after_clear():
    http_calls = 0

    def opener(req: urllib.request.Request, timeout: float) -> _PortalJsonResponse:
        nonlocal http_calls
        del timeout
        http_calls += 1
        return _PortalJsonResponse(
            {"success": True, "body": "sv", "token": _make_jwt(int(time.time()) + 3600)},
            url=req.full_url,
        )

    portal = PortalClient("https://portal.ut.edu.vn/api/v1", opener=opener)
    gen0 = portal._current_generation()
    portal.clear_token()

    tok, role = portal.ensure_web_session(
        "studentA", "passA", expected_generation=gen0
    )
    assert (tok, role) == ("", "")
    assert http_calls == 0
    assert portal._cached_token("studentA") == ""


def test_sec_134_01_mid_flight_switch_from_student_a_to_student_b_preserves_b_and_drops_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    epoch = 100
    monkeypatch.setattr(settings, "UTH_USERNAME", "studentA")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "passA")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    login_a_started = threading.Event()
    allow_login_a_finish = threading.Event()
    jwt_a = _make_jwt(int(time.time()) + 7200, role="sv")
    jwt_b = _make_jwt(int(time.time()) + 7200, role="gv")

    def opener(req: urllib.request.Request, timeout: float) -> _PortalJsonResponse:
        del timeout
        body = json.loads(req.data.decode("utf-8"))
        if body["username"] == "studentA":
            login_a_started.set()
            assert allow_login_a_finish.wait(timeout=2.0)
            return _PortalJsonResponse(
                {"success": True, "body": "sv", "token": jwt_a}, url=req.full_url
            )
        return _PortalJsonResponse(
            {"success": True, "body": "gv", "token": jwt_b}, url=req.full_url
        )

    portal = PortalClient("https://portal.ut.edu.vn/api/v1", opener=opener)
    svc = UthWebViewAuthService(
        portal_client=portal,
        credentials_provider=lambda: (settings.UTH_USERNAME, settings.UTH_PASSWORD),
        account_epoch_getter=lambda: epoch,
    )

    out_a: list[WebViewAuthBootstrap] = []
    t_a = threading.Thread(
        target=lambda: out_a.append(
            svc.prepare_initial_auth("https://portal.ut.edu.vn/tuition")
        ),
        daemon=True,
    )
    t_a.start()
    assert login_a_started.wait(timeout=1.5)

    # Switch to Student B and establish Student B's token BEFORE Student A's HTTP returns!
    epoch += 1
    portal.clear_token()
    monkeypatch.setattr(settings, "UTH_USERNAME", "studentB")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "passB")
    boot_b = svc.prepare_initial_auth("https://portal.ut.edu.vn/tuition")
    assert boot_b.account_epoch == 101
    assert json.dumps(jwt_b) in boot_b.portal_init_script

    allow_login_a_finish.set()
    t_a.join(timeout=2.0)

    assert len(out_a) == 1
    assert out_a[0].account_epoch == 100
    assert out_a[0].portal_init_script == ""
    assert portal._cached_token("studentA") == ""
    assert portal._cached_token("studentB") == jwt_b
    assert portal._token_role == "gv"


# ---------------------------------------------------------------------------
# 4. SEC-134-02: `uthelper_demo` Strict Zero-Egress
# ---------------------------------------------------------------------------


def test_sec_134_02_demo_account_strict_zero_egress_across_portal_moodle_and_webview_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pw = _reviewer_password()
    monkeypatch.setattr("core.client._urlopen", _forbid_network)
    monkeypatch.setattr(urllib.request, "build_opener", _forbid_network)
    monkeypatch.setattr(urllib.request, "urlopen", _forbid_network)

    monkeypatch.setattr(settings, "UTH_USERNAME", "  UTHelper_Demo ")
    monkeypatch.setattr(settings, "UTH_PASSWORD", pw)
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    portal = PortalClient(
        "https://portal-publicapp.ut.edu.vn/api/v1", opener=_forbid_network
    )
    assert portal.ensure_web_session("uthelper_demo", pw) == ("", "")
    assert portal.ensure_web_session("  UTHelper_Demo ", pw, force=True) == ("", "")
    assert portal._cached_token("uthelper_demo") == ""

    courses = MoodleClient("https://courses.ut.edu.vn")
    thnn = MoodleClient("https://thnn.ut.edu.vn")
    assert courses.can_authenticate_html_session() is False
    assert thnn.can_authenticate_html_session() is False
    assert courses.ensure_live_session() is False
    assert courses.get_session_cookies(ensure_login=True, verify_live=True) == []
    assert courses.get_session_cookies(force_relogin=True) == []

    svc = UthWebViewAuthService(
        courses_client=courses,
        thnn_client=thnn,
        portal_client=portal,
        credentials_provider=lambda: (settings.UTH_USERNAME, settings.UTH_PASSWORD),
        account_epoch_getter=lambda: 1,
    )
    for site in ("courses", "thnn", "portal", None):
        assert svc.has_credentials(site) is False  # type: ignore[arg-type]

    for url in (
        "https://courses.ut.edu.vn/login/index.php",
        "https://thnn.ut.edu.vn/course/view.php?id=10",
        "https://portal.ut.edu.vn/",
    ):
        boot = svc.prepare_initial_auth(url)
        assert boot.cookies == ()
        assert boot.portal_init_script == ""
        assert boot.account_epoch == 1

    tracker = WebViewRecoveryTracker()
    for login_url in (
        "https://courses.ut.edu.vn/login/index.php",
        "https://thnn.ut.edu.vn/login/index.php",
        "https://portal.ut.edu.vn/login",
    ):
        assert svc.recover_from_login_redirect(login_url, tracker) is None
    assert tracker.can_attempt_recovery("courses") is True
    assert tracker.can_attempt_recovery("thnn") is True
    assert tracker.can_attempt_recovery("portal") is True


# ---------------------------------------------------------------------------
# 5. SEC-134-03: Strict Host-Only Cookies & `elearning.ut.edu.vn` Canonicalization
# ---------------------------------------------------------------------------


def test_sec_134_03_get_session_cookies_filters_to_exact_host_and_strips_parent_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "sk1"
    client._session_owner = "2251120001"

    # Must REJECT parent-domain .ut.edu.vn, sibling thnn.ut.edu.vn, off-origin, and empty domain
    client._cookie_jar.set_cookie(
        _make_cookie("ParentDot", "leak1", domain=".ut.edu.vn", domain_specified=True)
    )
    client._cookie_jar.set_cookie(
        _make_cookie("ParentBare", "leak2", domain="ut.edu.vn", domain_specified=False)
    )
    client._cookie_jar.set_cookie(
        _make_cookie("SiblingLeak", "leak3", domain="thnn.ut.edu.vn", domain_specified=True)
    )
    client._cookie_jar.set_cookie(
        _make_cookie("EvilLeak", "leak4", domain="evil.example.com", domain_specified=True)
    )
    client._cookie_jar.set_cookie(
        _make_cookie("EmptyDomain", "leak5", domain="", domain_specified=False)
    )
    # Must ACCEPT exact site host (even with leading dot or uppercase) as host-only!
    client._cookie_jar.set_cookie(
        _make_cookie(
            "MoodleSession",
            "host_only_ok",
            domain=".COURSES.UT.EDU.VN",
            domain_specified=True,
        )
    )

    exported = client.get_session_cookies(ensure_login=False)
    assert len(exported) == 1
    assert exported[0]["name"] == "MoodleSession"
    assert exported[0]["value"] == "host_only_ok"
    assert exported[0]["domain"] == "courses.ut.edu.vn"
    assert exported[0]["domain_specified"] is False

    # Untrusted MoodleClient never exports cookies
    untrusted = MoodleClient("https://evil.example.com")
    untrusted._cookie_jar.set_cookie(
        _make_cookie("MoodleSession", "x", domain="evil.example.com")
    )
    assert untrusted.get_session_cookies(ensure_login=False) == []

    # Canonicalization of elearning.ut.edu.vn
    assert (
        normalize_uth_webview_target_url(
            "https://elearning.ut.edu.vn/mod/assign/view.php?id=5",
            has_credentials=False,
        )
        == "https://courses.ut.edu.vn/mod/assign/view.php?id=5"
    )


# ---------------------------------------------------------------------------
# 6. SEC-134-04: `PortalClient` Strict `*.ut.edu.vn` Host Pinning
# ---------------------------------------------------------------------------


def test_sec_134_04_portal_client_enforces_strict_uth_https_host_pinning_init_and_pre_send():
    for untrusted_base in (
        "https://evil.com/api/v1",
        "https://ut.edu.vn.evil.com/api/v1",
        "https://fakeut.edu.vn/api/v1",
        "https://portal-publicapp.ut.edu.vn:8443/api/v1",
        "https://portal.ut.edu.vn:443/api/v1",
        "https://user@portal.ut.edu.vn/api/v1",
        "https://:pass@portal.ut.edu.vn/api/v1",
        "https://portal.ut.edu.vn/api/v1?",
        "https://portal.ut.edu.vn/api/v1#",
        "https://portal.ut.edu.vn\\api\\v1",
        "http://127.0.0.1:8000/api/v1",
    ):
        with pytest.raises(ValueError):
            PortalClient(untrusted_base)

    # Trusted UTH hosts succeed
    for trusted_base in (
        "https://ut.edu.vn/api/v1",
        "https://portal.ut.edu.vn/api/v1",
        "https://portal-publicapp.ut.edu.vn/api/v1",
    ):
        c = PortalClient(trusted_base, opener=_forbid_network)
        assert c._base_url == trusted_base

    # Post-init attribute tampering is ALSO blocked before opener ever sees UTH_PASSWORD
    tampered = PortalClient(
        "https://portal.ut.edu.vn/api/v1", opener=_forbid_network
    )
    tampered._base_url = "https://evil.com/api/v1"
    with pytest.raises(PortalError):
        tampered.ensure_web_session("2251120001", "top-secret-password")
    with pytest.raises(PortalError):
        tampered.fetch_day(date(2026, 8, 11), "2251120001", "top-secret-password")


# ---------------------------------------------------------------------------
# 7. SEC-134-05: `can_authenticate_html_session()` & `401/403` Revoked-JWT Purge
# ---------------------------------------------------------------------------


def test_sec_134_05_can_authenticate_html_session_blocks_unstamped_origin_but_allows_verified_thnn_sso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.client._urlopen", _forbid_network)
    monkeypatch.setattr(urllib.request, "build_opener", _forbid_network)

    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "verified-pw")

    courses = MoodleClient("https://courses.ut.edu.vn")
    thnn = MoodleClient("https://thnn.ut.edu.vn")

    svc = UthWebViewAuthService(
        courses_client=courses,
        thnn_client=thnn,
        credentials_provider=lambda: (settings.UTH_USERNAME, settings.UTH_PASSWORD),
    )

    # 1. Unstamped UTH_CREDENTIALS_ORIGIN == "" or untrusted origin -> BLOCKED on both!
    for bad_origin in ("", "https://evil.example.com", "http://courses.ut.edu.vn"):
        monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", bad_origin)
        assert courses.can_authenticate_html_session() is False
        assert thnn.can_authenticate_html_session() is False
        assert svc.has_credentials("courses") is False
        assert svc.has_credentials("thnn") is False

        boot_thnn = svc.prepare_initial_auth("https://thnn.ut.edu.vn/login/index.php")
        assert boot_thnn.cookies == ()
        assert boot_thnn.target_url == "https://thnn.ut.edu.vn/login/index.php"
        assert (
            svc.recover_from_login_redirect(
                "https://thnn.ut.edu.vn/login/index.php", WebViewRecoveryTracker()
            )
            is None
        )

    # 2. Verified UTH_CREDENTIALS_ORIGIN == "https://courses.ut.edu.vn" ->
    #    HTML session allowed on BOTH courses & thnn, while _get_ws_token stays site-exact!
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "")
    assert courses.can_authenticate_html_session() is True
    assert thnn.can_authenticate_html_session() is True
    assert svc.has_credentials("courses") is True
    assert svc.has_credentials("thnn") is True

    # Site-exact WS token boundary on THNN remains strictly enforced!
    assert thnn._stored_credentials_match_site() is False
    assert thnn._get_ws_token() == ""
    assert thnn.last_login_error == "credentials_site_mismatch"


@pytest.mark.parametrize("http_status", [401, 403])
def test_sec_134_05_portal_401_and_403_purge_revoked_jwt_and_raise_auth_error(
    http_status: int,
) -> None:
    def opener(req: urllib.request.Request, timeout: float) -> _PortalJsonResponse:
        del timeout
        raise urllib.error.HTTPError(
            req.full_url, http_status, "Auth rejected", Message(), None
        )

    portal = PortalClient("https://portal.ut.edu.vn/api/v1", opener=opener)
    revoked_jwt = _make_jwt(int(time.time()) + 86400, role="gv")
    portal._store_token(revoked_jwt, "2251120001", role="gv")
    assert portal._cached_token("2251120001") == revoked_jwt

    with pytest.raises(PortalAuthenticationError) as exc_info:
        portal.ensure_web_session("2251120001", "revoked-pw", force=True)
    assert exc_info.value.status == http_status
    # Revoked JWT & role MUST be immediately purged!
    assert portal._cached_token("2251120001") == ""
    assert portal._token_role == ""

    # Subsequent force=False call MUST NOT replay the purged JWT!
    with pytest.raises(PortalAuthenticationError):
        portal.ensure_web_session("2251120001", "revoked-pw", force=False)

    # Stale generation 401/403 must NOT wipe a newer valid token from another login!
    fresh_jwt = _make_jwt(int(time.time()) + 86400, role="sv")
    stale_gen = portal._current_generation()
    portal._store_token(fresh_jwt, "student2", role="sv")
    with pytest.raises(PortalAuthenticationError):
        portal._login_with_generation("2251120001", "revoked-pw", stale_gen)
    assert portal._cached_token("student2") == fresh_jwt
    assert portal._token_role == "sv"


# ---------------------------------------------------------------------------
# 8. Critic Round 1 Adversarial Challenges (PoCs 1..4)
# ---------------------------------------------------------------------------


def test_challenge_1_same_user_concurrent_fetch_day_and_portal_webview_bootstrap_succeeds():
    """Concurrent background fetch_day for the SAME student must never break Portal WebView auto-login."""
    now = int(time.time())
    jwt_day = _make_jwt(now + 7200, role="sv")
    jwt_web = _make_jwt(now + 7200, role="sv")

    web_entered = threading.Event()
    day_finished = threading.Event()

    def opener(req: urllib.request.Request, timeout: float) -> _PortalJsonResponse:
        del timeout
        if req.full_url.endswith("/user/login"):
            # First login is WebView, which pauses while background fetch_day completes login!
            if not web_entered.is_set():
                web_entered.set()
                assert day_finished.wait(timeout=2.0)
                return _PortalJsonResponse(
                    {"success": True, "body": "sv", "token": jwt_web},
                    url=req.full_url,
                )
            return _PortalJsonResponse(
                {"success": True, "token": jwt_day},
                url=req.full_url,
            )
        return _PortalJsonResponse({"success": True, "body": []}, url=req.full_url)

    portal = PortalClient("https://portal.ut.edu.vn/api/v1", opener=opener)
    epoch = 1
    svc = UthWebViewAuthService(
        portal_client=portal,
        credentials_provider=lambda: ("studentA", "passA"),
        account_epoch_getter=lambda: epoch,
    )

    web_results: list[WebViewAuthBootstrap] = []
    t_web = threading.Thread(
        target=lambda: web_results.append(
            svc.prepare_initial_auth("https://portal.ut.edu.vn/")
        ),
        daemon=True,
    )
    t_web.start()
    assert web_entered.wait(timeout=1.5)

    # Background schedule sync logs in for the SAME studentA while WebView login is in flight
    assert portal.fetch_day(date(2026, 8, 11), "studentA", "passA") == ()
    day_finished.set()
    t_web.join(timeout=2.0)

    assert len(web_results) == 1
    boot = web_results[0]
    assert boot.target_url == "https://portal.ut.edu.vn/dashboard"
    assert boot.portal_init_script != ""
    assert portal._cached_token("studentA") != ""

    # Also test pre-lock bump: background fetch_day finishes right after _read_portal_generation
    portal2 = PortalClient(
        "https://portal.ut.edu.vn/api/v1",
        opener=lambda req, timeout: _PortalJsonResponse(
            {"success": True, "body": "sv", "token": jwt_day}
            if req.full_url.endswith("/user/login")
            else {"success": True, "body": []},
            url=req.full_url,
        ),
    )
    gen_before_bg = portal2._current_generation()
    assert portal2.fetch_day(date(2026, 8, 11), "studentA", "passA") == ()
    tok, role = portal2.ensure_web_session(
        "studentA", "passA", expected_generation=gen_before_bg
    )
    assert tok == jwt_day
    assert role == "sv"


def test_challenge_2_clear_session_before_call_ajax_api_never_triggers_auto_login_or_html_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If clear_session() runs right before call_ajax_api inside ensure_live_session, 0 logins & 0 HTTP calls occur."""
    monkeypatch.setattr(settings, "UTH_USERNAME", "studentA")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "passA")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "skAlive1"
    client._session_owner = "studentA"
    client._session_authenticated_at = 0.0
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "cookie_a"))

    login_calls: list[str] = []
    http_urls: list[str] = []

    monkeypatch.setattr(
        client,
        "login_session",
        lambda *_a, **_kw: login_calls.append("login") or True,
    )

    class _SpyOpener:
        def open(self, req: urllib.request.Request, timeout: float = 20) -> Any:
            del timeout
            http_urls.append(req.full_url)
            raise AssertionError(f"Unexpected post-clear HTTP request: {req.full_url}")

    monkeypatch.setattr(client, "_ensure_html_opener", lambda: _SpyOpener())

    real_call_ajax = client.call_ajax_api

    def _intercept_call_ajax(function: str, **params: Any) -> Any:
        # Simulate Logout clear_session() racing right after ensure_live_session() released _session_lock
        # and right before call_ajax_api acquires _session_lock!
        client.clear_session()
        return real_call_ajax(function, **params)

    monkeypatch.setattr(client, "call_ajax_api", _intercept_call_ajax)

    assert client.ensure_live_session() is False
    assert login_calls == []
    assert http_urls == []
    assert client.get_sesskey() == ""
    assert client.get_session_cookies(ensure_login=False) == []


def test_challenge_3_concurrent_last_login_error_mutation_never_breaks_quiz_lease_guard_or_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent threads mutating client._last_login_error must not break quiz_session_active refund or leak."""
    from core.moodle_sites import COURSES_MOODLE_SITE
    from core.quiz_player import acquire_quiz_session_lease, quiz_session_key

    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "skQuiz1"
    client._session_owner = "2251120001"
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "quiz_cookie_1"))

    svc = UthWebViewAuthService(
        courses_client=client,
        credentials_provider=lambda: ("2251120001", "secret"),
    )

    # Acquire quiz lease AFTER pre-check inside recover_from_login_redirect (or during login_session),
    # AND concurrently clobber client._last_login_error = "" from another thread!
    lease_key = quiz_session_key(COURSES_MOODLE_SITE, "2251120001")
    real_login_session = client.login_session
    held_leases: list[Any] = []

    def _racing_login_session(*args: Any, **kwargs: Any) -> bool:
        lease = acquire_quiz_session_lease(lease_key)
        held_leases.append(lease)
        res = real_login_session(*args, **kwargs)
        # Another thread (e.g. background _get_ws_token or another get_session_cookies) clobbers _last_login_error!
        t_clobber = threading.Thread(
            target=lambda: setattr(client, "_last_login_error", "")
        )
        t_clobber.start()
        t_clobber.join()
        return res

    monkeypatch.setattr(client, "login_session", _racing_login_session)
    try:
        tracker = WebViewRecoveryTracker()
        recovered = svc.recover_from_login_redirect(
            "https://courses.ut.edu.vn/login/index.php", tracker
        )
        assert recovered is None
        # Recovery budget MUST still be refunded!
        assert tracker.can_attempt_recovery("courses") is True
    finally:
        for lease in held_leases:
            lease.release()


def test_challenge_4_malformed_uth_hosts_and_non_moodle_cookie_domains_rejected():
    from core.webview_auth import _normalize_cookie_dicts

    # 4a: Reject leading/double/trailing dots and empty trailing-colon port in PortalClient
    for malformed in (
        "https://.ut.edu.vn/api/v1",
        "https://a..ut.edu.vn/api/v1",
        "https://portal.ut.edu.vn./api/v1",
        "https://portal.ut.edu.vn:/api/v1",
        "https://-portal.ut.edu.vn/api/v1",
        "https://portal-.ut.edu.vn/api/v1",
    ):
        with pytest.raises(ValueError):
            PortalClient(malformed)

    # 4b: _normalize_cookie_dicts canonicalizes elearning.ut.edu.vn -> courses.ut.edu.vn
    #     and rejects .edu.vn, portal.ut.edu.vn, evil.com, .ut.edu.vn!
    raw_cookies = [
        {"name": "C1", "value": "v1", "domain": ".edu.vn"},
        {"name": "C2", "value": "v2", "domain": "portal.ut.edu.vn"},
        {"name": "C3", "value": "v3", "domain": "evil.com"},
        {"name": "C4", "value": "v4", "domain": ".ut.edu.vn"},
        {"name": "MoodleSession", "value": "ok_alias", "domain": ".ELEARNING.UT.EDU.VN"},
        {"name": "MoodleTHNN", "value": "ok_thnn", "domain": "thnn.ut.edu.vn"},
    ]
    normalized = _normalize_cookie_dicts(
        raw_cookies, default_domain="courses.ut.edu.vn"
    )
    assert [(c["name"], c["domain"], c["domain_specified"]) for c in normalized] == [
        ("MoodleSession", "courses.ut.edu.vn", False),
        ("MoodleTHNN", "thnn.ut.edu.vn", False),
    ]
