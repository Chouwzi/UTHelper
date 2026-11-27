from __future__ import annotations

import flet as ft
from types import SimpleNamespace
from core.academic_radar import (
    CourseGradeItem,
    SemesterGradeData,
    calculate_radar_profile,
)
from gui.components.academic_radar.metric_detail_sheet import MetricDetailSheet
from gui.components.academic_radar.radar_card import AcademicRadarCard
from gui.components.academic_radar.radar_chart import AcademicRadarChart
from gui.components.grade_overview_view import GradeOverviewView


def _make_sample_semester(sem_id=75, sem_name="HK2 2025-2026"):
    courses = [
        CourseGradeItem("1", "CS1", "Kiến trúc máy tính", 3, 8.9, 9.8, 9.5, 4.0, "A+"),
        CourseGradeItem("2", "CS2", "Lập trình Java", 3, 7.7, 7.0, 7.4, 3.0, "B"),
        CourseGradeItem("3", "CS3", "Lập trình Web", 3, 7.9, 8.5, 8.2, 3.5, "B+"),
        CourseGradeItem("4", "CS4", "An toàn thông tin", 3, 9.0, 9.5, 9.3, 3.8, "A"),
        CourseGradeItem("5", "CS5", "Lập trình mạng", 3, 9.8, 9.1, 9.3, 3.8, "A"),
        CourseGradeItem("6", "CS6", "Cơ sở dữ liệu phân tán", 3, 8.5, 9.8, 9.3, 3.8, "A"),
    ]
    summary = {
        "diemTBHocLuc": 8.77,
        "diemTBTinChi": 3.75,
        "stcdatHocKy": 18,
        "soDiemRenLuyen": 83,
        "xepLoaiHocLuc": "Xuất sắc",
    }
    return SemesterGradeData(id=sem_id, name=sem_name, summary=summary, courses=courses)


def test_grade_overview_view_init_and_loading():
    closed = []
    refreshed = []
    view = GradeOverviewView(
        on_close=lambda: closed.append(True),
        on_refresh=lambda: refreshed.append(True),
    )

    assert view.visible is False
    view.show_loading()
    assert view.visible is True
    assert view._loading_row.visible is True
    assert view._radar_card.visible is False


def test_grade_overview_view_update_academic_data():
    page = SimpleNamespace(
        dialogs=[],
        show_dialog=lambda d: None,
        update=lambda: None,
    )
    view = GradeOverviewView(page=page)
    sem1 = _make_sample_semester(75, "HK2 2025-2026")
    sem2 = _make_sample_semester(74, "HK1 2025-2026")

    p1 = calculate_radar_profile(sem1, previous_semester=sem2)
    p2 = calculate_radar_profile(sem2)

    view.update_academic_data([sem1, sem2], [p1, p2])

    assert view._radar_card.visible is True
    assert view._grade_list_view.visible is True
    assert view._empty_state.visible is False
    assert len(view._grade_list_view.courses) == 6


def test_radar_card_and_chart_interaction():
    sem1 = _make_sample_semester(75, "HK2 2025-2026")
    sem2 = _make_sample_semester(74, "HK1 2025-2026")
    p1 = calculate_radar_profile(sem1, previous_semester=sem2)
    p2 = calculate_radar_profile(sem2)

    card = AcademicRadarCard(
        profiles=[p1, p2],
        on_semester_change=lambda idx: None,
    )

    assert card.current_profile == p1
    assert card.previous_profile == p2

    # Toggle comparison mode
    card._toggle_compare(None)
    assert card._is_comparing is True
    assert card._compare_btn.content == "Đang so sánh"

    # Test tapping a metric
    metric = p1.metrics["breakthrough"]
    card._on_metric_click(metric)


def test_metric_detail_sheet_modal_presentation():
    sem = _make_sample_semester(75, "HK2 2025-2026")
    profile = calculate_radar_profile(sem)
    metric = profile.metrics["breakthrough"]

    shown_dialogs = []
    page = SimpleNamespace(
        dialogs=[],
        overlay=[],
        show_dialog=shown_dialogs.append,
        update=lambda: None,
    )

    MetricDetailSheet.show(page, metric)
    assert len(shown_dialogs) == 1
    dialog = shown_dialogs[0]
    assert isinstance(dialog, ft.AlertDialog)


def test_grade_overview_view_theme_update():
    view = GradeOverviewView()
    sem = _make_sample_semester(75, "HK2 2025-2026")
    profile = calculate_radar_profile(sem)
    view.update_academic_data([sem], [profile])

    # Should not raise exception
    view.update_theme()


