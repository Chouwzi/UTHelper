"""Regression tests for Android InAppWebView parity and DOCX/PDF/PPTX viewer fixes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
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
