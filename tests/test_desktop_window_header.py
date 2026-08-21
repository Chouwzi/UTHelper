import asyncio
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


def test_custom_header_has_only_drag_minimize_and_close_controls():
    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace()
    controller._safe_run_task = lambda *_args, **_kwargs: None

    header = controller._build_desktop_window_header()
    controls = list(_walk(header))
    drag_areas = [item for item in controls if isinstance(item, ft.WindowDragArea)]
    buttons = [item for item in controls if isinstance(item, ft.IconButton)]

    assert header.height == 34
    assert header.bgcolor == C.BG
    assert len(drag_areas) == 1
    assert drag_areas[0].maximizable is False
    assert [button.icon for button in buttons] == [
        ft.Icons.MINIMIZE_ROUNDED,
        ft.Icons.CLOSE_ROUNDED,
    ]
    assert not any(isinstance(item, ft.Text) for item in controls)


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
