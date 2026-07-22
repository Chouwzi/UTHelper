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
    monkeypatch.setattr(detail_module, "get_countdown", lambda *_: ("Còn 5 phút", False))
    monkeypatch.setattr(detail_module, "get_countdown_color", lambda *_: "#new")

    changed = DetailView.refresh_countdown(view)

    assert changed is True
    assert view._countdown_txt.value == "Còn 5 phút"
    assert view._countdown_txt.color == "#new"
