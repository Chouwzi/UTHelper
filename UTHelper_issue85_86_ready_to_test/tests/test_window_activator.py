from __future__ import annotations

import asyncio

from gui.controllers.window_activator import WindowActivator


class WindowSpy:
    def __init__(self) -> None:
        self.visible = False
        self.minimized = True
        self.focused = False
        self.to_front_calls = 0
        self.direct_thread_accesses = 0

    async def to_front(self) -> None:
        self.to_front_calls += 1


class PageSpy:
    def __init__(self) -> None:
        self.window = WindowSpy()
        self.update_calls = 0
        self.scheduled: list[object] = []

    def update(self) -> None:
        self.update_calls += 1

    def run_task(self, task: object) -> None:
        self.scheduled.append(task.__func__)  # type: ignore[attr-defined]


def test_show_restores_focuses_updates_and_raises_window():
    page = PageSpy()
    activator = WindowActivator(page)  # type: ignore[arg-type]

    asyncio.run(activator.show())

    assert page.window.visible is True
    assert page.window.minimized is False
    assert page.window.focused is True
    assert page.update_calls == 1
    assert page.window.to_front_calls == 1


def test_request_show_schedules_async_work_instead_of_touching_window():
    page = PageSpy()
    WindowActivator(page).request_show()  # type: ignore[arg-type]

    assert page.scheduled == [WindowActivator.show]
    assert page.window.direct_thread_accesses == 0
