"""Tier 3 cross-feature pairwise E2E tests for Courses Tab & Office Viewer (F1-F11)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import flet as ft
import pytest

from core.courses.archive_service import ArchiveService
from core.courses.cache_store import CourseDetailSnapshot
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.preview_cache import PreviewFileCache
from gui.components.courses.activity_views import (
    ForumActivityView,
    PaneAssignmentView,
    QuizActivityView,
    RichContentActivityView,
)
from gui.components.courses.adaptive_nav import AdaptiveNavShell
from gui.components.courses.archive_explorer_view import ArchiveExplorerView
from gui.components.courses.courses_tab_view import CoursesTabView
from gui.components.courses.document_reader_view import DocumentReaderView
from gui.components.courses.webview_pane import CoursesWebViewPane
from gui.controllers.courses_coordinator import CoursesCoordinator
from gui.core.theme import C, apply_theme
from tests.e2e_courses.builders import (
    FakeActivityService,
    FakeCatalogService,
    build_assignment_payload,
    build_docx_bytes,
    build_ole2_bytes,
    build_pdf_bytes,
    build_pptx_bytes,
    build_zip_bytes,
    make_attachment,
    make_course,
    make_module,
    make_recent,
    make_section,
)
from tests.e2e_courses.conftest import (
    FakePage,
    assert_min_touch_target,
    assert_no_fixed_width_overflow,
    assert_valid_flet_mro,
    bind_control_page,
    collect_texts,
    http_request,
    walk_controls,
)


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), f"Timed out after {timeout}s waiting for condition"


def _find_icon_button(root: Any, tooltip_substr: str) -> ft.IconButton:
    for ctrl in walk_controls(root):
        if isinstance(ctrl, ft.IconButton) and tooltip_substr in str(
            getattr(ctrl, "tooltip", "") or ""
        ):
            return ctrl
    raise AssertionError(f"IconButton with tooltip containing {tooltip_substr!r} not found")


def _find_chip(root: Any, text_substr: str) -> ft.Chip:
    for ctrl in walk_controls(root):
        if isinstance(ctrl, ft.Chip):
            lbl = getattr(ctrl, "label", None)
            lbl_text = lbl.value if isinstance(lbl, ft.Text) else str(lbl or "")
            if text_substr in lbl_text:
                return ctrl
    raise AssertionError(f"Chip containing {text_substr!r} not found")


def _find_button_with_text(root: Any, text_substr: str) -> ft.BaseControl:
    btn_types = (ft.Button, ft.OutlinedButton, ft.TextButton, ft.FilledButton)
    for ctrl in walk_controls(root):
        if isinstance(ctrl, btn_types):
            content = getattr(ctrl, "content", None)
            label = content.value if isinstance(content, ft.Text) else str(content or "")
            if text_substr in label:
                return ctrl
    raise AssertionError(f"Button containing {text_substr!r} not found")


# ---------------------------------------------------------------------------
# Pair 1: 360x780 Mobile + DocumentReaderView DOCX Tier A + Immersive + Back
# ---------------------------------------------------------------------------


def test_pair01_mobile_docx_tier_a_immersive_and_handle_back(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pair 1 (F2, F3, F6, F8, F9, F10, F11): 360dp DOCX Tier A collapses bar & restores on back."""
    from core.courses.office_viewer_service import prepare_office_viewer
    from gui.components.courses.embedded_webview_host import build_embedded_webview_host

    platform_profile("android", page=fake_page)
    fake_page.resize(360, 780)
    bind_control_page(monkeypatch, ft.BaseControl, fake_page)

    nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    nav_shell.apply_width(360)
    nav_shell.apply_height(780)
    assert nav_shell.bar.visible is True

    courses_view = CoursesTabView(page=fake_page)
    courses_view.set_on_immersive_changed(nav_shell.set_immersive)
    courses_view.set_viewport_size(360, 780)

    docx_bytes = build_docx_bytes(
        headings=[(1, "Chương 1: Kiến trúc"), (2, "Mục 1.1 Tổng quan")],
        paragraphs=["Nội dung bài giảng Word cho sinh viên UTH."],
        omml_equations=["E = mc^2"],
        tables=[[
            ["Cột 1", "Cột 2", "Cột 3", "Cột 4", "Cột 5", "Cột 6"],
            ["A", "B", "C", "D", "E", "F"],
        ]],
        page_break_between=True,
    )

    descriptor = prepare_office_viewer(docx_bytes, "lecture.docx", preview_server)
    assert descriptor is not None
    assert descriptor.format == "docx"
    desc_url = getattr(descriptor, "viewer_url", None) or getattr(descriptor, "url", "")
    assert "/viewer/" in desc_url and urlsplit(desc_url).path.endswith("/docx_host.html")

    captured_urls: list[str] = []

    def _tier_a_builder(url: str) -> ft.Control:
        captured_urls.append(url)
        host = build_embedded_webview_host(url, page=fake_page)
        assert host is not None
        assert host.backend == "flet_webview"
        return host.control()

    reader = DocumentReaderView(
        file_name="lecture.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=courses_view.pop_overlay,
        on_save=lambda _n, _b: None,
        on_open_external=lambda _n, _b: None,
        webview_builder=_tier_a_builder,
        pane_width=360.0,
    )
    courses_view.push_overlay(reader, tag="reader:lecture.docx")

    assert captured_urls, "DocumentReaderView must invoke Tier A webview_builder for DOCX"
    viewer_url = captured_urls[0]
    parsed = urlsplit(viewer_url)
    assert "/viewer/" in parsed.path
    assert parsed.path.endswith("/docx_host.html")
    assert parsed.path == urlsplit(desc_url).path

    status, headers, body = http_request(viewer_url)
    assert status == 200
    assert "script-src 'self'" in headers.get("content-security-policy", "")
    assert len(body) > 0

    src_urls = parse_qs(parsed.query).get("src") or []
    assert len(src_urls) == 1
    src_status, _src_hdrs, src_body = http_request(src_urls[0])
    assert src_status == 200
    assert src_body == docx_bytes

    assert nav_shell.bar.visible is False
    assert_valid_flet_mro(courses_view)
    assert_no_fixed_width_overflow(
        courses_view, 360.0, allow_horizontal_scroll_descendants=True
    )

    assert courses_view.handle_back() is True
    assert nav_shell.bar.visible is True
    assert courses_view.handle_back() is False


