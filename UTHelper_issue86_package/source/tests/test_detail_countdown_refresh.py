import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.components import detail_view as detail_module
from gui.components.detail_view import DetailView


def test_open_detail_recomputes_countdown_from_current_clock(monkeypatch):
    view = SimpleNamespace(
        _current_data={"deadline": "2026-07-22T23:00:00", "type": "quiz"},
        _countdown_txt=SimpleNamespace(value="Còn 1 giờ", color="#old"),
    )
    monkeypatch.setattr(
        detail_module,
        "get_card_countdown",
        lambda *_: ("Còn 5 phút", False, "#new", 0.5),
    )

    changed = DetailView.refresh_countdown(view)

    assert changed is True
    assert view._countdown_txt.value == "Còn 5 phút"
    assert view._countdown_txt.color == "#new"


def test_controller_refreshes_detail_countdown_when_dashboard_stays_visible_behind():
    """Regression test: Detail countdown must tick even when dashboard stays visible behind it."""
    from gui.app_controller import AppController
    import threading

    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace(update_count=0)
    controller.page.update = lambda: setattr(
        controller.page, "update_count", controller.page.update_count + 1
    )

    detail_refreshed = False

    def fake_refresh_countdown():
        nonlocal detail_refreshed
        detail_refreshed = True
        return True

    controller.detail_view = SimpleNamespace(
        visible=True,
        refresh_countdown=fake_refresh_countdown,
    )
    # Dashboard is intentionally kept mounted behind detail to preserve scroll position
    controller.dashboard = SimpleNamespace(visible=True)
    controller.calendar_view = SimpleNamespace(visible=False)
    controller._cards_lock = threading.Lock()
    controller.active_cards = []

    controller._refresh_visible_countdown_views()

    assert detail_refreshed is True
    assert controller.page.update_count == 1
