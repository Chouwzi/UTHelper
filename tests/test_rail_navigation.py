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


def test_navigate_destination_prompts_unsaved_settings_before_switching_tabs(
    monkeypatch,
):
    import flet as ft
    from gui.components.settings_view import SettingsView
    from gui.components.courses.adaptive_nav import AdaptiveNavShell
    from gui.core.theme import apply_theme

    dialogs = []
    scheduled = []

    def run_task(coro_or_fn, *args, **kwargs):
        res = coro_or_fn(*args, **kwargs) if callable(coro_or_fn) else coro_or_fn
        scheduled.append(res)

    page = SimpleNamespace(
        window=SimpleNamespace(always_on_top=False),
        theme_mode="dark",
        on_route_change=None,
        theme=None,
        dark_theme=None,
        show_dialog=lambda dlg: dialogs.append(dlg),
        pop_dialog=lambda: None,
        update=lambda: None,
        run_task=run_task,
    )

    view = SettingsView(page=page, orchestrator=None, on_close=lambda: None)
    view.update = lambda: None
    view.visible = True
    view._selected_theme = "midnight_blue"
    view._apply_theme_colors("midnight_blue")
    view._baseline_snapshot = view._capture_form_snapshot()

    controller = AppController.__new__(AppController)
    controller.page = page
    controller.settings_view = view
    controller.detail_view = SimpleNamespace(visible=False)
    controller.calendar_view = SimpleNamespace(visible=False)
    controller.grade_overview_view = SimpleNamespace(visible=False)
    controller.course_grades_view = SimpleNamespace(visible=False)
    controller.courses_view = SimpleNamespace(visible=False)
    controller._nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    controller._rail_items = None
    controller._data_lock = None
    controller.all_data = None
    controller._needs_reload = False
    controller._rebuild_colors = lambda: None
    controller._update_footer = lambda: None
    controller._diag_screen = lambda _s: None
    controller._diag_breadcrumb = lambda *_a, **_kw: None
    controller._show_calendar = AsyncMock()
    controller._show_courses = AsyncMock()
    controller._show_grades = AsyncMock()
    controller._show_settings = AsyncMock()
    controller._close_settings = AsyncMock()
    controller._safe_run_task = run_task
    controller.view_manager = SimpleNamespace(
        show_dashboard=lambda: None,
        show_courses=lambda: None,
        close_settings=AsyncMock(),
    )

    try:
        # User selects a new theme inside Settings without saving.
        view._on_theme_select("nord_frost")
        assert view.has_changes() is True

        # Clicking "settings" again while already in Settings keeps unsaved edits intact.
        asyncio.run(controller._navigate_destination("settings"))
        controller._show_settings.assert_not_awaited()
        assert view.has_changes() is True
        assert dialogs == []

        # Clicking another tab ("calendar") opens the unsaved-changes popup instead of switching!
        controller._nav_shell.set_active("calendar")
        asyncio.run(controller._navigate_destination("calendar"))
        controller._show_calendar.assert_not_awaited()
        assert view.visible is True
        # Rail/bottom-bar highlight is snapped back to "settings" (index 4).
        assert controller._nav_shell.rail.selected_index == 4
        assert controller._nav_shell.bar.selected_index == 4
        assert len(dialogs) == 1
        assert isinstance(dialogs[0], ft.AlertDialog)

        # Case 1: User clicks "Hủy" (index 0) -> stays in Settings with unsaved theme.
        dialogs[0].actions[0].on_click(None)
        assert view.visible is True
        assert view.has_changes() is True
        controller._show_calendar.assert_not_awaited()

        # Case 2: User clicks "calendar" again, then chooses "Bỏ thay đổi" (index 1)
        # -> reverts theme to baseline and navigates directly to "calendar".
        dialogs.clear()
        asyncio.run(controller._navigate_destination("calendar"))
        assert len(dialogs) == 1
        monkeypatch.setattr("gui.core.theme.load_theme_from_settings", lambda: apply_theme("midnight_blue"))
        dialogs[0].actions[1].on_click(None)
        while scheduled:
            asyncio.run(scheduled.pop(0))
        assert view.has_changes() is False
        assert view._selected_theme == "midnight_blue"
        assert view.visible is False
        controller._show_calendar.assert_awaited_once()

        # Case 3: User re-enters Settings, picks theme, clicks "grades", chooses "Lưu" (index 2)
        # -> saves and navigates directly to "grades".
        view.visible = True
        view._on_theme_select("ocean_teal")
        dialogs.clear()
        saved_called = []

        async def fake_save(_e):
            saved_called.append(view._selected_theme)
            view._baseline_snapshot = view._capture_form_snapshot()
            return True

        view._save = fake_save
        asyncio.run(controller._navigate_destination("grades"))
        assert len(dialogs) == 1
        dialogs[0].actions[2].on_click(None)
        while scheduled:
            asyncio.run(scheduled.pop(0))
        assert saved_called == ["ocean_teal"]
        assert view.visible is False
        controller._show_grades.assert_awaited_once()
    finally:
        apply_theme("midnight_blue")
