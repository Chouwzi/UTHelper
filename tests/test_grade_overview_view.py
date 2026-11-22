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
