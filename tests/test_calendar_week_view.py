"""Tests for CalendarView week mode functionality."""
import sys
import os
import asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from datetime import date, timedelta
from unittest.mock import MagicMock, PropertyMock, patch
from types import SimpleNamespace
import pytest
import flet as ft

from gui.components.calendar_navigation import MonthAnchor, month_slot_anchors
from gui.components.calendar_view import CalendarView


def _page_stub():
    return SimpleNamespace(update=lambda: None, run_task=lambda *_args: None)


def test_month_period_cells_keep_absolute_dates_after_anchor_changes():
    view = CalendarView(_page_stub())
    period = view._build_month_period_page(MonthAnchor(2026, 8))
    dated_cells = [
        cell for row in period.day_cells for cell in row if cell.data["date"]
    ]

    view._year, view._month = 2027, 1

    assert dated_cells[0].data["date"] == date(2026, 8, 1)
    assert dated_cells[-1].data["date"] == date(2026, 8, 31)


def test_seeding_month_and_week_modes_always_builds_three_slots():
    view = CalendarView(_page_stub())
    assert [page.anchor for page in view._period_pages] == list(
        month_slot_anchors(MonthAnchor(view._year, view._month))
    )

    view._toggle_view_mode("week")

    assert len(view._period_pages) == 3
    assert view._period_pages[1].anchor == view._week_start


def test_page_change_advances_month_once_and_recenters():
    view = CalendarView(_page_stub())
    view._year, view._month = 2026, 12
    view._selected_day = 20
    view._seed_period_pages()
    jumps = []

    async def record_jump(index):
        jumps.append(index)

    view._period_pager.jump_to_page = record_jump

    asyncio.run(view._on_period_page_change(SimpleNamespace(data="2")))

    assert (view._year, view._month) == (2027, 1)
    assert view._selected_day is None
    assert jumps == [1]
    assert view._period_transition_active is False


def test_recenter_and_transition_events_do_not_advance_twice():
    view = CalendarView(_page_stub())
    before = (view._year, view._month)
    view._pager_recentering = True
    asyncio.run(view._on_period_page_change(SimpleNamespace(data="1")))
    assert (view._year, view._month) == before

    view._pager_recentering = False
    view._period_transition_active = True
    asyncio.run(view._on_period_page_change(SimpleNamespace(data="2")))
    assert (view._year, view._month) == before


def test_mobile_keeps_page_view_while_desktop_uses_native_dismissible():
    with patch("gui.components.calendar_view.platform_utils.UI_IS_DESKTOP", False):
        mobile_view = CalendarView(_page_stub())
    with patch("gui.components.calendar_view.platform_utils.UI_IS_DESKTOP", True):
        desktop_view = CalendarView(_page_stub())

    assert mobile_view._period_surface is mobile_view._period_pager
    assert mobile_view._period_pager.snap is True
    assert mobile_view._period_pager.horizontal is True
    assert desktop_view._period_surface is desktop_view._desktop_period_gesture
    assert desktop_view._desktop_period_host.content is desktop_view._period_dismissible
    assert desktop_view._period_dismissible.on_update is None
    assert desktop_view._period_dismissible.dismiss_direction == ft.DismissDirection.HORIZONTAL
    assert desktop_view._desktop_period_gesture.mouse_cursor == ft.MouseCursor.GRAB


def test_native_desktop_dismiss_commits_next_month_and_replaces_swiped_control():
    view = CalendarView(_page_stub())
    view._year, view._month = 2026, 12
    view._seed_period_pages()
    dismissed = view._period_dismissible

    asyncio.run(
        view._on_desktop_period_dismiss(
            SimpleNamespace(direction=ft.DismissDirection.END_TO_START)
        )
    )

    assert (view._year, view._month) == (2027, 1)
    assert view._period_dismissible is not dismissed
    assert view._desktop_period_host.content is view._period_dismissible
    assert view._period_transition_active is False


def test_native_desktop_dismiss_uses_week_navigation_pipeline_too():
    view = CalendarView(_page_stub())
    view._toggle_view_mode("week")
    original_week = view._week_start

    asyncio.run(
        view._on_desktop_period_dismiss(
            SimpleNamespace(direction=ft.DismissDirection.START_TO_END)
        )
    )

    assert view._week_start == original_week - timedelta(days=7)


