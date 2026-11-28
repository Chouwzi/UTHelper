"""Tests for CalendarView week mode functionality."""
import sys
import os
import asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from types import SimpleNamespace
import pytest
import flet as ft

from gui.components.calendar_navigation import MonthAnchor, month_slot_anchors
from gui.components.calendar_view import CalendarView


def _page_stub():
    return SimpleNamespace(update=lambda *_controls: None, run_task=lambda *_args: None)


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
    assert view._view_mode == "week"
    assert len(view._period_pages) == 3
    assert view._period_pages[1].anchor == view._week_start

    view._toggle_view_mode("month")

    assert [page.anchor for page in view._period_pages] == list(
        month_slot_anchors(MonthAnchor(view._year, view._month))
    )


@pytest.mark.parametrize("target_mode", ["week", "month"])
def test_schedule_tab_is_unhighlighted_during_calendar_mode_page_update(target_mode):
    page = _page_stub()
    view = CalendarView(page)
    view._toggle_view_mode("schedule")

    update_calls = []

    def assert_schedule_tab_is_clear_during_render(*_controls):
        assert view._mode_schedule_btn.bgcolor == "transparent"
        update_calls.append(target_mode)

    page.update = assert_schedule_tab_is_clear_during_render
    view._toggle_view_mode(target_mode)

    assert update_calls


def test_page_change_advances_month_once_and_recenters():
    view = CalendarView(_page_stub())
    view._toggle_view_mode("month")
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
    view._toggle_view_mode("month")
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
    assert desktop_view._desktop_period_host.content is desktop_view._desktop_period_stage
    assert isinstance(desktop_view._desktop_period_slot, ft.Stack)
    assert desktop_view._desktop_period_stage.controls[-1] is desktop_view._desktop_period_slot
    assert desktop_view._desktop_period_slot.controls == [
        desktop_view._period_dismissible
    ]
    assert desktop_view._period_dismissible.on_update is None
    assert desktop_view._period_dismissible.dismiss_direction == ft.DismissDirection.HORIZONTAL
    assert desktop_view._desktop_period_gesture.mouse_cursor == ft.MouseCursor.GRAB


def test_native_desktop_dismiss_commits_next_month_and_replaces_swiped_control():
    view = CalendarView(_page_stub())
    view._toggle_view_mode("month")
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
    assert view._desktop_period_host.content is view._desktop_period_stage
    assert view._desktop_period_stage.controls[-1] is view._desktop_period_slot
    assert view._desktop_period_slot.controls == [view._period_dismissible]
    assert view._period_transition_active is False


def test_confirm_dismiss_updates_target_underlay_and_header_before_removal():
    page = SimpleNamespace(update=MagicMock(), run_task=lambda *_args: None)
    view = CalendarView(page)
    view._toggle_view_mode("month")
    page.update.reset_mock()
    view._year, view._month = 2026, 8
    view._seed_period_pages()
    old_underlay = view._desktop_period_underlay.content
    source = view._period_dismissible
    event = SimpleNamespace(
        direction=ft.DismissDirection.END_TO_START,
        control=source,
    )

    confirm_dismiss = AsyncMock()
    with patch.object(ft.Dismissible, "confirm_dismiss", confirm_dismiss):
        asyncio.run(view._on_desktop_period_confirm(event))

    assert (view._year, view._month) == (2026, 9)
    assert view._month_label.value == "Tháng 9 2026"
    assert view._desktop_period_underlay.content is not old_underlay
    assert view._pending_desktop_delta == 1
    assert view._period_transition_active is True
    assert page.update.call_args.args == (
        view._header,
        view._desktop_period_underlay,
        view._detail_panel,
    )
    confirm_dismiss.assert_awaited_once_with(True)

    asyncio.run(view._on_desktop_period_dismiss(event))

    assert (view._year, view._month) == (2026, 9)
    assert view._pending_desktop_delta is None
    assert view._pending_desktop_control is None
    assert view._period_transition_active is False


def test_confirm_dismiss_resolves_each_replacement_before_next_drag():
    page = SimpleNamespace(update=MagicMock(), run_task=lambda *_args: None)
    view = CalendarView(page)
    view._toggle_view_mode("month")
    view._year, view._month = 2026, 8
    view._seed_period_pages()

    async def swipe_next_twice():
        for expected_month in (9, 10):
            source = view._period_dismissible
            event = SimpleNamespace(
                direction=ft.DismissDirection.END_TO_START,
                control=source,
            )
            confirm_dismiss = AsyncMock()
            with patch.object(
                ft.Dismissible, "confirm_dismiss", confirm_dismiss
            ):
                await view._on_desktop_period_confirm(event)
            confirm_dismiss.assert_awaited_once_with(True)
            await view._on_desktop_period_dismiss(event)
            assert view._month == expected_month
            assert view._pending_desktop_delta is None
            assert view._pending_desktop_control is None
            assert view._period_transition_active is False

    asyncio.run(swipe_next_twice())


