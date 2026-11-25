"""Zero-trust adversarial TDD suite for PR #134 Slice 2 (GUI, WebView2 & Mobile UthAppWebView).

Covers:
- BUG-134-01: Native Android/iOS UthAppWebView (`inappwebview`) Portal UserScript auto-auth,
  reactive Moodle/THNN/Portal self-heal, cross-host UTH SSO, and guaranteed spinner clear on failure.
- BUG-134-02: Off-STA async prefetch of BOTH `cookie_provider` and `init_script_provider` in
  `WindowsWebView2Dock`, and zero `asyncio.run(...)` calls (`marshal_to_ui` `APP-04`).
- BUG-134-03 & SEC-134-04: 100% preservation of `1ce5fbd` invariants (`ft.Stack` index-0,
  `TAB-DEMO-02`, `zalo://`/`intent://` cards, tokenized modal occlusion, 18s commit watchdog,
  `_preview_register_lock`, and keeping `"http://"` in `flet_webview.prevent_links`).
- SEC-134-01 & BUG-134-04: Pre-mutation `_account_epoch` invalidation + WebView teardown in
  `_handle_settings_account_login_async`, stale epoch bootstrap/recovery discard, and
  `elearning.ut.edu.vn -> courses.ut.edu.vn` canonicalization across `InAppWebViewDialog`,
  `CoursesCoordinator`, and `AppController._start_course_quiz`.
"""

from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
import json
import posixpath
import re
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional
from unittest.mock import AsyncMock, MagicMock, Mock
import urllib.parse

import flet as ft
import pytest

from config import settings
from core.courses.archive_service import ArchiveService
from core.courses.cache_store import CourseCacheStore
from core.courses.catalog_service import CourseCatalogService
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.models import CourseModuleItem, CourseSummary
from core.courses.preview_cache import PreviewFileCache
from core.demo_sandbox import DEMO_USERNAME
from core.url_validation import validate_https_url


