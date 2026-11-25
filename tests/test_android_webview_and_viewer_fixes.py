"""Regression tests for Android InAppWebView parity and DOCX/PDF/PPTX viewer fixes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest

from core.courses.local_preview_server import LocalPreviewServer
from gui.components.courses.document_reader_view import DocumentReaderView
from gui.components.courses.embedded_webview_host import build_embedded_webview_host
from gui.components.courses.webview_pane import CoursesWebViewPane
from tests.e2e_courses.builders import build_docx_bytes, build_pdf_bytes


def _make_live_mobile_page(platform: ft.PagePlatform = ft.PagePlatform.ANDROID) -> ft.Page:
    page = MagicMock(spec=ft.Page)
    page.web = False
    page.platform = platform
    page.width = 412
    page.height = 915

    def _run_task(coro_fn, *args):
        asyncio.run(coro_fn(*args))

    page.run_task = _run_task
    return page


@pytest.mark.parametrize("platform", [ft.PagePlatform.ANDROID, ft.PagePlatform.IOS])
def test_mobile_live_page_uses_inappwebview_backend_and_bootstraps_cookies(
    platform: ft.PagePlatform,
    monkeypatch: pytest.MonkeyPatch,
):
    page = _make_live_mobile_page(platform)
    events: list[tuple[str, object]] = []
    cookies_provided = [
        {
            "name": "MoodleSession",
            "value": "secret_sess_token",
            "domain": "courses.ut.edu.vn",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "expires": None,
        }
    ]

    host = build_embedded_webview_host(
        "https://courses.ut.edu.vn/mod/url/view.php?id=88",
        page=page,
        cookie_provider=lambda: cookies_provided,
        on_ready=lambda: events.append(("ready", True)),
        on_error=lambda err: events.append(("error", err)),
        on_message=lambda msg: events.append(("msg", msg)),
        on_url_changed=lambda u: events.append(("url", u)),
        on_title_changed=lambda t: events.append(("title", t)),
        on_loading_changed=lambda b: events.append(("loading", b)),
        on_history_changed=lambda b, f: events.append(("hist", (b, f))),
    )
    assert host is not None
    assert host.backend == "inappwebview"

    slot = host.control()
    wv = slot.content
    assert wv._c == "UthAppWebView"
    assert wv.require_initial_cookies is True

    wv.bootstrap_cookies = AsyncMock(return_value=True)
    wv.run_javascript = AsyncMock()
    wv.load_request = AsyncMock(return_value=True)
    wv.go_back = AsyncMock(return_value=True)
    wv.go_forward = AsyncMock(return_value=True)
    wv.reload = AsyncMock(return_value=True)

    slot.did_mount()
    wv.bootstrap_cookies.assert_awaited_once()
    passed_cookies = wv.bootstrap_cookies.await_args.args[0]
    assert passed_cookies[0]["name"] == "MoodleSession"

    # Console bridge forwards __UTH_MSG__: payloads from PDF/DOCX/PPTX viewers
    wv.on_console_message(SimpleNamespace(data='__UTH_MSG__:{"type":"ready"}'))
    assert ("msg", '{"type":"ready"}') in events

    # History, title, URL, loading events work without extra RPC roundtrips
    wv.on_page_started(SimpleNamespace(data="https://uteduvn-my.sharepoint.com/:x:/g/personal/a"))
    wv.on_title_change(SimpleNamespace(data="Chuyên đề Hệ thống giao thông"))
    wv.on_history_change(SimpleNamespace(data='{"can_go_back":true,"can_go_forward":false}'))
    wv.on_page_ended(SimpleNamespace(data="https://uteduvn-my.sharepoint.com/:x:/g/personal/a"))

    assert host.can_go_back() is True
    assert host.can_go_forward() is False
    assert ("title", "Chuyên đề Hệ thống giao thông") in events
    assert ("ready", True) in events

    asyncio.run(host.eval_js("window.setTheme('dark')"))
    wv.run_javascript.assert_awaited_once_with("window.setTheme('dark')")
    host.dispose()


def test_courses_webview_pane_on_android_inappwebview_skips_session_proxy_for_direct_redirects():
    """Android CoursesWebViewPane loads Moodle/SharePoint HTTPS URLs directly (like PC) instead of /session/ proxy."""
    page = _make_live_mobile_page(ft.PagePlatform.ANDROID)
    server = LocalPreviewServer()
    fake_client = SimpleNamespace(
        moodle_site_origin="https://courses.ut.edu.vn",
        get_session_cookies=lambda **_kw: [
            {
                "name": "MoodleSession",
                "value": "tok1",
                "domain": "courses.ut.edu.vn",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "expires": None,
            }
        ],
    )
    server.register_session_provider("courses", fake_client)
    try:
        moodle_url = "https://courses.ut.edu.vn/mod/url/view.php?id=99"
        pane = CoursesWebViewPane(
            page=page,
            source="courses",
            title="Tài liệu SharePoint",
            url=moodle_url,
            preview_server=server,
            session_client=fake_client,
            on_back=lambda: None,
            on_open_external=lambda _u: None,
        )
        try:
            assert pane._host is not None
            assert pane._host.backend == "inappwebview"
            wv = pane._host.control().content
            # Must hit Moodle HTTPS URL directly so 302 -> SharePoint works natively!
            assert wv.url == moodle_url
            assert "/session/" not in wv.url
            assert wv.require_initial_cookies is True
        finally:
            pane.dispose()
    finally:
        server.stop()


def test_flet_webview_fallback_enables_unrestricted_js_and_ignores_subresource_aborts():
    import flet_webview as fwv

    fake_page = SimpleNamespace(
        web=False,
        platform="android",
        run_task=lambda fn, *a: asyncio.run(fn(*a)),
    )
    errors: list[str] = []
    host = build_embedded_webview_host(
        "https://login.microsoftonline.com/",
        page=fake_page,  # type: ignore[arg-type]
        on_error=errors.append,
    )
    assert host is not None
    assert host.backend == "flet_webview"
    slot = host.control()
    wv = slot.content
    wv.set_javascript_mode = AsyncMock()
    wv.can_go_back = AsyncMock(return_value=False)
    wv.can_go_forward = AsyncMock(return_value=False)

    slot.did_mount()
    wv.set_javascript_mode.assert_awaited_once_with(fwv.JavaScriptMode.UNRESTRICTED)

    # Subframe / ERR_ABORTED resource errors must NOT trigger fatal viewer fallback
    wv.on_web_resource_error(SimpleNamespace(data="net::ERR_ABORTED", is_for_main_frame=False))
    wv.on_web_resource_error(SimpleNamespace(data="net::ERR_ABORTED"))
    assert errors == []

    wv.on_web_resource_error(SimpleNamespace(data="net::ERR_NAME_NOT_RESOLVED", is_for_main_frame=True))
    assert errors == ["net::ERR_NAME_NOT_RESOLVED"]
    host.dispose()


def test_document_reader_docx_watchdog_direct_platform_mount_and_popup_resize_stability():
    """DOCX arms watchdog, mobile PlatformView bypasses AnimatedSwitcher, and popup resize preserves WebView."""
    page = _make_live_mobile_page(ft.PagePlatform.ANDROID)
    server = LocalPreviewServer()
    try:
        # 1. DOCX arms viewer watchdog so a dead WebView falls back instead of hanging at 'Đang mở tài liệu...'
        docx_bytes = build_docx_bytes(paragraphs=["Chương 1: Hệ thống giao thông"])
        docx_reader = DocumentReaderView(
            file_name="34_123015.docx",
            data=docx_bytes,
            kind="docx",
            preview_server=server,
            on_close=lambda: None,
            on_save=lambda *_: None,
            on_open_external=lambda *_: None,
            webview_builder=lambda url, **kw: build_embedded_webview_host(
                url, page=page, **kw
            ),
        )
        try:
            assert docx_reader._viewer_watchdog is not None
            assert docx_reader._embedded_host is not None
            assert docx_reader._embedded_host.backend == "inappwebview"
            # Mobile PlatformView must mount directly in _body_host (bypassing AnimatedSwitcher)
            assert docx_reader._body_host.content is docx_reader._office_webview
            assert docx_reader._body_host.content is not docx_reader._body_switcher

            # Ready message cancels watchdog cleanly
            docx_reader._handle_viewer_message('{"type":"ready"}')
            assert docx_reader._viewer_watchdog is None
        finally:
            docx_reader.dispose()

        # 2. PDF on mobile mounts pdf_host.html directly and survives tablet Pop-up View resize
        pdf_bytes = build_pdf_bytes(["ĐẢNG LÃNH ĐẠO CẢ NƯỚC", "Trang 2 nội dung"])
        pdf_reader = DocumentReaderView(
            file_name="Chuong_3_1.pdf",
            data=pdf_bytes,
            kind="pdf",
            preview_server=server,
            on_close=lambda: None,
            on_save=lambda *_: None,
            on_open_external=lambda *_: None,
            webview_builder=lambda url, **kw: build_embedded_webview_host(
                url, page=page, **kw
            ),
        )
        # Force mobile pypdf-only condition (can_render_images=False) even on desktop test machine
        pdf_reader._pdf_session = SimpleNamespace(
            supported=True,
            page_count=2,
            can_render_images=False,
            close=lambda: None,
        )
        pdf_reader._set_body_content(pdf_reader._build_body())
        try:
            assert pdf_reader._active_webview_url is not None
            assert "pdf_host.html" in pdf_reader._active_webview_url
            initial_wv = pdf_reader._body_host.content
            assert initial_wv is pdf_reader._office_webview

            # Resizing into tablet Pop-up View (sync_bounds) preserves active WebView!
            pdf_reader.sync_bounds(0, 36, 420.0, 620.0)
            assert pdf_reader._active_webview_url is not None
            assert pdf_reader._body_host.content is initial_wv
            assert pdf_reader._webview_failed is False
        finally:
            pdf_reader.dispose()
    finally:
        server.stop()


def test_embedded_webview_host_preserves_pane_on_about_blank_and_origin_blob_events():
    """WV-04: about:blank and validated blob: events must not destroy CoursesWebViewPane."""
    page = _make_live_mobile_page(ft.PagePlatform.ANDROID)
    server = LocalPreviewServer()
    try:
        moodle_url = "https://courses.ut.edu.vn/mod/url/view.php?id=99"
        pane = CoursesWebViewPane(
            page=page,
            source="courses",
            title="SharePoint",
            url=moodle_url,
            preview_server=server,
            on_back=lambda: None,
            on_open_external=lambda _u: None,
        )
        try:
            assert pane._host is not None
            wv = pane._host.control().content

            wv.on_page_started(SimpleNamespace(data="about:blank"))
            assert pane._host is not None
            assert pane._url == moodle_url

            wv.on_url_change(
                SimpleNamespace(data="blob:https://courses.ut.edu.vn/550e8400-e29b")
            )
            assert pane._host is not None
            assert pane._url == moodle_url

            # Disallowed blob origin (private IP) must still trigger block!
            wv.on_url_change(SimpleNamespace(data="blob:https://127.0.0.1/evil"))
            assert pane._host is None
        finally:
            pane.dispose()
    finally:
        server.stop()


def test_mobile_inappwebview_locks_register_html_16hex_and_inline_html_previews():
    """INT-02: /<16-hex> register_html previews and inline html= must run viewer_locked=True."""
    page = _make_live_mobile_page(ft.PagePlatform.ANDROID)
    server = LocalPreviewServer()
    try:
        html_url = server.register_html("<html><body><a href='https://evil.example'>x</a></body></html>")
        host_item = build_embedded_webview_host(html_url, page=page)
        assert host_item is not None
        assert host_item.backend == "inappwebview"
        assert host_item.control().content.viewer_locked is True
        assert host_item.control().content.require_initial_cookies is False
        host_item.dispose()

        host_inline = build_embedded_webview_host(
            "",
            html="<html><body>Inline</body></html>",
            page=page,
        )
        assert host_inline is not None
        assert host_inline.backend == "inappwebview"
        assert host_inline.control().content.viewer_locked is True
        assert host_inline.control().content.require_initial_cookies is False
        host_inline.dispose()
    finally:
        server.stop()


def _visible_children(ctrl: ft.Control) -> list[ft.Control]:
    raw = getattr(ctrl, "controls", None)
    if isinstance(raw, list):
        return [c for c in raw if getattr(c, "visible", True) is not False]
    content = getattr(ctrl, "content", None)
    if content is not None and getattr(content, "visible", True) is not False:
        return [content]
    return []


def _visible_index_path(root: ft.Control, target: ft.Control) -> Optional[tuple[int, ...]]:
    if root is target:
        return ()
    for idx, child in enumerate(_visible_children(root)):
        sub = _visible_index_path(child, target)
        if sub is not None:
            return (idx, *sub)
    return None


def test_courses_webview_pane_loading_and_compact_toggles_keep_body_host_index_stable():
    """Toggling _nav_progress or _mobile_nav_bar must NEVER shift _body_host's visible child index."""
    page = _make_live_mobile_page(ft.PagePlatform.ANDROID)
    pane = CoursesWebViewPane(
        page=page,
        source="courses",
        title="SharePoint",
        url="https://uteduvn-my.sharepoint.com/:x:/g/personal/doc",
        on_back=lambda: None,
        on_open_external=lambda _u: None,
        pane_width=820.0,
    )
    try:
        baseline_path = _visible_index_path(pane, pane._body_host)
        assert baseline_path is not None

        pane._set_nav_loading(False)
        assert _visible_index_path(pane, pane._body_host) == baseline_path

        pane._set_nav_loading(True)
        assert _visible_index_path(pane, pane._body_host) == baseline_path

        pane._on_host_loading_changed(False)
        assert _visible_index_path(pane, pane._body_host) == baseline_path

        # Crossing 600dp (Samsung Pop-up View <-> Tablet landscape) keeps _body_host path identical
        pane.sync_bounds(0, 36, 420.0, 620.0)
        assert pane._mobile_nav_bar.visible is True
        assert _visible_index_path(pane, pane._body_host) == baseline_path

        pane.sync_bounds(89, 36, 1200.0, 800.0)
        assert pane._mobile_nav_bar.visible is False
        assert _visible_index_path(pane, pane._body_host) == baseline_path
    finally:
        pane.dispose()


