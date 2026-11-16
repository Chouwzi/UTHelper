"""TDD tests for Courses ViewModel state and Flet Master-Detail components."""

from __future__ import annotations

import asyncio
import gc
import threading
import warnings
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import flet as ft
import pytest

from core.courses.cache_store import CourseDetailSnapshot
from core.courses.catalog_service import CourseSyncResult
from core.courses.models import (
    CourseAttachment,
    CourseGradeItem,
    CourseModuleItem,
    CourseSection,
    CourseSummary,
    RecentMaterialEntry,
)
from gui.components.courses import CourseDetailPane, CourseListPane, CoursesTabView
from gui.components.courses.course_list_pane import _safe_progress
from gui.core.theme import C, apply_theme
from gui.view_models.courses_state import (
    CoursesState,
    compute_courses_layout_mode,
    extract_readable_html_and_links,
    flatten_course_resources,
    format_file_size,
    module_matches_type_filter,
    safe_http_url,
)


def _walk_controls(root):
    """Yield every Flet control reachable under ``root``."""
    if not isinstance(root, ft.BaseControl):
        return
    yield root
    for attr in ("content", "title", "subtitle", "leading", "trailing", "label"):
        child = getattr(root, attr, None)
        if isinstance(child, ft.BaseControl):
            yield from _walk_controls(child)
    for attr in ("controls", "tabs", "actions"):
        children = getattr(root, attr, None)
        if isinstance(children, (list, tuple)):
            for child in children:
                yield from _walk_controls(child)


def _make_course(
    course_id: int = 101,
    *,
    source: str = "courses",
    display_name: str = "Lập trình Python",
    course_code: str = "010112345601",
    semester_label: str = "HKII 2025-2026",
    semester_sort_key: tuple[int, int] = (2025, 2),
    program_tag: str = "CLC",
    teacher_name: str = "TS. Nguyễn Văn A",
    teacher_email: str = "nva@ut.edu.vn",
    meet_url: str = "https://meet.google.com/abc-defg-hij",
    progress: float | None = 0.5,
    completed_count: int = 2,
    total_trackable: int = 4,
    web_url: str = "https://courses.ut.edu.vn/course/view.php?id=101",
    last_synced_at: int = 1_770_000_000,
    has_unseen_updates: bool = False,
    is_pinned: bool = False,
) -> CourseSummary:
    return CourseSummary(
        id=course_id,
        source=source,  # type: ignore[arg-type]
        fullname=f"[{program_tag}]_{semester_label}_{display_name}_{course_code}",
        shortname=f"[{program_tag}]_{semester_label}_{display_name}_{course_code}",
        display_name=display_name,
        course_code=course_code,
        semester_label=semester_label,
        semester_sort_key=semester_sort_key,
        program_tag=program_tag,
        teacher_name=teacher_name,
        teacher_email=teacher_email,
        meet_url=meet_url,
        progress=progress,
        completed_count=completed_count,
        total_trackable=total_trackable,
        web_url=web_url,
        last_synced_at=last_synced_at,
        has_unseen_updates=has_unseen_updates,
        is_pinned=is_pinned,
    )


def _make_attachment(
    name: str = "lecture.pdf",
    url: str = "https://courses.ut.edu.vn/pluginfile.php/1/lecture.pdf",
    *,
    size: int = 2048,
    extension: str = "pdf",
    preview_kind: str = "pdf",
    is_archive: bool = False,
) -> CourseAttachment:
    return CourseAttachment(
        file_name=name,
        file_url=url,
        file_size=size,
        extension=extension,
        preview_kind=preview_kind,  # type: ignore[arg-type]
        is_archive=is_archive,
    )


class FakeCatalogService:
    """In-memory stub mirroring CourseCatalogService for UI tests."""

    def __init__(self, courses=None, recent=None, detail_map=None) -> None:
        self.courses = list(courses or [])
        self.recent = list(recent or [])
        self.detail_map: dict[tuple[str, int], CourseDetailSnapshot] = dict(
            detail_map or {}
        )
        self.catalog_synced_at = 1_770_000_000
        self.last_thnn_error = ""
        self.last_courses_error = ""
        self.stale = False
        self.sync_results: dict[tuple[str, int], CourseSyncResult] = {}
        self.grades_map: dict[tuple[str, int], list[CourseGradeItem]] = {}
        self.resolved_urls: dict[int, str] = {}
        self.toggle_completion_ok = True
        self.recorded_recent: list[RecentMaterialEntry] = []

    def load_cached_catalog(self):
        return list(self.courses), list(self.recent), self.catalog_synced_at

    def is_catalog_stale(self, max_age_hours: int) -> bool:
        return self.stale

    def refresh_catalog(self, include_thnn: bool = False):
        return list(self.courses)

    def open_course_cached(self, source: str, course_id: int) -> CourseDetailSnapshot:
        return self.detail_map.get(
            (source, course_id), CourseDetailSnapshot(sections=[], grades=[], last_synced_at=0)
        )

    def sync_course_delta(self, source: str, course_id: int, force: bool = False) -> CourseSyncResult:
        if (source, course_id) in self.sync_results:
            return self.sync_results[(source, course_id)]
        snap = self.open_course_cached(source, course_id)
        return CourseSyncResult(
            sections=list(snap.sections),
            grades=list(snap.grades),
            changed=False,
            new_cmids=set(),
            last_synced_at=snap.last_synced_at,
        )

    def load_course_grades(self, source: str, course_id: int, force: bool = False):
        if (source, course_id) in self.grades_map:
            return list(self.grades_map[(source, course_id)])
        return list(self.open_course_cached(source, course_id).grades)

    def toggle_pin(self, course_key: str):
        self.courses = [
            replace(c, is_pinned=not c.is_pinned) if c.course_key == course_key else c
            for c in self.courses
        ]
        return list(self.courses)

    def mark_course_seen(self, course_key: str):
        self.courses = [
            replace(c, has_unseen_updates=False) if c.course_key == course_key else c
            for c in self.courses
        ]
        return list(self.courses)

    def toggle_completion(self, source: str, course_id: int, cmid: int, completed: bool) -> bool:
        return self.toggle_completion_ok

    def resolve_module_url(self, source: str, module: CourseModuleItem) -> str:
        return self.resolved_urls.get(module.cmid, module.url or module.web_url)

    def record_recent(self, entry: RecentMaterialEntry):
        self.recorded_recent.append(entry)
        self.recent = [entry, *self.recent]
        return list(self.recent)


