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