def test_adaptive_nav_rail_slot_and_courses_tab_view_preserve_webview_across_rotations_and_popup_view():
    """Crossing 600dp/900dp breakpoints must never shift main_stack or reparent _workspace_stack."""
    from gui.components.courses.adaptive_nav import AdaptiveNavShell
    from gui.components.courses.courses_tab_view import CoursesTabView

    nav = AdaptiveNavShell(on_navigate=lambda _k: None)
    main_stack = ft.Stack(expand=True)
    body_row = ft.Row(controls=[nav.rail_slot, main_stack], expand=True)

    nav.apply_width(1400)
    assert nav.rail_host.visible is True
    assert nav.rail_slot.visible is True
    assert _visible_index_path(body_row, main_stack) == (1,)

    # Pop-up View (< 600dp): rail_host hides, but rail_slot stays visible at width 0 so main_stack stays at (1,)
    nav.apply_width(420)
    assert nav.rail_host.visible is False
    assert nav.rail_slot.visible is True
    assert nav.rail_slot.width == 0
    assert _visible_index_path(body_row, main_stack) == (1,)

    # CoursesTabView keeps _workspace_stack at the exact same ancestor path across wide, tablet_portrait, and mobile
    tab = CoursesTabView(page=None, catalog_service=None)
    overlay = ft.Container(expand=True)
    tab.push_overlay(overlay, tag="reader:test.pdf")

    tab.set_width(1311)  # wide
    wide_path = _visible_index_path(tab, tab._workspace_stack)
    assert wide_path is not None

    tab.set_width(787)  # tablet_portrait (Galaxy Tab S10+ portrait)
    assert tab._layout_mode == "tablet_portrait"
    assert _visible_index_path(tab, tab._workspace_stack) == wide_path

    tab.set_width(420)  # mobile / Samsung Pop-up View
    assert tab._layout_mode == "mobile"
    assert _visible_index_path(tab, tab._workspace_stack) == wide_path

    tab.set_width(1311)  # back to wide + expand toggle
    tab.set_pane3_expanded(True)
    assert _visible_index_path(tab, tab._workspace_stack) == wide_path


