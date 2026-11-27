"""TDD regression suite for Hotfix 2.6.4: layer-aware WebView2 occlusion & desktop speed."""

from __future__ import annotations

import gzip
import io
from email.message import Message
from http.cookiejar import Cookie
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
import urllib.request

import flet as ft
import pytest

from config import settings
from core.client import MoodleClient
from gui.components.courses import embedded_webview_host as host_mod
from gui.components.courses.embedded_webview_host import (
    EmbeddedWebViewHost,
    WindowsWebView2Dock,
    build_embedded_webview_host,
    clear_global_webview_occlusion,
    clear_persisted_webview2_profiles,
    is_globally_occluded,
    set_global_webview_occlusion,
)
from gui.components.courses.webview_pane import CoursesWebViewPane


def _make_cookie(name: str, value: str, domain: str = "courses.ut.edu.vn") -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
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


def test_occlusion_contract_app_overlay_ignores_detail_view_and_honors_modals() -> None:
    clear_global_webview_occlusion()
    try:
        assert host_mod._COURSES_PANE_ONLY_OCCLUSION_REASONS == frozenset({"detail_view"})
        assert is_globally_occluded() is False
        assert is_globally_occluded("courses_pane") is False
        assert is_globally_occluded("app_overlay") is False

        set_global_webview_occlusion("detail_view", True)
        assert is_globally_occluded() is True
        assert is_globally_occluded("courses_pane") is True
        assert is_globally_occluded("app_overlay") is False

        for global_reason in (
            "modal_dialog",
            "quiz_player",
            "desktop_filter",
            "webview_menu",
        ):
            set_global_webview_occlusion(global_reason, True)
            assert is_globally_occluded("courses_pane") is True
            assert is_globally_occluded("app_overlay") is True
            set_global_webview_occlusion(global_reason, False)
            assert is_globally_occluded("courses_pane") is True
            assert is_globally_occluded("app_overlay") is False

        set_global_webview_occlusion("modal_dialog", True, token="dlg-tok-1")
        assert is_globally_occluded("app_overlay") is True
        set_global_webview_occlusion("modal_dialog", False, token="dlg-tok-1")
        assert is_globally_occluded("app_overlay") is False
        assert is_globally_occluded("courses_pane") is True
    finally:
        clear_global_webview_occlusion()


def test_set_global_webview_occlusion_notifies_app_overlay_dock_when_modal_opens_over_detail_view() -> None:
    clear_global_webview_occlusion()
    courses_dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="https://courses.ut.edu.vn/course/view.php?id=1",
        occlusion_layer="courses_pane",
    )
    overlay_dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="https://courses.ut.edu.vn/mod/assign/view.php?id=2",
        occlusion_layer="app_overlay",
    )
    overlay_events: list[bool] = []
    overlay_dock._notify_occlusion_changed = lambda: overlay_events.append(  # type: ignore[method-assign]
        overlay_dock._effective_visible()
    )
    try:
        set_global_webview_occlusion("detail_view", True)
        assert courses_dock._effective_visible() is False
        assert overlay_dock._effective_visible() is True
        assert overlay_events == [True]

        # Opening a modal dialog while detail_view is ALREADY active MUST notify overlay_dock to hide!
        set_global_webview_occlusion("modal_dialog", True)
        assert courses_dock._effective_visible() is False
        assert overlay_dock._effective_visible() is False
        assert overlay_events == [True, False]

        # Closing the modal dialog while detail_view stays active MUST notify overlay_dock to restore!
        set_global_webview_occlusion("modal_dialog", False)
        assert courses_dock._effective_visible() is False
        assert overlay_dock._effective_visible() is True
        assert overlay_events == [True, False, True]
    finally:
        clear_global_webview_occlusion()
        courses_dock.dispose()
        overlay_dock.dispose()


