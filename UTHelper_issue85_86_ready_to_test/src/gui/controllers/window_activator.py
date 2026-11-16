"""Thread-safe routing of Windows activation requests onto the Flet UI loop."""

from __future__ import annotations

import flet as ft


class WindowActivator:
    def __init__(self, page: ft.Page) -> None:
        self._page = page

    def request_show(self) -> None:
        self._page.run_task(self.show)

    async def show(self) -> None:
        self._page.window.visible = True
        self._page.window.minimized = False
        self._page.window.focused = True
        self._page.update()
        await self._page.window.to_front()