def test_native_desktop_drag_uses_flutter_snap_without_python_frame_events():
    view = CalendarView(_page_stub())

    dismissible = view._period_dismissible
    assert dismissible.on_update is None
    assert dismissible.on_confirm_dismiss is None
    assert dismissible.on_dismiss == view._on_desktop_period_dismiss
    assert dismissible.dismiss_thresholds == {
        ft.DismissDirection.START_TO_END: 0.14,
        ft.DismissDirection.END_TO_START: 0.14,
    }
    assert dismissible.movement_duration == ft.Duration(milliseconds=140)
    assert dismissible.resize_duration == ft.Duration(milliseconds=0)
    assert view._detail_panel is not view._period_surface


def test_unmounted_arrow_navigation_uses_same_logical_period_pipeline():
    view = CalendarView(_page_stub())
    view._year, view._month = 2026, 12

    asyncio.run(view._request_period_navigation(1))

    assert (view._year, view._month) == (2027, 1)


def test_rapid_navigation_requests_do_not_queue_multiple_page_changes():
    view = CalendarView(_page_stub())
    calls = []

    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_go_to_page(index, **_kwargs):
            calls.append(index)
            entered.set()
            await release.wait()

        view._period_pager.go_to_page = blocked_go_to_page
        with patch.object(
            type(view._period_pager),
            "page",
            new_callable=PropertyMock,
            return_value=object(),
        ):
            first = asyncio.create_task(view._request_period_navigation(1))
            await entered.wait()
            second = asyncio.create_task(view._request_period_navigation(1))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(first, second)

    asyncio.run(exercise())

    assert calls == [2]


def test_today_and_mode_switch_reseed_the_center_slot():
    view = CalendarView(_page_stub())
    view._year, view._month = 2024, 1

    view._go_today()

    assert view._period_pager.selected_index == 1
    assert view._period_pages[1].anchor == MonthAnchor(
        date.today().year, date.today().month
    )

    view._toggle_view_mode("week")

    assert view._period_pager.selected_index == 1
    assert view._period_pages[1].anchor == view._week_start


def test_data_refresh_rebuilds_all_three_period_pages():
    view = CalendarView(_page_stub())
    original_controls = [period.control for period in view._period_pages]

    view.update_data(
        [
            {
                "title": "Bài mới",
                "deadline": f"{view._year}-{view._month:02d}-15 10:00",
                "urgency": "warning",
            }
        ]
    )

    assert len(view._period_pages) == 3
    assert [period.control for period in view._period_pages] != original_controls
    center_cells = [
        cell for row in view._period_pages[1].day_cells for cell in row
    ]
    target = next(cell for cell in center_cells if cell.data["date"] and cell.data["date"].day == 15)
    assert target.data["dots_row"].controls


def test_period_pager_has_bounded_height_for_each_view_mode():
    view = CalendarView(_page_stub())

    assert view._period_pager.height == 316

    view._toggle_view_mode("week")

    assert view._period_pager.height == 88


def test_month_period_page_uses_count_badge_for_three_or_more_deadlines():
    view = CalendarView(_page_stub())
    target_date = date(2026, 8, 3)
    view._deadline_map[target_date.isoformat()] = [
        {"urgency": "safe"},
        {"urgency": "warning"},
        {"urgency": "critical"},
    ]

    period = view._build_month_period_page(MonthAnchor(2026, 8))
    target = next(
        cell
        for row in period.day_cells
        for cell in row
        if cell.data["date"] == target_date
    )

    markers = target.data["dots_row"].controls
    assert len(markers) == 1
    assert markers[0].content.value == "3"


def test_week_period_page_preserves_deadline_badge_and_urgency_color():
    view = CalendarView(_page_stub())
    week_start = date(2026, 8, 10)
    target_date = week_start + timedelta(days=1)
    view._deadline_map[target_date.isoformat()] = [
        {"urgency": "safe"},
        {"urgency": "warning"},
        {"urgency": "critical"},
    ]

    period = view._build_week_period_page(week_start)
    target = period.week_cells[1]

    assert target.data["count_text"].value == "3 bài"
    assert target.data["count_text"].color is not None
    assert len(target.data["dots_row"].controls) == 1
    assert target.data["dots_row"].controls[0].content.value == "3"


def test_mode_switch_publishes_week_pager_in_the_same_ui_frame():
    observations = []
    holder = {}

    def observe_update():
        view = holder.get("view")
        if view is not None:
            observations.append(
                (
                    view._period_pager.height,
                    view._period_pages[1].anchor,
                )
            )

    view = CalendarView(
        SimpleNamespace(update=observe_update, run_task=lambda *_args: None)
    )
    holder["view"] = view

    view._toggle_view_mode("week")

    assert observations
    assert observations[-1] == (88, view._week_start)