def test_webview_pane_and_host_builder_forward_occlusion_layer_to_dock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(host_mod.sys, "platform", "win32")
    monkeypatch.setattr(host_mod, "_webview2_runtime_available", lambda: True)
    monkeypatch.setattr(WindowsWebView2Dock, "start", lambda self: None)

    win_page = SimpleNamespace(
        web=False,
        platform=ft.PagePlatform.WINDOWS,
        title="UTHelper",
        width=1200,
        height=800,
        update=lambda: None,
    )

    host_default = build_embedded_webview_host(
        "https://courses.ut.edu.vn/my/",
        page=win_page,  # type: ignore[arg-type]
    )
    assert host_default is not None and host_default.dock is not None
    assert host_default.dock._occlusion_layer == "courses_pane"
    host_default.dispose()

    pane_overlay = CoursesWebViewPane(
        page=win_page,  # type: ignore[arg-type]
        source="courses",
        title="Assignment",
        url="https://courses.ut.edu.vn/mod/assign/view.php?id=99",
        occlusion_layer="app_overlay",
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        assert pane_overlay._occlusion_layer == "app_overlay"
        assert pane_overlay._dock is not None
        assert pane_overlay._dock._occlusion_layer == "app_overlay"
    finally:
        pane_overlay.dispose()


def test_find_parent_hwnd_survives_process32next_error_no_more_files_and_caches_live_hwnd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if hasattr(host_mod, "_CACHED_PARENT_HWNDS"):
        host_mod._CACHED_PARENT_HWNDS.clear()

    class _PyWinError(Exception):
        def __init__(self, winerror: int, funcname: str, strerror: str) -> None:
            super().__init__(winerror, funcname, strerror)
            self.winerror = winerror

    snapshot_calls = 0

    def _spy_collect(pid: int) -> set[int]:
        nonlocal snapshot_calls
        snapshot_calls += 1
        return {pid, 4242}

    def _enum_raising_error_18(cb: Any, ctx: Any) -> None:
        cb(777, ctx)
        raise _PyWinError(18, "EnumWindows", "There are no more files.")

    monkeypatch.setattr(WindowsWebView2Dock, "_collect_child_pids", staticmethod(_spy_collect))
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            IsWindow=lambda hwnd: hwnd == 777,
            IsWindowVisible=lambda hwnd: hwnd == 777,
            GetWindowText=lambda hwnd: "UTHelper" if hwnd == 777 else "",
            GetClassName=lambda hwnd: "FLUTTER_RUNNER_WIN32_WINDOW" if hwnd == 777 else "",
            EnumWindows=_enum_raising_error_18,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _hwnd: (1, 4242)),
    )

    assert WindowsWebView2Dock._find_parent_hwnd("UTHelper") == 777
    assert snapshot_calls == 1

    # Subsequent lookup hits verified live parent HWND cache without re-running snapshot
    assert WindowsWebView2Dock._find_parent_hwnd("UTHelper") == 777
    assert snapshot_calls == 1