def test_inappwebview_suspend_preserves_visibility_syncs_redirect_url_and_allows_about_srcdoc():
    """inappwebview suspend() must NOT set slot.visible=False, and redirect URLs sync cleanly."""
    page = _make_live_mobile_page(ft.PagePlatform.ANDROID)
    pane = CoursesWebViewPane(
        page=page,
        source="courses",
        title="SharePoint Excel",
        url="https://courses.ut.edu.vn/mod/url/view.php?id=99",
        session_client=SimpleNamespace(
            get_session_cookies=lambda **_kw: [
                {"name": "MoodleSession", "value": "tok", "domain": "courses.ut.edu.vn"}
            ]
        ),
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        assert pane._host is not None
        assert pane._host.backend == "inappwebview"
        slot = pane._host.control()
        wv = slot.content
        assert wv.require_initial_cookies is True

        # Suspend must NOT unmount PlatformView via slot.visible = False
        pane.suspend()
        assert slot.visible is True
        assert slot.ignore_interactions is True
        pane.resume()
        assert slot.visible is True
        assert slot.ignore_interactions is False

        # SharePoint / Office Online about:srcdoc event must not destroy pane
        wv.on_page_started(SimpleNamespace(data="about:srcdoc"))
        wv.on_url_change(SimpleNamespace(data="about:srcdoc#wac"))
        assert pane._host is not None
        assert pane._url == "https://courses.ut.edu.vn/mod/url/view.php?id=99"

        # Redirect to SharePoint updates wv.url and clears require_initial_cookies for future remounts
        sp_url = "https://uteduvn-my.sharepoint.com/:x:/g/personal/u/doc?e=rs8b4a"
        wv.on_url_change(SimpleNamespace(data=sp_url))
        assert pane._url == sp_url
        assert wv.url == sp_url
        assert wv.require_initial_cookies is False
    finally:
        pane.dispose()


def test_pane_loading_overlay_marshals_off_thread_set_progress_onto_page_loop():
    """Worker-thread set_progress must marshal control mutations onto page.loop to prevent applyPatch RangeError."""
    import threading
    from gui.components.courses._shared import PaneLoadingOverlay

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    gate = threading.Event()
    stop = threading.Event()

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_until_complete(_wait_stop())

    async def _wait_stop() -> None:
        while not stop.is_set():
            await asyncio.sleep(0.005)

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    ready.wait(timeout=2.0)
    try:
        blocked_in_gate = threading.Event()

        def _hold_loop() -> None:
            blocked_in_gate.set()
            gate.wait(timeout=2.0)

        # Hold the event loop momentarily so off-thread mutation would be caught immediately
        loop.call_soon_threadsafe(_hold_loop)
        assert blocked_in_gate.wait(timeout=2.0)
        fake_page = SimpleNamespace(loop=loop, update=lambda: None)
        overlay = PaneLoadingOverlay(
            title="Chuong_3_1.pdf",
            on_back=lambda: None,
            page=fake_page,
        )
        overlay.update = lambda: None  # type: ignore[method-assign]

        # Call set_progress from THIS thread (off `loop`): must NOT mutate until `loop` runs
        overlay.set_progress(0.65, message="Đang tải 65%...", subtitle="650 KB / 1 MB")
        assert overlay._progress_bar.value is None
        gate.set()
        fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(0.02), loop)
        fut.result(timeout=2.0)
        assert overlay._progress_bar.value == pytest.approx(0.65)
        assert overlay._status_label.value == "Đang tải 65%..."
        assert overlay._subtitle_text.value == "650 KB / 1 MB"
    finally:
        gate.set()
        stop.set()
        t.join(timeout=2.0)
        loop.close()


