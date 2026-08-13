from datetime import date, datetime

import flet as ft

from core.today_schedule import (
    ClassSession,
    ScheduleLoadStatus,
    TodayScheduleSnapshot,
    TodayScheduleViewState,
)
from gui.components.today_schedule_panel import TodaySchedulePanel
from gui.core.theme import C, apply_theme


class _Page:
    def __init__(self, *, height=700):
        self.height = height
        self.dialogs = []
        self.pop_count = 0
        self.on_pop = None

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        self.pop_count += 1
        if self.on_pop is not None:
            self.on_pop()


def _state(*, status=ScheduleLoadStatus.READY, sessions=()):
    snapshot = TodayScheduleSnapshot(
        schedule_date=date(2026, 8, 11),
        sessions=tuple(sessions),
        fetched_at=datetime(2026, 8, 11, 6, 0),
    )
    return TodayScheduleViewState(status, snapshot=snapshot)


def _session(
    *,
    session_id="1",
    subject="Hệ quản trị cơ sở dữ liệu",
    start=datetime(2026, 8, 11, 7, 0),
    end=datetime(2026, 8, 11, 9, 30),
    room="A.101",
    campus="Cơ sở 1",
    cancelled=False,
):
    return ClassSession(
        session_id=session_id,
        subject=subject,
        course_code="CNS_CS1",
        start_at=start,
        end_at=end,
        room=room,
        campus=campus,
        period_start=1,
        period_end=3,
        cancelled=cancelled,
        note="",
    )


def _texts(control):
    values = []

    def walk(item):
        if isinstance(item, ft.Text):
            values.append(item.value)
        content = getattr(item, "content", None)
        if content is not None:
            walk(content)
        for child in getattr(item, "controls", None) or []:
            walk(child)

    walk(control)
    return values


def _controls(control, control_type):
    values = []

    def walk(item):
        if isinstance(item, control_type):
            values.append(item)
        content = getattr(item, "content", None)
        if content is not None:
            walk(content)
        for child in getattr(item, "controls", None) or []:
            walk(child)

    walk(control)
    return values


def test_spotlight_is_visible_without_expanding_and_separates_location_lines():
    current = _session()
    later = _session(
        session_id="2",
        subject="An ninh mạng",
        start=datetime(2026, 8, 11, 13, 0),
        end=datetime(2026, 8, 11, 15, 30),
    )
    panel = TodaySchedulePanel(on_need_data=lambda: None)

    panel.set_state(
        _state(sessions=(later, current)),
        now=datetime(2026, 8, 11, 8, 0),
    )

    text = _texts(panel.spotlight_content)
    assert "Hệ quản trị cơ sở dữ liệu" in text
    assert "Đang học" in text
    assert "07:00 – 09:30 · Tiết 1–3" in text
    assert "Phòng A.101" in text
    assert "Cơ sở 1" in text
    assert "Phòng A.101 · Cơ sở 1" not in text
    assert "Xem lịch hôm nay · 2 buổi" in text


def test_spotlight_reports_completion_when_every_class_has_finished():
    panel = TodaySchedulePanel(on_need_data=lambda: None)
    panel.set_state(
        _state(sessions=(_session(),)),
        now=datetime(2026, 8, 11, 10, 0),
    )

    text = _texts(panel.spotlight_content)
    assert "Đã hoàn thành lịch học hôm nay" in text
    assert "Hệ quản trị cơ sở dữ liệu" not in text
    assert "Xem lịch hôm nay · 1 buổi" in text


def test_mobile_schedule_surface_lists_each_location_on_its_own_line():
    page = _Page(height=640)
    panel = TodaySchedulePanel(page=page, mobile=True, on_need_data=lambda: None)
    panel.set_state(
        _state(sessions=(_session(),)),
        now=datetime(2026, 8, 11, 8, 0),
    )

    panel.open_schedule()

    assert isinstance(page.dialogs[-1], ft.BottomSheet)
    assert panel.surface_open is True
    assert panel._surface_mode == "list"
    text = _texts(page.dialogs[-1])
    assert "07:00 – 09:30 · Tiết 1–3" in text
    assert "Phòng A.101" in text
    assert "Cơ sở 1" in text
    assert "Phòng A.101 · Cơ sở 1" not in text