def test_academic_radar_and_grade_overview_modals_dismiss_on_escape_via_pop_dialog():
    from core.academic_radar.overall import OverallGpa
    from gui.components.academic_radar.course_components_dialog import (
        show_course_components_dialog,
    )
    from gui.components.academic_radar.expected_grade_dialog import ExpectedGradeDialog
    from gui.components.academic_radar.gpa_detail_sheet import GpaDetailSheet

    sem = _make_sample_semester(75, "HK2 2025-2026")
    profile = calculate_radar_profile(sem)
    metric = profile.metrics["breakthrough"]
    overall = OverallGpa(gpa_10=8.5, gpa_4=3.5, total_credits=18, semester_count=1, course_count=6)

    def _make_page():
        shown = []
        pops = []
        return (
            SimpleNamespace(
                width=400,
                height=800,
                dialogs=[],
                overlay=[],
                show_dialog=shown.append,
                pop_dialog=lambda: pops.append(True),
                update=lambda: None,
            ),
            shown,
            pops,
        )

    # 1. MetricDetailSheet
    p1, s1, pops1 = _make_page()
    MetricDetailSheet.show(p1, metric)
    assert isinstance(s1[0].content, ft.KeyboardListener)
    s1[0].content.on_key_down(SimpleNamespace(key="Enter"))
    assert pops1 == []
    s1[0].content.on_key_down(SimpleNamespace(key="Escape"))
    assert pops1 == [True]

    # 2. GpaDetailSheet
    p2, s2, pops2 = _make_page()
    GpaDetailSheet.show(p2, profile)
    assert isinstance(s2[0].content, ft.KeyboardListener)
    s2[0].content.on_key_down(SimpleNamespace(key="Escape"))
    assert pops2 == [True]
    s2[0].actions[0].on_click(None)
    assert pops2 == [True, True]

    # 3. ExpectedGradeDialog
    p3, s3, pops3 = _make_page()
    ExpectedGradeDialog.show(p3, overall)
    assert isinstance(s3[0].content, ft.KeyboardListener)
    s3[0].content.on_key_down(SimpleNamespace(key="Escape"))
    assert pops3 == [True]
    s3[0].actions[0].on_click(None)
    assert pops3 == [True, True]

    # 4. show_course_components_dialog
    p4, s4, pops4 = _make_page()
    show_course_components_dialog(p4, sem.courses[0])
    assert isinstance(s4[0].content, ft.KeyboardListener)
    s4[0].content.on_key_down(SimpleNamespace(key="Escape"))
    assert pops4 == [True]
    s4[0].actions[0].on_click(None)
    assert pops4 == [True, True]

    # 5. GradeOverviewView._open_planner -> _show_planner_dialog
    p5, s5, pops5 = _make_page()
    gv = GradeOverviewView(page=p5)
    gv.update_academic_data([sem], [profile])
    gv._open_planner(None)
    assert isinstance(s5[0].content, ft.KeyboardListener)
    s5[0].content.on_key_down(SimpleNamespace(key="Escape"))
    assert pops5 == [True]
    s5[0].actions[0].on_click(None)
    assert pops5 == [True, True]

    # 6. AcademicRadarCard._show_radar_info
    p6, s6, pops6 = _make_page()
    card = AcademicRadarCard(profiles=[profile], page=p6)
    card._show_radar_info(None)
    assert isinstance(s6[0].content, ft.KeyboardListener)
    s6[0].content.on_key_down(SimpleNamespace(key="Enter"))
    assert pops6 == []
    s6[0].content.on_key_down(SimpleNamespace(key="Escape"))
    assert pops6 == [True]
    s6[0].actions[0].on_click(None)
    assert pops6 == [True, True]

    # 6b. AcademicRadarCard._show_radar_info overlay fallback
    overlay_list = []

    def _raise_show_dialog(_dlg):
        raise RuntimeError("no show_dialog")

    p6_overlay = SimpleNamespace(
        width=400,
        height=800,
        overlay=overlay_list,
        show_dialog=_raise_show_dialog,
        update=lambda: None,
    )
    card_overlay = AcademicRadarCard(profiles=[profile], page=p6_overlay)
    card_overlay._show_radar_info(None)
    assert len(overlay_list) == 1
    assert overlay_list[0].open is True
    overlay_list[0].content.on_key_down(SimpleNamespace(key="Escape"))
    assert overlay_list[0].open is False


def test_grade_overview_update_theme_short_circuits_and_defers_updates_in_view_manager():
    from config import settings
    from gui.core.theme import apply_theme
    from gui.view_manager import ViewManager

    original_theme = settings.THEME
    update_calls = []
    page_updates = []
    page = SimpleNamespace(
        width=1120,
        height=760,
        update=lambda: page_updates.append(True),
        dialogs=[],
        overlay=[],
        show_dialog=lambda _d: None,
    )
    try:
        apply_theme("midnight_blue")
        view = GradeOverviewView(page=page)
        sem = _make_sample_semester(75, "HK2 2025-2026")
        profile = calculate_radar_profile(sem)
        view.update_academic_data([sem], [profile])

        radar_calls = []
        list_calls = []
        orig_radar = view._radar_card.update_theme
        orig_list = view._grade_list_view.update_theme
        view._radar_card.update_theme = lambda **kw: (radar_calls.append(kw), orig_radar(**kw))
        view._grade_list_view.update_theme = lambda **kw: (list_calls.append(kw), orig_list(**kw))
        view.update = lambda: update_calls.append(True)

        # Unchanged palette: short-circuits completely.
        view.update_theme()
        assert radar_calls == []
        assert list_calls == []
        assert update_calls == []

        # Changed palette inside ViewManager.show_grades_loading(): 1 page.update(), update=False on children.
        apply_theme("steel_light")
        vm = ViewManager(
            page=page,
            dashboard=SimpleNamespace(visible=True),
            detail_view=SimpleNamespace(visible=False),
            settings_view=SimpleNamespace(visible=False),
            calendar_view=SimpleNamespace(visible=False),
            grade_overview_view=view,
            controller=SimpleNamespace(
                calendar_btn=SimpleNamespace(icon_color=None),
                grades_btn=SimpleNamespace(icon_color=None),
                courses_btn=SimpleNamespace(icon_color=None),
            ),
        )
        vm.show_grades_loading()
        assert radar_calls == [{"update": False}]
        assert list_calls == [{"update": False}]
        assert update_calls == []
        assert len(page_updates) == 1
    finally:
        apply_theme(original_theme)


def test_grade_overview_header_prevents_wrapping_and_keeps_touch_targets():
    page = SimpleNamespace(width=360, height=800, update=lambda: None)
    view = GradeOverviewView(page=page)

    assert view._title.max_lines == 1
    assert view._title.no_wrap is True
    assert view._title.overflow == ft.TextOverflow.ELLIPSIS
    assert view._header.spacing <= 4
    assert view._courses_btn.height == 44
    assert view._courses_btn.style.padding == ft.Padding.symmetric(horizontal=8, vertical=0)

    view.set_width(320)
    assert view._title.size == 15
    view.set_width(400)
    assert view._title.size == 16


def test_radar_card_and_chart_fit_360px_mobile_without_clipping():
    sem1 = _make_sample_semester(75, "Học kỳ hè năm học 2025-2026")
    sem2 = _make_sample_semester(74, "HK1 2025-2026")
    p1 = calculate_radar_profile(sem1, previous_semester=sem2)
    p2 = calculate_radar_profile(sem2)

    card = AcademicRadarCard(
        profiles=[p1, p2],
        page=SimpleNamespace(width=360, height=800),
    )

    assert 220.0 <= card._radar_chart.width <= 294.0
    assert card._compare_btn.content == "So sánh kỳ trước"
    header_row = card.content.controls[0]
    assert isinstance(header_row, ft.Row)
    assert header_row.wrap is True
    assert card._sem_dropdown.expand is True
    assert card._sem_dropdown.expanded_insets == ft.Padding.zero()
    assert isinstance(card.content.controls[1], ft.Row)
    assert card.content.controls[1].controls == [card._sem_dropdown]

    for node in card._radar_chart.content.controls[1:]:
        assert node.left >= 0.0
        assert node.left + node.width <= card._radar_chart._chart_width
        assert node.top >= 0.0
        assert node.top + 50.0 <= card._radar_chart._chart_height

    card.set_viewport_width(390)
    assert card._radar_chart.width == 322.0


