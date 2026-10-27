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
