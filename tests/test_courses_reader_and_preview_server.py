"""TDD tests for LocalPreviewServer, DocumentReaderView, and ArchiveExplorerView."""

from __future__ import annotations

import asyncio
import http.client
import io
import re
import socket
import tarfile
import threading
import urllib.error
import urllib.request
import zipfile
from typing import Any
from unittest.mock import Mock
from urllib.parse import urlsplit

import flet as ft
import pytest

from core.courses.archive_service import ArchiveSecurityError, ArchiveService
from core.courses.local_preview_server import LocalPreviewServer
from gui.components.courses.archive_explorer_view import ArchiveExplorerView
from gui.components.courses.document_reader_view import DocumentReaderView
from gui.core.theme import C, apply_theme
from gui.core.theme_presets import THEME_PRESETS


def _walk_controls(root: Any):
    """Yield every Flet control reachable under ``root``."""
    if not isinstance(root, ft.BaseControl):
        return
    yield root
    for attr in ("content", "title", "subtitle", "leading", "trailing", "label"):
        child = getattr(root, attr, None)
        if isinstance(child, ft.BaseControl):
            yield from _walk_controls(child)
    for attr in ("controls", "tabs", "actions", "columns", "rows", "cells"):
        children = getattr(root, attr, None)
        if isinstance(children, (list, tuple)):
            for child in children:
                yield from _walk_controls(child)


def _all_texts(root: Any) -> list[str]:
    texts: list[str] = []
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.Text) and ctrl.value:
            texts.append(ctrl.value)
        elif isinstance(ctrl, ft.Markdown) and ctrl.value:
            texts.append(ctrl.value)
        elif isinstance(ctrl, (ft.Button, ft.OutlinedButton, ft.TextButton)):
            if isinstance(ctrl.content, str):
                texts.append(ctrl.content)
    return texts


def _build_minimal_docx(
    paragraphs: list[tuple[str, str | None]],
) -> bytes:
    """Build minimal valid DOCX bytes. Each item is (text, style_id_or_None)."""
    buf = io.BytesIO()
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    p_xml: list[str] = []
    for text, style in paragraphs:
        p_pr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        p_xml.append(f"<w:p>{p_pr}<w:r><w:t>{text}</w:t></w:r></w:p>")
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(p_xml)}</w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", doc_xml)
    return buf.getvalue()


def _col_letters(idx: int) -> str:
    res = ""
    n = idx + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        res = chr(ord("A") + rem) + res
    return res


def _build_minimal_xlsx(sheets: list[tuple[str, list[list[str]]]]) -> bytes:
    """Build minimal valid XLSX bytes with inline string cells."""
    buf = io.BytesIO()
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f"{overrides}"
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    wb_sheets = "".join(
        f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (name, _) in enumerate(sheets, start=1)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{wb_sheets}</sheets>"
        "</workbook>"
    )
    wb_rels_items = "".join(
        f'<Relationship Id="rId{i}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{wb_rels_items}"
        "</Relationships>"
    )
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        for s_idx, (_, rows) in enumerate(sheets, start=1):
            row_xml: list[str] = []
            for r_idx, row in enumerate(rows, start=1):
                cells_xml = "".join(
                    f'<c r="{_col_letters(c_idx)}{r_idx}" t="inlineStr"><is><t>{val}</t></is></c>'
                    for c_idx, val in enumerate(row)
                )
                row_xml.append(f'<row r="{r_idx}">{cells_xml}</row>')
            sheet_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<sheetData>{''.join(row_xml)}</sheetData>"
                "</worksheet>"
            )
            zf.writestr(f"xl/worksheets/sheet{s_idx}.xml", sheet_xml)
    return buf.getvalue()


