"""Behavioral boundaries for local Office previews and fixed-page rendering."""

import io
import zipfile
from unittest.mock import Mock

import flet as ft

from core.courses.local_preview_server import LocalPreviewServer
from core.courses.office_viewer_service import is_supported_office_package, prepare_office_viewer
from core.courses import windows_office_bridge
from gui.components.courses.document_reader_view import DocumentReaderView
from tests.e2e_courses.builders import build_docx_bytes, build_pdf_bytes


def test_office_package_accepts_original_bytes_and_rejects_expansion_and_traversal():
    original = build_docx_bytes(paragraphs=["Tài liệu gốc"])
    assert is_supported_office_package(original, "lecture.docx")
    assert not is_supported_office_package(original, "lecture.doc")
    assert not is_supported_office_package(b"not a ZIP", "lecture.docx")
    for extra in ("../document.xml", "word/../../document.xml"):
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(out, "w") as target:
            for entry in source.infolist():
                target.writestr(entry, source.read(entry))
            target.writestr(extra, "unexpected")
        assert not is_supported_office_package(out.getvalue(), "lecture.docx")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as target:
        target.writestr("word/document.xml", b"x" * (16 * 1024 * 1024 + 1))
        target.writestr("[Content_Types].xml", "types")
    assert not is_supported_office_package(out.getvalue(), "lecture.docx")


def test_native_office_export_is_optional_on_non_windows(monkeypatch):
    run = Mock(side_effect=AssertionError("must not start a process"))
    monkeypatch.setattr(windows_office_bridge.sys, "platform", "linux")
    monkeypatch.setattr(windows_office_bridge.subprocess, "run", run)
    assert windows_office_bridge.render_office_pdf(build_docx_bytes(), "a.docx") is None
    run.assert_not_called()


def test_office_preview_budget_keeps_native_fallback():
    original = build_docx_bytes(paragraphs=["Original Word"])
    server = LocalPreviewServer(max_total_bytes=64)
    try:
        assert prepare_office_viewer(original, "lecture.docx", server) is None
    finally:
        server.stop()


def test_office_pdf_preview_keeps_save_and_external_actions_on_original():
    original = build_docx_bytes(paragraphs=["Original Word"])
    rendered = build_pdf_bytes(["Fixed page from Office"])
    save, external = Mock(), Mock()
    server = LocalPreviewServer()
    reader = DocumentReaderView(
        file_name="lecture.docx", data=original, kind="docx",
        preview_server=server, on_close=lambda: None,
        on_save=save, on_open_external=external, rendered_pdf_bytes=rendered,
    )
    try:
        assert reader._ensure_pdf_session().page_count == 1
        assert "Fixed page" in reader._ensure_pdf_session().extract_page_text(0)
        reader._save_btn.on_click(None)
        reader._ext_btn.on_click(None)
        save.assert_called_once_with("lecture.docx", original)
        external.assert_called_once_with("lecture.docx", original)
    finally:
        reader.dispose()
        server.stop()


def test_office_webview_adapter_is_mounted_as_control_and_disposed():
    original = build_docx_bytes(paragraphs=["Original Word"])
    server = LocalPreviewServer()
    host = Mock()
    host.control.return_value = ft.Container(expand=True)
    reader = DocumentReaderView(
        file_name="lecture.docx", data=original, kind="docx", preview_server=server,
        on_close=lambda: None, on_save=lambda *_: None, on_open_external=lambda *_: None,
        webview_builder=lambda _url: host,
    )
    try:
        descriptor = prepare_office_viewer(original, "lecture.docx", server)
        assert descriptor is not None
        assert server.has_active_url(descriptor.blob_url)
        assert reader._body_switcher.content is host.control.return_value
        reader.sync_bounds(20, 40, 500, 600)
        host.sync_bounds.assert_called_once_with(20, 95, 500, 545)
        reader.suspend()
        reader.resume()
        host.suspend.assert_called_once()
        host.resume.assert_called_once()
    finally:
        reader.dispose()
        server.stop()
    host.dispose.assert_called_once()
