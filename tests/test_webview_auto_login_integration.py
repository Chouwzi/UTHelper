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

        # Authenticated landing (completed load) resets budget -> subsequent expiry CAN recover again
        captured_callbacks["on_url_changed"](
            "https://courses.ut.edu.vn/course/view.php?id=121137"
        )
        captured_callbacks["on_ready"]()
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

    # 3. portal.ut.edu.vn root -> normalized to /dashboard with webview_auth_service and preview_server
    controller._open_url_in_app("https://portal.ut.edu.vn/", "Portal")
    portal_kw = mounted_panes[-1].kwargs
    assert portal_kw["url"] == "https://portal.ut.edu.vn/dashboard"
    assert portal_kw["webview_auth_service"] is auth_service
    assert portal_kw["preview_server"] is preview_srv

    # 3b. Empty credentials preserve Portal login root URL
    monkeypatch.setattr(settings, "UTH_USERNAME", "")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")
    controller._open_url_in_app("https://portal.ut.edu.vn/", "Portal")
    assert mounted_panes[-1].kwargs["url"] == "https://portal.ut.edu.vn/"
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "super-secret")

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


# ---------------------------------------------------------------------------
# 5. Review round 1 regressions (Critical #1, #2, Important #3, #4, Minors)
# ---------------------------------------------------------------------------


