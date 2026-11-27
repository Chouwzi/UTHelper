"""Regression tests for PDF Knockout-Group blank pages & mobile PPTX rendering/scroll speed."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import flet as ft
import pytest

from core.courses.archive_service import ArchiveService
from core.courses.cache_store import CourseCacheStore
from core.courses.catalog_service import CourseCatalogService
from core.courses.document_converter import convert_document_bytes
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.models import CourseAttachment, CourseModuleItem, CourseSummary
from core.courses.preview_cache import PreviewFileCache
from gui.controllers.courses_coordinator import CoursesCoordinator
from tests.e2e_courses.builders import build_pdf_bytes

REPO_ROOT = Path(__file__).resolve().parents[1]


def _build_legacy_style_pptx_bytes() -> bytes:
    """Build a PPTX mimicking legacy .ppt->.pptx slides (Wingdings 'n', 85% lnSpc, graphicFrame)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/ppt/presentation.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            '<Override PartName="/ppt/slides/slide1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            '<Override PartName="/ppt/slides/slide2.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="ppt/presentation.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "ppt/presentation.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:sldIdLst>'
            '<p:sldId id="256" r:id="rId1"/>'
            '<p:sldId id="257" r:id="rId2"/>'
            "</p:sldIdLst>"
            '<p:sldSz cx="9144000" cy="6858000" type="screen4x3"/>'
            "</p:presentation>",
        )
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
            'Target="slides/slide1.xml"/>'
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
            'Target="slides/slide2.xml"/>'
            "</Relationships>",
        )
        slide1_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            "<p:cSld><p:spTree>"
            "<p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>"
            "<p:grpSpPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"0\" cy=\"0\"/>"
            "<a:chOff x=\"0\" y=\"0\"/><a:chExt cx=\"0\" cy=\"0\"/></a:xfrm></p:grpSpPr>"
            "<p:sp>"
            "<p:nvSpPr><p:cNvPr id=\"2\" name=\"Title 1\"/><p:cNvSpPr/><p:nvPr><p:ph type=\"title\"/></p:nvPr></p:nvSpPr>"
            "<p:spPr><a:xfrm><a:off x=\"914400\" y=\"457200\"/><a:ext cx=\"7315200\" cy=\"914400\"/></a:xfrm></p:spPr>"
            "<p:txBody><a:bodyPr/><a:lstStyle/>"
            "<a:p><a:r><a:rPr sz=\"3200\" b=\"1\"/><a:t>GIỚI THIỆU CHUNG</a:t></a:r></a:p>"
            "</p:txBody></p:sp>"
            "<p:sp>"
            "<p:nvSpPr><p:cNvPr id=\"3\" name=\"Body 2\"/><p:cNvSpPr/><p:nvPr><p:ph idx=\"1\"/></p:nvPr></p:nvSpPr>"
            "<p:spPr><a:xfrm><a:off x=\"685800\" y=\"1524000\"/><a:ext cx=\"7772400\" cy=\"4572000\"/></a:xfrm></p:spPr>"
            "<p:txBody><a:bodyPr><a:normAutofit/></a:bodyPr><a:lstStyle/>"
            "<a:p>"
            "<a:pPr lvl=\"0\"><a:lnSpc><a:spcPct val=\"85000\"/></a:lnSpc>"
            "<a:buFont typeface=\"Wingdings\"/><a:buChar char=\"n\"/></a:pPr>"
            "<a:r><a:rPr sz=\"2800\" b=\"1\"/><a:t>Tại sao phải Quản lý rủi ro?</a:t></a:r>"
            "</a:p>"
            "<a:p>"
            "<a:pPr lvl=\"1\"><a:lnSpc><a:spcPct val=\"85000\"/></a:lnSpc>"
            "<a:buFont typeface=\"Wingdings\"/><a:buChar char=\"\uf06e\"/></a:pPr>"
            "<a:r><a:rPr sz=\"2400\"/><a:t>Trong mọi hoạt động sản xuất và kinh doanh luôn tồn tại những yếu tố ngẫu nhiên, bất định và dự án phần mềm cũng không ngoại lệ.</a:t></a:r>"
            "</a:p>"
            "<a:p>"
            "<a:pPr lvl=\"1\"><a:lnSpc><a:spcPct val=\"90000\"/></a:lnSpc>"
            "<a:buFont typeface=\"Symbol\"/><a:buChar char=\"\uf0b7\"/></a:pPr>"
            "<a:r><a:rPr sz=\"2400\"/><a:t>Tiên liệu và hạn chế các yếu tố bất định trong thực tế triển khai.</a:t></a:r>"
            "</a:p>"
            "</p:txBody></p:sp>"
            "<p:graphicFrame>"
            "<p:nvGraphicFramePr><p:cNvPr id=\"4\" name=\"Legacy OLE 3\"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>"
            "<p:xfrm><a:off x=\"7620000\" y=\"100000\"/><a:ext cx=\"1371600\" cy=\"1143000\"/></p:xfrm>"
            "<a:graphic><a:graphicData uri=\"http://schemas.openxmlformats.org/presentationml/2006/ole\">"
            "<p:oleObj name=\"Object\"/>"
            "</a:graphicData></a:graphic>"
            "</p:graphicFrame>"
            "</p:spTree></p:cSld></p:sld>"
        )
        zf.writestr("ppt/slides/slide1.xml", slide1_xml)
        slide2_xml = slide1_xml.replace("GIỚI THIỆU CHUNG", "SLIDE TIẾP THEO")
        zf.writestr("ppt/slides/slide2.xml", slide2_xml)
    return buf.getvalue()