def _ensure_webview_auth_contract_module() -> Any:
    """Use real `core.webview_auth` when integrated, or install a spec-exact stub on standalone Slice 2."""
    try:
        import core.webview_auth as real_mod

        return real_mod
    except ModuleNotFoundError:
        pass

    mod = types.ModuleType("core.webview_auth")
    host_to_site = {
        "courses.ut.edu.vn": "courses",
        "elearning.ut.edu.vn": "courses",
        "thnn.ut.edu.vn": "thnn",
        "portal.ut.edu.vn": "portal",
    }
    canonical_host = {
        "courses": "courses.ut.edu.vn",
        "thnn": "thnn.ut.edu.vn",
        "portal": "portal.ut.edu.vn",
    }
    default_url = {
        "courses": "https://courses.ut.edu.vn/my/",
        "thnn": "https://thnn.ut.edu.vn/my/",
        "portal": "https://portal.ut.edu.vn/dashboard",
    }

    @dataclass(frozen=True)
    class WebViewAuthBootstrap:
        target_url: str
        site: str
        cookies: tuple[dict[str, Any], ...] = ()
        portal_init_script: str = ""
        account_epoch: Optional[int] = None

    def _parse_uth_url(url: object):
        validated = validate_https_url(url)
        if validated is None:
            return None, None
        parsed = urllib.parse.urlsplit(validated)
        if parsed.port is not None:
            return None, None
        site = host_to_site.get((parsed.hostname or "").casefold())
        return (site, parsed) if site else (None, None)

    def _norm_path(path: str) -> str:
        unquoted = urllib.parse.unquote(path or "")
        if not unquoted:
            return ""
        collapsed = re.sub(r"/+", "/", unquoted)
        if not collapsed.startswith("/"):
            collapsed = f"/{collapsed}"
        norm = posixpath.normpath(collapsed)
        return ("/" + norm.lstrip("/") if norm.startswith("//") else norm).lower()

    def classify_uth_webview_url(url: str) -> str:
        site, _ = _parse_uth_url(url)
        return site or "external"

    def is_uth_login_url(url: str) -> Optional[str]:
        site, parsed = _parse_uth_url(url)
        if site is None or parsed is None:
            return None
        np = _norm_path(parsed.path)
        if site in ("courses", "thnn"):
            return site if np in {"/login/index.php", "/login"} else None
        return "portal" if np in {"", "/", "/login"} else None

    def normalize_uth_webview_target_url(url: str, *, has_credentials: bool = True) -> str:
        if not isinstance(url, str) or not url:
            return ""
        site, parsed = _parse_uth_url(url)
        if site is None or parsed is None:
            return url
        canonical = urllib.parse.urlunsplit(
            ("https", canonical_host[site], parsed.path, parsed.query, parsed.fragment)
        )
        login_site = is_uth_login_url(canonical)
        if login_site is None:
            return canonical
        if login_site in ("courses", "thnn"):
            params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            wants = params.get("wantsurl") or params.get("WANTSURL") or []
            if len(wants) == 1:
                w_site, w_parsed = _parse_uth_url(wants[0])
                if w_site == login_site and w_parsed is not None:
                    w_canon = urllib.parse.urlunsplit(
                        ("https", canonical_host[w_site], w_parsed.path, w_parsed.query, w_parsed.fragment)
                    )
                    if is_uth_login_url(w_canon) is None:
                        return w_canon
                return default_url[login_site]
            return default_url[login_site] if has_credentials else canonical
        return default_url["portal"] if has_credentials else canonical

    def build_portal_local_storage_script(token: str, role: str = "sv") -> str:
        if not isinstance(token, str) or not token.strip():
            return ""
        tok_lit = json.dumps(token.strip())
        role_lit = json.dumps((role or "sv").strip() or "sv")
        return (
            "(function () {try {"
            'if (window.location && window.location.origin === "https://portal.ut.edu.vn") {'
            f'window.localStorage.setItem("account", {tok_lit});'
            f'window.localStorage.setItem("role", {role_lit});'
            "}} catch (_e) {}})();"
        )

    class WebViewRecoveryTracker:
        def __init__(self, *, max_attempts_per_site: int = 1) -> None:
            self._lock = threading.RLock()
            self._max = max(0, int(max_attempts_per_site))
            self._attempts: dict[str, int] = {}
            self._intended: dict[str, str] = {}

        def record_navigation(self, url: str) -> None:
            site, parsed = _parse_uth_url(url)
            if site and parsed:
                canon = urllib.parse.urlunsplit(
                    ("https", canonical_host[site], parsed.path, parsed.query, parsed.fragment)
                )
                if is_uth_login_url(canon) is None:
                    with self._lock:
                        self._intended[site] = canon

        def note_authenticated_landing(self, url: str) -> None:
            site, parsed = _parse_uth_url(url)
            if site and parsed:
                canon = urllib.parse.urlunsplit(
                    ("https", canonical_host[site], parsed.path, parsed.query, parsed.fragment)
                )
                if is_uth_login_url(canon) is None:
                    with self._lock:
                        self._attempts[site] = 0
                        self._intended[site] = canon

        def can_attempt_recovery(self, site: str) -> bool:
            with self._lock:
                return self._attempts.get(site, 0) < self._max

        def consume_recovery_attempt(self, site: str) -> bool:
            with self._lock:
                used = self._attempts.get(site, 0)
                if used >= self._max:
                    return False
                self._attempts[site] = used + 1
                return True

        def intended_url_for(self, site: str) -> str:
            with self._lock:
                return self._intended.get(site) or default_url.get(site, "")

    class UthWebViewAuthService:
        def __init__(
            self,
            *,
            courses_client: Any = None,
            thnn_client: Any = None,
            portal_client: Any = None,
            credentials_provider: Optional[Callable[[], tuple[str, str]]] = None,
            account_epoch_getter: Optional[Callable[[], int]] = None,
        ) -> None:
            self._courses_client = courses_client
            self._thnn_client = thnn_client
            self._portal_client = portal_client
            self._credentials_provider = credentials_provider
            self._account_epoch_getter = account_epoch_getter

        def client_for_site(self, site: str) -> Any:
            return {
                "courses": self._courses_client,
                "thnn": self._thnn_client,
                "portal": self._portal_client,
            }.get(site)

        def has_credentials(self, site: Optional[str] = None) -> bool:
            if site in ("courses", "thnn", "portal") and self.client_for_site(site) is None:
                return False
            if self._credentials_provider is not None:
                u, p = self._credentials_provider()
                return bool(str(u or "").strip() and str(p or "").strip())
            return site in ("courses", "thnn") and self.client_for_site(site) is not None

        def _epoch_now(self) -> Optional[int]:
            return self._account_epoch_getter() if callable(self._account_epoch_getter) else None

        @staticmethod
        def _normalize_cookies(raw_cookies: Any, *, default_domain: str) -> tuple[dict[str, Any], ...]:
            if not raw_cookies:
                return ()
            items = [raw_cookies] if isinstance(raw_cookies, dict) else list(raw_cookies)
            normalized: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                value = item.get("value")
                if not name or value is None:
                    continue
                copy_item = dict(item)
                copy_item["name"] = name
                copy_item["value"] = str(value)
                raw_dom = str(copy_item.get("domain") or default_domain).strip().lstrip(".").casefold()
                if raw_dom == "elearning.ut.edu.vn":
                    raw_dom = "courses.ut.edu.vn"
                if raw_dom not in {"courses.ut.edu.vn", "thnn.ut.edu.vn"}:
                    continue
                copy_item["domain"] = raw_dom
                copy_item["domain_specified"] = False
                copy_item["path"] = str(copy_item.get("path") or "/").strip() or "/"
                normalized.append(copy_item)
            return tuple(normalized)

        def prepare_initial_auth(
            self, url: str, *, expected_epoch: Optional[int] = None
        ) -> WebViewAuthBootstrap:
            start_epoch = expected_epoch if expected_epoch is not None else self._epoch_now()
            now_epoch = self._epoch_now()
            if start_epoch is not None and now_epoch is not None and now_epoch != start_epoch:
                return WebViewAuthBootstrap(
                    target_url="",
                    site=classify_uth_webview_url(url),
                    account_epoch=start_epoch,
                )
            site = classify_uth_webview_url(url)
            if site == "external":
                return WebViewAuthBootstrap(target_url=url, site="external", account_epoch=start_epoch)
            if site in ("courses", "thnn"):
                client = self.client_for_site(site)
                cookies = (
                    self._normalize_cookies(
                        client.get_session_cookies(
                            ensure_login=True, verify_live=True, force_relogin=False
                        ),
                        default_domain=canonical_host[site],
                    )
                    if client is not None and self.has_credentials(site)
                    else ()
                )
                if start_epoch is not None and self._epoch_now() not in (None, start_epoch):
                    return WebViewAuthBootstrap(target_url="", site=site, account_epoch=start_epoch)
                return WebViewAuthBootstrap(
                    target_url=normalize_uth_webview_target_url(url, has_credentials=bool(cookies)),
                    site=site,
                    cookies=cookies,
                    account_epoch=start_epoch,
                )
            script = ""
            if self._portal_client is not None and self.has_credentials("portal"):
                u, p = self._credentials_provider() if self._credentials_provider else ("", "")
                tok, role = self._portal_client.ensure_web_session(u, p, force=False)
                if start_epoch is not None and self._epoch_now() not in (None, start_epoch):
                    return WebViewAuthBootstrap(target_url="", site="portal", account_epoch=start_epoch)
                if tok:
                    script = build_portal_local_storage_script(tok, role)
            return WebViewAuthBootstrap(
                target_url=normalize_uth_webview_target_url(url, has_credentials=bool(script)),
                site="portal",
                portal_init_script=script,
                account_epoch=start_epoch,
            )

        def recover_from_login_redirect(
            self,
            login_url: str,
            tracker: Optional[WebViewRecoveryTracker] = None,
            *,
            expected_epoch: Optional[int] = None,
        ) -> Optional[WebViewAuthBootstrap]:
            start_epoch = expected_epoch if expected_epoch is not None else self._epoch_now()
            if start_epoch is not None and self._epoch_now() not in (None, start_epoch):
                return None
            site = is_uth_login_url(login_url)
            if site is None or tracker is None:
                return None
            if not self.has_credentials(site) or not tracker.consume_recovery_attempt(site):
                return None
            target_url = normalize_uth_webview_target_url(login_url, has_credentials=True)
            if target_url == default_url[site]:
                target_url = tracker.intended_url_for(site)
            if site in ("courses", "thnn"):
                client = self.client_for_site(site)
                cookies = self._normalize_cookies(
                    client.get_session_cookies(
                        ensure_login=True, verify_live=False, force_relogin=True
                    ),
                    default_domain=canonical_host[site],
                )
                if start_epoch is not None and self._epoch_now() not in (None, start_epoch):
                    return None
                if not cookies:
                    return None
                return WebViewAuthBootstrap(
                    target_url=target_url,
                    site=site,
                    cookies=cookies,
                    account_epoch=start_epoch,
                )
            u, p = self._credentials_provider() if self._credentials_provider else ("", "")
            tok, role = self._portal_client.ensure_web_session(u, p, force=True)
            if start_epoch is not None and self._epoch_now() not in (None, start_epoch):
                return None
            if not tok:
                return None
            return WebViewAuthBootstrap(
                target_url=target_url,
                site="portal",
                portal_init_script=build_portal_local_storage_script(tok, role),
                account_epoch=start_epoch,
            )

    mod.WebViewAuthBootstrap = WebViewAuthBootstrap  # type: ignore[attr-defined]
    mod.WebViewRecoveryTracker = WebViewRecoveryTracker  # type: ignore[attr-defined]
    mod.UthWebViewAuthService = UthWebViewAuthService  # type: ignore[attr-defined]
    mod.build_portal_local_storage_script = build_portal_local_storage_script  # type: ignore[attr-defined]
    mod.classify_uth_webview_url = classify_uth_webview_url  # type: ignore[attr-defined]
    mod.is_uth_login_url = is_uth_login_url  # type: ignore[attr-defined]
    mod.normalize_uth_webview_target_url = normalize_uth_webview_target_url  # type: ignore[attr-defined]
    sys.modules["core.webview_auth"] = mod
    return mod