def test_remount_after_page_started_redirect_syncs_url_skips_abort_wait_and_dedups_cookie_sync(
    monkeypatch: pytest.MonkeyPatch,
):
    """Tab-switch remounts must sync redirect URLs on page_started, skip 3s abort wait when cookies are not needed, and deduplicate concurrent cookie bootstrap."""
    import time

    page = _make_live_mobile_page(ft.PagePlatform.ANDROID)
    provider_calls = 0

    def _slow_provider():
        nonlocal provider_calls
        provider_calls += 1
        time.sleep(0.05)
        return [{"name": "MoodleSession", "value": "tok1", "domain": "courses.ut.edu.vn"}]

    pane = CoursesWebViewPane(
        page=page,
        source="courses",
        title="SharePoint Redirect",
        url="https://courses.ut.edu.vn/mod/url/view.php?id=99",
        session_client=SimpleNamespace(get_session_cookies=lambda **_kw: _slow_provider()),
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        assert pane._host is not None
        host = pane._host
        slot = host.control()
        wv = slot.content
        bootstrap_calls: list[tuple[int, str]] = []

        async def _fake_bootstrap(cookies, *, target_url=None):
            bootstrap_calls.append((len(cookies), str(target_url or "")))
            wv._initial_bootstrap_done = True
            if wv._cookie_channel_id is not None:
                wv._consumed_channel_ids.add(wv._cookie_channel_id)
            return True

        abort_calls = 0

        async def _fake_abort():
            nonlocal abort_calls
            abort_calls += 1
            return True

        monkeypatch.setattr(wv, "bootstrap_cookies", _fake_bootstrap)
        monkeypatch.setattr(wv, "abort_cookie_bootstrap", _fake_abort)
        monkeypatch.setattr(
            wv, "get_data_channel", lambda cid: SimpleNamespace(id=cid, send=lambda _b: None)
        )

        # Simulate initial mount + remount where did_mount and data_channel_open race while _slow_provider runs
        wv._initial_bootstrap_done = True
        wv.will_unmount()

        async def _run_concurrent_remount() -> None:
            scheduled: list[Any] = []
            monkeypatch.setattr(page, "run_task", lambda fn, *a, **k: scheduled.append(asyncio.create_task(fn(*a, **k))))
            slot.did_mount()
            await asyncio.sleep(0.01)
            # While _slow_provider is still in flight on thread pool, Dart fires data_channel_open!
            wv.on_data_channel_open(
                SimpleNamespace(channel_name="app_webview_cookies", channel_id=22)
            )
            if scheduled:
                await asyncio.gather(*scheduled)

        asyncio.run(_run_concurrent_remount())
        assert provider_calls == 1
        assert len(bootstrap_calls) == 1

        # Mid-redirect: page_started fires for SharePoint BEFORE onUpdateVisitedHistory (url_change)
        sp_url = "https://uteduvn-my.sharepoint.com/:x:/g/personal/u/doc?e=rs8b4a"
        wv.on_page_started(SimpleNamespace(data=sp_url))
        assert host._url == sp_url
        assert wv.url == sp_url
        assert wv.require_initial_cookies is False

        # Remounting now that require_initial_cookies is False must NOT schedule abort_cookie_bootstrap
        scheduled_after_redirect: list[Any] = []
        monkeypatch.setattr(page, "run_task", lambda fn, *a, **k: scheduled_after_redirect.append(fn))
        slot.did_mount()
        assert scheduled_after_redirect == []
        assert abort_calls == 0
    finally:
        pane.dispose()


def test_document_reader_dark_mode_syncs_viewer_url_and_preserves_html_webview_on_theme_refresh():
    """Toggling dark mode on PDF/DOCX WebView must sync theme= in wv.url, and update_theme must preserve .html WebViews."""
    from core.courses.office_viewer_service import prepare_pdf_viewer

    page = _make_live_mobile_page(ft.PagePlatform.ANDROID)
    server = LocalPreviewServer()
    try:
        pdf_bytes = build_pdf_bytes(["Trang 1"])
        pdf_desc = prepare_pdf_viewer(pdf_bytes, server, theme="light")
        assert pdf_desc is not None

        reader = DocumentReaderView(
            file_name="Lecture.pdf",
            data=pdf_bytes,
            kind="pdf",
            preview_server=server,
            on_close=lambda: None,
            on_save=lambda _n, _b: None,
            on_open_external=lambda _n, _b: None,
            webview_builder=lambda url, **kw: build_embedded_webview_host(url, page=page, **kw),
            preprepared_office_viewer=pdf_desc,
        )
        try:
            assert reader._embedded_host is not None
            wv = reader._embedded_host.control().content
            assert "theme=light" in wv.url
            reader._toggle_reader_mode()
            assert reader._dark_reader is True
            assert "theme=dark" in wv.url
            assert "theme=dark" in (reader._active_webview_url or "")
            reader._toggle_reader_mode()
            assert reader._dark_reader is False
            assert "theme=light" in wv.url
        finally:
            reader.dispose()

        html_bytes = b"<!doctype html><html><body><h1>Slide HTML</h1></body></html>"
        html_reader = DocumentReaderView(
            file_name="summary.html",
            data=html_bytes,
            kind="text",
            preview_server=server,
            on_close=lambda: None,
            on_save=lambda _n, _b: None,
            on_open_external=lambda _n, _b: None,
            webview_builder=lambda url, **kw: build_embedded_webview_host(url, page=page, **kw),
        )
        try:
            first_host = html_reader._embedded_host
            assert first_host is not None
            html_reader.update_theme()
            assert html_reader._embedded_host is first_host
        finally:
            html_reader.dispose()
    finally:
        server.stop()


def test_in_app_webview_dialog_uses_stack_progress_and_uth_app_webview_on_live_mobile(
    monkeypatch: pytest.MonkeyPatch,
):
    """InAppWebViewDialog must use UthAppWebView on live Android pages and keep fallback WebView index stable when status_bar toggles."""
    from flet_uth_quiz import UthAppWebView
    from gui.components import in_app_webview as webview_module
    from gui.components.in_app_webview import InAppWebViewDialog

    live_page = _make_live_mobile_page(ft.PagePlatform.ANDROID)
    dialogs: list[Any] = []
    monkeypatch.setattr(live_page, "show_dialog", dialogs.append, raising=False)

    dlg = InAppWebViewDialog(
        live_page,
        url="https://uteduvn-my.sharepoint.com/:x:/g/personal/doc",
        title="SharePoint",
    )
    assert dlg.show() is True
    assert len(dialogs) == 1

    def _walk_controls(ctrl):
        if ctrl is None:
            return
        yield ctrl
        c = getattr(ctrl, "content", None)
        if c is not None:
            yield from _walk_controls(c)
        for ch in getattr(ctrl, "controls", None) or ():
            yield from _walk_controls(ch)

    assert any(isinstance(n, UthAppWebView) for n in _walk_controls(dialogs[0]))

    # Fallback flet_webview path (non-ft.Page dummy) must keep _webview index path stable across started/ended!
    captured: list[dict[str, Any]] = []

    class _FakeWebView(ft.Container):
        def __init__(self, url, **kwargs):
            captured.append(kwargs)
            super().__init__(expand=True)

    monkeypatch.setattr(
        webview_module, "flet_webview", SimpleNamespace(WebView=_FakeWebView)
    )
    dummy_page = SimpleNamespace(
        platform=ft.PagePlatform.ANDROID,
        web=False,
        width=400,
        height=800,
        show_dialog=dialogs.append,
        pop_dialog=lambda: None,
        run_task=lambda *_a, **_k: None,
        update=lambda: None,
    )
    dialogs.clear()
    dlg_fb = InAppWebViewDialog(
        dummy_page,  # type: ignore[arg-type]
        url="https://portal.ut.edu.vn/schedule",
        title="Schedule",
    )
    assert dlg_fb.show() is True
    wv_node = dlg_fb._webview
    baseline = _visible_index_path(dialogs[0].content, wv_node)
    assert baseline is not None
    captured[0]["on_page_started"](SimpleNamespace(data="https://portal.ut.edu.vn/schedule"))
    assert _visible_index_path(dialogs[0].content, wv_node) == baseline
    captured[0]["on_page_ended"](SimpleNamespace(data="https://portal.ut.edu.vn/schedule"))
    assert _visible_index_path(dialogs[0].content, wv_node) == baseline


def test_courses_tab_view_roundtrip_mobile_tablet_wide_emits_valid_flet_patches() -> None:
    """Round-tripping wide -> mobile (<600dp Pop-up View) -> tablet_portrait -> wide
    must never emit out-of-bounds Flet list patches (Control.applyPatch RangeError)
    and must restore _workspace_layer.left == 321.0."""
    import msgpack
    from flet.messaging.connection import Connection
    from flet.messaging.protocol import (
        ClientAction,
        configure_encode_object_for_msgpack,
        decode_ext_from_msgpack,
    )
    from flet.messaging.session import Session, _context_page
    from flet.pubsub.pubsub_hub import PubSubHub

    from core.courses.models import (
        CourseModuleItem,
        CourseSection,
        CourseSummary,
        RecentMaterialEntry,
    )
    from gui.components.courses.courses_tab_view import CoursesTabView

    enc = configure_encode_object_for_msgpack(ft.BaseControl)

    def _roundtrip(obj: Any) -> Any:
        return msgpack.unpackb(
            msgpack.packb(obj, default=enc),
            ext_hook=decode_ext_from_msgpack,
            strict_map_key=False,
        )

    class _DartPatchMirror:
        def __init__(self, root_map: Any) -> None:
            self.controls: dict[int, dict[str, Any]] = {}
            self._index_tree(_roundtrip(root_map))

        def _index_tree(self, val: Any) -> Any:
            if isinstance(val, dict) and "_i" in val and "_c" in val:
                self.controls[val["_i"]] = val
                for k, v in list(val.items()):
                    val[k] = self._index_tree(v)
                return val
            if isinstance(val, list):
                return [self._index_tree(x) for x in val]
            if isinstance(val, dict):
                return {k: self._index_tree(v) for k, v in val.items()}
            return val

        def apply_patch(self, ctrl_id: int, raw_patch: Any) -> None:
            patch = _roundtrip(raw_patch)
            target_ctrl = self.controls[ctrl_id]
            path_index: dict[int, list[Any]] = {}

            def _build_paths(node: list[Any], path: list[Any]) -> None:
                path_index[node[0]] = path
                if len(node) > 1 and isinstance(node[1], dict):
                    for k, v in node[1].items():
                        _build_paths(v, [*path, k])

            _build_paths(patch[0], [])

            def _get_target(idx: int) -> tuple[Any, dict[str, Any]]:
                obj: Any = target_ctrl
                ctrl = target_ctrl
                for p in path_index[idx]:
                    obj = obj[p]
                    if isinstance(obj, dict) and "_i" in obj and "_c" in obj:
                        ctrl = obj
                return obj, ctrl

            for op in patch[1:]:
                op_type = op[0]
                if op_type == 3:
                    f_obj, _ = _get_target(op[1])
                    t_obj, _ = _get_target(op[3])
                    if isinstance(f_obj, list) and isinstance(t_obj, list):
                        t_obj.insert(op[4], f_obj.pop(op[2]))
                    elif isinstance(f_obj, dict) and isinstance(t_obj, dict):
                        t_obj[op[4]] = f_obj.pop(op[2])
                    continue
                obj, _ = _get_target(op[1])
                if op_type == 0:
                    key, val = op[2], self._index_tree(op[3])
                    if isinstance(obj, list) and (key < 0 or key >= len(obj)):
                        raise IndexError(
                            f"Control.applyPatch RangeError: list len={len(obj)}, replace key={key}"
                        )
                    obj[key] = val
                elif op_type == 1:
                    idx, val = op[2], self._index_tree(op[3])
                    if isinstance(obj, list):
                        obj.insert(idx, val)
                    else:
                        obj[idx] = val
                elif op_type == 2:
                    idx = op[2]
                    if isinstance(obj, list):
                        if idx < 0 or idx >= len(obj):
                            raise IndexError(
                                f"Control.applyPatch RangeError: list len={len(obj)}, remove idx={idx}"
                            )
                        obj.pop(idx)
                    else:
                        obj.pop(idx, None)

    class _FakeConn(Connection):
        def __init__(self) -> None:
            super().__init__()
            self.pubsubhub = PubSubHub()
            self.messages: list[Any] = []

        def send_message(self, message: Any) -> None:
            self.messages.append(message)

    conn = _FakeConn()
    sess = Session(conn)
    sess.attach_connection(conn)
    token = _context_page.set(sess.page)
    try:
        page = sess.page
        page.platform = ft.PagePlatform.ANDROID
        sess.apply_page_patch({"width": 1400, "height": 876})

        view = CoursesTabView(page=page)
        view.set_viewport_size(1312, 876)
        page.controls.append(view)
        mirror = _DartPatchMirror(sess.get_page_patch())
        conn.messages.clear()

        course = CourseSummary(
            id=9901,
            source="courses",
            fullname="AI",
            shortname="AI",
            display_name="AI",
            is_current_term=True,
        )
        section = CourseSection(
            id=1,
            course_id=9901,
            source="courses",
            section_num=0,
            name="S1",
            modules=(
                CourseModuleItem(
                    cmid=101,
                    instance_id=101,
                    modname="url",
                    name="M1",
                    section_id=1,
                    url="https://uteduvn-my.sharepoint.com/:x:/g/personal/doc",
                    open_strategy="external_url",
                ),
            ),
        )
        view._state.courses = [course]
        view._state.selected_course_key = course.course_key
        view._state.in_detail_mobile = True
        view._state.sections = [section]
        view._state.recent_items = [
            RecentMaterialEntry(
                course_key=course.course_key,
                course_name="AI",
                cmid=101,
                title="M1",
                attachment_url="https://uteduvn-my.sharepoint.com/:x:/g/personal/doc",
                file_name="doc.xlsx",
                opened_at=1,
            )
        ]
        view._render()
        view.push_overlay(ft.Container(expand=True), tag="wv")
        page.update()
        for msg in conn.messages:
            if msg.action == ClientAction.PATCH_CONTROL:
                mirror.apply_patch(msg.body.id, msg.body.patch)
        conn.messages.clear()

        for pw, cw, ph, expected_left in (
            (550, 550, 420, 0.0),
            (800, 712, 420, 0.0),
            (1400, 1312, 876, 321.0),
            (420, 420, 800, 0.0),
            (1400, 1312, 876, 321.0),
        ):
            sess.apply_page_patch({"width": pw, "height": ph})
            view.set_viewport_size(cw, ph)
            page.update()
            for msg in conn.messages:
                if msg.action == ClientAction.PATCH_CONTROL:
                    mirror.apply_patch(msg.body.id, msg.body.patch)
            conn.messages.clear()
            assert mirror.controls[view._workspace_layer._i].get("left") == expected_left
    finally:
        _context_page.reset(token)


def test_courses_webview_pane_initial_disabled_history_inappwebview_error_fallback_and_external_app_cards():
    """CoursesWebViewPane must disable < / > initially, fall back cleanly on pre-commit inappwebview errors,
    show external-app status/missing cards for zalo:///intent:// redirects, and cancel its commit watchdog."""
    page = _make_live_mobile_page(ft.PagePlatform.ANDROID)
    launched_external: list[str] = []
    closed: list[bool] = []

    # 1. Pre-commit main-frame error on inappwebview must fall back to card with external browser button
    pane_err = CoursesWebViewPane(
        page=page,
        source="courses",
        title="Bảng tính Google",
        url="https://docs.google.com/spreadsheets/d/1y5syAENL/edit?gid=1012692952#gid=1012692952",
        on_back=lambda: closed.append(True),
        on_open_external=launched_external.append,
    )
    try:
        assert pane_err._wv_back_btn.disabled is True
        assert pane_err._wv_fwd_btn.disabled is True
        assert pane_err._commit_watchdog is not None
        assert pane_err._host is not None
        assert pane_err._host.backend == "inappwebview"

        wv = pane_err._host.control().content
        wv.on_web_error(SimpleNamespace(data="net::ERR_CONNECTION_TIMED_OUT", is_for_main_frame=True))
        assert pane_err._host is None
        assert pane_err._commit_watchdog is None
        # Wait briefly for fallback worker thread and verify Android never shows Windows "WebView2" wording
        import time

        for _ in range(30):
            texts_err = [
                getattr(n, "value", "")
                for n in _iter_tree(pane_err._body_host)
                if isinstance(n, ft.Text)
            ]
            if any("Không thể tải trang web" in t for t in texts_err):
                break
            time.sleep(0.02)
        assert any("Không thể tải trang web" in t for t in texts_err)
        assert not any("WebView2" in t for t in texts_err)

        # Tapping Reload re-mounts a fresh InAppWebView host so transient errors recover!
        pane_err._on_webview_reload(None)
        assert pane_err._host is not None
        assert pane_err._host.backend == "inappwebview"
        assert pane_err._commit_watchdog is not None
    finally:
        pane_err.dispose()

    # 2. Post-commit error must NOT destroy an already-rendered inappwebview page
    pane_ok = CoursesWebViewPane(
        page=page,
        source="courses",
        title="Zoom Lớp Học",
        url="https://zoom.us/j/123456789",
        on_back=lambda: None,
        on_open_external=launched_external.append,
    )
    try:
        assert pane_ok._commit_watchdog is not None
        wv_ok = pane_ok._host.control().content
        wv_ok.on_page_ended(SimpleNamespace(data="https://zoom.us/j/123456789"))
        assert pane_ok._initial_page_committed is True
        assert pane_ok._commit_watchdog is None
        wv_ok.on_web_error(SimpleNamespace(data="net::ERR_TIMED_OUT", is_for_main_frame=True))
        assert pane_ok._host is not None
    finally:
        pane_ok.dispose()

    # 3. External native app launched (__UTH_EXTERNAL_APP_LAUNCHED__:intent) before commit shows status card
    pane_meet = CoursesWebViewPane(
        page=page,
        source="courses",
        title="Phòng học Google Meet",
        url="https://meet.google.com/abc-defg-hij",
        on_back=lambda: closed.append(True),
        on_open_external=launched_external.append,
    )
    try:
        wv_meet = pane_meet._host.control().content
        wv_meet.on_web_error(SimpleNamespace(data="__UTH_EXTERNAL_APP_LAUNCHED__:intent"))
        assert pane_meet._host is None
        texts_meet = [
            getattr(n, "value", "")
            for n in _iter_tree(pane_meet._body_host)
            if isinstance(n, ft.Text)
        ]
        assert any("ứng dụng ngoài" in t for t in texts_meet)
    finally:
        pane_meet.dispose()

    # 4. External native app missing (__UTH_EXTERNAL_APP_MISSING__:zalo) shows Zalo fallback card + external button
    pane_zalo = CoursesWebViewPane(
        page=page,
        source="courses",
        title="Nhóm Zalo Lớp",
        url="https://zalo.me/g/nhom_lop_a",
        on_back=lambda: closed.append(True),
        on_open_external=launched_external.append,
    )
    try:
        wv_zalo = pane_zalo._host.control().content
        wv_zalo.on_web_error(SimpleNamespace(data="__UTH_EXTERNAL_APP_MISSING__:zalo"))
        assert pane_zalo._host is None
        texts_zalo = [
            getattr(n, "value", "")
            for n in _iter_tree(pane_zalo._body_host)
            if isinstance(n, ft.Text)
        ]
        assert any("Zalo" in t for t in texts_zalo)
        ext_btns = [
            n
            for n in _iter_tree(pane_zalo._body_host)
            if isinstance(n, ft.FilledButton)
        ]
        assert len(ext_btns) == 1
        ext_btns[0].on_click(None)
        assert launched_external == ["https://zalo.me/g/nhom_lop_a"]

        # Reload on Zalo card re-mounts InAppWebView to retry native Zalo launch instead of showing WebView2 card
        pane_zalo._on_webview_reload(None)
        assert pane_zalo._host is not None
        assert pane_zalo._host.backend == "inappwebview"
    finally:
        pane_zalo.dispose()

    # 5. Blocked disallowed URL stays blocked even if user clicks Reload
    pane_blk = CoursesWebViewPane(
        page=page,
        source="courses",
        title="Blocked",
        url="https://courses.ut.edu.vn/mod/url/view.php?id=10",
        on_back=lambda: None,
        on_open_external=launched_external.append,
    )
    try:
        pane_blk._on_host_error("Blocked disallowed WebView URL: http://evil.example/")
        assert pane_blk._host is None
        pane_blk._on_webview_reload(None)
        assert pane_blk._host is None
        texts_blk = [
            getattr(n, "value", "")
            for n in _iter_tree(pane_blk._body_host)
            if isinstance(n, ft.Text)
        ]
        assert any("chặn" in t.lower() for t in texts_blk)
    finally:
        pane_blk.dispose()


def _iter_tree(ctrl: Optional[ft.Control]):
    if ctrl is None:
        return
    yield ctrl
    content = getattr(ctrl, "content", None)
    if isinstance(content, ft.Control):
        yield from _iter_tree(content)
    for child in getattr(ctrl, "controls", None) or ():
        if isinstance(child, ft.Control):
            yield from _iter_tree(child)


def test_in_app_webview_dialog_parity_toolbar_url_sync_and_error_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    """InAppWebViewDialog must show nav buttons even when flet_webview is None on mobile,
    sync self._url on navigation, and fall back cleanly on pre-commit WebView errors."""
    from gui.components import in_app_webview as webview_module
    from gui.components.in_app_webview import InAppWebViewDialog

    monkeypatch.setattr(webview_module, "flet_webview", None)
    live_page = _make_live_mobile_page(ft.PagePlatform.ANDROID)
    dialogs: list[Any] = []
    launched: list[str] = []
    monkeypatch.setattr(live_page, "show_dialog", dialogs.append, raising=False)

    dlg = InAppWebViewDialog(
        live_page,
        url="https://courses.ut.edu.vn/mod/url/view.php?id=55",
        title="Tài liệu ngoài",
        on_open_external=launched.append,
    )
    assert dlg.show() is True
    assert dlg._embedded_host is not None

    # 1. Back / Forward / Reload buttons must still be present in header even when flet_webview is None,
    #    with < and > initially disabled and updated via history_change
    header_btns = {
        getattr(n, "tooltip", None): n
        for n in _iter_tree(dialogs[0].title)
        if isinstance(n, ft.IconButton)
    }
    assert "Trang trước" in header_btns
    assert "Trang sau" in header_btns
    assert "Tải lại" in header_btns
    assert header_btns["Trang trước"].disabled is True
    assert header_btns["Trang sau"].disabled is True

    # 2. Navigating to a redirected HTTPS URL updates dlg._url and history_change enables Back
    wv = dlg._embedded_host.control().content
    wv.on_history_change(SimpleNamespace(data='{"can_go_back":true,"can_go_forward":false}'))
    assert header_btns["Trang trước"].disabled is False
    assert header_btns["Trang sau"].disabled is True
    redirected_url = "https://docs.google.com/spreadsheets/d/1y5syAENL/htmlview"
    wv.on_url_change(SimpleNamespace(data=redirected_url))
    assert dlg._url == redirected_url
    dlg._handle_open_external()
    assert launched == [redirected_url]

    # 3. Pre-commit error on a fresh dialog falls back cleanly, clears _webview, without leaving a dead blank host
    dialogs.clear()
    dlg_err = InAppWebViewDialog(
        live_page,
        url="https://zalo.me/g/nhom_lop_b",
        title="Nhóm Zalo",
        on_open_external=launched.append,
    )
    assert dlg_err.show() is True
    wv_err = dlg_err._embedded_host.control().content
    wv_err.on_web_error(SimpleNamespace(data="__UTH_EXTERNAL_APP_MISSING__:zalo"))
    assert dlg_err._embedded_host is None
    assert dlg_err._webview is None
    asyncio.run(dlg_err._reload())
    body_buttons = [
        n for n in _iter_tree(dialogs[0].content) if isinstance(n, ft.Button)
    ]
    assert len(body_buttons) >= 1


def test_demo_mode_blocks_mid_session_uth_lms_redirects_across_webview_hosts_and_dialog(
    monkeypatch: pytest.MonkeyPatch,
):
    """HIGH-SEAM: In Demo Mode (uthelper_demo), non-UTH HTTPS and local preview URLs work,
    but initial or mid-session redirects/links to *.ut.edu.vn are blocked across
    EmbeddedWebViewHost, WindowsWebView2Dock, CoursesWebViewPane, and InAppWebViewDialog."""
    from email.message import Message
    import importlib.util
    import sys
    from urllib.error import HTTPError
    from urllib.parse import urlsplit
    import urllib.request

    from config import settings
    from gui.components import in_app_webview as webview_module
    from gui.components.courses.embedded_webview_host import (
        WindowsWebView2Dock,
        _is_demo_lms_blocked,
        _is_demo_mode_active,
    )
    from gui.components.in_app_webview import (
        InAppWebViewDialog,
        _HttpsOnlyRedirectHandler,
        _fetch_https_preview,
    )
    from gui.core import utils as gui_utils

    monkeypatch.setattr(settings, "UTH_USERNAME", "uthelper_demo")

    # When PR #136's core.demo_sandbox is not merged yet, verify the standalone fallback
    # safely returns False without ImportError, then supply the minimal seam to verify
    # the full end-to-end WebView Demo blocking behavior. Once PR #136 merges, this test
    # runs directly against the real core.demo_sandbox and gui.core.utils.is_uth_lms_url.
    if importlib.util.find_spec("core.demo_sandbox") is None:
        assert _is_demo_mode_active() is False
        assert _is_demo_lms_blocked("https://courses.ut.edu.vn/my/") is False
        monkeypatch.setitem(
            sys.modules,
            "core.demo_sandbox",
            SimpleNamespace(
                is_demo_account=lambda user: str(user or "").strip().casefold()
                == "uthelper_demo"
            ),
        )
        monkeypatch.setattr(
            gui_utils,
            "is_uth_lms_url",
            lambda raw_url: (
                isinstance(raw_url, str)
                and urlsplit(raw_url.strip()).scheme.lower() in ("http", "https")
                and (
                    (urlsplit(raw_url.strip()).hostname or "").rstrip(".").casefold()
                    == "ut.edu.vn"
                    or (urlsplit(raw_url.strip()).hostname or "")
                    .rstrip(".")
                    .casefold()
                    .endswith(".ut.edu.vn")
                )
            ),
            raising=False,
        )

    live_page = _make_live_mobile_page(ft.PagePlatform.ANDROID)

    # 1. Direct UTH LMS EmbeddedWebViewHost is refused in Demo Mode, while loopback and non-UTH work
    assert (
        build_embedded_webview_host(
            "https://courses.ut.edu.vn/course/view.php?id=101", page=live_page
        )
        is None
    )
    loopback_host = build_embedded_webview_host(
        "http://127.0.0.1:51234/pdf-viewer/AbCdEfGh_IjKlMn/pdf_host.html",
        page=live_page,
    )
    assert loopback_host is not None
    loopback_host.dispose()

    # 2. WindowsWebView2Dock blocks mid-session UTH LMS navigation in Demo Mode
    dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="https://docs.google.com/spreadsheets/d/1/edit",
    )
    try:
        assert (
            dock._is_navigation_uri_allowed(
                "https://docs.google.com/spreadsheets/d/1/htmlview"
            )
            is True
        )
        assert dock._is_navigation_uri_allowed("https://courses.ut.edu.vn/my/") is False
        assert (
            dock._is_navigation_uri_allowed("https://portal.ut.edu.vn/dashboard")
            is False
        )
    finally:
        dock.dispose()

    # 3. CoursesWebViewPane opened on non-UTH page blocks mid-session redirect and _navigate_link to *.ut.edu.vn
    launched_ext: list[str] = []
    pane = CoursesWebViewPane(
        page=live_page,
        source="courses",
        title="Google Docs",
        url="https://docs.google.com/spreadsheets/d/1/edit",
        on_back=lambda: None,
        on_open_external=launched_ext.append,
    )
    try:
        assert pane._host is not None
        wv = pane._host.control().content
        assert getattr(wv, "block_uth_lms", False) is True

        # _navigate_link refuses UTH LMS link in Demo Mode
        pane._navigate_link("https://courses.ut.edu.vn/mod/assign/view.php?id=101")
        assert pane._url == "https://docs.google.com/spreadsheets/d/1/edit"
        assert pane._host is not None

        # Mid-session WebView redirect to courses.ut.edu.vn is blocked and tears down host
        wv.on_url_change(SimpleNamespace(data="https://courses.ut.edu.vn/my/"))
        assert pane._host is None
        assert pane._blocked_navigation is True
        assert "ut.edu.vn" not in pane._url
    finally:
        pane.dispose()

    # 4. InAppWebViewDialog blocks direct UTH show(), mid-session UTH redirects, preview fetches, and external launch
    dialogs: list[Any] = []
    monkeypatch.setattr(live_page, "show_dialog", dialogs.append, raising=False)
    monkeypatch.setattr(live_page, "pop_dialog", lambda: None, raising=False)

    assert (
        InAppWebViewDialog(
            live_page, url="https://courses.ut.edu.vn/course/view.php?id=101"
        ).show()
        is False
    )
    assert dialogs == []

    dlg = InAppWebViewDialog(
        live_page,
        url="https://docs.google.com/document/d/1/edit",
        title="Docs",
        on_open_external=launched_ext.append,
    )
    assert dlg.show() is True
    assert len(dialogs) == 1
    assert dlg._embedded_host is not None
    wv_dlg = dlg._embedded_host.control().content
    wv_dlg.on_url_change(SimpleNamespace(data="https://thnn.ut.edu.vn/my/"))
    assert dlg._closed is True
    assert dlg._embedded_host is None
    assert "ut.edu.vn" not in dlg._url

    # Fallback flet_webview path also closes on mid-session UTH redirect in Demo Mode
    created_fb: list[dict[str, Any]] = []

    class _FakeWebView(ft.Container):
        def __init__(self, url, **kwargs):
            created_fb.append(kwargs)
            super().__init__(expand=True)

    monkeypatch.setattr(
        webview_module, "flet_webview", SimpleNamespace(WebView=_FakeWebView)
    )
    pop_count = 0

    def _pop():
        nonlocal pop_count
        pop_count += 1

    dummy_page = SimpleNamespace(
        platform=ft.PagePlatform.ANDROID,
        web=False,
        width=400,
        height=800,
        show_dialog=dialogs.append,
        pop_dialog=_pop,
        run_task=lambda *_a, **_k: None,
        update=lambda: None,
    )
    dlg_fb = InAppWebViewDialog(
        dummy_page,  # type: ignore[arg-type]
        url="https://docs.google.com/document/d/2/edit",
        title="Docs 2",
        on_open_external=launched_ext.append,
    )
    assert dlg_fb.show() is True
    created_fb[0]["on_url_change"](SimpleNamespace(data="https://portal.ut.edu.vn/"))
    assert pop_count == 1
    assert dlg_fb._closed is True

    # _handle_open_external refuses if _url is UTH in Demo Mode
    dlg_fb._url = "https://courses.ut.edu.vn/my/"
    dlg_fb._handle_open_external()
    assert launched_ext == []

    # Static HTML preview & redirect handler reject UTH targets in Demo Mode
    assert _fetch_https_preview("https://courses.ut.edu.vn/my/") == ("", "")
    handler = _HttpsOnlyRedirectHandler()
    req = urllib.request.Request("https://example.com/shortlink")
    with pytest.raises(HTTPError):
        handler.redirect_request(
            req, None, 302, "Found", Message(), "https://courses.ut.edu.vn/my/"
        )


