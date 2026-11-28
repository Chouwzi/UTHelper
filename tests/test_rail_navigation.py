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
    from config import settings
    from gui.components.settings_view import SettingsView
    from gui.components.courses.adaptive_nav import AdaptiveNavShell
    from gui.core.theme import apply_theme, load_theme_from_settings

    original_theme = settings.THEME

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
        # Rail/bottom-bar highlight is snapped back to "settings" (index 5:
        # home, courses, calendar, grades, roadmap, settings).
        assert controller._nav_shell.rail.selected_index == 5
        assert controller._nav_shell.bar.selected_index == 5
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
        settings.THEME = original_theme
        load_theme_from_settings()


def test_leaving_settings_for_grades_never_violates_animated_switcher_visibility():
    """Regression test for ValueError('AnimatedSwitcher.content must be provided and visible')."""
    import flet as ft
    from gui.components.courses.adaptive_nav import AdaptiveNavShell
    from gui.view_manager import ViewManager

    dashboard = ft.Container(visible=False)
    detail_view = ft.Container(visible=False)
    settings_view = ft.Container(visible=True)
    calendar_view = ft.Container(visible=False)
    grade_view = ft.Container(visible=False)
    grade_view.show_loading = lambda: setattr(grade_view, "visible", True)
    grade_view.update_theme = lambda: None
    grade_view.update_academic_data = lambda *_a, **_k: None
    courses_view = ft.Container(visible=False)

    tab_switcher = ft.AnimatedSwitcher(content=settings_view)
    empty_detail = ft.Container(visible=True)
    detail_switcher = ft.AnimatedSwitcher(content=empty_detail)

    validated_updates = []

    def strict_page_update():
        # Run Flet's real AnimatedSwitcher validator on every page.update()!
        assert tab_switcher.content is not None
        assert tab_switcher.content.visible is True
        tab_switcher._before_update_safe()
        detail_switcher._before_update_safe()
        validated_updates.append(tab_switcher.content)

    page = SimpleNamespace(update=strict_page_update, overlay=[])
    controller = AppController.__new__(AppController)
    controller.page = page
    controller.dashboard = dashboard
    controller.detail_view = detail_view
    controller.settings_view = settings_view
    controller.calendar_view = calendar_view
    controller.grade_overview_view = grade_view
    controller.courses_view = courses_view
    controller.course_grades_view = None
    controller.tab_switcher = tab_switcher
    controller.detail_switcher = detail_switcher
    controller._detail_host = ft.Container(ignore_interactions=True)
    controller._nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    controller._rail_items = None
    controller._reconcile_settings_after_exit = lambda: None
    refresh_page_flags = []
    controller._update_footer = lambda *, update_page=True: (
        refresh_page_flags.append(update_page)
        or (strict_page_update() if update_page else None)
    )
    controller._needs_reload = False
    controller._academic_grade_service = SimpleNamespace(
        load_academic_data=lambda **_kw: [],
        get_radar_profiles=lambda _s: [],
    )
    controller.orchestrator = SimpleNamespace(_get_userid=lambda: None)
    controller._diag_screen = lambda *_a, **_k: None
    controller._diag_breadcrumb = lambda *_a, **_k: None
    controller.view_manager = ViewManager(
        page,
        dashboard,
        detail_view,
        settings_view,
        calendar_view,
        grade_view,
        controller,
        courses_view=courses_view,
        tab_switcher=tab_switcher,
        detail_switcher=detail_switcher,
        empty_detail=empty_detail,
        on_destination_change=controller._nav_shell.set_active,
    )

    # Navigate Settings -> Grades -> Settings -> Courses -> Settings -> Calendar -> Home
    asyncio.run(controller._navigate_destination("grades"))
    assert tab_switcher.content is grade_view
    assert grade_view.visible is True
    assert settings_view.visible is False
    assert refresh_page_flags == [False]

    settings_view.visible = True
    tab_switcher.content = settings_view
    asyncio.run(controller._navigate_destination("courses"))
    assert tab_switcher.content is courses_view
    assert courses_view.visible is True
    assert settings_view.visible is False

    settings_view.visible = True
    tab_switcher.content = settings_view
    calendar_view.show = lambda _d: None
    import threading

    controller._data_lock = threading.Lock()
    controller.all_data = []
    asyncio.run(controller._navigate_destination("calendar"))
    assert tab_switcher.content is calendar_view
    assert calendar_view.visible is True
    assert settings_view.visible is False
    assert len(validated_updates) >= 4


def test_adaptive_nav_shell_set_active_is_idempotent():
    from gui.components.courses.adaptive_nav import AdaptiveNavShell

    nav = AdaptiveNavShell(on_navigate=lambda _k: None)
    nav.set_active("grades")
    assert nav.rail.selected_index == 3
    assert nav.bar.selected_index == 3

    class _IndexSpy:
        def __init__(self, initial: int) -> None:
            self._selected_index = initial
            self.writes: list[int] = []

        @property
        def selected_index(self) -> int:
            return self._selected_index

        @selected_index.setter
        def selected_index(self, value: int) -> None:
            self.writes.append(value)
            self._selected_index = value

    nav.rail = _IndexSpy(3)
    nav.bar = _IndexSpy(3)
    nav.set_active("grades")
    assert nav.rail.writes == []
    assert nav.bar.writes == []