def test_remote_https_page_reveals_at_dom_content_loaded_and_keeps_hwnd_visible_on_in_page_nav(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loading_events: list[bool] = []
    dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="https://courses.ut.edu.vn/mod/assign/view.php?id=101",
    )
    host = EmbeddedWebViewHost(
        backend="webview2",
        url=dock._url,
        dock=dock,
        on_loading_changed=loading_events.append,
    )
    dock._on_loading_changed = host._handle_webview2_loading
    monkeypatch.setattr(dock, "_apply_window_bounds", lambda **_kw: None)

    callbacks: dict[str, Any] = {}

    class _Core:
        can_go_back = False
        can_go_forward = False
        source = "https://courses.ut.edu.vn/mod/assign/view.php?id=101"
        document_title = "Assignment 101"

        def add_document_title_changed(self, fn: Any) -> None:
            callbacks["title"] = fn

        def add_source_changed(self, fn: Any) -> None:
            callbacks["source"] = fn

        def add_navigation_starting(self, fn: Any) -> None:
            callbacks["nav_start"] = fn

        def add_dom_content_loaded(self, fn: Any) -> None:
            callbacks["dom_ready"] = fn

        def add_navigation_completed(self, fn: Any) -> None:
            callbacks["nav_done"] = fn

        def add_history_changed(self, fn: Any) -> None:
            callbacks["history"] = fn

        def add_web_message_received(self, fn: Any) -> None:
            callbacks["web_msg"] = fn

        def add_new_window_requested(self, fn: Any) -> None:
            callbacks["new_win"] = fn

    core = _Core()
    dock._core = core
    dock._bind_events(core)
    assert "dom_ready" in callbacks

    # 1. Initial navigation starts -> paint_ready is False
    callbacks["nav_start"](
        core,
        SimpleNamespace(
            uri="https://courses.ut.edu.vn/mod/assign/view.php?id=101",
            cancel=False,
        ),
    )
    assert dock._paint_ready is False
    assert loading_events[-1] is True

    # 2. DOMContentLoaded fires BEFORE slow subresources finish -> reveals HWND immediately!
    callbacks["dom_ready"](core, SimpleNamespace())
    assert dock._paint_ready is True
    assert host._loading_ring is not None and host._loading_ring.visible is False
    assert loading_events[-1] is False

    # 3. Clicking an in-page Moodle link keeps HWND visible (_paint_ready stays True) while pulsing progress bar
    callbacks["nav_start"](
        core,
        SimpleNamespace(
            uri="https://courses.ut.edu.vn/mod/assign/view.php?id=102",
            cancel=False,
        ),
    )
    assert dock._paint_ready is True
    assert host._loading_ring.visible is False
    assert loading_events[-1] is True

    core.source = "https://courses.ut.edu.vn/mod/assign/view.php?id=102"
    callbacks["dom_ready"](core, SimpleNamespace())
    assert dock._paint_ready is True
    assert loading_events[-1] is False

    # 4. Isolated local Office/PDF viewer STILL waits strictly for __UTH_VIEWER_READY__!
    viewer_dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="http://127.0.0.1:45678/viewer/tok/docx_host.html",
    )
    monkeypatch.setattr(viewer_dock, "_apply_window_bounds", lambda **_kw: None)
    v_cbs: dict[str, Any] = {}
    v_core = _Core()
    v_core.source = viewer_dock._url
    v_core.add_dom_content_loaded = lambda fn: v_cbs.__setitem__("dom_ready", fn)  # type: ignore[method-assign]
    v_core.add_navigation_completed = lambda fn: v_cbs.__setitem__("nav_done", fn)  # type: ignore[method-assign]
    viewer_dock._core = v_core
    viewer_dock._bind_events(v_core)
    v_cbs["dom_ready"](v_core, SimpleNamespace())
    assert viewer_dock._paint_ready is False
    v_cbs["nav_done"](v_core, SimpleNamespace(is_success=True))
    assert viewer_dock._paint_ready is False

    dock.dispose()
    viewer_dock.dispose()


def test_sweep_stale_profiles_runs_at_most_once_per_session_until_persisted_profiles_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_root = tmp_path / "webview2_profile"
    legacy_dir = profile_root / "EBWebView" / "uthlegacy_1"
    acct_dir = profile_root / "EBWebView" / "acct_keep"
    legacy_dir.mkdir(parents=True)
    acct_dir.mkdir(parents=True)

    if hasattr(host_mod, "_SWEPT_PROFILE_ROOTS"):
        host_mod._SWEPT_PROFILE_ROOTS.clear()

    host_mod._sweep_stale_profiles(profile_root, active_profile="acct_keep")
    assert not legacy_dir.exists()
    assert acct_dir.is_dir()

    # Second sweep in the same session must short-circuit with zero disk iterdir() calls
    iterdir_spy = Mock(side_effect=AssertionError("Redundant disk scan in _sweep_stale_profiles"))
    monkeypatch.setattr(Path, "iterdir", iterdir_spy)
    host_mod._sweep_stale_profiles(profile_root, active_profile="acct_keep")
    iterdir_spy.assert_not_called()

    monkeypatch.undo()
    clear_persisted_webview2_profiles(keep_profile_name="acct_keep", profile_root=profile_root)
    assert hasattr(host_mod, "_SWEPT_PROFILE_ROOTS")
    assert len(host_mod._SWEPT_PROFILE_ROOTS) == 0