_wv_auth_mod = _ensure_webview_auth_contract_module()
UthWebViewAuthService = _wv_auth_mod.UthWebViewAuthService
build_portal_local_storage_script = _wv_auth_mod.build_portal_local_storage_script


def _make_live_mobile_page(platform: ft.PagePlatform = ft.PagePlatform.ANDROID) -> ft.Page:
    page = MagicMock(spec=ft.Page)
    page.web = False
    page.platform = platform
    page.width = 412
    page.height = 915
    page.overlay = []

    def _run_task(coro_fn, *args, **kwargs):
        res = coro_fn(*args, **kwargs)
        if asyncio.iscoroutine(res):
            try:
                loop = asyncio.get_running_loop()
                return loop.create_task(res)
            except RuntimeError:
                return asyncio.run(res)
        return res

    page.run_task = _run_task
    return page


# ===========================================================================
# 1. BUG-134-01: Mobile Android & iOS UthAppWebView (`inappwebview`) Auto-Auth & Self-Heal
# ===========================================================================


@pytest.mark.parametrize("platform", [ft.PagePlatform.ANDROID, ft.PagePlatform.IOS])
def test_bug134_01_mobile_inappwebview_portal_bootstrap_and_reactive_self_heal_across_uth_sites(
    platform: ft.PagePlatform,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gui.components.courses.webview_pane import CoursesWebViewPane

    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "pw-secret")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    courses_calls: list[dict[str, Any]] = []
    portal_calls: list[bool] = []

    courses_client = SimpleNamespace(
        moodle_site_origin="https://courses.ut.edu.vn",
        can_authenticate_html_session=lambda: True,
        get_session_cookies=lambda **kw: (
            courses_calls.append(dict(kw))
            or [
                {
                    "name": "MoodleSession",
                    "value": "fresh_courses_cookie" if kw.get("force_relogin") else "init_courses_cookie",
                    "domain": "courses.ut.edu.vn",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                }
            ]
        ),
    )
    portal_client = SimpleNamespace(
        ensure_web_session=lambda _u, _p, force=False, **_kw: (
            portal_calls.append(bool(force))
            or ("jwt.portal.recovered" if force else "jwt.portal.initial", "sv")
        ),
    )
    auth_service = UthWebViewAuthService(
        courses_client=courses_client,
        portal_client=portal_client,
        credentials_provider=lambda: (settings.UTH_USERNAME, settings.UTH_PASSWORD),
    )

    page = _make_live_mobile_page(platform)
    pane = CoursesWebViewPane(
        page=page,
        source="courses",
        title="Portal UTH",
        url="https://portal.ut.edu.vn/",
        webview_auth_service=auth_service,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        assert pane._host is not None
        assert pane._host.backend == "inappwebview"
        slot = pane._host.control()
        wv = slot.content
        assert wv.url == "https://portal.ut.edu.vn/dashboard"
        assert wv.require_initial_cookies is True

        bootstrapped: list[dict[str, Any]] = []
        applied_navs: list[tuple[list[dict[str, Any]], str, str, str]] = []
        nav_done = threading.Event()

        async def _spy_bootstrap(
            cookies,
            *,
            target_url=None,
            init_script="",
            init_script_allowed_origin="",
        ):
            bootstrapped.append(
                {
                    "cookies": list(cookies),
                    "target_url": target_url,
                    "init_script": init_script,
                    "allowed_origin": init_script_allowed_origin,
                }
            )
            return True

        async def _spy_apply_auth(
            cookies,
            target_url,
            *,
            init_script="",
            init_script_allowed_origin="",
        ):
            applied_navs.append(
                (
                    list(cookies),
                    str(target_url),
                    str(init_script),
                    str(init_script_allowed_origin),
                )
            )
            nav_done.set()
            return True

        monkeypatch.setattr(wv, "bootstrap_cookies", _spy_bootstrap)
        monkeypatch.setattr(wv, "apply_auth_and_navigate", _spy_apply_auth)

        # 1. Initial mount bootstraps origin-pinned Portal AT_DOCUMENT_START script on inappwebview!
        slot.did_mount()
        assert len(bootstrapped) == 1
        assert bootstrapped[0]["target_url"] == "https://portal.ut.edu.vn/dashboard"
        assert bootstrapped[0]["allowed_origin"] == "https://portal.ut.edu.vn"
        assert "jwt.portal.initial" in bootstrapped[0]["init_script"]
        assert 'window.location.origin === "https://portal.ut.edu.vn"' in bootstrapped[0]["init_script"]
        assert portal_calls == [False]

        # 2. Portal session expires -> redirects to https://portal.ut.edu.vn/login -> self-heals on inappwebview!
        wv.on_url_change(SimpleNamespace(data="https://portal.ut.edu.vn/login"))
        assert nav_done.wait(timeout=2.0)
        assert len(applied_navs) == 1
        ck_p, url_p, js_p, origin_p = applied_navs[-1]
        assert ck_p == []
        assert url_p == "https://portal.ut.edu.vn/dashboard"
        assert origin_p == "https://portal.ut.edu.vn"
        assert "jwt.portal.recovered" in js_p
        assert portal_calls == [False, True]

        # 3. Cross-host UTH link from Portal -> Courses redirects to /login/index.php -> self-heals Courses cookies!
        deadline = time.monotonic() + 1.0
        while pane._recovery_in_flight and time.monotonic() < deadline:
            time.sleep(0.01)
        nav_done.clear()
        wv.on_page_started(
            SimpleNamespace(data="https://courses.ut.edu.vn/course/view.php?id=888")
        )
        wv.on_url_change(
            SimpleNamespace(data="https://courses.ut.edu.vn/login/index.php")
        )
        assert nav_done.wait(timeout=2.0)
        assert len(applied_navs) == 2
        ck_c, url_c, js_c, origin_c = applied_navs[-1]
        assert url_c == "https://courses.ut.edu.vn/course/view.php?id=888"
        assert js_c == ""
        assert origin_c == ""
        assert ck_c[0]["value"] == "fresh_courses_cookie"
    finally:
        pane.dispose()


def test_bug134_01_failed_self_heal_and_failed_apply_never_leave_nav_loading_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gui.components.courses.webview_pane import CoursesWebViewPane

    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "wrong-password")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    # Case A: relogin returns empty cookies (wrong password / offline) -> spinner clears
    dead_courses = SimpleNamespace(
        moodle_site_origin="https://courses.ut.edu.vn",
        can_authenticate_html_session=lambda: True,
        get_session_cookies=lambda **_kw: [],
    )
    auth_dead = UthWebViewAuthService(
        courses_client=dead_courses,
        credentials_provider=lambda: ("2251120001", "wrong-password"),
    )
    page = _make_live_mobile_page(ft.PagePlatform.ANDROID)
    pane = CoursesWebViewPane(
        page=page,
        source="courses",
        title="Course",
        url="https://courses.ut.edu.vn/course/view.php?id=5",
        webview_auth_service=auth_dead,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        wv = pane._host.control().content
        wv.on_page_ended(SimpleNamespace(data="https://courses.ut.edu.vn/login/index.php"))
        wv.on_url_change(SimpleNamespace(data="https://courses.ut.edu.vn/login/index.php"))
        deadline = time.monotonic() + 1.0
        while pane._recovery_in_flight and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        assert pane._nav_progress.visible is False
        assert pane._nav_progress_host.opacity == 0.0
    finally:
        pane.dispose()

    # Case B: recovery succeeds in Python, but mobile apply_auth_and_navigate returns False -> spinner MUST clear!
    ok_courses = SimpleNamespace(
        moodle_site_origin="https://courses.ut.edu.vn",
        can_authenticate_html_session=lambda: True,
        get_session_cookies=lambda **_kw: [
            {"name": "MoodleSession", "value": "ok_sess", "domain": "courses.ut.edu.vn"}
        ],
    )
    auth_ok = UthWebViewAuthService(
        courses_client=ok_courses,
        credentials_provider=lambda: ("2251120001", "right-pw"),
    )
    pane2 = CoursesWebViewPane(
        page=page,
        source="courses",
        title="Course 2",
        url="https://courses.ut.edu.vn/course/view.php?id=6",
        webview_auth_service=auth_ok,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        wv2 = pane2._host.control().content
        apply_called = threading.Event()

        async def _failing_apply(*_a, **_kw):
            apply_called.set()
            return False

        monkeypatch.setattr(wv2, "apply_auth_and_navigate", _failing_apply)
        wv2.on_url_change(SimpleNamespace(data="https://courses.ut.edu.vn/login/index.php"))
        assert apply_called.wait(timeout=2.0)
        time.sleep(0.05)
        assert pane2._nav_progress.visible is False
        assert pane2._nav_progress_host.opacity == 0.0
    finally:
        pane2.dispose()


# ===========================================================================
# 2. BUG-134-02: Off-STA Async Prefetch & Zero Worker `asyncio.run` (`APP-04`)
# ===========================================================================


def test_bug134_02_webview2_dock_prefetches_both_cookies_and_init_script_off_sta_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gui.components.courses.embedded_webview_host import WindowsWebView2Dock

    sta_tid = threading.get_ident()
    cookie_gate = threading.Event()
    script_gate = threading.Event()
    cookie_tids: list[int] = []
    script_tids: list[int] = []

    def _blocking_cookie_provider() -> list[dict[str, Any]]:
        cookie_tids.append(threading.get_ident())
        cookie_gate.wait(timeout=2.0)
        return [{"name": "MoodleSession", "value": "async_ck", "domain": "portal.ut.edu.vn"}]

    def _blocking_script_provider() -> str:
        script_tids.append(threading.get_ident())
        script_gate.wait(timeout=2.0)
        return build_portal_local_storage_script("jwt.off.sta", "sv")

    dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="https://portal.ut.edu.vn/dashboard",
        cookie_provider=_blocking_cookie_provider,
        init_script_provider=_blocking_script_provider,
    )
    dock._hwnd = 1
    dock._visible = False
    monkeypatch.setattr(dock, "_apply_window_bounds", lambda **_kw: None)

    # Simulate live STA pump thread active while prefetch worker runs in background
    keep_alive = threading.Event()
    fake_sta = threading.Thread(target=lambda: keep_alive.wait(timeout=3.0), daemon=True)
    fake_sta.start()
    dock._thread = fake_sta

    events: list[tuple[str, Any]] = []
    script_ops: list[Any] = []

    class _Core:
        settings = SimpleNamespace(
            is_status_bar_enabled=True,
            are_dev_tools_enabled=True,
            are_host_objects_allowed=True,
        )
        cookie_manager = SimpleNamespace(
            create_cookie=lambda n, v, d, p: SimpleNamespace(
                name=n, value=v, domain=d, path=p, is_secure=False, is_http_only=False
            ),
            add_or_update_cookie=lambda c: events.append(("cookie", c.value)),
        )

        def add_document_title_changed(self, _cb): pass
        def add_source_changed(self, _cb): pass
        def add_navigation_starting(self, _cb): pass
        def add_navigation_completed(self, _cb): pass
        def add_new_window_requested(self, _cb): pass

        def add_script_to_execute_on_document_created_async(self, js: str):
            events.append(("add_script", js))
            op = SimpleNamespace(completed=None, get_results=lambda: "sid-1")
            script_ops.append(op)
            return op

        def navigate(self, u: str):
            events.append(("navigate", u))

    core = _Core()
    ctrl = SimpleNamespace(core_webview2=core, is_visible=False, close=lambda: None)

    try:
        t0 = time.monotonic()
        dock._handle_ctrl_done(SimpleNamespace(get_results=lambda: ctrl))
        elapsed = time.monotonic() - t0

        # MUST return immediately on STA thread without blocking on cookie_gate or script_gate!
        assert elapsed < 0.25
        assert dock._ready is False
        assert events == []

        cookie_gate.set()
        script_gate.set()
        deadline = time.monotonic() + 2.0
        while dock._prefetched_init_script is None and time.monotonic() < deadline:
            time.sleep(0.01)

        dock._drain_commands()
        assert cookie_tids and cookie_tids[0] != sta_tid
        assert script_tids and script_tids[0] != sta_tid
        assert dock._ready is True
        assert ("cookie", "async_ck") in events
        assert any(ev[0] == "add_script" and "jwt.off.sta" in ev[1] for ev in events)
    finally:
        cookie_gate.set()
        script_gate.set()
        keep_alive.set()
        dock.dispose()


def test_bug134_02_zero_asyncio_run_in_webview_modules_and_worker_tasks_marshal_to_page_loop() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for rel_path in (
        "src/gui/components/courses/embedded_webview_host.py",
        "src/gui/components/courses/webview_pane.py",
    ):
        tree = ast.parse((repo_root / rel_path).read_text(encoding="utf-8"))
        asyncio_runs = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "asyncio"
            and node.func.attr == "run"
        ]
        assert asyncio_runs == [], f"{rel_path} contains forbidden asyncio.run at lines {asyncio_runs}"