def test_planner_and_metric_detail_dialogs_fit_360px_viewport_without_overflow():
    sem = _make_sample_semester(75, "HK2 2025-2026")
    profile = calculate_radar_profile(sem)
    metric = profile.metrics["breakthrough"]

    shown_planner = []
    page_planner = SimpleNamespace(
        width=360,
        height=800,
        dialogs=[],
        overlay=[],
        show_dialog=shown_planner.append,
        pop_dialog=lambda: None,
        update=lambda: None,
    )
    gv = GradeOverviewView(page=page_planner)
    gv.update_academic_data([sem], [profile])
    gv._open_planner(None)

    planner_dlg = shown_planner[0]
    assert planner_dlg.inset_padding == ft.Padding.symmetric(horizontal=16, vertical=24)
    assert planner_dlg.content.content.width <= 296.0
    assert gv._gpa_target_card.content.controls[0].controls[1].expand is True
    assert gv._graduation_card.content.controls[0].controls[1].expand is True
    assert gv._gpa_target_card._target_field.expand is True
    assert gv._gpa_target_card._target_field.width is None
    assert gv._graduation_card._target_field.expand is True
    assert gv._graduation_card._target_field.width is None

    shown_metric = []
    page_metric = SimpleNamespace(
        width=360,
        height=800,
        dialogs=[],
        overlay=[],
        show_dialog=shown_metric.append,
        pop_dialog=lambda: None,
        update=lambda: None,
    )
    MetricDetailSheet.show(page_metric, metric)
    metric_dlg = shown_metric[0]
    assert metric_dlg.inset_padding == ft.Padding.symmetric(horizontal=16, vertical=24)
    assert metric_dlg.content.content.width <= 296.0
    assert metric_dlg.content.content.padding is None


def _collect_texts(root) -> list[str]:
    texts: list[str] = []
    stack = [root]
    while stack:
        cur = stack.pop()
        if cur is None:
            continue
        if isinstance(cur, ft.Text) and cur.value is not None:
            texts.append(str(cur.value))
        for attr in ("content", "title", "subtitle"):
            child = getattr(cur, attr, None)
            if child is not None and not isinstance(child, str):
                stack.append(child)
        for seq_attr in ("controls", "actions"):
            seq = getattr(cur, seq_attr, None)
            if isinstance(seq, (list, tuple)):
                stack.extend(seq)
    return texts


def _walk_controls(root):
    stack = [root]
    while stack:
        cur = stack.pop()
        if cur is None or isinstance(cur, str):
            continue
        yield cur
        for attr in ("content", "title", "subtitle"):
            child = getattr(cur, attr, None)
            if child is not None and not isinstance(child, str):
                stack.append(child)
        for seq_attr in ("controls", "actions"):
            seq = getattr(cur, seq_attr, None)
            if isinstance(seq, (list, tuple)):
                stack.extend(seq)


def test_course_components_dialog_two_tabs_portal_and_course_parity():
    import dataclasses
    from core.academic_radar.models import ComponentScore, CourseEligibility
    from core.courses.models import CourseGradeItem as CoreMoodleGradeItem
    from gui.components.academic_radar.course_components_dialog import (
        MoodleCourseGradesResult,
        show_course_components_dialog,
    )

    course = CourseGradeItem(
        course_id="501",
        course_code="012000101",
        course_name="Lập trình Python",
        credits=3,
        process_grade=8.5,
        exam_grade=9.0,
        final_grade=8.8,
        credit_grade=4.0,
        letter_grade="A",
        is_passed=True,
        eligibility=CourseEligibility(status="academic", reason="Tính GPA chính khóa"),
        components=(ComponentScore("Chuyên cần", 9.5, 20.0),),
        subject_code="0120001",
        class_code="012000101",
        pass_status=True,
        extra_portal_fields={"diemThiLan1": 9.0, "hinhThucThi": "Vấn đáp"},
    )
    moodle_res = MoodleCourseGradesResult(
        grades=[
            CoreMoodleGradeItem(
                id=1,
                item_name="Quiz 1 (20%)",
                item_module="quiz",
                grade_formatted="8.5",
                range_formatted="0–10",
                percentage="85 %",
                feedback_html="<p>Làm tốt</p>",
            ),
            CoreMoodleGradeItem(
                id=2,
                item_name="Course total",
                item_module="course",
                grade_formatted="4.0",
                range_formatted="0–10",
                percentage="40 %",
                weight="100 %",
            ),
            CoreMoodleGradeItem(
                id=3,
                item_name="Bài tự luyện thêm",
                item_module="assign",
                grade_formatted="-",
            ),
        ],
        course_id=42,
        course_title="Lập trình Python [HK1]",
        source="courses",
        last_synced_at=1_770_000_000,
    )

    shown = []
    page = SimpleNamespace(
        width=360,
        height=800,
        dialogs=[],
        overlay=[],
        show_dialog=shown.append,
        pop_dialog=lambda: None,
        update=lambda: None,
    )
    show_course_components_dialog(page, course, moodle_result=moodle_res)
    dlg = shown[0]

    assert dlg.inset_padding == ft.Padding.symmetric(horizontal=16, vertical=24)
    assert isinstance(dlg.content, ft.KeyboardListener)
    assert dlg.content.autofocus is False
    dialog_container = dlg.content.content
    assert dialog_container.width <= 296.0

    main_col = dialog_container.content
    tab_track = main_col.controls[0]
    body_host = main_col.controls[1]
    tab_pills = tab_track.content.controls
    assert len(tab_pills) == 2

    # Tab 0 = Portal: shows component + omitted Portal metadata, never repeats outer QT/Thi/TK rows
    portal_texts = _collect_texts(body_host)
    assert "Chuyên cần" in portal_texts
    assert "4.00" in portal_texts
    assert "0120001" in portal_texts
    assert "012000101" in portal_texts
    assert "Điểm thi lần 1" in portal_texts
    assert "Vấn đáp" in portal_texts
    assert "Điểm quá trình" not in portal_texts
    assert "Tổng khóa học" not in portal_texts

    # Switch to Tab 1 = Course
    tab_pills[1].on_click(None)
    course_texts = _collect_texts(body_host)
    assert "ĐIỂM ĐÃ CẬP NHẬT" in course_texts
    assert "Quiz 1 (20%)" in course_texts
    assert "Tổng kết" in course_texts
    assert "Trọng số 20%" in course_texts
    assert "Làm tốt" in course_texts
    assert "Chưa có điểm / Bài tự luyện" in course_texts
    assert "Nguồn: Moodle (Môn học)" in course_texts

    # Verify Flet invariants: no expand=True inside Row(wrap=True) & ExpansionTile uses OutlinedBorder
    for ctrl in _walk_controls(dlg):
        if isinstance(ctrl, ft.Row) and bool(getattr(ctrl, "wrap", False)):
            for ch in ctrl.controls:
                assert not getattr(ch, "expand", None)
        if isinstance(ctrl, ft.ExpansionTile):
            assert isinstance(ctrl.shape, ft.OutlinedBorder)
            assert isinstance(ctrl.collapsed_shape, ft.OutlinedBorder)
        if isinstance(ctrl, ft.Container) and ctrl.margin is not None:
            assert isinstance(ctrl.margin, ft.Margin)
        for f in dataclasses.fields(ctrl):
            if f.name in {"padding", "inset_padding", "title_padding", "content_padding", "actions_padding"}:
                val = getattr(ctrl, f.name, None)
                if val is not None:
                    assert isinstance(val, ft.Padding)


