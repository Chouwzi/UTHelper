import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

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
    controller._scroll_to_top_in_progress = False
    controller._scroll_to_top_button = SimpleNamespace(
        opacity=1.0 if button_visible else 0.0,
        offset=ft.Offset(0, 0 if button_visible else 0.3),
        scale=1.0 if button_visible else 0.84,
        ignore_interactions=not button_visible,
        update=MagicMock(),
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
    assert controller.cards_column.scroll_interval == 32
    assert controller.cards_column.on_scroll == controller._on_dashboard_scroll
    assert controller._scroll_to_top_button not in controller.cards_column.controls
    assert controller._scroll_to_top_button.right == 12
    assert controller._scroll_to_top_button.bottom == 12
    assert controller._scroll_to_top_button.opacity == 0.0
    assert controller._scroll_to_top_button.ignore_interactions is True
    assert controller._scroll_to_top_button.border is None
    assert controller._scroll_to_top_button.on_click == controller._scroll_dashboard_to_top


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
    assert controller.page.update_count == 0
    assert controller._scroll_to_top_button.update.call_count == 1

    controller._on_dashboard_scroll(SimpleNamespace(pixels=0))

    assert controller._scroll_to_top_visible is False
    assert controller._scroll_to_top_button.opacity == 0.0
    assert controller._scroll_to_top_button.ignore_interactions is True
    assert controller.page.update_count == 0
    assert controller._scroll_to_top_button.update.call_count == 2


def test_manual_overscroll_at_start_hides_the_scroll_button():
    controller = _scroll_controller(offset=640, button_visible=True)

    controller._on_dashboard_scroll(
        SimpleNamespace(pixels=-12, min_scroll_extent=0)
    )

    assert controller._dashboard_scroll_offset == 0
    assert controller._scroll_to_top_visible is False
    assert controller._scroll_to_top_button.ignore_interactions is True
    assert controller.page.update_count == 0
    controller._scroll_to_top_button.update.assert_called_once_with()


def test_programmatic_scroll_events_do_not_reopen_or_repaint_button():
    controller = _scroll_controller(offset=840, button_visible=False)
    controller._scroll_to_top_in_progress = True

    controller._on_dashboard_scroll(SimpleNamespace(pixels=620))

    assert controller._dashboard_scroll_offset == 620
    assert controller._scroll_to_top_visible is False
    assert controller._scroll_to_top_button.opacity == 0.0
    controller._scroll_to_top_button.update.assert_not_called()
    assert controller.page.update_count == 0


def test_scroll_to_top_hides_locally_before_native_scroll_and_skips_page_rebuild():
    controller = _scroll_controller(offset=840, button_visible=True)
    calls = []
    events = []

    async def scroll_to(**kwargs):
        events.append("scroll-start")
        calls.append(kwargs)
        await asyncio.sleep(0)
        events.append("scroll-end")

    controller.cards_column = SimpleNamespace(scroll_to=scroll_to)
    controller._scroll_to_top_button.update = lambda: events.append("button-hide")

    asyncio.run(controller._scroll_dashboard_to_top())

    assert calls == [
        {
            "offset": 0,
            "duration": 300,
            "curve": ft.AnimationCurve.EASE_OUT_CUBIC,
        }
    ]
    assert events == ["button-hide", "scroll-start", "scroll-end"]
    assert controller.page.update_count == 0
    assert controller._dashboard_scroll_offset == 0
    assert controller._scroll_to_top_visible is False
    assert controller._scroll_to_top_button.opacity == 0.0
    assert controller._scroll_to_top_in_progress is False


def test_scroll_to_top_ignores_double_click_while_animation_is_running():
    controller = _scroll_controller(offset=840, button_visible=True)
    calls = 0

    async def scroll_to(**_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)

    controller.cards_column = SimpleNamespace(scroll_to=scroll_to)

    async def click_twice():
        await asyncio.gather(
            controller._scroll_dashboard_to_top(),
            controller._scroll_dashboard_to_top(),
        )

    asyncio.run(click_twice())

    assert calls == 1


def test_detail_keeps_dashboard_mounted_to_preserve_native_scroll(monkeypatch):
    events = []

    class Page:
        def update(self):
            events.append("update")

    async def fake_sleep(_seconds):
        events.append("sleep")

    monkeypatch.setattr("gui.view_manager.asyncio.sleep", fake_sleep)
    dashboard = SimpleNamespace(visible=False, opacity=0.0)
    detail = SimpleNamespace(
        visible=False,
        offset=None,
        opacity=1.0,
        show_loading=lambda _data: events.append("detail-loading"),
    )
    calendar = SimpleNamespace(visible=False)
    host = SimpleNamespace(controls=[])
    manager = ViewManager(
        page=Page(),
        dashboard=dashboard,
        detail_view=detail,
        settings_view=SimpleNamespace(visible=False),
        calendar_view=calendar,
        grade_overview_view=SimpleNamespace(visible=False),
        controller=SimpleNamespace(),
        view_host=host,
    )

    manager.show_detail_loading({"id": "quiz-1"})

    assert host.controls == [dashboard, detail]
    assert dashboard.visible is True
    assert detail.visible is True

    asyncio.run(manager.close_detail(detail_from_calendar=False))

    assert events == ["detail-loading", "update", "update", "sleep", "update"]
    assert host.controls == [dashboard]
    assert detail.visible is False


def test_calendar_detail_keeps_calendar_mounted_as_its_origin(monkeypatch):
    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr("gui.view_manager.asyncio.sleep", fake_sleep)
    dashboard = SimpleNamespace(visible=False, opacity=0.0)
    detail = SimpleNamespace(
        visible=False,
        offset=None,
        opacity=1.0,
        show_loading=lambda _data: None,
    )
    calendar = SimpleNamespace(visible=True)
    host = SimpleNamespace(controls=[])
    manager = ViewManager(
        page=_Page(),
        dashboard=dashboard,
        detail_view=detail,
        settings_view=SimpleNamespace(visible=False),
        calendar_view=calendar,
        grade_overview_view=SimpleNamespace(visible=False),
        controller=SimpleNamespace(),
        view_host=host,
    )

    manager.show_detail_loading({"id": "quiz-1"})

    assert host.controls == [calendar, detail]
    asyncio.run(manager.close_detail(detail_from_calendar=True))

    assert host.controls == [calendar]
