"""Integration tests for WebView2 / flet_webview dynamic auth, auto-relogin & app wiring."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from config import settings
from core.courses.archive_service import ArchiveService
from core.courses.cache_store import CourseCacheStore
from core.courses.catalog_service import CourseCatalogService
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.preview_cache import PreviewFileCache
from core.webview_auth import (
    UthWebViewAuthService,
    build_portal_local_storage_script,
)
from gui.components.courses import webview_pane as pane_mod
from gui.components.courses.embedded_webview_host import (
    EmbeddedWebViewHost,
    WindowsWebView2Dock,
)
from gui.components.courses.webview_pane import CoursesWebViewPane
from gui.controllers.courses_coordinator import CoursesCoordinator


def _moodle_cookie(
    value: str,
    domain: str = "courses.ut.edu.vn",
    *,
    name: str = "MoodleSession",
    http_only_key: str = "httpOnly",
) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": "/",
        "secure": True,
        http_only_key: True,
    }


class _FakeAsyncOp:
    """Deterministic WinRT-like async operation controlled by tests."""

    def __init__(self, result: Any = None) -> None:
        self._result = result
        self._completed_cb: Any = None

    @property
    def completed(self) -> Any:
        return self._completed_cb

    @completed.setter
    def completed(self, cb: Any) -> None:
        self._completed_cb = cb

    def get_results(self) -> Any:
        return self._result

    def complete(self, result: Any = None) -> None:
        if result is not None:
            self._result = result
        if callable(self._completed_cb):
            self._completed_cb(self, "Completed")


class _FakeCookieManager:
    def __init__(self, events: list[tuple[str, Any]]) -> None:
        self._events = events
        self.created_cookies: list[SimpleNamespace] = []

    def delete_cookies_with_domain_and_path(
        self, name: str, domain: str, path: str
    ) -> None:
        self._events.append(("delete_cookie", (name, domain, path)))

    def create_cookie(
        self, name: str, value: str, domain: str, path: str
    ) -> SimpleNamespace:
        obj = SimpleNamespace(
            name=name,
            value=value,
            domain=domain,
            path=path,
            is_secure=False,
            is_http_only=False,
        )
        self.created_cookies.append(obj)
        return obj

    def add_or_update_cookie(self, cookie_obj: SimpleNamespace) -> None:
        self._events.append(
            (
                "add_cookie",
                (
                    cookie_obj.name,
                    cookie_obj.value,
                    cookie_obj.domain,
                    cookie_obj.path,
                    cookie_obj.is_secure,
                    cookie_obj.is_http_only,
                ),
            )
        )


class _FakeWebView2Core:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.cookie_manager = _FakeCookieManager(self.events)
        self.callbacks: dict[str, Any] = {}
        self.pending_script_ops: list[_FakeAsyncOp] = []
        self.can_go_back = False
        self.can_go_forward = False
        self.document_title = ""
        self.source = ""
        self.settings = SimpleNamespace(
            is_status_bar_enabled=True,
            are_dev_tools_enabled=True,
            are_host_objects_allowed=True,
        )

    def add_document_title_changed(self, cb: Any) -> None:
        self.callbacks["title"] = cb

    def add_source_changed(self, cb: Any) -> None:
        self.callbacks["source"] = cb

    def add_navigation_starting(self, cb: Any) -> None:
        self.callbacks["nav_starting"] = cb

    def add_navigation_completed(self, cb: Any) -> None:
        self.callbacks["nav_completed"] = cb

    def add_history_changed(self, cb: Any) -> None:
        self.callbacks["history"] = cb

    def add_web_message_received(self, cb: Any) -> None:
        self.callbacks["web_msg"] = cb

    def add_new_window_requested(self, cb: Any) -> None:
        self.callbacks["new_window"] = cb

    def add_script_to_execute_on_document_created_async(
        self, script: str
    ) -> _FakeAsyncOp:
        self.events.append(("add_script", script))
        op = _FakeAsyncOp()
        self.pending_script_ops.append(op)
        return op

    def remove_script_to_execute_on_document_created(self, script_id: str) -> None:
        self.events.append(("remove_script", script_id))

    def navigate(self, url: str) -> None:
        self.events.append(("navigate", url))

    def navigate_to_string(self, html: str) -> None:
        self.events.append(("navigate_to_string", html))

    def execute_script_async(self, script: str) -> _FakeAsyncOp:
        self.events.append(("exec_js", script))
        return _FakeAsyncOp()


class _FakeMoodleSessionClient:
    def __init__(
        self,
        *,
        origin: str,
        initial_cookie: str,
        fresh_cookie: str,
    ) -> None:
        self.moodle_site_origin = origin
        self._domain = origin.replace("https://", "")
        self._initial_cookie = initial_cookie
        self._fresh_cookie = fresh_cookie
        self.calls: list[dict[str, Any]] = []
        self._last_login_error = ""

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
        val = self._fresh_cookie if force_relogin else self._initial_cookie
        return [_moodle_cookie(val, self._domain)]


class _FakePortalClient:
    def __init__(self, token: str = "jwt.portal.valid", role: str = "sv") -> None:
        self._token = token
        self._role = role
        self.calls: list[tuple[str, str, bool]] = []

    def ensure_web_session(
        self, username: str, password: str, *, force: bool = False
    ) -> tuple[str, str]:
        self.calls.append((username, password, force))
        return self._token, self._role


# ---------------------------------------------------------------------------
# 1. WindowsWebView2Dock: await document-created script before navigate & cleanup
# ---------------------------------------------------------------------------


def test_windows_webview2_dock_awaits_document_created_script_before_navigate_and_removes_after_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portal_script = build_portal_local_storage_script("jwt.portal.tok", "sv")
    dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="https://portal.ut.edu.vn/dashboard",
        init_script_provider=lambda: portal_script,
    )
    dock._hwnd = 1
    dock._visible = False
    monkeypatch.setattr(dock, "_apply_window_bounds", lambda **_kw: None)

    core = _FakeWebView2Core()
    ctrl = SimpleNamespace(core_webview2=core, is_visible=False, close=lambda: None)
    ctrl_op = SimpleNamespace(get_results=lambda: ctrl)

    dock._handle_ctrl_done(ctrl_op)

    # Script registration MUST be initiated and core.navigate MUST NOT have run yet!
    assert [ev[0] for ev in core.events] == ["add_script"]
    assert core.events[0][1] == portal_script
    assert len(core.pending_script_ops) == 1

    # Completing script registration schedules navigation strictly afterwards
    core.pending_script_ops[0].complete("script-1")
    dock._drain_commands()

    assert [ev[0] for ev in core.events] == ["add_script", "navigate"]
    assert core.events[1] == ("navigate", "https://portal.ut.edu.vn/dashboard")
    assert dock._active_init_script_id == "script-1"

    # Once navigation completes, script-1 is removed so it never lingers across later navigations
    core.callbacks["nav_completed"](core, SimpleNamespace(is_success=True))
    remove_events = [ev for ev in core.events if ev[0] == "remove_script"]
    assert remove_events == [("remove_script", "script-1")]
    assert dock._active_init_script_id == ""


def test_windows_webview2_dock_deletes_stale_moodle_session_duplicates_and_supports_http_only() -> None:
    dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="https://courses.ut.edu.vn/course/view.php?id=10",
        cookies=[
            _moodle_cookie(
                "fresh_sess_1",
                ".courses.ut.edu.vn",
                name="MoodleSession",
                http_only_key="http_only",
            ),
            _moodle_cookie(
                "user_hint",
                "courses.ut.edu.vn",
                name="MOODLEID1_",
                http_only_key="httpOnly",
            ),
        ],
    )
    core = _FakeWebView2Core()
    dock._inject_cookies(core)

    # Stale host & domain duplicates of MoodleSession deleted strictly before add_or_update_cookie
    assert core.events[0] == (
        "delete_cookie",
        ("MoodleSession", "courses.ut.edu.vn", "/"),
    )
    assert core.events[1] == (
        "delete_cookie",
        ("MoodleSession", ".courses.ut.edu.vn", "/"),
    )
    assert core.events[2][0] == "add_cookie"
    assert core.events[2][1] == (
        "MoodleSession",
        "fresh_sess_1",
        ".courses.ut.edu.vn",
        "/",
        True,
        True,
    )
    # Non-MoodleSession cookie still supports httpOnly without spurious delete
    assert core.events[3][0] == "add_cookie"
    assert core.events[3][1][0] == "MOODLEID1_"
    assert core.events[3][1][5] is True


def test_windows_webview2_dock_superseded_and_disposed_init_script_cleaned_up_without_stale_navigate() -> None:
    dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="https://portal.ut.edu.vn/dashboard",
    )
    core = _FakeWebView2Core()
    dock._core = core
    dock._ready = True

    script_1 = build_portal_local_storage_script("jwt.first", "sv")
    dock.apply_auth_and_navigate(
        [],
        "https://portal.ut.edu.vn/dashboard",
        init_script=script_1,
    )
    dock._drain_commands()
    assert len(core.pending_script_ops) == 1
    assert not any(ev[0] == "navigate" for ev in core.events)

    # Supersede with a newer navigation before script_1 finishes
    dock.navigate("https://courses.ut.edu.vn/my/")
    dock._drain_commands()
    assert ("navigate", "https://courses.ut.edu.vn/my/") in core.events

    # Stale script_1 completion must immediately remove script-stale and NOT navigate to portal
    core.pending_script_ops[0].complete("script-stale")
    dock._drain_commands()
    assert ("remove_script", "script-stale") in core.events
    assert ("navigate", "https://portal.ut.edu.vn/dashboard") not in core.events
    assert dock._active_init_script_id == ""

    # Pending script completion after dispose must remove script-disposed and NOT navigate
    script_2 = build_portal_local_storage_script("jwt.second", "sv")
    dock.apply_auth_and_navigate(
        [],
        "https://portal.ut.edu.vn/tuition",
        init_script=script_2,
    )
    dock._drain_commands()
    assert len(core.pending_script_ops) == 2

    dock._stop_event.set()
    core.pending_script_ops[1].complete("script-disposed")
    dock._drain_commands()
    assert ("remove_script", "script-disposed") in core.events
    assert ("navigate", "https://portal.ut.edu.vn/tuition") not in core.events


# ---------------------------------------------------------------------------
# 2. CoursesWebViewPane: reactive login recovery once, then stop on repeat
# ---------------------------------------------------------------------------


def test_courses_webview_pane_recovers_once_when_redirected_to_moodle_login_then_stops_on_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    courses_client = _FakeMoodleSessionClient(
        origin="https://courses.ut.edu.vn",
        initial_cookie="old_sess",
        fresh_cookie="fresh_sess_after_relogin",
    )
    auth_service = UthWebViewAuthService(
        courses_client=courses_client,
        credentials_provider=lambda: ("2251120001", "secret-pw"),
    )

    captured_callbacks: dict[str, Any] = {}
    apply_calls: list[tuple[list[dict[str, Any]], str, str]] = []
    recovery_applied = threading.Event()

    class _FakeHost:
        backend = "webview2"
        dock = None

        def control(self) -> ft.Control:
            return ft.Container(expand=True)

        def apply_auth_and_navigate(
            self,
            cookies: list[dict[str, Any]],
            url: str,
            *,
            init_script: str = "",
        ) -> None:
            apply_calls.append((list(cookies), url, init_script))
            recovery_applied.set()

        def navigate(self, url: str) -> None:
            return None

        def sync_bounds(self, *_a: Any, **_kw: Any) -> None:
            return None

        def dispose(self) -> None:
            return None

    fake_host = _FakeHost()

    def _fake_build(url: str, **kwargs: Any) -> _FakeHost:
        captured_callbacks["url"] = url
        captured_callbacks.update(kwargs)
        return fake_host

    monkeypatch.setattr(pane_mod, "build_embedded_webview_host", _fake_build)

    pane = CoursesWebViewPane(
        page=None,
        source="courses",
        title="Course 121137",
        url="https://courses.ut.edu.vn/course/view.php?id=121137",
        session_client=courses_client,
        webview_auth_service=auth_service,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        # Shared lazy bootstrap: invoking both cookie_provider and init_script_provider calls prepare once
        cookies_init = captured_callbacks["cookie_provider"]()
        script_init = captured_callbacks["init_script_provider"]()
        assert len(cookies_init) == 1
        assert cookies_init[0]["value"] == "old_sess"
        assert script_init == ""
        assert courses_client.calls == [
            {"ensure_login": True, "verify_live": True, "force_relogin": False}
        ]

        # 1st redirect to Moodle login -> background force_relogin + apply_auth_and_navigate
        captured_callbacks["on_url_changed"]("https://courses.ut.edu.vn/login/index.php")
        assert recovery_applied.wait(timeout=2.0)

        assert len(apply_calls) == 1
        fresh_cookies, nav_url, nav_script = apply_calls[0]
        assert nav_url == "https://courses.ut.edu.vn/course/view.php?id=121137"
        assert nav_script == ""
        assert fresh_cookies == [_moodle_cookie("fresh_sess_after_relogin", "courses.ut.edu.vn")]
        assert courses_client.calls[-1] == {
            "ensure_login": True,
            "verify_live": False,
            "force_relogin": True,
        }

        # Wait until recovery in-flight lock releases
        deadline = time.monotonic() + 1.0
        while pane._recovery_in_flight and time.monotonic() < deadline:
            time.sleep(0.01)

        # 2nd immediate redirect to login without authenticated landing -> MUST NOT loop!
        recovery_applied.clear()
        captured_callbacks["on_url_changed"]("https://courses.ut.edu.vn/login/index.php")
        assert recovery_applied.wait(timeout=0.25) is False
        assert len(apply_calls) == 1
        assert len(courses_client.calls) == 2

        # Authenticated landing resets budget -> subsequent expiry CAN recover again
        captured_callbacks["on_url_changed"](
            "https://courses.ut.edu.vn/course/view.php?id=121137"
        )
        captured_callbacks["on_url_changed"]("https://courses.ut.edu.vn/login/index.php")
        assert recovery_applied.wait(timeout=2.0)
        assert len(apply_calls) == 2
    finally:
        pane.dispose()


# ---------------------------------------------------------------------------
# 3. AppController._open_url_in_app & CoursesCoordinator wiring
# ---------------------------------------------------------------------------


def test_app_controller_open_url_in_app_authenticates_courses_alias_thnn_and_portal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gui.app_controller import AppController

    mounted_panes: list[Any] = []

    class _FakeWebViewPane(ft.Container):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__()
            self.kwargs = kwargs
            mounted_panes.append(self)

        def sync_bounds(self, *_a: Any, **_kw: Any) -> None:
            return None

        def dispose(self) -> None:
            return None

    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "super-secret")
    monkeypatch.setattr(
        "gui.components.courses.embedded_webview_host._is_windows_webview2_platform",
        lambda _page: True,
    )
    monkeypatch.setattr(
        "gui.components.courses.embedded_webview_host._webview2_runtime_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "gui.components.courses.webview_pane.CoursesWebViewPane",
        _FakeWebViewPane,
    )

    courses_client = _FakeMoodleSessionClient(
        origin="https://courses.ut.edu.vn",
        initial_cookie="c1",
        fresh_cookie="c2",
    )
    thnn_client = _FakeMoodleSessionClient(
        origin="https://thnn.ut.edu.vn",
        initial_cookie="t1",
        fresh_cookie="t2",
    )
    portal_client = _FakePortalClient()
    auth_service = UthWebViewAuthService(
        courses_client=courses_client,
        thnn_client=thnn_client,
        portal_client=portal_client,
        credentials_provider=lambda: (settings.UTH_USERNAME, settings.UTH_PASSWORD),
    )

    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace(
        width=1280,
        height=820,
        web=False,
        platform=ft.PagePlatform.WINDOWS,
        pop_dialog=lambda: None,
        update=lambda: None,
    )
    controller._nav_shell = SimpleNamespace(rail_visible=True)
    controller._app_webview_host = ft.Container(visible=False)
    controller._app_webview_pane = None
    controller.orchestrator = SimpleNamespace(
        client=courses_client,
        thnn_client=thnn_client,
    )
    controller._portal_client = portal_client
    controller._webview_auth_service = auth_service
    preview_srv = object()
    controller._courses_coordinator = SimpleNamespace(
        preview_server=preview_srv,
        _client_for=lambda src: thnn_client if src == "thnn" else courses_client,
    )

    # 1. elearning.ut.edu.vn alias -> normalized to courses.ut.edu.vn with courses client & preview_server
    controller._open_url_in_app("https://elearning.ut.edu.vn/course/view.php?id=10", "LMS")
    assert len(mounted_panes) == 1
    alias_kw = mounted_panes[-1].kwargs
    assert alias_kw["url"] == "https://courses.ut.edu.vn/course/view.php?id=10"
    assert alias_kw["source"] == "courses"
    assert alias_kw["session_client"] is courses_client
    assert alias_kw["preview_server"] is preview_srv
    assert alias_kw["webview_auth_service"] is auth_service

    # 2. thnn.ut.edu.vn -> thnn source, thnn_client, preview_server, webview_auth_service
    controller._open_url_in_app("https://thnn.ut.edu.vn/course/view.php?id=9", "THNN")
    thnn_kw = mounted_panes[-1].kwargs
    assert thnn_kw["url"] == "https://thnn.ut.edu.vn/course/view.php?id=9"
    assert thnn_kw["source"] == "thnn"
    assert thnn_kw["session_client"] is thnn_client
    assert thnn_kw["preview_server"] is preview_srv
    assert thnn_kw["webview_auth_service"] is auth_service

    # 3. portal.ut.edu.vn root -> normalized to /dashboard with webview_auth_service
    controller._open_url_in_app("https://portal.ut.edu.vn/", "Portal")
    portal_kw = mounted_panes[-1].kwargs
    assert portal_kw["url"] == "https://portal.ut.edu.vn/dashboard"
    assert portal_kw["webview_auth_service"] is auth_service

    # 4. CoursesCoordinator forwards webview_auth_service and maps elearning alias + thnn
    monkeypatch.setattr(
        "gui.controllers.courses_coordinator.CoursesWebViewPane",
        _FakeWebViewPane,
    )
    store = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|u")
    pcache = PreviewFileCache(tmp_path / "pc")
    local_srv = LocalPreviewServer()
    try:
        coord = CoursesCoordinator(
            page=controller.page,  # type: ignore[arg-type]
            catalog_service=CourseCatalogService(cache_store=store, preview_cache=pcache),
            preview_cache=pcache,
            archive_service=ArchiveService(),
            preview_server=local_srv,
            on_open_assignment_fallback=lambda _m: None,
            on_launch_url=lambda _u: None,
            courses_client=courses_client,
            thnn_client=thnn_client,
            webview_auth_service=auth_service,
        )
        coord._handle_course_url_action(
            "https://elearning.ut.edu.vn/course/view.php?id=55", "Elearning"
        )
        coord_alias_kw = mounted_panes[-1].kwargs
        assert coord_alias_kw["source"] == "courses"
        assert coord_alias_kw["url"] == "https://courses.ut.edu.vn/course/view.php?id=55"
        assert coord_alias_kw["session_client"] is courses_client
        assert coord_alias_kw["webview_auth_service"] is auth_service
    finally:
        local_srv.stop()


# ---------------------------------------------------------------------------
# 4. flet_webview (Android): cross-site Moodle bridge switch + Portal eval_js
# ---------------------------------------------------------------------------


def test_flet_webview_mobile_portal_injects_local_storage_and_courses_bridge_switches_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_webviews: list[Any] = []
    js_executed_event = threading.Event()

    class _FakeFletWebView(ft.Container):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(expand=True)
            self.url = kwargs.get("url")
            self.prevent_links = kwargs.get("prevent_links")
            self.on_page_started = kwargs.get("on_page_started")
            self.on_page_ended = kwargs.get("on_page_ended")
            self.on_web_resource_error = kwargs.get("on_web_resource_error")
            self.on_url_change = kwargs.get("on_url_change")
            self.on_console_message = kwargs.get("on_console_message")
            self.loaded_requests: list[str] = []
            self.ran_scripts: list[str] = []
            created_webviews.append(self)

        async def load_request(self, url: str) -> None:
            self.url = url
            self.loaded_requests.append(url)

        async def run_javascript(self, script: str) -> None:
            self.ran_scripts.append(script)
            js_executed_event.set()

        async def can_go_back(self) -> bool:
            return False

        async def can_go_forward(self) -> bool:
            return False

        async def get_title(self) -> str:
            return "UTHelper Mobile"

    fake_fwv_mod = types.ModuleType("flet_webview")
    fake_fwv_mod.WebView = _FakeFletWebView  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "flet_webview", fake_fwv_mod)

    android_page = SimpleNamespace(
        overlay=[],
        web=False,
        platform=ft.PagePlatform.ANDROID,
        title="UTHelper",
        width=390,
        height=844,
        update=lambda: None,
        run_task=lambda fn, *a: asyncio.run(fn(*a)),
    )

    courses_client = _FakeMoodleSessionClient(
        origin="https://courses.ut.edu.vn",
        initial_cookie="c_tok",
        fresh_cookie="c_fresh",
    )
    thnn_client = _FakeMoodleSessionClient(
        origin="https://thnn.ut.edu.vn",
        initial_cookie="t_tok",
        fresh_cookie="t_fresh",
    )
    portal_client = _FakePortalClient(token="eyJ.mobile.portal.jwt", role="sv")
    auth_service = UthWebViewAuthService(
        courses_client=courses_client,
        thnn_client=thnn_client,
        portal_client=portal_client,
        credentials_provider=lambda: ("2251120001", "never-in-js-secret"),
    )

    server = LocalPreviewServer()
    try:
        server.register_session_provider("courses", courses_client)
        server.register_session_provider("thnn", thnn_client)

        # Part A: Starting on courses.ut.edu.vn, navigating to thnn.ut.edu.vn builds a thnn bridge URL
        moodle_pane = CoursesWebViewPane(
            page=android_page,  # type: ignore[arg-type]
            source="courses",
            title="Course",
            url="https://courses.ut.edu.vn/course/view.php?id=10",
            preview_server=server,
            session_client=courses_client,
            webview_auth_service=auth_service,
            on_back=lambda: None,
            on_open_external=lambda _u: None,
        )
        try:
            assert isinstance(moodle_pane._host, EmbeddedWebViewHost)
            assert moodle_pane._host.backend == "flet_webview"
            moodle_wv = created_webviews[-1]
            assert f"/session/{server._bridge_token}/courses/course/view.php?id=10" in str(
                moodle_wv.url
            )

            expected_thnn_bridge = (
                f"/session/{server._bridge_token}/thnn/course/view.php?id=9"
            )
            assert expected_thnn_bridge in moodle_pane._build_bridge_or_target_url(
                "https://thnn.ut.edu.vn/course/view.php?id=9"
            )

            # Clicking a cross-site THNN link triggers url_change -> rewrites to THNN bridge URL once
            moodle_wv.on_url_change(
                SimpleNamespace(data="https://thnn.ut.edu.vn/course/view.php?id=9")
            )
            assert len(moodle_wv.loaded_requests) == 1
            assert expected_thnn_bridge in moodle_wv.loaded_requests[0]

            # Duplicate url_change before bridge loads does NOT loop
            moodle_wv.on_url_change(
                SimpleNamespace(data="https://thnn.ut.edu.vn/course/view.php?id=9")
            )
            assert len(moodle_wv.loaded_requests) == 1
        finally:
            moodle_pane.dispose()

        # Part B: Opening https://portal.ut.edu.vn/dashboard injects Portal localStorage via eval_js on page load
        portal_pane = CoursesWebViewPane(
            page=android_page,  # type: ignore[arg-type]
            source="courses",
            title="Portal",
            url="https://portal.ut.edu.vn/dashboard",
            preview_server=server,
            webview_auth_service=auth_service,
            on_back=lambda: None,
            on_open_external=lambda _u: None,
        )
        try:
            portal_wv = created_webviews[-1]
            assert portal_wv.url == "https://portal.ut.edu.vn/dashboard"

            # Simulate page finish -> eval_js injects Portal JWT + role + guarded replace
            portal_wv.on_page_ended(SimpleNamespace())
            assert js_executed_event.wait(timeout=2.0)

            assert len(portal_wv.ran_scripts) == 1
            injected_js = portal_wv.ran_scripts[0]
            assert 'window.location.origin === "https://portal.ut.edu.vn"' in injected_js
            assert json.dumps("eyJ.mobile.portal.jwt") in injected_js
            assert json.dumps("sv") in injected_js
            assert "window.location.replace" in injected_js
            assert "https://portal.ut.edu.vn/dashboard" in injected_js
            assert "never-in-js-secret" not in injected_js

            # Subsequent reload page_ended MUST NOT re-inject
            portal_wv.on_page_ended(SimpleNamespace())
            assert len(portal_wv.ran_scripts) == 1
        finally:
            portal_pane.dispose()
    finally:
        server.stop()


def test_embedded_webview_host_apply_auth_and_navigate_validates_url_and_handles_both_backends() -> None:
    # 1. webview2 backend delegates valid HTTPS URLs to dock and blocks unsafe schemes
    dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="https://courses.ut.edu.vn/my/",
    )
    core = _FakeWebView2Core()
    dock._core = core
    dock._ready = True
    wv2_host = EmbeddedWebViewHost(
        backend="webview2",
        url="https://courses.ut.edu.vn/my/",
        dock=dock,
    )
    wv2_host.apply_auth_and_navigate(
        [_moodle_cookie("bad", "courses.ut.edu.vn")],
        "javascript:alert(1)",
    )
    wv2_host.apply_auth_and_navigate(
        [_moodle_cookie("bad", "courses.ut.edu.vn")],
        "http://evil.example/steal",
    )
    dock._drain_commands()
    assert core.events == []

    wv2_host.apply_auth_and_navigate(
        [_moodle_cookie("good_sess", "courses.ut.edu.vn")],
        "https://courses.ut.edu.vn/course/view.php?id=77",
        init_script="",
    )
    dock._drain_commands()
    assert ("navigate", "https://courses.ut.edu.vn/course/view.php?id=77") in core.events
    assert any(
        ev[0] == "add_cookie" and ev[1][1] == "good_sess" for ev in core.events
    )
    wv2_host.dispose()

    # 2. flet_webview backend stores one-shot pending init_script and runs on page_ended
    ran_scripts: list[str] = []
    loaded_urls: list[str] = []

    class _MinimalFletWv(ft.Container):
        def __init__(self) -> None:
            super().__init__(expand=True)
            self.url = "https://portal.ut.edu.vn/login"

        async def load_request(self, url: str) -> None:
            loaded_urls.append(url)

        async def run_javascript(self, script: str) -> None:
            ran_scripts.append(script)

        async def can_go_back(self) -> bool:
            return False

        async def can_go_forward(self) -> bool:
            return False

    fake_page = SimpleNamespace(
        run_task=lambda fn, *a: asyncio.run(fn(*a)),
    )
    fwv_host = EmbeddedWebViewHost(
        backend="flet_webview",
        page=fake_page,  # type: ignore[arg-type]
        url="https://portal.ut.edu.vn/login",
        webview_control=_MinimalFletWv(),
    )
    portal_js = build_portal_local_storage_script("jwt.recovery", "sv")
    fwv_host.apply_auth_and_navigate(
        [],
        "https://portal.ut.edu.vn/dashboard",
        init_script=portal_js,
    )
    assert loaded_urls == ["https://portal.ut.edu.vn/dashboard"]
    assert ran_scripts == []

    fwv_host._handle_flet_page_ended(SimpleNamespace())
    assert ran_scripts == [portal_js]

    # One-shot: second page_ended does not re-run script
    fwv_host._handle_flet_page_ended(SimpleNamespace())
    assert ran_scripts == [portal_js]

    # Navigating away or redirecting to another origin clears pending init_script
    fwv_host.apply_auth_and_navigate(
        [],
        "https://portal.ut.edu.vn/dashboard",
        init_script=portal_js,
    )
    fwv_host._handle_flet_page_started(
        SimpleNamespace(data="https://courses.ut.edu.vn/my/")
    )
    fwv_host._handle_flet_page_ended(SimpleNamespace())
    assert ran_scripts == [portal_js]
    fwv_host.dispose()