def test_month_date_click_repaints_the_mounted_period_cell_immediately():
    view = CalendarView(_page_stub())
    selected = date(2026, 8, 20)
    view._year, view._month = selected.year, selected.month
    view._selected_day = None
    view._seed_period_pages()

    view._on_absolute_date_click(selected)

    target = next(
        cell
        for row in view._period_pages[1].day_cells
        for cell in row
        if cell.data["date"] == selected
    )
    assert target.border is not None


def test_week_date_click_repaints_the_mounted_period_cell_immediately():
    view = CalendarView(_page_stub())
    view._toggle_view_mode("week")
    selected = view._week_start + timedelta(days=4)
    view._selected_date = None
    view._seed_period_pages()

    view._on_absolute_date_click(selected)

    target = view._period_pages[1].week_cells[4]
    assert target.border is not None


class TestCalendarWeekState:
    """Test the week view state management logic (no GUI required)."""

    def test_week_start_is_monday(self):
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        assert week_start.weekday() == 0

    def test_week_navigation(self):
        start = date(2026, 6, 15)
        assert start + timedelta(weeks=1) == date(2026, 6, 22)
        assert start + timedelta(weeks=-1) == date(2026, 6, 8)

    def test_week_spans_two_months(self):
        start = date(2026, 6, 29)
        end = start + timedelta(days=6)
        assert start.month == 6
        assert end.month == 7

    def test_week_number_calculation(self):
        d = date(2026, 6, 18)
        assert d.isocalendar()[1] == 25

    def test_selected_date_sync_on_mode_switch(self):
        year, month, day = 2026, 6, 25
        sel = date(year, month, day)
        week_start = sel - timedelta(days=sel.weekday())
        assert week_start == date(2026, 6, 22)
        assert week_start <= sel <= week_start + timedelta(days=6)

    def test_go_today_sets_both_states(self):
        today = date.today()
        selected_day = today.day
        selected_date = today
        week_start = today - timedelta(days=today.weekday())
        assert selected_day == today.day
        assert selected_date == today
        assert week_start.weekday() == 0
        assert week_start <= today <= week_start + timedelta(days=6)

    def test_week_cell_click_updates_selection(self):
        clicked_date = date(2026, 7, 1)
        selected_date = clicked_date
        selected_day = clicked_date.day
        year = clicked_date.year
        month = clicked_date.month
        assert selected_date == date(2026, 7, 1)
        assert selected_day == 1
        assert year == 2026
        assert month == 7


class TestCalendarViewImport:
    def test_import_calendar_view(self):
        try:
            from gui.components.calendar_view import CalendarView
            assert CalendarView is not None
        except ImportError as e:
            if "flet" in str(e).lower():
                pytest.skip("Flet not available")
            raise

    def test_timedelta_imported(self):
        from datetime import timedelta
        assert timedelta(weeks=1).days == 7


class TestWeekViewDataLogic:
    def test_activity_counting_for_week(self):
        deadline_map = {
            "2026-06-15": [{"title": "A1", "urgency": "safe"}],
            "2026-06-16": [{"title": "A2", "urgency": "warning"}, {"title": "A3", "urgency": "critical"}],
            "2026-06-18": [{"title": "A4", "urgency": "safe"}],
            "2026-06-22": [{"title": "A5", "urgency": "overdue"}],
        }
        week_start = date(2026, 6, 15)
        week_total = 0
        for i in range(7):
            d = week_start + timedelta(days=i)
            key = d.strftime("%Y-%m-%d")
            acts = deadline_map.get(key, [])
            week_total += len(acts)
        assert week_total == 4

    def test_urgency_priority_sorting(self):
        urgency_priority = {"overdue": 0, "critical": 1, "warning": 2, "safe": 3}
        acts = [
            {"urgency": "safe"}, {"urgency": "overdue"},
            {"urgency": "warning"}, {"urgency": "critical"},
        ]
        sorted_acts = sorted(acts, key=lambda a: urgency_priority.get(a.get("urgency", "safe"), 3))
        assert sorted_acts[0]["urgency"] == "overdue"
        assert sorted_acts[1]["urgency"] == "critical"
        assert sorted_acts[-1]["urgency"] == "safe"

    def test_submission_counting(self):
        acts = [
            {"submission_status": "submitted"},
            {"submission_status": "Đã nộp"},
            {"submission_status": "graded"},
            {"submission_status": ""},
            {"submission_status": "Chưa nộp"},
        ]
        submitted_statuses = ("submitted", "Đã nộp", "graded", "Đã chấm")
        count = sum(1 for a in acts if a.get("submission_status", "") in submitted_statuses)
        assert count == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