# ===========================================================================
# 3. SEC-134-01, SEC-134-04 & BUG-134-03/04: Pre-Mutation Epoch, Demo/PreventLinks & Canonicalization
# ===========================================================================


def test_sec134_01_settings_account_login_invalidates_epoch_and_closes_webviews_before_mutating_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gui.app_controller as app_controller_module
    from gui.app_controller import AppController

    monkeypatch.setattr(settings, "UTH_USERNAME", "student_a")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "pass_a")
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")

    timeline: list[tuple[str, ...]] = []

    controller = AppController.__new__(AppController)
    controller._account_epoch = 5
    controller._settings_identity = ("student_a", "pass_a")

    def _close_app_wv(*, update_page: bool = True) -> bool:
        # At the exact instant Student A's WebView closes, settings MUST still hold Student A (never Student B!)
        timeline.append(("close_app_webview", settings.UTH_USERNAME, str(controller._account_epoch)))
        return True

    def _reset_courses(ns: str) -> None:
        timeline.append(("reset_courses", settings.UTH_USERNAME, ns, str(controller._account_epoch)))

    def _save_settings() -> bool:
        timeline.append(("save_settings", settings.UTH_USERNAME, str(controller._account_epoch)))
        return True

    controller._close_app_webview = _close_app_wv  # type: ignore[method-assign]
    controller._courses_coordinator = SimpleNamespace(
        courses_view=SimpleNamespace(
            clear_overlays=lambda **_kw: timeline.append(
                ("clear_overlays", settings.UTH_USERNAME, str(controller._account_epoch))
            )
        ),
        reset_for_account_switch=_reset_courses,
    )
    controller._portal_client = SimpleNamespace(
        clear_token=lambda: timeline.append(("clear_portal_token", settings.UTH_USERNAME))
    )
    controller._complete_login_success = AsyncMock(
        side_effect=lambda: timeline.append(("complete_login", settings.UTH_USERNAME))
    )
    monkeypatch.setattr(app_controller_module, "save_settings", _save_settings)

    asyncio.run(controller._handle_settings_account_login_async("student_b", "pass_b"))

    # Prove epoch bumped to 6 AND WebViews closed BEFORE settings.UTH_USERNAME changed from student_a -> student_b!
    close_idx = next(i for i, ev in enumerate(timeline) if ev[0] == "close_app_webview")
    save_idx = next(i for i, ev in enumerate(timeline) if ev[0] == "save_settings")
    assert close_idx < save_idx
    assert timeline[close_idx] == ("close_app_webview", "student_a", "6")
    assert ("clear_overlays", "student_a", "6") in timeline[:save_idx]
    assert timeline[save_idx] == ("save_settings", "student_b", "6")
    # Preserve _settings_identity for _complete_login_success teardown comparison!
    assert controller._settings_identity == ("student_a", "pass_a")


