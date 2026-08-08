import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import gui.app_controller as app_controller_module
from gui.app_controller import AppController


def test_render_cards_refreshes_current_filters():
    controller = AppController.__new__(AppController)
    calls = []

    controller._refresh_ui = lambda: calls.append("refresh")

    controller._render_cards()

    assert calls == ["refresh"]


class _FakePage:
    def __init__(self):
        self.update_count = 0

    def update(self):
        self.update_count += 1


class _FakeCard:
    def __init__(self, critical: bool = False):
        self._is_critical_active = critical
        self.shadow = None
        self.update_count = 0
        self.countdown_count = 0

    def update(self):
        self.update_count += 1

    def update_countdown(self):
        self.countdown_count += 1
        return True  # Simulate changed countdown


def test_pulse_tick_batches_page_update_without_per_card_updates():
    controller = AppController.__new__(AppController)
    controller.page = _FakePage()
    cards = [_FakeCard(critical=True), _FakeCard(critical=False)]

    controller._pulse_cards_once(cards, pulse_high=True)

    assert cards[0].shadow is not None
    assert [c.update_count for c in cards] == [0, 0]
    assert controller.page.update_count == 1


def test_countdown_tick_batches_page_update_without_per_card_updates():
    controller = AppController.__new__(AppController)
    controller.page = _FakePage()
    cards = [_FakeCard(), _FakeCard()]

    controller._countdown_cards_once(cards)

    assert [c.countdown_count for c in cards] == [1, 1]
    assert [c.update_count for c in cards] == [0, 0]
    assert controller.page.update_count == 1


def test_get_today_schedule_items_filters_and_sorts_today(monkeypatch):
    class _FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 8, 5)

    monkeypatch.setattr(app_controller_module, "date", _FixedDate)

    controller = AppController.__new__(AppController)
    controller.all_data = [
        {"title": "Bài muộn", "deadline": "2026-08-05T15:00:00", "urgency": "warning"},
        {"title": "Bài sớm", "deadline": "2026-08-05T09:00:00", "urgency": "critical"},
        {"title": "Bài mai", "deadline": "2026-08-06T09:00:00"},
        {"title": "Không hạn", "deadline": ""},
    ]

    items = controller._get_today_schedule_items()

    assert [item["title"] for item in items] == ["Bài sớm", "Bài muộn"]


def test_toggle_today_schedule_flips_state_and_refreshes():
    controller = AppController.__new__(AppController)
    controller._today_schedule_expanded = False
    calls = []

    controller._refresh_today_schedule_panel = lambda activities=None: calls.append(controller._today_schedule_expanded)

    controller._toggle_today_schedule()

    assert controller._today_schedule_expanded is True
    assert calls == [True]