def test_layout_mode_breakpoints_and_edge_inputs():
    assert compute_courses_layout_mode(1280) == "wide"
    assert compute_courses_layout_mode(980) == "wide"
    assert compute_courses_layout_mode(979.9) == "tablet_portrait"
    assert compute_courses_layout_mode(600) == "tablet_portrait"
    assert compute_courses_layout_mode(599.9) == "mobile"
    assert compute_courses_layout_mode(0) == "mobile"
    assert compute_courses_layout_mode(-50) == "mobile"
    assert compute_courses_layout_mode(float("nan")) == "mobile"
    assert compute_courses_layout_mode(float("inf")) == "mobile"
    assert compute_courses_layout_mode(None) == "mobile"  # type: ignore[arg-type]


def test_format_file_size_boundaries():
    assert format_file_size(0) == ""
    assert format_file_size(-128) == ""
    assert format_file_size(512) == "512 B"
    assert format_file_size(1024) == "1.0 KB"
    assert format_file_size(1536) == "1.5 KB"
    assert format_file_size(1024 * 1024) == "1.0 MB"
    assert format_file_size(1024 ** 3) == "1.0 GB"


def test_flatten_course_resources_and_module_type_filters():
    pdf_mod = CourseModuleItem(
        cmid=1,
        instance_id=1,
        modname="resource",
        name="Slide 01",
        section_id=10,
        attachments=(_make_attachment("slide.pdf", extension="pdf", preview_kind="pdf"),),
        open_strategy="preview_file",
    )
    office_mod = CourseModuleItem(
        cmid=2,
        instance_id=2,
        modname="resource",
        name="Lab Sheet",
        section_id=10,
        attachments=(_make_attachment("lab.xlsx", extension="xlsx", preview_kind="xlsx"),),
        open_strategy="preview_file",
    )
    archive_mod = CourseModuleItem(
        cmid=3,
        instance_id=3,
        modname="folder",
        name="Code Zip",
        section_id=10,
        attachments=(
            _make_attachment(
                "code.zip",
                extension="zip",
                preview_kind="archive",
                is_archive=True,
            ),
        ),
        open_strategy="folder_files",
    )
    link_mod = CourseModuleItem(
        cmid=4,
        instance_id=4,
        modname="googlemeet",
        name="Phòng học Meet",
        section_id=10,
        url="https://meet.google.com/aaa-bbbb-ccc",
        open_strategy="external_url",
    )
    assign_mod = CourseModuleItem(
        cmid=5,
        instance_id=5,
        modname="assign",
        name="Bài tập 1",
        section_id=10,
        open_strategy="assignment_detail",
    )
    attend_mod = CourseModuleItem(
        cmid=6,
        instance_id=6,
        modname="attendance",
        name="Điểm danh",
        section_id=10,
        open_strategy="webview_activity",
    )
    page_mod = CourseModuleItem(
        cmid=7,
        instance_id=7,
        modname="page",
        name="Giới thiệu môn",
        section_id=10,
        inline_html="<p>Xin chào</p>",
        open_strategy="inline_page",
    )
    sec = CourseSection(
        id=10,
        course_id=101,
        source="courses",
        section_num=1,
        name="Tuần 1",
        modules=(pdf_mod, office_mod, archive_mod, link_mod, assign_mod, attend_mod, page_mod),
    )

    assert [m.cmid for _, m in flatten_course_resources([sec], kind_filter="all")] == [1, 2, 3, 4]
    assert [m.cmid for _, m in flatten_course_resources([sec], kind_filter="pdf")] == [1]
    assert [m.cmid for _, m in flatten_course_resources([sec], kind_filter="office")] == [2]
    assert [m.cmid for _, m in flatten_course_resources([sec], kind_filter="archive")] == [3]
    assert [m.cmid for _, m in flatten_course_resources([sec], kind_filter="link")] == [4]

    assert module_matches_type_filter(pdf_mod, "materials")
    assert module_matches_type_filter(page_mod, "materials")
    assert not module_matches_type_filter(assign_mod, "materials")
    assert module_matches_type_filter(assign_mod, "tasks")
    assert module_matches_type_filter(attend_mod, "attendance")
    assert module_matches_type_filter(link_mod, "links")
    assert module_matches_type_filter(assign_mod, "all")


def test_courses_state_filters_semesters_and_selection():
    c1 = _make_course(
        1,
        display_name="Cấu trúc dữ liệu",
        course_code="010100112201",
        semester_label="HKII 2025-2026",
        semester_sort_key=(2025, 2),
        teacher_name="ThS. Trần B",
    )
    c2 = _make_course(
        2,
        source="thnn",
        display_name="Thực hành mạng",
        course_code="010100998801",
        semester_label="HKI 2025-2026",
        semester_sort_key=(2025, 3),
        program_tag="THNN",
        teacher_name="TS. Lê C",
    )
    c3 = _make_course(
        3,
        display_name="Toán rời rạc",
        course_code="010100334401",
        semester_label="Khác",
        semester_sort_key=(0, 0),
    )

    state = CoursesState()
    state.set_courses([c1, c2, c3])

    assert state.available_semesters() == ["HKI 2025-2026", "HKII 2025-2026", "Khác"]
    assert state.selected_course() is None

    state.select_course("thnn:2")
    assert state.selected_course_key == "thnn:2"
    assert state.in_detail_mobile is True
    assert state.selected_course() == c2

    state.semester_filter = "THNN"
    assert state.filtered_courses() == [c2]

    state.semester_filter = "HKII 2025-2026"
    assert state.filtered_courses() == [c1]

    state.semester_filter = "all"
    state.search_query = "lê c"
    assert state.filtered_courses() == [c2]
    state.search_query = "010100334401"
    assert state.filtered_courses() == [c3]


def test_course_list_pane_renders_search_chips_recent_progress_and_focus():
    on_select = Mock()
    on_pin = Mock()
    on_recent = Mock()
    on_filter = Mock()
    on_refresh = Mock()

    pane = CourseListPane(
        on_select_course=on_select,
        on_toggle_pin=on_pin,
        on_open_recent=on_recent,
        on_filter_change=on_filter,
        on_refresh_catalog=on_refresh,
    )
    c1 = _make_course(1, is_pinned=True, has_unseen_updates=True, progress=0.75)
    c2 = _make_course(2, source="thnn", display_name="THNN Cisco", program_tag="THNN")
    recent = RecentMaterialEntry(
        course_key="courses:1",
        course_name="Lập trình Python",
        cmid=11,
        title="Slide Chương 2",
        attachment_url="https://courses.ut.edu.vn/pluginfile.php/1/c2.pdf",
        file_name="c2.pdf",
        opened_at=1_770_000_000,
    )
    state = CoursesState(
        courses=[c1, c2],
        recent_items=[recent],
        selected_course_key="courses:1",
        offline_notice="Đang dùng dữ liệu ngoại tuyến",
    )
    pane.render(state)

    controls = list(_walk_controls(pane))
    text_fields = [c for c in controls if isinstance(c, ft.TextField)]
    assert len(text_fields) == 1
    assert text_fields[0].hint_text == "Tìm tên môn, mã HP, giảng viên... (Ctrl+K)"

    chips = [c for c in controls if isinstance(c, ft.Chip)]
    chip_labels = [c.label.value if isinstance(c.label, ft.Text) else str(c.label) for c in chips]
    assert "Tất cả" in chip_labels
    assert "THNN" in chip_labels

    texts = [c.value for c in controls if isinstance(c, ft.Text) and isinstance(c.value, str)]
    assert any("Đang dùng dữ liệu ngoại tuyến" in t for t in texts)
    assert any("Tiếp tục nhanh" in t for t in texts)

    bars = [c for c in controls if isinstance(c, ft.ProgressBar)]
    assert any(bar.bar_height == 3 and bar.value == 0.75 for bar in bars)

    # Verify focus_search awaits _search_field.focus()
    focus_mock = AsyncMock()
    pane._search_field.focus = focus_mock
    asyncio.run(pane.focus_search())
    focus_mock.assert_awaited_once()

    pane.update_theme()


