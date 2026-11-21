"""Tier 4 real-world student mobile E2E workflow scenarios (F1-F11)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import flet as ft
import pytest

from core.courses.archive_service import ArchiveService
from core.courses.cache_store import CourseDetailSnapshot
from core.courses.document_converter import convert_document_bytes
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.preview_cache import PreviewFileCache
from gui.components.courses.activity_views import (
    AttendanceActivityView,
    ForumActivityView,
    GroupActivityView,
    PaneAssignmentView,
    QuizActivityView,
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
    build_pdf_bytes,
    build_pptx_bytes,
    build_zip_bytes,
    make_attachment,
    make_course,
    make_grade,
    make_module,
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


def _find_button_with_text(root: Any, text_substr: str) -> ft.BaseControl:
    btn_types = (ft.Button, ft.OutlinedButton, ft.TextButton, ft.FilledButton)
    for ctrl in walk_controls(root):
        if isinstance(ctrl, btn_types):
            content = getattr(ctrl, "content", None)
            label = content.value if isinstance(content, ft.Text) else str(content or "")
            if text_substr in label:
                return ctrl
    raise AssertionError(f"Button containing {text_substr!r} not found")


def _find_clickable_outline_item(root: Any, text_substr: str) -> ft.BaseControl:
    btn_types = (ft.Button, ft.OutlinedButton, ft.TextButton, ft.FilledButton)
    for ctrl in walk_controls(root):
        if isinstance(ctrl, btn_types) and any(text_substr in t for t in collect_texts(ctrl)):
            return ctrl
        if (
            isinstance(ctrl, ft.ListTile)
            and callable(getattr(ctrl, "on_click", None))
            and any(text_substr in t for t in collect_texts(ctrl))
        ):
            return ctrl
    matched_containers = [
        ctrl
        for ctrl in walk_controls(root)
        if isinstance(ctrl, ft.Container)
        and callable(getattr(ctrl, "on_click", None))
        and any(text_substr in t for t in collect_texts(ctrl))
    ]
    if matched_containers:
        return matched_containers[-1]
    raise AssertionError(
        f"Clickable outline item (Button/ListTile/Container) containing {text_substr!r} not found"
    )


def _find_chip(root: Any, text_substr: str) -> ft.Chip:
    for ctrl in walk_controls(root):
        if isinstance(ctrl, ft.Chip):
            lbl = getattr(ctrl, "label", None)
            lbl_text = lbl.value if isinstance(lbl, ft.Text) else str(lbl or "")
            if text_substr in lbl_text:
                return ctrl
    raise AssertionError(f"Chip containing {text_substr!r} not found")


# ---------------------------------------------------------------------------
# Scenario 1: 360dp Android Phone -> Search/Filter -> Multi-Page DOCX + Dark Mode + Back
# ---------------------------------------------------------------------------


def test_scenario1_mobile_course_search_docx_lecture_dark_mode_and_back(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 1 (F2, F3, F6, F8, F9, F10, F11): Full phone DOCX lecture reading journey."""
    from core.courses.office_viewer_service import prepare_office_viewer
    from gui.components.courses.embedded_webview_host import EmbeddedWebViewHost

    platform_profile("android", page=fake_page)
    fake_page.resize(360, 780)
    bind_control_page(monkeypatch, ft.BaseControl, fake_page)

    nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    nav_shell.apply_width(360)
    nav_shell.apply_height(780)
    nav_shell.set_active("courses")

    wide_table = [
        ["STT", "Thuật toán", "Độ phức tạp", "Bộ nhớ", "Ứng dụng", "Ghi chú"],
        ["1", "QuickSort", "O(n log n)", "O(log n)", "Sắp xếp", "Phổ biến"],
        ["2", "MergeSort", "O(n log n)", "O(n)", "Ổn định", "Linked list"],
    ]
    docx_bytes = build_docx_bytes(
        headings=[
            (1, "Chương 1: Phân tích thuật toán"),
            (2, "1.1 Phương trình đặc trưng"),
            (2, "1.2 Bảng so sánh"),
        ],
        paragraphs=[
            "Sinh viên cần nắm vững cách đánh giá độ phức tạp thời gian.",
            "Xem chi tiết trong bảng 6 cột bên dưới.",
        ],
        omml_equations=["T(n) = 2T(n/2) + n", "E = mc^2"],
        tables=[wide_table],
        include_header_footer=True,
        page_break_between=True,
    )

    c_python = make_course(
        101,
        display_name="Cấu trúc dữ liệu & Giải thuật",
        semester_label="HKII 2025-2026",
        is_current_term=True,
    )
    c_old = make_course(
        102,
        display_name="Tin học cơ sở",
        semester_label="HKI 2024-2025",
        semester_sort_key=(2024, 1),
        is_current_term=False,
    )
    docx_att = make_attachment(
        "BaiGiang_Tuan03.docx",
        "https://courses.ut.edu.vn/pluginfile.php/101/BaiGiang_Tuan03.docx",
        file_size=len(docx_bytes),
    )
    docx_mod = make_module(11, name="Bài giảng tuần 3 (Word)", attachments=(docx_att,))

    catalog = FakeCatalogService(
        courses=[c_python, c_old],
        detail_map={
            ("courses", 101): CourseDetailSnapshot(
                sections=[make_section(1, modules=(docx_mod,))],
                grades=[],
                last_synced_at=1_770_000_000,
            )
        },
        attachment_bytes_map={docx_att.file_url: docx_bytes},
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
    courses_view = coord.courses_view
    courses_view.set_on_immersive_changed(nav_shell.set_immersive)
    courses_view.set_viewport_size(360, 780)
    asyncio.run(coord.ensure_loaded_async())

    # 1. Smart default current-term filter + search
    assert courses_view._state.semester_filter == "current"
    assert len(courses_view._state.filtered_courses()) == 1
    courses_view._handle_filter_change("current", "Giải thuật")
    assert [c.id for c in courses_view._state.filtered_courses()] == [101]
    assert nav_shell.bar.visible is True

    # 2. Open course -> verify mobile detail pane
    asyncio.run(courses_view._on_user_select_course_async(c_python.course_key))
    assert courses_view._state.in_detail_mobile is True
    assert_valid_flet_mro(courses_view)
    assert_no_fixed_width_overflow(courses_view, 360.0)

    # 3. Verify Tier A OfficeViewerService + LocalPreviewServer serving + Tier B wide table
    descriptor = prepare_office_viewer(docx_bytes, "BaiGiang_Tuan03.docx", preview_server)
    assert descriptor is not None
    viewer_url = getattr(descriptor, "viewer_url", None) or getattr(descriptor, "url", "")
    assert "/viewer/" in viewer_url and "docx_host.html" in viewer_url
    v_status, v_hdrs, _v_body = http_request(viewer_url)
    assert v_status == 200
    assert "script-src 'self'" in v_hdrs.get("content-security-policy", "")
    src_qs = parse_qs(urlsplit(viewer_url).query).get("src") or []
    assert src_qs and http_request(src_qs[0])[2] == docx_bytes

    converted = convert_document_bytes("BaiGiang_Tuan03.docx", docx_bytes, "docx")
    assert len(converted.outline) >= 3
    assert any("QuickSort" in " ".join(r) for b in converted.docx_blocks if b.kind == "table" for r in b.rows)

    # 4. Open DOCX in DocumentReaderView with Tier A EmbeddedWebViewHost + toggle dark mode
    js_scripts: list[str] = []

    class _RecordingHost(EmbeddedWebViewHost):
        def __init__(self, url: str) -> None:
            super().__init__(
                backend="flet_webview",
                page=fake_page,
                url=url,
                webview_control=ft.Container(expand=True),
            )

        async def eval_js(self, script: str) -> None:
            js_scripts.append(script)

    reader = DocumentReaderView(
        file_name="BaiGiang_Tuan03.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=courses_view.pop_overlay,
        on_save=lambda _n, _b: None,
        on_open_external=lambda _n, _b: None,
        webview_builder=lambda url: _RecordingHost(url),
        pane_width=360.0,
    )
    courses_view.push_overlay(reader, tag="reader:BaiGiang_Tuan03.docx")
    assert nav_shell.bar.visible is False
    assert_valid_flet_mro(courses_view)
    assert_no_fixed_width_overflow(
        courses_view, 360.0, allow_horizontal_scroll_descendants=True
    )

    reader._night_btn.on_click(None)
    assert js_scripts, "Dark mode toggle in Tier A DOCX reader must execute eval_js"

    # Also verify Tier B rendering of the 6-col wide table, zoom, and search at 360dp
    tier_b_reader = DocumentReaderView(
        file_name="BaiGiang_Tuan03.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda _n, _b: None,
        on_open_external=lambda _n, _b: None,
        webview_builder=None,
        pane_width=360.0,
    )
    zoom_in_btn = _find_icon_button(tier_b_reader, "Phóng to")
    prev_scale = tier_b_reader._docx_scale
    zoom_in_btn.on_click(None)
    assert tier_b_reader._docx_scale > prev_scale
    assert f"{int(round(tier_b_reader._docx_scale * 100))}%" in "\n".join(
        collect_texts(tier_b_reader)
    )

    search_fields = [
        c
        for c in walk_controls(tier_b_reader)
        if isinstance(c, ft.TextField)
        and "tìm" in str(getattr(c, "hint_text", "") or "").lower()
        and getattr(c, "visible", True) is not False
    ]
    if not search_fields:
        toggle_btns = [
            c
            for c in walk_controls(tier_b_reader)
            if isinstance(c, ft.IconButton)
            and "tìm" in str(getattr(c, "tooltip", "") or "").lower()
        ]
        assert toggle_btns, "Expected DOCX search field or search toggle button"
        toggle_btns[0].on_click(None)
        search_fields = [
            c
            for c in walk_controls(tier_b_reader)
            if isinstance(c, ft.TextField)
            and "tìm" in str(getattr(c, "hint_text", "") or "").lower()
        ]
    assert search_fields
    search_fields[0].value = "QuickSort"
    search_fields[0].on_submit(type("E", (), {"control": search_fields[0]})())
    search_texts = "\n".join(collect_texts(tier_b_reader))
    assert "QuickSort" in search_texts
    assert "Khớp:" in search_texts

    assert_valid_flet_mro(tier_b_reader)
    assert_no_fixed_width_overflow(
        tier_b_reader, 360.0, allow_horizontal_scroll_descendants=True
    )

    # 5. Android Back -> Detail -> Catalog (immersive restored & list preserved)
    assert courses_view.handle_back() is True
    assert nav_shell.bar.visible is True
    assert courses_view._state.in_detail_mobile is True

    assert courses_view.handle_back() is True
    assert courses_view._state.in_detail_mobile is False
    assert courses_view._state.search_query == "Giải thuật"
    assert len(courses_view._list_pane._card_map) == 1
    assert courses_view.handle_back() is False


# ---------------------------------------------------------------------------
# Scenario 2: 16:9 PPTX Deck (Charts, Tables, Notes) + Safe Android Save + Exit
# ---------------------------------------------------------------------------


def test_scenario2_pptx_16_9_deck_slides_notes_android_save_and_exit(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 2 (F2, F3, F5, F7, F8, F9, F11): 16:9 PPTX review + Android save without file://."""
    import flet_webview as fwv
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

    nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    nav_shell.apply_width(390)

    pptx_bytes = build_pptx_bytes(
        slides=[
            ("Tổng quan hệ phân tán", "Kiến trúc microservices và message queue"),
            ("Hiệu năng & Benchmark", "Throughput và latency theo từng kịch bản"),
            ("Tổng kết môn học", "Ôn tập chuẩn bị thi cuối kỳ"),
        ],
        aspect="16:9",
        include_notes=True,
        include_table=True,
        include_chart=True,
    )

    saved_records: list[tuple[str, bytes]] = []
    launched_urls: list[str] = []

    async def _fake_save_file(self: Any, **kwargs: Any) -> str:
        saved_records.append((str(kwargs.get("file_name") or ""), bytes(kwargs.get("src_bytes") or b"")))
        return "/storage/emulated/0/Download/slides.pptx"

    async def _fake_launch_url(self: Any, url: Any, **_kwargs: Any) -> None:
        launched_urls.append(str(url))

    monkeypatch.setattr(ft.FilePicker, "save_file", _fake_save_file)
    monkeypatch.setattr(ft.UrlLauncher, "launch_url", _fake_launch_url)

    course = make_course(201, display_name="Hệ phân tán")
    att = make_attachment(
        "HePhanTan_16x9.pptx",
        "https://courses.ut.edu.vn/pluginfile.php/201/HePhanTan_16x9.pptx",
        file_size=len(pptx_bytes),
    )
    mod = make_module(21, name="Slide bài giảng 16:9", attachments=(att,))
    catalog = FakeCatalogService(
        courses=[course],
        detail_map={
            ("courses", 201): CourseDetailSnapshot(
                sections=[make_section(1, modules=(mod,))],
                grades=[],
                last_synced_at=1_770_000_000,
            )
        },
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
    coord.courses_view.set_on_immersive_changed(nav_shell.set_immersive)
    coord.courses_view.set_viewport_size(390, 844)
    asyncio.run(coord.ensure_loaded_async())
    asyncio.run(coord.courses_view._on_user_select_course_async(course.course_key))
    asyncio.run(coord.open_attachment_async(course, mod, att))

    assert nav_shell.bar.visible is False
    assert coord.courses_view._overlays
    reader = coord.courses_view._overlays[-1]
    assert isinstance(reader, DocumentReaderView)

    # Verify coordinator-opened PPTX DocumentReaderView actually mounts Tier A
    assert built_hosts, "coord.open_attachment_async must mount Tier A via build_embedded_webview_host for PPTX"
    mounted_url, mounted_host = built_hosts[-1]
    assert "/viewer/" in mounted_url and "pptx_host.html" in mounted_url
    assert mounted_host is not None
    assert mounted_host.backend == "flet_webview"
    assert any(isinstance(c, fwv.WebView) for c in walk_controls(reader))
    assert http_request(mounted_url)[0] == 200

    # Verify Tier A PPTX descriptor & /viewer/pptx_host.html serving
    desc = prepare_office_viewer(pptx_bytes, "HePhanTan_16x9.pptx", preview_server)
    assert desc is not None
    v_url = getattr(desc, "viewer_url", None) or getattr(desc, "url", "")
    assert "/viewer/" in v_url and "pptx_host.html" in v_url
    assert http_request(v_url)[0] == 200

    # Verify Tier B slide extraction includes speaker notes, table, and all 3 slides
    tier_b_reader = DocumentReaderView(
        file_name="HePhanTan_16x9.pptx",
        data=pptx_bytes,
        kind="pptx",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda _n, _b: None,
        on_open_external=lambda _n, _b: None,
        webview_builder=None,
        pane_width=390.0,
    )
    tier_b_texts = "\n".join(collect_texts(tier_b_reader))
    assert "Slide 1" in tier_b_texts and "Slide 3" in tier_b_texts
    assert "Ghi chú giảng viên slide 1" in tier_b_texts
    assert_valid_flet_mro(tier_b_reader)
    assert_no_fixed_width_overflow(
        tier_b_reader, 390.0, allow_horizontal_scroll_descendants=True
    )

    # Trigger Android safe save & external open
    reader._save_btn.on_click(None)
    reader._ext_btn.on_click(None)
    assert len(saved_records) == 2
    assert all(name == "HePhanTan_16x9.pptx" and data == pptx_bytes for name, data in saved_records)
    assert not any(u.startswith("file://") for u in launched_urls)
    assert not any(u.startswith("file://") for u in fake_page.launched_urls)

    # Exit immersive mode via Back
    assert coord.courses_view.handle_back() is True
    assert nav_shell.bar.visible is True


# ---------------------------------------------------------------------------
# Scenario 3: Nested ZIP Archive -> PDF with Outlines -> Mobile BottomSheet -> Back
# ---------------------------------------------------------------------------


def test_scenario3_nested_zip_archive_pdf_outlines_bottomsheet_and_back(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 3 (F1, F6, F8, F9, F10, F11): Nested ZIP -> PDF modal outline -> step-by-step back."""
    platform_profile("android", page=fake_page)
    fake_page.resize(360, 780)
    bind_control_page(monkeypatch, ft.BaseControl, fake_page)

    pdf_bytes = build_pdf_bytes(
        ["Trang 1: Mở đầu Lab 4", "Trang 2: Cấu hình môi trường", "Trang 3: Bài tập thực hành"],
        outline_titles=[("1. Mở đầu", 0), ("2. Cấu hình", 1), ("3. Thực hành", 2)],
    )
    docx_bytes = build_docx_bytes(paragraphs=["Ghi chú bổ sung bài thực hành tuần 4."])
    zip_bytes = build_zip_bytes(
        {
            "labs/week04/lab_manual.pdf": pdf_bytes,
            "labs/week04/notes.docx": docx_bytes,
            "README.txt": b"Course Lab Archive",
        }
    )

    course = make_course(301, display_name="Thực hành Mạng máy tính")
    att = make_attachment(
        "Lab_Materials.zip",
        "https://courses.ut.edu.vn/pluginfile.php/301/Lab_Materials.zip",
        file_size=len(zip_bytes),
    )
    mod = make_module(31, name="Tài liệu thực hành", attachments=(att,))
    catalog = FakeCatalogService(
        courses=[course],
        detail_map={
            ("courses", 301): CourseDetailSnapshot(
                sections=[make_section(1, modules=(mod,))],
                grades=[],
                last_synced_at=1_770_000_000,
            )
        },
        attachment_bytes_map={att.file_url: zip_bytes},
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
    courses_view = coord.courses_view
    courses_view.set_viewport_size(360, 780)
    asyncio.run(coord.ensure_loaded_async())
    asyncio.run(courses_view._on_user_select_course_async(course.course_key))
    asyncio.run(coord.open_attachment_async(course, mod, att))

    assert len(courses_view._overlays) == 1
    explorer = courses_view._overlays[-1]
    assert isinstance(explorer, ArchiveExplorerView)

    labs_dir = next(n for n in explorer._stack[-1].children if n.name == "labs")
    explorer._open_node(labs_dir)
    week04_dir = next(n for n in explorer._stack[-1].children if n.name == "week04")
    explorer._open_node(week04_dir)
    pdf_node = next(n for n in explorer._stack[-1].children if n.name == "lab_manual.pdf")
    explorer._open_node(pdf_node)

    assert len(courses_view._overlays) == 2
    reader = courses_view._overlays[-1]
    assert isinstance(reader, DocumentReaderView)
    assert_valid_flet_mro(courses_view)
    assert_no_fixed_width_overflow(
        courses_view, 360.0, allow_horizontal_scroll_descendants=True
    )

    outline_btn = _find_icon_button(reader, "Mục lục PDF")
    outline_btn.on_click(None)

    sheet = next(
        (
            d
            for d in [*fake_page.dialogs, *fake_page.overlay]
            if isinstance(d, ft.BottomSheet) and getattr(d, "open", False)
        ),
        None,
    )
    assert sheet is not None, "Expected modal BottomSheet PDF outline on 360dp phone"
    assert "3. Thực hành" in "\n".join(collect_texts(sheet))

    # Jump to page 3 via outline item in BottomSheet
    page3_item = _find_clickable_outline_item(sheet, "3. Thực hành")
    page3_item.on_click(None)
    assert reader._pdf_page_index == 2

    # Selecting a destination dismisses its sheet and preserves the reader.
    assert len(courses_view._overlays) == 2
    assert courses_view._overlays[-1] is reader
    assert sheet.open is False

    # Android Back then pops reader -> week04 -> labs -> root -> detail -> catalog.
    assert courses_view.handle_back() is True
    assert len(courses_view._overlays) == 1
    assert courses_view._overlays[-1] is explorer
    assert len(explorer._stack) == 3

    assert courses_view.handle_back() is True  # week04 -> labs
    assert len(explorer._stack) == 2
    assert courses_view.handle_back() is True  # labs -> root
    assert len(explorer._stack) == 1
    assert courses_view.handle_back() is True  # pops explorer -> CourseDetailPane
    assert len(courses_view._overlays) == 0
    assert courses_view._state.in_detail_mobile is True
    assert courses_view.handle_back() is True  # CourseDetailPane -> Catalog
    assert courses_view._state.in_detail_mobile is False


# ---------------------------------------------------------------------------
# Scenario 4: 50-Q Quiz Review + Collapsed Jump Grid + WebViewPane + Back
# ---------------------------------------------------------------------------


def test_scenario4_quiz_50_questions_review_filters_webview_and_back(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
    tmp_path: Any,
) -> None:
    """Scenario 4 (F2, F4, F9, F10, F11): 50-Q quiz review + CoursesWebViewPane + step-by-step back."""
    platform_profile("android", page=fake_page)
    fake_page.resize(390, 844)

    course = make_course(401, display_name="Xác suất thống kê")
    quiz_mod = make_module(41, modname="quiz", name="Kiểm tra giữa kỳ (50 câu)")
    catalog = FakeCatalogService(
        courses=[course],
        detail_map={
            ("courses", 401): CourseDetailSnapshot(
                sections=[make_section(1, modules=(quiz_mod,))],
                grades=[],
                last_synced_at=1_770_000_000,
            )
        },
    )
    activity_svc = FakeActivityService()

    coord = CoursesCoordinator(
        page=fake_page,  # type: ignore[arg-type]
        catalog_service=catalog,  # type: ignore[arg-type]
        preview_cache=PreviewFileCache(tmp_path),
        archive_service=ArchiveService(),
        preview_server=preview_server,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda u: fake_page.launch_url(u),
        activity_service=activity_svc,  # type: ignore[arg-type]
    )
    courses_view = coord.courses_view
    courses_view.set_viewport_size(390, 844)
    asyncio.run(coord.ensure_loaded_async())
    asyncio.run(courses_view._on_user_select_course_async(course.course_key))
    asyncio.run(courses_view._handle_open_module_async(quiz_mod))

    quiz_view = courses_view._overlays[-1]
    assert isinstance(quiz_view, QuizActivityView)
    _wait_until(
        lambda: quiz_view._summary is not None
        and isinstance(quiz_view._body_host.content, ft.ListView)
    )
    assert "Lịch sử làm bài" in "\n".join(collect_texts(quiz_view))

    # Enter 50-question attempt review
    att_50 = next(a for a in quiz_view._summary.attempts if a.attempt_id == 5001)
    prev_quiz_body = quiz_view._body_host.content
    quiz_view._open_attempt_review(quiz_view._summary, 5001, att_50)
    _wait_until(
        lambda: quiz_view._in_review
        and quiz_view._current_review is not None
        and quiz_view._body_host.content is not prev_quiz_body
        and isinstance(quiz_view._body_host.content, ft.ListView)
    )

    tiles = [c for c in walk_controls(quiz_view) if isinstance(c, ft.ExpansionTile)]
    assert tiles and tiles[0].expanded is False
    wrong_chip = _find_chip(quiz_view, "Câu sai")
    wrong_chip.on_select(None)
    assert_valid_flet_mro(quiz_view)
    assert_no_fixed_width_overflow(quiz_view, 390.0)

    # Open quiz attempt in CoursesWebViewPane
    wv_btn = _find_button_with_text(quiz_view, "WebView")
    wv_btn.on_click(None)
    assert len(courses_view._overlays) == 2
    wv_pane = courses_view._overlays[-1]
    assert isinstance(wv_pane, CoursesWebViewPane)
    assert wv_pane._compact_mode is True
    assert wv_pane._more_menu_btn in wv_pane._header_container.content.controls
    assert wv_pane._mobile_nav_bar.visible is True
    assert wv_pane._ext_btn.visible is True

    # Navigate to loopback preview URL -> external open button must hide on 127.0.0.1
    local_url = preview_server.register_html("<html><body><h1>Quiz Preview</h1></body></html>")
    wv_pane._on_native_url_or_bridge(local_url)
    assert wv_pane._ext_btn.visible is False
    assert wv_pane._more_menu_item_ext.visible is False

    # Step-by-step back: WebView -> Quiz review -> Quiz summary -> Detail -> Catalog
    assert courses_view.handle_back() is True  # pops WebView
    assert courses_view._overlays[-1] is quiz_view
    assert quiz_view._in_review is True
    assert courses_view.handle_back() is True  # review -> summary
    assert quiz_view._in_review is False
    assert courses_view.handle_back() is True  # pops Quiz
    assert len(courses_view._overlays) == 0
    assert courses_view._state.in_detail_mobile is True
    assert courses_view.handle_back() is True  # Detail -> Catalog
    assert courses_view._state.in_detail_mobile is False


# ---------------------------------------------------------------------------
# Scenario 5: Forum -> Group -> Attendance -> Grades -> Assignment on 390x844
# ---------------------------------------------------------------------------


def test_scenario5_forum_group_attendance_grades_and_assignment_on_390dp(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
    tmp_path: Any,
) -> None:
    """Scenario 5 (F9, F10, F11): All core Moodle activities & grades on 390x844 phone."""
    platform_profile("android", page=fake_page)
    fake_page.resize(390, 844)

    course = make_course(501, display_name="Công nghệ Phần mềm")
    forum_mod = make_module(51, modname="forum", name="Diễn đàn môn học")
    group_mod = make_module(52, modname="groupselect", name="Đăng ký nhóm đồ án")
    att_mod = make_module(53, modname="attendance", name="Điểm danh chuyên cần")
    assign_mod = make_module(54, modname="assign", name="Nộp báo cáo giữa kỳ")
    grades = [
        make_grade(1, item_name="Chuyên cần (10%)", grade_formatted="9.50", weight="10 %"),
        make_grade(2, item_name="Giữa kỳ (30%)", grade_formatted="8.25", weight="30 %"),
        make_grade(3, item_name="Tổng kết môn", item_module="course", grade_formatted="8.60"),
    ]

    catalog = FakeCatalogService(
        courses=[course],
        detail_map={
            ("courses", 501): CourseDetailSnapshot(
                sections=[make_section(1, modules=(forum_mod, group_mod, att_mod, assign_mod))],
                grades=grades,
                last_synced_at=1_770_000_000,
            )
        },
    )
    activity_svc = FakeActivityService()

    coord = CoursesCoordinator(
        page=fake_page,  # type: ignore[arg-type]
        catalog_service=catalog,  # type: ignore[arg-type]
        preview_cache=PreviewFileCache(tmp_path),
        archive_service=ArchiveService(),
        preview_server=preview_server,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda u: fake_page.launch_url(u),
        activity_service=activity_svc,  # type: ignore[arg-type]
        assignment_detail_fetcher=lambda p: build_assignment_payload(
            cmid=int(p.get("cmid") or 54), title=str(p.get("title") or "Bài tập")
        ),
    )
    courses_view = coord.courses_view
    courses_view.set_viewport_size(390, 844)
    asyncio.run(coord.ensure_loaded_async())
    asyncio.run(courses_view._on_user_select_course_async(course.course_key))

    # 1. Forum -> drill into discussion -> back
    asyncio.run(courses_view._handle_open_module_async(forum_mod))
    forum_view = courses_view._overlays[-1]
    assert isinstance(forum_view, ForumActivityView)
    _wait_until(
        lambda: forum_view._summary is not None
        and isinstance(forum_view._body_host.content, ft.ListView)
    )
    prev_forum_body = forum_view._body_host.content
    forum_view._open_discussion(forum_view._summary, forum_view._summary.discussions[0])
    _wait_until(
        lambda: forum_view._in_posts
        and forum_view._current_posts is not None
        and forum_view._body_host.content is not prev_forum_body
        and isinstance(forum_view._body_host.content, ft.ListView)
    )
    assert_valid_flet_mro(forum_view)
    assert_no_fixed_width_overflow(forum_view, 390.0)
    assert courses_view.handle_back() is True
    assert courses_view.handle_back() is True

    # 2. Study Group -> join group -> back
    asyncio.run(courses_view._handle_open_module_async(group_mod))
    group_view = courses_view._overlays[-1]
    assert isinstance(group_view, GroupActivityView)
    _wait_until(
        lambda: group_view._summary is not None
        and isinstance(group_view._body_host.content, ft.ListView)
    )
    prev_group_body = group_view._body_host.content
    join_btn = _find_button_with_text(group_view, "Tham gia nhóm")
    join_btn.on_click(None)
    _wait_until(
        lambda: len(activity_svc.submitted_group_actions) == 1
        and group_view._body_host.content is not prev_group_body
        and isinstance(group_view._body_host.content, ft.ListView)
    )
    assert_valid_flet_mro(group_view)
    assert_no_fixed_width_overflow(group_view, 390.0)
    assert courses_view.handle_back() is True

    # 3. Attendance -> submit self-attendance -> back
    asyncio.run(courses_view._handle_open_module_async(att_mod))
    att_view = courses_view._overlays[-1]
    assert isinstance(att_view, AttendanceActivityView)
    _wait_until(
        lambda: att_view._summary is not None
        and isinstance(att_view._body_host.content, ft.ListView)
    )
    prev_att_body = att_view._body_host.content
    submit_att_btn = _find_button_with_text(att_view, "Gửi điểm danh")
    submit_att_btn.on_click(None)
    _wait_until(
        lambda: len(activity_svc.submitted_attendance) == 1
        and att_view._body_host.content is not prev_att_body
        and isinstance(att_view._body_host.content, ft.ListView)
    )
    assert "9/10" in "\n".join(collect_texts(att_view))
    assert_valid_flet_mro(att_view)
    assert_no_fixed_width_overflow(att_view, 390.0)
    assert courses_view.handle_back() is True

    # 4. Grades Hero & rows on 390dp
    asyncio.run(courses_view._on_subtab_change_async(2))
    grades_texts = "\n".join(collect_texts(courses_view._detail_pane))
    assert "8.60" in grades_texts
    assert_valid_flet_mro(courses_view._detail_pane)
    assert_no_fixed_width_overflow(courses_view._detail_pane, 390.0)

    # 5. Assignment submission view on 390dp
    asyncio.run(courses_view._handle_open_module_async(assign_mod))
    pane_assign = courses_view._overlays[-1]
    assert isinstance(pane_assign, PaneAssignmentView)
    assert_valid_flet_mro(pane_assign)
    assert_no_fixed_width_overflow(
        pane_assign, 390.0, allow_horizontal_scroll_descendants=True
    )
    assert courses_view.handle_back() is True


# ---------------------------------------------------------------------------
# Scenario 6: Dynamic Rotation & Resize (360x780 <-> 780x360 <-> 430x932 <-> 1200x800)
# ---------------------------------------------------------------------------


def test_scenario6_dynamic_rotation_and_resize_across_catalog_detail_reader(
    fake_page: FakePage,
    platform_profile: Callable[..., None],
    preview_server: LocalPreviewServer,
) -> None:
    """Scenario 6 (F2, F8, F10, F11): Portrait <-> Landscape <-> Large Phone <-> Desktop transitions."""
    platform_profile("android", page=fake_page)

    nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    course = make_course(601, display_name="Trí tuệ nhân tạo")
    other_course = make_course(602, display_name="Mạng máy tính")
    catalog = FakeCatalogService(
        courses=[course, other_course],
        detail_map={
            ("courses", 601): CourseDetailSnapshot(
                sections=[make_section(1, modules=(make_module(61),))],
                grades=[],
                last_synced_at=1_770_000_000,
            )
        },
    )

    courses_view = CoursesTabView(page=fake_page, catalog_service=catalog)  # type: ignore[arg-type]
    courses_view.set_on_immersive_changed(nav_shell.set_immersive)
    courses_view.load_initial()

    # Step 1: 360x780 portrait + filter course list
    fake_page.resize(360, 780)
    nav_shell.apply_width(360)
    nav_shell.apply_height(780)
    courses_view.set_viewport_size(360, 780)
    assert courses_view._layout_mode == "mobile"
    assert nav_shell.bar.visible is True
    assert nav_shell.rail_host.visible is False
    courses_view._handle_filter_change("all", "Trí tuệ")
    list_pane_id = id(courses_view._list_pane)
    cards_col_id = id(courses_view._list_pane._cards_list)
    assert len(courses_view._list_pane._card_map) == 1

    # Select course and open DocumentReaderView
    asyncio.run(courses_view._on_user_select_course_async(course.course_key))
    docx_bytes = build_docx_bytes(
        headings=["Chương 1"],
        paragraphs=["Tài liệu kiểm thử xoay màn hình."],
    )
    bounds_log: list[tuple[float, float, float, float]] = []
    reader = DocumentReaderView(
        file_name="rotate.docx",
        data=docx_bytes,
        kind="docx",
        preview_server=preview_server,
        on_close=courses_view.pop_overlay,
        on_save=lambda _n, _b: None,
        on_open_external=lambda _n, _b: None,
        pane_width=360.0,
    )
    orig_sync = reader.sync_bounds

    def _spy_sync(l_px: float, t_px: float, w_px: float, h_px: float) -> None:
        bounds_log.append((l_px, t_px, w_px, h_px))
        orig_sync(l_px, t_px, w_px, h_px)

    reader.sync_bounds = _spy_sync  # type: ignore[method-assign]
    courses_view.push_overlay(reader, tag="reader:rotate")
    assert nav_shell.bar.visible is False

    # Step 2: 780x360 landscape
    fake_page.resize(780, 360)
    nav_shell.apply_width(780)
    nav_shell.apply_height(360)
    courses_view.set_viewport_size(780, 360)
    assert courses_view._layout_mode == "tablet_portrait"
    assert nav_shell.rail_host.visible is True
    assert nav_shell.rail.scrollable is True
    assert nav_shell.bar.visible is False
    assert "Thông tin môn" in collect_texts(courses_view._detail_pane)
    assert id(courses_view._list_pane) == list_pane_id
    assert id(courses_view._list_pane._cards_list) == cards_col_id
    assert courses_view._state.search_query == "Trí tuệ"
    assert len(courses_view._list_pane._card_map) == 1
    assert_valid_flet_mro(courses_view)
    assert_no_fixed_width_overflow(
        courses_view, 780.0, allow_horizontal_scroll_descendants=True
    )

    # Step 3: 430x932 large portrait phone
    fake_page.resize(430, 932)
    nav_shell.apply_width(430)
    nav_shell.apply_height(932)
    courses_view.set_viewport_size(430, 932)
    assert courses_view._layout_mode == "mobile"
    assert nav_shell.rail_host.visible is False
    assert nav_shell.bar.visible is False  # still collapsed during immersive overlay
    assert id(courses_view._list_pane) == list_pane_id
    assert courses_view._state.search_query == "Trí tuệ"
    assert len(courses_view._list_pane._card_map) == 1
    assert_valid_flet_mro(courses_view)
    assert_no_fixed_width_overflow(
        courses_view, 430.0, allow_horizontal_scroll_descendants=True
    )

    # Step 4: 1200x800 desktop wide
    fake_page.resize(1200, 800)
    nav_shell.apply_width(1200)
    nav_shell.apply_height(800)
    courses_view.set_viewport_size(1200, 800)
    assert courses_view._layout_mode == "wide"
    assert nav_shell.rail_host.visible is True
    assert nav_shell.rail.scrollable is False
    assert id(courses_view._list_pane) == list_pane_id
    assert id(courses_view._list_pane._cards_list) == cards_col_id
    assert courses_view._state.search_query == "Trí tuệ"
    assert len(courses_view._list_pane._card_map) == 1
    list_texts = "\n".join(collect_texts(courses_view._list_pane))
    assert "Trí tuệ nhân tạo" in list_texts
    assert "Mạng máy tính" not in list_texts
    assert_valid_flet_mro(courses_view)
    assert_no_fixed_width_overflow(
        courses_view, 1200.0, allow_horizontal_scroll_descendants=True
    )

    # Verify sync_bounds tracked each resize cleanly and update_theme works end-to-end
    assert len(bounds_log) >= 4
    apply_theme("steel_light")
    courses_view.update_theme()
    nav_shell.refresh_theme()
    assert courses_view.bgcolor == C.BG
    assert nav_shell.rail_host.bgcolor == C.SURFACE
    assert_valid_flet_mro(courses_view)