def test_in_app_webview_dialog_barrier_dismiss_disposes_host_and_pairs_modal_occlusion(
    monkeypatch: pytest.MonkeyPatch,
):
    """MEDIUM-LIFECYCLE: Barrier dismiss (on_dismiss) and close() must idempotently dispose
    _embedded_host and pair modal_dialog WebView2 occlusion (both 2-arg and tokenized)."""
    import inspect

    from gui.components.courses import embedded_webview_host as host_mod
    from gui.components.courses.embedded_webview_host import (
        clear_global_webview_occlusion,
        is_globally_occluded,
        set_global_webview_occlusion,
    )
    from gui.components.in_app_webview import (
        InAppWebViewDialog,
        _set_modal_webview_occlusion,
    )

    clear_global_webview_occlusion()
    try:
        live_page = _make_live_mobile_page(ft.PagePlatform.ANDROID)
        dialogs: list[Any] = []
        monkeypatch.setattr(live_page, "show_dialog", dialogs.append, raising=False)
        monkeypatch.setattr(live_page, "pop_dialog", lambda: None, raising=False)

        # 1. Barrier dismiss disposes _embedded_host and clears modal_dialog occlusion
        dlg = InAppWebViewDialog(
            live_page,
            url="https://docs.google.com/spreadsheets/d/1/edit",
            title="Sheet",
        )
        assert is_globally_occluded() is False
        assert dlg.show() is True
        assert is_globally_occluded() is True
        assert dlg._embedded_host is not None

        dispose_calls = 0
        orig_dispose = dlg._embedded_host.dispose

        def _spy_dispose() -> None:
            nonlocal dispose_calls
            dispose_calls += 1
            orig_dispose()

        monkeypatch.setattr(dlg._embedded_host, "dispose", _spy_dispose)
        alert = dialogs[-1]
        assert callable(alert.on_dismiss)
        alert.on_dismiss(None)

        assert dlg._closed is True
        assert dlg._embedded_host is None
        assert dlg._webview is None
        assert dispose_calls == 1
        assert is_globally_occluded() is False

        # Subsequent close() is a safe idempotent no-op
        dlg.close()
        assert dispose_calls == 1
        assert is_globally_occluded() is False

        # 2. Stacked outer occlusion stays occluded when inner InAppWebViewDialog closes
        if "token" in inspect.signature(set_global_webview_occlusion).parameters:
            set_global_webview_occlusion("modal_dialog", True, token="outer_modal_1")
            assert is_globally_occluded() is True

            dlg2 = InAppWebViewDialog(
                live_page,
                url="https://docs.google.com/document/d/2/edit",
                title="Doc",
            )
            assert dlg2.show() is True
            assert is_globally_occluded() is True
            dlg2.close()
            dialogs[-1].on_dismiss(None)
            assert is_globally_occluded() is True

            set_global_webview_occlusion("modal_dialog", False, token="outer_modal_1")
            assert is_globally_occluded() is False
        else:
            set_global_webview_occlusion("detail_view", True)
            assert is_globally_occluded() is True
            dlg2 = InAppWebViewDialog(
                live_page,
                url="https://docs.google.com/document/d/2/edit",
                title="Doc",
            )
            assert dlg2.show() is True
            dlg2.close()
            assert is_globally_occluded() is True
            set_global_webview_occlusion("detail_view", False)
            assert is_globally_occluded() is False

        # 3. Explicit 2-arg TypeError fallback remains safe even if signature lacks token=
        two_arg_calls: list[tuple[str, bool]] = []
        with monkeypatch.context() as mp:
            mp.setattr(
                host_mod,
                "set_global_webview_occlusion",
                lambda reason, occluded: two_arg_calls.append((reason, occluded)),
            )
            _set_modal_webview_occlusion(True, token="tok_test")
            _set_modal_webview_occlusion(False, token="tok_test")
        assert two_arg_calls == [("modal_dialog", True), ("modal_dialog", False)]
    finally:
        clear_global_webview_occlusion()