# ---------------------------------------------------------------------------
# Pair 2: 360x780 Mobile + Archive nested folder + PDF modal BottomSheet + Back
# ---------------------------------------------------------------------------


def test_pair02_mobile_archive_nested_pdf_modal_outline_and_back_stack(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pair 2 (F1, F8, F9, F10): Nested archive -> PDF modal BottomSheet outline -> step-by-step back."""
    platform_profile("android", page=fake_page)
    fake_page.resize(360, 780)
    bind_control_page(monkeypatch, ft.BaseControl, fake_page)

    pdf_bytes = build_pdf_bytes(
        ["Trang 1 mở đầu", "Trang 2 nội dung chính", "Trang 3 tổng kết"],
        outline_titles=[("Chương 1", 0), ("Chương 2", 2)],
    )
    zip_bytes = build_zip_bytes(
        {
            "labs/week04/guide.pdf": pdf_bytes,
            "README.txt": b"Lab instructions",
        }
    )

    courses_view = CoursesTabView(page=fake_page)
    courses_view.set_viewport_size(360, 780)

    def _open_entry(name: str, extracted: bytes, kind: Any) -> None:
        reader = DocumentReaderView(
            file_name=name,
            data=extracted,
            kind=kind,
            preview_server=preview_server,
            on_close=courses_view.pop_overlay,
            on_save=lambda _n, _b: None,
            on_open_external=lambda _n, _b: None,
            pane_width=360.0,
        )
        courses_view.push_overlay(reader, tag=f"reader:{name}")

    explorer = ArchiveExplorerView(
        archive_name="labs.zip",
        archive_bytes=zip_bytes,
        archive_service=ArchiveService(),
        on_close=courses_view.pop_overlay,
        on_open_entry=_open_entry,
        on_save_raw=lambda _n, _b: None,
    )
    courses_view.push_overlay(explorer, tag="archive:labs.zip")

    root_node = explorer._stack[-1]
    labs_node = next(ch for ch in root_node.children if ch.name == "labs")
    explorer._open_node(labs_node)
    week04_node = next(ch for ch in explorer._stack[-1].children if ch.name == "week04")
    explorer._open_node(week04_node)
    guide_node = next(ch for ch in explorer._stack[-1].children if ch.name == "guide.pdf")
    explorer._open_node(guide_node)

    assert len(courses_view._overlays) == 2
    reader_top = courses_view._overlays[-1]
    assert isinstance(reader_top, DocumentReaderView)
    assert_valid_flet_mro(courses_view)
    assert_no_fixed_width_overflow(
        courses_view, 360.0, allow_horizontal_scroll_descendants=True
    )

    outline_btn = _find_icon_button(reader_top, "Mục lục PDF")
    outline_btn.on_click(None)

    sheet = next(
        (
            dlg
            for dlg in [*fake_page.dialogs, *fake_page.overlay]
            if isinstance(dlg, ft.BottomSheet) and getattr(dlg, "open", False)
        ),
        None,
    )
    assert sheet is not None, (
        "360dp mobile PDF outline must open a modal ft.BottomSheet rather than a 220dp side panel"
    )
    assert_no_fixed_width_overflow(
        courses_view, 360.0, allow_horizontal_scroll_descendants=True
    )

    # 1st back closes outline BottomSheet while keeping reader overlay mounted
    assert courses_view.handle_back() is True
    assert len(courses_view._overlays) == 2
    assert courses_view._overlays[-1] is reader_top
    assert sheet.open is False

    # 2nd back pops reader overlay back to explorer
    assert courses_view.handle_back() is True
    assert len(courses_view._overlays) == 1
    assert courses_view._overlays[-1] is explorer
    assert len(explorer._stack) == 3  # root -> labs -> week04

    assert courses_view.handle_back() is True  # week04 -> labs
    assert len(explorer._stack) == 2
    assert courses_view.handle_back() is True  # labs -> root
    assert len(explorer._stack) == 1
    assert courses_view.handle_back() is True  # pops explorer
    assert len(courses_view._overlays) == 0


# ---------------------------------------------------------------------------
# Pair 3: Theme Toggle across CoursesTabView + Tier A eval_js + WebViewPane
# ---------------------------------------------------------------------------


def test_pair03_theme_toggle_across_courses_reader_tier_a_webview_and_server(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pair 3 (F2, F3, F6, F8, F11): Dark/light theme toggle propagates across views & eval_js."""
    from gui.components.courses.embedded_webview_host import EmbeddedWebViewHost

    platform_profile("android", page=fake_page)
    fake_page.resize(390, 844)
    bind_control_page(monkeypatch, DocumentReaderView, fake_page)
    apply_theme("midnight_blue")

    courses_view = CoursesTabView(page=fake_page)
    courses_view.set_viewport_size(390, 844)

    js_calls: list[str] = []

    class _SpyHost(EmbeddedWebViewHost):
        def __init__(self, url: str) -> None:
            super().__init__(
                backend="flet_webview",
                page=fake_page,
                url=url,
                webview_control=ft.Container(expand=True),
            )

        async def eval_js(self, script: str) -> None:
            js_calls.append(script)

    hosts: list[_SpyHost] = []

    def _builder(url: str) -> Any:
        h = _SpyHost(url)
        hosts.append(h)
        return h

    docx_bytes = build_docx_bytes(
        headings=["Chương 1"],
        paragraphs=["Kiểm thử giao diện sáng tối."],
    )
    reader = DocumentReaderView(
        file_name="theme_doc.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=courses_view.pop_overlay,
        on_save=lambda _n, _b: None,
        on_open_external=lambda _n, _b: None,
        webview_builder=_builder,
        pane_width=390.0,
    )
    courses_view.push_overlay(reader, tag="reader:theme")

    html_url = preview_server.register_html("<html><body><p>Preview</p></body></html>")
    webview_pane = CoursesWebViewPane(
        page=fake_page,
        source="courses",
        title="Trang Moodle",
        url=html_url,
        preview_server=preview_server,
        on_back=courses_view.pop_overlay,
        on_open_external=lambda _u: None,
        pane_width=390.0,
    )
    courses_view.push_overlay(webview_pane, tag="webview:theme")

    # Toggle reader dark mode -> Tier A host receives JS theme update
    reader._night_btn.on_click(None)
    assert js_calls, "Toggling reader dark/light mode on Tier A DOCX must invoke eval_js"

    # Toggle global app themes
    apply_theme("steel_light")
    courses_view.update_theme()
    light_bg = C.BG
    assert courses_view.bgcolor == light_bg
    assert reader.bgcolor == light_bg
    assert webview_pane.bgcolor == light_bg
    assert_valid_flet_mro(courses_view)

    apply_theme("nord_frost")
    courses_view.update_theme()
    assert courses_view.bgcolor == C.BG
    assert reader.bgcolor == C.BG
    assert webview_pane.bgcolor == C.BG
    assert preview_server.has_active_url(html_url) is True
    assert_valid_flet_mro(courses_view)


# ---------------------------------------------------------------------------
# Pair 4: Android CoursesCoordinator + PPTX Tier A + FilePicker.save_file
# ---------------------------------------------------------------------------


def test_pair04_android_coordinator_pptx_tier_a_and_safe_filepicker_save(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pair 4 (F3, F5, F7, F8): Android PPTX Tier A + FilePicker save without file:// URI leak."""
    fwv = pytest.importorskip("flet_webview")
    from core.courses.office_viewer_service import prepare_office_viewer
    from gui.components.courses import embedded_webview_host as ewh_mod
    from gui.controllers import courses_coordinator as coord_mod

    platform_profile("android", page=fake_page)
    fake_page.resize(390, 844)
    bind_control_page(monkeypatch, DocumentReaderView, fake_page)
    bind_control_page(monkeypatch, fwv.WebView, fake_page)

    built_hosts: list[tuple[str, Any]] = []
    orig_build = ewh_mod.build_embedded_webview_host

    def _spy_build(url: str = "", **kwargs: Any) -> Any:
        host = orig_build(url, **kwargs)
        built_hosts.append((url, host))
        return host

    monkeypatch.setattr(ewh_mod, "build_embedded_webview_host", _spy_build)
    monkeypatch.setattr(coord_mod, "build_embedded_webview_host", _spy_build)

    pptx_bytes = build_pptx_bytes(
        slides=[("Bài 1: Giới thiệu", "Nội dung slide 1"), ("Bài 2: Thực hành", "Nội dung slide 2")],
        aspect="16:9",
        include_notes=True,
        include_table=True,
        include_chart=True,
    )

    descriptor = prepare_office_viewer(pptx_bytes, "lecture.pptx", preview_server)
    assert descriptor is not None
    viewer_url = getattr(descriptor, "viewer_url", None) or getattr(descriptor, "url", "")
    assert "/viewer/" in viewer_url and "pptx_host.html" in viewer_url
    status, headers, _body = http_request(viewer_url)
    assert status == 200
    assert "script-src 'self'" in headers.get("content-security-policy", "")

    saved_calls: list[dict[str, Any]] = []
    launched_external: list[str] = []

    async def _fake_save_file(
        self: Any,
        dialog_title: Any = None,
        file_name: Any = None,
        initial_directory: Any = None,
        file_type: Any = None,
        allowed_extensions: Any = None,
        src_bytes: Any = None,
    ) -> str:
        saved_calls.append(
            {
                "dialog_title": dialog_title,
                "file_name": file_name,
                "src_bytes": src_bytes,
            }
        )
        return f"/storage/emulated/0/Download/{file_name}"

    async def _fake_launch_url(self: Any, url: Any, **_kwargs: Any) -> None:
        launched_external.append(str(url))

    monkeypatch.setattr(ft.FilePicker, "save_file", _fake_save_file)
    monkeypatch.setattr(ft.UrlLauncher, "launch_url", _fake_launch_url)

    course = make_course(101)
    att = make_attachment("lecture.pptx", "https://courses.ut.edu.vn/pluginfile.php/1/lecture.pptx")
    mod = make_module(10, name="Slide bài giảng", attachments=(att,))
    catalog = FakeCatalogService(
        courses=[course],
        detail_map={("courses", 101): CourseDetailSnapshot(sections=[make_section(1, modules=(mod,))], grades=[], last_synced_at=100)},
        attachment_bytes_map={att.file_url: pptx_bytes},
    )

    coord = CoursesCoordinator(
        page=fake_page,  # type: ignore[arg-type]
        catalog_service=catalog,  # type: ignore[arg-type]
        preview_cache=PreviewFileCache(tmp_path),
        archive_service=ArchiveService(),
        preview_server=preview_server,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda u: fake_page.launch_url(u),
    )
    coord.courses_view.set_viewport_size(390, 844)
    coord.open_attachment_bytes_in_overlay(course, mod, att, pptx_bytes)

    assert coord.courses_view._overlays
    reader = coord.courses_view._overlays[-1]
    assert isinstance(reader, DocumentReaderView)
    assert built_hosts, (
        "CoursesCoordinator.open_attachment_bytes_in_overlay must mount Tier A via build_embedded_webview_host"
    )
    mounted_url, mounted_host = built_hosts[-1]
    assert "/viewer/" in mounted_url and "pptx_host.html" in mounted_url
    assert mounted_host is not None
    assert mounted_host.backend == "flet_webview"
    assert any(isinstance(c, fwv.WebView) for c in walk_controls(reader))

    reader._ext_btn.on_click(None)

    assert not any(u.startswith("file://") for u in launched_external)
    assert not any(u.startswith("file://") for u in fake_page.launched_urls)
    assert len(saved_calls) == 1
    assert saved_calls[0]["file_name"] == "lecture.pptx"
    assert saved_calls[0]["src_bytes"] == pptx_bytes


# ---------------------------------------------------------------------------
# Pair 5: Quiz 120-Q Review + Search/Filter + CoursesWebViewPane + handle_back
# ---------------------------------------------------------------------------


def test_pair05_quiz_120_questions_search_filter_webview_and_back_stack(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
) -> None:
    """Pair 5 (F4, F9, F10): 120-Q Quiz collapsed jump grid + filter + WebViewPane + handle_back."""
    platform_profile("android", page=fake_page)
    fake_page.resize(390, 844)

    courses_view = CoursesTabView(page=fake_page)
    courses_view.set_viewport_size(390, 844)

    activity_svc = FakeActivityService()
    mod = make_module(201, modname="quiz", name="Thi thử 120 câu")

    def _open_wv(title: str, url: str) -> None:
        pane = CoursesWebViewPane(
            page=fake_page,  # type: ignore[arg-type]
            source="courses",
            title=title,
            url=url,
            preview_server=preview_server,
            on_back=courses_view.pop_overlay,
            on_open_external=lambda u: fake_page.launch_url(u),
            pane_width=390.0,
        )
        courses_view.push_overlay(pane, tag="webview:quiz")

    quiz_view = QuizActivityView(
        source="courses",
        course_id=101,
        module=mod,
        activity_service=activity_svc,  # type: ignore[arg-type]
        on_close=courses_view.pop_overlay,
        on_open_webview=_open_wv,
        pane_width=390.0,
    )
    courses_view.push_overlay(quiz_view, tag="activity:quiz:201")

    _wait_until(
        lambda: quiz_view._summary is not None
        and isinstance(quiz_view._body_host.content, ft.ListView)
    )
    att_120 = next(a for a in quiz_view._summary.attempts if a.attempt_id == 5120)
    prev_body = quiz_view._body_host.content
    quiz_view._open_attempt_review(quiz_view._summary, 5120, att_120)
    _wait_until(
        lambda: quiz_view._in_review
        and quiz_view._current_review is not None
        and quiz_view._body_host.content is not prev_body
        and isinstance(quiz_view._body_host.content, ft.ListView)
    )

    tiles = [c for c in walk_controls(quiz_view) if isinstance(c, ft.ExpansionTile)]
    assert tiles, "120-question review must expose collapsible jump grid ExpansionTile"
    assert tiles[0].expanded is False
    assert_valid_flet_mro(quiz_view)
    assert_no_fixed_width_overflow(quiz_view, 390.0)

    # Filter to incorrect/unanswered and search for question #10
    wrong_chip = _find_chip(quiz_view, "Câu sai")
    wrong_chip.on_select(None)
    search_field = next(
        c
        for c in walk_controls(quiz_view)
        if isinstance(c, ft.TextField) and "Tìm câu hỏi" in str(c.hint_text or "")
    )
    search_field.value = "số 10"
    search_field.on_submit(type("E", (), {"control": search_field})())
    texts_after_search = "\n".join(collect_texts(quiz_view))
    assert "Câu 10" in texts_after_search
    assert "Câu 1\n" not in texts_after_search
    assert_no_fixed_width_overflow(quiz_view, 390.0)

    # Open WebView attempt link from review screen
    wv_btn = _find_button_with_text(quiz_view, "WebView")
    wv_btn.on_click(None)
    assert len(courses_view._overlays) == 2
    wv_pane = courses_view._overlays[-1]
    assert isinstance(wv_pane, CoursesWebViewPane)
    assert wv_pane._mobile_nav_bar.visible is True
    assert_min_touch_target(wv_pane._wv_back_btn, 44.0)
    assert_min_touch_target(wv_pane._more_menu_btn, 44.0)

    # Step back: WebViewPane -> Quiz review -> Quiz summary -> Pop Quiz
    assert courses_view.handle_back() is True
    assert courses_view._overlays[-1] is quiz_view
    assert quiz_view._in_review is True

    assert courses_view.handle_back() is True
    assert quiz_view._in_review is False

    assert courses_view.handle_back() is True
    assert len(courses_view._overlays) == 0


# ---------------------------------------------------------------------------
# Pair 6: 780x360 Landscape + AdaptiveNavShell + Collapsible Meta + sync_bounds
# ---------------------------------------------------------------------------


def test_pair06_landscape_780x360_nav_rail_collapsible_meta_and_sync_bounds(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
) -> None:
    """Pair 6 (F2, F8, F11): 780x360 landscape enables scrollable rail, collapsible meta, and sync_bounds."""
    platform_profile("android", page=fake_page)
    fake_page.resize(780, 360)

    nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    assert nav_shell.apply_width(780) is True
    assert nav_shell.apply_height(360) is True
    assert nav_shell.rail_host.visible is True
    assert nav_shell.bar.visible is False
    assert nav_shell.rail.scrollable is True

    course = make_course(
        101,
        display_name="Phát triển Ứng dụng Di động",
        teacher_name="PGS.TS. Trần Văn B",
        teacher_email="tvb@ut.edu.vn",
    )
    catalog = FakeCatalogService(
        courses=[course],
        detail_map={
            ("courses", 101): CourseDetailSnapshot(
                sections=[make_section(1, modules=(make_module(10),))],
                grades=[],
                last_synced_at=1_770_000_000,
            )
        },
        groups_map={("courses", 101): [{"id": 1, "name": "Nhóm 01"}]},
    )

    courses_view = CoursesTabView(page=fake_page, catalog_service=catalog)  # type: ignore[arg-type]
    courses_view.load_initial()
    courses_view.set_viewport_size(780, 360)
    asyncio.run(courses_view._on_user_select_course_async(course.course_key))

    detail_texts = collect_texts(courses_view._detail_pane)
    assert "Thông tin môn" in detail_texts
    assert courses_view._detail_pane._meta_expanded is False

    # Expand metadata on tap
    courses_view._detail_pane._toggle_meta_expanded()
    assert courses_view._detail_pane._meta_expanded is True

    bounds_calls: list[tuple[float, float, float, float]] = []
    docx_bytes = build_docx_bytes(paragraphs=["Landscape test document"])
    reader = DocumentReaderView(
        file_name="landscape.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=courses_view.pop_overlay,
        on_save=lambda _n, _b: None,
        on_open_external=lambda _n, _b: None,
        pane_width=780.0,
    )
    orig_sync_bounds = reader.sync_bounds

    def _wrapped_sync(left: float, top: float, width: float, height: float) -> None:
        bounds_calls.append((left, top, width, height))
        orig_sync_bounds(left, top, width, height)

    reader.sync_bounds = _wrapped_sync  # type: ignore[method-assign]
    courses_view.push_overlay(reader, tag="reader:landscape")

    fake_page.resize(360, 780)
    courses_view.set_viewport_size(360, 780)
    assert_valid_flet_mro(courses_view)
    assert_no_fixed_width_overflow(courses_view, 360.0)
    fake_page.resize(780, 360)
    courses_view.set_viewport_size(780, 360)

    assert len(bounds_calls) >= 3
    assert bounds_calls[-1][2] > bounds_calls[-2][2]
    assert_valid_flet_mro(courses_view)
    assert_no_fixed_width_overflow(
        courses_view, 780.0, allow_horizontal_scroll_descendants=True
    )


# ---------------------------------------------------------------------------
# Pair 7: Master List Scroll Preservation + Filter + Phone Detail + Back
# ---------------------------------------------------------------------------


def test_pair07_master_list_scroll_preservation_filter_detail_and_phone_back(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
) -> None:
    """Pair 7 (F9, F11): Mounted _list_pane preserves list/filter & omits phone selection border on back."""
    platform_profile("android", page=fake_page)
    fake_page.resize(360, 780)

    courses = [
        make_course(101, display_name="Lập trình Python", semester_label="HKII 2025-2026", is_current_term=True),
        make_course(102, display_name="Cấu trúc dữ liệu", semester_label="HKII 2025-2026", is_current_term=True),
        make_course(103, display_name="Mạng máy tính", semester_label="HKII 2025-2026", is_current_term=True),
        make_course(104, display_name="Python nâng cao", semester_label="HKI 2025-2026", is_current_term=False, semester_sort_key=(2025, 1)),
    ]
    catalog = FakeCatalogService(
        courses=courses,
        detail_map={
            ("courses", 101): CourseDetailSnapshot(
                sections=[make_section(1, modules=(make_module(10),))],
                grades=[],
                last_synced_at=1_770_000_000,
            )
        },
    )

    courses_view = CoursesTabView(page=fake_page, catalog_service=catalog)  # type: ignore[arg-type]
    courses_view.set_viewport_size(360, 780)
    courses_view.load_initial()

    courses_view._handle_filter_change("all", "Python")
    list_pane_id = id(courses_view._list_pane)
    cards_col_id = id(courses_view._list_pane._cards_list)
    assert len(courses_view._list_pane._card_map) == 2

    asyncio.run(courses_view._on_user_select_course_async("courses:101"))
    assert courses_view._state.in_detail_mobile is True
    assert courses_view._narrow_list_host.content is courses_view._list_pane
    assert courses_view._narrow_list_host.opacity == 0.0
    assert courses_view._narrow_workspace_host.visible is True

    assert courses_view.handle_back() is True
    assert courses_view._state.in_detail_mobile is False
    assert courses_view._narrow_list_host.opacity == 1.0
    assert id(courses_view._list_pane) == list_pane_id
    assert id(courses_view._list_pane._cards_list) == cards_col_id
    assert courses_view._state.search_query == "Python"
    assert len(courses_view._list_pane._card_map) == 2

    # Phone catalog list must not show persistent desktop accent selection border
    for card in courses_view._list_pane._card_map.values():
        border = getattr(card, "border", None)
        if isinstance(border, ft.Border) and border.top is not None:
            assert border.top.color != C.ACCENT


# ---------------------------------------------------------------------------
# Pair 8: LocalPreviewServer /viewer/ CSP + OfficeViewerService + _HTML_CSP
# ---------------------------------------------------------------------------


def test_pair08_preview_server_viewer_csp_and_html_csp_isolation(
    preview_server: LocalPreviewServer,
) -> None:
    """Pair 8 (F3, F6, F7): /viewer/ serves _VIEWER_APP_CSP while /<id> enforces _HTML_CSP."""
    from core.courses.office_viewer_service import prepare_office_viewer

    untrusted_url = preview_server.register_html(
        "<html><body><script>window.pwn=1</script><p>Hello</p></body></html>"
    )
    h_status, h_hdrs, _h_body = http_request(untrusted_url)
    assert h_status == 200
    html_csp = h_hdrs.get("content-security-policy", "")
    assert "default-src 'none'" in html_csp
    assert "script-src 'self'" not in html_csp

    docx_bytes = build_docx_bytes(paragraphs=["DOCX CSP check"])
    pptx_bytes = build_pptx_bytes(slides=[("Slide 1", "PPTX CSP check")])

    docx_desc = prepare_office_viewer(docx_bytes, "a.docx", preview_server)
    pptx_desc = prepare_office_viewer(pptx_bytes, "b.pptx", preview_server)
    assert docx_desc is not None and pptx_desc is not None

    for desc, expected_host in ((docx_desc, "docx_host.html"), (pptx_desc, "pptx_host.html")):
        v_url = getattr(desc, "viewer_url", None) or getattr(desc, "url", "")
        assert expected_host in v_url
        v_status, v_hdrs, _v_body = http_request(v_url)
        assert v_status == 200
        v_csp = v_hdrs.get("content-security-policy", "")
        assert "script-src 'self'" in v_csp
        assert "object-src 'none'" in v_csp
        assert v_hdrs.get("x-content-type-options") == "nosniff"

    # Path traversal and bad token must be rejected
    base = preview_server.viewer_base_url()
    trav_status, _, _ = http_request(f"{base}../../pyproject.toml")
    assert trav_status in (403, 404)
    bad_tok_url = f"http://127.0.0.1:{preview_server.port}/viewer/invalid-token/docx_host.html"
    tok_status, _, _ = http_request(bad_tok_url)
    assert tok_status == 403


# ---------------------------------------------------------------------------
# Pair 9: ForumActivityView Drill-Down + RichContentActivityView Chapter Switch
# ---------------------------------------------------------------------------


def test_pair09_forum_thread_drilldown_and_rich_content_chapter_switch(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
) -> None:
    """Pair 9 (F9, F10, F11): Forum posts drill-down + Book chapter switch + step-by-step back."""
    platform_profile("android", page=fake_page)
    fake_page.resize(360, 780)

    courses_view = CoursesTabView(page=fake_page)
    courses_view.set_viewport_size(360, 780)
    activity_svc = FakeActivityService()

    forum_mod = make_module(301, modname="forum", name="Diễn đàn học phần")
    forum_view = ForumActivityView(
        source="courses",
        course_id=101,
        module=forum_mod,
        activity_service=activity_svc,  # type: ignore[arg-type]
        on_close=courses_view.pop_overlay,
        pane_width=360.0,
    )
    courses_view.push_overlay(forum_view, tag="activity:forum:301")
    _wait_until(
        lambda: forum_view._summary is not None
        and isinstance(forum_view._body_host.content, ft.ListView)
    )

    disc = forum_view._summary.discussions[0]
    prev_forum_body = forum_view._body_host.content
    forum_view._open_discussion(forum_view._summary, disc)
    _wait_until(
        lambda: forum_view._in_posts
        and forum_view._current_posts is not None
        and forum_view._body_host.content is not prev_forum_body
        and isinstance(forum_view._body_host.content, ft.ListView)
    )
    assert_valid_flet_mro(forum_view)
    assert_no_fixed_width_overflow(forum_view, 360.0)

    book_mod = make_module(302, modname="book", name="Giáo trình điện tử")
    rich_view = RichContentActivityView(
        source="courses",
        course_id=101,
        module=book_mod,
        activity_service=activity_svc,  # type: ignore[arg-type]
        on_close=courses_view.pop_overlay,
        pane_width=360.0,
    )
    courses_view.push_overlay(rich_view, tag="activity:rich:302")
    _wait_until(
        lambda: rich_view._rich_page is not None
        and isinstance(rich_view._body_host.content, ft.ListView)
    )

    assert "Chương 1/3" in collect_texts(rich_view)
    next_btn = _find_button_with_text(rich_view, "Chương sau")
    next_btn.on_click(None)
    next_btn = _find_button_with_text(rich_view, "Chương sau")
    next_btn.on_click(None)
    assert "Chương 3/3" in collect_texts(rich_view)
    assert_valid_flet_mro(rich_view)
    assert_no_fixed_width_overflow(rich_view, 360.0)

    assert courses_view.handle_back() is True  # pops RichContentActivityView
    assert courses_view._overlays[-1] is forum_view
    assert forum_view._in_posts is True

    assert courses_view.handle_back() is True  # posts -> discussions
    assert forum_view._in_posts is False
    assert courses_view.handle_back() is True  # pops ForumActivityView
    assert len(courses_view._overlays) == 0


# ---------------------------------------------------------------------------
# Pair 10: PaneAssignmentView + DetailView Mobile 3-Button Wrap + Error Retry
# ---------------------------------------------------------------------------


def test_pair10_pane_assignment_detail_view_mobile_wrap_retry_and_back(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
    tmp_path: Any,
) -> None:
    """Pair 10 (F9, F10): Assignment error -> retry -> DetailView mobile wrap -> handle_back."""
    platform_profile("android", page=fake_page)
    fake_page.resize(360, 780)

    attempts = {"count": 0}
    # Real Flet owns a persistent event loop. Drain this fake page's queued
    # jobs explicitly instead of cancelling them when asyncio.run() returns.
    fake_page.eager_tasks = False

    def _flaky_fetcher(payload: dict[str, Any]) -> dict[str, Any]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ConnectionError("Timeout loading assignment")
        return build_assignment_payload(
            cmid=int(payload.get("cmid") or 401),
            title="Bài tập tuần 5",
        )

    course = make_course(101)
    assign_mod = make_module(401, modname="assign", name="Bài tập tuần 5")
    catalog = FakeCatalogService(
        courses=[course],
        detail_map={
            ("courses", 101): CourseDetailSnapshot(
                sections=[make_section(1, modules=(assign_mod,))],
                grades=[],
                last_synced_at=1_770_000_000,
            )
        },
    )

    coord = CoursesCoordinator(
        page=fake_page,  # type: ignore[arg-type]
        catalog_service=catalog,  # type: ignore[arg-type]
        preview_cache=PreviewFileCache(tmp_path),
        archive_service=ArchiveService(),
        preview_server=preview_server,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda u: fake_page.launch_url(u),
        assignment_detail_fetcher=_flaky_fetcher,
    )
    coord.courses_view.set_viewport_size(360, 780)
    asyncio.run(coord.ensure_loaded_async())
    asyncio.run(coord.courses_view._on_user_select_course_async(course.course_key))
    asyncio.run(coord.courses_view._handle_open_module_async(assign_mod))
    handler, args, kwargs = fake_page.scheduled_tasks[-1]
    asyncio.run(handler(*args, **kwargs))

    assert coord.courses_view._overlays
    pane_assign = coord.courses_view._overlays[-1]
    assert isinstance(pane_assign, PaneAssignmentView)
    assert attempts["count"] == 1
    assert pane_assign._error_strip.visible is True
    assert_valid_flet_mro(coord.courses_view)
    assert_no_fixed_width_overflow(
        coord.courses_view, 360.0, allow_horizontal_scroll_descendants=True
    )

    retry_btn = _find_button_with_text(pane_assign, "Thử lại")
    retry_btn.on_click(None)
    handler, args, kwargs = fake_page.scheduled_tasks[-1]
    asyncio.run(handler(*args, **kwargs))

    assert attempts["count"] == 2
    assert pane_assign._error_strip.visible is False
    assert pane_assign._detail_view._submission_actions_row.wrap is True
    assert_valid_flet_mro(coord.courses_view)
    assert_no_fixed_width_overflow(
        coord.courses_view, 360.0, allow_horizontal_scroll_descendants=True
    )

    assert coord.courses_view.handle_back() is True
    assert len(coord.courses_view._overlays) == 0


# ---------------------------------------------------------------------------
# Pair 11: Legacy OLE2 .doc/.ppt + Tier B Fallback + Android FilePicker Save
# ---------------------------------------------------------------------------


def test_pair11_legacy_ole2_doc_ppt_tier_b_fallback_and_android_filepicker(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pair 11 (F5, F6, F7, F8): Legacy OLE2 .doc/.ppt degrades gracefully & saves safely on Android."""
    platform_profile("android", page=fake_page)
    fake_page.resize(360, 780)

    saved_files: list[str] = []
    launched: list[str] = []

    async def _fake_save(self: Any, **_kwargs: Any) -> str:
        saved_files.append(str(_kwargs.get("file_name") or ""))
        return "/storage/Download/ok"

    async def _fake_launch(self: Any, url: Any, **_kwargs: Any) -> None:
        launched.append(str(url))

    monkeypatch.setattr(ft.FilePicker, "save_file", _fake_save)
    monkeypatch.setattr(ft.UrlLauncher, "launch_url", _fake_launch)

    doc_ole = build_ole2_bytes({"WordDocument": b"Legacy DOC lecture content for UTH students."})
    ppt_ole = build_ole2_bytes({"PowerPoint Document": b"Legacy PPT slide notes."})

    coord = CoursesCoordinator(
        page=fake_page,  # type: ignore[arg-type]
        catalog_service=FakeCatalogService(),  # type: ignore[arg-type]
        preview_cache=PreviewFileCache(tmp_path),
        archive_service=ArchiveService(),
        preview_server=preview_server,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda u: fake_page.launch_url(u),
    )

    for fname, raw, kind in (
        ("old_notes.doc", doc_ole, "docx"),
        ("old_slides.ppt", ppt_ole, "pptx"),
    ):
        reader = DocumentReaderView(
            file_name=fname,
            data=raw,
            kind=kind,  # type: ignore[arg-type]
            preview_server=preview_server,
            on_close=lambda: None,
            on_save=lambda n, b: coord._schedule(coord._save_bytes_async, n, b),
            on_open_external=lambda n, b: coord._schedule(coord._open_external_async, n, b),
            pane_width=360.0,
        )
        texts = "\n".join(collect_texts(reader))
        assert "BadZipFile" not in texts
        assert "Traceback" not in texts
        assert_valid_flet_mro(reader)
        assert_no_fixed_width_overflow(
            reader, 360.0, allow_horizontal_scroll_descendants=True
        )
        reader._ext_btn.on_click(None)

    assert saved_files == ["old_notes.doc", "old_slides.ppt"]
    assert not any(u.startswith("file://") for u in launched)


# ---------------------------------------------------------------------------
# Pair 12: Offline Catalog Error + Multi-Item Recent + Mobile Ergonomics
# ---------------------------------------------------------------------------


def test_pair12_offline_catalog_retry_multi_recent_and_mobile_ergonomics(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
) -> None:
    """Pair 12 (F11): Offline retry CTA + multi-recent expand + >=44dp targets + no (Ctrl+K) hint."""
    platform_profile("android", page=fake_page)
    fake_page.resize(360, 780)

    courses = [
        make_course(101, display_name="Nhập môn AI", is_pinned=True),
        make_course(102, display_name="Cơ sở dữ liệu"),
    ]
    recents = [
        make_recent(cmid=11, title="Slide 1", file_name="slide1.pptx", opened_at=300),
        make_recent(cmid=12, title="Lab 2", file_name="lab2.docx", opened_at=200),
        make_recent(cmid=13, title="Đề cương", file_name="syllabus.pdf", opened_at=100),
    ]
    catalog = FakeCatalogService(courses=courses, recent=recents)
    catalog.last_courses_error = "Không thể kết nối máy chủ Moodle UTH"

    refresh_calls = {"count": 0}
    orig_refresh = catalog.refresh_catalog

    def _tracked_refresh(include_thnn: bool = False) -> list[Any]:
        refresh_calls["count"] += 1
        return orig_refresh(include_thnn)

    catalog.refresh_catalog = _tracked_refresh  # type: ignore[method-assign]

    courses_view = CoursesTabView(page=fake_page, catalog_service=catalog)  # type: ignore[arg-type]
    courses_view.set_viewport_size(360, 780)
    courses_view.load_initial()

    list_pane = courses_view._list_pane
    hint = str(list_pane._search_field.hint_text or "")
    assert "(Ctrl+K)" not in hint

    list_texts = "\n".join(collect_texts(list_pane))
    assert "Không thể kết nối máy chủ Moodle UTH" in list_texts
    retry_btn = _find_button_with_text(list_pane, "Thử lại")
    retry_btn.on_click(None)
    assert refresh_calls["count"] >= 1

    # Recent materials expand button must meet >=44dp touch target and reveal items 2 & 3
    expand_recent_btn = _find_icon_button(list_pane, "Xem thêm 2 mục gần đây")
    assert_min_touch_target(expand_recent_btn, 44.0)
    assert "lab2.docx" not in "\n".join(collect_texts(list_pane))
    expand_recent_btn.on_click(None)
    expanded_texts = "\n".join(collect_texts(list_pane))
    assert "lab2.docx" in expanded_texts
    assert "syllabus.pdf" in expanded_texts

    # Pin icon buttons on mobile course cards must meet >=44dp touch target
    pin_btns = [
        c
        for c in walk_controls(list_pane)
        if isinstance(c, ft.IconButton)
        and ("ghim" in str(c.tooltip or "").lower())
    ]
    assert len(pin_btns) == 2
    for pin_btn in pin_btns:
        assert_min_touch_target(pin_btn, 44.0)

    assert_valid_flet_mro(courses_view)
    assert_no_fixed_width_overflow(
        courses_view, 360.0, allow_horizontal_scroll_descendants=True
    )