def test_shared_webview2_sta_reuses_cached_environment_across_docks_and_releases_on_profile_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_root = tmp_path / "wv2_env_cache"
    profile_root.mkdir(parents=True)
    env_creations: list[str] = []
    ctrl_creations: list[str] = []

    class _FakeOp:
        def __init__(self, result: Any) -> None:
            self._result = result
            self._cb: Any = None

        @property
        def completed(self) -> Any:
            return self._cb

        @completed.setter
        def completed(self, cb: Any) -> None:
            self._cb = cb
            if callable(cb):
                cb(self, "Completed")

        def get_results(self) -> Any:
            return self._result

    class _FakeEnv:
        def create_core_webview2_controller_options(self) -> Any:
            return SimpleNamespace(profile_name="", is_in_private_mode_enabled=True)

        def create_core_webview2_controller_async(self, _wref: Any, opts: Any) -> _FakeOp:
            ctrl_creations.append(opts.profile_name)
            core = SimpleNamespace(
                settings=SimpleNamespace(
                    is_status_bar_enabled=True,
                    are_dev_tools_enabled=True,
                    are_host_objects_allowed=True,
                ),
                cookie_manager=None,
                navigate=lambda _u: None,
                add_document_title_changed=lambda _cb: None,
                add_source_changed=lambda _cb: None,
                add_navigation_starting=lambda _cb: None,
                add_dom_content_loaded=lambda _cb: None,
                add_navigation_completed=lambda _cb: None,
                add_new_window_requested=lambda _cb: None,
            )
            ctrl = SimpleNamespace(core_webview2=core, is_visible=False, close=lambda: None)
            return _FakeOp(ctrl)

    fake_wv2 = SimpleNamespace(
        CoreWebView2Environment=SimpleNamespace(
            create_with_options_async=lambda _bf, udf, _opt: (
                env_creations.append(udf) or _FakeOp(_FakeEnv())
            )
        ),
        CoreWebView2ControllerWindowReference=SimpleNamespace(
            create_from_window_handle=lambda h: h
        ),
    )

    cache = host_mod._WebView2EnvironmentCache()
    env1_holder: list[Any] = []
    env2_holder: list[Any] = []
    cache.acquire_async(fake_wv2, str(profile_root), env1_holder.append, lambda _e: None)
    cache.acquire_async(fake_wv2, str(profile_root), env2_holder.append, lambda _e: None)

    assert len(env_creations) == 1
    assert len(env1_holder) == 1 and env1_holder[0] is env2_holder[0]

    cache.clear(profile_root)
    env3_holder: list[Any] = []
    cache.acquire_async(fake_wv2, str(profile_root), env3_holder.append, lambda _e: None)
    assert len(env_creations) == 2
    assert env3_holder[0] is not env1_holder[0]