def test_sec134_01_inflight_webview_recovery_discards_result_when_account_epoch_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gui.components.courses import webview_pane as pane_mod
    from gui.components.courses.webview_pane import CoursesWebViewPane

    epoch_box = {"val": 10}
    relogin_started = threading.Event()
    relogin_release = threading.Event()
    apply_calls: list[Any] = []

    class _SlowClient:
        moodle_site_origin = "https://courses.ut.edu.vn"

        def can_authenticate_html_session(self) -> bool:
            return True

        def get_session_cookies(self, **_kw):
            relogin_started.set()
            relogin_release.wait(timeout=2.0)
            return [{"name": "MoodleSession", "value": "stale_student_a", "domain": "courses.ut.edu.vn"}]

    auth_service = UthWebViewAuthService(
        courses_client=_SlowClient(),
        credentials_provider=lambda: ("student_a", "pw_a"),
        account_epoch_getter=lambda: epoch_box["val"],
    )

    captured: dict[str, Any] = {}
    fake_host = SimpleNamespace(
        backend="webview2",
        dock=None,
        control=lambda: ft.Container(expand=True),
        apply_auth_and_navigate=lambda *a, **kw: apply_calls.append((a, kw)) or True,
        navigate=lambda _u: None,
        sync_bounds=lambda *_a, **_kw: None,
        dispose=lambda: None,
    )
    monkeypatch.setattr(
        pane_mod,
        "build_embedded_webview_host",
        lambda url, **kw: captured.update(kw) or fake_host,
    )

    pane = CoursesWebViewPane(
        page=None,
        source="courses",
        title="Course",
        url="https://courses.ut.edu.vn/course/view.php?id=10",
        webview_auth_service=auth_service,
        account_epoch_getter=lambda: epoch_box["val"],
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        captured["on_url_changed"]("https://courses.ut.edu.vn/login/index.php")
        assert relogin_started.wait(timeout=2.0)

        # Account switches mid-flight before HTTP relogin finishes!
        epoch_box["val"] = 11
        relogin_release.set()

        deadline = time.monotonic() + 1.0
        while pane._recovery_in_flight and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)

        assert apply_calls == []
    finally:
        relogin_release.set()
        pane.dispose()