def test_course_detail_pane_checkbox_rule_folder_summary_teacher_copy_and_empty_grades():
    on_back = Mock()
    on_refresh = Mock()
    on_open_mod = Mock()
    on_download = Mock()
    on_toggle = Mock()
    on_copy = Mock()
    on_launch = Mock()
    on_subtab = Mock()

    pane = CourseDetailPane(
        on_back_mobile=on_back,
        on_refresh_course=on_refresh,
        on_open_module=on_open_mod,
        on_download_attachment=on_download,
        on_toggle_completion=on_toggle,
        on_copy_text=on_copy,
        on_launch_url=on_launch,
        on_subtab_change=on_subtab,
    )

    course = _make_course(
        55,
        source="thnn",
        display_name="THNN Mạng",
        teacher_name="TS. Phạm D",
        teacher_email="phamd@ut.edu.vn",
        meet_url="https://meet.google.com/xyz-1234-abc",
        web_url="",  # Empty web_url tests fallback origin computation in Tab 2
    )
    mod_manual = CourseModuleItem(
        cmid=101,
        instance_id=1,
        modname="folder",
        name="Tài liệu chương 1",
        section_id=1,
        attachments=(
            _make_attachment("a.pdf"),
            _make_attachment("b.docx", extension="docx", preview_kind="docx"),
        ),
        completion_mode=1,
        completion_state=1,
        open_strategy="folder_files",
        is_new=True,
    )
    mod_auto = CourseModuleItem(
        cmid=102,
        instance_id=2,
        modname="assign",
        name="Nộp lab 1",
        section_id=1,
        completion_mode=2,
        completion_state=0,
        open_strategy="assignment_detail",
    )
    mod_none = CourseModuleItem(
        cmid=103,
        instance_id=3,
        modname="page",
        name="Hướng dẫn",
        section_id=2,
        completion_mode=0,
        open_strategy="inline_page",
    )
    sec1 = CourseSection(id=1, course_id=55, source="thnn", section_num=1, name="Tuần 1", modules=(mod_manual, mod_auto))
    sec2 = CourseSection(id=2, course_id=55, source="thnn", section_num=2, name="Tuần 2", modules=(mod_none,))

    state = CoursesState(
        courses=[course],
        selected_course_key=course.course_key,
        sections=[sec1, sec2],
        grades=[],
        last_synced_at=1_770_000_000,
        active_subtab=0,
    )
    pane.render(state, show_back=True)

    assert pane._back_btn.visible is True
    controls = list(_walk_controls(pane))

    # Only completion_mode == 1 gets a Checkbox inside Tab 0 (completion_mode 0 and 2 do not)
    content_controls = list(_walk_controls(pane._content_tab_body))
    checkboxes = [c for c in content_controls if isinstance(c, ft.Checkbox)]
    assert len(checkboxes) == 1
    assert checkboxes[0].value is True

    # Tab 1 (Tài liệu) renders flattened resource/folder rows
    resource_controls = list(_walk_controls(pane._resources_tab_body))
    resource_texts = [c.value for c in resource_controls if isinstance(c, ft.Text) and isinstance(c.value, str)]
    assert any("Tài liệu chương 1" in t for t in resource_texts)

    # Multi-file folder summary shows "2 tệp"
    texts = [c.value for c in controls if isinstance(c, ft.Text) and isinstance(c.value, str)]
    assert any("2 tệp" in t for t in texts)

    # First section and section with is_new module should be expanded
    tiles = [c for c in controls if isinstance(c, ft.ExpansionTile)]
    assert len(tiles) == 2
    assert tiles[0].expanded is True
    assert tiles[1].expanded is False

    # Teacher chip copies teacher_email
    teacher_chips = [
        c for c in controls
        if isinstance(c, ft.Chip) and c.on_click is not None and "Phạm D" in (c.label.value if isinstance(c.label, ft.Text) else "")
    ]
    assert len(teacher_chips) == 1
    teacher_chips[0].on_click(None)
    on_copy.assert_called_once_with("phamd@ut.edu.vn")

    # Empty grades fallback URL uses https://thnn.ut.edu.vn when web_url is empty
    grade_buttons = [
        c for c in controls
        if isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton))
        and "Xem điểm trên Moodle" in str(getattr(c, "content", ""))
    ]
    assert len(grade_buttons) == 1
    grade_buttons[0].on_click(None)
    on_launch.assert_called_with("https://thnn.ut.edu.vn/grade/report/user/index.php?id=55")

    # Non-empty web_url computes origin via urlsplit
    course_courses = _make_course(
        77,
        source="courses",
        web_url="https://lms.example.edu.vn/course/view.php?id=77&section=2",
    )
    state_non_empty = CoursesState(
        courses=[course_courses],
        selected_course_key=course_courses.course_key,
        grades=[],
    )
    pane.render(state_non_empty, show_back=False)
    assert pane._back_btn.visible is False
    controls2 = list(_walk_controls(pane))
    grade_buttons2 = [
        c for c in controls2
        if isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton))
        and "Xem điểm trên Moodle" in str(getattr(c, "content", ""))
    ]
    grade_buttons2[0].on_click(None)
    on_launch.assert_called_with("https://lms.example.edu.vn/grade/report/user/index.php?id=77")

    pane.update_theme()


def test_courses_tab_view_breakpoints_overlays_and_back():
    svc = FakeCatalogService(courses=[_make_course(1)])
    view = CoursesTabView(
        page=None,
        catalog_service=svc,
        on_open_assignment=Mock(),
        on_open_webview_activity=Mock(),
        on_open_attachment_viewer=Mock(),
        on_download_attachment=Mock(),
        on_launch_url=Mock(),
    )
    assert view.control is view

    view.load_initial()
    assert len(view._state.courses) == 1

    view.set_width(1100)
    assert view._layout_mode == "wide"

    view.set_width(750)
    assert view._layout_mode == "tablet_portrait"

    view.set_width(400)
    assert view._layout_mode == "mobile"

    view._state.select_course("courses:1")
    view._render()
    assert view._state.in_detail_mobile is True

    overlay = ft.Container(content=ft.Text("Reader"))
    view.push_overlay(overlay)
    assert view.handle_back() is True  # Pops overlay first
    assert view._state.in_detail_mobile is True
    assert view.handle_back() is True  # Exits mobile detail
    assert view._state.in_detail_mobile is False
    assert view.handle_back() is False

    apply_theme("midnight_blue")
    view.update_theme()