def test_moodle_client_open_html_requests_gzip_and_reuses_cookie_only_live_session_in_zero_ms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret-pw")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    client = MoodleClient("https://courses.ut.edu.vn")
    seen_headers: list[dict[str, str]] = []
    html_payload = '<html><body><script>M.cfg={"sesskey":"skGzip1"};</script></body></html>'
    gz_bytes = gzip.compress(html_payload.encode("utf-8"))

    class _GzResp:
        def __init__(self, url: str, raw: bytes, *, gzipped: bool = True) -> None:
            self._url = url
            self._stream = io.BytesIO(raw)
            self.headers = Message()
            self.headers["Content-Type"] = "text/html; charset=utf-8"
            if gzipped:
                self.headers["Content-Encoding"] = "gzip"

        def geturl(self) -> str:
            return self._url

        def info(self) -> Message:
            return self.headers

        def read(self, amt: int = -1) -> bytes:
            return self._stream.read() if amt < 0 else self._stream.read(amt)

        def __enter__(self) -> "_GzResp":
            return self

        def __exit__(self, *_a: Any) -> bool:
            return False

    class _Opener:
        def open(self, req: urllib.request.Request, timeout: float = 20) -> _GzResp:
            del timeout
            seen_headers.append(
                {str(k).lower(): str(v) for k, v in req.header_items()}
            )
            return _GzResp("https://courses.ut.edu.vn/my/", gz_bytes, gzipped=True)

    client._html_opener = _Opener()
    final_url, decoded = client._open_html("/my/")
    assert final_url == "https://courses.ut.edu.vn/my/"
    assert "skGzip1" in decoded
    assert seen_headers[0].get("accept-encoding") == "gzip"

    # Oversized (>2 MiB decompressed) gzip HTML payload is still rejected safely
    oversized_gz = gzip.compress(b"A" * (2 * 1024 * 1024 + 4096))

    class _OversizedOpener:
        def open(self, _req: urllib.request.Request, timeout: float = 20) -> _GzResp:
            del timeout
            return _GzResp("https://courses.ut.edu.vn/my/", oversized_gz, gzipped=True)

    client._html_opener = _OversizedOpener()
    with pytest.raises(ValueError, match="too large"):
        client._open_html("/my/")

    # Cookie-only authenticated session (_sesskey == "" from require_sesskey=False, fresh timestamp)
    # MUST return cookies in 0ms without triggering a redundant login_session() or network probe!
    import time as _time

    client._sesskey = ""
    client._session_owner = "2251120001"
    client._session_authenticated_at = _time.monotonic()
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "cookie_only_fresh"))

    forbidden_login = Mock(side_effect=AssertionError("Unexpected login_session on fresh cookie"))
    forbidden_ajax = Mock(side_effect=AssertionError("Unexpected AJAX probe on fresh cookie"))
    monkeypatch.setattr(client, "login_session", forbidden_login)
    monkeypatch.setattr(client, "call_ajax_api", forbidden_ajax)

    exported = client.get_session_cookies(ensure_login=True, verify_live=True)
    assert [c["value"] for c in exported] == ["cookie_only_fresh"]
    forbidden_login.assert_not_called()
    forbidden_ajax.assert_not_called()


