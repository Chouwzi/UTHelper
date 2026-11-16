import asyncio
import os
import sys
from types import SimpleNamespace

import flet as ft

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.app_controller import AppController
from gui.components.adaptive_filter_menu import (
    AdaptiveFilterMenu,
    FilterMenuOption,
)
from gui.view_manager import ViewManager


class _FakePage:
    def __init__(self):
        self.update_count = 0
        self.controls = []
        self.width = 420
        self.height = 800

    def update(self):
        self.update_count += 1

    def add(self, *controls):
        self.controls.extend(controls)


def test_view_manager_preserves_persistent_overlays_after_navigation():
    page = _FakePage()
    dashboard = SimpleNamespace(visible=True, opacity=1.0)
    detail = SimpleNamespace(
        visible=False,
        offset=None,
        opacity=1.0,
        show_loading=lambda _data: None,
        update_detail=lambda _data: None,
    )
    calendar = SimpleNamespace(
        visible=False,
        show=lambda _data: None,
        hide=lambda: None,
        offset=None,
        opacity=1.0,
    )
    settings = SimpleNamespace(
        visible=False,
        offset=None,
        opacity=1.0,
        load_current_settings=lambda: asyncio.sleep(0),
        cancel_pending_load=lambda: None,
    )
    grades = SimpleNamespace(
        visible=False,
        show_loading=lambda: None,
        hide=lambda: None,
    )
    dismiss_called = []
    controller = SimpleNamespace(
        calendar_btn=SimpleNamespace(icon_color=None),
        grades_btn=SimpleNamespace(icon_color=None),
        _dismiss_desktop_filter_menu=lambda: dismiss_called.append("dismiss"),
    )
    host = SimpleNamespace(controls=[])

    overlay1 = SimpleNamespace(name="top_dismiss")
    overlay2 = SimpleNamespace(name="upper_body_dismiss")
    overlay3 = SimpleNamespace(name="dismiss")
    overlay4 = SimpleNamespace(name="popup_host")
    overlays = [overlay1, overlay2, overlay3, overlay4]

    manager = ViewManager(
        page=page,
        dashboard=dashboard,
        detail_view=detail,
        settings_view=settings,
        calendar_view=calendar,
        grade_overview_view=grades,
        controller=controller,
        view_host=host,
        overlays=overlays,
    )

    # 1. Initially show dashboard
    manager.show_dashboard()
    assert host.controls == [dashboard, overlay1, overlay2, overlay3, overlay4]

    # 2. Open Calendar then Close Calendar
    manager.show_calendar([])
    assert host.controls == [calendar, overlay1, overlay2, overlay3, overlay4]
    asyncio.run(manager.close_calendar())
    assert host.controls == [dashboard, overlay1, overlay2, overlay3, overlay4]

    # 3. Open Settings then Close Settings
    asyncio.run(manager.show_settings())
    assert host.controls == [settings, overlay1, overlay2, overlay3, overlay4]
    asyncio.run(manager.close_settings())
    assert host.controls == [dashboard, overlay1, overlay2, overlay3, overlay4]

    # 4. Open Detail Card then Close Detail
    manager.show_detail_loading({"id": 1})
    assert host.controls == [dashboard, detail, overlay1, overlay2, overlay3, overlay4]
    asyncio.run(manager.close_detail(detail_from_calendar=False))
    assert host.controls == [dashboard, overlay1, overlay2, overlay3, overlay4]

    # 5. Open Grades then Close Grades
    manager.show_grades_loading()
    assert host.controls == [grades, overlay1, overlay2, overlay3, overlay4]
    asyncio.run(manager.close_grades())
    assert host.controls == [dashboard, overlay1, overlay2, overlay3, overlay4]