def test_critical1_flet_webview_page_started_before_303_login_redirect_does_not_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []
    nav_event = threading.Event()

    class _RedirectingFletWebView(ft.Container):
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
            created.append(self)

        async def load_request(self, url: str) -> None:
            self.loaded_requests.append(url)
            nav_event.set()

        async def can_go_back(self) -> bool:
            return False

        async def can_go_forward(self) -> bool:
            return False

    fake_fwv_mod = types.ModuleType("flet_webview")
    fake_fwv_mod.WebView = _RedirectingFletWebView  # type: ignore[attr-defined]
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
        initial_cookie="old",
        fresh_cookie="still_rejected",
    )
    auth_service = UthWebViewAuthService(
        courses_client=courses_client,
        credentials_provider=lambda: ("2251120001", "pw"),
    )

    pane = CoursesWebViewPane(
        page=android_page,  # type: ignore[arg-type]
        source="courses",
        title="Course",
        url="https://courses.ut.edu.vn/course/view.php?id=121137",
        preview_server=None,
        session_client=courses_client,
        webview_auth_service=auth_service,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        wv = created[-1]
        wv.on_url_change(
            SimpleNamespace(data="https://courses.ut.edu.vn/login/index.php")
        )
        assert nav_event.wait(timeout=2.0)

        # Wait for recovery thread to finish before Android WebView fires async load callbacks
        deadline = time.monotonic() + 1.0
        while pane._recovery_in_flight and time.monotonic() < deadline:
            time.sleep(0.01)

        # Real Android WebView emits on_page_started(target) BEFORE HTTP 303 bounces to /login/index.php
        wv.on_page_started(
            SimpleNamespace(data="https://courses.ut.edu.vn/course/view.php?id=121137")
        )
        wv.on_url_change(
            SimpleNamespace(data="https://courses.ut.edu.vn/login/index.php")
        )
        wv.on_page_ended(
            SimpleNamespace(data="https://courses.ut.edu.vn/login/index.php")
        )

        # Confirm NO second recovery fires after on_page_started(target) -> 303 -> /login
        time.sleep(0.2)
        assert len(wv.loaded_requests) == 1
        assert [c for c in courses_client.calls if c["force_relogin"]] == [
            {"ensure_login": True, "verify_live": False, "force_relogin": True}
        ]
        assert auth_service.has_credentials("courses") is True
        assert auth_service.has_credentials("portal") is False
    finally:
        pane.dispose()


def test_critical1_portal_spa_dashboard_bounce_to_root_does_not_loop_and_rearms_after_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portal_client = _FakePortalClient(token="jwt.rejected.by.spa", role="sv")
    auth_service = UthWebViewAuthService(
        portal_client=portal_client,
        credentials_provider=lambda: ("2251120001", "pw"),
    )
    captured: dict[str, Any] = {}
    apply_calls: list[tuple[str, str]] = []
    recovery_done = threading.Event()

    class _SpaFakeHost:
        backend = "webview2"
        dock = None

        def control(self) -> ft.Control:
            return ft.Container(expand=True)

        def apply_auth_and_navigate(
            self,
            _cookies: list[dict[str, Any]],
            url: str,
            *,
            init_script: str = "",
        ) -> None:
            apply_calls.append((url, init_script))
            recovery_done.set()

        def navigate(self, _url: str) -> None:
            return None

        def sync_bounds(self, *_a: Any, **_kw: Any) -> None:
            return None

        def dispose(self) -> None:
            return None

    fake_host = _SpaFakeHost()

    def _fake_build(url: str, **kwargs: Any) -> _SpaFakeHost:
        captured.update(kwargs)
        return fake_host

    monkeypatch.setattr(pane_mod, "build_embedded_webview_host", _fake_build)

    pane = CoursesWebViewPane(
        page=None,
        source="courses",
        title="Portal",
        url="https://portal.ut.edu.vn/dashboard",
        webview_auth_service=auth_service,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    pane._portal_rearm_grace_seconds = 0.08
    try:
        # 1st redirect to Portal root -> recovers once
        captured["on_url_changed"]("https://portal.ut.edu.vn/")
        assert recovery_done.wait(timeout=2.0)

        # Wait for recovery worker thread to settle before WebView2 fires async navigation events
        deadline = time.monotonic() + 1.0
        while pane._recovery_in_flight and time.monotonic() < deadline:
            time.sleep(0.01)

        # SPA shell returns HTTP 200 for /dashboard -> fires SourceChanged + NavigationCompleted
        captured["on_url_changed"]("https://portal.ut.edu.vn/dashboard")
        captured["on_ready"]()
        # Portal React Router rejects JWT in JS and immediately redirects back to "/"
        captured["on_url_changed"]("https://portal.ut.edu.vn/")

        time.sleep(0.2)
        assert len(apply_calls) == 1
        assert len(portal_client.calls) == 1

        # Now simulate valid stable stay on /dashboard past grace window -> budget re-arms!
        captured["on_url_changed"]("https://portal.ut.edu.vn/dashboard")
        captured["on_ready"]()
        time.sleep(0.15)
        assert pane._recovery_tracker.can_attempt_recovery("portal") is True

        recovery_done.clear()
        captured["on_url_changed"]("https://portal.ut.edu.vn/")
        assert recovery_done.wait(timeout=2.0)
        assert len(apply_calls) == 2
    finally:
        pane.dispose()


def test_critical2_webview2_aborted_prior_nav_completed_preserves_new_init_script_and_cross_origin_clears_it() -> None:
    dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="https://portal.ut.edu.vn/login",
    )
    core = _FakeWebView2Core()
    dock._core = core
    dock._ready = True
    dock._bind_events(core)

    portal_js = build_portal_local_storage_script("jwt.recovery.1", "sv")
    dock.apply_auth_and_navigate(
        [],
        "https://portal.ut.edu.vn/dashboard",
        init_script=portal_js,
    )
    dock._drain_commands()
    core.pending_script_ops[0].complete("script-1")
    dock._drain_commands()

    assert dock._active_init_script_id == "script-1"
    assert ("navigate", "https://portal.ut.edu.vn/dashboard") in core.events

    # Aborted prior navigation (nav_id=10) fires NavigationCompleted BEFORE nav_id=11 starts
    core.callbacks["nav_completed"](
        core,
        SimpleNamespace(
            navigation_id=10,
            is_success=False,
            web_error_status="OperationCanceled",
        ),
    )
    assert dock._active_init_script_id == "script-1"
    assert ("remove_script", "script-1") not in core.events

    # Target navigation (nav_id=11) starts on Portal origin
    core.callbacks["nav_starting"](
        core,
        SimpleNamespace(
            navigation_id=11,
            uri="https://portal.ut.edu.vn/dashboard",
            cancel=False,
        ),
    )
    assert dock._active_init_script_id == "script-1"

    # Late completion for prior nav_id=10 STILL must not remove script-1
    core.callbacks["nav_completed"](
        core,
        SimpleNamespace(navigation_id=10, is_success=False),
    )
    assert dock._active_init_script_id == "script-1"
    assert ("remove_script", "script-1") not in core.events

    # Completion for matching nav_id=11 removes script-1 cleanly
    core.callbacks["nav_completed"](
        core,
        SimpleNamespace(navigation_id=11, is_success=True),
    )
    assert dock._active_init_script_id == ""
    assert ("remove_script", "script-1") in core.events

    # Cross-origin nav_starting immediately removes active script before foreign document loads
    portal_js_2 = build_portal_local_storage_script("jwt.recovery.2", "sv")
    dock.apply_auth_and_navigate(
        [],
        "https://portal.ut.edu.vn/dashboard",
        init_script=portal_js_2,
    )
    dock._drain_commands()
    core.pending_script_ops[1].complete("script-2")
    dock._drain_commands()
    assert dock._active_init_script_id == "script-2"

    core.callbacks["nav_starting"](
        core,
        SimpleNamespace(
            navigation_id=12,
            uri="https://courses.ut.edu.vn/my/",
            cancel=False,
        ),
    )
    assert dock._active_init_script_id == ""
    assert ("remove_script", "script-2") in core.events


def test_critical2_flet_webview_prior_page_ended_does_not_consume_pending_init_script() -> None:
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

    fake_page = SimpleNamespace(run_task=lambda fn, *a: asyncio.run(fn(*a)))
    fwv_host = EmbeddedWebViewHost(
        backend="flet_webview",
        page=fake_page,  # type: ignore[arg-type]
        url="https://portal.ut.edu.vn/login",
        webview_control=_MinimalFletWv(),
    )
    portal_js = build_portal_local_storage_script("jwt.flet.recovery", "sv")

    # Simulate prior same-origin page currently loading when apply_auth_and_navigate is invoked
    fwv_host._handle_flet_page_started(
        SimpleNamespace(data="https://portal.ut.edu.vn/login")
    )
    fwv_host.apply_auth_and_navigate(
        [],
        "https://portal.ut.edu.vn/dashboard",
        init_script=portal_js,
    )
    # Prior login page finishes first -> MUST NOT consume or run portal_js yet!
    fwv_host._handle_flet_page_ended(
        SimpleNamespace(data="https://portal.ut.edu.vn/login")
    )
    assert ran_scripts == []

    # Now target /dashboard starts and completes -> runs portal_js once!
    fwv_host._handle_flet_page_started(
        SimpleNamespace(data="https://portal.ut.edu.vn/dashboard")
    )
    fwv_host._handle_flet_page_ended(
        SimpleNamespace(data="https://portal.ut.edu.vn/dashboard")
    )
    assert ran_scripts == [portal_js]

    # Cross-origin url_change clears pending script so it never executes on foreign origin
    fwv_host.apply_auth_and_navigate(
        [],
        "https://portal.ut.edu.vn/dashboard",
        init_script=portal_js,
    )
    fwv_host._handle_flet_url_change(
        SimpleNamespace(data="https://docs.example.com/other")
    )
    fwv_host._handle_flet_page_ended(
        SimpleNamespace(data="https://docs.example.com/other")
    )
    assert ran_scripts == [portal_js]
    fwv_host.dispose()


def test_important3_webview2_cross_site_303_redirect_preserves_clicked_thnn_course_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    courses_client = _FakeMoodleSessionClient(
        origin="https://courses.ut.edu.vn",
        initial_cookie="c_init",
        fresh_cookie="c_fresh",
    )
    thnn_client = _FakeMoodleSessionClient(
        origin="https://thnn.ut.edu.vn",
        initial_cookie="t_init",
        fresh_cookie="t_fresh_303",
    )
    auth_service = UthWebViewAuthService(
        courses_client=courses_client,
        thnn_client=thnn_client,
        credentials_provider=lambda: ("2251120001", "pw"),
    )
    captured: dict[str, Any] = {}
    apply_calls: list[tuple[list[dict[str, Any]], str, str]] = []
    recovered_evt = threading.Event()

    class _FakeWv2Host:
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
            recovered_evt.set()

        def navigate(self, _url: str) -> None:
            return None

        def sync_bounds(self, *_a: Any, **_kw: Any) -> None:
            return None

        def dispose(self) -> None:
            return None

    fake_host = _FakeWv2Host()
    monkeypatch.setattr(
        pane_mod,
        "build_embedded_webview_host",
        lambda url, **kw: captured.update(kw) or fake_host,
    )

    pane = CoursesWebViewPane(
        page=None,
        source="courses",
        title="Courses",
        url="https://courses.ut.edu.vn/course/view.php?id=10",
        session_client=courses_client,
        webview_auth_service=auth_service,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        # Clicking a THNN link fires NavigationStarting BEFORE server 303s to /login/index.php (no wantsurl!)
        assert "on_navigation_starting" in captured
        captured["on_navigation_starting"](
            "https://thnn.ut.edu.vn/course/view.php?id=9"
        )
        captured["on_url_changed"]("https://thnn.ut.edu.vn/login/index.php")
        assert recovered_evt.wait(timeout=2.0)

        assert len(apply_calls) == 1
        cookies, target_url, script = apply_calls[0]
        assert target_url == "https://thnn.ut.edu.vn/course/view.php?id=9"
        assert script == ""
        assert any(
            c["domain"] == "thnn.ut.edu.vn" and c["value"] == "t_fresh_303"
            for c in cookies
        )
    finally:
        pane.dispose()


def test_important3_android_portal_to_moodle_cross_site_bridge_unblocks_loopback_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []

    class _FakeFletWebView(ft.Container):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(expand=True)
            self.url = kwargs.get("url")
            self.prevent_links = list(kwargs.get("prevent_links") or [])
            self.on_page_started = kwargs.get("on_page_started")
            self.on_page_ended = kwargs.get("on_page_ended")
            self.on_web_resource_error = kwargs.get("on_web_resource_error")
            self.on_url_change = kwargs.get("on_url_change")
            self.on_console_message = kwargs.get("on_console_message")
            self.loaded_requests: list[str] = []
            created.append(self)

        async def load_request(self, url: str) -> None:
            self.url = url
            self.loaded_requests.append(url)

        async def run_javascript(self, _script: str) -> None:
            return None

        async def can_go_back(self) -> bool:
            return False

        async def can_go_forward(self) -> bool:
            return False

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
    portal_client = _FakePortalClient(token="jwt.portal.bridge", role="sv")
    auth_service = UthWebViewAuthService(
        courses_client=courses_client,
        portal_client=portal_client,
        credentials_provider=lambda: ("2251120001", "pw"),
    )

    server = LocalPreviewServer()
    try:
        # Note: deliberately DO NOT pre-register courses_client on server;
        # pane should resolve it via webview_auth_service.client_for_site("courses")!
        pane = CoursesWebViewPane(
            page=android_page,  # type: ignore[arg-type]
            source="courses",
            title="Portal",
            url="https://portal.ut.edu.vn/dashboard",
            preview_server=server,
            session_client=None,
            webview_auth_service=auth_service,
            on_back=lambda: None,
            on_open_external=lambda _u: None,
        )
        try:
            wv = created[-1]
            assert "http://" in wv.prevent_links

            # User clicks a Moodle link from inside Portal on Android
            wv.on_url_change(
                SimpleNamespace(data="https://courses.ut.edu.vn/course/view.php?id=10")
            )
            assert len(wv.loaded_requests) == 1
            assert (
                f"/session/{server._bridge_token}/courses/course/view.php?id=10"
                in wv.loaded_requests[0]
            )
            # Loopback http:// allowed via prevent_links update, while dangerous schemes & external http remain blocked
            assert "http://" not in wv.prevent_links
            assert "javascript:" in wv.prevent_links
            assert pane._host is not None
            assert pane._host._validate_navigation_url("http://evil.example/steal") == ""
        finally:
            pane.dispose()
    finally:
        server.stop()


def test_important4_mobile_portal_bootstrap_suppresses_concurrent_recovery_and_clears_cross_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []
    js_done = threading.Event()
    bootstrap_started = threading.Event()
    release_bootstrap = threading.Event()

    class _SlowPortalClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, bool]] = []

        def ensure_web_session(
            self, username: str, password: str, *, force: bool = False
        ) -> tuple[str, str]:
            self.calls.append((username, password, force))
            bootstrap_started.set()
            release_bootstrap.wait(timeout=2.0)
            return "jwt.cold.mobile", "sv"

    class _FakeFletWebView(ft.Container):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(expand=True)
            self.url = kwargs.get("url")
            self.prevent_links = list(kwargs.get("prevent_links") or [])
            self.on_page_started = kwargs.get("on_page_started")
            self.on_page_ended = kwargs.get("on_page_ended")
            self.on_web_resource_error = kwargs.get("on_web_resource_error")
            self.on_url_change = kwargs.get("on_url_change")
            self.on_console_message = kwargs.get("on_console_message")
            self.ran_scripts: list[str] = []
            created.append(self)

        async def load_request(self, url: str) -> None:
            self.url = url

        async def run_javascript(self, script: str) -> None:
            self.ran_scripts.append(script)
            js_done.set()

        async def can_go_back(self) -> bool:
            return False

        async def can_go_forward(self) -> bool:
            return False

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

    # Part A: Cold SPA bounce to "/" while initial bootstrap is in flight -> NO concurrent force=True login, budget preserved
    slow_portal = _SlowPortalClient()
    auth_service = UthWebViewAuthService(
        portal_client=slow_portal,
        credentials_provider=lambda: ("2251120001", "pw"),
    )
    pane = CoursesWebViewPane(
        page=android_page,  # type: ignore[arg-type]
        source="courses",
        title="Portal",
        url="https://portal.ut.edu.vn/dashboard",
        webview_auth_service=auth_service,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        wv = created[-1]
        assert bootstrap_started.wait(timeout=2.0)

        # Cold SPA redirects to "/" and finishes page load while prepare_initial_auth is still running
        wv.on_url_change(SimpleNamespace(data="https://portal.ut.edu.vn/"))
        wv.on_page_ended(SimpleNamespace(data="https://portal.ut.edu.vn/"))

        release_bootstrap.set()
        assert js_done.wait(timeout=2.0)
        time.sleep(0.1)

        assert slow_portal.calls == [("2251120001", "pw", False)]
        assert len(wv.ran_scripts) == 1
        assert json.dumps("jwt.cold.mobile") in wv.ran_scripts[0]
        assert pane._recovery_tracker.can_attempt_recovery("portal") is True
    finally:
        pane.dispose()

    # Part B: Navigating away from Portal before page_ended clears pending script -> never evaluated on foreign origin
    fast_portal = _FakePortalClient(token="jwt.never.on.foreign", role="sv")
    auth_service_b = UthWebViewAuthService(
        portal_client=fast_portal,
        credentials_provider=lambda: ("2251120001", "pw"),
    )
    pane_b = CoursesWebViewPane(
        page=android_page,  # type: ignore[arg-type]
        source="courses",
        title="Portal",
        url="https://portal.ut.edu.vn/dashboard",
        webview_auth_service=auth_service_b,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        wv_b = created[-1]
        # Wait for background portal bootstrap to stage _pending_flet_portal_script
        deadline = time.monotonic() + 2.0
        while not pane_b._pending_flet_portal_script and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pane_b._pending_flet_portal_script

        # User navigates away to external origin before Portal page finishes
        wv_b.on_url_change(SimpleNamespace(data="https://docs.example.com/guide"))
        wv_b.on_page_ended(SimpleNamespace(data="https://docs.example.com/guide"))
        assert wv_b.ran_scripts == []
        assert pane_b._pending_flet_portal_script == ""
    finally:
        pane_b.dispose()


# ---------------------------------------------------------------------------
# 6. Review round 2 regressions (prior login completion & flet url_change race)
# ---------------------------------------------------------------------------


def test_round2_prior_login_completion_racing_after_recovery_does_not_rearm_on_webview2_or_flet_webview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Subtest A: WebView2 — prior /login/index.php NavigationCompleted(is_success=True) fires
    # AFTER _apply_recovery_bootstrap enqueues auth_navigate but BEFORE target commits.
    courses_client_wv2 = _FakeMoodleSessionClient(
        origin="https://courses.ut.edu.vn",
        initial_cookie="old_wv2",
        fresh_cookie="fresh_wv2",
    )
    auth_wv2 = UthWebViewAuthService(
        courses_client=courses_client_wv2,
        credentials_provider=lambda: ("2251120001", "pw"),
    )
    core = _FakeWebView2Core()
    dock_holder: dict[str, WindowsWebView2Dock] = {}
    recovery_enqueued = threading.Event()

    def _fake_build_wv2(url: str, **kwargs: Any) -> EmbeddedWebViewHost:
        dock = WindowsWebView2Dock(
            page_title="UTHelper",
            url=url,
            cookie_provider=kwargs.get("cookie_provider"),
            init_script_provider=kwargs.get("init_script_provider"),
            on_ready=kwargs.get("on_ready"),
            on_url_changed=kwargs.get("on_url_changed"),
            on_navigation_starting=kwargs.get("on_navigation_starting"),
        )
        orig_apply = dock.apply_auth_and_navigate

        def _spy_apply(cookies: list[dict[str, Any]], nav_url: str, *, init_script: str = "") -> None:
            orig_apply(cookies, nav_url, init_script=init_script)
            recovery_enqueued.set()

        dock.apply_auth_and_navigate = _spy_apply  # type: ignore[method-assign]
        dock._core = core
        dock._ready = True
        dock._bind_events(core)
        dock_holder["dock"] = dock
        return EmbeddedWebViewHost(
            backend="webview2",
            url=url,
            dock=dock,
            on_ready=kwargs.get("on_ready"),
            on_url_changed=kwargs.get("on_url_changed"),
            on_navigation_starting=kwargs.get("on_navigation_starting"),
        )

    monkeypatch.setattr(pane_mod, "build_embedded_webview_host", _fake_build_wv2)

    pane_wv2 = CoursesWebViewPane(
        page=None,
        source="courses",
        title="Course WV2",
        url="https://courses.ut.edu.vn/course/view.php?id=121137",
        session_client=courses_client_wv2,
        webview_auth_service=auth_wv2,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        # WebView2 commits /login/index.php via SourceChanged -> triggers recovery
        core.source = "https://courses.ut.edu.vn/login/index.php"
        core.callbacks["source"](core, SimpleNamespace())
        assert recovery_enqueued.wait(timeout=2.0)

        deadline = time.monotonic() + 1.0
        while pane_wv2._recovery_in_flight and time.monotonic() < deadline:
            time.sleep(0.01)

        # Prior /login/index.php finishes (NavigationCompleted is_success=True) BEFORE auth_navigate drains!
        core.callbacks["nav_completed"](core, SimpleNamespace(is_success=True))

        # Recovery budget MUST remain exhausted (False) — no premature re-arm!
        assert pane_wv2._recovery_tracker.can_attempt_recovery("courses") is False

        # Re-emitting login SourceChanged must NOT trigger a 2nd forced relogin
        recovery_enqueued.clear()
        core.callbacks["source"](core, SimpleNamespace())
        assert recovery_enqueued.wait(timeout=0.2) is False
        assert [c for c in courses_client_wv2.calls if c["force_relogin"]] == [
            {"ensure_login": True, "verify_live": False, "force_relogin": True}
        ]
    finally:
        pane_wv2.dispose()

    # Subtests B & C: flet_webview — (B) prior on_page_ended("/login/index.php") after recovery,
    # and (C) on_page_started(target) -> direct on_page_ended("/login/index.php") with NO url_change!
    monkeypatch.undo()
    created_flet: list[Any] = []
    flet_nav_event = threading.Event()

    class _FakeFletWv(ft.Container):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(expand=True)
            self.url = kwargs.get("url")
            self.prevent_links = list(kwargs.get("prevent_links") or [])
            self.on_page_started = kwargs.get("on_page_started")
            self.on_page_ended = kwargs.get("on_page_ended")
            self.on_web_resource_error = kwargs.get("on_web_resource_error")
            self.on_url_change = kwargs.get("on_url_change")
            self.on_console_message = kwargs.get("on_console_message")
            self.loaded_requests: list[str] = []
            created_flet.append(self)

        async def load_request(self, url: str) -> None:
            self.loaded_requests.append(url)
            flet_nav_event.set()

        async def can_go_back(self) -> bool:
            return False

        async def can_go_forward(self) -> bool:
            return False

    fake_fwv_mod = types.ModuleType("flet_webview")
    fake_fwv_mod.WebView = _FakeFletWv  # type: ignore[attr-defined]
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
    courses_client_flet = _FakeMoodleSessionClient(
        origin="https://courses.ut.edu.vn",
        initial_cookie="old_flet",
        fresh_cookie="fresh_flet",
    )
    auth_flet = UthWebViewAuthService(
        courses_client=courses_client_flet,
        credentials_provider=lambda: ("2251120001", "pw"),
    )
    pane_flet = CoursesWebViewPane(
        page=android_page,  # type: ignore[arg-type]
        source="courses",
        title="Course Mobile",
        url="https://courses.ut.edu.vn/course/view.php?id=121137",
        preview_server=None,
        session_client=courses_client_flet,
        webview_auth_service=auth_flet,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        wv = created_flet[-1]
        wv.on_url_change(
            SimpleNamespace(data="https://courses.ut.edu.vn/login/index.php")
        )
        assert flet_nav_event.wait(timeout=2.0)

        deadline = time.monotonic() + 1.0
        while pane_flet._recovery_in_flight and time.monotonic() < deadline:
            time.sleep(0.01)

        # (B) Prior login page finishes after _apply_recovery_bootstrap -> MUST NOT re-arm!
        wv.on_page_ended(
            SimpleNamespace(data="https://courses.ut.edu.vn/login/index.php")
        )
        assert pane_flet._recovery_tracker.can_attempt_recovery("courses") is False

        # (C) Target starts, server 303s directly to on_page_ended("/login/index.php") WITHOUT on_url_change!
        wv.on_page_started(
            SimpleNamespace(data="https://courses.ut.edu.vn/course/view.php?id=121137")
        )
        wv.on_page_ended(
            SimpleNamespace(data="https://courses.ut.edu.vn/login/index.php")
        )
        time.sleep(0.2)
        assert pane_flet._current_url == "https://courses.ut.edu.vn/login/index.php"
        assert pane_flet._recovery_tracker.can_attempt_recovery("courses") is False
        assert len(wv.loaded_requests) == 1
        assert [c for c in courses_client_flet.calls if c["force_relogin"]] == [
            {"ensure_login": True, "verify_live": False, "force_relogin": True}
        ]
    finally:
        pane_flet.dispose()

    # Subtest D: _on_host_error cancels active Portal re-arm grace timer
    portal_client = _FakePortalClient(token="jwt.err.cancel", role="sv")
    auth_portal = UthWebViewAuthService(
        portal_client=portal_client,
        credentials_provider=lambda: ("2251120001", "pw"),
    )
    pane_portal = CoursesWebViewPane(
        page=android_page,  # type: ignore[arg-type]
        source="courses",
        title="Portal Err",
        url="https://portal.ut.edu.vn/dashboard",
        webview_auth_service=auth_portal,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    pane_portal._portal_rearm_grace_seconds = 0.08
    try:
        assert pane_portal._recovery_tracker.consume_recovery_attempt("portal") is True
        pane_portal._portal_recovery_pending_rearm = True
        pane_portal._on_host_ready()
        assert pane_portal._portal_rearm_timer is not None
        pane_portal._on_host_error("Navigation failed: timeout")
        assert pane_portal._portal_rearm_timer is None
        time.sleep(0.15)
        assert pane_portal._recovery_tracker.can_attempt_recovery("portal") is False
    finally:
        pane_portal.dispose()


def test_round2_flet_webview_prior_same_origin_url_change_does_not_bypass_requires_start() -> None:
    ran_scripts: list[str] = []

    class _MinimalFletWv(ft.Container):
        def __init__(self) -> None:
            super().__init__(expand=True)
            self.url = "https://portal.ut.edu.vn/login"

        async def load_request(self, url: str) -> None:
            self.url = url

        async def run_javascript(self, script: str) -> None:
            ran_scripts.append(script)

        async def can_go_back(self) -> bool:
            return False

        async def can_go_forward(self) -> bool:
            return False

        async def reload(self) -> None:
            return None

    fake_page = SimpleNamespace(run_task=lambda fn, *a: asyncio.run(fn(*a)))
    fwv_host = EmbeddedWebViewHost(
        backend="flet_webview",
        page=fake_page,  # type: ignore[arg-type]
        url="https://portal.ut.edu.vn/login",
        webview_control=_MinimalFletWv(),
    )
    portal_js = build_portal_local_storage_script("jwt.round2.strict", "sv")

    # Prior same-origin login page is actively loading
    fwv_host._handle_flet_page_started(
        SimpleNamespace(data="https://portal.ut.edu.vn/login")
    )
    fwv_host.apply_auth_and_navigate(
        [],
        "https://portal.ut.edu.vn/dashboard",
        init_script=portal_js,
    )
    # Prior in-flight same-origin login page emits url_change("/login") -> page_ended("/login")
    # BEFORE load_request("/dashboard") starts — MUST NOT bypass requires_start or consume portal_js!
    fwv_host._handle_flet_url_change(
        SimpleNamespace(data="https://portal.ut.edu.vn/login")
    )
    fwv_host._handle_flet_page_ended(
        SimpleNamespace(data="https://portal.ut.edu.vn/login")
    )
    assert ran_scripts == []

    # Target /dashboard starts and finishes -> runs portal_js once!
    fwv_host._handle_flet_page_started(
        SimpleNamespace(data="https://portal.ut.edu.vn/dashboard")
    )
    fwv_host._handle_flet_page_ended(
        SimpleNamespace(data="https://portal.ut.edu.vn/dashboard")
    )
    assert ran_scripts == [portal_js]

    # reload() clears any pending init_script on flet_webview
    fwv_host.apply_auth_and_navigate(
        [],
        "https://portal.ut.edu.vn/dashboard",
        init_script=portal_js,
    )
    fwv_host.reload()
    fwv_host._handle_flet_page_started(
        SimpleNamespace(data="https://portal.ut.edu.vn/dashboard")
    )
    fwv_host._handle_flet_page_ended(
        SimpleNamespace(data="https://portal.ut.edu.vn/dashboard")
    )
    assert ran_scripts == [portal_js]
    fwv_host.dispose()


# ---------------------------------------------------------------------------
# 7. Review round 3 regressions (STA COM safety, /session/ spoof guard, page_ended order)
# ---------------------------------------------------------------------------


def test_round3_webview2_current_url_and_portal_rearm_timer_never_touch_core_source_off_sta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sta_tid = threading.get_ident()
    off_sta_reads: list[int] = []

    class _StaEnforcingCore(_FakeWebView2Core):
        def __init__(self) -> None:
            super().__init__()
            self._sta_source = ""

        @property
        def source(self) -> str:
            cur_tid = threading.get_ident()
            if cur_tid != sta_tid:
                off_sta_reads.append(cur_tid)
                raise OSError("RPC_E_WRONG_THREAD")
            return self._sta_source

        @source.setter
        def source(self, val: str) -> None:
            self._sta_source = val

    core = _StaEnforcingCore()
    dock_holder: dict[str, WindowsWebView2Dock] = {}

    def _fake_build_wv2(url: str, **kwargs: Any) -> EmbeddedWebViewHost:
        dock = WindowsWebView2Dock(
            page_title="UTHelper",
            url=url,
            on_ready=kwargs.get("on_ready"),
            on_url_changed=kwargs.get("on_url_changed"),
            on_navigation_starting=kwargs.get("on_navigation_starting"),
        )
        dock._core = core
        dock._ready = True
        dock._bind_events(core)
        dock_holder["dock"] = dock
        return EmbeddedWebViewHost(
            backend="webview2",
            url=url,
            dock=dock,
            on_ready=kwargs.get("on_ready"),
            on_url_changed=kwargs.get("on_url_changed"),
            on_navigation_starting=kwargs.get("on_navigation_starting"),
        )

    monkeypatch.setattr(pane_mod, "build_embedded_webview_host", _fake_build_wv2)

    portal_client = _FakePortalClient(token="jwt.sta.safe", role="sv")
    auth_portal = UthWebViewAuthService(
        portal_client=portal_client,
        credentials_provider=lambda: ("2251120001", "pw"),
    )
    pane = CoursesWebViewPane(
        page=None,
        source="courses",
        title="Portal STA",
        url="https://portal.ut.edu.vn/dashboard",
        webview_auth_service=auth_portal,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    pane._portal_rearm_grace_seconds = 0.05
    try:
        dock = dock_holder["dock"]
        # Commit /dashboard on STA thread
        core.source = "https://portal.ut.edu.vn/dashboard"
        core.callbacks["source"](core, SimpleNamespace())

        # Off-STA worker thread reading dock.current_url MUST NOT touch core.source
        worker_urls: list[str] = []
        worker_errors: list[BaseException] = []

        def _off_sta_read() -> None:
            try:
                worker_urls.append(dock.current_url)
            except BaseException as exc:  # pragma: no cover - regression guard
                worker_errors.append(exc)

        t = threading.Thread(target=_off_sta_read)
        t.start()
        t.join(timeout=2.0)
        assert worker_errors == []
        assert worker_urls == ["https://portal.ut.edu.vn/dashboard"]
        assert off_sta_reads == []

        # Portal re-arm Timer runs on a non-STA thread and must re-arm cleanly without touching core.source
        assert pane._recovery_tracker.consume_recovery_attempt("portal") is True
        pane._portal_recovery_pending_rearm = True
        core.callbacks["nav_completed"](core, SimpleNamespace(is_success=True))

        time.sleep(0.15)
        assert off_sta_reads == []
        assert pane._recovery_tracker.can_attempt_recovery("portal") is True
    finally:
        pane.dispose()


def test_round3_external_session_url_cannot_spoof_bridge_or_return_stale_current_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        pane_mod._extract_bridged_uth_url(
            "https://evil.example/session/tok123/courses/course/view.php?id=9"
        )
        == ""
    )
    assert (
        pane_mod._extract_bridged_uth_url(
            "http://127.0.0.1:54321/session/tok123/courses/course/view.php?id=9"
        )
        == "https://courses.ut.edu.vn/course/view.php?id=9"
    )

    captured: dict[str, Any] = {}

    class _FakeHost:
        backend = "flet_webview"
        dock = None
        current_url = "https://courses.ut.edu.vn/course/view.php?id=10"

        def control(self) -> ft.Control:
            return ft.Container(expand=True)

        def navigate(self, _url: str) -> None:
            return None

        def sync_bounds(self, *_a: Any, **_kw: Any) -> None:
            return None

        def dispose(self) -> None:
            return None

    fake_host = _FakeHost()
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
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        assert pane._recovery_tracker.consume_recovery_attempt("courses") is True
        pane._recovery_awaiting_commit.add("courses")

        spoof_url = "https://evil.example/session/tok123/courses/course/view.php?id=9"
        fake_host.current_url = spoof_url
        captured["on_url_changed"](spoof_url)
        captured["on_ready"]()

        assert pane._resolved_current_url() == spoof_url
        assert pane._recovery_tracker.can_attempt_recovery("courses") is False
        assert "courses" in pane._recovery_awaiting_commit

        # External /session/foo must return host_url, NOT fall through to stale self._current_url
        pane._current_url = "https://courses.ut.edu.vn/course/view.php?id=10"
        fake_host.current_url = "https://evil.example/session/foo"
        assert pane._resolved_current_url() == "https://evil.example/session/foo"
    finally:
        pane.dispose()


def test_round3_flet_page_ended_does_not_consume_init_script_queued_by_on_url_changed() -> None:
    ran_scripts: list[str] = []

    class _MinimalFletWv(ft.Container):
        def __init__(self) -> None:
            super().__init__(expand=True)
            self.url = "https://portal.ut.edu.vn/dashboard"

        async def load_request(self, url: str) -> None:
            self.url = url

        async def run_javascript(self, script: str) -> None:
            ran_scripts.append(script)

        async def can_go_back(self) -> bool:
            return False

        async def can_go_forward(self) -> bool:
            return False

    fake_page = SimpleNamespace(run_task=lambda fn, *a: asyncio.run(fn(*a)))
    portal_js = build_portal_local_storage_script("jwt.sync.callback", "sv")
    host_holder: dict[str, EmbeddedWebViewHost] = {}

    def _on_url_changed(u: str) -> None:
        if u == "https://portal.ut.edu.vn/login":
            host_holder["host"].apply_auth_and_navigate(
                [],
                "https://portal.ut.edu.vn/dashboard",
                init_script=portal_js,
            )

    fwv_host = EmbeddedWebViewHost(
        backend="flet_webview",
        page=fake_page,  # type: ignore[arg-type]
        url="https://portal.ut.edu.vn/dashboard",
        webview_control=_MinimalFletWv(),
        on_url_changed=_on_url_changed,
    )
    host_holder["host"] = fwv_host

    # Finishing page reports raw_ended=/login -> on_url_changed queues portal_js for /dashboard;
    # the finishing /login page_ended handler MUST NOT consume portal_js immediately!
    fwv_host._handle_flet_page_ended(
        SimpleNamespace(data="https://portal.ut.edu.vn/login")
    )
    assert ran_scripts == []

    # When /dashboard actually starts & finishes, portal_js runs once!
    fwv_host._handle_flet_page_started(
        SimpleNamespace(data="https://portal.ut.edu.vn/dashboard")
    )
    fwv_host._handle_flet_page_ended(
        SimpleNamespace(data="https://portal.ut.edu.vn/dashboard")
    )
    assert ran_scripts == [portal_js]
    fwv_host.dispose()