def test_env_cache_clear_during_inflight_acquire_never_orphans_waiters_and_skips_release_for_kept_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_root = tmp_path / "wv2_race_root"
    profile_root.mkdir(parents=True)
    inflight_ops: list[Any] = []

    class _DeferredOp:
        def __init__(self, result: Any) -> None:
            self._result = result
            self.completed: Any = None

        def finish(self) -> None:
            if callable(self.completed):
                self.completed(self, "Completed")

        def get_results(self) -> Any:
            return self._result

    fake_wv2 = SimpleNamespace(
        CoreWebView2Environment=SimpleNamespace(
            create_with_options_async=lambda _bf, _udf, _opt: (
                inflight_ops.append(_DeferredOp(SimpleNamespace(tag=len(inflight_ops) + 1)))
                or inflight_ops[-1]
            )
        )
    )

    cache = host_mod._WebView2EnvironmentCache()
    waiter1_results: list[Any] = []
    waiter1_errors: list[str] = []
    cache.acquire_async(
        fake_wv2,
        str(profile_root),
        waiter1_results.append,
        waiter1_errors.append,
        profile_name="acct_active",
    )
    assert len(inflight_ops) == 1

    # Simulate concurrent clear() while create_with_options_async is in flight!
    cache.clear(profile_root)

    # Completing the in-flight op MUST still resolve waiter1 (never orphan/hang),
    # while NOT caching the pre-clear environment for subsequent callers!
    inflight_ops[0].finish()
    assert len(waiter1_results) == 1 and waiter1_errors == []

    waiter2_results: list[Any] = []
    cache.acquire_async(
        fake_wv2,
        str(profile_root),
        waiter2_results.append,
        lambda _e: None,
        profile_name="acct_active",
    )
    assert len(inflight_ops) == 2
    inflight_ops[1].finish()
    assert len(waiter2_results) == 1
    assert waiter2_results[0] is not waiter1_results[0]

    # Startup sweep with keep_profile_name matching the only active profile MUST NOT release warm env!
    real_cache = host_mod._SHARED_WV2_STA._env_cache
    real_cache.clear(profile_root)
    real_cache.acquire_async(
        fake_wv2,
        str(profile_root),
        lambda _e: None,
        lambda _e: None,
        profile_name="acct_active",
    )
    inflight_ops[-1].finish()
    assert host_mod._SHARED_WV2_STA.has_only_kept_profile("acct_active", profile_root) is True
    assert host_mod._SHARED_WV2_STA.has_only_kept_profile("acct_other", profile_root) is False

    release_spy = Mock()
    monkeypatch.setattr(host_mod._SHARED_WV2_STA, "release_environments", release_spy)
    stale_dir = profile_root / "EBWebView" / "acct_stale"
    kept_dir = profile_root / "EBWebView" / "acct_active"
    stale_dir.mkdir(parents=True)
    kept_dir.mkdir(parents=True)
    clear_persisted_webview2_profiles(
        keep_profile_name="acct_active", profile_root=profile_root
    )
    assert not stale_dir.exists()
    assert kept_dir.is_dir()
    release_spy.assert_not_called()

    # Account switch where another profile ("acct_stale") WAS opened MUST release cached env!
    real_cache.acquire_async(
        fake_wv2,
        str(profile_root),
        lambda _e: None,
        lambda _e: None,
        profile_name="acct_stale",
    )
    clear_persisted_webview2_profiles(
        keep_profile_name="acct_active", profile_root=profile_root
    )
    release_spy.assert_called_once_with(profile_root)
    real_cache.clear(profile_root)


def test_read_html_response_bytes_streams_large_multi_chunk_gzip_on_wrapper_response() -> None:
    import os as _os

    # >90 KB incompressible random hex payload so compressed gzip size exceeds 64 KB (multi-chunk!)
    large_body = (
        "<html><body>"
        + _os.urandom(90 * 1024).hex()
        + '<script>M.cfg={"sesskey":"skTail99"};</script></body></html>'
    )
    gz_large = gzip.compress(large_body.encode("utf-8"), compresslevel=1)
    assert len(gz_large) > 75 * 1024

    class _CustomWrapperResp:
        def __init__(self, raw: bytes) -> None:
            self._buf = io.BytesIO(raw)
            self.headers = Message()
            self.headers["Content-Type"] = "text/html; charset=utf-8"
            self.headers["Content-Encoding"] = "gzip"

        def geturl(self) -> str:
            return "https://courses.ut.edu.vn/course/view.php?id=55"

        def info(self) -> Message:
            return self.headers

        def read(self, amt: int = -1) -> bytes:
            return self._buf.read() if amt < 0 else self._buf.read(amt)

        def __enter__(self) -> "_CustomWrapperResp":
            return self

        def __exit__(self, *_a: Any) -> bool:
            return False

    client = MoodleClient("https://courses.ut.edu.vn")
    client._html_opener = SimpleNamespace(
        open=lambda _req, timeout=20: _CustomWrapperResp(gz_large)
    )
    final_url, html_text = client._open_html("/course/view.php?id=55")
    assert final_url.endswith("id=55")
    assert len(html_text) == len(large_body)
    assert "skTail99" in html_text


