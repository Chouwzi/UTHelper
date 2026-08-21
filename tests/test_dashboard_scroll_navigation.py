import asyncio
import os
import sys
from types import SimpleNamespace

import flet as ft


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.app_controller import AppController
from gui.view_manager import ViewManager
from core.today_schedule import ScheduleLoadStatus, TodayScheduleViewState


class _Page:
    def __init__(self):
        self.update_count = 0

    def update(self):
        self.update_count += 1


def _scroll_controller(*, offset=0.0, button_visible=False):
    controller = AppController.__new__(AppController)
    controller.page = _Page()
    controller._dashboard_scroll_offset = offset
    controller._scroll_to_top_visible = button_visible
    controller._scroll_to_top_button = SimpleNamespace(
        opacity=1.0 if button_visible else 0.0,
        offset=ft.Offset(0, 0 if button_visible else 0.3),
        scale=1.0 if button_visible else 0.84,
        ignore_interactions=not button_visible,
    )
    return controller


def test_dashboard_list_is_scrollable_by_api_and_button_is_a_positioned_overlay():
    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace()
    controller._today_schedule_state = TodayScheduleViewState(
        ScheduleLoadStatus.IDLE
    )

    controller._init_banner_and_states()

    assert controller.cards_column.build_controls_on_demand is False
    assert controller.cards_column.auto_scroll is False
    assert controller.cards_column.scroll_interval == 50
    assert controller.cards_column.on_scroll == controller._on_dashboard_scroll
    assert controller._scroll_to_top_button not in controller.cards_column.controls
    assert controller._scroll_to_top_button.right == 12
    assert controller._scroll_to_top_button.bottom == 12
    assert controller._scroll_to_top_button.opacity == 0.0
    assert controller._scroll_to_top_button.ignore_interactions is True


def test_scroll_button_uses_hysteresis_and_does_not_repaint_every_scroll_event():
    controller = _scroll_controller()

    controller._on_dashboard_scroll(SimpleNamespace(pixels=80))
    assert controller._scroll_to_top_visible is False
    assert controller.page.update_count == 0

    for pixels in range(260, 1260, 10):
        controller._on_dashboard_scroll(SimpleNamespace(pixels=pixels))

    assert controller._dashboard_scroll_offset == 1250
    assert controller._scroll_to_top_visible is True
    assert controller._scroll_to_top_button.opacity == 1.0
    assert controller._scroll_to_top_button.offset == ft.Offset(0, 0)
    assert controller._scroll_to_top_button.scale == 1.0
    assert controller._scroll_to_top_button.ignore_interactions is False
    assert controller.page.update_count == 1

    controller._on_dashboard_scroll(SimpleNamespace(pixels=0))

    assert controller._scroll_to_top_visible is False
    assert controller._scroll_to_top_button.opacity == 0.0
    assert controller._scroll_to_top_button.ignore_interactions is True
    assert controller.page.update_count == 2


def test_scroll_to_top_is_animated_then_hides_the_overlay():
    controller = _scroll_controller(offset=840, button_visible=True)
    calls = []

    async def scroll_to(**kwargs):
        calls.append(kwargs)

    controller.cards_column = SimpleNamespace(scroll_to=scroll_to)

    asyncio.run(controller._scroll_dashboard_to_top())

    assert calls == [
        {
            "offset": 0,
            "duration": 420,
            "curve": ft.AnimationCurve.EASE_OUT_CUBIC,
        }
    ]
    assert controller._dashboard_scroll_offset == 0
    assert controller._scroll_to_top_visible is False
    assert controller._scroll_to_top_button.opacity == 0.0


def test_dashboard_scroll_restore_is_exact_and_not_animated():
    controller = _scroll_controller(offset=487.5)
    calls = []

    async def scroll_to(**kwargs):
        calls.append(kwargs)

    controller.cards_column = SimpleNamespace(scroll_to=scroll_to)

    asyncio.run(controller._restore_dashboard_scroll())

    assert calls == [{"offset": 487.5, "duration": 0}]


def test_close_detail_restores_dashboard_before_transition_wait(monkeypatch):
    events = []

    class Page:
        def update(self):
            events.append("update")

    async def restore_scroll():
        events.append("restore")

    async def fake_sleep(_seconds):
        events.append("sleep")

    monkeypatch.setattr("gui.view_manager.asyncio.sleep", fake_sleep)
    dashboard = SimpleNamespace(visible=False, opacity=0.0)
    detail = SimpleNamespace(visible=True, offset=None, opacity=1.0)
    calendar = SimpleNamespace(visible=False)
    host = SimpleNamespace(controls=[])
    manager = ViewManager(
        page=Page(),
        dashboard=dashboard,
        detail_view=detail,
        settings_view=SimpleNamespace(visible=False),
        calendar_view=calendar,
        grade_overview_view=SimpleNamespace(visible=False),
        controller=SimpleNamespace(_restore_dashboard_scroll=restore_scroll),
        view_host=host,
    )

    asyncio.run(manager.close_detail(detail_from_calendar=False))

    assert events == ["update", "restore", "sleep", "update"]
    assert host.controls == [dashboard]
    assert detail.visible is False


def test_close_calendar_detail_does_not_touch_dashboard_scroll(monkeypatch):
    restore_count = 0

    async def restore_scroll():
        nonlocal restore_count
        restore_count += 1

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr("gui.view_manager.asyncio.sleep", fake_sleep)
    dashboard = SimpleNamespace(visible=False, opacity=0.0)
    detail = SimpleNamespace(visible=True, offset=None, opacity=1.0)
    calendar = SimpleNamespace(visible=False)
    manager = ViewManager(
        page=_Page(),
        dashboard=dashboard,
        detail_view=detail,
        settings_view=SimpleNamespace(visible=False),
        calendar_view=calendar,
        grade_overview_view=SimpleNamespace(visible=False),
        controller=SimpleNamespace(_restore_dashboard_scroll=restore_scroll),
        view_host=SimpleNamespace(controls=[]),
    )

    asyncio.run(manager.close_detail(detail_from_calendar=True))

    assert restore_count == 0