def test_courses_tab_view_stale_selection_guards_and_error_surfacing():
    c1 = _make_course(1, display_name="Course One")
    c2 = _make_course(2, display_name="Course Two")
    sec1 = CourseSection(id=10, course_id=1, source="courses", section_num=1, name="S1")
    sec2 = CourseSection(id=20, course_id=2, source="courses", section_num=1, name="S2")

    class RacingService(FakeCatalogService):
        def sync_course_delta(self, source: str, course_id: int, force: bool = False):
            # Simulate user switching to courses:2 while courses:1 is awaiting delta sync
            if course_id == 1 and view._state.selected_course_key == "courses:1":
                view._state.select_course("courses:2")
                view._state.sections = [sec2]
            return CourseSyncResult(
                sections=[sec1] if course_id == 1 else [sec2],
                grades=[],
                changed=True,
                new_cmids=set(),
                last_synced_at=1_770_000_100,
                error="Moodle timeout" if course_id == 2 else "",
            )

        def load_course_grades(self, source: str, course_id: int, force: bool = False):
            if course_id == 1:
                view._state.select_course("courses:2")
                return [CourseGradeItem(id=99, item_name="Stale Grade")]
            return [CourseGradeItem(id=2, item_name="Fresh Grade")]

        def toggle_completion(self, source: str, course_id: int, cmid: int, completed: bool) -> bool:
            if course_id == 1:
                view._state.select_course("courses:2")
                return False
            return True

    svc = RacingService(
        courses=[c1, c2],
        detail_map={
            ("courses", 1): CourseDetailSnapshot(sections=[sec1], grades=[]),
            ("courses", 2): CourseDetailSnapshot(sections=[sec2], grades=[]),
        },
    )
    svc.last_thnn_error = "THNN hết hạn phiên"
    view = CoursesTabView(
        page=None,
        catalog_service=svc,
        on_open_assignment=Mock(),
        on_open_webview_activity=Mock(),
        on_open_attachment_viewer=Mock(),
        on_download_attachment=Mock(),
        on_launch_url=Mock(),
    )

    asyncio.run(view.load_initial_async())
    assert "THNN hết hạn phiên" in view._state.offline_notice

    # 1. _select_course_async stale guard
    asyncio.run(view._select_course_async("courses:1"))
    assert view._state.selected_course_key == "courses:2"
    assert view._state.sections == [sec2]

    # 2. _refresh_single_course_async surfaces res.error on active course
    asyncio.run(view._refresh_single_course_async())
    assert "Moodle timeout" in view._state.offline_notice

    # 3. _refresh_single_course_async stale guard
    view._state.select_course("courses:1")
    view._state.sections = [sec1]
    asyncio.run(view._refresh_single_course_async())
    assert view._state.selected_course_key == "courses:2"
    assert view._state.sections == [sec2]

    # 4. _on_subtab_change_async stale guard
    view._state.select_course("courses:1")
    view._state.grades = []
    asyncio.run(view._on_subtab_change_async(2))
    assert view._state.selected_course_key == "courses:2"
    assert view._state.grades == []

    # 5. _toggle_completion_async stale guard
    view._state.select_course("courses:1")
    view._state.offline_notice = ""
    mod = CourseModuleItem(cmid=5, instance_id=5, modname="page", name="P", section_id=10, completion_mode=1)
    asyncio.run(view._toggle_completion_async(mod, True))
    assert view._state.selected_course_key == "courses:2"
    assert view._state.offline_notice == ""


def test_open_module_strategy_dispatch():
    course = _make_course(10, source="thnn")
    svc = FakeCatalogService(courses=[course])
    svc.resolved_urls[3] = "https://meet.google.com/resolved-room"

    on_assign = Mock()
    on_webview = Mock()
    on_viewer = Mock()
    on_download = Mock()
    on_launch = Mock()

    view = CoursesTabView(
        page=None,
        catalog_service=svc,
        on_open_assignment=on_assign,
        on_open_webview_activity=on_webview,
        on_open_attachment_viewer=on_viewer,
        on_download_attachment=on_download,
        on_launch_url=on_launch,
    )
    view._state.set_courses([course])
    view._state.select_course(course.course_key)

    # assignment_detail
    m_assign = CourseModuleItem(cmid=1, instance_id=1, modname="assign", name="BT1", section_id=1, open_strategy="assignment_detail")
    asyncio.run(view._handle_open_module_async(m_assign))
    on_assign.assert_called_once_with(course, m_assign)

    # webview_activity
    m_quiz = CourseModuleItem(cmid=2, instance_id=2, modname="quiz", name="Quiz 1", section_id=1, open_strategy="webview_activity")
    asyncio.run(view._handle_open_module_async(m_quiz))
    on_webview.assert_called_once_with(course, m_quiz)

    # external_url uses resolve_module_url
    m_url = CourseModuleItem(cmid=3, instance_id=3, modname="googlemeet", name="Meet", section_id=1, open_strategy="external_url")
    asyncio.run(view._handle_open_module_async(m_url))
    on_launch.assert_called_with("https://meet.google.com/resolved-room")

    # preview_file opens attachment viewer
    att_pdf = _make_attachment("slide.pdf")
    m_pdf = CourseModuleItem(cmid=4, instance_id=4, modname="resource", name="Slide", section_id=1, attachments=(att_pdf,), open_strategy="preview_file")
    asyncio.run(view._handle_open_module_async(m_pdf))
    on_viewer.assert_called_once_with(course, m_pdf, att_pdf)

    # inline_page without on_open_inline_page pushes safe in-app reader overlay
    m_page = CourseModuleItem(
        cmid=5,
        instance_id=5,
        modname="page",
        name="Thông báo",
        section_id=1,
        inline_html='<p>Nội dung quan trọng</p><script>alert(1)</script><a href="https://ut.edu.vn/docs">Tài liệu</a><a href="javascript:alert(2)">Bad</a>',
        open_strategy="inline_page",
    )
    asyncio.run(view._handle_open_module_async(m_page))
    assert len(view._overlays) == 1
    overlay_controls = list(_walk_controls(view._overlays[-1]))
    overlay_texts = [c.value for c in overlay_controls if isinstance(c, ft.Text) and isinstance(c.value, str)]
    assert any("Nội dung quan trọng" in t for t in overlay_texts)
    assert not any("alert(1)" in t for t in overlay_texts)
    link_buttons = [
        c for c in overlay_controls
        if isinstance(c, (ft.TextButton, ft.OutlinedButton, ft.Chip))
        and "ut.edu.vn" in str(getattr(c, "tooltip", "") or getattr(c, "label", "") or getattr(c, "content", ""))
    ]
    assert len(link_buttons) == 1
    assert not any("javascript:" in str(getattr(c, "tooltip", "")) for c in overlay_controls)
    link_buttons[0].on_click(None)
    on_launch.assert_called_with("https://ut.edu.vn/docs")
    assert view.pop_overlay() is True

    # inline_page WITH custom on_open_inline_page callback
    on_inline = Mock()
    view._on_open_inline_page = on_inline
    asyncio.run(view._handle_open_module_async(m_page))
    on_inline.assert_called_once_with(course, m_page)

    # folder_files with multiple attachments pushes folder overlay
    att1 = _make_attachment("one.pdf")
    att2 = _make_attachment("two.docx", extension="docx", preview_kind="docx")
    m_folder = CourseModuleItem(
        cmid=6,
        instance_id=6,
        modname="folder",
        name="Thư mục bài giảng",
        section_id=1,
        attachments=(att1, att2),
        open_strategy="folder_files",
    )
    asyncio.run(view._handle_open_module_async(m_folder))
    assert len(view._overlays) == 1
    folder_controls = list(_walk_controls(view._overlays[-1]))
    file_rows = [
        c for c in folder_controls
        if isinstance(c, ft.Container) and c.on_click is not None
    ]
    assert len(file_rows) == 2
    file_rows[0].on_click(None)
    on_viewer.assert_called_with(course, m_folder, att1)
    dl_btns = [c for c in folder_controls if isinstance(c, ft.IconButton) and c.icon == ft.Icons.DOWNLOAD_ROUNDED]
    assert len(dl_btns) == 2
    dl_btns[1].on_click(None)
    on_download.assert_called_once_with(course, att2)
    assert view.pop_overlay() is True

    # web_fallback launches mod.web_url
    m_fb = CourseModuleItem(
        cmid=7,
        instance_id=7,
        modname="forum",
        name="Diễn đàn",
        section_id=1,
        web_url="https://thnn.ut.edu.vn/mod/forum/view.php?id=7",
        open_strategy="web_fallback",
    )
    asyncio.run(view._handle_open_module_async(m_fb))
    on_launch.assert_called_with("https://thnn.ut.edu.vn/mod/forum/view.php?id=7")