def test_rejected_confirm_explicitly_releases_flet_drag_lock():
    view = CalendarView(_page_stub())
    view._period_transition_active = True
    source = SimpleNamespace(confirm_dismiss=AsyncMock())
    event = SimpleNamespace(
        direction=ft.DismissDirection.END_TO_START,
        control=source,
    )

    asyncio.run(view._on_desktop_period_confirm(event))

    source.confirm_dismiss.assert_awaited_once_with(False)


def test_missing_dismiss_event_is_rearmed_by_bounded_recovery():
    page = SimpleNamespace(update=MagicMock(), run_task=lambda *_args: None)
    view = CalendarView(page)
    view._toggle_view_mode("month")
    view._year, view._month = 2026, 8
    view._seed_period_pages()
    source = view._period_dismissible
    event = SimpleNamespace(
        direction=ft.DismissDirection.END_TO_START,
        control=source,
    )

    with patch.object(ft.Dismissible, "confirm_dismiss", AsyncMock()):
        asyncio.run(view._on_desktop_period_confirm(event))
    with patch(
        "gui.components.calendar_view.asyncio.sleep", new=AsyncMock()
    ):
        asyncio.run(view._recover_desktop_period_dismiss(source, 1))

    assert (view._year, view._month) == (2026, 9)
    assert view._period_dismissible is not source
    assert view._pending_desktop_delta is None
    assert view._pending_desktop_control is None
    assert view._period_transition_active is False