def test_desktop_schedule_surface_uses_a_dialog():
    page = _Page()
    panel = TodaySchedulePanel(page=page, mobile=False, on_need_data=lambda: None)
    panel.set_state(_state(sessions=(_session(),)))

    panel.open_schedule()

    assert isinstance(page.dialogs[-1], ft.AlertDialog)


def test_desktop_dialog_clips_content_to_rounded_top_corners():
    page = _Page()
    panel = TodaySchedulePanel(page=page, mobile=False, on_need_data=lambda: None)
    panel.set_state(_state(sessions=(_session(),)))

    panel.open_schedule()

    dialog = page.dialogs[-1]
    assert dialog.clip_behavior is ft.ClipBehavior.ANTI_ALIAS
    assert panel._surface_content.border_radius == 18
    assert panel._surface_content.clip_behavior is ft.ClipBehavior.ANTI_ALIAS


def test_list_detail_back_returns_to_list_without_stacking_a_dialog():
    page = _Page()
    panel = TodaySchedulePanel(page=page, mobile=True, on_need_data=lambda: None)
    panel.set_state(
        _state(sessions=(_session(),)),
        now=datetime(2026, 8, 11, 8, 0),
    )
    panel.open_schedule()
    original_surface = page.dialogs[-1]

    panel._list_rows[0].on_click(None)

    assert panel._surface_mode == "detail"
    assert len(page.dialogs) == 1
    assert page.dialogs[-1] is original_surface
    assert "Chi tiết lớp học" in _texts(original_surface)
    assert panel.handle_back() is True
    assert panel._surface_mode == "list"
    assert page.pop_count == 0


def test_direct_spotlight_detail_back_closes_surface_to_dashboard():
    page = _Page()
    session = _session()
    panel = TodaySchedulePanel(page=page, mobile=False, on_need_data=lambda: None)
    panel.set_state(
        _state(sessions=(session,)),
        now=datetime(2026, 8, 11, 8, 0),
    )

    panel.spotlight_tap_target.on_click(None)

    assert panel._surface_mode == "detail"
    assert "Chi tiết lớp học" in _texts(page.dialogs[-1])
    assert panel.handle_back() is True
    assert panel.surface_open is False
    assert page.pop_count == 1


def test_close_marks_surface_closed_before_flet_pop_callback_runs():
    page = _Page()
    session = _session()
    panel = TodaySchedulePanel(page=page, mobile=False, on_need_data=lambda: None)
    panel.set_state(
        _state(sessions=(session,)),
        now=datetime(2026, 8, 11, 8, 0),
    )
    panel.open_schedule()
    panel._list_rows[0].on_click(None)
    observed = []
    page.on_pop = lambda: observed.append(
        (panel.surface_open, panel._surface_mode, panel.handle_back())
    )

    assert panel.close_surface() is True

    assert observed == [(False, None, False)]
    assert panel.surface_open is False
    assert page.pop_count == 1


def test_detail_uses_consistent_type_scale_and_one_full_width_information_card():
    page = _Page()
    session = _session()
    panel = TodaySchedulePanel(page=page, mobile=False, on_need_data=lambda: None)
    panel.set_state(
        _state(sessions=(session,)),
        now=datetime(2026, 8, 11, 8, 0),
    )

    panel.open_detail(session, from_list=False)

    surface = panel._surface_content
    texts = _controls(surface, ft.Text)
    text_by_value = {text.value: text for text in texts}
    assert text_by_value["Chi tiết lớp học"].size == 17
    assert text_by_value["Hệ quản trị cơ sở dữ liệu"].size == 16
    assert text_by_value["CNS_CS1"].size == 11
    assert text_by_value["THỜI GIAN"].size == 10
    assert text_by_value["PHÒNG HỌC"].size == 10
    assert text_by_value["CƠ SỞ"].size == 10
    assert "TRẠNG THÁI" not in text_by_value
    info_cards = [
        control
        for control in _controls(surface, ft.Container)
        if control.data == "schedule-detail-info"
    ]
    assert len(info_cards) == 1
    assert info_cards[0].width == float("inf")


