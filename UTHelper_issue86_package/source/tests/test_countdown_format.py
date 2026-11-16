from datetime import datetime
from types import SimpleNamespace

from gui.core import utils


def test_countdown_above_one_day_keeps_minutes_visible(monkeypatch):
    now = datetime(2026, 8, 26, 22, 10, 0)
    monkeypatch.setattr(utils, "datetime", SimpleNamespace(now=lambda: now))

    countdown, overdue = utils.get_countdown("2026-08-28 07:34:00", "quiz")

    assert (countdown, overdue) == ("Còn 1 ngày 9 giờ 24 phút", False)