def test_course_components_dialog_course_tab_resolves_async_loader():
    import asyncio
    from core.courses.models import CourseGradeItem as CoreMoodleGradeItem
    from gui.components.academic_radar.course_components_dialog import (
        MoodleCourseGradesResult,
        show_course_components_dialog,
    )

    course = CourseGradeItem("1", "CS1", "Algorithms", 3, 8.0, 8.5, 8.3)
    initial = MoodleCourseGradesResult(needs_async_fetch=True)

    async def _loader(_course):
        return MoodleCourseGradesResult(
            grades=[
                CoreMoodleGradeItem(
                    id=10,
                    item_name="Midterm",
                    item_module="quiz",
                    grade_formatted="8.5",
                )
            ],
            course_id=77,
            course_title="Algorithms",
            source="courses",
        )

    async def scenario():
        shown = []
        scheduled = []
        page = SimpleNamespace(
            width=1000,
            height=800,
            dialogs=[],
            overlay=[],
            show_dialog=shown.append,
            pop_dialog=lambda: None,
            update=lambda: None,
            run_task=lambda fn: scheduled.append(asyncio.create_task(fn())),
        )
        show_course_components_dialog(
            page,
            course,
            moodle_result=initial,
            async_loader=_loader,
        )
        dlg = shown[0]
        main_col = dlg.content.content.content
        tab_pills = main_col.controls[0].content.controls
        body_host = main_col.controls[1]

        tab_pills[1].on_click(None)
        assert "Đang tải bảng điểm Moodle..." in _collect_texts(body_host)

        await asyncio.gather(*scheduled)
        texts_after = _collect_texts(body_host)
        assert "Midterm" in texts_after
        assert "8.5" in texts_after

    asyncio.run(scenario())


def test_all_academic_radar_dialogs_keep_keyboard_listener_with_autofocus_false():
    from core.academic_radar.overall import OverallGpa
    from gui.components.academic_radar.course_components_dialog import (
        show_course_components_dialog,
    )
    from gui.components.academic_radar.expected_grade_dialog import ExpectedGradeDialog
    from gui.components.academic_radar.gpa_detail_sheet import GpaDetailSheet

    sem = _make_sample_semester(75, "HK2 2025-2026")
    profile = calculate_radar_profile(sem)
    metric = profile.metrics["breakthrough"]
    overall = OverallGpa(gpa_10=8.5, gpa_4=3.5, total_credits=18, semester_count=1, course_count=6)

    def _page():
        shown = []
        return (
            SimpleNamespace(
                width=400,
                height=800,
                dialogs=[],
                overlay=[],
                show_dialog=shown.append,
                pop_dialog=lambda: None,
                update=lambda: None,
            ),
            shown,
        )

    p1, s1 = _page()
    MetricDetailSheet.show(p1, metric)
    assert isinstance(s1[0].content, ft.KeyboardListener)
    assert s1[0].content.autofocus is False

    p2, s2 = _page()
    GpaDetailSheet.show(p2, profile)
    assert isinstance(s2[0].content, ft.KeyboardListener)
    assert s2[0].content.autofocus is False

    p3, s3 = _page()
    ExpectedGradeDialog.show(p3, overall)
    assert isinstance(s3[0].content, ft.KeyboardListener)
    assert s3[0].content.autofocus is False

    p4, s4 = _page()
    show_course_components_dialog(p4, sem.courses[0])
    assert isinstance(s4[0].content, ft.KeyboardListener)
    assert s4[0].content.autofocus is False

    p5, s5 = _page()
    gv = GradeOverviewView(page=p5)
    gv.update_academic_data([sem], [profile])
    gv._open_planner(None)
    assert isinstance(s5[0].content, ft.KeyboardListener)
    assert s5[0].content.autofocus is False

    p6, s6 = _page()
    card = AcademicRadarCard(profiles=[profile], page=p6)
    card._show_radar_info(None)
    assert isinstance(s6[0].content, ft.KeyboardListener)
    assert s6[0].content.autofocus is False


