"""Regression and integration tests for Milestone 3 (F13-F16)."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import socket
import struct
import sys
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import flet as ft
import pytest

from core.client import MoodleClient, _stream_bounded_response
from core.courses.archive_service import ArchiveService
from core.courses.cache_store import CourseCacheStore
from core.courses.catalog_service import CourseCatalogService
from core.courses.local_preview_server import LocalPreviewServer, _VIEWER_APP_CSP
from core.courses.models import (
    CourseAttachment,
    CourseModuleItem,
    CourseSection,
    CourseSummary,
)
from core.courses.office_viewer_service import (
    is_supported_office_package,
    is_supported_office_package_file,
    prepare_office_viewer_from_file,
    prepare_pdf_viewer,
)
from core.courses.preview_cache import PreviewFileCache
from core.courses import windows_office_bridge
from gui.components.courses.document_reader_view import DocumentReaderView
from gui.components.courses.embedded_webview_host import (
    EmbeddedWebViewHost,
    WindowsWebView2Dock,
    clear_persisted_webview2_profiles,
    webview2_account_profile_name,
)
from gui.controllers.courses_coordinator import CoursesCoordinator
from tests.e2e_courses.builders import build_docx_bytes, build_pdf_bytes, build_pptx_bytes

REPO_ROOT = Path(__file__).resolve().parents[1]


def _walk_controls(root):
    yield root
    for attr in ("content", "leading", "title", "subtitle", "trailing"):
        child = getattr(root, attr, None)
        if child is not None:
            yield from _walk_controls(child)
    for attr in ("controls", "actions"):
        children = getattr(root, attr, None)
        if isinstance(children, (list, tuple)):
            for child in children:
                if child is not None:
                    yield from _walk_controls(child)


# ---------------------------------------------------------------------------
# F13: Offline pdf.js bundle, routing & compact mobile toolbar
# ---------------------------------------------------------------------------


def test_f13_pdf_viewer_bundle_is_offline_and_complete():
    pdf_dir = REPO_ROOT / "src" / "assets" / "pdf_viewer"
    for name in (
        "pdf_host.html",
        "pdf_host.js",
        "pdf_viewer.css",
        "pdf.min.mjs",
        "pdf.worker.min.mjs",
        "manifest.json",
        "licenses/pdfjs-dist.txt",
    ):
        path = pdf_dir / name
        assert path.is_file() and path.stat().st_size > 0, name

    host_html = (pdf_dir / "pdf_host.html").read_text(encoding="utf-8")
    host_js = (pdf_dir / "pdf_host.js").read_text(encoding="utf-8")
    assert "http://" not in host_html and "https://" not in host_html
    assert "http://" not in host_js and "https://" not in host_js
    assert "__UTH_MSG__:" in host_js
    assert "window.setTheme" in host_js


def test_f13_local_preview_server_serves_pdf_viewer_with_mjs_and_csp(tmp_path: Path):
    srv = LocalPreviewServer()
    try:
        base = srv.pdf_viewer_base_url()
        assert "/pdf-viewer/" in base
        pdf_bytes = build_pdf_bytes(["Offline pdfjs page 1"])
        desc = prepare_pdf_viewer(pdf_bytes, srv, theme="dark")
        assert desc is not None
        assert desc.format == "pdf"
        assert "pdf_host.html" in desc.viewer_url

        parsed = http.client.urlsplit(desc.viewer_url)
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        try:
            conn.request("GET", f"{parsed.path}?{parsed.query}")
            r_html = conn.getresponse()
            html_body = r_html.read()
            assert r_html.status == 200
            assert r_html.getheader("Cache-Control") == "no-store, private"
            assert r_html.getheader("Content-Security-Policy") == _VIEWER_APP_CSP
            assert "worker-src 'self'" in _VIEWER_APP_CSP
            assert b"pdf_host.js" in html_body

            base_path = http.client.urlsplit(base).path
            conn.request("GET", f"{base_path}pdf.min.mjs")
            r_mjs = conn.getresponse()
            _ = r_mjs.read()
            assert r_mjs.status == 200
            assert r_mjs.getheader("Content-Type") == "application/javascript; charset=utf-8"
            assert r_mjs.getheader("Cache-Control") == "public, max-age=3600"
            etag = r_mjs.getheader("ETag")
            assert etag

            conn.request("GET", f"{base_path}pdf.min.mjs", headers={"If-None-Match": etag})
            r_304 = conn.getresponse()
            body_304 = r_304.read()
            assert r_304.status == 304
            assert body_304 == b""
        finally:
            conn.close()
    finally:
        srv.stop()


def test_f13_non_raster_pdf_routes_to_pdfjs_webview_and_supports_dark_toggle(monkeypatch):
    from core.courses import pdf_renderer as pdf_mod

    monkeypatch.setattr(pdf_mod, "_pdfium", None)
    monkeypatch.setattr(pdf_mod, "_pymupdf", None)
    pdf_bytes = build_pdf_bytes(["Mobile non-raster PDF content"])
    srv = LocalPreviewServer()
    built_urls: list[str] = []
    eval_calls: list[str] = []

    class _FakeHost:
        def __init__(self, url: str) -> None:
            self._url = url
            self._ctrl = ft.Container(key="pdfjs-wv", expand=True)

        def control(self) -> ft.Control:
            return self._ctrl

        async def eval_js(self, script: str) -> None:
            eval_calls.append(script)

        def sync_bounds(self, *_a, **_kw) -> None:
            return None

        def dispose(self) -> None:
            return None

    def _wv_builder(url: str, **_kw):
        built_urls.append(url)
        return _FakeHost(url)

    try:
        reader = DocumentReaderView(
            file_name="mobile.pdf",
            data=pdf_bytes,
            kind="pdf",
            preview_server=srv,
            on_close=lambda: None,
            on_save=lambda *_a: None,
            on_open_external=lambda *_a: None,
            webview_builder=_wv_builder,
            pane_width=360.0,
        )
        try:
            assert len(built_urls) == 1
            assert "pdf_host.html" in built_urls[0]
            assert reader._night_btn.visible is True
            all_texts = [
                getattr(c, "value", "")
                for c in _walk_controls(reader)
                if isinstance(c, ft.Text)
            ]
            assert not any("Mobile non-raster PDF content" in str(t) for t in all_texts)
            reader._toggle_reader_mode()
            assert any("setTheme('dark')" in call for call in eval_calls)
        finally:
            reader.dispose()
    finally:
        srv.stop()


def test_f13_compact_360dp_native_pdf_toolbar_stays_single_row():
    pdf_bytes = build_pdf_bytes(["Page 1", "Page 2"])
    srv = LocalPreviewServer()
    try:
        reader = DocumentReaderView(
            file_name="desktop_compact.pdf",
            data=pdf_bytes,
            kind="pdf",
            preview_server=srv,
            on_close=lambda: None,
            on_save=lambda *_a: None,
            on_open_external=lambda *_a: None,
            pane_width=360.0,
        )
        try:
            rows = [
                c
                for c in _walk_controls(reader)
                if isinstance(c, ft.Row)
                and any(
                    isinstance(ch, ft.IconButton) and getattr(ch, "tooltip", "") == "Phóng to"
                    for ch in (c.controls or [])
                )
            ]
            assert rows
            assert rows[0].wrap is False
        finally:
            reader.dispose()
    finally:
        srv.stop()


def test_f13_coordinator_skips_warm_pdf_on_mobile(tmp_path: Path, monkeypatch):
    from core.courses import pdf_renderer as pdf_mod

    opened_pdf_sessions: list[object] = []
    monkeypatch.setattr(
        "gui.controllers.courses_coordinator.PdfDocumentSession",
        lambda *a, **kw: opened_pdf_sessions.append((a, kw)) or Mock(),
    )
    monkeypatch.setattr(
        "gui.components.courses.document_reader_view.PdfDocumentSession",
        lambda *a, **kw: opened_pdf_sessions.append((a, kw)) or Mock(),
    )
    _ = pdf_mod
    pdf_bytes = build_pdf_bytes(["Mobile PDF"])
    store = CourseCacheStore(cache_dir=tmp_path, namespace="m")
    pcache = PreviewFileCache(tmp_path / "pcache")
    srv = LocalPreviewServer()
    cat = CourseCatalogService(cache_store=store, preview_cache=pcache)
    cat.fetch_attachment_bytes = lambda *_a, **_kw: pdf_bytes  # type: ignore[method-assign]
    page = SimpleNamespace(
        overlay=[],
        web=False,
        platform=ft.PagePlatform.ANDROID,
        title="UTHelper",
        width=390,
        height=844,
        update=lambda: None,
        run_task=lambda *_a: None,
    )
    coord = CoursesCoordinator(
        page=page,  # type: ignore[arg-type]
        catalog_service=cat,
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        monkeypatch.setattr(
            "gui.controllers.courses_coordinator.is_embedded_webview_supported",
            lambda _p: True,
        )
        course = CourseSummary(id=1, source="courses", fullname="C1", shortname="C1", display_name="C1")
        mod = CourseModuleItem(cmid=1, instance_id=1, modname="resource", name="PDF", section_id=1)
        att = CourseAttachment(
            file_name="lecture.pdf",
            file_url="https://courses.ut.edu.vn/pluginfile.php/1/lecture.pdf",
            preview_kind="pdf",
            file_size=len(pdf_bytes),
        )
        asyncio.run(coord.open_attachment_async(course, mod, att))
        assert opened_pdf_sessions == []
    finally:
        coord.close()


# ---------------------------------------------------------------------------
# F14: Office->PDF attach & LibreOffice fallback, WebView2 paint gate, PPTX worker
# ---------------------------------------------------------------------------


def test_f14_windows_office_bridge_does_not_abort_when_open_and_supports_libreoffice(
    tmp_path: Path, monkeypatch
):
    assert "exit 2" not in windows_office_bridge._EXPORT_SCRIPT
    assert "$owned" in windows_office_bridge._EXPORT_SCRIPT

    # Non-win32 guard still skips subprocesses
    monkeypatch.setattr(windows_office_bridge.sys, "platform", "linux")
    assert windows_office_bridge.render_office_pdf(build_pptx_bytes(), "slides.pptx") is None

    # LibreOffice fallback path on win32 when MS Office COM is absent
    monkeypatch.setattr(windows_office_bridge.sys, "platform", "win32")
    monkeypatch.setattr(windows_office_bridge.shutil, "which", lambda _name: None)
    monkeypatch.setattr(windows_office_bridge, "_find_soffice_executable", lambda: r"C:\LO\soffice.exe")
    fake_winreg = SimpleNamespace(
        HKEY_CLASSES_ROOT=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=Mock(side_effect=OSError("no office")),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    captured_cmds: list[list[str]] = []

    class _FakeProc:
        pid = 4242
        returncode = 0

        def __init__(self, cmd, **_kw) -> None:
            captured_cmds.append(list(cmd))
            out_dir = Path(cmd[cmd.index("--outdir") + 1])
            in_path = Path(cmd[-1])
            (out_dir / f"{in_path.stem}.pdf").write_bytes(b"%PDF-1.4\n%lo-ok\n")

        def wait(self, timeout=None) -> int:
            return 0

    monkeypatch.setattr(windows_office_bridge.subprocess, "Popen", _FakeProc)
    pdf = windows_office_bridge.render_office_pdf(build_pptx_bytes(), "deck.pptx", timeout=15)
    assert pdf is not None and pdf.startswith(b"%PDF-")
    assert len(captured_cmds) == 1
    assert any(arg.startswith("-env:UserInstallation=") for arg in captured_cmds[0])
    assert "--headless" in captured_cmds[0] and "--convert-to" in captured_cmds[0]


def test_f14_preview_cache_converted_pdf_and_coordinator_reuse(tmp_path: Path, monkeypatch):
    pcache = PreviewFileCache(tmp_path / "pcache")
    pptx_bytes = build_pptx_bytes(slides=["Slide 1"])
    pdf_bytes = build_pdf_bytes(["Exported Slide 1"])

    assert pcache.get_cached_converted_pdf(pptx_bytes, "deck.pptx") is None
    pcache.put_converted_pdf(pptx_bytes, "deck.pptx", pdf_bytes)
    assert pcache.get_cached_converted_pdf(pptx_bytes, "deck.pptx") == pdf_bytes

    store = CourseCacheStore(cache_dir=tmp_path, namespace="win")
    srv = LocalPreviewServer()
    cat = CourseCatalogService(cache_store=store, preview_cache=pcache)
    cat.fetch_attachment_bytes = lambda *_a, **_kw: pptx_bytes  # type: ignore[method-assign]
    page = SimpleNamespace(
        overlay=[],
        web=False,
        platform=ft.PagePlatform.WINDOWS,
        title="UTHelper",
        width=1200,
        height=800,
        update=lambda: None,
        run_task=lambda *_a: None,
    )
    render_calls: list[str] = []

    def _fake_render(data: bytes, fname: str) -> bytes:
        render_calls.append(fname)
        return pdf_bytes

    monkeypatch.setattr("gui.controllers.courses_coordinator.render_office_pdf", _fake_render)
    pcache2 = PreviewFileCache(tmp_path / "pcache2")
    coord = CoursesCoordinator(
        page=page,  # type: ignore[arg-type]
        catalog_service=cat,
        preview_cache=pcache2,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        course = CourseSummary(id=1, source="courses", fullname="C1", shortname="C1", display_name="C1")
        mod = CourseModuleItem(cmid=1, instance_id=1, modname="resource", name="PPTX", section_id=1)
        att = CourseAttachment(
            file_name="deck.pptx",
            file_url="https://courses.ut.edu.vn/pluginfile.php/1/deck.pptx",
            preview_kind="pptx",
            file_size=len(pptx_bytes),
        )
        asyncio.run(coord.open_attachment_async(course, mod, att))
        asyncio.run(coord.open_attachment_async(course, mod, att))
        assert render_calls == ["deck.pptx"]
    finally:
        coord.close()


def test_f14_webview2_paint_gating_and_internal_message_filtering():
    forwarded_msgs: list[str] = []
    ready_events: list[str] = []
    error_events: list[str] = []
    dock = WindowsWebView2Dock(
        page_title="UTHelper",
        url="http://127.0.0.1:45678/viewer/tok/pptx_host.html",
        on_ready=lambda: ready_events.append("ready"),
        on_error=lambda err: error_events.append(err),
        on_message=lambda msg: forwarded_msgs.append(msg),
    )
    host = EmbeddedWebViewHost(
        backend="webview2",
        url=dock._url,
        dock=dock,
        on_ready=lambda: ready_events.append("ready"),
        on_error=lambda err: error_events.append(err),
        on_message=lambda msg: forwarded_msgs.append(msg),
    )
    callbacks: dict[str, object] = {}

    class _Core:
        can_go_back = False
        can_go_forward = False
        def add_document_title_changed(self, fn): callbacks["title"] = fn
        def add_source_changed(self, fn): callbacks["source"] = fn
        def add_navigation_starting(self, fn): callbacks["nav_start"] = fn
        def add_navigation_completed(self, fn): callbacks["nav_done"] = fn
        def add_history_changed(self, fn): callbacks["history"] = fn
        def add_web_message_received(self, fn): callbacks["web_msg"] = fn
        def add_new_window_requested(self, fn): callbacks["new_win"] = fn

    core = _Core()
    dock._core = core
    dock._bind_events(core)

    # Navigation completed does NOT reveal or clear loading spinner before paint_ready
    callbacks["nav_done"](core, SimpleNamespace(is_success=True))  # type: ignore[operator]
    assert ready_events == ["ready"]
    assert dock._paint_ready is False
    host._handle_webview2_loading(False)
    assert host._loading_ring is not None and host._loading_ring.visible is True

    # Internal __UTH_VIEWER_READY__ marks paint_ready without leaking to on_message
    callbacks["web_msg"](  # type: ignore[operator]
        core,
        SimpleNamespace(try_get_web_message_as_string=lambda: "__UTH_VIEWER_READY__"),
    )
    assert dock._paint_ready is True
    assert forwarded_msgs == []
    host._handle_webview2_loading(False)
    assert host._loading_ring.visible is False

    # __UTH_MSG__: JSON is stripped & forwarded
    callbacks["web_msg"](  # type: ignore[operator]
        core,
        SimpleNamespace(try_get_web_message_as_string=lambda: '__UTH_MSG__:{"type":"ready"}'),
    )
    assert forwarded_msgs == ['{"type":"ready"}']

    # __UTH_VIEWER_ERROR__: triggers on_error without leaking to on_message
    callbacks["web_msg"](  # type: ignore[operator]
        core,
        SimpleNamespace(try_get_web_message_as_string=lambda: "__UTH_VIEWER_ERROR__:oom"),
    )
    assert error_events == ["oom"]
    assert forwarded_msgs == ['{"type":"ready"}']


def test_f14_pptx_worker_exists_and_document_reader_watchdog_falls_back():
    worker_js = (
        REPO_ROOT / "src" / "assets" / "office_viewer" / "pptx_worker.js"
    ).read_text(encoding="utf-8")
    host_js = (
        REPO_ROOT / "src" / "assets" / "office_viewer" / "pptx_host.js"
    ).read_text(encoding="utf-8")
    common_js = (
        REPO_ROOT / "src" / "assets" / "office_viewer" / "viewer_common.js"
    ).read_text(encoding="utf-8")
    assert 'importScripts("./pptx_engine.js")' in worker_js
    assert 'new Worker("./pptx_worker.js")' in host_js
    assert "__UTH_MSG__:" in common_js
    assert "postMessage" in common_js

    pptx_bytes = build_pptx_bytes(slides=["Slide One", "Slide Two"])
    srv = LocalPreviewServer()
    captured: dict[str, object] = {}

    class _HangingHost:
        def control(self) -> ft.Control:
            return ft.Container(key="hanging-pptx", expand=True)

        def dispose(self) -> None:
            return None

    def _wv_builder(url: str, **kwargs):
        captured["url"] = url
        captured["on_message"] = kwargs.get("on_message")
        return _HangingHost()

    try:
        reader = DocumentReaderView(
            file_name="deck.pptx",
            data=pptx_bytes,
            kind="pptx",
            preview_server=srv,
            on_close=lambda: None,
            on_save=lambda *_a: None,
            on_open_external=lambda *_a: None,
            webview_builder=_wv_builder,
        )
        try:
            assert "pptx_host.html" in str(captured["url"])
            assert reader._viewer_watchdog is not None
            # Ready message cancels watchdog
            captured["on_message"]('{"type":"ready"}')  # type: ignore[operator]
            assert reader._viewer_ready_received is True
            assert reader._viewer_watchdog is None

            # Error message triggers Tier-B PPTX slide fallback cleanly
            captured["on_message"]('{"type":"error","message":"worker timeout"}')  # type: ignore[operator]
            assert reader._webview_failed is True
            assert reader._webview_fallback_notice
        finally:
            reader.dispose()
    finally:
        srv.stop()


# ---------------------------------------------------------------------------
# F15: AppController WebView close-before-purge boundary & namespace forwarding
# ---------------------------------------------------------------------------


def test_f15_app_controller_closes_webviews_before_purging_on_account_switch(
    tmp_path: Path, monkeypatch
):
    from config import settings
    from gui import app_controller as ac_mod

    events: list[str] = []
    controller = ac_mod.AppController.__new__(ac_mod.AppController)
    controller._settings_identity = ("student_old", "pass_old")
    controller.page = SimpleNamespace(update=lambda: None)
    controller.orchestrator = SimpleNamespace(
        moodle_service=SimpleNamespace(clear_all_caches=lambda: None),
        clear_detail_cache=lambda: None,
        _userid_cache=1,
        is_logged_in=True,
    )
    controller._portal_client = SimpleNamespace(clear_token=lambda: None)
    controller._today_schedule_coordinator = SimpleNamespace(
        replace_cache=lambda _c: None, refresh=Mock()
    )
    controller._today_schedule_component = SimpleNamespace(set_state=lambda _s: None)
    controller._reconcile_class_schedule_notifications = Mock()
    controller._load_data_async = AsyncMock()
    controller._close_app_webview = lambda **_kw: events.append("close_app_webview") or True
    controller._courses_coordinator = SimpleNamespace(
        reset_for_account_switch=lambda ns: events.append(f"reset_courses:{ns}")
    )
    controller.quiz_player_view = SimpleNamespace(
        visible=False,
        clear_persisted_sessions=lambda: events.append("clear_quiz_sessions"),
    )

    def _run_task(fn, *args, **kwargs):
        if fn == controller.quiz_player_view.clear_persisted_sessions:
            fn(*args, **kwargs)

    purge_done = threading.Event()
    monkeypatch.setattr(
        "gui.components.courses.embedded_webview_host.clear_persisted_webview2_profiles",
        lambda *, keep_profile_name=None, **_kw: (
            events.append(f"purge_webview2:{keep_profile_name}"),
            purge_done.set(),
        ),
    )

    controller._safe_run_task = _run_task
    monkeypatch.setattr(settings, "UTH_USERNAME", "student_new")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "pass_new")
    monkeypatch.setattr(sys, "platform", "win32")

    asyncio.run(ac_mod.AppController._complete_login_success(controller))
    assert purge_done.wait(timeout=2.0)

    assert events[0] == "close_app_webview"
    assert events[1].startswith("reset_courses:")
    assert "clear_quiz_sessions" in events[2:]
    assert any(item.startswith("purge_webview2:acct_") for item in events[2:])


def test_f15_courses_coordinator_forwards_account_namespace(tmp_path: Path, monkeypatch):
    from gui.components.courses import embedded_webview_host as host_mod

    store = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|u1")
    pcache = PreviewFileCache(tmp_path / "pc")
    srv = LocalPreviewServer()
    page = SimpleNamespace(
        overlay=[],
        web=False,
        platform=ft.PagePlatform.WINDOWS,
        title="UTHelper",
        width=1200,
        height=800,
        update=lambda: None,
    )
    coord = CoursesCoordinator(
        page=page,  # type: ignore[arg-type]
        catalog_service=CourseCatalogService(cache_store=store, preview_cache=pcache),
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
        account_namespace="https://courses.ut.edu.vn|u1",
    )
    try:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(host_mod, "_webview2_runtime_available", lambda: True)

        class _FakeDock(WindowsWebView2Dock):
            def start(self) -> None:
                return None

        monkeypatch.setattr(host_mod, "WindowsWebView2Dock", _FakeDock)
        wv = coord.build_webview_host("https://courses.ut.edu.vn/")
        assert wv is not None and wv.dock is not None
        assert wv.dock._profile_name == webview2_account_profile_name(
            "https://courses.ut.edu.vn|u1"
        )
        wv.dispose()
    finally:
        coord.close()


# ---------------------------------------------------------------------------
# F16: Fast ZIP validation, skip redundant convert, HTTP/1.1 Keep-Alive, streaming
# ---------------------------------------------------------------------------


def test_f16_office_package_skips_decompressing_non_xml_media_entries(monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
        zf.writestr("word/media/large_video.bin", b"M" * (4 * 1024 * 1024))
    payload = buf.getvalue()

    opened_names: list[str] = []
    orig_open = zipfile.ZipFile.open

    def _spy_open(self, name, *args, **kwargs):
        entry_name = name.filename if isinstance(name, zipfile.ZipInfo) else str(name)
        opened_names.append(entry_name)
        return orig_open(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", _spy_open)
    assert is_supported_office_package(payload, "media_heavy.docx") is True
    assert "word/media/large_video.bin" not in opened_names
    assert "[Content_Types].xml" in opened_names and "word/document.xml" in opened_names


def test_f16_coordinator_skips_convert_document_bytes_for_ooxml_webview(
    tmp_path: Path, monkeypatch
):
    docx_bytes = build_docx_bytes(paragraphs=["Fast WebView path"])
    doc_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 128

    store = CourseCacheStore(cache_dir=tmp_path, namespace="f16")
    pcache = PreviewFileCache(tmp_path / "pc")
    srv = LocalPreviewServer()
    cat = CourseCatalogService(cache_store=store, preview_cache=pcache)
    cat.fetch_attachment_bytes = lambda *_a, **kw: (  # type: ignore[method-assign]
        docx_bytes if "lecture.docx" in _a[1] else doc_bytes
    )
    page = SimpleNamespace(
        overlay=[],
        web=False,
        platform=ft.PagePlatform.ANDROID,
        title="UTHelper",
        width=390,
        height=844,
        update=lambda: None,
        run_task=lambda *_a: None,
    )
    convert_calls: list[str] = []

    def _spy_convert(name: str, data: bytes, kind: str, **_kw):
        convert_calls.append(name)
        from core.courses.document_converter import ConvertedDocument

        return ConvertedDocument(title=name, kind=kind, markdown_text="legacy")

    monkeypatch.setattr("gui.controllers.courses_coordinator.convert_document_bytes", _spy_convert)
    monkeypatch.setattr(
        "gui.controllers.courses_coordinator.is_embedded_webview_supported",
        lambda _p: True,
    )

    coord = CoursesCoordinator(
        page=page,  # type: ignore[arg-type]
        catalog_service=cat,
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        course = CourseSummary(id=1, source="courses", fullname="C", shortname="C", display_name="C")
        mod = CourseModuleItem(cmid=1, instance_id=1, modname="resource", name="Doc", section_id=1)
        att_docx = CourseAttachment(
            file_name="lecture.docx",
            file_url="https://courses.ut.edu.vn/pluginfile.php/1/lecture.docx",
            preview_kind="docx",
            file_size=len(docx_bytes),
        )
        att_doc = CourseAttachment(
            file_name="legacy.doc",
            file_url="https://courses.ut.edu.vn/pluginfile.php/1/legacy.doc",
            preview_kind="docx",
            file_size=len(doc_bytes),
        )
        asyncio.run(coord.open_attachment_async(course, mod, att_docx))
        assert convert_calls == []
        asyncio.run(coord.open_attachment_async(course, mod, att_doc))
        assert convert_calls == ["legacy.doc"]
    finally:
        coord.close()


def test_f16_local_preview_server_keepalive_ram_cache_and_register_file(
    tmp_path: Path, monkeypatch
):
    disk_file = tmp_path / "cached.pdf"
    disk_file.write_bytes(b"0123456789ABCDEF" * 4)

    srv = LocalPreviewServer()
    try:
        file_url = srv.register_file(disk_file, "application/pdf")
        desc = prepare_office_viewer_from_file(
            tmp_path / "sample.docx", "sample.docx", srv
        )
        assert desc is None  # missing file
        sample_docx = tmp_path / "sample.docx"
        sample_docx.write_bytes(build_docx_bytes(paragraphs=["Disk file"]))
        desc_ok = prepare_office_viewer_from_file(sample_docx, "sample.docx", srv)
        assert desc_ok is not None

        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        try:
            fp = http.client.urlsplit(file_url)
            conn.request("GET", f"{fp.path}?{fp.query}")
            r1 = conn.getresponse()
            b1 = r1.read()
            assert r1.status == 200
            assert b1 == disk_file.read_bytes()

            # Reuse same Keep-Alive connection for 206 byte range
            conn.request("GET", f"{fp.path}?{fp.query}", headers={"Range": "bytes=4-11"})
            r2 = conn.getresponse()
            b2 = r2.read()
            assert r2.status == 206
            assert b2 == b"456789AB"

            # Reuse same Keep-Alive connection for 404
            conn.request("GET", "/nonexistent")
            r3 = conn.getresponse()
            _ = r3.read()
            assert r3.status == 404

            # Static asset RAM cache hit does not re-read disk
            base_path = http.client.urlsplit(srv.viewer_base_url()).path
            conn.request("GET", f"{base_path}viewer.css")
            r_css1 = conn.getresponse()
            css_body = r_css1.read()
            assert r_css1.status == 200
            assert len(css_body) > 0

            monkeypatch.setattr(Path, "read_bytes", Mock(side_effect=AssertionError("disk read on cache hit")))
            conn.request("GET", f"{base_path}viewer.css")
            r_css2 = conn.getresponse()
            assert r_css2.status == 200
            assert r_css2.read() == css_body
        finally:
            conn.close()

        # Deleting backing file produces 404 cleanly
        disk_file.unlink()
        conn2 = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        try:
            fp = http.client.urlsplit(file_url)
            conn2.request("GET", f"{fp.path}?{fp.query}")
            r404 = conn2.getresponse()
            _ = r404.read()
            assert r404.status == 404
        finally:
            conn2.close()
    finally:
        srv.stop()


def test_f16_moodle_client_releases_session_lock_skips_throttle_and_streams_gzip(
    tmp_path: Path, monkeypatch
):
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "abc"
    client._session_owner = "u1"
    monkeypatch.setattr("core.client.settings.UTH_USERNAME", "u1")

    throttle_mock = Mock(side_effect=AssertionError("must not throttle downloads"))
    monkeypatch.setattr(client, "_throttle", throttle_mock)

    plain_payload = b"hello-stream-" * 5000
    gzip_payload = gzip.compress(plain_payload)
    entered_read = threading.Event()
    allow_finish = threading.Event()

    class _FakeResp(io.BytesIO):
        def __init__(self, data: bytes, headers: dict[str, str]) -> None:
            super().__init__(data)
            self._headers = headers
            self.status = 200
            self.fp = self

        def geturl(self) -> str:
            return "https://courses.ut.edu.vn/pluginfile.php/1/file.pdf"

        def info(self):
            return self._headers

        def read(self, n: int = -1):
            entered_read.set()
            assert allow_finish.wait(timeout=3.0)
            return super().read(n)

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            self.close()
            return False

    fake_opener = SimpleNamespace(
        open=lambda _req, timeout=60: _FakeResp(
            gzip_payload,
            {"Content-Encoding": "gzip", "Content-Length": str(len(gzip_payload))},
        )
    )
    client._html_opener = fake_opener

    pcache = PreviewFileCache(tmp_path / "stream_cache")
    result_holder: dict[str, object] = {}

    def _run_dl() -> None:
        result_holder["path"] = pcache.stage_and_commit(
            "https://courses.ut.edu.vn/pluginfile.php/1/file.pdf",
            1,
            "file.pdf",
            lambda stream: client.download_session_file_to_path(
                "https://courses.ut.edu.vn/pluginfile.php/1/file.pdf", stream
            ),
        )

    t = threading.Thread(target=_run_dl)
    t.start()
    assert entered_read.wait(timeout=2.0)

    # Probe thread can acquire _session_lock non-blocking while download is mid-stream!
    acquired = client._session_lock.acquire(blocking=False)
    try:
        assert acquired is True
    finally:
        if acquired:
            client._session_lock.release()
        allow_finish.set()
    t.join(timeout=3.0)

    saved = result_holder.get("path")
    assert isinstance(saved, Path) and saved.read_bytes() == plain_payload
    throttle_mock.assert_not_called()

    # Session generation change mid-download aborts commit cleanly
    entered_read.clear()
    allow_finish.clear()
    client._html_opener = SimpleNamespace(
        open=lambda _req, timeout=60: _FakeResp(
            plain_payload,
            {"Content-Length": str(len(plain_payload))},
        )
    )
    result_holder.clear()
    t2 = threading.Thread(
        target=lambda: result_holder.__setitem__(
            "ok",
            client.download_session_file(
                "https://courses.ut.edu.vn/pluginfile.php/1/file2.pdf"
            ),
        )
    )
    t2.start()
    assert entered_read.wait(timeout=2.0)
    client.clear_session()
    allow_finish.set()
    t2.join(timeout=3.0)
    assert result_holder["ok"] is None


def test_f16_concurrent_prefetch_up_to_4_items_and_5mib(tmp_path: Path):
    store = CourseCacheStore(cache_dir=tmp_path, namespace="pref")
    pcache = PreviewFileCache(tmp_path / "pc")
    svc = CourseCatalogService(cache_store=store, preview_cache=pcache)
    downloaded: list[str] = []

    def _fake_fetch(source, file_url, time_modified, file_name):
        downloaded.append(file_name)
        return pcache.put_bytes(file_url, time_modified, file_name, b"ok")

    svc.fetch_or_cache_preview_file = _fake_fetch  # type: ignore[method-assign]
    srv = LocalPreviewServer()
    page = SimpleNamespace(
        overlay=[],
        web=False,
        platform="windows",
        title="UTHelper",
        width=1200,
        height=800,
        update=lambda: None,
    )
    coord = CoursesCoordinator(
        page=page,  # type: ignore[arg-type]
        catalog_service=svc,
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        course = CourseSummary(id=1, source="courses", fullname="C1", shortname="C1", display_name="C1")
        coord.courses_view._state.selected_course_key = course.course_key
        atts = tuple(
            CourseAttachment(
                file_name=f"deck_{i}.pptx",
                file_url=f"https://courses.ut.edu.vn/pluginfile.php/1/deck_{i}.pptx",
                preview_kind="pptx",
                file_size=3 * 1024 * 1024 + i,  # 3 MiB (> 1.5 MiB, <= 5 MiB)
                time_modified=1,
            )
            for i in range(6)
        )
        secs = [
            CourseSection(
                id=1,
                course_id=1,
                source="courses",
                section_num=1,
                name="S1",
                modules=(
                    CourseModuleItem(
                        cmid=1,
                        instance_id=1,
                        modname="resource",
                        name="M1",
                        section_id=1,
                        attachments=atts,
                    ),
                ),
            )
        ]
        asyncio.run(coord._prefetch_course_attachments_async(course, secs))
        assert len(downloaded) == 4
    finally:
        coord.close()


# ---------------------------------------------------------------------------
# Iteration 2 remediation regressions (Challenger 1 + Verifier minor fixes)
# ---------------------------------------------------------------------------


def test_f16_stream_bounded_response_rejects_premature_eof_and_truncated_gzip(
    tmp_path: Path,
):
    # (i) Premature TCP EOF when Content-Length: 100000 closes after 1000 bytes
    srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv_sock.bind(("127.0.0.1", 0))
    srv_sock.listen(1)
    port_cl = srv_sock.getsockname()[1]

    def _serve_short_cl() -> None:
        conn, _ = srv_sock.accept()
        try:
            conn.recv(4096)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\nConnection: close\r\n\r\n"
                + (b"X" * 1000)
            )
        finally:
            conn.close()

    t_cl = threading.Thread(target=_serve_short_cl, daemon=True)
    t_cl.start()
    cache_cl = PreviewFileCache(tmp_path / "cache_cl")
    try:
        http_conn = http.client.HTTPConnection("127.0.0.1", port_cl, timeout=5)
        try:
            http_conn.request("GET", "/partial.pdf")
            resp = http_conn.getresponse()
            committed = cache_cl.stage_and_commit(
                "https://courses.ut.edu.vn/partial.pdf",
                1,
                "partial.pdf",
                lambda stream: _stream_bounded_response(resp, stream.write),
            )
            assert committed is None
            assert list((tmp_path / "cache_cl").iterdir()) == []
        finally:
            http_conn.close()
    finally:
        srv_sock.close()
        t_cl.join(timeout=2.0)

    # (ii) Truncated Content-Encoding: gzip stream over valid HTTP chunked framing (eof == False)
    full_gz = gzip.compress(os.urandom(300000))
    assert len(full_gz) > 200000
    truncated_gz = full_gz[:2000]

    class _TruncGzipHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            chunk_hdr = f"{len(truncated_gz):x}\r\n".encode("ascii")
            self.wfile.write(chunk_hdr + truncated_gz + b"\r\n0\r\n\r\n")
            self.wfile.flush()

        def log_message(self, *_args) -> None:
            return

    gz_server = ThreadingHTTPServer(("127.0.0.1", 0), _TruncGzipHandler)
    t_gz = threading.Thread(target=gz_server.serve_forever, daemon=True)
    t_gz.start()
    cache_gz = PreviewFileCache(tmp_path / "cache_gz")
    try:
        http_conn2 = http.client.HTTPConnection("127.0.0.1", gz_server.server_address[1], timeout=5)
        try:
            http_conn2.request("GET", "/trunc.pdf")
            resp2 = http_conn2.getresponse()
            committed2 = cache_gz.stage_and_commit(
                "https://courses.ut.edu.vn/trunc.pdf",
                1,
                "trunc.pdf",
                lambda stream: _stream_bounded_response(resp2, stream.write),
            )
            assert committed2 is None
            assert list((tmp_path / "cache_gz").iterdir()) == []
        finally:
            http_conn2.close()
    finally:
        gz_server.shutdown()
        gz_server.server_close()
        t_gz.join(timeout=2.0)


def test_f16_session_server_rejects_transfer_encoding_post_with_close():
    proxy_calls: list[tuple[str, bytes | None]] = []

    class _FakeSessionClient:
        moodle_site_origin = "https://courses.ut.edu.vn"

        def open_session_raw(
            self,
            url: str,
            *,
            method: str = "GET",
            body: bytes | None = None,
            **_kwargs,
        ):
            proxy_calls.append((method, body))
            return 200, url, "application/json", b'{"ok":true}'

    srv = LocalPreviewServer()
    try:
        srv.register_session_provider("courses", _FakeSessionClient())
        sess_url = srv.build_session_bridge_url(
            "courses", "https://courses.ut.edu.vn/mod/quiz/view.php?id=1"
        )
        assert sess_url is not None
        parsed = http.client.urlsplit(sess_url)
        assert parsed.port is not None

        # Raw Keep-Alive socket sending Transfer-Encoding: chunked POST must get 501/411 + Connection: close
        raw_sock = socket.create_connection(("127.0.0.1", parsed.port), timeout=5)
        try:
            req_target = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
            payload = (
                f"POST {req_target} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{parsed.port}\r\n"
                "Transfer-Encoding: chunked\r\n"
                "Connection: keep-alive\r\n"
                "\r\n"
                "4\r\ntest\r\n0\r\n\r\n"
            ).encode("ascii")
            raw_sock.sendall(payload)
            chunks: list[bytes] = []
            while True:
                data = raw_sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
            response_raw = b"".join(chunks).decode("latin1")
            status_line = response_raw.split("\r\n", 1)[0]
            assert ("501" in status_line) or ("411" in status_line)
            assert "content-length: 0" in response_raw.lower()
            assert "connection: close" in response_raw.lower()
            assert proxy_calls == []
        finally:
            raw_sock.close()

        # Subsequent normal Content-Length POST + GET over Keep-Alive works cleanly
        conn = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=5)
        try:
            req_target = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
            conn.request(
                "POST",
                req_target,
                body=b"attempt=1",
                headers={"Content-Length": "9", "Content-Type": "application/x-www-form-urlencoded"},
            )
            r1 = conn.getresponse()
            assert r1.status == 200 and r1.read() == b'{"ok":true}'

            conn.request("GET", req_target)
            r2 = conn.getresponse()
            assert r2.status == 200 and r2.read() == b'{"ok":true}'
            assert proxy_calls == [("POST", b"attempt=1"), ("GET", None)]
        finally:
            conn.close()
    finally:
        srv.stop()


def test_f16_office_package_rejects_stealth_deflated_non_xml_bomb(tmp_path: Path):
    bomb_size = 50 * 1024 * 1024
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        dst.writestr("[Content_Types].xml", "<Types/>")
        dst.writestr("ppt/presentation.xml", "<p:presentation/>")
        dst.writestr("ppt/media/bomb.bin", b"\x00" * bomb_size)

    raw = bytearray(buf.getvalue())
    with zipfile.ZipFile(io.BytesIO(bytes(raw))) as zf:
        info = zf.getinfo("ppt/media/bomb.bin")
        comp_sz = info.compress_size
        assert info.file_size == bomb_size

    forged_size = comp_sz + 4096
    real_le = struct.pack("<I", bomb_size)
    forged_le = struct.pack("<I", forged_size)
    assert raw.count(real_le) >= 2
    stealth_bytes = bytes(raw.replace(real_le, forged_le))

    with zipfile.ZipFile(io.BytesIO(stealth_bytes)) as check_zf:
        forged_info = check_zf.getinfo("ppt/media/bomb.bin")
        assert forged_info.file_size == forged_size
        assert forged_info.file_size >= forged_info.compress_size

    assert is_supported_office_package(stealth_bytes, "stealth.pptx") is False
    stealth_path = tmp_path / "stealth.pptx"
    stealth_path.write_bytes(stealth_bytes)
    assert is_supported_office_package_file(stealth_path, "stealth.pptx") is False

    # Legitimate compressed non-XML media stays accepted
    ok_buf = io.BytesIO()
    with zipfile.ZipFile(ok_buf, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        dst.writestr("[Content_Types].xml", "<Types/>")
        dst.writestr("word/document.xml", "<w:document/>")
        dst.writestr("word/media/sample.bin", b"PNG-PIXEL-DATA-" * 4096)
    ok_bytes = ok_buf.getvalue()
    assert is_supported_office_package(ok_bytes, "valid_deflated.docx") is True
    ok_path = tmp_path / "valid_deflated.docx"
    ok_path.write_bytes(ok_bytes)
    assert is_supported_office_package_file(ok_path, "valid_deflated.docx") is True


def test_f15_courses_coordinator_reset_updates_catalog_service_cache_namespace(tmp_path: Path):
    store = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|u1")
    pcache = PreviewFileCache(tmp_path / "pc")
    svc = CourseCatalogService(cache_store=store, preview_cache=pcache)
    srv = LocalPreviewServer()
    page = SimpleNamespace(
        overlay=[],
        web=False,
        platform="windows",
        title="UTHelper",
        width=1200,
        height=800,
        update=lambda: None,
    )
    coord = CoursesCoordinator(
        page=page,  # type: ignore[arg-type]
        catalog_service=svc,
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
        account_namespace="https://courses.ut.edu.vn|u1",
    )
    try:
        coord.reset_for_account_switch("https://courses.ut.edu.vn|u2")
        expected_digest = hashlib.sha256(b"https://courses.ut.edu.vn|u2").hexdigest()[:12]
        assert store._digest == expected_digest
    finally:
        coord.close()


def test_f13_pdf_host_cancels_active_render_tasks_on_apply_zoom():
    host_js = (
        REPO_ROOT / "src" / "assets" / "pdf_viewer" / "pdf_host.js"
    ).read_text(encoding="utf-8")
    apply_zoom_block = host_js.split("function applyZoom(", 1)[1].split("async function runSearch(", 1)[0]
    assert "renderGeneration += 1" in apply_zoom_block
    assert "state.renderTask.cancel()" in apply_zoom_block
    assert "renderingPages.clear()" in apply_zoom_block