def test_mode_roundtrip_discards_late_dismiss_without_reverting_month():
    view = CalendarView(_page_stub())
    today = date.today()
    view._toggle_view_mode("month")
    view._year, view._month = 2026, 8
    view._seed_period_pages()
    stale_source = view._period_dismissible
    view._pending_desktop_delta = 1
    view._pending_desktop_control = stale_source
    view._period_transition_active = True

    view._toggle_view_mode("week")
    view._toggle_view_mode("month")
    current_source = view._period_dismissible

    assert current_source is not stale_source
    assert view._pending_desktop_delta is None
    assert view._pending_desktop_control is None
    asyncio.run(
        view._on_desktop_period_dismiss(
            SimpleNamespace(
                direction=ft.DismissDirection.END_TO_START,
                control=stale_source,
            )
        )
    )
    assert (view._year, view._month) == (today.year, today.month)
    assert view._period_dismissible is current_source

    event = SimpleNamespace(
        direction=ft.DismissDirection.END_TO_START,
        control=current_source,
    )
    confirm_dismiss = AsyncMock()
    with patch.object(ft.Dismissible, "confirm_dismiss", confirm_dismiss):
        asyncio.run(view._on_desktop_period_confirm(event))
    confirm_dismiss.assert_awaited_once_with(True)
    asyncio.run(view._on_desktop_period_dismiss(event))
    expected_next_month = (
        (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    )
    assert (view._year, view._month) == expected_next_month


def test_current_swipe_heals_orphaned_pending_control():
    view = CalendarView(_page_stub())
    view._toggle_view_mode("month")
    view._year, view._month = 2026, 8
    view._seed_period_pages()
    current_source = view._period_dismissible
    view._pending_desktop_delta = -1
    view._pending_desktop_control = SimpleNamespace()
    view._period_transition_active = True
    event = SimpleNamespace(
        direction=ft.DismissDirection.END_TO_START,
        control=current_source,
    )
    confirm_dismiss = AsyncMock()

    with patch.object(ft.Dismissible, "confirm_dismiss", confirm_dismiss):
        asyncio.run(view._on_desktop_period_confirm(event))

    confirm_dismiss.assert_awaited_once_with(True)
    assert (view._year, view._month) == (2026, 9)
    assert view._pending_desktop_control is current_source


def test_native_desktop_dismiss_reuses_two_adjacent_period_pages():
    view = CalendarView(_page_stub())
    view._toggle_view_mode("month")
    view._year, view._month = 2026, 8
    view._seed_period_pages()
    old_pages = list(view._period_pages)

    asyncio.run(
        view._on_desktop_period_dismiss(
            SimpleNamespace(direction=ft.DismissDirection.END_TO_START)
        )
    )

    assert view._period_pages[0] is old_pages[1]
    assert view._period_pages[1] is old_pages[2]
    assert view._period_pages[2].anchor == MonthAnchor(2026, 10)


def test_native_desktop_reverse_dismiss_reuses_two_adjacent_period_pages():
    view = CalendarView(_page_stub())
    view._toggle_view_mode("month")
    view._year, view._month = 2026, 8
    view._seed_period_pages()
    old_pages = list(view._period_pages)

    asyncio.run(
        view._on_desktop_period_dismiss(
            SimpleNamespace(direction=ft.DismissDirection.START_TO_END)
        )
    )

    assert view._period_pages[1] is old_pages[0]
    assert view._period_pages[2] is old_pages[1]
    assert view._period_pages[0].anchor == MonthAnchor(2026, 6)


def test_release_frame_preview_keeps_activity_dot_and_count_badge():
    view = CalendarView(_page_stub())
    view._deadline_map = {
        "2026-09-03": [{"urgency": "safe"}],
        "2026-09-19": [{"urgency": "safe"}] * 3,
    }

    preview = view._build_month_period_preview(MonthAnchor(2026, 9))
    dated_cells = {}
    for row in preview.control.controls[1:]:
        for cell in row.controls:
            content = cell.content
            day_text = (
                content.controls[0]
                if isinstance(content, ft.Column)
                else content
            )
            if day_text.value:
                dated_cells[int(day_text.value)] = cell

    single_marker_row = dated_cells[3].content.controls[-1]
    count_marker_row = dated_cells[19].content.controls[-1]

    assert len(single_marker_row.controls) == 1
    assert single_marker_row.controls[0].width == 6
    assert count_marker_row.controls[0].content.value == "3"


def test_native_desktop_drag_moves_adjacent_pages_as_one_clipped_strip():
    view = CalendarView(_page_stub())
    view._toggle_view_mode("month")

    strip = view._period_dismissible.content
    assert isinstance(strip, ft.Stack)
    assert strip.clip_behavior == ft.ClipBehavior.NONE
    assert strip.width == float("inf")
    assert [page.offset.x for page in strip.controls] == [-1, 0, 1]
    assert [page.left for page in strip.controls] == [0, 0, 0]
    assert [page.right for page in strip.controls] == [0, 0, 0]
    assert view._period_dismissible.background is not None
    assert view._period_dismissible.secondary_background is not None
    assert view._period_dismissible.background is not strip.controls[0]
    assert view._period_dismissible.secondary_background is not strip.controls[2]
    assert len(view._period_background_pages) == 2
    assert all(page.day_cells for page in view._period_background_pages)
    assert all(
        cell.on_click is None and cell.animate is None
        for page in view._period_background_pages
        for row in page.day_cells
        for cell in row
    )
    assert view._desktop_period_stage.controls[0].content is view._grid
    assert view._desktop_period_stage.controls[1] is view._desktop_period_slot
    assert view._desktop_period_slot.controls == [view._period_dismissible]
    assert view._period_dismissible.left == 0
    assert view._period_dismissible.right == 0
    assert view._period_dismissible.top == 0
    assert view._period_dismissible.bottom == 0
    assert view._desktop_period_host.clip_behavior == ft.ClipBehavior.HARD_EDGE


def test_native_desktop_dismiss_batches_only_changed_subtrees_and_can_repeat():
    page = SimpleNamespace(update=MagicMock(), run_task=lambda *_args: None)
    view = CalendarView(page)
    view._toggle_view_mode("month")
    page.update.reset_mock()
    view._year, view._month = 2026, 8
    view._seed_period_pages()
    first = view._period_dismissible

    async def exercise():
        with patch.object(view, "update") as broad_update:
            event = SimpleNamespace(direction=ft.DismissDirection.END_TO_START)
            await view._on_desktop_period_dismiss(event)
            second = view._period_dismissible
            await view._on_desktop_period_dismiss(event)
            return broad_update.call_count, second

    broad_update_count, second = asyncio.run(exercise())

    assert (view._year, view._month) == (2026, 10)
    assert first is not second
    assert second is not view._period_dismissible
    assert broad_update_count == 0
    updates = [entry.args for entry in page.update.call_args_list]
    immediate_update = (
        view._header,
        view._desktop_period_slot,
        view._detail_panel,
    )
    assert updates[0] == immediate_update
    assert updates[1] == immediate_update


def test_native_desktop_strip_uses_fractional_offsets_without_size_events():
    view = CalendarView(_page_stub())

    strip = view._period_dismissible.content
    assert view._desktop_period_host.width == float("inf")
    assert view._desktop_period_host.on_size_change is None
    assert [page.offset.x for page in strip.controls] == [-1, 0, 1]


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
    assert dismissible.on_confirm_dismiss == view._on_desktop_period_confirm
    assert dismissible.on_dismiss == view._on_desktop_period_dismiss
    assert dismissible.dismiss_thresholds == {
        ft.DismissDirection.START_TO_END: 0.14,
        ft.DismissDirection.END_TO_START: 0.14,
    }
    assert dismissible.resize_duration == ft.Duration(milliseconds=1)
    assert dismissible.movement_duration == ft.Duration(milliseconds=30)
    assert view._detail_panel is not view._period_surface


def test_unmounted_arrow_navigation_uses_same_logical_period_pipeline():
    view = CalendarView(_page_stub())
    view._toggle_view_mode("month")
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
    view._week_start = date(2024, 1, 1)

    view._go_today()

    assert view._period_pager.selected_index == 1
    assert view._period_pages[1].anchor == view._week_start

    view._toggle_view_mode("month")

    assert view._period_pager.selected_index == 1
    assert view._period_pages[1].anchor == MonthAnchor(
        date.today().year, date.today().month
    )


def test_data_refresh_rebuilds_all_three_period_pages():
    view = CalendarView(_page_stub())
    view._toggle_view_mode("month")
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

    assert view._period_pager.height == 88

    view._toggle_view_mode("month")

    assert view._period_pager.height == 316


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
    view._toggle_view_mode("month")
    holder["view"] = view

    view._toggle_view_mode("week")

    assert observations
    assert observations[-1] == (88, view._week_start)


def test_month_date_click_repaints_the_mounted_period_cell_immediately():
    view = CalendarView(_page_stub())
    view._toggle_view_mode("month")
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


def test_calendar_view_skips_week_render_in_schedule_mode_and_uses_responsive_cells():
    from gui.components.calendar_view import CalendarView

    page = SimpleNamespace(
        width=360,
        height=740,
        dialogs=[],
        show_dialog=lambda _d: None,
        update=lambda: None,
    )
    opened = []
    cal = CalendarView(
        page,
        on_open_url=lambda u, t: opened.append((u, t)),
        on_open_external=lambda u: opened.append(("ext", u)),
    )
    render_calls = []
    cal._render_week = lambda: render_calls.append("render")

    cal._toggle_view_mode("schedule")
    render_calls.clear()
    cal.update_data([])
    cal.update_theme()

    assert render_calls == []
    assert cal._mode_schedule_btn.height == 44
    assert cal._mode_month_btn.height == 44
    assert cal._mode_week_btn.height == 44
    cell = cal._make_week_cell(0)
    assert cell.expand is True
    assert cal._schedule_view._on_open_url is not None
    assert cal._schedule_view._on_open_external is not None


def _class_session(
    day: date,
    *,
    subject: str = "Cấu trúc dữ liệu và giải thuật",
    start_hm: tuple[int, int] = (7, 30),
    end_hm: tuple[int, int] = (10, 0),
    room: str = "F502 - CS1 (P.Thạnh Mỹ Tây, TP.HCM)",
    campus: str = "Cơ sở 1",
    cancelled: bool = False,
):
    from datetime import datetime, time
    from core.today_schedule import ClassSession

    return ClassSession(
        session_id=f"cls-{day.isoformat()}-{start_hm[0]:02d}{start_hm[1]:02d}-{subject}",
        subject=subject,
        course_code="010112345601",
        start_at=datetime.combine(day, time(start_hm[0], start_hm[1])),
        end_at=datetime.combine(day, time(end_hm[0], end_hm[1])),
        room=room,
        campus=campus,
        period_start=1,
        period_end=3,
        cancelled=cancelled,
        note="Nghỉ bù tuần sau" if cancelled else "",
    )


def _walk_controls(control):
    seen: set[int] = set()

    def visit(item):
        if item is None or id(item) in seen:
            return
        if not isinstance(item, ft.BaseControl):
            return
        seen.add(id(item))
        yield item
        for attr in ("content", "title", "subtitle", "leading", "trailing"):
            yield from visit(getattr(item, attr, None))
        for attr in ("controls", "actions"):
            for child in getattr(item, attr, None) or ():
                yield from visit(child)

    yield from visit(control)


def test_toggle_view_mode_clears_schedule_button_highlight_immediately_when_switching_to_week_or_month():
    from gui.core.theme import C

    snapshots: list[tuple[str, str, str]] = []
    holder: dict[str, CalendarView] = {}

    def _observe_update(*_controls):
        cal = holder.get("cal")
        if cal is not None:
            snapshots.append(
                (
                    cal._mode_month_btn.bgcolor,
                    cal._mode_week_btn.bgcolor,
                    cal._mode_schedule_btn.bgcolor,
                )
            )

    page = SimpleNamespace(
        width=1120,
        height=760,
        update=_observe_update,
        run_task=lambda *_a, **_k: None,
    )
    cal = CalendarView(page)
    holder["cal"] = cal
    active_bg = ft.Colors.with_opacity(0.19, C.ACCENT)

    cal._toggle_view_mode("schedule")
    assert cal._mode_schedule_btn.bgcolor == active_bg
    assert cal._mode_week_btn.bgcolor == "transparent"
    assert cal._mode_month_btn.bgcolor == "transparent"

    snapshots.clear()
    cal._toggle_view_mode("week")
    assert snapshots, "Expected page.update() when leaving schedule mode to week"
    first_month_bg, first_week_bg, first_sched_bg = snapshots[0]
    assert first_sched_bg == "transparent"
    assert first_week_bg == active_bg
    assert first_month_bg == "transparent"

    cal._toggle_view_mode("schedule")
    snapshots.clear()
    cal._toggle_view_mode("month")
    assert snapshots
    m_bg, w_bg, s_bg = snapshots[0]
    assert s_bg == "transparent"
    assert m_bg == active_bg
    assert w_bg == "transparent"


def test_adaptive_mode_bar_hides_schedule_tab_on_mobile_and_falls_back_to_week_on_compact_resize(
    monkeypatch,
):
    import platform_utils

    monkeypatch.setattr(platform_utils, "UI_IS_MOBILE", True)
    mobile_cal = CalendarView(
        SimpleNamespace(width=1024, height=760, update=lambda *_a: None, run_task=lambda *_a: None)
    )
    assert mobile_cal._mode_schedule_btn.visible is False

    monkeypatch.setattr(platform_utils, "UI_IS_MOBILE", False)
    compact_page = SimpleNamespace(
        width=420, height=760, update=lambda *_a: None, run_task=lambda *_a: None
    )
    compact_cal = CalendarView(compact_page)
    assert compact_cal._mode_schedule_btn.visible is False

    desktop_page = SimpleNamespace(
        width=1120, height=760, update=lambda *_a: None, run_task=lambda *_a: None
    )
    desktop_cal = CalendarView(desktop_page)
    assert desktop_cal._mode_schedule_btn.visible is True

    target_selected = date(2026, 11, 26)
    desktop_cal._toggle_view_mode("schedule")
    desktop_cal._schedule_view._selected = target_selected

    desktop_page.width = 480
    desktop_cal.handle_viewport_resize()

    assert desktop_cal._mode_schedule_btn.visible is False
    assert desktop_cal._view_mode == "week"
    assert desktop_cal._selected_date == target_selected
    assert desktop_cal._week_start == target_selected - timedelta(days=target_selected.weekday())


def test_shift_bars_and_compact_room_badge_handle_cancelled_only_vs_mixed_shifts():
    from gui.components.calendar_view import (
        classify_session_shift,
        compact_room_badge_text,
    )
    from gui.core.theme import C

    monday = date(2026, 11, 23)
    tuesday = monday + timedelta(days=1)

    s_morn_edge = _class_session(monday, start_hm=(11, 59), end_hm=(12, 30))
    s_aft_start = _class_session(monday, start_hm=(12, 0), end_hm=(14, 30))
    s_aft_edge = _class_session(monday, start_hm=(17, 29), end_hm=(19, 0), cancelled=True)
    s_eve_only_cancelled = _class_session(
        monday,
        subject="Lập trình mạng",
        start_hm=(17, 30),
        end_hm=(20, 0),
        cancelled=True,
    )
    s_online = _class_session(
        tuesday,
        subject="Kỹ năng mềm",
        start_hm=(8, 0),
        end_hm=(10, 30),
        room="E-Learning",
        campus="Online",
    )

    assert classify_session_shift(s_morn_edge) == "morning"
    assert classify_session_shift(s_aft_start) == "afternoon"
    assert classify_session_shift(s_aft_edge) == "afternoon"
    assert classify_session_shift(s_eve_only_cancelled) == "evening"

    assert compact_room_badge_text(s_morn_edge) == "F502 · CS1"
    assert compact_room_badge_text(s_online) == "E-Learning"

    dialogs = []
    page = SimpleNamespace(
        width=1120,
        height=760,
        dialogs=dialogs,
        show_dialog=dialogs.append,
        pop_dialog=lambda: None,
        update=lambda *_a: None,
        run_task=lambda *_a: None,
    )
    cal = CalendarView(page)
    cal._schedule_view._weeks[monday] = {
        monday: (s_morn_edge, s_aft_start, s_aft_edge, s_eve_only_cancelled),
        tuesday: (s_online,),
    }
    cal._deadline_map[monday.isoformat()] = [
        {"title": "BT1", "urgency": "safe"},
        {"title": "BT2", "urgency": "warning"},
        {"title": "BT3", "urgency": "critical"},
    ]

    # Check month cell subtle 10x2.5 accent bar + preserved 3+ count badge
    cal._toggle_view_mode("month")
    cal._year, cal._month = 2026, 11
    cal._selected_day = monday.day
    cal._selected_date = monday
    cal._seed_period_pages()
    cal._render_month()

    month_cell = next(
        cell
        for row in cal._period_pages[1].day_cells
        for cell in row
        if cell.data["date"] == monday
    )
    markers = month_cell.data["dots_row"].controls
    assert markers[0].content.value == "3"
    assert any(
        isinstance(m, ft.Container) and m.width == 10 and m.height == 2.5 and m.bgcolor == C.ACCENT
        for m in markers
    )

    # Switch to week mode and inspect shift_bars (morning=active, afternoon=mixed->ACCENT, evening=cancelled-only->CRITICAL 0.45)
    cal._toggle_view_mode("week")
    cal._on_absolute_date_click(monday)
    mon_week_cell = cal._period_pages[1].week_cells[0]
    wed_week_cell = cal._period_pages[1].week_cells[2]
    assert set(mon_week_cell.data.keys()) >= {
        "wd_label",
        "day_text",
        "dots_row",
        "count_text",
        "shift_bars",
    }
    m_bar, a_bar, e_bar = mon_week_cell.data["shift_bars"].controls
    assert (m_bar.bgcolor, m_bar.opacity) == (C.ACCENT, 1.0)
    assert (a_bar.bgcolor, a_bar.opacity) == (C.ACCENT, 1.0)
    assert (e_bar.bgcolor, e_bar.opacity) == (C.CRITICAL, 0.45)

    for empty_bar in wed_week_cell.data["shift_bars"].controls:
        assert (empty_bar.bgcolor, empty_bar.opacity) == (C.BORDER, 0.25)

    # Compact session row in _detail_panel shortens room to "F502 · CS1" while 1-tap opens full details
    detail_texts = [
        t.value
        for t in _walk_controls(cal._day_list)
        if isinstance(t, ft.Text) and t.value
    ]
    assert "F502 · CS1" in detail_texts
    assert not any("P.Thạnh Mỹ Tây" in txt for txt in detail_texts)

    compact_rows = [
        c
        for c in cal._day_list.controls
        if isinstance(c, ft.Container)
        and isinstance(c.data, dict)
        and c.data.get("compact_session_row") is True
        and c.data.get("min_height") == 46
    ]
    assert len(compact_rows) == 4
    compact_rows[0].on_click(None)
    assert len(dialogs) == 1
    modal_texts = [
        t.value
        for t in _walk_controls(dialogs[0])
        if isinstance(t, ft.Text) and t.value
    ]
    assert "F502 - CS1 (P.Thạnh Mỹ Tây, TP.HCM)" in modal_texts


def test_week_navigation_advances_selected_weekday_and_filter_chips_switch_cleanly():
    monday = date(2026, 11, 23)
    wednesday = monday + timedelta(days=2)
    next_monday = monday + timedelta(days=7)
    next_wednesday = wednesday + timedelta(days=7)

    cal = CalendarView(_page_stub())
    cal._schedule_view._weeks[monday] = {
        monday: (_class_session(monday, subject="Giải tích 1"),),
        wednesday: (_class_session(wednesday, subject="Vật lý đại cương"),),
    }
    cal._schedule_view._weeks[next_monday] = {
        next_monday: (_class_session(next_monday, subject="Hóa đại cương"),),
        next_wednesday: (_class_session(next_wednesday, subject="Triết học"),),
    }
    cal._deadline_map[wednesday.isoformat()] = [
        {
            "title": "Nộp báo cáo thí nghiệm",
            "deadline": "2026-11-25 23:59",
            "urgency": "warning",
            "submission_status": "submitted",
        }
    ]

    cal._year, cal._month, cal._selected_day = (
        wednesday.year,
        wednesday.month,
        wednesday.day,
    )
    cal._selected_date = wednesday
    cal._week_start = monday
    cal._reset_period_pager()
    cal._render_week()

    assert "2 ca học" in cal._month_summary.value
    assert "1 bài tập" in cal._month_summary.value

    # Navigate +1 week and -1 week -> _selected_date shifts by +7 / -7 days (stays Wednesday!)
    asyncio.run(cal._request_period_navigation(1))
    assert cal._week_start == next_monday
    assert cal._selected_date == next_wednesday

    asyncio.run(cal._request_period_navigation(-1))
    assert cal._week_start == monday
    assert cal._selected_date == wednesday

    # Switch filter chip to "week_classes" -> shows both Monday & Wednesday classes
    cal._filter_week_chip.on_click(None)
    assert cal._detail_filter_mode == "week_classes"
    week_texts = [
        t.value
        for t in _walk_controls(cal._day_list)
        if isinstance(t, ft.Text) and t.value
    ]
    assert "Giải tích 1" in week_texts
    assert "Vật lý đại cương" in week_texts

    # Tapping a day cell in "week" mode while on "week_classes" resets filter to "day"
    cal._on_absolute_date_click(monday)
    assert cal._detail_filter_mode == "day"

    # Switch to "tasks" in "week" mode -> shows week's task; tapping day cell resets to "day"
    cal._filter_tasks_chip.on_click(None)
    assert cal._detail_filter_mode == "tasks"
    task_texts = [
        t.value
        for t in _walk_controls(cal._day_list)
        if isinstance(t, ft.Text) and t.value
    ]
    assert "Nộp báo cáo thí nghiệm" in task_texts
    cal._on_week_cell_click(2)
    assert cal._detail_filter_mode == "day"

    # In "month" mode, tapping a day in another week while on "week_classes" keeps "week_classes"
    cal._toggle_view_mode("month")
    cal._filter_week_chip.on_click(None)
    assert cal._detail_filter_mode == "week_classes"
    cal._on_absolute_date_click(next_monday)
    assert cal._detail_filter_mode == "week_classes"
    next_week_texts = [
        t.value
        for t in _walk_controls(cal._day_list)
        if isinstance(t, ft.Text) and t.value
    ]
    assert "Hóa đại cương" in next_week_texts
    assert "Triết học" in next_week_texts


def test_reset_schedule_clears_class_indicators_and_timeline_rows_across_accounts():
    from gui.core.theme import C

    monday = date(2026, 11, 23)
    cal = CalendarView(_page_stub())
    cal._schedule_view._weeks[monday] = {
        monday: (_class_session(monday, subject="Môn tài khoản A"),)
    }
    cal._year, cal._month, cal._selected_day = monday.year, monday.month, monday.day
    cal._selected_date = monday
    cal._week_start = monday
    cal._reset_period_pager()
    cal._render_week()
    cal._filter_week_chip.on_click(None)
    assert cal._detail_filter_mode == "week_classes"
    assert len(cal._day_list.controls) > 0

    # Account logout/switch while CalendarView is hidden
    cal.hide()
    cal.reset_schedule()

    assert cal._detail_filter_mode == "day"
    assert cal._day_list.controls == []
    assert "ca học" not in cal._month_summary.value
    for bar in cal._period_pages[1].week_cells[0].data["shift_bars"].controls:
        assert (bar.bgcolor, bar.opacity) == (C.BORDER, 0.25)


def test_day_tap_and_overlapping_month_swipe_never_cancel_or_wedge_inflight_week_load():
    import threading
    import time as _time

    week_start = date(2026, 11, 23)
    boundary_monday = date(2026, 11, 30)  # Overlaps both Nov 2026 and Dec 2026

    async def scenario():
        tasks: list[asyncio.Task] = []
        fetch_counts: dict[date, int] = {}
        entered_boundary = threading.Event()
        release_boundary = threading.Event()

        class _AsyncPage:
            width = 1120
            height = 760
            window = SimpleNamespace(width=1120)

            def run_task(self, coro_fn, *args, **kwargs):
                t = asyncio.create_task(coro_fn(*args, **kwargs))
                tasks.append(t)
                return t

            def update(self, *_controls):
                pass

        def _fetch_week(monday_date: date):
            fetch_counts[monday_date] = fetch_counts.get(monday_date, 0) + 1
            if monday_date == boundary_monday and fetch_counts[monday_date] == 1:
                entered_boundary.set()
                release_boundary.wait(timeout=1.0)
            else:
                _time.sleep(0.05)
            return {
                monday_date: (
                    _class_session(
                        monday_date, subject=f"Ca học {monday_date.isoformat()}"
                    ),
                )
            }

        cal = CalendarView(_AsyncPage(), fetch_schedule_week=_fetch_week)
        cal.visible = True

        # --- Reproducer A: Day taps during in-flight week load ---
        cal._year, cal._month, cal._selected_day = (
            week_start.year,
            week_start.month,
            week_start.day,
        )
        cal._selected_date = week_start
        cal._week_start = week_start
        cal._reset_period_pager()
        cal._render_week()

        await asyncio.sleep(0.01)
        assert week_start in cal._schedule_view._loading

        # Tap T3 and T5 while week_start fetch is still in flight
        cal._on_week_cell_click(1)
        cal._on_absolute_date_click(week_start + timedelta(days=4))

        await asyncio.sleep(0.10)
        assert cal._schedule_view._loading == set()
        assert len(cal._schedule_view.sessions_for_day(week_start)) == 1

        # Manual reload_week must NOT be blocked by a wedged _loading entry
        cal._schedule_view.reload_week(week_start)
        await asyncio.sleep(0.10)
        assert cal._schedule_view._loading == set()
        assert fetch_counts[week_start] >= 2

        # --- Reproducer B: Month 1 -> Month 2 swipe while shared boundary Monday is mid-fetch ---
        cal._schedule_view.hydrate_and_ensure_weeks(
            [boundary_monday], debounce_ms=250
        )
        # Wait past 250ms debounce until boundary_monday is mid-fetch in worker thread
        for _ in range(40):
            if entered_boundary.is_set():
                break
            await asyncio.sleep(0.02)
        assert entered_boundary.is_set()
        assert boundary_monday in cal._schedule_view._loading

        # Swipe to December 2026 (which also includes boundary_monday 2026-11-30!)
        dec_monday_2 = date(2026, 12, 7)
        cal._schedule_view.hydrate_and_ensure_weeks(
            [boundary_monday, dec_monday_2], debounce_ms=250
        )
        release_boundary.set()

        await asyncio.sleep(0.42)
        pending = [t for t in tasks if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        assert cal._schedule_view._loading == set()
        assert len(cal._schedule_view.sessions_for_day(boundary_monday)) == 1
        assert len(cal._schedule_view.sessions_for_day(dec_monday_2)) == 1

    asyncio.run(scenario())


def test_hidden_calendar_update_theme_and_reset_do_not_trigger_network_schedule_fetches():
    scheduled_tasks = []
    fetch_calls = []

    page = SimpleNamespace(
        width=1120,
        height=760,
        update=lambda *_a: None,
        run_task=lambda *_a, **_k: scheduled_tasks.append(_a),
    )
    cal = CalendarView(
        page,
        fetch_schedule_week=lambda m: fetch_calls.append(m) or {},
    )
    assert cal.visible is False
    scheduled_tasks.clear()

    cal.update_theme()
    cal.reset_schedule()

    assert scheduled_tasks == []
    assert fetch_calls == []


def test_calendar_defaults_to_week_mode_places_week_before_month_and_resets_to_week_on_show():
    from gui.core.theme import C

    page = SimpleNamespace(
        width=1120,
        height=760,
        update=lambda *_a: None,
        run_task=lambda *_a, **_k: None,
    )
    cal = CalendarView(page)
    active_bg = ft.Colors.with_opacity(0.19, C.ACCENT)

    # 1. Mode toggle order: Tuần -> Tháng -> Lịch học
    assert cal._mode_toggle.content.controls == [
        cal._mode_week_btn,
        cal._mode_month_btn,
        cal._mode_schedule_btn,
    ]
    assert cal._mode_week_text.value == "Tuần"
    assert cal._mode_month_text.value == "Tháng"
    assert cal._mode_schedule_text.value == "Lịch học"

    # 2. Initial default mode is "week"
    assert cal._view_mode == "week"
    assert cal._grid.visible is False
    assert cal._week_strip.visible is True
    assert cal._period_pager.height == 88
    assert cal._desktop_period_host.height == 88
    assert cal._mode_week_btn.bgcolor == active_bg
    assert cal._mode_month_btn.bgcolor == "transparent"
    assert cal._mode_schedule_btn.bgcolor == "transparent"

    # 3. Re-opening Calendar via show() from "month" or "schedule" defaults back to "week"
    cal._toggle_view_mode("month")
    assert cal._view_mode == "month"
    cal.hide()
    cal.show([])
    assert cal._view_mode == "week"
    assert cal._grid.visible is False
    assert cal._week_strip.visible is True
    assert cal._mode_week_btn.bgcolor == active_bg
    assert cal._mode_month_btn.bgcolor == "transparent"
    assert cal._month_label.value.startswith("Tuần ")

    cal._toggle_view_mode("schedule")
    assert cal._view_mode == "schedule"
    cal.hide()
    cal.show([])
    assert cal._view_mode == "week"
    assert cal._schedule_view.visible is False
    assert cal._detail_panel.visible is True
    assert cal._mode_week_btn.bgcolor == active_bg
    assert cal._mode_schedule_btn.bgcolor == "transparent"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
