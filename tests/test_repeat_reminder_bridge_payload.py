"""Cross-platform bridge payload tests for repeating deadline reminders."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../extensions/flet_uth_background_sync/src")))

from config import settings
from platform_utils.background_sync import AndroidBackgroundBridge


def test_native_background_payload_includes_repeat_milestones(monkeypatch):
    monkeypatch.setattr(settings, "NOTIFY_MILESTONES_MINUTES", [1440, 180, 60, 30, 5])
    monkeypatch.setattr(settings, "NOTIFY_REPEAT_ENABLED", True)
    monkeypatch.setattr(settings, "NOTIFY_REPEAT_INTERVAL_HOURS", 2)

    payload = AndroidBackgroundBridge.settings_payload()

    expected = set(range(120, 1441, 120))
    assert expected.issubset(set(payload["countdown_minutes"]))
    assert len(payload["countdown_minutes"]) == len(set(payload["countdown_minutes"]))
    assert 180 in payload["countdown_minutes"]
    assert 4320 not in payload["countdown_minutes"]


def test_native_background_payload_preserves_fixed_milestones_when_repeat_disabled(monkeypatch):
    monkeypatch.setattr(settings, "NOTIFY_MILESTONES_MINUTES", [4320, 1440, 180, 60, 30, 5])
    monkeypatch.setattr(settings, "NOTIFY_REPEAT_ENABLED", False)
    monkeypatch.setattr(settings, "NOTIFY_REPEAT_INTERVAL_HOURS", 2)

    payload = AndroidBackgroundBridge.settings_payload()

    assert payload["countdown_minutes"] == [5, 30, 60, 180, 1440, 4320]