def test_empty_username_never_fast_paths_fresh_cookie_in_ensure_live_session_or_get_session_cookies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time as _time

    monkeypatch.setattr(settings, "UTH_USERNAME", "")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")

    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = ""
    client._session_owner = ""
    client._session_authenticated_at = _time.monotonic()
    client._cookie_jar.set_cookie(_make_cookie("MoodleSession", "stale_anon_cookie"))
    monkeypatch.setattr(client, "login_session", lambda **_kw: False)

    assert client.ensure_live_session() is False
    assert client.get_session_cookies(ensure_login=True, verify_live=True) == []


def test_shared_sta_skips_gw_hwndprev_query_for_occluded_dock_coexisting_with_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_global_webview_occlusion()
    courses_dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="https://courses.ut.edu.vn/course/view.php?id=1",
        occlusion_layer="courses_pane",
    )
    overlay_dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="https://courses.ut.edu.vn/mod/assign/view.php?id=2",
        occlusion_layer="app_overlay",
    )
    try:
        set_global_webview_occlusion("detail_view", True)
        assert courses_dock._effective_visible() is False
        assert overlay_dock._effective_visible() is True

        gw_calls: list[int] = []
        fake_w32 = SimpleNamespace(
            IsWindow=lambda _h: True,
            GetWindowRect=lambda _h: (100, 100, 900, 700),
            IsIconic=lambda _h: False,
            IsWindowVisible=lambda _h: True,
            GetWindow=lambda h, _cmd: gw_calls.append(h) or 222,
        )
        courses_dock._parent_hwnd = 999
        courses_dock._hwnd = 111
        overlay_dock._parent_hwnd = 999
        overlay_dock._hwnd = 222

        s_hidden = host_mod._compute_dock_parent_sync_state(courses_dock, fake_w32, 0)
        assert gw_calls == []
        assert s_hidden is not None and s_hidden[-1] is True

        s_visible = host_mod._compute_dock_parent_sync_state(overlay_dock, fake_w32, 0)
        assert gw_calls == [999]
        assert s_visible is not None and s_visible[-1] is True
    finally:
        clear_global_webview_occlusion()
        courses_dock.dispose()
        overlay_dock.dispose()