def test_detail_pane_grades_list_meet_button_and_view_focus_search():
    on_launch = Mock()
    pane = CourseDetailPane(on_launch_url=on_launch)
    course = _make_course(88, meet_url="https://meet.google.com/live-class")
    grades = [
        CourseGradeItem(
            id=1,
            item_name="Giữa kỳ",
            grade_formatted="8.50",
            range_formatted="0–10",
            percentage="85.0%",
            weight="30%",
        )
    ]
    state = CoursesState(
        courses=[course],
        selected_course_key=course.course_key,
        grades=grades,
        active_subtab=2,
    )
    pane.render(state, show_back=False)

    controls = list(_walk_controls(pane))
    meet_btns = [
        c for c in controls
        if isinstance(c, ft.OutlinedButton) and "Vào phòng học" in str(c.content)
    ]
    assert len(meet_btns) == 1
    meet_btns[0].on_click(None)
    on_launch.assert_called_once_with("https://meet.google.com/live-class")

    grade_texts = [
        c.value for c in _walk_controls(pane._grades_tab_body)
        if isinstance(c, ft.Text) and isinstance(c.value, str)
    ]
    assert any("Giữa kỳ" in t for t in grade_texts)
    assert any("8.50" in t for t in grade_texts)

    # Loading skeletons and empty filtered list in CourseListPane
    list_pane = CourseListPane()
    list_pane.render(CoursesState(courses=[], loading_catalog=True))
    assert len(list_pane._cards_list.controls) == 4
    list_pane.render(CoursesState(courses=[course], search_query="khongtontai"))
    empty_texts = [
        c.value for c in _walk_controls(list_pane._cards_list)
        if isinstance(c, ft.Text) and isinstance(c.value, str)
    ]
    assert any("Không tìm thấy môn học" in t for t in empty_texts)

    # focus_search exits mobile detail before focusing search input
    view = CoursesTabView(page=None, catalog_service=FakeCatalogService(courses=[course]))
    view.load_initial()
    view.set_width(420)
    view._state.select_course(course.course_key)
    focus_mock = AsyncMock()
    view._list_pane._search_field.focus = focus_mock
    asyncio.run(view.focus_search())
    assert view._state.in_detail_mobile is False
    focus_mock.assert_awaited_once()


def test_tabs_on_change_reads_e_data_before_selected_index_and_loads_grades():
    on_subtab = Mock()
    pane = CourseDetailPane(on_subtab_change=on_subtab)
    course = _make_course(200)
    state = CoursesState(courses=[course], selected_course_key=course.course_key, active_subtab=0)
    pane.render(state, show_back=False)
    assert pane._tabs.selected_index == 0

    # Flet 0.86 Tabs.on_change passes new tab index in e.data while e.control.selected_index may still be 0
    pane._tabs.on_change(SimpleNamespace(control=pane._tabs, data="2"))
    on_subtab.assert_called_once_with(2)
    assert state.active_subtab == 2

    # Validate int, invalid, and out-of-range values cleanly
    on_subtab.reset_mock()
    pane._tabs.on_change(SimpleNamespace(control=pane._tabs, data=1))
    on_subtab.assert_called_once_with(1)

    on_subtab.reset_mock()
    pane._tabs.on_change(SimpleNamespace(control=pane._tabs, data="invalid"))
    on_subtab.assert_called_once_with(0)

    on_subtab.reset_mock()
    pane._tabs.on_change(SimpleNamespace(control=pane._tabs, data="9"))
    on_subtab.assert_called_once_with(0)

    on_subtab.reset_mock()
    pane._tabs.on_change(SimpleNamespace(control=pane._tabs, data="-1"))
    on_subtab.assert_called_once_with(0)

    # End-to-end with CoursesTabView: switching to subtab "2" triggers lazy grade load
    grade = CourseGradeItem(id=1, item_name="Cuối kỳ", grade_formatted="9.0")
    svc = FakeCatalogService(courses=[course])
    svc.grades_map[("courses", 200)] = [grade]
    view = CoursesTabView(page=None, catalog_service=svc)
    view.load_initial()
    asyncio.run(view._select_course_async(course.course_key))
    assert view._state.grades == []

    view._detail_pane._tabs.selected_index = 0
    # Simulate _on_subtab_change_async triggered via Tabs.on_change e.data="2"
    captured: list[int] = []
    view._detail_pane._on_subtab_change = lambda idx: captured.append(idx)
    view._detail_pane._tabs.on_change(
        SimpleNamespace(control=view._detail_pane._tabs, data="2")
    )
    assert captured == [2]
    asyncio.run(view._on_subtab_change_async(captured[0]))
    assert view._state.active_subtab == 2
    assert view._state.grades == [grade]