def test_grade_overview_preserves_and_restores_scroll_offset_across_modal_dismissals():
    import asyncio
    import warnings

    sem = _make_sample_semester(75, "HK2 2025-2026")
    profile = calculate_radar_profile(sem)

    # 1. Synchronous test: verifies key, auto_scroll=False, synthetic 0.0 guard, and zero unawaited coro warnings
    shown_sync = []
    scroll_calls_sync = []
    page_sync = SimpleNamespace(
        width=400,
        height=800,
        dialogs=[],
        overlay=[],
        show_dialog=shown_sync.append,
        pop_dialog=lambda: None,
        update=lambda: None,
    )
    gv_sync = GradeOverviewView(page=page_sync)
    gv_sync.update_academic_data([sem], [profile])
    assert gv_sync._scrollable_content.key == "grade_overview_scroll"
    assert gv_sync._scrollable_content.auto_scroll is False
    assert callable(gv_sync._scrollable_content.on_scroll)

    gv_sync._scrollable_content.scroll_to = lambda **kw: scroll_calls_sync.append(kw)
    gv_sync._on_content_scroll(SimpleNamespace(pixels=640.0, min_scroll_extent=0.0, out_of_range=False))
    assert gv_sync._saved_scroll_offset == 640.0

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", RuntimeWarning)
        gv_sync._on_course_clicked(None, sem.courses[0])
        assert len(gv_sync._open_dialog_tokens) == 1
        assert scroll_calls_sync == [{"offset": 640.0, "duration": 0}]
        # Synthetic focus-jump scroll event while modal is open must NOT clobber saved offset and counter-pins
        gv_sync._on_content_scroll(SimpleNamespace(pixels=0.0, min_scroll_extent=0.0, out_of_range=False))
        assert gv_sync._saved_scroll_offset == 640.0
        assert scroll_calls_sync == [
            {"offset": 640.0, "duration": 0},
            {"offset": 640.0, "duration": 0},
        ]
        # Close via button
        shown_sync[-1].actions[0].on_click(None)
        assert gv_sync._open_dialog_tokens == {}
        assert scroll_calls_sync == [
            {"offset": 640.0, "duration": 0},
            {"offset": 640.0, "duration": 0},
            {"offset": 640.0, "duration": 0},
        ]
        assert gv_sync._saved_scroll_offset == 640.0
        assert not [w for w in caught if "was never awaited" in str(w.message)]

    # 2. Async test across all 4 dialog triggers (course, planner, metric sheet, radar info) & handle_back()
    async def scenario():
        shown = []
        scroll_calls = []

        async def _fake_scroll_to(**kw):
            scroll_calls.append(kw)

        page = SimpleNamespace(
            width=400,
            height=800,
            dialogs=[],
            overlay=[],
            show_dialog=shown.append,
            pop_dialog=lambda: None,
            update=lambda: None,
        )
        gv = GradeOverviewView(page=page)
        gv.update_academic_data([sem], [profile])
        gv._scrollable_content.scroll_to = _fake_scroll_to
        gv._on_content_scroll(SimpleNamespace(pixels=520.0, min_scroll_extent=0.0, out_of_range=False))

        # a) Course dialog dismissed via handle_back()
        gv._on_course_clicked(None, sem.courses[0])
        gv._on_content_scroll(SimpleNamespace(pixels=0.0, min_scroll_extent=0.0, out_of_range=False))
        assert gv.handle_back() is True
        assert gv.handle_back() is False
        await asyncio.sleep(0.08)
        assert scroll_calls[-1] == {"offset": 520.0, "duration": 0}

        # b) Planner dialog dismissed via Escape
        gv._open_planner(None)
        gv._on_content_scroll(SimpleNamespace(pixels=0.0, min_scroll_extent=0.0, out_of_range=False))
        shown[-1].content.on_key_down(SimpleNamespace(key="Escape"))
        await asyncio.sleep(0.08)
        assert scroll_calls[-1] == {"offset": 520.0, "duration": 0}

        # c) MetricDetailSheet dismissed via on_dismiss
        gv._radar_card._on_metric_click(profile.metrics["breakthrough"])
        gv._on_content_scroll(SimpleNamespace(pixels=0.0, min_scroll_extent=0.0, out_of_range=False))
        shown[-1].on_dismiss(None)
        await asyncio.sleep(0.08)
        assert scroll_calls[-1] == {"offset": 520.0, "duration": 0}

        # d) Radar info dialog dismissed via close button
        gv._radar_card._show_radar_info(None)
        gv._on_content_scroll(SimpleNamespace(pixels=0.0, min_scroll_extent=0.0, out_of_range=False))
        shown[-1].actions[0].on_click(None)
        await asyncio.sleep(0.08)
        assert scroll_calls[-1] == {"offset": 520.0, "duration": 0}
        assert gv._saved_scroll_offset == 520.0

    asyncio.run(scenario())


def test_course_components_dialog_kv_rows_bound_long_values_and_pass_status_none():
    from core.academic_radar.models import CourseEligibility
    from gui.components.academic_radar.course_components_dialog import (
        build_portal_tab_body,
        show_course_components_dialog,
    )
    from gui.components.academic_radar.expected_grade_dialog import ExpectedGradeDialog
    from gui.components.academic_radar.gpa_detail_sheet import GpaDetailSheet
    from core.academic_radar.overall import OverallGpa

    long_reason = "Học phần điều kiện theo quy chế UTH (GDTC/GDQP/Anh văn tổng quát)"
    failing_unflagged = CourseGradeItem(
        course_id="99",
        course_code="012000101",
        course_name="Giải tích 1",
        credits=3,
        final_grade=3.0,
        credit_grade=None,
        letter_grade="",
        is_passed=True,
        eligibility=CourseEligibility(status="conditional", reason=long_reason),
        pass_status=None,
        extra_portal_fields={"ghiChu": "X" * 200},
    )

    shown = []
    page = SimpleNamespace(
        width=360,
        height=800,
        dialogs=[],
        overlay=[],
        show_dialog=shown.append,
        pop_dialog=lambda: None,
        update=lambda: None,
    )
    show_course_components_dialog(page, failing_unflagged)
    dlg = shown[0]
    body_host = dlg.content.content.content.controls[1]

    # Failing final_grade with pass_status=None must render "Chưa đạt", not "Đạt"
    portal_texts = _collect_texts(body_host)
    assert "Chưa đạt" in portal_texts
    assert "Đạt" not in portal_texts

    # Both label and value inside _build_kv_row must have bounded flex constraints on 360px
    kv_rows = [
        ctrl
        for ctrl in _walk_controls(body_host)
        if isinstance(ctrl, ft.Row)
        and len(ctrl.controls) == 2
        and all(isinstance(ch, ft.Text) for ch in ctrl.controls)
    ]
    assert kv_rows
    for row in kv_rows:
        lbl, val = row.controls
        assert lbl.expand == 2
        assert lbl.max_lines == 2
        assert lbl.overflow == ft.TextOverflow.ELLIPSIS
        assert val.expand == 3
        assert val.max_lines == 3
        assert val.overflow == ft.TextOverflow.ELLIPSIS

    # Ungraded course with pass_status=None shows "Chưa có kết quả"
    ungraded = CourseGradeItem(
        course_id="100",
        course_code="012000102",
        course_name="Giải tích 2",
        credits=3,
        final_grade=None,
        pass_status=None,
    )
    ungraded_texts = _collect_texts(build_portal_tab_body(ungraded, is_mobile=True))
    assert "Chưa có kết quả" in ungraded_texts

    # Legacy GpaDetailSheet & ExpectedGradeDialog include mobile inset/content padding on 360px
    sem = _make_sample_semester(75, "HK2 2025-2026")
    profile = calculate_radar_profile(sem)
    overall = OverallGpa(gpa_10=8.5, gpa_4=3.5, total_credits=18, semester_count=1, course_count=6)
    shown_legacy = []
    legacy_page = SimpleNamespace(
        width=360,
        height=800,
        dialogs=[],
        overlay=[],
        show_dialog=shown_legacy.append,
        pop_dialog=lambda: None,
        update=lambda: None,
    )
    GpaDetailSheet.show(legacy_page, profile)
    ExpectedGradeDialog.show(legacy_page, overall)
    for leg_dlg in shown_legacy:
        assert leg_dlg.inset_padding == ft.Padding.symmetric(horizontal=16, vertical=24)
        assert leg_dlg.content_padding == ft.Padding.only(left=16, right=16, top=12, bottom=8)


