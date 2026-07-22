import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from platform_utils.background_sync import AndroidBackgroundBridge


def test_zero_interval_disables_android_periodic_worker(monkeypatch):
    monkeypatch.setattr("config.settings.BACKGROUND_CHECK_ANDROID", True)
    monkeypatch.setattr("config.settings.CHECK_INTERVAL_MINUTES", 0)

    payload = AndroidBackgroundBridge.settings_payload()

    assert payload["enabled"] is False
    assert payload["interval_minutes"] == 0
