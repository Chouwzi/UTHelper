import asyncio
import math
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import flet as ft


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.app_controller import AppController
from gui.components.desktop_window_header import build_desktop_window_header
from gui.core.theme import C
from core.version import APP_VERSION


def _walk(control):
    yield control
    content = getattr(control, "content", None)
    if content is not None:
        yield from _walk(content)
    for child in getattr(control, "controls", ()):
        yield from _walk(child)


def test_custom_header_contains_title_version_drag_minimize_maximize_and_close():
    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace()
    controller._safe_run_task = lambda *_args, **_kwargs: None
    controller._app_title = ft.Text("UTHelper")
    controller._version_badge = ft.Container(content=ft.Text(f"v{APP_VERSION}"))

    header = controller._build_desktop_window_header()
    controls = list(_walk(header))
    drag_areas = [item for item in controls if isinstance(item, ft.WindowDragArea)]
    cursor_regions = [item for item in controls if isinstance(item, ft.GestureDetector)]
    buttons = [item for item in controls if isinstance(item, ft.IconButton)]

    assert header.height == 34
    assert header.bgcolor == C.BG
    assert len(drag_areas) == 1
    assert drag_areas[0].maximizable is True
    assert len(cursor_regions) == 1
    assert cursor_regions[0].mouse_cursor == ft.MouseCursor.GRAB
    assert [button.icon for button in buttons] == [
        ft.Icons.HORIZONTAL_RULE_ROUNDED,
        ft.Icons.CROP_SQUARE_ROUNDED,
        ft.Icons.CLOSE_ROUNDED,
    ]
    assert controller._app_title in controls
    assert controller._version_badge in controls
    assert controller._app_title.weight == ft.FontWeight.W_700


def test_custom_header_without_on_maximize_preserves_non_maximizable_layout():
    header = build_desktop_window_header(
        on_minimize=lambda _e: None,
        on_close=lambda _e: None,
        on_maximize=None,
    )
    controls = list(_walk(header))
    drag_areas = [item for item in controls if isinstance(item, ft.WindowDragArea)]
    buttons = [item for item in controls if isinstance(item, ft.IconButton)]

    assert len(drag_areas) == 1
    assert drag_areas[0].maximizable is False
    assert [button.icon for button in buttons] == [
        ft.Icons.HORIZONTAL_RULE_ROUNDED,
        ft.Icons.CLOSE_ROUNDED,
    ]


def test_dashboard_desktop_header_replaces_old_title_row_with_sync_status(monkeypatch):
    controller = AppController.__new__(AppController)
    status = ft.Semantics(content=ft.Text("Đã cập nhật"))
    controller._sync_status = SimpleNamespace(control=status)
    controller.status_text = status.content
    controller._app_title = ft.Text("UTHelper")
    controller._version_badge = ft.Container(content=ft.Text(f"v{APP_VERSION}"))
    controller.calendar_btn = ft.IconButton(ft.Icons.CALENDAR_MONTH_ROUNDED)
    controller.grades_btn = ft.IconButton(ft.Icons.INSIGHTS_ROUNDED)
    controller.courses_btn = ft.IconButton(ft.Icons.MENU_BOOK_ROUNDED)
    controller.refresh_btn = ft.IconButton(ft.Icons.REFRESH_ROUNDED)
    controller.settings_btn = ft.IconButton(ft.Icons.SETTINGS_ROUNDED)
    monkeypatch.setattr("gui.app_controller.platform_utils.UI_IS_MOBILE", False)

    header = controller._build_dashboard_header()
    controls = list(_walk(header))

    assert status in controls
    assert controller._app_title not in controls
    assert controller._version_badge not in controls
    assert controller._dashboard_title in controls
    assert controller._dashboard_title.value == "Tổng quan"
    assert controller.refresh_btn in controls
    assert controller.calendar_btn not in controls
    assert controller.courses_btn not in controls
    assert controller.grades_btn not in controls
    assert controller.settings_btn not in controls
    assert status.expand is True
    assert controller.status_text.no_wrap is True
    assert controller.status_text.overflow == ft.TextOverflow.ELLIPSIS


