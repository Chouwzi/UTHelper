from datetime import date, datetime

import flet as ft

from core.today_schedule import (
    ClassSession,
    ScheduleLoadStatus,
    TodayScheduleSnapshot,
    TodayScheduleViewState,
)
from gui.components.today_schedule_panel import TodaySchedulePanel


def _state(*, status=ScheduleLoadStatus.READY, sessions=()):
    snapshot = TodayScheduleSnapshot(
        schedule_date=date(2026, 8, 11),
        sessions=tuple(sessions),
        fetched_at=datetime(2026, 8, 11, 6, 0),
    )
    return TodayScheduleViewState(status, snapshot=snapshot)


def _session(*, cancelled=False):
    return ClassSession(
        session_id="1",
        subject="Hệ quản trị cơ sở dữ liệu",
        course_code="CNS_CS1",
        start_at=datetime(2026, 8, 11, 7, 0),
        end_at=datetime(2026, 8, 11, 9, 30),
        room="A.101",
        campus="Cơ sở 1",
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


def test_panel_shows_explicit_loading_when_opened_before_fetch_finishes():
    refresh_calls = []
    panel = TodaySchedulePanel(on_need_data=lambda: refresh_calls.append("fetch"))
    panel.set_state(TodayScheduleViewState(ScheduleLoadStatus.LOADING))

    panel.toggle()

    assert panel.expanded is True
    assert panel.subtitle.value == "Đang lấy lịch học từ Portal..."
    assert panel.loading.visible is True
    assert panel.body.visible is True
    assert refresh_calls == []


def test_panel_renders_subject_time_period_room_campus_and_live_status():
    panel = TodaySchedulePanel(on_need_data=lambda: None)
    panel.set_state(
        _state(sessions=(_session(),)),
        now=datetime(2026, 8, 11, 8, 0),
    )
    panel.toggle()

    text = _texts(panel.item_column.controls[0])

    assert panel.subtitle.value == "1 buổi học"
    assert "Hệ quản trị cơ sở dữ liệu" in text
    assert "07:00 – 09:30 · Tiết 1–3" in text
    assert "Phòng A.101 · Cơ sở 1" in text
    assert "Đang học" in text


def test_panel_empty_day_is_not_reported_as_an_error_or_moodle_activity():
    panel = TodaySchedulePanel(on_need_data=lambda: None)
    panel.set_state(_state(sessions=()))
    panel.toggle()

    assert panel.subtitle.value == "Hôm nay không có lịch"
    assert panel.empty.visible is True
    assert "Hôm nay không có lịch học" in _texts(panel.empty)


def test_panel_refreshes_phase_when_time_crosses_class_boundary():
    panel = TodaySchedulePanel(on_need_data=lambda: None)
    panel.set_state(
        _state(sessions=(_session(),)),
        now=datetime(2026, 8, 11, 6, 30),
    )
    assert "Chưa đến giờ" in _texts(panel.item_column.controls[0])

    assert panel.refresh_time_state(datetime(2026, 8, 11, 7, 0)) is True

    assert "Đang học" in _texts(panel.item_column.controls[0])
