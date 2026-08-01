import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from platform_utils.background_sync import (
    AndroidBackgroundBridge,
    create_android_background_bridge,
)


def test_zero_interval_disables_android_periodic_worker(monkeypatch):
    monkeypatch.setattr("config.settings.BACKGROUND_CHECK_ANDROID", True)
    monkeypatch.setattr("config.settings.CHECK_INTERVAL_MINUTES", 0)

    payload = AndroidBackgroundBridge.settings_payload()

    assert payload["enabled"] is False
    assert payload["interval_minutes"] == 0


def test_native_notification_bridge_is_available_on_ios(monkeypatch):
    import flet_uth_background_sync
    import platform_utils

    class FakeService:
        pass

    class FakePage:
        def __init__(self):
            self.services = []

    monkeypatch.setattr(platform_utils, "IS_ANDROID", False)
    monkeypatch.setattr(platform_utils, "IS_IOS", True)
    monkeypatch.setattr(flet_uth_background_sync, "AndroidBackgroundSync", FakeService)

    page = FakePage()
    bridge = create_android_background_bridge(page)

    assert bridge is not None
    assert bridge.available is True
    assert len(page.services) == 1