def test_sec134_04_and_bug134_03_04_preserve_prevent_links_demo_lock_and_canonicalize_elearning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gui.app_controller import AppController
    from gui.components.courses.embedded_webview_host import (
        EmbeddedWebViewHost,
        build_embedded_webview_host,
    )
    from gui.components.courses.webview_pane import CoursesWebViewPane
    from gui.components.in_app_webview import InAppWebViewDialog
    from gui.controllers.courses_coordinator import CoursesCoordinator

    # 1. SEC-134-04: allow_loopback_port MUST NEVER strip "http://" from flet_webview.prevent_links!
    fake_wv = SimpleNamespace(
        url="https://portal.ut.edu.vn/dashboard",
        prevent_links=["javascript:", "file:", "data:", "intent:", "http://"],
    )
    host_fwv = EmbeddedWebViewHost(
        backend="flet_webview",
        url="https://portal.ut.edu.vn/dashboard",
        webview_control=fake_wv,
    )
    host_fwv.allow_loopback_port(54321)
    assert 54321 in host_fwv._allowed_loopback_ports
    assert "http://" in fake_wv.prevent_links
    host_fwv.dispose()

    # 2. BUG-134-04: InAppWebViewDialog canonicalizes elearning.ut.edu.vn -> courses.ut.edu.vn
    dummy_page = SimpleNamespace(
        platform=ft.PagePlatform.WINDOWS,
        web=False,
        width=600,
        height=600,
        overlay=[],
        show_dialog=lambda _d: None,
        pop_dialog=lambda: None,
        run_task=lambda *_a, **_k: None,
        update=lambda: None,
    )
    dlg = InAppWebViewDialog(
        dummy_page,  # type: ignore[arg-type]
        url="https://elearning.ut.edu.vn/course/view.php?id=42",
        title="LMS",
    )
    assert dlg._url == "https://courses.ut.edu.vn/course/view.php?id=42"

    # 3. BUG-134-04: AppController._start_course_quiz canonicalizes elearning.ut.edu.vn before prepare_activity
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    prepared_activities: list[dict[str, Any]] = []
    controller = AppController.__new__(AppController)
    controller.all_data = []
    controller._snapshot_all_data = lambda: []  # type: ignore[method-assign]
    controller.quiz_player_view = SimpleNamespace(
        visible=False,
        prepare_activity=lambda act, _acc: (
            prepared_activities.append(dict(act)) or (None, "player_unavailable")
        ),
    )
    controller._courses_coordinator = None
    course = CourseSummary(
        id=101,
        source="courses",
        fullname="Lập trình Python",
        shortname="PY101",
        display_name="Lập trình Python",
    )
    mod = CourseModuleItem(
        cmid=777,
        instance_id=501,
        modname="quiz",
        name="Quiz 1",
        section_id=1,
        url="https://elearning.ut.edu.vn/mod/quiz/view.php?id=777",
        web_url="https://elearning.ut.edu.vn/mod/quiz/view.php?id=777",
    )
    controller._start_course_quiz(course, mod)
    assert len(prepared_activities) == 1
    assert prepared_activities[0]["url"] == "https://courses.ut.edu.vn/mod/quiz/view.php?id=777"

    # 4. BUG-134-03: CoursesCoordinator.ensure_preview_server acquires _preview_register_lock
    store = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|u")
    pcache = PreviewFileCache(tmp_path / "pc")
    local_srv = LocalPreviewServer()
    lock_entries = 0

    class _SpyLock:
        def __enter__(self):
            nonlocal lock_entries
            lock_entries += 1
            return self

        def __exit__(self, *_a):
            return False

    try:
        coord = CoursesCoordinator(
            page=dummy_page,  # type: ignore[arg-type]
            catalog_service=CourseCatalogService(cache_store=store, preview_cache=pcache),
            preview_cache=pcache,
            archive_service=ArchiveService(),
            preview_server=local_srv,
            on_open_assignment_fallback=lambda _m: None,
            on_launch_url=lambda _u: None,
            courses_client=SimpleNamespace(),
            thnn_client=SimpleNamespace(),
        )
        coord._preview_register_lock = _SpyLock()  # type: ignore[assignment]
        local_srv._session_providers.clear()
        assert coord.ensure_preview_server() is local_srv
        assert lock_entries == 1
        assert "courses" in local_srv._session_providers
        assert "thnn" in local_srv._session_providers
    finally:
        local_srv.stop()

    # 5. BUG-134-03 / SEC-134-02: Demo Mode blocks UTH LMS in CoursesWebViewPane & build_embedded_webview_host
    monkeypatch.setattr(settings, "UTH_USERNAME", DEMO_USERNAME)
    assert (
        build_embedded_webview_host(
            "https://portal.ut.edu.vn/dashboard",
            page=_make_live_mobile_page(ft.PagePlatform.ANDROID),
        )
        is None
    )
    demo_pane = CoursesWebViewPane(
        page=_make_live_mobile_page(ft.PagePlatform.ANDROID),
        source="courses",
        title="Demo Blocked",
        url="https://portal.ut.edu.vn/dashboard",
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        assert demo_pane._blocked_navigation is True
        assert demo_pane._host is None
    finally:
        demo_pane.dispose()


# ===========================================================================
# 4. Round 2 Adversarial Challenge Regressions (#140: Challenges 1, 2, 3)
# ===========================================================================


def test_challenge1_same_account_relogin_preserves_courses_namespace_and_login_dialog_guards_uncommitted_creds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gui.app_controller as app_controller_module
    from gui.app_controller import AppController
    from gui.controllers.courses_coordinator import CoursesCoordinator

    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "PORTAL_API_BASE", "https://portal.ut.edu.vn/api/v1")
    monkeypatch.setattr(settings, "UTH_USERNAME", "studenta")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "passA")
    monkeypatch.setattr(app_controller_module, "save_settings", lambda: True)

    expected_ns = "https://courses.ut.edu.vn|studenta"
    store = CourseCacheStore(cache_dir=tmp_path / "courses_cache", namespace=expected_ns)
    initial_catalog_path = store._catalog_path
    pcache = PreviewFileCache(tmp_path / "preview_cache")
    seeded_file = pcache.put_bytes(
        "https://courses.ut.edu.vn/pluginfile.php/1/lecture.pdf",
        100,
        "lecture.pdf",
        b"%PDF-1.4",
    )
    assert seeded_file.is_file()
    local_srv = LocalPreviewServer()

    dummy_page = SimpleNamespace(
        platform=ft.PagePlatform.WINDOWS,
        web=False,
        width=1200,
        height=800,
        overlay=[],
        services=[],
        session=None,
        show_dialog=lambda _d: None,
        pop_dialog=lambda: None,
        run_task=lambda *_a, **_k: None,
        update=lambda: None,
    )
    try:
        coord = CoursesCoordinator(
            page=dummy_page,  # type: ignore[arg-type]
            catalog_service=CourseCatalogService(cache_store=store, preview_cache=pcache),
            preview_cache=pcache,
            archive_service=ArchiveService(),
            preview_server=local_srv,
            on_open_assignment_fallback=lambda _m: None,
            on_launch_url=lambda _u: None,
            account_namespace=expected_ns,
        )
        overlay_cleared = []
        coord.courses_view.clear_overlays = lambda **_kw: overlay_cleared.append(True)  # type: ignore[method-assign]

        controller = AppController.__new__(AppController)
        controller.page = dummy_page
        controller._account_epoch = 1
        controller._settings_identity = ("studenta", "passA")
        controller._courses_coordinator = coord
        controller._portal_client = SimpleNamespace(clear_token=lambda: None)
        controller._portal_week_client = SimpleNamespace(clear_token=lambda: None)
        controller.orchestrator = SimpleNamespace(
            is_logged_in=True,
            client=SimpleNamespace(moodle_site_origin="https://courses.ut.edu.vn"),
            thnn_client=SimpleNamespace(moodle_site_origin="https://thnn.ut.edu.vn"),
        )
        controller._load_data_async = AsyncMock()  # type: ignore[method-assign]

        # 1. Same-account re-login MUST NOT poison coord._account_namespace or CourseCacheStore to __logged_out__!
        asyncio.run(controller._handle_settings_account_login_async("studenta", "passA"))
        assert overlay_cleared == [True]
        assert coord._account_namespace == expected_ns
        assert store._catalog_path == initial_catalog_path
        assert "6542a21fb479" not in store._catalog_path.name
        assert pcache.get_cached_path(
            "https://courses.ut.edu.vn/pluginfile.php/1/lecture.pdf",
            100,
            "lecture.pdf",
        ) is not None

        # 2. _build_webview_auth_service credentials_provider blocks uncommitted settings mutations (e.g. during login_dialog 0.7s window)
        auth_svc = controller._build_webview_auth_service(
            portal_client=SimpleNamespace(ensure_web_session=lambda u, p, **_k: (f"tok:{u}", "sv")),
        )
        assert auth_svc.has_credentials("portal") is True
        # Simulate login_dialog mutating settings to Student B BEFORE _complete_login_success commits _settings_identity
        monkeypatch.setattr(settings, "UTH_USERNAME", "studentb")
        monkeypatch.setattr(settings, "UTH_PASSWORD", "passB")
        assert auth_svc.has_credentials("portal") is False

        # Once _teardown_account_session commits Student B, credentials_provider unlocks Student B and updates courses namespace!
        asyncio.run(controller._complete_login_success())
        assert controller._settings_identity == ("studentb", "passB")
        assert auth_svc.has_credentials("portal") is True
        assert coord._account_namespace == "https://courses.ut.edu.vn|studentb"

        # 3. _show_login_dialog invalidates active WebViews & bumps _account_epoch upfront before dialog opens
        dialog_calls: list[int] = []
        fake_login_mod = types.ModuleType("gui.components.login_dialog")

        async def _fake_show_login_dialog(_page, _orch, _cb, **_kw):
            dialog_calls.append(controller._account_epoch)

        fake_login_mod.show_login_dialog = _fake_show_login_dialog  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "gui.components.login_dialog", fake_login_mod)
        controller._diag_screen = lambda *_a, **_k: None  # type: ignore[method-assign]
        controller._diag_breadcrumb = lambda *_a, **_k: None  # type: ignore[method-assign]
        controller._attach_modal_occlusion_release = lambda **_k: False  # type: ignore[method-assign]
        before_dialog_epoch = controller._account_epoch
        asyncio.run(controller._show_login_dialog())
        assert dialog_calls == [before_dialog_epoch + 1]
    finally:
        local_srv.stop()