def test_app_controller_filter_overlay_available_after_navigating_tabs_and_detail(monkeypatch):
    page = _FakePage()
    controller = AppController.__new__(AppController)
    controller.page = page
    import inspect

    controller._safe_run_task = lambda fn, *args: asyncio.run(fn(*args)) if inspect.iscoroutinefunction(fn) else fn(*args)

    # Create filter overlays and view host as in _init_ui
    top_dismiss = ft.Container(visible=False)
    upper_dismiss = ft.Container(visible=False)
    dismiss_layer = ft.Container(visible=False)
    popup_host = ft.Container(visible=True, opacity=0, ignore_interactions=True)

    controller._desktop_filter_top_dismiss_layer = top_dismiss
    controller._desktop_filter_upper_body_dismiss_layer = upper_dismiss
    controller._desktop_filter_dismiss_layer = dismiss_layer
    controller._desktop_filter_popup_host = popup_host
    controller._active_desktop_filter_menu = None

    dashboard = ft.Container(visible=True)
    detail = ft.Container(visible=False)
    settings = ft.Container(visible=False)
    calendar = ft.Container(visible=False)
    grades = ft.Container(visible=False)

    detail.show_loading = lambda _d: None
    detail.update_detail = lambda _d: None
    calendar.show = lambda _d: None
    calendar.hide = lambda: None
    settings.load_current_settings = lambda: asyncio.sleep(0)
    settings.cancel_pending_load = lambda: None
    grades.show_loading = lambda: None
    grades.hide = lambda: None

    controller.calendar_btn = ft.IconButton(icon_color=None)
    controller.grades_btn = ft.IconButton(icon_color=None)

    overlays = [top_dismiss, upper_dismiss, dismiss_layer, popup_host]
    main_stack = ft.Stack(controls=[dashboard, *overlays])

    view_manager = ViewManager(
        page=page,
        dashboard=dashboard,
        detail_view=detail,
        settings_view=settings,
        calendar_view=calendar,
        grade_overview_view=grades,
        controller=controller,
        view_host=main_stack,
        overlays=overlays,
    )
    controller.view_manager = view_manager

    menu = AdaptiveFilterMenu(
        page=page,
        title="Mức độ",
        options=[
            FilterMenuOption("all", "Tất cả", "#E2E8F0", count=5),
            FilterMenuOption("warning", "Sắp tới", "#F59E0B", count=2),
        ],
        selected_key="all",
        on_select=lambda _k: None,
        mobile=False,
    )

    # Step 1: User navigates to detail card and closes it
    view_manager.show_detail_loading({"id": 101})
    assert popup_host in main_stack.controls
    asyncio.run(view_manager.close_detail(detail_from_calendar=False))
    assert popup_host in main_stack.controls
    assert main_stack.controls == [dashboard, top_dismiss, upper_dismiss, dismiss_layer, popup_host]

    # Step 2: User clicks filter button on dashboard
    event = SimpleNamespace(
        global_position=SimpleNamespace(x=100.0, y=120.0),
        local_position=SimpleNamespace(x=20.0, y=10.0),
    )
    controller._toggle_desktop_filter_menu(menu, event)

    # Popup host should be populated and active in the stack
    assert controller._active_desktop_filter_menu is menu
    assert menu._expanded is True
    assert popup_host.content is menu.popup_panel
    assert popup_host.ignore_interactions is False
    assert dismiss_layer.visible is True
    assert popup_host in main_stack.controls

    # Step 3: User dismisses filter
    controller._dismiss_desktop_filter_menu()
    assert controller._active_desktop_filter_menu is None
    assert menu._expanded is False
    assert popup_host.opacity == 0
    assert popup_host.ignore_interactions is True
    assert dismiss_layer.visible is False
    assert popup_host in main_stack.controls


def test_view_manager_with_tab_and_detail_switchers():
    page = _FakePage()
    dashboard = SimpleNamespace(visible=True, opacity=1.0)
    detail = SimpleNamespace(
        visible=False,
        offset=None,
        opacity=1.0,
        show_loading=lambda _data: None,
        update_detail=lambda _data: None,
    )
    calendar = SimpleNamespace(
        visible=False,
        show=lambda _data: None,
        hide=lambda: None,
        offset=None,
        opacity=1.0,
    )
    settings = SimpleNamespace(
        visible=False,
        offset=None,
        opacity=1.0,
        load_current_settings=lambda: asyncio.sleep(0),
        cancel_pending_load=lambda: None,
    )
    grades = SimpleNamespace(
        visible=False,
        show_loading=lambda: None,
        hide=lambda: None,
    )
    controller = SimpleNamespace(
        calendar_btn=SimpleNamespace(icon_color=None),
        grades_btn=SimpleNamespace(icon_color=None),
        _dismiss_desktop_filter_menu=lambda: None,
    )
    empty_detail = ft.Container(expand=True, ignore_interactions=True)
    tab_switcher = ft.AnimatedSwitcher(content=dashboard)
    detail_switcher = ft.AnimatedSwitcher(content=empty_detail)
    main_stack = ft.Stack(controls=[tab_switcher, detail_switcher])

    manager = ViewManager(
        page=page,
        dashboard=dashboard,
        detail_view=detail,
        settings_view=settings,
        calendar_view=calendar,
        grade_overview_view=grades,
        controller=controller,
        view_host=main_stack,
        tab_switcher=tab_switcher,
        detail_switcher=detail_switcher,
        empty_detail=empty_detail,
    )

    # 1. Open and Close Calendar via tab_switcher
    manager.show_calendar([])
    assert tab_switcher.content is calendar
    assert detail_switcher.content is empty_detail
    asyncio.run(manager.close_calendar())
    assert tab_switcher.content is dashboard
    assert detail_switcher.content is empty_detail

    # 2. Open and Close Settings via tab_switcher
    asyncio.run(manager.show_settings())
    assert tab_switcher.content is settings
    asyncio.run(manager.close_settings())
    assert tab_switcher.content is dashboard

    # 3. Open Detail Card over Dashboard via detail_switcher without touching tab_switcher
    manager.show_detail_loading({"id": "item-1"})
    assert tab_switcher.content is dashboard  # Dashboard is still mounted in tab_switcher!
    assert detail_switcher.content is detail  # Detail view is presented on top layer
    asyncio.run(manager.close_detail(detail_from_calendar=False))
    assert tab_switcher.content is dashboard  # Dashboard remains intact
    assert detail_switcher.content is empty_detail

    # 4. Open and Close Grades via tab_switcher
    manager.show_grades_loading()
    assert tab_switcher.content is grades
    asyncio.run(manager.close_grades())
    assert tab_switcher.content is dashboard