def test_grade_overview_modal_scroll_guard_races_and_pop_dialog_fallback():
    sem = _make_sample_semester(75, "HK2 2025-2026")
    profile = calculate_radar_profile(sem)

    class _FakeSession:
        def __init__(self):
            self.index = {}

        def update_control(self, *a, **kw):
            pass

        def register_control(self, *a, **kw):
            pass

        def patch_control(self, *a, **kw):
            pass

    class _RealPageDouble(ft.Page):
        run_task = None  # type: ignore[assignment]

        def __init__(self, *, raise_on_pop: bool = False):
            super().__init__(sess=_FakeSession())
            self.width = 400
            self.height = 800
            self.shown = []
            self.pop_calls = 0
            self.raise_on_pop = raise_on_pop

        def show_dialog(self, dialog):
            self.shown.append(dialog)

        def pop_dialog(self):
            self.pop_calls += 1
            if self.raise_on_pop:
                raise RuntimeError("pop_dialog failed")

        def update(self, *controls):
            pass

    # 1. Late on_dismiss after overlay close must NOT yank scroll back to stale 640.0
    real_page1 = _RealPageDouble()
    scroll_calls1 = []
    gv1 = GradeOverviewView(page=real_page1)
    gv1.update_academic_data([sem], [profile])
    gv1._scrollable_content.scroll_to = lambda **kw: scroll_calls1.append(kw)
    gv1._on_content_scroll(SimpleNamespace(pixels=640.0, min_scroll_extent=0.0, out_of_range=False))
    tok1 = gv1._begin_modal_scroll_guard()
    gv1._end_modal_scroll_guard(tok1)
    assert len(scroll_calls1) == 1
    gv1._on_content_scroll(SimpleNamespace(pixels=1200.0, min_scroll_extent=0.0, out_of_range=False))
    assert gv1._saved_scroll_offset == 1200.0
    # Second (late) _end_modal_scroll_guard(tok1) must be a strict no-op
    gv1._end_modal_scroll_guard(tok1)
    assert len(scroll_calls1) == 1
    assert gv1._saved_scroll_offset == 1200.0

    # 2. Rapid close-then-reopen before async on_dismiss & handle_back() after close button
    real_page2 = _RealPageDouble()
    scroll_calls2 = []
    gv2 = GradeOverviewView(page=real_page2)
    gv2.update_academic_data([sem], [profile])
    gv2._scrollable_content.scroll_to = lambda **kw: scroll_calls2.append(kw)
    gv2._on_content_scroll(SimpleNamespace(pixels=640.0, min_scroll_extent=0.0, out_of_range=False))

    gv2._on_course_clicked(None, sem.courses[0])
    dlg1 = real_page2.shown[-1]
    assert scroll_calls2 == [{"offset": 640.0, "duration": 0}]
    # Close Dialog 1 via "Đóng" button (async on_dismiss not yet delivered)
    dlg1.actions[0].on_click(None)
    assert real_page2.pop_calls == 1
    # Immediate handle_back() must NOT call pop_dialog() a second time
    assert gv2.handle_back() is False
    assert real_page2.pop_calls == 1

    # Open Dialog 2 before Dialog 1's on_dismiss arrives -> pins on open
    gv2._open_planner(None)
    dlg2 = real_page2.shown[-1]
    assert len(gv2._open_dialog_tokens) == 2
    assert len(scroll_calls2) == 2

    # Dialog 1's delayed on_dismiss arrives while Dialog 2 is still open -> no close scroll_to yet
    dlg1.on_dismiss(None)
    assert len(gv2._open_dialog_tokens) == 1
    assert len(scroll_calls2) == 2

    # Dialog 2 closes via handle_back(), then on_dismiss arrives -> restores scroll once on final close
    assert gv2.handle_back() is True
    assert gv2.handle_back() is False
    assert real_page2.pop_calls == 2
    dlg2.on_dismiss(None)
    assert gv2._open_dialog_tokens == {}
    assert len(scroll_calls2) == 3
    assert all(c == {"offset": 640.0, "duration": 0} for c in scroll_calls2)

    # 3. pop_dialog() raising on real ft.Page must still clear _open_dialog_tokens for all modals
    real_page3 = _RealPageDouble(raise_on_pop=True)
    gv3 = GradeOverviewView(page=real_page3)
    gv3.update_academic_data([sem], [profile])
    gv3._scrollable_content.scroll_to = lambda **kw: None
    gv3._on_content_scroll(SimpleNamespace(pixels=500.0, min_scroll_extent=0.0, out_of_range=False))

    gv3._on_course_clicked(None, sem.courses[0])
    assert len(gv3._open_dialog_tokens) == 1
    real_page3.shown[-1].actions[0].on_click(None)
    assert gv3._open_dialog_tokens == {}

    gv3._open_planner(None)
    assert len(gv3._open_dialog_tokens) == 1
    real_page3.shown[-1].actions[0].on_click(None)
    assert gv3._open_dialog_tokens == {}

    gv3._radar_card._show_radar_info(None)
    assert len(gv3._open_dialog_tokens) == 1
    real_page3.shown[-1].actions[0].on_click(None)
    assert gv3._open_dialog_tokens == {}


