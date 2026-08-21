import asyncio
import math
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import flet as ft


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.app_controller import AppController
from gui.core.theme import C


def _walk(control):
    yield control
    content = getattr(control, "content", None)
    if content is not None:
        yield from _walk(content)
    for child in getattr(control, "controls", ()):
        yield from _walk(child)


def test_custom_header_contains_title_version_drag_minimize_and_close():
    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace()
    controller._safe_run_task = lambda *_args, **_kwargs: None
    controller._app_title = ft.Text("UTHelper")
    controller._version_badge = ft.Container(content=ft.Text("v2.3.1"))

    header = controller._build_desktop_window_header()
    controls = list(_walk(header))
    drag_areas = [item for item in controls if isinstance(item, ft.WindowDragArea)]
    buttons = [item for item in controls if isinstance(item, ft.IconButton)]

    assert header.height == 34
    assert header.bgcolor == C.BG
    assert len(drag_areas) == 1
    assert drag_areas[0].maximizable is False
    assert [button.icon for button in buttons] == [
        ft.Icons.HORIZONTAL_RULE_ROUNDED,
        ft.Icons.CLOSE_ROUNDED,
    ]
    assert controller._app_title in controls
    assert controller._version_badge in controls
    assert controller._app_title.weight == ft.FontWeight.W_700


def test_dashboard_desktop_header_replaces_old_title_row_with_sync_status(monkeypatch):
    controller = AppController.__new__(AppController)
    status = ft.Semantics(content=ft.Text("Đã cập nhật"))
    controller._sync_status = SimpleNamespace(control=status)
    controller.status_text = status.content
    controller._app_title = ft.Text("UTHelper")
    controller._version_badge = ft.Container(content=ft.Text("v2.3.1"))
    controller.calendar_btn = ft.IconButton(ft.Icons.CALENDAR_MONTH_ROUNDED)
    controller.grades_btn = ft.IconButton(ft.Icons.INSIGHTS_ROUNDED)
    controller.refresh_btn = ft.IconButton(ft.Icons.REFRESH_ROUNDED)
    controller.settings_btn = ft.IconButton(ft.Icons.SETTINGS_ROUNDED)
    monkeypatch.setattr("gui.app_controller.platform_utils.UI_IS_MOBILE", False)

    header = controller._build_dashboard_header()
    controls = list(_walk(header))

    assert status in controls
    assert controller._app_title not in controls
    assert controller._version_badge not in controls
    assert status.expand is True
    assert controller.status_text.no_wrap is True
    assert controller.status_text.overflow == ft.TextOverflow.ELLIPSIS


def test_close_button_rotates_one_full_turn_only_on_hover_enter():
    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace()
    controller._safe_run_task = lambda *_args, **_kwargs: None
    controller._app_title = ft.Text("UTHelper")
    controller._version_badge = ft.Container(content=ft.Text("v2.3.1"))
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
