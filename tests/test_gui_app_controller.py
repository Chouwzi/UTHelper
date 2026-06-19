import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

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