def test_grade_overview_notify_uses_page_overlay_without_popping_dialogs():
    dialog_calls: list[object] = []
    closed_snack = ft.SnackBar(content=ft.Text("old"), open=False)
    page = SimpleNamespace(
        width=1120,
        height=760,
        overlay=[closed_snack],
        show_dialog=lambda d: dialog_calls.append(d),
        update=lambda: None,
    )
    gv = GradeOverviewView(page=page)
    gv._notify(page, "Đã sao chép bảng điểm")

    assert dialog_calls == []
    assert closed_snack not in page.overlay
    snacks = [c for c in page.overlay if isinstance(c, ft.SnackBar)]
    assert len(snacks) == 1
    assert snacks[0].open is True
    assert snacks[0].content.value == "Đã sao chép bảng điểm"


def test_opening_course_modal_immediately_pins_scroll_offset_before_close():
    import asyncio

    sem = _make_sample_semester(75, "HK2 2025-2026")
    profile = calculate_radar_profile(sem)

    async def scenario():
        events: list[tuple[str, object]] = []
        shown: list[ft.AlertDialog] = []
        pending_tasks: list[asyncio.Task] = []

        async def _fake_scroll_to(**kw):
            events.append(("scroll_to", kw))

        def _show_dialog(dlg):
            events.append(("show_dialog", dlg.semantics_label))
            shown.append(dlg)

        page = SimpleNamespace(
            width=400,
            height=800,
            dialogs=[],
            overlay=[],
            show_dialog=_show_dialog,
            pop_dialog=lambda: events.append(("pop_dialog", True)),
            update=lambda: None,
            run_task=lambda fn: pending_tasks.append(asyncio.create_task(fn())),
        )
        gv = GradeOverviewView(page=page)
        assert gv._scrollable_content.scroll_interval == 10
        gv.update_academic_data([sem], [profile])
        gv._scrollable_content.scroll_to = _fake_scroll_to

        # When at top (0.0), opening a modal does not schedule unnecessary scroll_to
        gv._on_course_clicked(None, sem.courses[0])
        assert gv._pinned_modal_offset() == 0.0
        assert pending_tasks == []
        shown[-1].actions[0].on_click(None)
        assert gv._pinned_modal_offset() is None
        events.clear()

        # Scroll down to 580.0
        gv._on_content_scroll(
            SimpleNamespace(pixels=580.0, min_scroll_extent=0.0, out_of_range=False)
        )
        assert gv._saved_scroll_offset == 580.0

        # 1. Course modal: pins 580.0 immediately AFTER show_dialog, BEFORE close!
        gv._on_course_clicked(None, sem.courses[0])
        assert gv._pinned_modal_offset() == 580.0
        assert len(pending_tasks) == 1
        await asyncio.gather(*pending_tasks)
        pending_tasks.clear()
        assert events == [
            ("show_dialog", f"Bảng điểm {sem.courses[0].course_name}"),
            ("scroll_to", {"offset": 580.0, "duration": 0}),
        ]

        # Switching tabs inside CourseComponentsDialog (Portal -> Course -> Portal) keeps 580.0 pinned
        course_dlg = shown[-1]
        tab_pills = course_dlg.content.content.content.controls[0].content.controls
        events.clear()
        tab_pills[1].on_click(None)
        assert len(pending_tasks) == 1
        await asyncio.gather(*pending_tasks)
        pending_tasks.clear()
        assert ("scroll_to", {"offset": 580.0, "duration": 0}) in events
        assert gv._saved_scroll_offset == 580.0

        tab_pills[0].on_click(None)
        await asyncio.gather(*pending_tasks)
        pending_tasks.clear()

        course_dlg.actions[0].on_click(None)
        await asyncio.gather(*pending_tasks)
        pending_tasks.clear()
        assert gv._open_dialog_tokens == {}

        # 2. Planner modal: _pin_scroll_while_modal_open runs strictly AFTER show_dialog
        events.clear()
        gv._open_planner(None)
        assert gv._pinned_modal_offset() == 580.0
        assert len(pending_tasks) == 1
        await asyncio.gather(*pending_tasks)
        pending_tasks.clear()
        assert events == [
            ("show_dialog", "Tính GPA mong muốn"),
            ("scroll_to", {"offset": 580.0, "duration": 0}),
        ]
        shown[-1].actions[0].on_click(None)
        await asyncio.gather(*pending_tasks)
        pending_tasks.clear()

        # 3. AcademicRadarCard metric detail sheet: pins 580.0 on open
        events.clear()
        gv._radar_card._on_metric_click(profile.metrics["breakthrough"])
        assert gv._pinned_modal_offset() == 580.0
        assert len(pending_tasks) == 1
        await asyncio.gather(*pending_tasks)
        pending_tasks.clear()
        assert events[0][0] == "show_dialog"
        assert events[1] == ("scroll_to", {"offset": 580.0, "duration": 0})
        shown[-1].actions[0].on_click(None)
        await asyncio.gather(*pending_tasks)
        pending_tasks.clear()

        # 4. AcademicRadarCard radar info dialog: pins 580.0 on open
        events.clear()
        gv._radar_card._show_radar_info(None)
        assert gv._pinned_modal_offset() == 580.0
        assert len(pending_tasks) == 1
        await asyncio.gather(*pending_tasks)
        pending_tasks.clear()
        assert events == [
            ("show_dialog", "Giải thích 6 chỉ số Radar học tập"),
            ("scroll_to", {"offset": 580.0, "duration": 0}),
        ]
        shown[-1].actions[0].on_click(None)
        await asyncio.gather(*pending_tasks)
        pending_tasks.clear()
        assert gv._saved_scroll_offset == 580.0

    asyncio.run(scenario())