def test_copy_text_async_updates_page_for_snackbar_overlay_and_dialog_fallback(monkeypatch):
    monkeypatch.setattr(ft.Clipboard, "set", AsyncMock())

    # Case 1: mounted view with page.overlay — page.update() MUST be called, not only self.update()
    fake_page = SimpleNamespace(overlay=[], update=Mock())
    view = CoursesTabView(page=fake_page, catalog_service=FakeCatalogService())
    view.update = Mock()
    monkeypatch.setattr(CoursesTabView, "page", property(lambda _self: fake_page))

    asyncio.run(view._copy_text_async("gv@ut.edu.vn"))
    assert len(fake_page.overlay) == 1
    assert isinstance(fake_page.overlay[0], ft.SnackBar)
    assert fake_page.overlay[0].open is True
    fake_page.update.assert_called_once()

    # Case 2: fallback when page.overlay is None — uses page.show_dialog and page.update()
    dialog_page = SimpleNamespace(overlay=None, show_dialog=Mock(), update=Mock())
    view_dlg = CoursesTabView(page=dialog_page, catalog_service=FakeCatalogService())
    monkeypatch.setattr(CoursesTabView, "page", property(lambda _self: dialog_page))

    asyncio.run(view_dlg._copy_text_async("0123456789"))
    dialog_page.show_dialog.assert_called_once()
    assert isinstance(dialog_page.show_dialog.call_args[0][0], ft.SnackBar)
    dialog_page.update.assert_called_once()


def test_courses_state_thnn_filter_is_case_insensitive_and_pane_highlights_chip():
    c_reg = _make_course(1, source="courses", display_name="Giải tích")
    c_thnn = _make_course(2, source="thnn", display_name="THNN Linux", program_tag="THNN")

    state = CoursesState(courses=[c_reg, c_thnn], semester_filter="thnn")
    assert state.filtered_courses() == [c_thnn]

    pane = CourseListPane()
    pane.render(state)
    chips = [c for c in _walk_controls(pane._chips_row) if isinstance(c, ft.Chip)]
    thnn_chips = [
        c for c in chips
        if (c.label.value if isinstance(c.label, ft.Text) else "") == "THNN"
    ]
    assert len(thnn_chips) == 1
    assert thnn_chips[0].selected is True


def test_aba_and_concurrent_request_tokens_prevent_stale_races():
    c_a = _make_course(1, display_name="Course A")
    c_b = _make_course(2, display_name="Course B")
    sec_a_old = CourseSection(id=1, course_id=1, source="courses", section_num=1, name="Old A")
    sec_a_new = CourseSection(id=2, course_id=1, source="courses", section_num=1, name="Fresh A")
    sec_b = CourseSection(id=3, course_id=2, source="courses", section_num=1, name="Course B Sec")

    gate_a_old = threading.Event()
    entered_a_old = threading.Event()
    calls = {"sync": 0, "cat": 0, "grades": 0}
    gate_cat1 = threading.Event()
    entered_cat1 = threading.Event()
    gate_grades1 = threading.Event()
    entered_grades1 = threading.Event()

    class RaceService(FakeCatalogService):
        def sync_course_delta(self, source: str, course_id: int, force: bool = False):
            calls["sync"] += 1
            if calls["sync"] == 1:
                entered_a_old.set()
                gate_a_old.wait(timeout=2)
                return CourseSyncResult(
                    sections=[sec_a_old],
                    grades=[],
                    changed=True,
                    new_cmids=set(),
                    last_synced_at=100,
                )
            if course_id == 2:
                return CourseSyncResult(
                    sections=[sec_b], grades=[], changed=True, new_cmids=set(), last_synced_at=200
                )
            return CourseSyncResult(
                sections=[sec_a_new], grades=[], changed=True, new_cmids=set(), last_synced_at=300
            )

        def refresh_catalog(self, include_thnn: bool = False):
            calls["cat"] += 1
            if calls["cat"] == 1:
                entered_cat1.set()
                gate_cat1.wait(timeout=2)
                return [c_a]
            return [c_a, c_b]

        def load_course_grades(self, source: str, course_id: int, force: bool = False):
            calls["grades"] += 1
            if calls["grades"] == 1:
                entered_grades1.set()
                gate_grades1.wait(timeout=2)
                return [CourseGradeItem(id=1, item_name="Stale Grade")]
            return [CourseGradeItem(id=2, item_name="Fresh Grade")]

    svc = RaceService(
        courses=[c_a, c_b],
        detail_map={
            ("courses", 1): CourseDetailSnapshot(sections=[sec_a_new], grades=[]),
            ("courses", 2): CourseDetailSnapshot(sections=[sec_b], grades=[]),
        },
    )
    view = CoursesTabView(page=None, catalog_service=svc)
    view.load_initial()

    # 1. ABA race on _select_course_async: A (slow) -> B -> A (fast), slow A must NOT overwrite Fresh A!
    async def run_aba():
        t1 = asyncio.create_task(view._select_course_async("courses:1"))
        await asyncio.to_thread(entered_a_old.wait, 2)
        await view._select_course_async("courses:2")
        await view._select_course_async("courses:1")
        gate_a_old.set()
        await t1

    asyncio.run(run_aba())
    assert view._state.selected_course_key == "courses:1"
    assert view._state.sections == [sec_a_new]
    assert view._state.last_synced_at == 300

    # 2. Concurrent refresh_catalog_async: slow call 1 must not clobber call 2's courses or loading_catalog
    async def run_cat_race():
        t1 = asyncio.create_task(view.refresh_catalog_async())
        await asyncio.to_thread(entered_cat1.wait, 2)
        t2 = asyncio.create_task(view.refresh_catalog_async())
        await t2
        gate_cat1.set()
        await t1

    asyncio.run(run_cat_race())
    assert view._state.courses == [c_a, c_b]
    assert view._state.loading_catalog is False

    # 3. Concurrent _on_subtab_change_async vs course refresh: stale grades must be dropped
    async def run_grade_race():
        t1 = asyncio.create_task(view._on_subtab_change_async(2))
        await asyncio.to_thread(entered_grades1.wait, 2)
        await view._refresh_single_course_async()
        gate_grades1.set()
        await t1

    asyncio.run(run_grade_race())
    assert [g.item_name for g in view._state.grades] == ["Fresh Grade"]