def test_leaving_clean_settings_for_all_four_tabs_never_snaps_back_to_settings(
    monkeypatch,
):
    import threading
    import flet as ft
    from gui.components.courses.adaptive_nav import AdaptiveNavShell
    from gui.core.theme import C, load_theme_from_settings, semantic_palette
    from gui.view_manager import ViewManager

    # Isolate theme state so prior tests' apply_theme() calls do not leave C
    # out of sync with load_theme_from_settings() on clean CI runners.
    load_theme_from_settings()
    monkeypatch.setattr("gui.core.theme.load_theme_from_settings", load_theme_from_settings)

    dashboard = ft.Container(visible=False)
    detail_view = ft.Container(visible=False)
    settings_view = ft.Container(visible=True)
    settings_view.has_changes = lambda: False
    settings_view.cancel_pending_load = lambda: None
    calendar_view = ft.Container(visible=False)
    calendar_view.show = lambda _d: setattr(calendar_view, "visible", True)
    calendar_view.hide = lambda: None
    grade_view = ft.Container(visible=False)
    grade_view.show_loading = lambda: setattr(grade_view, "visible", True)
    grade_view.update_theme = lambda: None
    grade_view.update_academic_data = lambda *_a, **_k: None
    courses_view = ft.Container(visible=False)

    tab_switcher = ft.AnimatedSwitcher(content=settings_view)
    empty_detail = ft.Container(visible=True)
    detail_switcher = ft.AnimatedSwitcher(content=empty_detail)

    flushed_bar_indices: list[int] = []
    nav_calls: list[str] = []

    nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    orig_set_active = nav_shell.set_active

    def spy_set_active(key: str) -> None:
        nav_calls.append(key)
        orig_set_active(key)

    nav_shell.set_active = spy_set_active

    def record_page_update() -> None:
        flushed_bar_indices.append(nav_shell.bar.selected_index)

    page = SimpleNamespace(
        update=record_page_update,
        overlay=[],
        theme_mode=None,
        theme=None,
        dark_theme=None,
        bgcolor=C.BG,
    )
    controller = AppController.__new__(AppController)
    controller.page = page
    controller.dashboard = dashboard
    controller.detail_view = detail_view
    controller.settings_view = settings_view
    controller.calendar_view = calendar_view
    controller.grade_overview_view = grade_view
    controller.courses_view = courses_view
    controller.course_grades_view = None
    controller.tab_switcher = tab_switcher
    controller.detail_switcher = detail_switcher
    controller._detail_host = ft.Container(ignore_interactions=True)
    controller._nav_shell = nav_shell
    controller._rail_items = None
    controller._rendered_palette = semantic_palette()
    real_rebuild = AppController._rebuild_colors.__get__(controller, AppController)
    clean_rebuild_calls: list[tuple[bool, bool]] = []

    def tracked_real_rebuild(*, sync_nav: bool = True, update_page: bool = True) -> None:
        clean_rebuild_calls.append((sync_nav, update_page))
        real_rebuild(sync_nav=sync_nav, update_page=update_page)

    controller._rebuild_colors = tracked_real_rebuild
    controller._reconcile_settings_after_exit = AppController._reconcile_settings_after_exit.__get__(
        controller, AppController
    )
    controller._update_footer = lambda *, update_page=True: None
    controller._needs_reload = False
    controller._data_lock = threading.Lock()
    controller.all_data = []
    controller._academic_grade_service = SimpleNamespace(
        load_academic_data=lambda **_kw: [],
        get_radar_profiles=lambda _s: [],
    )
    controller.orchestrator = SimpleNamespace(_get_userid=lambda: None)
    controller._diag_screen = lambda *_a, **_k: None
    controller._diag_breadcrumb = lambda *_a, **_k: None
    controller.view_manager = ViewManager(
        page,
        dashboard,
        detail_view,
        settings_view,
        calendar_view,
        grade_view,
        controller,
        courses_view=courses_view,
        tab_switcher=tab_switcher,
        detail_switcher=detail_switcher,
        empty_detail=empty_detail,
        on_destination_change=nav_shell.set_active,
    )

    for target, expected_idx in (("home", 0), ("courses", 1), ("calendar", 2), ("grades", 3)):
        nav_calls.clear()
        flushed_bar_indices.clear()
        settings_view.visible = True
        tab_switcher.content = settings_view
        nav_shell.set_active("settings")
        nav_calls.clear()

        asyncio.run(controller._navigate_destination(target))
        assert clean_rebuild_calls == [], (target, clean_rebuild_calls)
        assert "settings" not in nav_calls, (target, nav_calls)
        assert 4 not in flushed_bar_indices, (target, flushed_bar_indices)
        assert nav_shell.bar.selected_index == expected_idx
        assert settings_view.visible is False

    # Even when palette rebuild IS needed on exit, sync_nav=False & update_page=False prevent snap-back
    rebuild_calls = []
    controller._rendered_palette = ("stale",)
    controller._rebuild_colors = lambda *, sync_nav=True, update_page=True: rebuild_calls.append(
        (sync_nav, update_page)
    )
    settings_view.visible = True
    tab_switcher.content = settings_view
    nav_calls.clear()
    flushed_bar_indices.clear()
    asyncio.run(controller._navigate_destination("courses"))
    assert rebuild_calls == [(False, False)]
    assert "settings" not in nav_calls
    assert 4 not in flushed_bar_indices
    assert nav_shell.bar.selected_index == 1

    # Also verify real AppController._rebuild_colors executes cleanly under forced rebuild without snap-back
    controller._rendered_palette = ("stale",)
    controller._rebuild_colors = tracked_real_rebuild
    settings_view.visible = True
    tab_switcher.content = settings_view
    nav_calls.clear()
    flushed_bar_indices.clear()
    clean_rebuild_calls.clear()
    asyncio.run(controller._navigate_destination("grades"))
    assert clean_rebuild_calls == [(False, False)]
    assert controller._rendered_palette == semantic_palette()
    assert "settings" not in nav_calls
    assert 4 not in flushed_bar_indices
    assert nav_shell.bar.selected_index == 3
    assert settings_view.visible is False