def test_synthetic_zero_scroll_event_while_modal_open_is_counter_pinned_across_nested_dialogs():
    sem = _make_sample_semester(75, "HK2 2025-2026")
    profile = calculate_radar_profile(sem)

    shown: list[ft.AlertDialog] = []
    pops: list[bool] = []
    scroll_calls: list[dict[str, object]] = []

    page = SimpleNamespace(
        width=400,
        height=800,
        dialogs=[],
        overlay=[],
        show_dialog=shown.append,
        pop_dialog=lambda: pops.append(True),
        update=lambda: None,
    )
    gv = GradeOverviewView(page=page)
    gv.update_academic_data([sem], [profile])

    # Verify recursion guard even if scroll_to synchronously emits a synthetic 0.0 scroll event!
    def _reentrant_scroll_to(**kw):
        scroll_calls.append(kw)
        gv._on_content_scroll(
            SimpleNamespace(pixels=0.0, min_scroll_extent=0.0, out_of_range=False)
        )

    gv._scrollable_content.scroll_to = _reentrant_scroll_to
    gv._on_content_scroll(
        SimpleNamespace(pixels=640.0, min_scroll_extent=0.0, out_of_range=False)
    )
    assert gv._saved_scroll_offset == 640.0

    # Open outer modal (GpaDetailSheet or Planner -> token 1)
    gv.open_gpa_detail_sheet()
    assert len(gv._open_dialog_tokens) == 1
    assert gv._pinned_modal_offset() == 640.0
    assert len(scroll_calls) == 1
    assert scroll_calls[-1] == {"offset": 640.0, "duration": 0}

    # Open nested ExpectedGradeDialog on top of outer modal (1 -> 2 tokens)
    gv.open_expected_grade_dialog()
    assert len(gv._open_dialog_tokens) == 2
    assert gv._pinned_modal_offset() == 640.0
    assert len(scroll_calls) == 2
    assert scroll_calls[-1] == {"offset": 640.0, "duration": 0}

    # Synthetic pixels=0.0 from Flutter route focus change -> counter-pins immediately without recursion
    gv._on_content_scroll(
        SimpleNamespace(pixels=0.0, min_scroll_extent=0.0, out_of_range=False)
    )
    assert gv._saved_scroll_offset == 640.0
    assert len(scroll_calls) == 3
    assert scroll_calls[-1] == {"offset": 640.0, "duration": 0}

    # Subpixel jitter within <= 2.0px tolerance must NOT re-trigger scroll_to
    gv._on_content_scroll(
        SimpleNamespace(pixels=641.5, min_scroll_extent=0.0, out_of_range=False)
    )
    assert len(scroll_calls) == 3
    assert gv._saved_scroll_offset == 640.0

    # Explicit _is_restoring_scroll=True guard must ignore synthetic 0.0 completely
    gv._is_restoring_scroll = True
    try:
        gv._on_content_scroll(
            SimpleNamespace(pixels=0.0, min_scroll_extent=0.0, out_of_range=False)
        )
    finally:
        gv._is_restoring_scroll = False
    assert len(scroll_calls) == 3

    # Close nested dialog first via handle_back() (2 -> 1): outer token remains pinned at 640.0!
    assert gv.handle_back() is True
    assert len(pops) == 1
    assert len(gv._open_dialog_tokens) == 1
    assert gv._pinned_modal_offset() == 640.0
    assert len(scroll_calls) == 3  # no close-restore yet while outer dialog is still open

    # Synthetic 0.0 while outer dialog is still open -> still counter-pinned to 640.0!
    gv._on_content_scroll(
        SimpleNamespace(pixels=0.0, min_scroll_extent=0.0, out_of_range=False)
    )
    assert len(scroll_calls) == 4
    assert gv._saved_scroll_offset == 640.0

    # Close outer dialog via handle_back() (1 -> 0): restores 640.0 cleanly on final close!
    assert gv.handle_back() is True
    assert gv.handle_back() is False
    assert len(pops) == 2
    assert gv._open_dialog_tokens == {}
    assert gv._pinned_modal_offset() is None
    assert len(scroll_calls) == 5
    assert scroll_calls[-1] == {"offset": 640.0, "duration": 0}
    assert gv._saved_scroll_offset == 640.0


def test_grade_overview_preserves_rendered_radar_and_semester_data_on_refresh_error():
    page = SimpleNamespace(width=400, height=800, update=lambda: None)
    gv = GradeOverviewView(page=page)

    # 1. Cold start error (no existing data): shows full _error_state
    gv.show_error("Không thể kết nối Portal.")
    assert gv._error_state.visible is True
    assert gv._radar_card.visible is False
    assert gv._grade_list_view.visible is False

    # 2. Populate rendered academic data and scroll down
    sem1 = _make_sample_semester(75, "HK2 2025-2026")
    sem2 = _make_sample_semester(74, "HK1 2025-2026")
    p1 = calculate_radar_profile(sem1, previous_semester=sem2)
    p2 = calculate_radar_profile(sem2)
    gv.update_academic_data([sem1, sem2], [p1, p2])
    gv._on_content_scroll(
        SimpleNamespace(pixels=420.0, min_scroll_extent=0.0, out_of_range=False)
    )

    # 3. Refresh cycle fails (show_loading -> show_error): rendered radar & semester courses stay intact!
    gv.show_loading()
    assert gv._radar_card.visible is True
    assert gv._grade_list_view.visible is True
    assert gv._loading_row.visible is True

    gv.show_error("Hệ thống UTH đang tạm ngưng bảo trì (01h00 - 04h00).")
    assert gv._loading_row.visible is False
    assert gv._radar_card.visible is True
    assert gv._grade_list_view.visible is True
    assert gv._error_state.visible is False
    assert gv._stale_banner.visible is True
    assert "bảo trì" in gv._stale_text.value
    assert gv._semesters == [sem1, sem2]
    assert gv._profiles == [p1, p2]
    assert len(gv._grade_list_view.courses) == 6
    assert gv._saved_scroll_offset == 420.0

    # 4. Subsequent successful update clears stale banner
    gv.update_academic_data([sem1, sem2], [p1, p2])
    assert gv._stale_banner.visible is False


