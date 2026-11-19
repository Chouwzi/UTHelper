import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.app_controller import AppController


def _controller(active: str) -> AppController:
    controller = AppController.__new__(AppController)
    controller.detail_view = SimpleNamespace(visible=False)
    controller.settings_view = SimpleNamespace(
        visible=active == "settings", has_changes=lambda: False
    )
    controller.calendar_view = SimpleNamespace(visible=active == "calendar")
    controller.grade_overview_view = SimpleNamespace(visible=active == "grades")
    controller.courses_view = SimpleNamespace(visible=active == "courses")
    controller._dismiss_desktop_filter_menu = lambda: None
    controller._sync_rail_active = lambda *a, **k: None
    for name in (
        "_close_detail", "_close_settings", "_close_calendar", "_close_grades",
        "_close_courses", "_show_calendar", "_show_grades", "_show_courses",
        "_show_settings",
    ):
        setattr(controller, name, AsyncMock())
    return controller


def test_clicking_the_active_rail_item_keeps_it_open():
    for section in ("calendar", "grades", "courses", "settings"):
        controller = _controller(section)
        asyncio.run(controller._rail_navigate(section))
        for name in ("_close_settings", "_close_calendar", "_close_grades", "_close_courses"):
            getattr(controller, name).assert_not_awaited()
        for name in ("_show_calendar", "_show_grades", "_show_courses", "_show_settings"):
            getattr(controller, name).assert_not_awaited()


def test_clicking_another_rail_item_still_switches_section():
    controller = _controller("calendar")
    asyncio.run(controller._rail_navigate("courses"))
    controller._close_calendar.assert_awaited_once()
    controller._show_courses.assert_awaited_once()


def test_clicking_home_from_a_section_still_returns_to_overview():
    controller = _controller("grades")
    asyncio.run(controller._rail_navigate("home"))
    controller._close_grades.assert_awaited_once()
    controller._show_grades.assert_not_awaited()