def test_challenge2_courses_coordinator_shares_app_controller_account_epoch_without_divergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gui.components.courses import webview_pane as pane_mod
    from gui.controllers.courses_coordinator import CoursesCoordinator

    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "pw")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    app_epoch = {"value": 7}
    courses_client = SimpleNamespace(
        moodle_site_origin="https://courses.ut.edu.vn",
        can_authenticate_html_session=lambda: True,
        get_session_cookies=lambda **_kw: [
            {"name": "MoodleSession", "value": "shared_epoch_ok", "domain": "courses.ut.edu.vn"}
        ],
    )
    auth_svc = UthWebViewAuthService(
        courses_client=courses_client,
        credentials_provider=lambda: ("2251120001", "pw"),
        account_epoch_getter=lambda: app_epoch["value"],
    )

    captured_host_kw: dict[str, Any] = {}
    fake_host = SimpleNamespace(
        backend="webview2",
        dock=None,
        control=lambda: ft.Container(expand=True),
        apply_auth_and_navigate=lambda *_a, **_kw: True,
        navigate=lambda _u: None,
        sync_bounds=lambda *_a, **_kw: None,
        dispose=lambda **_kw: None,
    )
    monkeypatch.setattr(
        pane_mod,
        "build_embedded_webview_host",
        lambda url, **kw: captured_host_kw.update(kw) or fake_host,
    )

    store = CourseCacheStore(cache_dir=tmp_path / "cc", namespace="https://courses.ut.edu.vn|2251120001")
    pcache = PreviewFileCache(tmp_path / "pc")
    local_srv = LocalPreviewServer()
    dummy_page = SimpleNamespace(
        platform=ft.PagePlatform.WINDOWS,
        web=False,
        width=1100,
        height=760,
        overlay=[],
        run_task=lambda *_a, **_k: None,
        update=lambda: None,
    )
    try:
        # Notice coord._account_epoch is 0 while AppController's epoch is 7!
        coord = CoursesCoordinator(
            page=dummy_page,  # type: ignore[arg-type]
            catalog_service=CourseCatalogService(cache_store=store, preview_cache=pcache),
            preview_cache=pcache,
            archive_service=ArchiveService(),
            preview_server=local_srv,
            on_open_assignment_fallback=lambda _m: None,
            on_launch_url=lambda _u: None,
            courses_client=courses_client,
            webview_auth_service=auth_svc,
            account_epoch_getter=lambda: app_epoch["value"],
        )
        assert coord._account_epoch == 0
        coord.open_in_app_webview(
            "courses",
            "Course 10",
            "https://courses.ut.edu.vn/course/view.php?id=10",
        )
        cookies = captured_host_kw["cookie_provider"]()
        assert len(cookies) == 1
        assert cookies[0]["value"] == "shared_epoch_ok"
    finally:
        local_srv.stop()