def test_windows_desktop_coordinator_routes_pdf_and_office_pdf_to_pdfjs_viewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows Desktop must use offline pdf_host.html (not Edge raw PDF plugin) so Knockout-Group PDFs render."""
    pdf_bytes = build_pdf_bytes(["Knockout transparency slide 1"])
    pptx_bytes = _build_legacy_style_pptx_bytes()
    store = CourseCacheStore(cache_dir=tmp_path, namespace="win_pdf")
    pcache = PreviewFileCache(tmp_path / "pcache")
    srv = LocalPreviewServer()
    cat = CourseCatalogService(cache_store=store, preview_cache=pcache)

    payload_map = {
        "https://courses.ut.edu.vn/pluginfile.php/1/ch5.pdf": pdf_bytes,
        "https://courses.ut.edu.vn/pluginfile.php/1/deck.pptx": pptx_bytes,
    }
    cat.fetch_attachment_bytes = lambda _src, url, **_kw: payload_map[url]  # type: ignore[method-assign]

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
    monkeypatch.setattr(
        "gui.controllers.courses_coordinator.is_embedded_webview_supported",
        lambda _p: True,
    )
    monkeypatch.setattr(
        "gui.controllers.courses_coordinator.render_office_pdf",
        lambda _raw, _name: pdf_bytes,
    )

    mounted_urls: list[str] = []

    class _FakeHost:
        def __init__(self, url: str) -> None:
            self._url = url
            self._ctrl = ft.Container(key="wv-host", expand=True)

        def control(self) -> ft.Control:
            return self._ctrl

        def sync_bounds(self, *_a, **_kw) -> None:
            return None

        def suspend(self) -> None:
            return None

        def resume(self) -> None:
            return None

        def dispose(self) -> None:
            return None

    coord = CoursesCoordinator(
        page=page,  # type: ignore[arg-type]
        catalog_service=cat,
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    monkeypatch.setattr(
        coord,
        "build_webview_host",
        lambda url="", **_kw: (mounted_urls.append(url), _FakeHost(url))[1],
    )

    try:
        course = CourseSummary(id=1, source="courses", fullname="C1", shortname="C1", display_name="C1")
        mod = CourseModuleItem(cmid=1, instance_id=1, modname="resource", name="Slides", section_id=1)

        # 1. Standalone PDF on Windows Desktop -> pdf_host.html + Night Mode enabled
        att_pdf = CourseAttachment(
            file_name="Chương 5. Quản Lý Chất Lượng.pdf",
            file_url="https://courses.ut.edu.vn/pluginfile.php/1/ch5.pdf",
            preview_kind="pdf",
            file_size=len(pdf_bytes),
        )
        asyncio.run(coord.open_attachment_async(course, mod, att_pdf))
        assert mounted_urls, "Expected embedded WebView to mount PDF"
        assert "pdf_host.html" in mounted_urls[-1]
        reader_pdf = coord.courses_view._overlays[-1]
        assert reader_pdf._night_btn.visible is True

        # 2. Archive-extracted PDF entry on Windows Desktop -> pdf_host.html
        coord._open_entry_reader("nested_slide.pdf", pdf_bytes, "pdf")
        assert "pdf_host.html" in mounted_urls[-1]

        # 3. Office->PDF converted PPTX on Windows Desktop -> pdf_host.html (safe for LibreOffice PDFs)
        att_pptx = CourseAttachment(
            file_name="deck.pptx",
            file_url="https://courses.ut.edu.vn/pluginfile.php/1/deck.pptx",
            preview_kind="pptx",
            file_size=len(pptx_bytes),
        )
        asyncio.run(coord.open_attachment_async(course, mod, att_pptx))
        assert "pdf_host.html" in mounted_urls[-1]
    finally:
        coord.close()


def test_tier_b_pptx_converter_maps_wingdings_and_pua_bullets_to_unicode() -> None:
    """Tier B PPTX converter must map Wingdings 'n'/PUA bullets to geometric Unicode, not raw 'n'."""
    pptx_bytes = _build_legacy_style_pptx_bytes()
    conv = convert_document_bytes("7_QL_Rui_ro.pptx", pptx_bytes, "pptx")
    assert conv.rich_slides
    slide1 = conv.rich_slides[0]
    bullets = [
        p.bullet
        for el in slide1.elements
        if el.kind == "text"
        for p in el.paragraphs
        if p.bullet
    ]
    assert bullets == ["■", "■", "•"]
    assert "n<" not in conv.html_document
    assert "\uf06e" not in conv.html_document
    assert "\uf0b7" not in conv.html_document


def test_pptx_host_and_worker_fix_wingdings_line_height_graphic_frame_and_prewarm() -> None:
    """Tier A pptx_host + pptx_worker must fix Wingdings 'n', tight line-height, graphicFrame, and prewarm."""
    pptx_bytes = _build_legacy_style_pptx_bytes()
    worker_js = (REPO_ROOT / "src" / "assets" / "office_viewer" / "pptx_worker.js").read_text(encoding="utf-8")
    host_js = (REPO_ROOT / "src" / "assets" / "office_viewer" / "pptx_host.js").read_text(encoding="utf-8")

    # Worker must use OffscreenCanvas measureText and background slide prewarming
    assert "OffscreenCanvas" in worker_js
    assert "measureText" in worker_js
    assert "prewarmed" in worker_js and "prewarmed" in host_js

    # Run real Chromium end-to-end against LocalPreviewServer + pptx_host.html
    playwright = pytest.importorskip("playwright.sync_api")
    from core.courses.office_viewer_service import prepare_office_viewer

    srv = LocalPreviewServer()
    try:
        desc = prepare_office_viewer(pptx_bytes, "7_QL_Rui_ro.pptx", srv)
        assert desc is not None
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True, channel="msedge")
            except Exception:
                try:
                    browser = p.chromium.launch(headless=True, channel="chrome")
                except Exception as exc:
                    pytest.skip(f"Chromium browser channel unavailable: {exc}")
            try:
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.goto(desc.viewer_url, wait_until="networkidle")
                page.wait_for_function(
                    "() => document.documentElement.dataset.ready === 'true'",
                    timeout=10000,
                )
                slide_info = page.evaluate(
                    """() => {
                        const svg = document.querySelector('#slide svg');
                        const fullText = svg ? svg.textContent : '';
                        const paras = [...document.querySelectorAll('#slide [data-pptx-paragraph]')];
                        const bulletSpans = paras.map(p => p.querySelector('span')?.textContent?.trim() || '');
                        const lineHeights = paras.map(p => parseFloat(p.style.lineHeight || '0'));
                        const fallbackFrames = document.querySelectorAll('#slide [data-pptx-fallback="graphicFrame"]');
                        return {
                            fullText,
                            bulletSpans,
                            lineHeights,
                            fallbackFrameCount: fallbackFrames.length,
                        };
                    }"""
                )
                assert "graphicFrame" not in slide_info["fullText"]
                assert slide_info["fallbackFrameCount"] == 0
                assert slide_info["bulletSpans"] == ["GIỚI THIỆU CHUNG", "■", "■", "•"]
                assert all(lh >= 1.24 for lh in slide_info["lineHeights"])
            finally:
                browser.close()
    finally:
        srv.stop()


def test_pdf_host_prioritizes_visible_page_and_cancels_scrolled_past_renders() -> None:
    """pdf_host.js must cancel off-window in-flight renderTasks, pick real visible page, and avoid {alpha: false}."""
    pdf_host_js = (REPO_ROOT / "src" / "assets" / "pdf_viewer" / "pdf_host.js").read_text(encoding="utf-8")
    assert "alpha: false" not in pdf_host_js
    assert "cancelDistantInflightRenders" in pdf_host_js
    assert "pickBestVisiblePage" in pdf_host_js
    assert "disableAutoFetch: false" in pdf_host_js
    assert "rangeChunkSize: 524288" in pdf_host_js
