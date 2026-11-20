"""Tier 1 E2E Tests — Offline Word/PPTX Engines & DocumentReaderView Tier A/B UX (F6–F8)."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
import sys
from typing import Any
from urllib.parse import parse_qs, urlsplit

import flet as ft
import pytest

from core.courses.document_converter import convert_document_bytes
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.pdf_renderer import PdfDocumentSession
from gui.components.courses.document_reader_view import DocumentReaderView
from tests.e2e_courses.builders import (
    build_docx_bytes,
    build_ole2_bytes,
    build_pdf_bytes,
    build_pptx_bytes,
)
from tests.e2e_courses.conftest import (
    FakePage,
    assert_no_fixed_width_overflow,
    assert_valid_flet_mro,
    bind_control_page,
    collect_texts,
    http_request,
    walk_controls,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OFFICE_VIEWER_DIR = REPO_ROOT / "src" / "assets" / "office_viewer"


def _extract_src_param(viewer_url: str) -> str:
    parsed = urlsplit(viewer_url)
    params = parse_qs(parsed.query)
    values = params.get("src") or []
    return values[0] if values else ""


# ---------------------------------------------------------------------------
# F6: High-Fidelity Offline Word (.docx/.doc) Engine (>=5 tests)
# ---------------------------------------------------------------------------


def test_f6_offline_word_bundle_assets_exist_and_within_budget() -> None:
    """F6: src/assets/office_viewer/ bundles docx_host shell + offline jszip/docx-preview under 5 MB."""
    assert OFFICE_VIEWER_DIR.is_dir()
    files = [p for p in OFFICE_VIEWER_DIR.rglob("*") if p.is_file() and p.name != ".gitkeep"]
    names_lower = {p.name.lower() for p in files}

    assert "docx_host.html" in names_lower
    assert "docx_host.js" in names_lower
    assert any("jszip" in n and n.endswith(".js") for n in names_lower)
    assert any("docx" in n and "preview" in n and n.endswith(".js") for n in names_lower)

    total_bytes = sum(p.stat().st_size for p in files)
    assert 0 < total_bytes < 5 * 1024 * 1024

    for asset_name in ("docx_host.html", "docx_host.js"):
        txt = (OFFICE_VIEWER_DIR / asset_name).read_text(encoding="utf-8")
        assert txt.strip()
        for cdn in ("unpkg.com", "jsdelivr.net", "cdnjs.cloudflare.com", "https://cdn.", "http://cdn."):
            assert cdn not in txt.lower()


def test_f6_prepare_office_viewer_docx_registers_fetchable_host_and_blob(
    preview_server: LocalPreviewServer,
) -> None:
    """F6: prepare_office_viewer registers /viewer/<token>/docx_host.html + fetchable .docx payload."""
    svc = importlib.import_module("core.courses.office_viewer_service")
    docx_bytes = build_docx_bytes(
        headings=[(1, "Chương 1: Nhập môn"), (2, "Mục 1.1: Cài đặt")],
        paragraphs=["Nội dung bài giảng UTH E-learning."],
        tables=[[["Mã MH", "Tên môn"], ["IT001", "Python"]]],
        omml_equations=["a^2 + b^2 = c^2"],
        include_image=True,
        include_header_footer=True,
    )

    desc = svc.prepare_office_viewer(docx_bytes, "lecture.docx", preview_server)
    assert desc is not None
    assert desc.format == "docx"
    assert "/viewer/" in desc.viewer_url
    assert "docx_host.html" in desc.viewer_url

    st_host, hdrs_host, body_host = http_request(desc.viewer_url)
    assert st_host == 200
    assert "script-src 'self'" in hdrs_host.get("content-security-policy", "")
    assert b"<html" in body_host.lower()

    src_url = getattr(desc, "blob_url", "") or _extract_src_param(desc.viewer_url)
    assert src_url.startswith("http://127.0.0.1:")
    st_blob, _hdrs_blob, body_blob = http_request(src_url)
    assert st_blob == 200
    assert body_blob == docx_bytes


def test_f6_docx_host_supports_omml_responsive_a4_scaling_and_theme_hooks() -> None:
    """F6: docx_host HTML/JS & inline helper support OMML equations, mobile A4 scaling, and dark/light theme."""
    svc = importlib.import_module("core.courses.office_viewer_service")

    host_html = (OFFICE_VIEWER_DIR / "docx_host.html").read_text(encoding="utf-8")
    host_js = (OFFICE_VIEWER_DIR / "docx_host.js").read_text(encoding="utf-8")
    combined = (host_html + "\n" + host_js).lower()

    assert "viewport" in host_html.lower()
    assert "theme" in combined
    assert "omath" in combined or "math" in combined
    assert "scale" in combined or "zoom" in combined

    docx_bytes = build_docx_bytes(paragraphs=["Inline fallback test"], omml_equations=["x + y = z"])
    if hasattr(svc, "build_inline_viewer_html"):
        inline_html = svc.build_inline_viewer_html(docx_bytes, "lecture.docx", theme="dark")
        assert isinstance(inline_html, str) and "<html" in inline_html.lower()
        assert "unpkg.com" not in inline_html.lower()


def test_f6_convert_document_bytes_extracts_headings_tables_omml_and_images() -> None:
    """F6: convert_document_bytes extracts headings, tables, OMML text, and images for search/outline/Tier-B."""
    docx_bytes = build_docx_bytes(
        headings=[(1, "Giải tích 1"), (2, "Đạo hàm")],
        paragraphs=["Công thức cơ bản trong bài học."],
        omml_equations=["E = mc^2"],
        tables=[[["H1", "H2"], ["v1", "v2"]]],
        include_image=True,
        page_break_between=True,
    )
    conv = convert_document_bytes("giai_tich.docx", docx_bytes, "docx")

    assert len(conv.outline) == 2
    assert [item.title for item in conv.outline] == ["Giải tích 1", "Đạo hàm"]
    assert any(b.kind == "table" and b.rows for b in conv.docx_blocks)
    assert any(b.kind == "image" and b.image_bytes for b in conv.docx_blocks)
    assert "E = mc^2" in conv.markdown_text or any(
        "E = mc^2" in (b.text or "") for b in conv.docx_blocks
    )


def test_f6_legacy_ole2_doc_handled_gracefully(preview_server: LocalPreviewServer) -> None:
    """F6: Legacy OLE2 .doc payloads degrade gracefully without crashing Tier A or Tier B."""
    svc = importlib.import_module("core.courses.office_viewer_service")
    ole2_doc = build_ole2_bytes(
        {
            "WordDocument": b"UTH legacy document content\r\n",
            "1Table": b"\x00" * 128,
        }
    )

    desc = svc.prepare_office_viewer(ole2_doc, "old_lecture.doc", preview_server)
    if desc is not None:
        assert desc.format == "legacy_doc"

    conv = convert_document_bytes("old_lecture.doc", ole2_doc, "docx")
    assert conv.kind == "docx"
    assert isinstance(conv.markdown_text, str)


def test_f6_office_viewer_service_is_pure_core_without_gui_imports() -> None:
    """F6: core.courses.office_viewer_service obeys Clean Architecture (zero gui/flet imports)."""
    svc = importlib.import_module("core.courses.office_viewer_service")
    src_path = Path(svc.__file__)
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert "gui" not in imported_roots
    assert "flet" not in imported_roots


# ---------------------------------------------------------------------------
# F7: High-Fidelity Offline PowerPoint (.pptx/.ppt) Engine (>=5 tests)
# ---------------------------------------------------------------------------


def test_f7_offline_pptx_bundle_assets_exist_and_self_contained() -> None:
    """F7: src/assets/office_viewer/ bundles pptx_host shell and offline slide engine assets."""
    assert OFFICE_VIEWER_DIR.is_dir()
    files = [p for p in OFFICE_VIEWER_DIR.rglob("*") if p.is_file() and p.name != ".gitkeep"]
    names_lower = {p.name.lower() for p in files}

    assert "pptx_host.html" in names_lower
    assert "pptx_host.js" in names_lower
    assert any(
        ("pptx" in n and n not in {"pptx_host.js", "pptx_host.css"} and n.endswith(".js"))
        for n in names_lower
    )

    for asset_name in ("pptx_host.html", "pptx_host.js"):
        txt = (OFFICE_VIEWER_DIR / asset_name).read_text(encoding="utf-8")
        assert txt.strip()
        for cdn in ("unpkg.com", "jsdelivr.net", "cdnjs.cloudflare.com", "https://cdn.", "http://cdn."):
            assert cdn not in txt.lower()


def test_f7_prepare_office_viewer_pptx_registers_fetchable_viewer_and_blob(
    preview_server: LocalPreviewServer,
) -> None:
    """F7: prepare_office_viewer registers /viewer/<token>/pptx_host.html and serves PPTX bytes."""
    svc = importlib.import_module("core.courses.office_viewer_service")
    pptx_bytes = build_pptx_bytes(
        slides=[("Slide 1: Mở đầu", "Mục tiêu môn học"), ("Slide 2: Thực hành", "Bài tập tuần 1")],
        aspect="16:9",
        include_notes=True,
        include_table=True,
        include_chart=True,
        include_image=True,
    )

    desc = svc.prepare_office_viewer(pptx_bytes, "slides.pptx", preview_server)
    assert desc is not None
    assert desc.format == "pptx"
    assert "/viewer/" in desc.viewer_url
    assert "pptx_host.html" in desc.viewer_url

    st_host, hdrs_host, body_host = http_request(desc.viewer_url)
    assert st_host == 200
    assert "script-src 'self'" in hdrs_host.get("content-security-policy", "")
    assert b"<html" in body_host.lower()

    src_url = getattr(desc, "blob_url", "") or _extract_src_param(desc.viewer_url)
    assert src_url.startswith("http://127.0.0.1:")
    st_blob, _hdrs_blob, body_blob = http_request(src_url)
    assert st_blob == 200
    assert body_blob == pptx_bytes


def test_f7_pptx_host_supports_aspect_fit_navigation_and_notes() -> None:
    """F7: pptx_host shell/JS exposes slide navigation, aspect-ratio fit, notes, and theme controls."""
    host_html = (OFFICE_VIEWER_DIR / "pptx_host.html").read_text(encoding="utf-8")
    host_js = (OFFICE_VIEWER_DIR / "pptx_host.js").read_text(encoding="utf-8")
    combined = (host_html + "\n" + host_js).lower()

    assert "viewport" in host_html.lower()
    assert "note" in combined
    assert "slide" in combined
    assert "theme" in combined
    assert any(k in combined for k in ("nextslide", "gotoslide", "prevslide", "setslide", "slideindex"))


def test_f7_convert_document_bytes_preserves_rich_slides_aspects_tables_and_notes() -> None:
    """F7: convert_document_bytes preserves 4:3 aspect ratio, placed elements, tables, and speaker notes."""
    pptx_bytes = build_pptx_bytes(
        slides=[("Chủ đề A", "Nội dung 4:3"), ("Chủ đề B", "Biểu đồ & bảng")],
        aspect="4:3",
        include_notes=True,
        include_table=True,
        include_chart=True,
        include_image=True,
    )
    conv = convert_document_bytes("deck_4x3.pptx", pptx_bytes, "pptx")

    assert len(conv.slides) == 2
    assert len(conv.rich_slides) == 2
    first = conv.rich_slides[0]
    assert abs(first.aspect_ratio - (4.0 / 3.0)) < 0.05
    assert "Ghi chú giảng viên slide 1" in first.notes_text
    assert first.elements
    assert any("Cột 1" in cell for tbl in first.tables for row in tbl for cell in row)


def test_f7_legacy_ole2_ppt_fallback_and_windows_bridge_guard(
    preview_server: LocalPreviewServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F7: Legacy OLE2 .ppt falls back cleanly; optional windows_office_bridge guards COM on win32."""
    svc = importlib.import_module("core.courses.office_viewer_service")
    ole2_ppt = build_ole2_bytes({"PowerPoint Document": b"Legacy slide text\x00"})

    desc = svc.prepare_office_viewer(ole2_ppt, "legacy.ppt", preview_server)
    if desc is not None:
        assert desc.format == "legacy_ppt"

    conv = convert_document_bytes("legacy.ppt", ole2_ppt, "pptx")
    assert conv.kind == "pptx"
    assert isinstance(conv.markdown_text, str)

    spec = importlib.util.find_spec("core.courses.windows_office_bridge")
    if spec is not None:
        assert spec.origin is not None
        bridge_src = Path(spec.origin).read_text(encoding="utf-8")
        tree = ast.parse(bridge_src)
        com_roots = {"win32com", "pythoncom", "comtypes", "pywintypes"}
        for stmt in tree.body:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    assert alias.name.split(".", 1)[0] not in com_roots
            elif isinstance(stmt, ast.ImportFrom) and stmt.module:
                assert stmt.module.split(".", 1)[0] not in com_roots
        has_win32_guard = any(
            isinstance(node, ast.Compare)
            and any(isinstance(c, ast.Constant) and c.value == "win32" for c in node.comparators)
            for node in ast.walk(tree)
        )
        assert has_win32_guard, "Expected sys.platform == 'win32' guard in windows_office_bridge"

        orig_platform = sys.platform
        monkeypatch.setattr(sys, "platform", "linux")
        try:
            bridge = importlib.import_module("core.courses.windows_office_bridge")
            bridge = importlib.reload(bridge)
            assert bridge is not None
        finally:
            monkeypatch.setattr(sys, "platform", orig_platform)
            if "core.courses.windows_office_bridge" in sys.modules:
                importlib.reload(sys.modules["core.courses.windows_office_bridge"])