def test_dashboard_omits_slop_stat_row_and_aligns_horizontal_keylines(monkeypatch):
    monkeypatch.setattr("gui.app_controller.platform_utils.UI_IS_MOBILE", False)
    assert not hasattr(AppController, "_build_stat_row")
    assert not hasattr(AppController, "_update_stat_tiles")

    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace(width=1280, height=800, window=SimpleNamespace())
    controller._nav_shell = SimpleNamespace(
        apply_width=lambda _w: True,
        apply_height=lambda _h: None,
    )
    controller.courses_view = None
    controller.grade_overview_view = None
    controller.calendar_view = None
    controller.course_grades_view = None
    controller.settings_view = ft.Container()
    controller.detail_view = ft.Container()
    controller._detail_host = ft.Container()
    controller._header_container = ft.Container()
    controller._update_banner = ft.Container()
    controller._filter_container = ft.Container()
    controller._content_area = ft.Container()
    controller._footer = ft.Container(visible=True)
    controller._sidebar = ft.Container(visible=False)
    controller._sidebar_title = ft.Container()
    controller._sidebar_column = ft.Column()
    controller.today_schedule_panel = ft.Container()
    controller.cards_column = ft.ListView()
    controller._dashboard_title = ft.Text("Tổng quan")
    controller._wide_layout = False
    controller._last_layout_width = None
    import threading

    controller._cards_lock = threading.Lock()
    controller.active_cards = []

    controller._apply_responsive_layout(1280, 800, force=True)

    # Header, search/filter toolbar, update banner, and cards (+10px card margin)
    # share the exact same horizontal keyline.
    assert controller._header_container.padding.left == controller._filter_container.padding.left
    assert controller._header_container.padding.right == controller._filter_container.padding.right
    assert controller._update_banner.margin.left == controller._header_container.padding.left
    assert controller._content_area.padding.left + 10 == controller._header_container.padding.left
    assert controller._content_area.padding.right + 10 == controller._header_container.padding.right
    assert controller._footer.visible is False
    assert controller._sidebar.visible is True


def test_dashboard_filter_reset_and_search_clear_controls():
    controller = AppController.__new__(AppController)
    controller.active_urgency = "warning"
    controller.active_type = "quiz"
    controller.active_course = "CS101"
    controller.active_search = "Chương 4"
    controller._reset_filters_btn = ft.TextButton("Xóa lọc", visible=False)
    controller._search_clear_btn = ft.IconButton(ft.Icons.CLOSE_ROUNDED, visible=False)
    controller.search_field = ft.TextField(value="Chương 4")
    selected = []
    controller.urgency_filter_menu = SimpleNamespace(
        set_selected=lambda k: selected.append(("urgency", k))
    )
    controller.type_filter_menu = SimpleNamespace(
        set_selected=lambda k: selected.append(("type", k))
    )
    controller.course_filter_menu = SimpleNamespace(
        set_selected=lambda k: selected.append(("course", k))
    )
    refreshes = []
    controller._refresh_ui = lambda: refreshes.append(True)

    controller._sync_filter_controls_state()
    assert controller._reset_filters_btn.visible is True
    assert controller._search_clear_btn.visible is True

    controller._clear_search()
    assert controller.active_search == ""
    assert controller.search_field.value == ""
    assert controller._search_clear_btn.visible is False
    assert controller._reset_filters_btn.visible is True

    asyncio.run(controller._reset_all_filters())
    assert controller.active_urgency == "all"
    assert controller.active_type == "all"
    assert controller.active_course == "all"
    assert controller._reset_filters_btn.visible is False
    assert selected == [("urgency", "all"), ("type", "all"), ("course", "all")]
    assert len(refreshes) == 2


def test_close_button_rotates_one_full_turn_only_on_hover_enter():
    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace()
    controller._safe_run_task = lambda *_args, **_kwargs: None
    controller._app_title = ft.Text("UTHelper")
    controller._version_badge = ft.Container(content=ft.Text(f"v{APP_VERSION}"))
    header = controller._build_desktop_window_header()
    close = [
        item
        for item in _walk(header)
        if isinstance(item, ft.IconButton) and item.icon == ft.Icons.CLOSE_ROUNDED
    ][0]
    updates = []
    close.update = lambda: updates.append(close.rotate)

    close.on_hover(SimpleNamespace(data="true"))
    close.on_hover(SimpleNamespace(data="false"))
    close.on_hover(SimpleNamespace(data=True))

    assert close.animate_rotation.duration == 360
    assert close.animate_rotation.curve == ft.AnimationCurve.EASE_IN_OUT_CUBIC
    assert updates == [math.tau, math.tau * 2]


def test_custom_minimize_and_close_have_distinct_native_actions():
    updates = []
    destroy = AsyncMock()
    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace(
        window=SimpleNamespace(minimized=False, destroy=destroy),
        update=lambda: updates.append("update"),
    )

    controller._minimize_window()
    asyncio.run(controller._exit_from_window_header())

    assert controller.page.window.minimized is True
    assert updates == ["update"]
    destroy.assert_awaited_once_with()
