"""Tier 2 E2E Boundary & Corner Case Tests (F1-F11, >= 5 tests per feature)."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
import time
import tomllib
from types import SimpleNamespace
from typing import Any, Callable
from xml.etree import ElementTree
import zipfile

import flet as ft
import pytest

from core.courses import pdf_renderer
from core.courses.archive_service import ArchiveService
from core.courses.document_converter import convert_document_bytes
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.pdf_renderer import PdfDocumentSession
from core.courses.preview_cache import PreviewFileCache
from gui.app_controller import AppController
from gui.components.courses import embedded_webview_host as ewh_mod
from gui.components.courses._shared import build_offline_banner
from gui.components.courses.activity_views import QuizActivityView
from gui.components.courses.adaptive_nav import AdaptiveNavShell
from gui.components.courses.archive_explorer_view import ArchiveExplorerView
from gui.components.courses.course_detail_pane import CourseDetailPane
from gui.components.courses.course_list_pane import CourseListPane
from gui.components.courses.courses_tab_view import CoursesTabView
from gui.components.courses.document_reader_view import DocumentReaderView
from gui.components.courses.embedded_webview_host import (
    EmbeddedWebViewHost,
    build_embedded_webview_host,
)
from gui.components.courses.webview_pane import CoursesWebViewPane
from gui.controllers.courses_coordinator import CoursesCoordinator
from gui.core.theme import C, apply_theme
from gui.view_models.courses_state import CoursesState, compute_courses_layout_mode
from tests.e2e_courses.builders import (
    FakeActivityService,
    FakeCatalogService,
    build_docx_bytes,
    build_ole2_bytes,
    build_pdf_bytes,
    build_pptx_bytes,
    build_zip_bytes,
    make_course,
    make_grade,
    make_module,
    make_recent,
    make_section,
    tiny_png_bytes,
)
from tests.e2e_courses.conftest import (
    FakePage,
    FakeView,
    assert_no_fixed_width_overflow,
    assert_valid_flet_mro,
    collect_texts,
    http_request,
    walk_controls,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0, label: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"Timed out after {timeout:.1f}s waiting for {label}")


def _find_clickable_with_text(root: ft.BaseControl, needle: str) -> ft.Container:
    for ctrl in walk_controls(root):
        if isinstance(ctrl, ft.Container) and callable(getattr(ctrl, "on_click", None)):
            if any(needle in t for t in collect_texts(ctrl)):
                return ctrl
    raise AssertionError(f"Clickable container with text {needle!r} not found")


def _find_button_with_text(root: ft.BaseControl, needle: str) -> ft.BaseControl:
    btn_types = (ft.Button, ft.OutlinedButton, ft.TextButton, ft.FilledButton)
    for ctrl in walk_controls(root):
        if isinstance(ctrl, btn_types):
            content = getattr(ctrl, "content", None)
            text_val = content if isinstance(content, str) else " ".join(collect_texts(ctrl))
            if needle in text_val and callable(getattr(ctrl, "on_click", None)):
                return ctrl
    raise AssertionError(f"Button with text {needle!r} not found")


def _build_zero_slide_pptx_bytes() -> bytes:
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
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="ppt/presentation.xml"/></Relationships>',
        )
        zf.writestr(
            "ppt/presentation.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            "<p:sldIdLst/>"
            '<p:sldSz cx="12192000" cy="6858000" type="screen16x9"/>'
            "</p:presentation>",
        )
    return buf.getvalue()


def _build_nested_outline_pdf_bytes() -> bytes:
    """Build a 3-page PDF with nested parent -> child /Outlines bookmarks."""
    import pypdf

    base_pdf = build_pdf_bytes(["Trang mot", "Trang hai", "Trang ba"])
    reader = pypdf.PdfReader(io.BytesIO(base_pdf))
    writer = pypdf.PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    parent = writer.add_outline_item(title="Phan 1", page_number=0)
    writer.add_outline_item(title="Muc 1.1", page_number=1, parent=parent)
    writer.add_outline_item(title="Phan 2", page_number=2)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# F1 Boundaries: Packaging & PDF/Imaging Deps (5 tests)
# ---------------------------------------------------------------------------


def test_f1_boundary_corrupted_truncated_pdf_unsupported_no_crash() -> None:
    sess = PdfDocumentSession(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Broken")
    try:
        assert sess.supported is False
        assert sess.page_count == 0
        assert sess.error_message
        assert sess.render_page_png(0) is None
        assert sess.search("Broken") == []
    finally:
        sess.close()


def test_f1_boundary_empty_zero_byte_pdf() -> None:
    sess = PdfDocumentSession(b"")
    try:
        assert sess.supported is False
        assert sess.can_render_images is False
        assert sess.page_count == 0
        assert sess.outline == []
        assert sess.error_message
    finally:
        sess.close()


def test_f1_boundary_pdf_zero_outlines_vs_multilevel_outlines() -> None:
    no_outline_pdf = build_pdf_bytes(["Trang 1", "Trang 2"], outline_titles=None)
    s0 = PdfDocumentSession(no_outline_pdf)
    try:
        assert s0.supported is True
        assert s0.page_count == 2
        assert s0.outline == []
    finally:
        s0.close()

    nested_pdf = _build_nested_outline_pdf_bytes()
    s1 = PdfDocumentSession(nested_pdf)
    try:
        assert s1.supported is True
        assert s1.page_count == 3
        titles = [e.title for e in s1.outline]
        assert "Phan 1" in titles
        assert "Muc 1.1" in titles
        assert "Phan 2" in titles
        assert all(0 <= e.page_index < 3 for e in s1.outline)
    finally:
        s1.close()


def test_f1_boundary_missing_pypdfium2_and_pil_graceful_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_bytes = build_pdf_bytes(["Noi dung trang 1", "Noi dung trang 2"])
    monkeypatch.setattr(pdf_renderer, "_pdfium", None)
    monkeypatch.setattr(pdf_renderer, "_PilImage", None)
    monkeypatch.setattr(pdf_renderer, "_pymupdf", None)

    sess = PdfDocumentSession(pdf_bytes)
    try:
        assert sess.supported is True
        assert sess.can_render_images is False
        assert sess.page_count == 2
        assert "Noi dung trang 1" in sess.extract_page_text(0)
        assert sess.render_page_png(0) is None
    finally:
        sess.close()

    monkeypatch.setattr(pdf_renderer, "_pypdf", None)
    sess_none = PdfDocumentSession(pdf_bytes)
    try:
        assert sess_none.supported is False
        assert sess_none.error_message
    finally:
        sess_none.close()


def test_f1_boundary_pyproject_store_safe_deps_and_loopback_xml() -> None:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    core_deps = [d.lower() for d in config["project"]["dependencies"]]

    forbidden_core = (
        "pypdfium2",
        "pywin32",
        "pystray",
        "windows-toasts",
        "webview2",
        "winrt",
    )
    for pkg in forbidden_core:
        assert not any(pkg in dep for dep in core_deps), (
            f"Windows/404 package {pkg!r} must not be in [project] dependencies"
        )

    android_cfg = config["tool"]["flet"]["android"]
    assert "flet-webview==0.86.5" in android_cfg["dependencies"]
    assert not any("pypdfium2" in d.lower() for d in android_cfg["dependencies"])
    manifest_app = android_cfg["manifest_application"]
    assert manifest_app.get("allowBackup") == "false"
    assert manifest_app.get("networkSecurityConfig") == "@xml/network_security_config"

    xml_path = (
        REPO_ROOT
        / "extensions/flet_uth_background_sync/flutter/flet_uth_background_sync"
        / "android/src/main/res/xml/network_security_config.xml"
    )
    assert xml_path.is_file()
    root = ElementTree.fromstring(xml_path.read_text(encoding="utf-8"))
    base_cfg = root.find("base-config")
    assert base_cfg is not None
    assert base_cfg.attrib.get("cleartextTrafficPermitted") == "false"
    domains = {d.text.strip() for d in root.findall(".//domain") if d.text}
    assert {"127.0.0.1", "localhost"}.issubset(domains)


# ---------------------------------------------------------------------------
# F2 Boundaries: EmbeddedWebViewHost (5 tests)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "javascript:alert(1)",
        "file:///C:/Windows/win.ini",
        "data:text/html,<script>alert(1)</script>",
        "",
        "   ",
    ],
)
def test_f2_boundary_rejects_unsafe_or_empty_urls(
    bad_url: str, fake_page: FakePage, platform_profile
) -> None:
    platform_profile("android", page=fake_page)
    assert build_embedded_webview_host(bad_url, page=fake_page) is None


def test_f2_boundary_lifecycle_methods_idempotent_after_dispose(
    fake_page: FakePage, platform_profile
) -> None:
    platform_profile("android", page=fake_page)
    host = build_embedded_webview_host("https://courses.ut.edu.vn/", page=fake_page)
    assert host is not None

    host.dispose()
    host.dispose()
    asyncio.run(host.eval_js("console.log('ignored')"))
    host.navigate("https://thnn.ut.edu.vn/")
    host.suspend()
    host.resume()
    host.sync_bounds(0, 0, 360, 640)
    host.go_back()
    host.go_forward()
    host.reload()


def test_f2_boundary_windows_missing_webview2_runtime_returns_none(
    fake_page: FakePage, platform_profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    platform_profile("desktop", page=fake_page, is_windows=True)
    monkeypatch.setattr(ewh_mod, "_is_flet_webview_platform", lambda _p: False)
    monkeypatch.setattr(ewh_mod, "_webview2_runtime_available", lambda: False)
    assert build_embedded_webview_host("https://courses.ut.edu.vn/", page=fake_page) is None


def test_f2_boundary_repeated_sync_bounds_identical_zero_and_negative_dimensions(
    fake_page: FakePage,
) -> None:
    recorded: list[tuple[float, float, float, float, float]] = []

    class _FakeDock:
        def set_bounds(self, left: float, top: float, width: float, height: float, *, bottom_inset: float = 0.0) -> None:
            recorded.append((left, top, width, height, bottom_inset))

        def dispose(self) -> None:
            pass

    host = EmbeddedWebViewHost(
        backend="webview2",
        page=fake_page,
        url="https://courses.ut.edu.vn/",
        dock=_FakeDock(),  # type: ignore[arg-type]
    )
    host.sync_bounds(10, 20, 360, 640)
    host.sync_bounds(10, 20, 360, 640)
    host.sync_bounds(0, 0, 0, 0)
    host.sync_bounds(-10, -20, -100, -50)
    assert len(recorded) == 4


def test_f2_boundary_embedded_host_invokes_on_error_and_stops_after_dispose(
    fake_page: FakePage, platform_profile
) -> None:
    platform_profile("android", page=fake_page)
    errors: list[str] = []
    host = build_embedded_webview_host(
        "https://courses.ut.edu.vn/",
        page=fake_page,
        on_error=errors.append,
    )
    assert host is not None
    host._handle_flet_error(SimpleNamespace(data="ERR_NAME_NOT_RESOLVED"))
    assert errors == ["ERR_NAME_NOT_RESOLVED"]

    host.dispose()
    host._handle_flet_error(SimpleNamespace(data="AFTER_DISPOSE"))
    assert errors == ["ERR_NAME_NOT_RESOLVED"]


# ---------------------------------------------------------------------------
# F3 Boundaries: LocalPreviewServer /viewer/ & Blobs (5 tests)
# ---------------------------------------------------------------------------


def test_f3_boundary_viewer_bad_token_returns_403(preview_server: LocalPreviewServer) -> None:
    base = preview_server.viewer_base_url()
    port = preview_server.port
    status, _hdrs, _body = http_request(f"http://127.0.0.1:{port}/viewer/invalid-token/docx_host.html")
    assert status == 403
    assert base.startswith(f"http://127.0.0.1:{port}/viewer/")


@pytest.mark.parametrize(
    "bad_rel",
    [
        "../../src/main.py",
        "%2e%2e/%2e%2e/pyproject.toml",
        "..%5c..%5cpyproject.toml",
        "sub/..\\..\\secret.js",
        "a%00.js",
    ],
)
def test_f3_boundary_viewer_path_traversal_variants_rejected(
    bad_rel: str, preview_server: LocalPreviewServer
) -> None:
    base = preview_server.viewer_base_url()
    status, _hdrs, _body = http_request(f"{base}{bad_rel}")
    assert status in (403, 404)


def test_f3_boundary_viewer_disallowed_extensions_rejected(tmp_path: Path) -> None:
    (tmp_path / "good.html").write_text("<html><body>OK</body></html>", encoding="utf-8")
    (tmp_path / "bad.py").write_text("print(1)", encoding="utf-8")
    (tmp_path / "bad.exe").write_bytes(b"MZ")
    (tmp_path / "bad.txt").write_text("plain", encoding="utf-8")

    srv = LocalPreviewServer(viewer_assets_dir=tmp_path)
    try:
        base = srv.viewer_base_url()
        st_ok, hdrs_ok, _ = http_request(f"{base}good.html")
        assert st_ok == 200
        assert "script-src 'self'" in hdrs_ok.get("content-security-policy", "")

        for name in ("bad.py", "bad.exe", "bad.txt"):
            st, _hdrs, _body = http_request(f"{base}{name}")
            assert st in (403, 404)
    finally:
        srv.stop()


def test_f3_boundary_http_range_206_and_416_on_docx_pptx_blobs(
    preview_server: LocalPreviewServer,
) -> None:
    docx = build_docx_bytes(paragraphs=["Range test paragraph"])
    url = preview_server.register_bytes(
        docx,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    st_206, hdrs_206, body_206 = http_request(url, headers={"Range": "bytes=0-63"})
    assert st_206 == 206
    assert hdrs_206.get("content-range") == f"bytes 0-63/{len(docx)}"
    assert body_206 == docx[:64]

    st_416, hdrs_416, _ = http_request(url, headers={"Range": f"bytes={len(docx) + 500}-"})
    assert st_416 == 416
    assert hdrs_416.get("content-range") == f"bytes */{len(docx)}"

    st_inv, _, _ = http_request(url, headers={"Range": "bytes=20-5"})
    assert st_inv == 416


def test_f3_boundary_oversized_lru_eviction_and_stop_cleanup() -> None:
    srv = LocalPreviewServer(max_items=2, max_total_bytes=1024)
    try:
        with pytest.raises(ValueError):
            srv.register_bytes(b"x" * 2048, "application/octet-stream")

        u1 = srv.register_bytes(b"a" * 400, "application/octet-stream")
        u2 = srv.register_bytes(b"b" * 400, "application/octet-stream")
        u3 = srv.register_bytes(b"c" * 400, "application/octet-stream")

        assert srv.has_active_url(u1) is False
        assert srv.has_active_url(u2) is True
        assert srv.has_active_url(u3) is True
        st1, _, _ = http_request(u1)
        assert st1 == 404
    finally:
        srv.stop()

    assert srv.port == 0
    assert srv.has_active_url(u3) is False
    srv.stop()  # idempotent


# ---------------------------------------------------------------------------
# F4 Boundaries: CoursesWebViewPane (5 tests)
# ---------------------------------------------------------------------------


def test_f4_boundary_external_open_hidden_on_loopback_shown_on_moodle(
    fake_page: FakePage,
) -> None:
    for local_url in (
        "http://127.0.0.1:8765/viewer/tok/docx_host.html",
        "http://localhost:8000/viewer/tok/pptx_host.html",
    ):
        pane = CoursesWebViewPane(
            page=fake_page,
            source="courses",
            title="Local Viewer",
            url=local_url,
            on_back=lambda: None,
            on_open_external=lambda _u: None,
            embedded_webview_factory=lambda _u: ft.Container(expand=True),
            pane_width=360.0,
        )
        assert pane._ext_btn.visible is False
        assert pane._more_menu_item_ext.visible is False

    remote = CoursesWebViewPane(
        page=fake_page,
        source="courses",
        title="Moodle Quiz",
        url="https://courses.ut.edu.vn/mod/quiz/view.php?id=99",
        on_back=lambda: None,
        on_open_external=lambda _u: None,
        embedded_webview_factory=lambda _u: ft.Container(expand=True),
        pane_width=360.0,
    )
    assert remote._ext_btn.visible is True
    assert remote._more_menu_item_ext.visible is True


def test_f4_boundary_header_layout_across_360_599_600dp(fake_page: FakePage) -> None:
    pane = CoursesWebViewPane(
        page=fake_page,
        source="courses",
        title="Moodle Page",
        url="https://courses.ut.edu.vn/mod/page/view.php?id=10",
        on_back=lambda: None,
        on_open_external=lambda _u: None,
        embedded_webview_factory=lambda _u: ft.Container(expand=True),
        pane_width=360.0,
    )
    assert pane._mobile_nav_bar.visible is True
    assert_no_fixed_width_overflow(pane, 360)

    pane.sync_bounds(0, 0, 599, 780)
    assert pane._mobile_nav_bar.visible is True
    assert_no_fixed_width_overflow(pane, 599)

    pane.sync_bounds(0, 0, 600, 780)
    assert pane._mobile_nav_bar.visible is False
    assert_no_fixed_width_overflow(pane, 600)


def test_f4_boundary_navigation_when_engine_unavailable_no_crash(
    fake_page: FakePage, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ewh_mod, "_is_flet_webview_platform", lambda _p: False)
    monkeypatch.setattr(ewh_mod, "_webview2_runtime_available", lambda: False)
    pane = CoursesWebViewPane(
        page=fake_page,
        source="courses",
        title="Fallback Page",
        url="https://example.org/activity",
        on_back=lambda: None,
        on_open_external=lambda _u: None,
        embedded_webview_factory=lambda _u: None,
        pane_width=360.0,
    )
    pane._on_webview_back(None)
    pane._on_webview_forward(None)
    pane._on_webview_reload(None)
    pane.sync_bounds(0, 0, 360, 640)
    pane.suspend()
    pane.resume()
    assert pane.handle_back() is False
    pane.dispose()


def test_f4_boundary_rapid_dispose_during_pending_load(
    fake_page: FakePage, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ewh_mod, "_is_flet_webview_platform", lambda _p: False)
    monkeypatch.setattr(ewh_mod, "_webview2_runtime_available", lambda: False)
    pane = CoursesWebViewPane(
        page=fake_page,
        source="courses",
        title="Rapid Dispose",
        url="https://example.org/slow",
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    pane.dispose()
    pane.dispose()
    pane.resume()
    pane.sync_bounds(0, 0, 360, 640)
    assert pane._disposed is True


def test_f4_boundary_update_theme_across_light_and_dark_presets(fake_page: FakePage) -> None:
    pane = CoursesWebViewPane(
        page=fake_page,
        source="courses",
        title="Theme Test",
        url="https://courses.ut.edu.vn/course/view.php?id=1",
        on_back=lambda: None,
        on_open_external=lambda _u: None,
        embedded_webview_factory=lambda _u: ft.Container(expand=True),
    )
    for preset in ("snow_light", "material_graphite"):
        apply_theme(preset)
        pane.update_theme()
        assert pane.bgcolor == C.BG
        assert pane._title_text.color == C.TEXT_PRIMARY
        assert_valid_flet_mro(pane)


# ---------------------------------------------------------------------------
# F5 Boundaries: Cross-Platform External Open / Save (5 tests)
# ---------------------------------------------------------------------------


def _make_coordinator(tmp_path: Path, fake_page: FakePage, preview_server: LocalPreviewServer) -> CoursesCoordinator:
    return CoursesCoordinator(
        page=fake_page,
        catalog_service=FakeCatalogService(),  # type: ignore[arg-type]
        preview_cache=PreviewFileCache(tmp_path),
        archive_service=ArchiveService(),
        preview_server=preview_server,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )


@pytest.mark.parametrize(
    "bad_name",
    ["setup.exe", "run.bat", "script.ps1", "auto.cmd", "payload.vbs", "pkg.msi", "tricky.exe "],
)
def test_f5_boundary_blocked_dangerous_extensions_never_launched_externally(
    bad_name: str,
    tmp_path: Path,
    fake_page: FakePage,
    preview_server: LocalPreviewServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[str] = []
    saved: list[str] = []

    async def _fake_launch(_self: Any, url: str) -> None:
        launched.append(url)

    async def _fake_save(_self: Any, *, file_name: str = "", **_kw: Any) -> str:
        saved.append(file_name)
        return f"saved/{file_name}"

    monkeypatch.setattr(ft.UrlLauncher, "launch_url", _fake_launch)
    monkeypatch.setattr(ft.FilePicker, "save_file", _fake_save)

    coord = _make_coordinator(tmp_path, fake_page, preview_server)
    asyncio.run(coord._open_external_async(bad_name, b"MZ"))
    assert launched == []
    assert len(saved) == 1
    assert saved[0].strip() == bad_name.strip()


@pytest.mark.parametrize("raw_name", ["../../evil.docx", "", "..\\..\\..\\boot.pdf"])
def test_f5_boundary_empty_or_path_traversal_filename_sanitized(
    raw_name: str,
    tmp_path: Path,
    fake_page: FakePage,
    preview_server: LocalPreviewServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[str] = []

    async def _fake_launch(_self: Any, url: str) -> None:
        launched.append(url)

    monkeypatch.setattr(ft.UrlLauncher, "launch_url", _fake_launch)

    coord = _make_coordinator(tmp_path, fake_page, preview_server)
    asyncio.run(coord._open_external_async(raw_name, b"hello"))
    assert len(launched) == 1
    ext_dir = (tmp_path / "external").resolve()
    written = list(ext_dir.iterdir())
    assert len(written) == 1
    assert written[0].resolve().is_relative_to(ext_dir)


def test_f5_boundary_android_file_picker_cancellation_none_handled_cleanly(
    tmp_path: Path,
    fake_page: FakePage,
    preview_server: LocalPreviewServer,
    platform_profile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform_profile("android", page=fake_page)

    async def _cancel_save(_self: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(ft.FilePicker, "save_file", _cancel_save)
    coord = _make_coordinator(tmp_path, fake_page, preview_server)
    asyncio.run(coord._save_bytes_async("lecture.pdf", b"%PDF-1.4"))


def test_f5_boundary_zero_byte_attachment_handled_cleanly(
    tmp_path: Path,
    fake_page: FakePage,
    preview_server: LocalPreviewServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_launch(_self: Any, _url: str) -> None:
        return None

    async def _fake_save(_self: Any, **_kw: Any) -> str:
        return "ok"

    monkeypatch.setattr(ft.UrlLauncher, "launch_url", _fake_launch)
    monkeypatch.setattr(ft.FilePicker, "save_file", _fake_save)

    coord = _make_coordinator(tmp_path, fake_page, preview_server)
    asyncio.run(coord._open_external_async("empty.pdf", b""))
    asyncio.run(coord._save_bytes_async("empty.pdf", b""))


def test_f5_boundary_url_launcher_exception_falls_back_to_save(
    tmp_path: Path,
    fake_page: FakePage,
    preview_server: LocalPreviewServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved_bytes: list[bytes] = []

    async def _failing_launch(_self: Any, _url: str) -> None:
        raise RuntimeError("FileUriExposedException")

    async def _fake_save(_self: Any, *, src_bytes: bytes = b"", **_kw: Any) -> str:
        saved_bytes.append(src_bytes)
        return "saved"

    monkeypatch.setattr(ft.UrlLauncher, "launch_url", _failing_launch)
    monkeypatch.setattr(ft.FilePicker, "save_file", _fake_save)

    coord = _make_coordinator(tmp_path, fake_page, preview_server)
    asyncio.run(coord._open_external_async("slides.pdf", b"%PDF-1.4"))
    assert saved_bytes == [b"%PDF-1.4"]


# ---------------------------------------------------------------------------
# F6 Boundaries: Word (.docx/.doc) Engine (5 tests)
# ---------------------------------------------------------------------------


def test_f6_boundary_corrupted_non_zip_docx_graceful_error() -> None:
    doc = convert_document_bytes("broken.docx", b"not-a-valid-zip", "docx")
    assert doc.kind == "docx"
    assert doc.markdown_text or doc.html_document


def test_f6_boundary_empty_docx_zero_paragraphs(preview_server: LocalPreviewServer) -> None:
    empty_docx = build_docx_bytes(paragraphs=(), headings=(), tables=())
    doc = convert_document_bytes("empty.docx", empty_docx, "docx")
    assert doc.kind == "docx"
    assert doc.outline == []
    reader = DocumentReaderView(
        file_name="empty.docx",
        data=empty_docx,
        kind="docx",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda *_a: None,
        on_open_external=lambda *_a: None,
        pane_width=360.0,
    )
    assert_valid_flet_mro(reader)
    assert_no_fixed_width_overflow(reader, 360, allow_horizontal_scroll_descendants=True)


def test_f6_boundary_six_column_wide_table_in_docx(preview_server: LocalPreviewServer) -> None:
    table = [
        ["Cột 1", "Cột 2", "Cột 3", "Cột 4", "Cột 5", "Cột 6"],
        ["A", "B", "C", "D", "E", "F"],
    ]
    docx_bytes = build_docx_bytes(paragraphs=(), tables=[table])
    doc = convert_document_bytes("wide_table.docx", docx_bytes, "docx")
    tbl_blocks = [b for b in doc.docx_blocks if b.kind == "table"]
    assert tbl_blocks and len(tbl_blocks[0].rows[0]) == 6

    reader = DocumentReaderView(
        file_name="wide_table.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda *_a: None,
        on_open_external=lambda *_a: None,
        pane_width=360.0,
    )
    assert_valid_flet_mro(reader)
    assert_no_fixed_width_overflow(reader, 360, allow_horizontal_scroll_descendants=True)


def test_f6_boundary_legacy_ole2_doc_binary_magic_header() -> None:
    ole2_doc = build_ole2_bytes({"WordDocument": b"\x00" * 128})
    assert ole2_doc.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    doc = convert_document_bytes("legacy.doc", ole2_doc, "docx")
    assert doc.kind == "docx"
    assert doc.html_document


def test_f6_boundary_100_heading_docx_outline_and_reader(
    preview_server: LocalPreviewServer,
) -> None:
    headings = [(1 + (i % 3), f"Mục {i}: Nội dung {i}") for i in range(1, 101)]
    docx_bytes = build_docx_bytes(paragraphs=("Đoạn mở đầu",), headings=headings)
    doc = convert_document_bytes("many_headings.docx", docx_bytes, "docx")
    assert len(doc.outline) == 100
    assert len({item.anchor_id for item in doc.outline}) == 100

    reader = DocumentReaderView(
        file_name="many_headings.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda *_a: None,
        on_open_external=lambda *_a: None,
        preconverted=doc,
        pane_width=360.0,
    )
    assert_valid_flet_mro(reader)


# ---------------------------------------------------------------------------
# F7 Boundaries: PowerPoint (.pptx/.ppt) Engine (5 tests)
# ---------------------------------------------------------------------------


def test_f7_boundary_corrupted_pptx_graceful_error() -> None:
    doc = convert_document_bytes("corrupt.pptx", b"PK\x03\x04broken-pptx", "pptx")
    assert doc.kind == "pptx"
    assert doc.html_document


def test_f7_boundary_empty_zero_slide_pptx(preview_server: LocalPreviewServer) -> None:
    zero_pptx = _build_zero_slide_pptx_bytes()
    doc = convert_document_bytes("empty.pptx", zero_pptx, "pptx")
    assert doc.kind == "pptx"
    assert doc.slides == []

    reader = DocumentReaderView(
        file_name="empty.pptx",
        data=zero_pptx,
        kind="pptx",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda *_a: None,
        on_open_external=lambda *_a: None,
        pane_width=360.0,
    )
    assert_valid_flet_mro(reader)


@pytest.mark.parametrize(
    ("aspect", "expected_ratio"),
    [("4:3", 4 / 3), ("16:9", 16 / 9), ("16:10", 16 / 10)],
)
def test_f7_boundary_aspect_ratios_4_3_16_9_and_widescreen(
    aspect: str, expected_ratio: float
) -> None:
    pptx = build_pptx_bytes(slides=[("Slide 1", "Body")], aspect=aspect)
    doc = convert_document_bytes(f"deck_{aspect}.pptx", pptx, "pptx")
    assert doc.rich_slides
    slide = doc.rich_slides[0]
    assert abs(slide.aspect_ratio - expected_ratio) < 0.05


def test_f7_boundary_missing_notes_and_empty_chart_table() -> None:
    pptx = build_pptx_bytes(
        slides=[("Slide A", "Body A")],
        include_notes=False,
        include_table=False,
        include_chart=False,
    )
    doc = convert_document_bytes("plain.pptx", pptx, "pptx")
    assert len(doc.slides) == 1
    assert doc.slides[0][2] == ""


def test_f7_boundary_legacy_ole2_ppt_binary_magic_header() -> None:
    ole2_ppt = build_ole2_bytes({"PowerPoint Document": b"\x00" * 64})
    assert ole2_ppt.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    doc = convert_document_bytes("legacy.ppt", ole2_ppt, "pptx")
    assert doc.kind == "pptx"
    assert doc.html_document


# ---------------------------------------------------------------------------
# F8 Boundaries: DocumentReaderView Tier A/B & Mobile UX (5 tests)
# ---------------------------------------------------------------------------


def test_f8_boundary_pdf_batch_1_vs_5_vs_7_pages_footer_wrap(
    preview_server: LocalPreviewServer,
) -> None:
    for count in (1, 5):
        pdf = build_pdf_bytes([f"Page {i}" for i in range(1, count + 1)])
        r = DocumentReaderView(
            file_name=f"p{count}.pdf",
            data=pdf,
            kind="pdf",
            preview_server=preview_server,
            on_close=lambda: None,
            on_save=lambda *_a: None,
            on_open_external=lambda *_a: None,
            pane_width=360.0,
        )
        assert not any("Xem tiếp trang" in t for t in collect_texts(r))
        r.dispose()

    pdf7 = build_pdf_bytes([f"Page {i}" for i in range(1, 8)])
    r7 = DocumentReaderView(
        file_name="p7.pdf",
        data=pdf7,
        kind="pdf",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda *_a: None,
        on_open_external=lambda *_a: None,
        pane_width=360.0,
    )
    try:
        assert any("Xem tiếp trang 6" in t for t in collect_texts(r7))
        footer_row = next(
            (
                c
                for c in walk_controls(r7)
                if isinstance(c, ft.Row) and any("Xem tiếp trang" in t for t in collect_texts(c))
            ),
            None,
        )
        assert footer_row is not None
        assert footer_row.wrap is True
        assert_no_fixed_width_overflow(r7, 360)
    finally:
        r7.dispose()


def test_f8_boundary_pdf_search_zero_hits_and_special_regex_chars() -> None:
    pdf = build_pdf_bytes(["Literal pattern .*+? inside page 1", "Second page text"])
    sess = PdfDocumentSession(pdf)
    try:
        assert sess.search("khong_ton_tai_xyz_999") == []
        hits = sess.search(".*+?")
        assert len(hits) == 1
        assert hits[0].page_index == 0
        assert sess.search(".*[invalid(regex") == []
    finally:
        sess.close()


@pytest.mark.parametrize("width", [359.0, 360.0, 599.0, 600.0])
def test_f8_boundary_document_reader_width_breakpoints(
    width: float, preview_server: LocalPreviewServer
) -> None:
    docx_bytes = build_docx_bytes(
        headings=[(1, "Chương 1")],
        paragraphs=["Nội dung kiểm thử responsive width."],
    )
    reader = DocumentReaderView(
        file_name="responsive.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda *_a: None,
        on_open_external=lambda *_a: None,
        pane_width=width,
    )
    reader.sync_bounds(0, 0, width, 780)
    assert_valid_flet_mro(reader)
    assert_no_fixed_width_overflow(reader, width, allow_horizontal_scroll_descendants=True)


def test_f8_boundary_interactive_viewer_zoom_min_max_bounds(
    preview_server: LocalPreviewServer,
) -> None:
    img_reader = DocumentReaderView(
        file_name="diagram.png",
        data=tiny_png_bytes(),
        kind="image",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda *_a: None,
        on_open_external=lambda *_a: None,
        pane_width=360.0,
    )
    viewers = [c for c in walk_controls(img_reader) if isinstance(c, ft.InteractiveViewer)]
    assert len(viewers) == 1
    assert 0 < float(viewers[0].min_scale or 0) < 1.0
    assert float(viewers[0].max_scale or 0) >= 3.0


def test_f8_boundary_rapid_dispose_and_update_theme_tier_a_and_b(
    preview_server: LocalPreviewServer,
) -> None:
    docx_bytes = build_docx_bytes(paragraphs=["Theme & dispose test"])
    # Tier B
    r_b = DocumentReaderView(
        file_name="tier_b.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda *_a: None,
        on_open_external=lambda *_a: None,
    )
    for preset in ("snow_light", "material_graphite"):
        apply_theme(preset)
        r_b.update_theme()
        assert_valid_flet_mro(r_b)
    r_b.dispose()
    r_b.dispose()

    # Tier A (with webview_builder returning control)
    r_a = DocumentReaderView(
        file_name="tier_a.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda *_a: None,
        on_open_external=lambda *_a: None,
        webview_builder=lambda _u: ft.Container(expand=True),
    )
    apply_theme("snow_light")
    r_a.update_theme()
    r_a.dispose()
    r_a.dispose()


# ---------------------------------------------------------------------------
# F9 Boundaries: Android System Back Sub-Stack (5 tests)
# ---------------------------------------------------------------------------


def test_f9_boundary_handle_back_empty_overlays_and_list_root_returns_false(
    fake_page: FakePage, platform_profile
) -> None:
    platform_profile("android", page=fake_page)
    tab = CoursesTabView(page=fake_page)
    tab.set_viewport_size(360, 780)
    assert tab._overlays == []
    assert tab._state.in_detail_mobile is False
    assert tab.handle_back() is False


def test_f9_boundary_handle_back_wide_mode_collapses_pane3_expanded_first(
    fake_page: FakePage,
) -> None:
    tab = CoursesTabView(page=fake_page)
    tab.set_viewport_size(1100, 780)
    tab.set_pane3_expanded(True)
    assert tab.pane3_expanded is True
    assert tab.handle_back() is True
    assert tab.pane3_expanded is False
    assert tab.handle_back() is False


def test_f9_boundary_archive_3_level_nested_folder_back_stack() -> None:
    archive_bytes = build_zip_bytes({"a/b/c/leaf.txt": b"leaf"})
    explorer = ArchiveExplorerView(
        archive_name="deep.zip",
        archive_bytes=archive_bytes,
        archive_service=ArchiveService(),
        on_close=lambda: None,
        on_open_entry=lambda *_a: None,
        on_save_raw=lambda *_a: None,
    )
    assert explorer.handle_back() is False
    for folder in ("a", "b", "c"):
        _find_clickable_with_text(explorer, folder).on_click(None)

    assert explorer.handle_back() is True  # c -> b
    assert explorer.handle_back() is True  # b -> a
    assert explorer.handle_back() is True  # a -> root
    assert explorer.handle_back() is False  # at root


def test_f9_boundary_quiz_handle_back_summary_false_vs_review_true() -> None:
    quiz_view = QuizActivityView(
        source="courses",
        course_id=101,
        module=make_module(901, modname="quiz", name="Quiz Boundary"),
        activity_service=FakeActivityService(),
        on_close=lambda: None,
        on_open_webview=lambda *_a: None,
        pane_width=360.0,
    )
    _wait_until(lambda: any("Xem lại" in t for t in collect_texts(quiz_view)), label="quiz summary")
    assert quiz_view.handle_back() is False

    _find_button_with_text(quiz_view, "Xem lại").on_click(None)
    _wait_until(
        lambda: any("Quay lại tổng quan Quiz" in t for t in collect_texts(quiz_view)),
        label="quiz review",
    )
    assert quiz_view.handle_back() is True
    assert quiz_view.handle_back() is False


def test_f9_boundary_app_controller_confirm_pop_true_at_root_false_when_handled(
    fake_page: FakePage, platform_profile
) -> None:
    platform_profile("android", page=fake_page)
    fv = FakeView(can_pop=False)
    fake_page.views = [fv]

    course = make_course(101)
    svc = FakeCatalogService(courses=[course])
    tab = CoursesTabView(page=fake_page, catalog_service=svc)
    tab.set_viewport_size(360, 780)
    tab.visible = True
    asyncio.run(tab.load_initial_async())
    asyncio.run(tab._on_user_select_course_async(course.course_key))

    ctrl = AppController.__new__(AppController)
    ctrl.page = fake_page
    ctrl._today_schedule_component = None
    ctrl.courses_view = tab
    ctrl.detail_view = SimpleNamespace(visible=False)
    ctrl.settings_view = SimpleNamespace(visible=False)
    ctrl.calendar_view = SimpleNamespace(visible=False)
    ctrl.grade_overview_view = SimpleNamespace(visible=False)
    ctrl.view_manager = SimpleNamespace(_detail_origin=None)

    # In detail mobile -> handled -> confirm_pop(False)
    asyncio.run(ctrl._on_root_view_confirm_pop(SimpleNamespace(control=fv)))
    assert fv.confirmed_pops == [False]

    # Now at catalog list root -> not handled -> confirm_pop(True)
    asyncio.run(ctrl._on_root_view_confirm_pop(SimpleNamespace(control=fv)))
    assert fv.confirmed_pops == [False, True]


# ---------------------------------------------------------------------------
# F10 Boundaries: Viewport Breakpoints & Extreme Inputs (5 tests)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("width", "height", "expected_mode"),
    [
        (360, 780, "mobile"),
        (390, 844, "mobile"),
        (430, 932, "mobile"),
        (599, 780, "mobile"),
        (600, 780, "tablet_portrait"),
        (979, 780, "tablet_portrait"),
        (980, 800, "wide"),
    ],
)
def test_f10_boundary_exact_breakpoint_viewports(
    width: int, height: int, expected_mode: str, fake_page: FakePage
) -> None:
    assert compute_courses_layout_mode(width) == expected_mode
    tab = CoursesTabView(page=fake_page)
    tab.set_viewport_size(width, height)
    assert tab._layout_mode == expected_mode
    assert_valid_flet_mro(tab)
    if width < 600:
        assert_no_fixed_width_overflow(tab, width)


def test_f10_boundary_empty_course_zero_sections_zero_grades() -> None:
    course = make_course(101)
    state = CoursesState(
        courses=[course],
        selected_course_key=course.course_key,
        sections=[],
        grades=[],
        in_detail_mobile=True,
        layout_mode="mobile",
    )
    detail = CourseDetailPane()
    for subtab in (0, 1, 2):
        state.active_subtab = subtab
        detail.render(state, show_back=True, update=False)
        assert_valid_flet_mro(detail)
        assert_no_fixed_width_overflow(detail, 360)


def test_f10_boundary_extremely_long_vietnamese_course_title_teacher_and_email() -> None:
    long_title = "Phát triển hệ thống thông tin doanh nghiệp và quản trị cơ sở dữ liệu phân tán thời gian thực " * 2
    long_teacher = "PGS.TSKH. Nguyễn Hoàng Trần Lê Phạm Minh Phương Anh Tuấn"
    long_email = "nguyen.hoang.tran.le.pham.minh.phuong.anh.tuan.giangvien@ut.edu.vn"
    course = make_course(
        101,
        display_name=long_title,
        teacher_name=long_teacher,
        teacher_email=long_email,
    )
    state = CoursesState(
        courses=[course],
        selected_course_key=course.course_key,
        layout_mode="mobile",
        in_detail_mobile=True,
        user_groups=[{"name": "Nhóm nghiên cứu khoa học ứng dụng công nghệ cao số 01"}],
    )
    list_pane = CourseListPane()
    list_pane.render(state, update=False)
    assert_valid_flet_mro(list_pane)
    assert_no_fixed_width_overflow(list_pane, 360)

    detail_pane = CourseDetailPane()
    detail_pane.render(state, show_back=True, update=False)
    assert_valid_flet_mro(detail_pane)
    assert_no_fixed_width_overflow(detail_pane, 360)


def test_f10_boundary_120_question_quiz_jump_grid_collapsed_on_360dp() -> None:
    svc = FakeActivityService()
    quiz_view = QuizActivityView(
        source="courses",
        course_id=101,
        module=make_module(902, modname="quiz", name="Thi cuối kỳ 120 câu"),
        activity_service=svc,
        on_close=lambda: None,
        on_open_webview=lambda *_a: None,
        pane_width=360.0,
    )
    _wait_until(lambda: any("Xem lại" in t for t in collect_texts(quiz_view)), label="quiz summary")
    review_btns = [
        ctrl
        for ctrl in walk_controls(quiz_view)
        if isinstance(ctrl, ft.OutlinedButton) and "Xem lại" in str(getattr(ctrl, "content", ""))
    ]
    assert len(review_btns) >= 2
    # Second attempt in FakeActivityService has attempt_id=5120 (120 questions)
    review_btns[1].on_click(None)
    _wait_until(
        lambda: any("Mục lục câu hỏi (120)" in t for t in collect_texts(quiz_view)),
        label="120-q quiz review",
    )
    tiles = [c for c in walk_controls(quiz_view) if isinstance(c, ft.ExpansionTile)]
    assert tiles and tiles[0].expanded is False
    assert_valid_flet_mro(quiz_view)
    assert_no_fixed_width_overflow(quiz_view, 360)


def test_f10_boundary_6col_wide_table_and_long_url_in_inline_card(
    preview_server: LocalPreviewServer,
) -> None:
    long_url = "https://courses.ut.edu.vn/pluginfile.php/12345/mod_label/intro/" + ("long_segment_" * 8) + "syllabus.pdf"
    mod = make_module(
        55,
        modname="label",
        name="Thông báo dài",
        inline_html=f'<p>Xem tài liệu:</p><a href="{long_url}">{long_url}</a>',
    )
    course = make_course(101)
    state = CoursesState(
        courses=[course],
        selected_course_key=course.course_key,
        sections=[make_section(1, modules=(mod,))],
        layout_mode="mobile",
        in_detail_mobile=True,
    )
    detail = CourseDetailPane()
    detail.render(state, show_back=True, update=False)
    assert_valid_flet_mro(detail)
    assert_no_fixed_width_overflow(detail, 360)

    docx_bytes = build_docx_bytes(
        paragraphs=(),
        tables=[[["H1", "H2", "H3", "H4", "H5", "H6"], ["1", "2", "3", "4", "5", "6"]]],
    )
    reader = DocumentReaderView(
        file_name="6col.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda *_a: None,
        on_open_external=lambda *_a: None,
        pane_width=360.0,
    )
    assert_valid_flet_mro(reader)
    assert_no_fixed_width_overflow(reader, 360, allow_horizontal_scroll_descendants=True)


# ---------------------------------------------------------------------------
# F11 Boundaries: Landscape, Nav Shell & State Caps (5 tests)
# ---------------------------------------------------------------------------


def test_f11_boundary_780x360_ultra_short_landscape_rail_and_detail(
    fake_page: FakePage,
) -> None:
    shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    assert shell.apply_width(780) is True
    assert shell.apply_height(360) is True
    assert shell.rail.scrollable is True
    assert shell.apply_height(419) is True
    assert shell.rail.scrollable is True
    assert shell.apply_height(420) is False
    assert shell.rail.scrollable is False

    course = make_course(101, credits=3, teacher_name="TS. A")
    tab = CoursesTabView(page=fake_page)
    tab._state.set_courses([course])
    tab._state.select_course(course.course_key)
    tab.set_viewport_size(780, 360)
    assert any("Thông tin môn" in t for t in collect_texts(tab._detail_pane))
    assert_valid_flet_mro(tab)


@pytest.mark.parametrize(
    ("width", "expected_rail"),
    [
        (0, False),
        (float("nan"), False),
        (float("inf"), False),
        (-50, False),
        (599, False),
        (600, True),
    ],
)
def test_f11_boundary_adaptive_nav_shell_apply_width_edge_values(
    width: float, expected_rail: bool
) -> None:
    shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    assert shell.apply_width(width) is expected_rail
    assert shell.rail_visible is expected_rail
    assert shell.rail_host.visible is expected_rail
    assert shell.bar.visible is (not expected_rail)


def test_f11_boundary_rapid_push_pop_multiple_overlays_preserves_immersive_state(
    fake_page: FakePage, platform_profile
) -> None:
    platform_profile("android", page=fake_page)
    transitions: list[bool] = []
    tab = CoursesTabView(page=fake_page, on_immersive_changed=transitions.append)
    tab.set_viewport_size(360, 780)

    tab.push_overlay(ft.Container(expand=True), tag="o1")
    assert transitions[-1] is True
    tab.push_overlay(ft.Container(expand=True), tag="o2")
    assert tab._immersive_active is True

    # Popping o2 still leaves o1 active -> stays immersive
    assert tab.pop_overlay() is True
    assert tab._immersive_active is True

    # Popping o1 leaves 0 overlays -> exits immersive
    assert tab.pop_overlay() is True
    assert transitions[-1] is False

    # Rapid push 2 then clear_overlays -> exits immersive cleanly
    tab.push_overlay(ft.Container(expand=True), tag="o3")
    tab.push_overlay(ft.Container(expand=True), tag="o4")
    assert tab._immersive_active is True
    tab.clear_overlays()
    assert transitions[-1] is False


def test_f11_boundary_recent_items_0_vs_1_vs_5_plus_cap() -> None:
    course = make_course(101)
    pane = CourseListPane()

    # 0 items
    pane.render(CoursesState(courses=[course], recent_items=[]), update=False)
    assert pane._recent_slot.visible is False
    assert not any("Tiếp tục nhanh" in t for t in collect_texts(pane))

    # 1 item -> no expand button
    pane.render(CoursesState(courses=[course], recent_items=[make_recent(cmid=1)]), update=False)
    assert pane._recent_slot.visible is True
    assert any("Tiếp tục nhanh" in t for t in collect_texts(pane))
    assert not any("Xem thêm" in t for t in collect_texts(pane))

    # 8 items -> capped at 5 ("Xem thêm 4 mục gần đây")
    many = [
        make_recent(
            cmid=i,
            file_name=f"f{i}.pdf",
            attachment_url=f"https://courses.ut.edu.vn/pluginfile.php/{i}/f{i}.pdf",
        )
        for i in range(1, 9)
    ]
    pane.render(CoursesState(courses=[course], recent_items=many), update=False)
    assert any("Xem thêm 4 mục gần đây" in t for t in collect_texts(pane))


def test_f11_boundary_offline_banner_on_retry_none_vs_callback() -> None:
    no_retry = build_offline_banner("Chỉ đọc", on_retry=None)
    assert_valid_flet_mro(no_retry)
    assert not any("Thử lại" in t for t in collect_texts(no_retry))

    hits: list[bool] = []
    with_retry = build_offline_banner("Lỗi đồng bộ", on_retry=lambda: hits.append(True))
    assert_valid_flet_mro(with_retry)
    _find_button_with_text(with_retry, "Thử lại").on_click(None)
    assert hits == [True]