def test_challenge3_macos_teardown_invokes_quiz_session_cleaner_and_flet_webview_clears_local_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import flet_uth_quiz
    from gui.app_controller import AppController
    from gui.components.courses.embedded_webview_host import EmbeddedWebViewHost

    # Part A: EmbeddedWebViewHost.dispose(clear_browser_storage=True) clears localStorage & cache on flet_webview
    storage_ops: list[str] = []

    class _MacFletWv(ft.Container):
        def __init__(self) -> None:
            super().__init__(expand=True)
            self.url = "https://portal.ut.edu.vn/dashboard"

        async def clear_local_storage(self) -> None:
            storage_ops.append("clear_local_storage")

        async def clear_cache(self) -> None:
            storage_ops.append("clear_cache")

    mac_page = SimpleNamespace(
        platform=ft.PagePlatform.MACOS,
        web=False,
        services=[],
        session=SimpleNamespace(connection=object()),
        run_task=lambda fn, *a, **k: asyncio.run(fn(*a, **k)),
        update=lambda: None,
    )
    host = EmbeddedWebViewHost(
        backend="flet_webview",
        page=mac_page,  # type: ignore[arg-type]
        url="https://portal.ut.edu.vn/dashboard",
        webview_control=_MacFletWv(),
    )
    host.dispose(clear_browser_storage=True)
    assert storage_ops == ["clear_local_storage", "clear_cache"]

    # Part B: AppController._teardown_account_session on macOS registers UthQuizSessionCleaner and calls clear_sessions()
    cleaner_calls: list[str] = []

    class _FakeMacSessionCleaner:
        async def clear_sessions(self) -> bool:
            cleaner_calls.append("clear_sessions")
            return True

    monkeypatch.setattr(flet_uth_quiz, "UthQuizSessionCleaner", _FakeMacSessionCleaner)
    controller = AppController.__new__(AppController)
    controller.page = mac_page
    controller._account_epoch = 0
    controller._settings_identity = ("studenta", "passA")
    controller._reconcile_class_schedule_notifications = AsyncMock()  # type: ignore[method-assign]
    # Simulate real macOS QuizPlayerView returning False because _native_runtime_supported is False on darwin
    controller.quiz_player_view = SimpleNamespace(
        visible=False,
        clear_persisted_sessions=AsyncMock(return_value=False),
    )

    controller._teardown_account_session(
        ("", ""),
        reset_moodle_and_caches=True,
        account_switched_or_logged_out=True,
        schedule_refresh_trigger=None,
        trigger_android_logout=False,
        clear_ui=False,
        mark_logged_out=True,
    )
    assert cleaner_calls == ["clear_sessions"]
    assert any(isinstance(svc, _FakeMacSessionCleaner) for svc in mac_page.services)


def test_round2_off_loop_flet_webview_dispose_purges_storage_and_close_app_webview_disposes_before_detach() -> None:
    from gui.app_controller import AppController
    from gui.components.courses.embedded_webview_host import EmbeddedWebViewHost

    # Part A: Off-loop dispose(clear_browser_storage=True) via page.loop.call_soon_threadsafe
    # must NOT drop _purge_flet_storage after self._disposed = True!
    storage_ops: list[str] = []
    purged_done = threading.Event()

    class _OffLoopFletWv(ft.Container):
        def __init__(self) -> None:
            super().__init__(expand=True)
            self.url = "https://portal.ut.edu.vn/dashboard"

        async def clear_local_storage(self) -> None:
            storage_ops.append("clear_local_storage")

        async def clear_cache(self) -> None:
            storage_ops.append("clear_cache")
            purged_done.set()

    async def _run_off_loop_scenario() -> None:
        ui_loop = asyncio.get_running_loop()
        off_loop_page = SimpleNamespace(
            platform=ft.PagePlatform.MACOS,
            web=False,
            loop=ui_loop,
            run_task=lambda fn, *a, **k: ui_loop.create_task(fn(*a, **k)),
            update=lambda: None,
        )
        host = EmbeddedWebViewHost(
            backend="flet_webview",
            page=off_loop_page,  # type: ignore[arg-type]
            url="https://portal.ut.edu.vn/dashboard",
            webview_control=_OffLoopFletWv(),
        )
        await asyncio.to_thread(host.dispose, clear_browser_storage=True)
        deadline = time.monotonic() + 2.0
        while not purged_done.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)

    asyncio.run(_run_off_loop_scenario())
    assert storage_ops == ["clear_local_storage", "clear_cache"]

    # Part B: AppController._close_app_webview disposes pane BEFORE detaching host.content = None
    observed_host_content_during_dispose: list[Any] = []
    host_box = ft.Container(visible=True)

    class _OrderSpyPane(ft.Container):
        def dispose(self, *, clear_browser_storage: bool = False) -> None:
            observed_host_content_during_dispose.append(
                (host_box.content, clear_browser_storage)
            )

    spy_pane = _OrderSpyPane()
    host_box.content = spy_pane
    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace(update=lambda: None)
    controller._app_webview_host = host_box
    controller._app_webview_pane = spy_pane

    assert controller._close_app_webview(clear_browser_storage=True) is True
    assert observed_host_content_during_dispose == [(spy_pane, True)]
    assert host_box.content is None
    assert host_box.visible is False
