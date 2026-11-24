"""Tier 1 E2E Feature Tests (F9, F10, F11): Mobile UI, Back Delegation & Ergonomics."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, Callable

import flet as ft
import pytest

from core.courses.activity_service import (
    AttendanceRecord,
    AttendanceSummary,
    CourseGroupEntry,
    GroupActivitySummary,
)
from core.courses.archive_service import ArchiveService
from core.courses.cache_store import CourseDetailSnapshot
from core.courses.models import CourseSummary
from gui.app_controller import AppController
from gui.components.courses._shared import (
    PaneErrorOverlay,
    PaneLoadingOverlay,
    build_offline_banner,
)
from gui.components.courses.activity_views import (
    AttendanceActivityView,
    ForumActivityView,
    GroupActivityView,
    PaneAssignmentView,
    QuizActivityView,
    RichContentActivityView,
)
from gui.components.courses.adaptive_nav import AdaptiveNavShell
from gui.components.courses.archive_explorer_view import ArchiveExplorerView
from gui.components.courses.course_detail_pane import CourseDetailPane
from gui.components.courses.course_list_pane import CourseListPane
from gui.components.courses.courses_tab_view import CoursesTabView
from gui.components.courses.document_reader_view import DocumentReaderView
from gui.components.courses.webview_pane import CoursesWebViewPane
from gui.components.detail_view import DetailView
from gui.core.theme import C
from gui.view_models.courses_state import CoursesState
from tests.e2e_courses.builders import (
    FakeActivityService,
    FakeCatalogService,
    build_assignment_payload,
    build_pdf_bytes,
    build_zip_bytes,
    make_attachment,
    make_course,
    make_grade,
    make_module,
    make_recent,
    make_section,
)
from tests.e2e_courses.conftest import (
    FakePage,
    FakeView,
    assert_min_touch_target,
    assert_no_fixed_width_overflow,
    assert_valid_flet_mro,
    bind_control_page,
    collect_texts,
    walk_controls,
)


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


def _find_icon_buttons(root: ft.BaseControl, tooltip_substring: str) -> list[ft.IconButton]:
    needle = tooltip_substring.casefold()
    return [
        ctrl
        for ctrl in walk_controls(root)
        if isinstance(ctrl, ft.IconButton)
        and needle in str(getattr(ctrl, "tooltip", "") or "").casefold()
    ]


# ---------------------------------------------------------------------------
# F9: Android System Back Sub-Stack Delegation (>= 5 tests)
# ---------------------------------------------------------------------------


def test_f9_courses_tab_delegates_back_to_archive_stack_then_pops_overlay_then_exits_detail(
    fake_page: FakePage, platform_profile
) -> None:
    fake_page.resize(360, 780)
    platform_profile("android", page=fake_page)
    course = make_course(101, display_name="Mạng máy tính")
    snap = CourseDetailSnapshot(sections=[make_section(1)], grades=[], last_synced_at=1_770_000_000)
    svc = FakeCatalogService(courses=[course], detail_map={("courses", 101): snap})

    tab = CoursesTabView(page=fake_page, catalog_service=svc)
    tab.set_viewport_size(360, 780)
    asyncio.run(tab.load_initial_async())
    asyncio.run(tab._on_user_select_course_async(course.course_key))
    assert tab._state.in_detail_mobile is True

    archive_bytes = build_zip_bytes({"folder1/sub/notes.txt": b"hello world"})
    explorer = ArchiveExplorerView(
        archive_name="lab.zip",
        archive_bytes=archive_bytes,
        archive_service=ArchiveService(),
        on_close=tab.pop_overlay,
        on_open_entry=lambda *_a: None,
        on_save_raw=lambda *_a: None,
    )
    tab.push_overlay(explorer, tag="archive:lab")

    # Drill into folder1 -> sub
    _find_clickable_with_text(explorer, "folder1").on_click(None)
    assert any("sub" in t for t in collect_texts(explorer))
    _find_clickable_with_text(explorer, "sub").on_click(None)
    assert any("notes.txt" in t for t in collect_texts(explorer))

    # 1. Back pops inner 'sub' folder -> back in 'folder1'
    assert tab.handle_back() is True
    assert len(tab._overlays) == 1
    assert any("sub" in t for t in collect_texts(explorer))

    # 2. Back pops 'folder1' -> back at archive root
    assert tab.handle_back() is True
    assert len(tab._overlays) == 1
    assert any("folder1" in t for t in collect_texts(explorer))

    # 3. Back at archive root pops the ArchiveExplorerView overlay
    assert tab.handle_back() is True
    assert len(tab._overlays) == 0
    assert tab._state.in_detail_mobile is True

    # 4. Back in mobile detail exits to master course list
    assert tab.handle_back() is True
    assert tab._state.in_detail_mobile is False

    # 5. Back at root returns False
    assert tab.handle_back() is False


def test_f9_quiz_activity_view_back_steps_from_review_to_summary_then_closes(
    fake_page: FakePage, platform_profile
) -> None:
    fake_page.resize(360, 780)
    platform_profile("android", page=fake_page)
    tab = CoursesTabView(page=fake_page)
    tab.set_viewport_size(360, 780)

    mod = make_module(201, modname="quiz", name="Kiểm tra giữa kỳ")
    quiz_view = QuizActivityView(
        source="courses",
        course_id=101,
        module=mod,
        activity_service=FakeActivityService(),
        on_close=tab.pop_overlay,
        on_open_webview=lambda *_a: None,
        pane_width=360.0,
    )
    tab.push_overlay(quiz_view, tag="activity:quiz:201")

    _wait_until(lambda: any("Xem lại" in t for t in collect_texts(quiz_view)), label="quiz summary")
    assert quiz_view.handle_back() is False

    review_btn = _find_button_with_text(quiz_view, "Xem lại")
    review_btn.on_click(None)
    _wait_until(
        lambda: any("Quay lại tổng quan Quiz" in t for t in collect_texts(quiz_view)),
        label="quiz attempt review",
    )

    # First back steps from attempt review back to summary without popping overlay
    assert tab.handle_back() is True
    assert len(tab._overlays) == 1
    assert any("Lịch sử làm bài" in t for t in collect_texts(quiz_view))
    assert not any("Quay lại tổng quan Quiz" in t for t in collect_texts(quiz_view))

    # Second back pops QuizActivityView overlay
    assert tab.handle_back() is True
    assert len(tab._overlays) == 0


def test_f9_forum_activity_view_back_steps_from_posts_to_discussions_then_closes(
    fake_page: FakePage, platform_profile
) -> None:
    fake_page.resize(360, 780)
    platform_profile("android", page=fake_page)
    tab = CoursesTabView(page=fake_page)
    tab.set_viewport_size(360, 780)

    mod = make_module(301, modname="forum", name="Diễn đàn môn học")
    forum_view = ForumActivityView(
        source="courses",
        course_id=101,
        module=mod,
        activity_service=FakeActivityService(),
        on_close=tab.pop_overlay,
        on_open_webview=lambda *_a: None,
        pane_width=360.0,
    )
    tab.push_overlay(forum_view, tag="activity:forum:301")

    _wait_until(
        lambda: any("Hướng dẫn đồ án cuối kỳ" in t for t in collect_texts(forum_view)),
        label="forum discussions",
    )
    assert forum_view.handle_back() is False

    _find_clickable_with_text(forum_view, "Hướng dẫn đồ án").on_click(None)
    _wait_until(
        lambda: any("Danh sách chủ đề" in t for t in collect_texts(forum_view)),
        label="forum posts",
    )

    # First back steps from posts thread back to discussions list
    assert tab.handle_back() is True
    assert len(tab._overlays) == 1
    assert any("Hướng dẫn đồ án cuối kỳ" in t for t in collect_texts(forum_view))
    assert not any("Danh sách chủ đề" in t for t in collect_texts(forum_view))

    # Second back pops ForumActivityView overlay
    assert tab.handle_back() is True
    assert len(tab._overlays) == 0


def test_f9_webview_pane_handle_back_navigates_browser_history_before_closing(
    fake_page: FakePage, platform_profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    fwv = pytest.importorskip("flet_webview")

    fake_page.resize(360, 780)
    platform_profile("android", page=fake_page)
    bind_control_page(monkeypatch, fwv.WebView, fake_page)

    state = {"history_depth": 2, "go_back_calls": 0, "can_go_back_calls": 0}

    async def _fake_can_go_back(self: Any) -> bool:
        state["can_go_back_calls"] += 1
        return state["history_depth"] > 0

    async def _fake_can_go_forward(self: Any) -> bool:
        return False

    async def _fake_go_back(self: Any) -> None:
        state["go_back_calls"] += 1
        state["history_depth"] = max(0, state["history_depth"] - 1)

    async def _fake_get_title(self: Any) -> str:
        return "Quiz WebView"

    monkeypatch.setattr(fwv.WebView, "can_go_back", _fake_can_go_back)
    monkeypatch.setattr(fwv.WebView, "can_go_forward", _fake_can_go_forward)
    monkeypatch.setattr(fwv.WebView, "go_back", _fake_go_back)
    monkeypatch.setattr(fwv.WebView, "get_title", _fake_get_title)

    tab = CoursesTabView(page=fake_page)
    tab.set_viewport_size(360, 780)

    pane = CoursesWebViewPane(
        page=fake_page,
        source="courses",
        title="Quiz WebView",
        url="https://courses.ut.edu.vn/mod/quiz/view.php?id=10",
        on_back=tab.pop_overlay,
        on_open_external=lambda _u: None,
        pane_width=360.0,
    )
    try:
        tab.push_overlay(pane, tag="webview:10")
        wv = next(c for c in walk_controls(pane) if isinstance(c, fwv.WebView))
        assert callable(wv.on_page_ended)
        wv.on_page_ended(
            SimpleNamespace(data="https://courses.ut.edu.vn/mod/quiz/view.php?id=10")
        )
        assert state["can_go_back_calls"] >= 1
        assert pane._wv_back_btn.disabled is False

        # 1st back -> browser history back
        assert tab.handle_back() is True
        assert state["go_back_calls"] == 1
        assert len(tab._overlays) == 1
        assert pane._wv_back_btn.disabled is False

        # 2nd back -> browser history back again, exhausting history
        assert tab.handle_back() is True
        assert state["go_back_calls"] == 2
        assert len(tab._overlays) == 1
        assert pane._wv_back_btn.disabled is True

        # History is now empty: pane.handle_back() returns False, and tab.handle_back() pops pane
        assert pane.handle_back() is False
        assert tab.handle_back() is True
        assert len(tab._overlays) == 0
    finally:
        pane.dispose()


def test_f9_document_reader_handle_back_closes_outline_or_search_sheet_before_closing(
    fake_page: FakePage, platform_profile, preview_server
) -> None:
    fake_page.resize(360, 780)
    platform_profile("android", page=fake_page)
    tab = CoursesTabView(page=fake_page)
    tab.set_viewport_size(360, 780)

    pdf_bytes = build_pdf_bytes(
        ["Trang 1 nội dung", "Trang 2 nội dung"],
        outline_titles=[("Chương 1", 0), ("Chương 2", 1)],
    )
    reader = DocumentReaderView(
        file_name="lecture.pdf",
        data=pdf_bytes,
        kind="pdf",
        preview_server=preview_server,
        on_close=tab.pop_overlay,
        on_save=lambda *_a: None,
        on_open_external=lambda *_a: None,
        pane_width=360.0,
    )
    tab.push_overlay(reader, tag="reader:lecture.pdf")

    # Open PDF outline sheet / panel via toolbar button or search toggle
    outline_btns = _find_icon_buttons(reader, "Mục lục")
    assert outline_btns, "Expected outline button on PDF with bookmarks"
    outline_btns[0].on_click(None)

    # First back must be handled by DocumentReaderView (closing outline/search sheet),
    # leaving the reader overlay open.
    assert callable(getattr(reader, "handle_back", None))
    assert tab.handle_back() is True
    assert len(tab._overlays) == 1

    # Second back closes DocumentReaderView overlay itself
    assert tab.handle_back() is True
    assert len(tab._overlays) == 0


def test_f9_app_controller_navigate_back_propagates_through_courses_view_handle_back(
    fake_page: FakePage, platform_profile
) -> None:
    fake_page.resize(360, 780)
    platform_profile("android", page=fake_page)
    fake_view = FakeView(can_pop=False)
    fake_page.views = [fake_view]

    tab = CoursesTabView(page=fake_page)
    tab.set_viewport_size(360, 780)
    tab.visible = True

    archive = ArchiveExplorerView(
        archive_name="nested.zip",
        archive_bytes=build_zip_bytes({"dirA/file.txt": b"abc"}),
        archive_service=ArchiveService(),
        on_close=tab.pop_overlay,
        on_open_entry=lambda *_a: None,
        on_save_raw=lambda *_a: None,
    )
    tab.push_overlay(archive, tag="archive:nested")
    _find_clickable_with_text(archive, "dirA").on_click(None)

    ctrl = AppController.__new__(AppController)
    ctrl.page = fake_page
    ctrl._today_schedule_component = None
    ctrl.courses_view = tab
    ctrl.detail_view = SimpleNamespace(visible=False)
    ctrl.settings_view = SimpleNamespace(visible=False)
    ctrl.calendar_view = SimpleNamespace(visible=False)
    ctrl.grade_overview_view = SimpleNamespace(visible=False)
    ctrl.view_manager = SimpleNamespace(
        _detail_origin=None,
        show_dashboard=lambda: setattr(tab, "visible", False),
    )

    # 1. System Back pops dirA inside ArchiveExplorer -> confirm_pop(False)
    asyncio.run(ctrl._on_root_view_confirm_pop(SimpleNamespace(control=fake_view)))
    assert fake_view.confirmed_pops[-1] is False
    assert len(tab._overlays) == 1

    # 2. System Back pops ArchiveExplorer overlay -> confirm_pop(False)
    asyncio.run(ctrl._on_root_view_confirm_pop(SimpleNamespace(control=fake_view)))
    assert fake_view.confirmed_pops[-1] is False
    assert len(tab._overlays) == 0

    # 3. System Back at Courses root closes Courses -> Dashboard -> confirm_pop(False)
    asyncio.run(ctrl._on_root_view_confirm_pop(SimpleNamespace(control=fake_view)))
    assert fake_view.confirmed_pops[-1] is False
    assert tab.visible is False

    # 4. System Back at Dashboard root -> confirm_pop(True) allows platform pop
    asyncio.run(ctrl._on_root_view_confirm_pop(SimpleNamespace(control=fake_view)))
    assert fake_view.confirmed_pops[-1] is True


# ---------------------------------------------------------------------------
# F10: Zero-Overflow 360-430dp Mobile Layouts (>= 5 tests)
# ---------------------------------------------------------------------------


def test_f10_overlays_and_fallback_cards_zero_fixed_overflow_at_360dp(
    fake_page: FakePage, platform_profile, preview_server
) -> None:
    fake_page.resize(360, 780)
    platform_profile("android", page=fake_page)

    for preset in ("document", "archive", "assignment", "activity"):
        loading = PaneLoadingOverlay(
            title="Bài giảng tuần 1 rất dài cho điện thoại 360dp",
            preset=preset,  # type: ignore[arg-type]
            on_back=lambda: None,
        )
        assert_valid_flet_mro(loading)
        assert_no_fixed_width_overflow(loading, 360)

    err = PaneErrorOverlay(
        title="Lỗi tải tài liệu",
        message="Không thể kết nối tới máy chủ Moodle UTH",
        on_back=lambda: None,
        on_retry=lambda: None,
        on_open_webview=lambda: None,
    )
    assert_valid_flet_mro(err)
    assert_no_fixed_width_overflow(err, 360)

    tab = CoursesTabView(page=fake_page)
    tab.set_viewport_size(360, 780)
    course = make_course(101)
    folder_mod = make_module(
        401,
        modname="folder",
        name="Thư mục tài liệu thực hành môn học",
        attachments=(
            make_attachment("slide1.pdf"),
            make_attachment("slide2.docx"),
        ),
    )
    tab._push_folder_overlay(course, folder_mod)
    assert_valid_flet_mro(tab)
    assert_no_fixed_width_overflow(tab, 360)
    tab.pop_overlay()

    inline_mod = make_module(
        402,
        modname="page",
        name="Thông báo học phần",
        inline_html='<p>Đọc kỹ giáo trình trước buổi học.</p><a href="https://courses.ut.edu.vn/mod/page/view.php?id=402">Mở link</a>',
    )
    tab._push_inline_reader_overlay(inline_mod)
    assert_valid_flet_mro(tab)
    assert_no_fixed_width_overflow(tab, 360)

    reader_fallback = DocumentReaderView(
        file_name="archive_unknown.bin",
        data=b"\x00\x01\x02\x03",
        kind="unsupported",
        preview_server=preview_server,
        on_close=lambda: None,
        on_save=lambda *_a: None,
        on_open_external=lambda *_a: None,
        pane_width=360.0,
    )
    assert_valid_flet_mro(reader_fallback)
    assert_no_fixed_width_overflow(reader_fallback, 360)

    archive_unsupported = ArchiveExplorerView(
        archive_name="broken.rar",
        archive_bytes=b"Rar!invalid",
        archive_service=ArchiveService(),
        on_close=lambda: None,
        on_open_entry=lambda *_a: None,
        on_save_raw=lambda *_a: None,
    )
    assert_valid_flet_mro(archive_unsupported)
    assert_no_fixed_width_overflow(archive_unsupported, 360)


def test_f10_course_detail_pane_360dp_chips_pills_tabs_inline_card_and_grades() -> None:
    course = make_course(
        101,
        source="thnn",
        display_name="Lập trình hướng đối tượng và Cấu trúc dữ liệu nâng cao",
        teacher_name="PGS.TS. Nguyễn Thị Minh Phương",
        teacher_email="nguyen.thi.minh.phuong@ut.edu.vn",
        meet_url="https://meet.google.com/abc-defg-hij",
        credits=4,
    )
    mods = (
        make_module(
            1,
            modname="label",
            name="Ghi chú môn học",
            inline_html=(
                '<p>Tham gia lớp học trực tuyến qua liên kết sau:</p>'
                '<a href="https://meet.google.com/very-long-meeting-room-code-for-mobile-testing">Phòng học trực tuyến Google Meet Ca Sáng Thứ Hai</a>'
            ),
        ),
        make_module(
            2,
            modname="resource",
            name="Đề cương chi tiết môn học.pdf",
            attachments=(make_attachment("De_cuong.pdf"),),
            completion_mode=1,
            completion_state=1,
            is_new=True,
        ),
    )
    sections = [make_section(1, name="Chương 1: Tổng quan", modules=mods)]
    grades = [
        make_grade(1, item_name="Tổng kết môn", item_module="course", grade_formatted="8.75 / 10", weight="100%"),
        make_grade(2, item_name="Bài tập lớn cuối kỳ nhóm", grade_formatted="9.00", weight="40%", feedback_html="<p>Trình bày rất tốt.</p>"),
        make_grade(3, item_name="Chuyên cần", grade_formatted="-", weight="10%"),
    ]
    state = CoursesState(
        courses=[course],
        selected_course_key=course.course_key,
        sections=sections,
        grades=grades,
        in_detail_mobile=True,
        layout_mode="mobile",
        viewport_height=780.0,
        user_groups=[{"name": "Nhóm Thực Hành Ca 1 - Thứ 5 - Phòng A204"}],
    )

    detail = CourseDetailPane()
    for subtab in (0, 1, 2):
        state.active_subtab = subtab
        detail.render(state, show_back=True, update=False)
        assert_valid_flet_mro(detail)
        assert_no_fixed_width_overflow(detail, 360, allow_horizontal_scroll_descendants=True)

    # Verify 3-pill segmented tabs exist and use expand=True rather than overflowing widths
    assert len(detail._segment_pills) == 3
    for pill in detail._segment_pills:
        assert pill.expand is True
    texts = collect_texts(detail)
    assert any("Nội dung" in t for t in texts)
    assert any("Tài liệu" in t for t in texts)
    assert any("Điểm môn" in t for t in texts)


def test_f10_group_and_attendance_activity_views_render_cleanly_at_360dp() -> None:
    group_mod = make_module(501, modname="groupselect", name="Chọn nhóm đồ án")
    group_summary = GroupActivitySummary(
        cmid=501,
        modname="groupselect",
        title="Đăng ký nhóm thực hành lập trình",
        intro="Mỗi nhóm tối đa 4 sinh viên, chọn 1 nhóm phù hợp.",
        my_group_names=("Nhóm 1 - Alpha",),
        groups=(
            CourseGroupEntry(
                group_id="g1",
                name="Nhóm 1 - Phát triển ứng dụng di động đa nền tảng",
                description="Xây dựng ứng dụng hỗ trợ sinh viên UTH",
                member_count_label="3/4",
                members=("Nguyễn Văn A", "Trần Thị B", "Lê Văn C"),
                is_member=True,
                can_join=False,
                can_leave=True,
            ),
            CourseGroupEntry(
                group_id="g2",
                name="Nhóm 2 - AI & Khoa học dữ liệu",
                description="Phân tích dữ liệu học tập",
                member_count_label="2/4",
                members=("Phạm Minh D", "Hoàng Anh E"),
                is_member=False,
                can_join=True,
                can_leave=False,
            ),
        ),
    )
    group_view = GroupActivityView(
        source="courses",
        course_id=101,
        module=group_mod,
        activity_service=FakeActivityService(),
        on_close=lambda: None,
        initial_summary=group_summary,
        pane_width=360.0,
    )
    assert_valid_flet_mro(group_view)
    assert_no_fixed_width_overflow(group_view, 360)

    att_mod = make_module(502, modname="attendance", name="Điểm danh chuyên cần")
    att_summary = AttendanceSummary(
        cmid=502,
        title="Điểm danh lớp CLC",
        taken_sessions="9/10",
        points_over_taken="18/18",
        percentage="100%",
        records=(
            AttendanceRecord(
                date_str="18/11/2026 07:30",
                description="Buổi 1 - Lý thuyết kiến trúc phần mềm",
                status="Present",
                points="2/2",
                remarks="Đúng giờ",
            ),
        ),
        submit_form={"sessid": "1", "sesskey": "xyz"},
        submit_action_url="https://courses.ut.edu.vn/mod/attendance/attendance.php",
        status_options=(("1", "Present"), ("2", "Late")),
        requires_password=True,
    )
    att_view = AttendanceActivityView(
        source="courses",
        module=att_mod,
        activity_service=FakeActivityService(),
        on_close=lambda: None,
        initial_summary=att_summary,
        pane_width=360.0,
    )
    assert_valid_flet_mro(att_view)
    assert_no_fixed_width_overflow(att_view, 360)


def test_f10_forum_rich_content_pane_assignment_and_detail_view_360dp(
    fake_page: FakePage, platform_profile
) -> None:
    fake_page.resize(360, 780)
    platform_profile("android", page=fake_page)
    svc = FakeActivityService()

    # ForumActivityView (discussions + posts)
    forum_view = ForumActivityView(
        source="courses",
        course_id=101,
        module=make_module(601, modname="forum", name="Diễn đàn"),
        activity_service=svc,
        on_close=lambda: None,
        pane_width=360.0,
    )
    _wait_until(lambda: any("Hướng dẫn đồ án" in t for t in collect_texts(forum_view)), label="forum")
    assert_valid_flet_mro(forum_view)
    assert_no_fixed_width_overflow(forum_view, 360)
    _find_clickable_with_text(forum_view, "Hướng dẫn đồ án").on_click(None)
    _wait_until(lambda: any("DeCuong_DoAn.pdf" in t for t in collect_texts(forum_view)), label="posts")
    assert_valid_flet_mro(forum_view)
    assert_no_fixed_width_overflow(forum_view, 360)

    # RichContentActivityView (active chapter highlight + 360dp)
    rich_view = RichContentActivityView(
        source="courses",
        course_id=101,
        module=make_module(602, modname="book", name="Giáo trình"),
        activity_service=svc,
        on_close=lambda: None,
        pane_width=360.0,
    )
    _wait_until(lambda: any("Chương 1" in t for t in collect_texts(rich_view)), label="rich book")
    chips = [c for c in walk_controls(rich_view) if isinstance(c, ft.Chip)]
    assert len(chips) == 3
    assert chips[0].selected is True
    assert chips[1].selected is False
    assert_valid_flet_mro(rich_view)
    assert_no_fixed_width_overflow(rich_view, 360)

    # DetailView + PaneAssignmentView at 360dp (3-button submission row wraps cleanly)
    detail_view = DetailView(fake_page, on_close=lambda: None, embedded_in_pane=True)
    detail_view.update_detail(build_assignment_payload())
    submission_row = None
    for ctrl in walk_controls(detail_view):
        if isinstance(ctrl, ft.Row) and all(
            btn in ctrl.controls
            for btn in (detail_view._pick_btn, detail_view._submit_btn, detail_view._finalize_btn)
        ):
            submission_row = ctrl
            break
    assert submission_row is not None, "Expected 3-button submission row in DetailView"
    assert submission_row.wrap is True, "3-button submission row must wrap on 360dp mobile"

    pane_assign = PaneAssignmentView(
        detail_view=detail_view,
        title="Bài tập lớn giữa kỳ",
        on_close=lambda: None,
    )
    pane_assign.update_detail(build_assignment_payload())
    assert_valid_flet_mro(pane_assign)
    assert_no_fixed_width_overflow(pane_assign, 360)


def test_f10_quiz_activity_view_360dp_choices_compact_meta_and_collapsed_jump_grid() -> None:
    svc = FakeActivityService()
    quiz_view = QuizActivityView(
        source="courses",
        course_id=101,
        module=make_module(701, modname="quiz", name="Kiểm tra giữa kỳ"),
        activity_service=svc,
        on_close=lambda: None,
        on_open_webview=lambda *_a: None,
        pane_width=360.0,
    )
    _wait_until(lambda: any("Mở bài" in t for t in collect_texts(quiz_view)), label="quiz summary")
    assert_valid_flet_mro(quiz_view)
    assert_no_fixed_width_overflow(quiz_view, 360)

    # Compact meta label width (< 150) on 360dp
    meta_label = next(
        (c for c in walk_controls(quiz_view) if isinstance(c, ft.Text) and c.value == "Mở bài"),
        None,
    )
    assert meta_label is not None
    assert meta_label.width is not None and float(meta_label.width) < 150.0

    # Open 50-question attempt review at 360dp
    _find_button_with_text(quiz_view, "Xem lại").on_click(None)
    _wait_until(
        lambda: any("Mục lục câu hỏi" in t for t in collect_texts(quiz_view)),
        label="quiz review",
    )
    assert_valid_flet_mro(quiz_view)
    assert_no_fixed_width_overflow(quiz_view, 360)

    jump_tiles = [c for c in walk_controls(quiz_view) if isinstance(c, ft.ExpansionTile)]
    assert jump_tiles, "Expected collapsible question jump ExpansionTile"
    assert jump_tiles[0].expanded is False, "Jump grid must be collapsed by default on <600dp"

    # Verify choice rows expand text properly
    choice_texts = [
        c
        for c in walk_controls(quiz_view)
        if isinstance(c, ft.Text) and str(c.value or "").startswith("Phương án A cho câu 1")
    ]
    assert choice_texts and choice_texts[0].expand is True


def test_f10_courses_tab_view_360_390_430dp_full_workspace_no_overflow(
    fake_page: FakePage, platform_profile
) -> None:
    platform_profile("android", page=fake_page)
    course = make_course(101, credits=3, has_unseen_updates=True, is_pinned=True)
    sec = make_section(
        1,
        modules=(
            make_module(11, modname="resource", name="Bài giảng 1", attachments=(make_attachment("b1.pdf"),)),
            make_module(12, modname="assign", name="Bài tập tuần 1"),
        ),
    )
    svc = FakeCatalogService(
        courses=[course],
        recent=[make_recent()],
        detail_map={
            ("courses", 101): CourseDetailSnapshot(
                sections=[sec],
                grades=[make_grade(1)],
                last_synced_at=1_770_000_000,
            )
        },
    )
    tab = CoursesTabView(page=fake_page, catalog_service=svc)
    asyncio.run(tab.load_initial_async())

    for width in (360, 390, 430):
        fake_page.resize(width, 800)
        tab.set_viewport_size(width, 800)
        tab._state.in_detail_mobile = False
        tab._render()
        assert_valid_flet_mro(tab)
        assert_no_fixed_width_overflow(tab, width, allow_horizontal_scroll_descendants=True)

        asyncio.run(tab._on_user_select_course_async(course.course_key))
        assert tab._state.in_detail_mobile is True
        assert_valid_flet_mro(tab)
        assert_no_fixed_width_overflow(tab, width, allow_horizontal_scroll_descendants=True)


# ---------------------------------------------------------------------------
# F11: Mobile Ergonomics, Scroll Preservation, Immersive & Landscape (>= 5 tests)
# ---------------------------------------------------------------------------


def test_f11_master_list_stays_mounted_for_scroll_and_hides_selected_border_on_phone(
    fake_page: FakePage, platform_profile
) -> None:
    fake_page.resize(360, 780)
    platform_profile("android", page=fake_page)
    c1 = make_course(101, display_name="Môn học 1")
    c2 = make_course(102, display_name="Môn học 2")
    svc = FakeCatalogService(courses=[c1, c2])

    tab = CoursesTabView(page=fake_page, catalog_service=svc)
    tab.set_viewport_size(360, 780)
    asyncio.run(tab.load_initial_async())

    list_pane_id = id(tab._list_pane)
    cards_col_id = id(tab._list_pane._cards_list)

    # Push to detail mobile
    asyncio.run(tab._on_user_select_course_async(c2.course_key))
    assert tab._state.in_detail_mobile is True
    # _list_pane remains mounted inside _narrow_stack behind workspace host
    assert tab._narrow_list_host in tab._narrow_stack.controls
    assert tab._narrow_list_host.content is tab._list_pane
    assert id(tab._list_pane) == list_pane_id
    assert id(tab._list_pane._cards_list) == cards_col_id

    # Return to phone list via Back
    assert tab.handle_back() is True
    assert tab._state.in_detail_mobile is False
    assert id(tab._list_pane) == list_pane_id
    assert id(tab._list_pane._cards_list) == cards_col_id

    # On phone list (not in_detail_mobile), selected border must NOT be highlighted
    c2_card = tab._list_pane._card_map[c2.course_key]
    assert c2_card.border == ft.Border.all(1, C.BORDER)

    # On wide desktop, selected border IS highlighted with C.ACCENT
    tab.set_viewport_size(1100, 780)
    c2_card_wide = tab._list_pane._card_map[c2.course_key]
    assert c2_card_wide.border == ft.Border.all(1.5, C.ACCENT)


def test_f11_pin_and_download_touch_targets_and_ctrl_k_hint_gated_by_platform(
    fake_page: FakePage, platform_profile
) -> None:
    fake_page.resize(360, 780)
    platform_profile("android", page=fake_page)
    course = make_course(101)
    sec = make_section(
        1,
        modules=(
            make_module(10, modname="resource", name="Tài liệu PDF", attachments=(make_attachment("doc.pdf"),)),
        ),
    )
    svc = FakeCatalogService(
        courses=[course],
        detail_map={("courses", 101): CourseDetailSnapshot(sections=[sec], grades=[], last_synced_at=1_770_000_000)},
    )
    tab = CoursesTabView(page=fake_page, catalog_service=svc)
    tab.set_viewport_size(360, 780)
    asyncio.run(tab.load_initial_async())
    asyncio.run(tab._on_user_select_course_async(course.course_key))

    # Pin button in CourseListPane >= 44dp
    pin_btns = _find_icon_buttons(tab._list_pane, "ghim")
    assert pin_btns, "Expected Pin IconButton on course card"
    for btn in pin_btns:
        assert_min_touch_target(btn, 44.0)

    # Download button in CourseDetailPane >= 44dp
    dl_btns = _find_icon_buttons(tab._detail_pane, "Tải xuống")
    assert dl_btns, "Expected Download IconButton on attachment row"
    for btn in dl_btns:
        assert_min_touch_target(btn, 44.0)

    # (Ctrl+K) desktop hint must be hidden when UI_IS_MOBILE=True
    assert "Ctrl+K" not in str(tab._list_pane._search_field.hint_text or "")

    # Switching to desktop platform shows (Ctrl+K) hint
    platform_profile("desktop", page=fake_page)
    tab._list_pane.render(tab._state, update=False)
    assert "Ctrl+K" in str(tab._list_pane._search_field.hint_text or "")


def test_f11_immersive_overlay_hides_bottom_nav_bar_and_restores_on_pop(
    fake_page: FakePage, platform_profile
) -> None:
    fake_page.resize(360, 780)
    platform_profile("android", page=fake_page)

    shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    shell.apply_width(360)
    assert shell.bar.visible is True
    assert shell.rail_visible is False

    tab = CoursesTabView(page=fake_page, on_immersive_changed=shell.set_immersive)
    tab.set_viewport_size(360, 780)
    assert shell.bar.visible is True

    # Push Pane-3 overlay on mobile -> immersive hides bottom NavigationBar
    overlay = PaneLoadingOverlay(title="Đang tải", on_back=tab.pop_overlay)
    tab.push_overlay(overlay, tag="test:immersive")
    assert shell.bar.visible is False

    # Pop overlay -> bottom NavigationBar is restored
    assert tab.pop_overlay() is True
    assert shell.bar.visible is True


def test_f11_landscape_780x360_rail_scrolls_banners_scroll_and_metadata_collapses(
    fake_page: FakePage, platform_profile
) -> None:
    fake_page.resize(780, 360)
    platform_profile("android", page=fake_page)

    shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    assert shell.apply_width(780) is True
    assert shell.apply_height(360) is True
    assert shell.rail_host.visible is True
    assert shell.rail.scrollable is True

    course = make_course(101, credits=3, teacher_name="TS. Nguyễn Văn A", meet_url="https://meet.google.com/abc-defg-hij")
    svc = FakeCatalogService(
        courses=[course],
        recent=[make_recent()],
        detail_map={("courses", 101): CourseDetailSnapshot(sections=[make_section(1)], grades=[], last_synced_at=1_770_000_000)},
    )
    tab = CoursesTabView(page=fake_page, catalog_service=svc)
    tab.set_viewport_size(780, 360)
    asyncio.run(tab.load_initial_async())
    tab._state.offline_notice = "Đang xem bản lưu ngoại tuyến"
    asyncio.run(tab._on_user_select_course_async(course.course_key))

    # List banners scroll with list on compact height (<500dp)
    assert tab._list_pane._notice_slot.visible is False
    assert tab._list_pane._recent_slot.visible is False
    list_texts = collect_texts(tab._list_pane._cards_list)
    assert any("Tiếp tục nhanh" in t for t in list_texts)

    # CourseDetailPane collapses metadata behind "Thông tin môn" on compact height
    assert any("Thông tin môn" in t for t in collect_texts(tab._detail_pane))
    toggle = _find_clickable_with_text(tab._detail_pane, "Thông tin môn")
    toggle.on_click(None)
    assert tab._detail_pane._meta_expanded is True


def test_f11_multi_item_recent_banner_expand_and_cap_at_5() -> None:
    recents = [
        make_recent(
            cmid=i,
            title=f"Bài giảng số {i}",
            file_name=f"slide_{i}.pdf",
            attachment_url=f"https://courses.ut.edu.vn/pluginfile.php/{i}/slide_{i}.pdf",
        )
        for i in range(1, 8)
    ]
    course = make_course(101)
    state = CoursesState(courses=[course], recent_items=recents, layout_mode="mobile")
    pane = CourseListPane()
    pane.render(state, update=False)

    # Collapsed initially: shows first item + expand button for 4 more (capped at 5 total)
    texts_collapsed = collect_texts(pane)
    assert any("slide_1.pdf" in t for t in texts_collapsed)
    assert not any("slide_2.pdf" in t for t in texts_collapsed)
    expand_btns = _find_icon_buttons(pane, "Xem thêm 4 mục gần đây")
    assert len(expand_btns) == 1
    assert_min_touch_target(expand_btns[0], 44.0)

    # Expand -> items 1..5 visible, items 6..7 capped out
    expand_btns[0].on_click(None)
    texts_expanded = collect_texts(pane)
    for idx in range(1, 6):
        assert any(f"slide_{idx}.pdf" in t for t in texts_expanded)
    assert not any("slide_6.pdf" in t for t in texts_expanded)
    assert not any("slide_7.pdf" in t for t in texts_expanded)


def test_f11_offline_banner_retry_cta_invokes_callback_and_omits_when_none() -> None:
    calls: list[int] = []
    banner_with_retry = build_offline_banner("Mất kết nối mạng", on_retry=lambda: calls.append(1))
    assert_valid_flet_mro(banner_with_retry)
    retry_btn = _find_button_with_text(banner_with_retry, "Thử lại")
    retry_btn.on_click(None)
    assert calls == [1]

    banner_no_retry = build_offline_banner("Chế độ ngoại tuyến", on_retry=None)
    assert_valid_flet_mro(banner_no_retry)
    assert not any("Thử lại" in t for t in collect_texts(banner_no_retry))