def _build_minimal_pptx(slides: list[tuple[str, str]]) -> bytes:
    """Build minimal valid PPTX bytes with body text and optional speaker notes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            "</Types>",
        )
        for idx, (body, note) in enumerate(slides, start=1):
            slide_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                f"<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{body}</a:t></a:r></a:p>"
                "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
            )
            zf.writestr(f"ppt/slides/slide{idx}.xml", slide_xml)
            if note:
                rels_xml = (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" '
                    f'Target="../notesSlides/notesSlide{idx}.xml"/>'
                    "</Relationships>"
                )
                notes_xml = (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    f"<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{note}</a:t></a:r></a:p>"
                    "</p:txBody></p:sp></p:spTree></p:cSld></p:notes>"
                )
                zf.writestr(f"ppt/slides/_rels/slide{idx}.xml.rels", rels_xml)
                zf.writestr(f"ppt/notesSlides/notesSlide{idx}.xml", notes_xml)
    return buf.getvalue()


def _build_zip_bytes(entries: dict[str, bytes | None]) -> bytes:
    """Build ZIP bytes; value None creates an explicit directory entry."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, payload in entries.items():
            if payload is None:
                dir_path = path if path.endswith("/") else f"{path}/"
                zf.writestr(dir_path, b"")
            else:
                zf.writestr(path, payload)
    return buf.getvalue()


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    parsed = urlsplit(url)
    path_and_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3.0)
    try:
        conn.request(method, path_and_query, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, hdrs, body
    finally:
        conn.close()


@pytest.fixture
def preview_server():
    srv = LocalPreviewServer()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture(autouse=True)
def _restore_theme():
    yield
    apply_theme("midnight_blue")


# ======================================================================
# 1. LocalPreviewServer tests
# ======================================================================


def test_preview_server_serves_html_with_hardening_headers(preview_server: LocalPreviewServer):
    html_text = "<!DOCTYPE html><html><body><h1>Xin chào</h1></body></html>"
    url = preview_server.register_html(html_text)
    parsed = urlsplit(url)

    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == preview_server.port
    assert re.fullmatch(r"/[0-9a-f]{16}", parsed.path)
    assert "token=" in parsed.query

    status, headers, body = _http_request(url, method="GET")
    assert status == 200
    assert body == html_text.encode("utf-8")
    assert headers["cache-control"] == "no-store, private"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["accept-ranges"] == "bytes"
    assert headers["content-type"].startswith("text/html")
    assert (
        headers["content-security-policy"]
        == "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
    )


def test_preview_server_head_and_byte_ranges_for_pdf(preview_server: LocalPreviewServer):
    payload = b"0123456789"
    url = preview_server.register_bytes(payload, "application/pdf")

    status, headers, body = _http_request(url, method="HEAD")
    assert status == 200
    assert body == b""
    assert headers["content-length"] == "10"
    assert headers["accept-ranges"] == "bytes"
    assert headers["content-type"] == "application/pdf"
    assert "content-security-policy" not in headers

    status, headers, body = _http_request(url, method="GET", headers={"Range": "bytes=0-4"})
    assert status == 206
    assert body == b"01234"
    assert headers["content-range"] == "bytes 0-4/10"
    assert headers["content-length"] == "5"
    assert headers["accept-ranges"] == "bytes"

    status, headers, body = _http_request(url, method="HEAD", headers={"Range": "bytes=2-5"})
    assert status == 206
    assert body == b""
    assert headers["content-range"] == "bytes 2-5/10"
    assert headers["content-length"] == "4"

    status, headers, body = _http_request(url, method="GET", headers={"Range": "bytes=-3"})
    assert status == 206
    assert body == b"789"
    assert headers["content-range"] == "bytes 7-9/10"

    status, headers, body = _http_request(url, method="GET", headers={"Range": "bytes=6-"})
    assert status == 206
    assert body == b"6789"
    assert headers["content-range"] == "bytes 6-9/10"

    status, headers, body = _http_request(url, method="GET", headers={"Range": "bytes=7-99"})
    assert status == 206
    assert body == b"789"
    assert headers["content-range"] == "bytes 7-9/10"

    for invalid in (
        "bytes=99-100",
        "bytes=5-2",
        "bytes=-0",
        "bytes=abc-def",
        "bytes=-",
        "bytes=0-1,2-3",
        "items=0-2",
    ):
        status, headers, _ = _http_request(url, method="GET", headers={"Range": invalid})
        assert status == 416, invalid
        assert headers["content-range"] == "bytes */10", invalid


def test_preview_server_auth_and_item_id_validation(preview_server: LocalPreviewServer):
    url = preview_server.register_bytes(b"secret", "text/plain")
    parsed = urlsplit(url)
    base = f"http://127.0.0.1:{parsed.port}"
    token = parsed.query.split("=", 1)[1]

    # Wrong, missing, or duplicated token -> 403
    for query in ("?token=wrongtoken", "", f"?token={token}&token={token}"):
        status, _, body = _http_request(f"{base}{parsed.path}{query}")
        assert status == 403
        assert body == b""

    # Invalid item_id format -> 404
    for bad_path in ("/", "/not-hex", "/1234", "/../etc/passwd", "/gggggggggggggggg"):
        status, _, _ = _http_request(f"{base}{bad_path}?token={token}")
        assert status == 404, bad_path

    # Valid hex item_id that does not exist -> 404
    status, _, _ = _http_request(f"{base}/0000000000000000?token={token}")
    assert status == 404


def test_preview_server_eviction_and_bounds_validation():
    with pytest.raises(ValueError):
        LocalPreviewServer(max_items=0)
    with pytest.raises(ValueError):
        LocalPreviewServer(max_items=-1)
    with pytest.raises(ValueError):
        LocalPreviewServer(max_total_bytes=0)
    with pytest.raises(ValueError):
        LocalPreviewServer(max_total_bytes=-5)

    srv_items = LocalPreviewServer(max_items=3, max_total_bytes=1024)
    try:
        u1 = srv_items.register_bytes(b"1", "text/plain")
        u2 = srv_items.register_bytes(b"2", "text/plain")
        u3 = srv_items.register_bytes(b"3", "text/plain")
        # Touch u1 so u2 becomes the LRU candidate
        assert _http_request(u1)[0] == 200
        u4 = srv_items.register_bytes(b"4", "text/plain")
        assert _http_request(u2)[0] == 404
        assert _http_request(u1)[0] == 200
        assert _http_request(u3)[0] == 200
        assert _http_request(u4)[0] == 200
        with pytest.raises(ValueError):
            srv_items.register_bytes(b"hi", "text/plain\r\nX-Injected: 1")
    finally:
        srv_items.stop()

    srv_bytes = LocalPreviewServer(max_items=10, max_total_bytes=10)
    try:
        a = srv_bytes.register_bytes(b"12345", "application/octet-stream")
        b = srv_bytes.register_bytes(b"67890", "application/octet-stream")
        c = srv_bytes.register_bytes(b"abcde", "application/octet-stream")
        assert _http_request(a)[0] == 404
        assert _http_request(b)[0] == 200
        assert _http_request(c)[0] == 200

        exact_cap = srv_bytes.register_bytes(b"0123456789", "application/octet-stream")
        assert _http_request(b)[0] == 404
        assert _http_request(c)[0] == 404
        assert _http_request(exact_cap)[0] == 200

        with pytest.raises(ValueError):
            srv_bytes.register_bytes(b"0123456789X", "application/octet-stream")
    finally:
        srv_bytes.stop()


def test_preview_server_stop_idempotent_and_broken_pipe_safe():
    srv = LocalPreviewServer()
    url = srv.register_html("<p>ok</p>")
    port = srv.port
    assert port > 0
    assert _http_request(url)[0] == 200

    # Abruptly drop socket mid-exchange; server remains healthy
    parsed = urlsplit(url)
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
        sock.sendall(f"GET {parsed.path}?{parsed.query} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
    assert _http_request(url)[0] == 200

    srv.stop()
    srv.stop()
    assert srv.port == 0

    url2 = srv.register_html("<p>restarted</p>")
    try:
        assert srv.port > 0
        assert _http_request(url2)[0] == 200
    finally:
        srv.stop()


def test_preview_server_has_active_url_csp_variants_empty_range_and_concurrency():
    srv = LocalPreviewServer(max_items=2, max_total_bytes=4096)
    try:
        # Empty payload & single-byte 0-0 range
        empty_url = srv.register_bytes(b"", "application/octet-stream")
        assert srv.has_active_url(empty_url) is True
        assert _http_request(empty_url)[0] == 200
        assert _http_request(empty_url, headers={"Range": "bytes=0-0"})[0] == 416
        status_e, hdrs_e, _ = _http_request(empty_url, headers={"Range": "bytes=0-"})
        assert status_e == 416
        assert hdrs_e["content-range"] == "bytes */0"

        one_byte_url = srv.register_bytes(b"Z", "text/plain")
        st1, hdrs1, body1 = _http_request(one_byte_url, headers={"Range": "bytes=0-0"})
        assert st1 == 206
        assert body1 == b"Z"
        assert hdrs1["content-range"] == "bytes 0-0/1"

        # Non-ASCII digits rejected in Range (both latin-1 HTTP and unit parser check)
        from core.courses.local_preview_server import _parse_byte_range

        assert _http_request(one_byte_url, headers={"Range": "bytes=\u00b2-"})[0] == 416
        assert _parse_byte_range("bytes=\u0660-\u0660", 10) is None

        # Strict CSP enforced on XHTML and SVG too
        svg_url = srv.register_bytes(b"<svg></svg>", "image/svg+xml")
        st_svg, hdrs_svg, _ = _http_request(svg_url)
        assert st_svg == 200
        assert "default-src 'none'" in hdrs_svg["content-security-policy"]
        # empty_url was evicted because max_items=2
        assert srv.has_active_url(empty_url) is False

        xhtml_url = srv.register_bytes(b"<html/>", "application/xhtml+xml; charset=utf-8")
        st_xh, hdrs_xh, _ = _http_request(xhtml_url)
        assert st_xh == 200
        assert "default-src 'none'" in hdrs_xh["content-security-policy"]

        # has_active_url rejects invalid/mismatched URLs
        assert srv.has_active_url("") is False
        assert srv.has_active_url("http://127.0.0.1:99999/0000000000000000?token=x") is False
        assert srv.has_active_url(xhtml_url + "wrong") is False

        # Content-Type validation: control chars and non-latin-1 rejected
        with pytest.raises(ValueError):
            srv.register_bytes(b"a", "text/plain\x00")
        with pytest.raises(ValueError):
            srv.register_bytes(b"a", "text/plain; charset=\u2603")

        # Dead-thread socket cleanup before restart (real thread exited without stop())
        real_srv = srv._server
        real_thread = srv._thread
        assert real_srv is not None and real_thread is not None
        real_srv.shutdown()
        real_thread.join(timeout=2.0)
        assert not real_thread.is_alive()
        restarted_url = srv.register_bytes(b"ok", "text/plain")
        assert srv._server is not real_srv
        assert srv.has_active_url(restarted_url) is True
        assert _http_request(restarted_url)[0] == 200

        # Concurrent register_bytes & stop stress test
        errors: list[Exception] = []

        def _worker():
            try:
                for i in range(10):
                    srv.register_bytes(f"p{i}".encode(), "text/plain")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(3)]
        for t in threads:
            t.start()
        srv.stop()
        for t in threads:
            t.join(timeout=3.0)
        assert not errors
    finally:
        srv.stop()
    assert srv.has_active_url(restarted_url) is False


# ======================================================================
# 2. DocumentReaderView tests
# ======================================================================


def test_document_reader_tier_a_webview_mounts_html_and_pdf(
    preview_server: LocalPreviewServer,
):
    docx_bytes = _build_minimal_docx([("Tiêu đề 1", "Heading1"), ("Nội dung mẫu", None)])
    captured_urls: list[str] = []

    def _wv_builder(url: str) -> ft.Control:
        captured_urls.append(url)
        return ft.Container(key="fake-wv", content=ft.Text(f"WV:{url}"))

    reader_docx = DocumentReaderView(
        file_name="lesson.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=Mock(),
        webview_builder=_wv_builder,
    )
    assert len(captured_urls) == 1
    status, headers, body = _http_request(captured_urls[0])
    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert b"N\xe1\xbb\x99i dung m\xe1\xba\xabu" in body
    assert any(getattr(c, "key", None) == "fake-wv" for c in _walk_controls(reader_docx))

    pdf_bytes = b"%PDF-1.4\n%fake\n"
    reader_pdf = DocumentReaderView(
        file_name="slides.pdf",
        data=pdf_bytes,
        kind="pdf",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=Mock(),
        webview_builder=_wv_builder,
    )
    assert len(captured_urls) == 2
    status, headers, body = _http_request(captured_urls[1])
    assert status == 200
    assert headers["content-type"] == "application/pdf"
    assert body == pdf_bytes
    assert any(getattr(c, "key", None) == "fake-wv" for c in _walk_controls(reader_pdf))


def test_document_reader_image_pdf_fallback_and_header_callbacks(
    preview_server: LocalPreviewServer,
):
    png_bytes = b"\x89PNG\r\n\x1a\nfake"
    on_close = Mock()
    on_save = Mock()
    on_ext = Mock()

    img_reader = DocumentReaderView(
        file_name="diagram.png",
        data=png_bytes,
        kind="image",
        preview_server=preview_server,
        on_close=on_close,
        on_save=on_save,
        on_open_external=on_ext,
    )
    assert img_reader._night_btn.visible is False
    viewers = [c for c in _walk_controls(img_reader) if isinstance(c, ft.InteractiveViewer)]
    images = [c for c in _walk_controls(img_reader) if isinstance(c, ft.Image)]
    assert len(viewers) == 1
    assert viewers[0].min_scale == 0.5
    assert viewers[0].max_scale == 5.0
    assert len(images) == 1
    assert images[0].src == png_bytes

    img_reader._back_btn.on_click(None)
    img_reader._save_btn.on_click(None)
    img_reader._ext_btn.on_click(None)
    on_close.assert_called_once()
    on_save.assert_called_once_with("diagram.png", png_bytes)
    on_ext.assert_called_once_with("diagram.png", png_bytes)

    pdf_ext = Mock()
    pdf_reader = DocumentReaderView(
        file_name="handout.pdf",
        data=b"%PDF-1.4\n",
        kind="pdf",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=pdf_ext,
        webview_builder=lambda _u: None,
    )
    assert pdf_reader._night_btn.visible is True
    pdf_btns = [
        c
        for c in _walk_controls(pdf_reader)
        if isinstance(c, (ft.Button, ft.OutlinedButton)) and c.content == "Mở bằng ứng dụng PDF"
    ]
    assert len(pdf_btns) == 1
    pdf_btns[0].on_click(None)
    pdf_ext.assert_called_once_with("handout.pdf", b"%PDF-1.4\n")


def test_document_reader_docx_outline_dark_mode_and_conversion_cache(
    preview_server: LocalPreviewServer,
    monkeypatch: pytest.MonkeyPatch,
):
    import gui.components.courses.document_reader_view as drv_mod

    docx_bytes = _build_minimal_docx(
        [
            ("Chương 1", "Heading1"),
            ("Đoạn mở đầu", None),
            ("Mục 1.1", "Heading2"),
            ("Chi tiết", None),
        ]
    )
    real_convert = drv_mod.convert_document_bytes
    calls: list[bool] = []

    def _spy_convert(file_name: str, data: bytes, kind: Any, *, dark_mode: bool = False):
        calls.append(dark_mode)
        return real_convert(file_name, data, kind, dark_mode=dark_mode)

    monkeypatch.setattr(drv_mod, "convert_document_bytes", _spy_convert)

    reader = DocumentReaderView(
        file_name="chapter1.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=Mock(),
    )
    assert calls == [False]
    assert reader._night_btn.visible is True

    chips = [c for c in _walk_controls(reader) if isinstance(c, ft.Chip)]
    chip_labels = [c.label.value for c in chips if isinstance(c.label, ft.Text)]
    assert "Chương 1" in chip_labels
    assert "Mục 1.1" in chip_labels
    assert "Mục lục:" in _all_texts(reader)

    md_controls = [c for c in _walk_controls(reader) if isinstance(c, ft.Markdown)]
    assert md_controls
    assert all(m.selectable is True for m in md_controls)
    assert all(m.extension_set == ft.MarkdownExtensionSet.GITHUB_WEB for m in md_controls)

    # ScrollKey anchors present and clicking outline chip calls scroll_to cleanly
    list_views = [c for c in _walk_controls(reader) if isinstance(c, ft.ListView)]
    assert len(list_views) == 1
    scroll_calls: list[Any] = []

    async def _fake_scroll_to(**kwargs):
        scroll_calls.append(kwargs)

    list_views[0].scroll_to = _fake_scroll_to  # type: ignore[method-assign]

    async def _trigger_chip():
        chips[0].on_click(None)
        await asyncio.sleep(0)

    asyncio.run(_trigger_chip())
    assert scroll_calls and scroll_calls[0]["scroll_key"] == "sec-1"

    # Toggle dark reader mode -> uses material_graphite colors in Tier B
    reader._toggle_reader_mode()
    assert reader._dark_reader is True
    assert calls == [False, True]
    assert reader._night_btn.icon == ft.Icons.LIGHT_MODE_OUTLINED
    graphite = THEME_PRESETS["material_graphite"]
    surface_containers = [
        c
        for c in _walk_controls(reader)
        if isinstance(c, ft.Container) and c.bgcolor == graphite["surface"]
    ]
    assert surface_containers

    # update_theme preserves _dark_reader and reuses _converted_cache
    apply_theme("steel_light")
    reader.update_theme()
    assert reader.bgcolor == C.BG
    assert reader._dark_reader is True
    assert calls == [False, True]

    # Toggle back to light reuses cache[False] without re-parsing OOXML
    reader._toggle_reader_mode()
    assert reader._dark_reader is False
    assert calls == [False, True]


def test_document_reader_xlsx_uneven_columns_sheet_switching_and_bounds(
    preview_server: LocalPreviewServer,
    monkeypatch: pytest.MonkeyPatch,
):
    import gui.components.courses.document_reader_view as drv_mod

    wide_header = [f"H{i}" for i in range(45)]
    wide_rows = [wide_header] + [[f"R{r}C{c}" for c in range(45)] for r in range(130)]
    xlsx_bytes = _build_minimal_xlsx(
        [
            ("SheetMot", [["OnlyCol1"], ["A", "B", "C", "D"]]),
            ("SheetRong", []),
            ("SheetLon", wide_rows),
        ]
    )
    real_convert = drv_mod.convert_document_bytes
    calls: list[bool] = []

    def _spy_convert(file_name: str, data: bytes, kind: Any, *, dark_mode: bool = False):
        calls.append(dark_mode)
        return real_convert(file_name, data, kind, dark_mode=dark_mode)

    monkeypatch.setattr(drv_mod, "convert_document_bytes", _spy_convert)

    reader = DocumentReaderView(
        file_name="grades.xlsx",
        data=xlsx_bytes,
        kind="xlsx",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=Mock(),
    )
    assert calls == [False]
    tables = [c for c in _walk_controls(reader) if isinstance(c, ft.DataTable)]
    assert len(tables) == 1
    assert len(tables[0].columns) == 4
    assert len(tables[0].rows) == 1
    assert len(tables[0].rows[0].cells) == 4

    # Sheet 2 is empty -> no zero-column DataTable crash
    chips = [c for c in _walk_controls(reader) if isinstance(c, ft.Chip)]
    assert len(chips) == 3
    chips[1].on_select(None)
    assert [c for c in _walk_controls(reader) if isinstance(c, ft.DataTable)] == []
    assert any("trống" in t.lower() for t in _all_texts(reader))
    assert calls == [False]

    # Sheet 3 exceeds 40 cols and 120 rows -> bounded cleanly with hint
    reader._select_sheet(2)
    tables3 = [c for c in _walk_controls(reader) if isinstance(c, ft.DataTable)]
    assert len(tables3) == 1
    assert len(tables3[0].columns) == 40
    assert len(tables3[0].rows) == 119
    assert all(len(r.cells) == 40 for r in tables3[0].rows)
    assert any("40" in t and "120" in t for t in _all_texts(reader))
    assert calls == [False]

    # CSV single-sheet Tier B fallback when webview_builder returns None
    csv_reader = DocumentReaderView(
        file_name="scores.csv",
        data=b"Name,Score\nAn,9.5\n",
        kind="csv",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=Mock(),
        webview_builder=lambda _u: None,
    )
    csv_tables = [c for c in _walk_controls(csv_reader) if isinstance(c, ft.DataTable)]
    assert len(csv_tables) == 1
    assert len(csv_tables[0].columns) == 2
    assert [c for c in _walk_controls(csv_reader) if isinstance(c, ft.Chip)] == []


def test_document_reader_pptx_text_and_unsupported_fallback(
    preview_server: LocalPreviewServer,
):
    pptx_bytes = _build_minimal_pptx([("Khái niệm OOP", "Nhấn mạnh tính đóng gói")])
    pptx_reader = DocumentReaderView(
        file_name="slides.pptx",
        data=pptx_bytes,
        kind="pptx",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=Mock(),
    )
    texts = _all_texts(pptx_reader)
    assert any("Slide 1" in t for t in texts)
    assert any("Khái niệm OOP" in t for t in texts)
    assert any("Nhấn mạnh tính đóng gói" in t for t in texts)

    py_reader = DocumentReaderView(
        file_name="main.py",
        data=b"print('hello')\n",
        kind="text",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=Mock(),
    )
    assert py_reader._night_btn.visible is False
    assert [c for c in _walk_controls(py_reader) if isinstance(c, ft.Markdown)] == []
    mono_texts = [
        c for c in _walk_controls(py_reader) if isinstance(c, ft.Text) and c.selectable and "print" in (c.value or "")
    ]
    assert len(mono_texts) == 1
    assert "monospace" in (mono_texts[0].font_family or "").lower()

    md_reader = DocumentReaderView(
        file_name="README.md",
        data=b"# Title\n\nBody",
        kind="text",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=Mock(),
    )
    assert any(
        isinstance(c, ft.Markdown) and c.extension_set == ft.MarkdownExtensionSet.GITHUB_WEB
        for c in _walk_controls(md_reader)
    )

    on_ext = Mock()
    unsup_reader = DocumentReaderView(
        file_name="binary.bin",
        data=b"\x00\x01\x02",
        kind="unsupported",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=on_ext,
    )
    fallback_btns = [
        c
        for c in _walk_controls(unsup_reader)
        if isinstance(c, (ft.Button, ft.OutlinedButton)) and c.content == "Lưu & Mở bằng ứng dụng hệ thống"
    ]
    assert len(fallback_btns) == 1
    fallback_btns[0].on_click(None)
    on_ext.assert_called_once_with("binary.bin", b"\x00\x01\x02")


def test_document_reader_tier_b_pdf_lazy_server_oversized_and_url_revalidation():
    # Tier B PDF without webview_builder must NOT start LocalPreviewServer
    srv = LocalPreviewServer(max_items=2, max_total_bytes=64)
    try:
        pdf_tier_b = DocumentReaderView(
            file_name="notes.pdf",
            data=b"%PDF-1.4\nhello",
            kind="pdf",
            preview_server=srv,
            on_close=Mock(),
            on_save=Mock(),
            on_open_external=Mock(),
            webview_builder=None,
        )
        assert srv.port == 0
        assert pdf_tier_b._pdf_url is None

        # Oversized PDF (> max_total_bytes) gracefully degrades to Tier B card
        oversized_pdf = DocumentReaderView(
            file_name="huge.pdf",
            data=b"%PDF-" + b"x" * 100,
            kind="pdf",
            preview_server=srv,
            on_close=Mock(),
            on_save=Mock(),
            on_open_external=Mock(),
            webview_builder=lambda u: ft.Text(u),
        )
        assert any(
            isinstance(c, (ft.Button, ft.OutlinedButton)) and c.content == "Mở bằng ứng dụng PDF"
            for c in _walk_controls(oversized_pdf)
        )

        # Oversized DOCX HTML (> max_total_bytes) gracefully falls back to Tier B Markdown
        docx_bytes = _build_minimal_docx([("Tiêu đề", "Heading1"), ("Đoạn ngắn", None)])
        oversized_docx = DocumentReaderView(
            file_name="huge.docx",
            data=docx_bytes,
            kind="docx",
            preview_server=srv,
            on_close=Mock(),
            on_save=Mock(),
            on_open_external=Mock(),
            webview_builder=lambda u: ft.Text(u),
        )
        assert any(isinstance(c, ft.Markdown) for c in _walk_controls(oversized_docx))
    finally:
        srv.stop()

    # Evicted or stopped URL re-registers cleanly on rebuild / update_theme
    big_srv = LocalPreviewServer(max_items=1, max_total_bytes=1024 * 1024)
    try:
        built_urls: list[str] = []

        def _wv(url: str) -> ft.Control:
            built_urls.append(url)
            return ft.Container(key=f"wv-{len(built_urls)}", content=ft.Text(url))

        reader = DocumentReaderView(
            file_name="small.pdf",
            data=b"%PDF-1.4\n",
            kind="pdf",
            preview_server=big_srv,
            on_close=Mock(),
            on_save=Mock(),
            on_open_external=Mock(),
            webview_builder=_wv,
        )
        assert len(built_urls) == 1
        first_url = built_urls[0]
        assert big_srv.has_active_url(first_url) is True

        # update_theme on active PDF Tier A skips rebuilding WebView while URL is active
        reader.update_theme()
        assert len(built_urls) == 1

        # Evict first_url, then update_theme re-registers cleanly
        big_srv.register_bytes(b"other", "text/plain")
        assert big_srv.has_active_url(first_url) is False
        reader.update_theme()
        assert len(built_urls) == 2
        assert built_urls[1] != first_url
        assert big_srv.has_active_url(built_urls[1]) is True
    finally:
        big_srv.stop()


def test_document_reader_dark_contrast_outline_recovery_image_and_sheet_clamp(
    preview_server: LocalPreviewServer,
):
    from gui.components.courses.document_reader_view import _split_markdown_by_outline
    from core.courses.document_converter import DocumentOutlineItem

    # _split_markdown_by_outline scans forward past a mismatched/truncated heading
    outline = [
        DocumentOutlineItem(anchor_id="sec-1", level=1, title="First"),
        DocumentOutlineItem(anchor_id="sec-2", level=2, title="Missing Heading"),
        DocumentOutlineItem(anchor_id="sec-3", level=2, title="Third"),
    ]
    sections = _split_markdown_by_outline("# First\n\nBody 1\n\n## Third\n\nBody 3", outline)
    anchors = [aid for aid, _ in sections]
    assert anchors == ["sec-1", "sec-3"]

    # Light app theme + _dark_reader=True applies material_graphite canvas & Markdown decorations
    apply_theme("steel_light")
    graphite = THEME_PRESETS["material_graphite"]
    docx_bytes = _build_minimal_docx([("Đầu mục", "Heading1"), ("Nội dung", None)])
    reader = DocumentReaderView(
        file_name="dark_test.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=Mock(),
    )
    reader._toggle_reader_mode()
    assert reader._body_host.bgcolor == graphite["bg"]
    md = next(c for c in _walk_controls(reader) if isinstance(c, ft.Markdown))
    assert md.md_style_sheet is not None
    assert md.md_style_sheet.codeblock_decoration is not None
    assert md.md_style_sheet.codeblock_decoration.bgcolor == graphite["surface"]
    assert md.md_style_sheet.blockquote_decoration is not None
    assert md.md_style_sheet.a_text_style is not None
    assert md.md_style_sheet.a_text_style.color == graphite["accent"]

    # Multi-sheet XLSX dark reader styles sheet chips with dark palette + clamps out-of-bounds index
    xlsx_bytes = _build_minimal_xlsx([("S1", [["A"], ["1"]]), ("S2", [["B"], ["2"]])])
    xlsx_reader = DocumentReaderView(
        file_name="multi.xlsx",
        data=xlsx_bytes,
        kind="xlsx",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=Mock(),
    )
    xlsx_reader._toggle_reader_mode()
    xlsx_reader._select_sheet(99)
    assert xlsx_reader._sheet_index == 1
    assert xlsx_reader._body_host.bgcolor == graphite["bg"]
    sheet_chips = [c for c in _walk_controls(xlsx_reader) if isinstance(c, ft.Chip)]
    assert len(sheet_chips) == 2
    assert sheet_chips[0].bgcolor == graphite["surface"]
    assert sheet_chips[1].selected_color == graphite["accent"]

    # PPTX dark reader uses material_graphite canvas & accent
    pptx_reader = DocumentReaderView(
        file_name="deck.pptx",
        data=_build_minimal_pptx([("Slide text", "Note")]),
        kind="pptx",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=Mock(),
    )
    pptx_reader._toggle_reader_mode()
    assert pptx_reader._body_host.bgcolor == graphite["bg"]
    assert any(
        isinstance(c, ft.Text) and c.value == "Slide 1" and c.color == graphite["accent"]
        for c in _walk_controls(pptx_reader)
    )

    # Corrupt image provides error_content fallback; empty image falls back cleanly
    img_reader = DocumentReaderView(
        file_name="bad.png",
        data=b"not-a-valid-png",
        kind="image",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=Mock(),
    )
    img_ctrl = next(c for c in _walk_controls(img_reader) if isinstance(c, ft.Image))
    assert img_ctrl.error_content is not None

    empty_img_reader = DocumentReaderView(
        file_name="empty.png",
        data=b"",
        kind="image",
        preview_server=preview_server,
        on_close=Mock(),
        on_save=Mock(),
        on_open_external=Mock(),
    )
    assert [c for c in _walk_controls(empty_img_reader) if isinstance(c, ft.Image)] == []
    assert any("Tệp trống" in t for t in _all_texts(empty_img_reader))


# ======================================================================
# 3. ArchiveExplorerView tests
# ======================================================================


def test_archive_explorer_zip_drilldown_breadcrumbs_handle_back_and_open_entry():
    zip_bytes = _build_zip_bytes(
        {
            "docs/sub/note.txt": b"hello world",
            "readme.md": b"# Hi",
            "empty_dir/": None,
        }
    )
    on_close = Mock()
    on_open_entry = Mock()
    on_save_raw = Mock()

    explorer = ArchiveExplorerView(
        archive_name="bundle.zip",
        archive_bytes=zip_bytes,
        archive_service=ArchiveService(),
        on_close=on_close,
        on_open_entry=on_open_entry,
        on_save_raw=on_save_raw,
    )
    assert any("2 tệp" in t for t in _all_texts(explorer))
    assert explorer.handle_back() is False

    # Drill into 'docs' then 'sub'
    def _row_named(label: str) -> ft.Container:
        for c in _walk_controls(explorer):
            if isinstance(c, ft.Container) and c.on_click is not None:
                if any(label == t for t in _all_texts(c)):
                    return c
        raise AssertionError(f"Row {label!r} not found")

    _row_named("docs").on_click(None)
    assert "sub" in _all_texts(explorer)
    _row_named("sub").on_click(None)
    assert "note.txt" in _all_texts(explorer)

    # Open extracted file
    _row_named("note.txt").on_click(None)
    on_open_entry.assert_called_once_with("note.txt", b"hello world", "text")

    # Back pops stack cleanly until root
    assert explorer.handle_back() is True
    assert "sub" in _all_texts(explorer)
    assert explorer.handle_back() is True
    assert "readme.md" in _all_texts(explorer)
    assert explorer.handle_back() is False

    # Empty folder state
    _row_named("empty_dir").on_click(None)
    assert any("Thư mục trống" in t for t in _all_texts(explorer))

    # Jump to root via breadcrumb 'Gốc'
    goc_btns = [
        c
        for c in _walk_controls(explorer)
        if isinstance(c, ft.TextButton) and c.content == "Gốc"
    ]
    assert len(goc_btns) == 1
    goc_btns[0].on_click(None)
    assert explorer.handle_back() is False

    # Header buttons
    explorer._save_btn.on_click(None)
    on_save_raw.assert_called_once_with("bundle.zip", zip_bytes)
    explorer._back_btn.on_click(None)
    on_close.assert_called_once()


def test_archive_explorer_extraction_errors_surface_inline_banner():
    zip_bytes = _build_zip_bytes({"secret.txt": b"data"})
    svc = ArchiveService()
    svc.extract_entry_bytes = Mock(side_effect=ArchiveSecurityError("ZipSlip blocked"))  # type: ignore[method-assign]
    on_open_entry = Mock()

    explorer = ArchiveExplorerView(
        archive_name="bad.zip",
        archive_bytes=zip_bytes,
        archive_service=svc,
        on_close=Mock(),
        on_open_entry=on_open_entry,
        on_save_raw=Mock(),
    )
    row = next(
        c
        for c in _walk_controls(explorer)
        if isinstance(c, ft.Container) and c.on_click is not None and "secret.txt" in _all_texts(c)
    )
    row.on_click(None)
    on_open_entry.assert_not_called()
    assert any("ZipSlip blocked" in t for t in _all_texts(explorer))

    svc.extract_entry_bytes = Mock(side_effect=FileNotFoundError("missing"))  # type: ignore[method-assign]
    row2 = next(
        c
        for c in _walk_controls(explorer)
        if isinstance(c, ft.Container) and c.on_click is not None and "secret.txt" in _all_texts(c)
    )
    row2.on_click(None)
    assert any("missing" in t for t in _all_texts(explorer))

    svc.extract_entry_bytes = Mock(side_effect=RuntimeError("unrar missing"))  # type: ignore[method-assign]
    row3 = next(
        c
        for c in _walk_controls(explorer)
        if isinstance(c, ft.Container) and c.on_click is not None and "secret.txt" in _all_texts(c)
    )
    row3.on_click(None)
    assert any("unrar missing" in t for t in _all_texts(explorer))

    # Subsequent valid extraction clears inline error banner cleanly
    svc.extract_entry_bytes = Mock(return_value=b"data")  # type: ignore[method-assign]
    row4 = next(
        c
        for c in _walk_controls(explorer)
        if isinstance(c, ft.Container) and c.on_click is not None and "secret.txt" in _all_texts(c)
    )
    row4.on_click(None)
    on_open_entry.assert_called_once_with("secret.txt", b"data", "text")
    assert explorer._banner_host.visible is False


def test_archive_explorer_unsupported_rar_inspect_security_error_and_theme():
    on_save_raw = Mock()
    rar_svc = ArchiveService(rar_tool_finder=lambda: None)
    rar_explorer = ArchiveExplorerView(
        archive_name="homework.rar",
        archive_bytes=b"Rar!\x1a\x07\x00",
        archive_service=rar_svc,
        on_close=Mock(),
        on_open_entry=Mock(),
        on_save_raw=on_save_raw,
    )
    zarchiver_btns = [
        c
        for c in _walk_controls(rar_explorer)
        if isinstance(c, ft.Button) and c.content == "Lưu & Mở bằng ZArchiver / Files"
    ]
    assert len(zarchiver_btns) == 1
    zarchiver_btns[0].on_click(None)
    on_save_raw.assert_called_once_with("homework.rar", b"Rar!\x1a\x07\x00")

    bad_svc = ArchiveService()
    bad_svc.inspect_bytes = Mock(side_effect=ArchiveSecurityError("Bomb limit exceeded"))  # type: ignore[method-assign]
    bad_explorer = ArchiveExplorerView(
        archive_name="bomb.zip",
        archive_bytes=b"PK\x03\x04",
        archive_service=bad_svc,
        on_close=Mock(),
        on_open_entry=Mock(),
        on_save_raw=Mock(),
    )
    assert any("Bomb limit exceeded" in t for t in _all_texts(bad_explorer))

    apply_theme("steel_light")
    rar_explorer.update_theme()
    assert rar_explorer.bgcolor == C.BG


def test_archive_explorer_jump_to_bounds_error_icon_theme_and_targz():
    # Build valid .tar.gz archive
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tf:
        payload = b"hello tar"
        info = tarfile.TarInfo("folder/hello.txt")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    opened: list[tuple[str, bytes, str]] = []
    explorer = ArchiveExplorerView(
        archive_name="bundle.tar.gz",
        archive_bytes=tar_buf.getvalue(),
        archive_service=ArchiveService(),
        on_close=Mock(),
        on_open_entry=lambda name, b, k: opened.append((name, b, k)),
        on_save_raw=Mock(),
    )
    folder_row = next(
        c
        for c in _walk_controls(explorer)
        if isinstance(c, ft.Container) and c.on_click is not None and "folder" in _all_texts(c)
    )
    assert folder_row.ink is True
    folder_row.on_click(None)

    file_row = next(
        c
        for c in _walk_controls(explorer)
        if isinstance(c, ft.Container) and c.on_click is not None and "hello.txt" in _all_texts(c)
    )
    file_row.on_click(None)
    assert opened == [("hello.txt", b"hello tar", "text")]

    # _jump_to out-of-bounds is a no-op; valid index jumps cleanly
    explorer._jump_to(99)
    assert len(explorer._stack) == 2
    explorer._jump_to(0)
    assert len(explorer._stack) == 1

    # Error banner uses ERROR_OUTLINE_ROUNDED icon and survives update_theme()
    explorer._svc.extract_entry_bytes = Mock(side_effect=OSError("io fail"))  # type: ignore[method-assign]
    folder_row2 = next(
        c
        for c in _walk_controls(explorer)
        if isinstance(c, ft.Container) and c.on_click is not None and "folder" in _all_texts(c)
    )
    folder_row2.on_click(None)
    file_row2 = next(
        c
        for c in _walk_controls(explorer)
        if isinstance(c, ft.Container) and c.on_click is not None and "hello.txt" in _all_texts(c)
    )
    file_row2.on_click(None)
    icons = [c.icon for c in _walk_controls(explorer._banner_host) if isinstance(c, ft.Icon)]
    assert ft.Icons.ERROR_OUTLINE_ROUNDED in icons

    apply_theme("steel_light")
    explorer.update_theme()
    assert explorer.bgcolor == C.BG
    icons_after = [c.icon for c in _walk_controls(explorer._banner_host) if isinstance(c, ft.Icon)]
    assert ft.Icons.ERROR_OUTLINE_ROUNDED in icons_after