def test_open_recent_respects_mobile_back_cancellation():
    c1 = _make_course(1)
    mod = CourseModuleItem(
        cmid=11,
        instance_id=11,
        modname="resource",
        name="Slide",
        section_id=1,
        attachments=(_make_attachment("slide.pdf"),),
        open_strategy="preview_file",
    )
    sec = CourseSection(id=1, course_id=1, source="courses", section_num=1, name="S1", modules=(mod,))
    entered = threading.Event()
    gate = threading.Event()

    class SlowCacheSvc(FakeCatalogService):
        def open_course_cached(self, source: str, course_id: int):
            entered.set()
            gate.wait(timeout=2)
            return super().open_course_cached(source, course_id)

    svc = SlowCacheSvc(
        courses=[c1],
        detail_map={("courses", 1): CourseDetailSnapshot(sections=[sec], grades=[])},
    )
    on_viewer = Mock()
    view = CoursesTabView(page=None, catalog_service=svc, on_open_attachment_viewer=on_viewer)
    view.load_initial()
    view.set_width(400)  # mobile mode

    recent = RecentMaterialEntry(
        course_key="courses:1",
        course_name="C1",
        cmid=11,
        title="Slide",
        attachment_url="https://courses.ut.edu.vn/pluginfile.php/1/slide.pdf",
        file_name="slide.pdf",
        opened_at=1_770_000_000,
    )

    async def run_recent_and_back():
        task = asyncio.create_task(view._open_recent_async(recent))
        await asyncio.to_thread(entered.wait, 2)
        assert view.handle_back() is True  # User exited mobile detail while loading!
        gate.set()
        await task

    asyncio.run(run_recent_and_back())
    on_viewer.assert_not_called()


def test_async_exceptions_recover_ui_state_and_revert_checkbox():
    c1 = _make_course(1)
    mod = CourseModuleItem(
        cmid=42,
        instance_id=42,
        modname="page",
        name="Checklist",
        section_id=1,
        completion_mode=1,
        completion_state=0,
        open_strategy="inline_page",
    )
    sec = CourseSection(id=1, course_id=1, source="courses", section_num=1, name="S1", modules=(mod,))

    class BrokenService(FakeCatalogService):
        def toggle_pin(self, course_key: str):
            raise RuntimeError("pin boom")

        def toggle_completion(self, source: str, course_id: int, cmid: int, completed: bool) -> bool:
            raise RuntimeError("completion boom")

        def load_course_grades(self, source: str, course_id: int, force: bool = False):
            raise RuntimeError("grades boom")

    svc = BrokenService(
        courses=[c1],
        detail_map={("courses", 1): CourseDetailSnapshot(sections=[sec], grades=[])},
    )
    view = CoursesTabView(page=None, catalog_service=svc)
    view.load_initial()
    asyncio.run(view._select_course_async(c1.course_key))

    # _toggle_pin_async catches exception and sets offline_notice
    asyncio.run(view._toggle_pin_async(c1.course_key))
    assert view._state.offline_notice

    # _on_subtab_change_async catches exception, resets loading_detail, sets offline_notice
    view._state.offline_notice = ""
    asyncio.run(view._on_subtab_change_async(2))
    assert view._state.loading_detail is False
    assert view._state.offline_notice

    # _toggle_completion_async reverts checkbox visually back to False when provider raises
    cbs = [c for c in _walk_controls(view._detail_pane._content_tab_body) if isinstance(c, ft.Checkbox)]
    assert len(cbs) == 1
    cbs[0].value = True  # Simulate user toggle before async failure
    view._state.offline_notice = ""
    asyncio.run(view._toggle_completion_async(mod, True))
    assert view._state.offline_notice
    cbs_after = [c for c in _walk_controls(view._detail_pane._content_tab_body) if isinstance(c, ft.Checkbox)]
    assert cbs_after[0].value is False


def test_snackbar_prunes_stale_snackbars_and_removes_on_dismiss(monkeypatch):
    monkeypatch.setattr(ft.Clipboard, "set", AsyncMock())
    stale_sb = ft.SnackBar(content=ft.Text("old"), open=True)
    fake_page = SimpleNamespace(overlay=[stale_sb, ft.Container()], update=Mock())
    view = CoursesTabView(page=fake_page, catalog_service=FakeCatalogService())

    asyncio.run(view._copy_text_async("new@ut.edu.vn"))
    snackbars = [item for item in fake_page.overlay if isinstance(item, ft.SnackBar)]
    assert len(snackbars) == 1
    assert snackbars[0] is not stale_sb
    assert len(fake_page.overlay) == 2  # Preserved non-SnackBar overlay container

    # Triggering on_dismiss closes and removes the SnackBar from page.overlay
    assert callable(snackbars[0].on_dismiss)
    snackbars[0].on_dismiss(None)
    assert snackbars[0].open is False
    assert snackbars[0] not in fake_page.overlay


def test_subtab_0_1_preserves_expansion_tiles_single_update_theme_and_tablet_wrapper(monkeypatch):
    course = _make_course(10)
    mod = CourseModuleItem(cmid=1, instance_id=1, modname="assign", name="HW1", section_id=1, open_strategy="assignment_detail")
    sec = CourseSection(id=1, course_id=10, source="courses", section_num=1, name="S1", modules=(mod,))
    svc = FakeCatalogService(courses=[course], detail_map={("courses", 10): CourseDetailSnapshot(sections=[sec], grades=[])})

    fake_page = SimpleNamespace(overlay=[], update=Mock())
    view = CoursesTabView(page=fake_page, catalog_service=svc)
    update_mock = Mock()
    view.update = update_mock
    monkeypatch.setattr(CoursesTabView, "page", property(lambda _self: fake_page))

    view.load_initial()
    asyncio.run(view._select_course_async(course.course_key))

    tiles_before = [c for c in _walk_controls(view._detail_pane._content_tab_body) if isinstance(c, ft.ExpansionTile)]
    assert len(tiles_before) == 1

    # Subtab switch 0 -> 1 -> 0 does NOT rebuild ExpansionTile controls
    asyncio.run(view._on_subtab_change_async(1))
    asyncio.run(view._on_subtab_change_async(0))
    tiles_after = [c for c in _walk_controls(view._detail_pane._content_tab_body) if isinstance(c, ft.ExpansionTile)]
    assert tiles_after[0] is tiles_before[0]

    # Persistent tablet wrapper reused across resizes
    view.set_width(700)
    wrapper_1 = view._main_host.content
    assert view._tablet_content.width == 700.0
    view.set_width(780)
    assert view._main_host.content is wrapper_1
    assert view._tablet_content.width == 780.0

    # Single-pass update_theme() including overlay color remapping
    apply_theme("midnight_blue")
    view._theme_snapshot = dict(view._theme_snapshot)
    overlay_card = ft.Container(bgcolor=C.SURFACE, content=ft.Text("Reader", color=C.TEXT_PRIMARY))
    view.push_overlay(overlay_card)
    update_mock.reset_mock()
    apply_theme("nord_frost")
    view.update_theme()
    assert update_mock.call_count == 1
    assert overlay_card.bgcolor == C.SURFACE
    assert overlay_card.content.color == C.TEXT_PRIMARY
    apply_theme("midnight_blue")