# ---------------------------------------------------------------------------
# F8: DocumentReaderView Tier A/B & Mobile Reader UX (>=5 tests)
# ---------------------------------------------------------------------------


def test_f8_docx_and_pptx_mount_tier_a_embedded_webview_host(
    fake_page: FakePage,
    platform_profile: Any,
    preview_server: LocalPreviewServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F8: DocumentReaderView mounts Tier A EmbeddedWebViewHost for .docx and .pptx when available."""
    import flet_webview as fwv

    host_mod = importlib.import_module("gui.components.courses.embedded_webview_host")
    platform_profile("android", page=fake_page)
    bind_control_page(monkeypatch, DocumentReaderView, fake_page)
    fake_page.resize(390, 844)

    for kind, fname, payload, expected_host in (
        ("docx", "lecture.docx", build_docx_bytes(paragraphs=["Tier A DOCX"]), "docx_host.html"),
        ("pptx", "slides.pptx", build_pptx_bytes(slides=[("S1", "Tier A PPTX")]), "pptx_host.html"),
    ):
        built_urls: list[str] = []

        def _builder(url: str) -> Any:
            built_urls.append(url)
            return host_mod.build_embedded_webview_host(url, page=fake_page)

        reader = DocumentReaderView(
            file_name=fname,
            data=payload,
            kind=kind,  # type: ignore[arg-type]
            preview_server=preview_server,
            on_close=lambda: None,
            on_save=lambda _n, _b: None,
            on_open_external=lambda _n, _b: None,
            webview_builder=_builder,
            pane_width=390.0,
        )
        try:
            assert any(expected_host in u for u in built_urls), f"Expected {expected_host} in {built_urls}"
            assert_valid_flet_mro(reader)
            assert_no_fixed_width_overflow(reader, 390.0)
            assert any(isinstance(c, fwv.WebView) for c in walk_controls(reader))
        finally:
            reader.dispose()


def test_f8_docx_and_pptx_fall_back_to_tier_b_when_webview_unavailable(
    preview_server: LocalPreviewServer,
) -> None:
    """F8: DocumentReaderView renders Tier B native fallback cleanly when webview_builder returns None."""
    docx_bytes = build_docx_bytes(
        headings=[(1, "Phần mở đầu")],
        paragraphs=["Nội dung Tier B dự phòng."],
    )
    pptx_bytes = build_pptx_bytes(
        slides=[("Slide dự phòng", "Hiển thị văn bản slide")],
        include_notes=True,
    )

    for kind, fname, payload, expected_text in (
        ("docx", "fallback.docx", docx_bytes, "Nội dung Tier B dự phòng."),
        ("pptx", "fallback.pptx", pptx_bytes, "Hiển thị văn bản slide"),
    ):
        reader = DocumentReaderView(
            file_name=fname,
            data=payload,
            kind=kind,  # type: ignore[arg-type]
            preview_server=preview_server,
            on_close=lambda: None,
            on_save=lambda _n, _b: None,
            on_open_external=lambda _n, _b: None,
            webview_builder=lambda _u: None,
            pane_width=360.0,
        )
        try:
            assert_valid_flet_mro(reader)
            assert_no_fixed_width_overflow(reader, 360.0, allow_horizontal_scroll_descendants=True)
            texts = " ".join(collect_texts(reader))
            assert expected_text in texts
        finally:
            reader.dispose()


def test_f8_tier_a_and_pdf_tier_b_dark_light_toggle(
    fake_page: FakePage,
    platform_profile: Any,
    preview_server: LocalPreviewServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F8: Dark/Light toggle invokes eval_js on Tier-A host and switches PDF Tier-B rendering mode."""
    host_mod = importlib.import_module("gui.components.courses.embedded_webview_host")
    platform_profile("android", page=fake_page)
    bind_control_page(monkeypatch, DocumentReaderView, fake_page)
    fake_page.resize(390, 844)

    js_scripts: list[str] = []
    orig_eval = host_mod.EmbeddedWebViewHost.eval_js

    async def _spy_eval(self: Any, script: str) -> None:
        js_scripts.append(str(script))
        await orig_eval(self, script)

    monkeypatch.setattr(host_mod.EmbeddedWebViewHost, "eval_js", _spy_eval)

    docx_reader = DocumentReaderView(
        file_name="theme.docx",
        data=build_docx_bytes(paragraphs=["Theme test"]),
        kind="docx",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda _n, _b: None,
        on_open_external=lambda _n, _b: None,
        webview_builder=lambda url: host_mod.build_embedded_webview_host(url, page=fake_page),
        pane_width=390.0,
    )
    try:
        assert docx_reader._night_btn.tooltip == "Chế độ đọc tối"
        assert callable(docx_reader._night_btn.on_click)
        docx_reader._night_btn.on_click(None)
        assert docx_reader._night_btn.tooltip == "Chế độ đọc sáng"
        assert js_scripts, "Expected Tier-A dark/light toggle to invoke eval_js"
        docx_reader._night_btn.on_click(None)
        assert docx_reader._night_btn.tooltip == "Chế độ đọc tối"
    finally:
        docx_reader.dispose()

    pdf_bytes = build_pdf_bytes(["PDF Page 1", "PDF Page 2"])
    pdf_session = PdfDocumentSession(pdf_bytes)
    pdf_reader = DocumentReaderView(
        file_name="theme.pdf",
        data=pdf_bytes,
        kind="pdf",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda _n, _b: None,
        on_open_external=lambda _n, _b: None,
        preopened_pdf=pdf_session,
        pane_width=360.0,
    )
    try:
        assert pdf_reader._night_btn.tooltip == "Chế độ đọc tối"
        assert callable(pdf_reader._night_btn.on_click)
        pdf_reader._night_btn.on_click(None)
        assert pdf_reader._night_btn.tooltip == "Chế độ đọc sáng"
        assert_valid_flet_mro(pdf_reader)
    finally:
        pdf_reader.dispose()


def test_f8_mobile_360dp_reader_compact_toolbar_collapsible_search_and_pager(
    preview_server: LocalPreviewServer,
) -> None:
    """F8: 360dp reader uses compact controls, collapsible search, bottom pager, and wrap=True PDF footer."""
    pdf_bytes = build_pdf_bytes([f"Trang PDF số {i}" for i in range(1, 8)])
    pdf_session = PdfDocumentSession(pdf_bytes)
    reader = DocumentReaderView(
        file_name="multipage.pdf",
        data=pdf_bytes,
        kind="pdf",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda _n, _b: None,
        on_open_external=lambda _n, _b: None,
        preopened_pdf=pdf_session,
        pane_width=360.0,
    )
    try:
        assert_valid_flet_mro(reader)
        assert_no_fixed_width_overflow(reader, 360.0)

        # Collapsible search on <600dp: search input is not permanently expanded until toggled
        visible_search_fields = [
            c
            for c in walk_controls(reader)
            if isinstance(c, ft.TextField)
            and "tìm" in str(getattr(c, "hint_text", "") or "").lower()
            and getattr(c, "visible", True) is not False
        ]
        assert not visible_search_fields, "Expected search TextField to be collapsed by default at 360dp"

        # Bottom pager / batch footer uses wrap=True so controls never overflow 360dp
        footer_rows = [
            c
            for c in walk_controls(reader)
            if isinstance(c, ft.Row)
            and any("xem tiếp trang" in txt.lower() for txt in collect_texts(c))
        ]
        assert footer_rows and all(getattr(r, "wrap", False) is True for r in footer_rows)
    finally:
        reader.dispose()


def test_f8_mobile_360dp_pdf_outline_uses_modal_bottom_sheet_not_side_row(
    fake_page: FakePage,
    preview_server: LocalPreviewServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F8: At 360dp, PDF outline opens as modal BottomSheet instead of a 220dp side-by-side Row."""
    bind_control_page(monkeypatch, DocumentReaderView, fake_page)
    fake_page.resize(360, 780)

    pdf_bytes = build_pdf_bytes(
        ["Page 1", "Page 2", "Page 3"],
        outline_titles=[("Chương 1", 0), ("Chương 2", 1), ("Chương 3", 2)],
    )
    pdf_session = PdfDocumentSession(pdf_bytes)
    reader = DocumentReaderView(
        file_name="outlined.pdf",
        data=pdf_bytes,
        kind="pdf",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda _n, _b: None,
        on_open_external=lambda _n, _b: None,
        preopened_pdf=pdf_session,
        pane_width=360.0,
    )
    try:
        outline_btns = [
            c
            for c in walk_controls(reader)
            if isinstance(c, ft.IconButton) and getattr(c, "tooltip", "") == "Mục lục PDF"
        ]
        assert outline_btns, "Expected PDF outline trigger button"
        outline_btns[0].on_click(None)

        # Must NOT mount a fixed 220dp side-by-side outline Container inside reader
        side_220 = [
            c
            for c in walk_controls(reader)
            if isinstance(c, ft.Container) and getattr(c, "width", None) == 220
        ]
        assert not side_220, "Must not render 220dp side-by-side outline panel on 360dp phone"

        sheets = [
            d
            for d in (*fake_page.dialogs, *fake_page.overlay)
            if isinstance(d, ft.BottomSheet)
        ]
        assert sheets, "Expected modal BottomSheet for PDF outline on 360dp phone"
        sheet_texts = " ".join(collect_texts(sheets[-1]))
        assert "Chương 1" in sheet_texts
        assert "Chương 2" in sheet_texts
        assert_valid_flet_mro(reader)
    finally:
        reader.dispose()


def test_f8_mobile_360dp_pdf_pinch_zoom_and_wide_docx_table_horizontal_scroll(
    preview_server: LocalPreviewServer,
) -> None:
    """F8: 360dp PDF pages support InteractiveViewer pinch-to-zoom; wide Tier-B DOCX tables scroll horizontally."""
    pdf_bytes = build_pdf_bytes(["Zoomable page 1"])
    pdf_session = PdfDocumentSession(pdf_bytes)
    pdf_reader = DocumentReaderView(
        file_name="zoom.pdf",
        data=pdf_bytes,
        kind="pdf",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda _n, _b: None,
        on_open_external=lambda _n, _b: None,
        preopened_pdf=pdf_session,
        pane_width=360.0,
    )
    try:
        assert_valid_flet_mro(pdf_reader)
        assert_no_fixed_width_overflow(pdf_reader, 360.0)
        if pdf_session.can_render_images:
            viewers = [c for c in walk_controls(pdf_reader) if isinstance(c, ft.InteractiveViewer)]
            assert viewers, "Expected InteractiveViewer pinch-to-zoom wrapper around PDF page on 360dp"
    finally:
        pdf_reader.dispose()

    wide_table = [
        [f"Cột {i}" for i in range(1, 7)],
        [f"Giá trị {i}" for i in range(1, 7)],
    ]
    docx_bytes = build_docx_bytes(tables=[wide_table])
    docx_reader = DocumentReaderView(
        file_name="wide_table.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda _n, _b: None,
        on_open_external=lambda _n, _b: None,
        webview_builder=lambda _u: None,
        pane_width=360.0,
    )
    try:
        assert_valid_flet_mro(docx_reader)
        assert_no_fixed_width_overflow(
            docx_reader,
            360.0,
            allow_horizontal_scroll_descendants=True,
        )
        h_scroll_rows = [
            c
            for c in walk_controls(docx_reader)
            if isinstance(c, ft.Row)
            and getattr(c, "scroll", None) is not None
            and any("Cột 6" in txt for txt in collect_texts(c))
        ]
        assert h_scroll_rows, "Expected horizontal Row(scroll=...) wrapper around wide 6-column DOCX table at 360dp"
    finally:
        docx_reader.dispose()