def test_phase_boundary_advances_spotlight_and_refreshes_open_detail():
    page = _Page()
    current = _session(
        session_id="current",
        subject="Tin học cơ bản",
        start=datetime(2026, 8, 11, 7, 0),
        end=datetime(2026, 8, 11, 9, 30),
    )
    upcoming = _session(
        session_id="next",
        subject="An ninh mạng",
        start=datetime(2026, 8, 11, 10, 0),
        end=datetime(2026, 8, 11, 12, 0),
    )
    panel = TodaySchedulePanel(page=page, mobile=True, on_need_data=lambda: None)
    panel.set_state(
        _state(sessions=(current, upcoming)),
        now=datetime(2026, 8, 11, 8, 0),
    )
    panel.open_detail(current, from_list=False)

    assert panel.refresh_time_state(datetime(2026, 8, 11, 9, 30)) is True

    assert "An ninh mạng" in _texts(panel.spotlight_content)
    detail_text = _texts(page.dialogs[-1])
    assert "Tin học cơ bản" in detail_text
    assert "Đã kết thúc" in detail_text


def test_panel_shows_explicit_loading_before_fetch_finishes():
    refresh_calls = []
    panel = TodaySchedulePanel(on_need_data=lambda: refresh_calls.append("fetch"))
    panel.set_state(TodayScheduleViewState(ScheduleLoadStatus.LOADING))

    assert "Đang lấy lịch học từ Portal..." in _texts(panel.spotlight_content)
    assert refresh_calls == []


def test_panel_empty_day_is_not_reported_as_an_error_or_moodle_activity():
    panel = TodaySchedulePanel(on_need_data=lambda: None)
    panel.set_state(_state(sessions=()))

    assert "Hôm nay không có lịch học" in _texts(panel.spotlight_content)


def test_panel_refreshes_phase_when_time_crosses_class_boundary():
    panel = TodaySchedulePanel(on_need_data=lambda: None)
    panel.set_state(
        _state(sessions=(_session(),)),
        now=datetime(2026, 8, 11, 6, 30),
    )
    assert "Chưa đến giờ" in _texts(panel.spotlight_content)

    assert panel.refresh_time_state(datetime(2026, 8, 11, 7, 0)) is True

    assert "Đang học" in _texts(panel.spotlight_content)


def test_panel_refresh_theme_updates_static_and_dynamic_controls():
    panel = TodaySchedulePanel(on_need_data=lambda: None)
    panel.set_state(
        _state(sessions=(_session(),)),
        now=datetime(2026, 8, 11, 8, 0),
    )
    original_theme = "midnight_blue"
    try:
        apply_theme("material_graphite")
        panel.refresh_theme()

        assert panel.control.bgcolor == C.SURFACE
        assert panel.control.border.top.color == C.BORDER
        texts = _controls(panel.spotlight_content, ft.Text)
        assert any(text.color == C.TEXT_PRIMARY for text in texts)
        assert any(text.color == C.TEXT_SECONDARY for text in texts)
        assert any(text.color == C.SAFE for text in texts)
    finally:
        apply_theme(original_theme)


def test_refresh_theme_updates_an_open_schedule_detail_surface():
    page = _Page()
    session = _session()
    panel = TodaySchedulePanel(page=page, mobile=True, on_need_data=lambda: None)
    panel.set_state(
        _state(sessions=(session,)),
        now=datetime(2026, 8, 11, 8, 0),
    )
    panel.open_detail(session, from_list=False)
    original_theme = "midnight_blue"
    try:
        apply_theme("material_graphite")
        panel.refresh_theme()

        assert panel._surface.bgcolor == C.BG
        assert panel._surface_content.bgcolor == C.BG
        detail_containers = _controls(panel._surface_content, ft.Container)
        assert any(control.bgcolor == C.SURFACE for control in detail_containers)
        assert all(
            text.color in {C.TEXT_PRIMARY, C.TEXT_SECONDARY, C.SAFE}
            for text in _controls(panel._surface_content, ft.Text)
        )
    finally:
        apply_theme(original_theme)
