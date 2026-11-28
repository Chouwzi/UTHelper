import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gui.components.calendar_view import CalendarView
from gui.view_manager import ViewManager


class _Page:
    def __init__(self):
        self.update_count = 0

    def update(self):
        self.update_count += 1


def test_show_calendar_seeds_real_period_pager_once():
    page = _Page()
    calendar = CalendarView(page)
    controller = SimpleNamespace(
        calendar_btn=SimpleNamespace(icon_color=None),
        grades_btn=SimpleNamespace(icon_color=None),
    )
    manager = ViewManager(
        page=page,
        dashboard=SimpleNamespace(visible=True),
        detail_view=SimpleNamespace(visible=False),
        settings_view=SimpleNamespace(visible=False),
        calendar_view=calendar,
        grade_overview_view=SimpleNamespace(visible=False),
        controller=controller,
    )
    seed_count = 0
    original_seed = calendar._seed_period_pages

    def counted_seed():
        nonlocal seed_count
        seed_count += 1
        original_seed()

    calendar._seed_period_pages = counted_seed

    manager.show_calendar([])

    assert seed_count == 1
    assert calendar.visible is True


def test_calendar_show_reuses_period_pages_and_defers_duplicate_update(monkeypatch):
    import platform_utils
    from datetime import date

    monkeypatch.setattr(platform_utils, "UI_IS_DESKTOP", False)
    page = _Page()
    calendar = CalendarView(page)
    controller = SimpleNamespace(
        calendar_btn=SimpleNamespace(icon_color=None),
        grades_btn=SimpleNamespace(icon_color=None),
    )
    manager = ViewManager(
        page=page,
        dashboard=SimpleNamespace(visible=True),
        detail_view=SimpleNamespace(visible=False),
        settings_view=SimpleNamespace(visible=False),
        calendar_view=calendar,
        grade_overview_view=SimpleNamespace(visible=False),
        controller=controller,
    )

    assert calendar._make_day_cell().animate is None
    assert calendar._make_week_cell(0).animate is None

    initial_controls = [period.control for period in calendar._period_pages]
    manager.show_calendar([])
    assert page.update_count == 1
    assert [period.control for period in calendar._period_pages] == initial_controls

    manager.show_calendar([])
    assert page.update_count == 2
    assert [period.control for period in calendar._period_pages] == initial_controls

    today = date.today()
    calendar.update_data(
        [
            {
                "title": "Deadline",
                "deadline": f"{today.year}-{today.month:02d}-{today.day:02d} 12:00",
                "urgency": "warning",
            }
        ]
    )
    assert [period.control for period in calendar._period_pages] != initial_controls


def test_show_calendar_hydrates_cached_schedule_in_single_update(tmp_path):
    from datetime import date, datetime, time
    from core.today_schedule import ClassSession, WeekScheduleCache, monday_of

    today = date.today()
    monday = monday_of(today)
    cache = WeekScheduleCache(cache_dir=tmp_path, namespace="vm-cal")
    cache.save_week(
        monday,
        {
            today: (
                ClassSession(
                    session_id="vm-sess-1",
                    subject="Kiến trúc máy tính",
                    course_code="0101001",
                    start_at=datetime.combine(today, time(7, 30)),
                    end_at=datetime.combine(today, time(10, 0)),
                    room="F502 - CS1 (P.Thạnh Mỹ Tây, TP.HCM)",
                    campus="Cơ sở 1",
                    period_start=1,
                    period_end=3,
                    cancelled=False,
                    note="",
                ),
            )
        },
        datetime.now(),
    )

    page = _Page()
    calendar = CalendarView(page, schedule_cache=cache)
    controller = SimpleNamespace(
        calendar_btn=SimpleNamespace(icon_color=None),
        grades_btn=SimpleNamespace(icon_color=None),
    )
    manager = ViewManager(
        page=page,
        dashboard=SimpleNamespace(visible=True),
        detail_view=SimpleNamespace(visible=False),
        settings_view=SimpleNamespace(visible=False),
        calendar_view=calendar,
        grade_overview_view=SimpleNamespace(visible=False),
        controller=controller,
    )

    manager.show_calendar([])

    assert page.update_count == 1
    assert len(calendar._schedule_view.sessions_for_day(today)) == 1
    assert len(calendar._day_list.controls) == 1