@pytest.mark.skipif(
    sys.platform != "win32" or not host_mod._webview2_runtime_available(),
    reason="Requires Windows WebView2 runtime",
)
def test_live_windows_webview2_dock_app_overlay_hwnd_visibility_and_warm_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import http.server
    import socketserver
    import subprocess
    import threading
    import time
    import win32gui

    profile_base = tmp_path / "localappdata"
    profile_base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOCALAPPDATA", str(profile_base))
    host_mod._CACHED_PARENT_HWNDS.clear()
    clear_global_webview_occlusion()

    class _LocalPageHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"<html><body><h1>Overlay Live Page</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", 0), _LocalPageHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    page_url = f"http://127.0.0.1:{port}/page"

    parent_title = f"UTHelperLiveSmoke_{int(time.perf_counter() * 1000)}"
    parent_script = (
        "import ctypes, time\n"
        "from ctypes import wintypes\n"
        "import win32api, win32con, win32gui\n"
        "hinst = win32api.GetModuleHandle(None)\n"
        "wc = win32gui.WNDCLASS()\n"
        "wc.lpfnWndProc = win32gui.DefWindowProc\n"
        "wc.hInstance = hinst\n"
        "wc.lpszClassName = 'FLUTTER_RUNNER_WIN32_WINDOW'\n"
        "win32gui.RegisterClass(wc)\n"
        f"hwnd = win32gui.CreateWindowEx(win32con.WS_EX_TOOLWINDOW, 'FLUTTER_RUNNER_WIN32_WINDOW', {parent_title!r}, "
        "win32con.WS_POPUP | win32con.WS_VISIBLE, 100, 100, 640, 480, 0, 0, hinst, None)\n"
        "print('READY', hwnd, flush=True)\n"
        "msg = wintypes.MSG()\n"
        "while True:\n"
        "    while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):\n"
        "        ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))\n"
        "        ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))\n"
        "    time.sleep(0.005)\n"
    )
    parent_proc = subprocess.Popen(
        [sys.executable, "-c", parent_script],
        stdout=subprocess.PIPE,
        text=True,
    )

    def _wait_until(pred: Any, timeout_s: float = 5.0) -> bool:
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            if pred():
                return True
            time.sleep(0.01)
        return bool(pred())

    dock1: WindowsWebView2Dock | None = None
    dock2: WindowsWebView2Dock | None = None
    try:
        assert parent_proc.stdout is not None
        ready_line = parent_proc.stdout.readline().strip()
        assert ready_line.startswith("READY ")

        # DetailView occlusion is ALREADY active before opening app_overlay WebView!
        set_global_webview_occlusion("detail_view", True)

        ready1 = threading.Event()
        t0 = time.perf_counter()
        dock1 = WindowsWebView2Dock(
            page_title=parent_title,
            url=page_url,
            account_namespace="https://courses.ut.edu.vn|smoke_user",
            occlusion_layer="app_overlay",
            on_ready=ready1.set,
        )
        dock1.start()
        assert ready1.wait(timeout=12.0), f"Cold WebView2 dock failed: {dock1._failed}"
        assert _wait_until(lambda: dock1 is not None and dock1._paint_ready, timeout_s=3.0)
        cold_ms = (time.perf_counter() - t0) * 1000.0

        # HWND MUST be visible despite detail_view occlusion!
        assert _wait_until(
            lambda: bool(dock1 and dock1._hwnd and win32gui.IsWindowVisible(dock1._hwnd)),
            timeout_s=2.0,
        )

        # Opening a modal_dialog on top of detail_view MUST hide the app_overlay HWND!
        set_global_webview_occlusion("modal_dialog", True)
        assert _wait_until(
            lambda: bool(dock1 and dock1._hwnd and not win32gui.IsWindowVisible(dock1._hwnd)),
            timeout_s=2.0,
        )

        # Closing modal_dialog while detail_view remains active MUST restore app_overlay HWND!
        set_global_webview_occlusion("modal_dialog", False)
        assert _wait_until(
            lambda: bool(dock1 and dock1._hwnd and win32gui.IsWindowVisible(dock1._hwnd)),
            timeout_s=2.0,
        )

        dock1.dispose()
        dock1 = None

        # Second open reuses warm _SHARED_WV2_STA CoreWebView2Environment!
        ready2 = threading.Event()
        t_warm = time.perf_counter()
        dock2 = WindowsWebView2Dock(
            page_title=parent_title,
            url=page_url,
            account_namespace="https://courses.ut.edu.vn|smoke_user",
            occlusion_layer="app_overlay",
            on_ready=ready2.set,
        )
        dock2.start()
        assert ready2.wait(timeout=8.0)
        assert _wait_until(lambda: dock2 is not None and dock2._paint_ready, timeout_s=2.0)
        warm_ms = (time.perf_counter() - t_warm) * 1000.0
        assert warm_ms < cold_ms or warm_ms < 500.0

        dock2.dispose()
        dock2 = None
        time.sleep(0.05)
        clear_persisted_webview2_profiles(
            profile_root=profile_base / "UTHelper" / "webview2_profile"
        )
    finally:
        clear_global_webview_occlusion()
        if dock1 is not None:
            dock1.dispose()
        if dock2 is not None:
            dock2.dispose()
        clear_persisted_webview2_profiles(
            profile_root=profile_base / "UTHelper" / "webview2_profile"
        )
        httpd.shutdown()
        parent_proc.terminate()
        parent_proc.wait(timeout=3.0)

