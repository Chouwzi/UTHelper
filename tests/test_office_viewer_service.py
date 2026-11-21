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


def test_office_package_rejects_symlinks_case_insensitive_xml_and_forged_zip_sizes():
    import stat

    original = build_docx_bytes(paragraphs=["Safe doc"])

    # 1. Symlink entry inside ZIP is rejected
    out_sym = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as src, zipfile.ZipFile(out_sym, "w") as dst:
        for entry in src.infolist():
            dst.writestr(entry, src.read(entry))
        sym_info = zipfile.ZipInfo("word/secret_link.txt")
        sym_info.external_attr = (stat.S_IFLNK | 0o777) << 16
        dst.writestr(sym_info, b"C:/Windows/win.ini")
    assert not is_supported_office_package(out_sym.getvalue(), "doc.docx")

    # 2. Forged ZIP central-directory header claiming small file_size while stream > 16 MiB is rejected
    forged_buf = io.BytesIO()
    with zipfile.ZipFile(forged_buf, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        dst.writestr("[Content_Types].xml", "<Types/>")
        dst.writestr("word/document.xml", "<w:document/>")
        dst.writestr("word/CUSTOM.XML", b"a" * (16 * 1024 * 1024 + 128))
    raw_forged = bytearray(forged_buf.getvalue())
    assert not is_supported_office_package(bytes(raw_forged), "bomb.docx")


def test_pptx_host_sanitizes_smil_and_svg_data_href_while_preserving_foreign_object():
    from pathlib import Path

    pptx_host_js = (
        Path(__file__).resolve().parents[1] / "src" / "assets" / "office_viewer" / "pptx_host.js"
    ).read_text(encoding="utf-8")
    assert "animate, set" in pptx_host_js
    assert "data:image\\/svg\\+xml" in pptx_host_js
    assert "foreignObject" not in pptx_host_js.split("querySelectorAll(", 1)[1].split(")", 1)[0]