def test_duplicate_overlay_guard_focus_search_and_url_fallback_and_no_coro_warnings():
    course = _make_course(20)
    att1 = _make_attachment("a.pdf")
    att2 = _make_attachment("b.pdf")
    m_page = CourseModuleItem(
        cmid=5,
        instance_id=5,
        modname="page",
        name="Page",
        section_id=1,
        inline_html="<p>Hello</p>",
        open_strategy="inline_page",
    )
    m_folder = CourseModuleItem(
        cmid=6,
        instance_id=6,
        modname="folder",
        name="Folder",
        section_id=1,
        attachments=(att1, att2),
        open_strategy="folder_files",
    )
    m_unsafe_primary = CourseModuleItem(
        cmid=7,
        instance_id=7,
        modname="forum",
        name="Forum",
        section_id=1,
        web_url="javascript:alert(1)",
        url="https://ut.edu.vn/fallback-ok",
        open_strategy="web_fallback",
    )
    on_launch = Mock()
    on_download = Mock()
    view = CoursesTabView(
        page=None,
        catalog_service=FakeCatalogService(courses=[course]),
        on_launch_url=on_launch,
        on_download_attachment=on_download,
    )
    view.load_initial()
    view._state.select_course(course.course_key)

    # Duplicate overlay guard: double-opening inline_page or folder_files keeps only 1 modal overlay
    asyncio.run(view._handle_open_module_async(m_page))
    asyncio.run(view._handle_open_module_async(m_page))
    assert len(view._overlays) == 1
    asyncio.run(view._handle_open_module_async(m_folder))
    assert len(view._overlays) == 1

    # Folder item click falls back cleanly to on_download_attachment when on_open_attachment_viewer is None
    folder_rows = [
        c for c in _walk_controls(view._overlays[-1])
        if isinstance(c, ft.Container) and c.on_click is not None
    ]
    folder_rows[0].on_click(None)
    on_download.assert_called_once_with(course, att1)

    # focus_search() clears active overlays before focusing search field
    focus_mock = AsyncMock()
    view._list_pane._search_field.focus = focus_mock
    asyncio.run(view.focus_search())
    assert view._overlays == []
    focus_mock.assert_awaited_once()

    # Safe URL fallback priority: invalid web_url does not shadow valid mod.url
    asyncio.run(view._handle_open_module_async(m_unsafe_primary))
    on_launch.assert_called_once_with("https://ut.edu.vn/fallback-ok")

    # Zero unawaited coroutine warnings when scheduling/dispatching without event loop
    async def async_cb(*_args):
        return None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", RuntimeWarning)
        view._dispatch_callback(async_cb, "x")
        view._dispatch_callback(lambda *_a: async_cb(), "y")
        gc.collect()
    unawaited = [
        w for w in caught
        if issubclass(w.category, RuntimeWarning) and "was never awaited" in str(w.message)
    ]
    assert unawaited == []


def test_edge_helpers_first_visible_section_expansion_and_stale_error_guard():
    assert safe_http_url("  https://ut.edu.vn/a  ") == "https://ut.edu.vn/a"
    assert safe_http_url("javascript:alert(1)") == ""
    assert safe_http_url("https://ut.edu.vn/a\nb") == ""

    text, links = extract_readable_html_and_links(
        "<p>Line 1</p><script>bad()</script><a href='https://ut.edu.vn'>UTH</a><a href='data:text/html,hi'>X</a>"
    )
    assert "Line 1" in text and "bad()" not in text
    assert links == [("UTH", "https://ut.edu.vn")]

    # _safe_progress rejects bool, NaN, and clamps [0, 1]
    assert _safe_progress(_make_course(1, progress=True, total_trackable=0)) is None  # type: ignore[arg-type]
    assert _safe_progress(_make_course(1, progress=float("nan"), total_trackable=0)) is None
    assert _safe_progress(_make_course(1, progress=-0.4, total_trackable=0)) == 0.0
    assert _safe_progress(_make_course(1, progress=1.8, total_trackable=0)) == 1.0

    # First visible section expands when section 0 is filtered out, and multi-file folder hides single download icon
    m_assign = CourseModuleItem(cmid=1, instance_id=1, modname="assign", name="HW", section_id=1, open_strategy="assignment_detail")
    m_folder = CourseModuleItem(
        cmid=2,
        instance_id=2,
        modname="folder",
        name="Multi",
        section_id=2,
        attachments=(_make_attachment("1.pdf"), _make_attachment("2.pdf")),
        open_strategy="folder_files",
    )
    sec0 = CourseSection(id=1, course_id=1, source="courses", section_num=0, name="Sec0", modules=(m_assign,))
    sec1 = CourseSection(id=2, course_id=1, source="courses", section_num=1, name="Sec1", modules=(m_folder,))
    pane = CourseDetailPane()
    pane.render(
        CoursesState(
            courses=[_make_course(1)],
            selected_course_key="courses:1",
            sections=[sec0, sec1],
            module_filter="materials",
        ),
        show_back=False,
    )
    tiles = [c for c in _walk_controls(pane._content_tab_body) if isinstance(c, ft.ExpansionTile)]
    assert len(tiles) == 1
    assert tiles[0].expanded is True
    dl_icons = [c for c in _walk_controls(tiles[0]) if isinstance(c, ft.IconButton) and c.icon == ft.Icons.DOWNLOAD_ROUNDED]
    assert dl_icons == []

    # Stale failure from deselected course does not overwrite active course's offline_notice
    c1 = _make_course(1)
    c2 = _make_course(2)
    entered = threading.Event()
    gate = threading.Event()

    class DeselectedErrorSvc(FakeCatalogService):
        def sync_course_delta(self, source: str, course_id: int, force: bool = False):
            if course_id == 1:
                entered.set()
                gate.wait(timeout=2)
                raise RuntimeError("stale boom")
            return super().sync_course_delta(source, course_id, force)

    view = CoursesTabView(page=None, catalog_service=DeselectedErrorSvc(courses=[c1, c2]))
    view.load_initial()

    async def run_stale_err():
        t1 = asyncio.create_task(view._select_course_async("courses:1"))
        await asyncio.to_thread(entered.wait, 2)
        await view._select_course_async("courses:2")
        view._state.offline_notice = ""
        gate.set()
        await t1

    asyncio.run(run_stale_err())
    assert view._state.selected_course_key == "courses:2"
    assert view._state.offline_notice == ""
