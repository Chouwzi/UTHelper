import os
import sys
import asyncio

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from platform_utils.background_sync import (
    AndroidBackgroundBridge,
    create_android_background_bridge,
)
from gui.app_controller import AppController


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


class _CredentialService:
    def __init__(self):
        self.credentials = []
        self.logout_calls = 0
        self.configure_calls = []

    async def set_credentials(self, base_url, token):
        self.credentials.append((base_url, token))

    async def logout(self):
        self.logout_calls += 1

    async def configure(self, payload):
        self.configure_calls.append(payload)
        return {"enabled": payload["enabled"]}


@pytest.mark.parametrize(
    ("base_url", "token_origin"),
    (
        ("https://thnn.ut.edu.vn", ""),
        ("https://thnn.ut.edu.vn", "https://courses.ut.edu.vn"),
        ("https://courses.ut.edu.vn", "https://evil.example"),
    ),
)
def test_background_bridge_never_hands_off_unbound_or_cross_site_token(
    monkeypatch, base_url, token_origin
):
    service = _CredentialService()
    bridge = AndroidBackgroundBridge.__new__(AndroidBackgroundBridge)
    bridge.service = service
    monkeypatch.setattr("config.settings.MOODLE_BASE_URL", base_url)

    asyncio.run(bridge.configure("secret-token", token_origin=token_origin))

    assert service.credentials == []
    assert service.logout_calls == 1
    assert len(service.configure_calls) == 1


def test_background_bridge_hands_off_only_matching_explicit_site_token(monkeypatch):
    service = _CredentialService()
    bridge = AndroidBackgroundBridge.__new__(AndroidBackgroundBridge)
    bridge.service = service
    monkeypatch.setattr("config.settings.MOODLE_BASE_URL", "https://thnn.ut.edu.vn")

    asyncio.run(
        bridge.configure(
            "thnn-token",
            token_origin="https://thnn.ut.edu.vn",
        )
    )

    assert service.credentials == [("https://thnn.ut.edu.vn", "thnn-token")]
    assert service.logout_calls == 0


def test_install_update_forwards_identity_version_signer_and_cancel():
    class FakeService:
        def __init__(self):
            self.calls = []

        async def install_update(self, *args):
            self.calls.append(("install", args))
            return {"status": "verified"}

        async def cancel_update(self):
            self.calls.append(("cancel", ()))

    bridge = AndroidBackgroundBridge.__new__(AndroidBackgroundBridge)
    bridge.service = FakeService()

    result = asyncio.run(
        bridge.install_update(
            "https://github.com/Chouwzi/UTHelper/releases/download/"
            "v2.2.0/UTHelper-2.2.0.apk",
            "ab" * 32,
            123,
            "com.uthelper.uthelper",
            2_002_000,
            "cd" * 32,
        )
    )
    asyncio.run(bridge.cancel_update())

    assert result == {"status": "verified"}
    assert bridge.service.calls == [
        (
            "install",
            (
                "https://github.com/Chouwzi/UTHelper/releases/download/"
                "v2.2.0/UTHelper-2.2.0.apk",
                "ab" * 32,
                123,
                "com.uthelper.uthelper",
                2_002_000,
                "cd" * 32,
            ),
        ),
        ("cancel", ()),
    ]


@pytest.mark.parametrize(
    ("base_url", "client_origin", "token_origin", "expected"),
    (
        (
            "https://thnn.ut.edu.vn",
            "https://courses.ut.edu.vn",
            "https://thnn.ut.edu.vn",
            ("", ""),
        ),
        (
            "https://courses.ut.edu.vn",
            "https://courses.ut.edu.vn",
            "https://evil.example",
            ("", ""),
        ),
        (
            "https://thnn.ut.edu.vn",
            "https://thnn.ut.edu.vn",
            "https://thnn.ut.edu.vn",
            ("thnn-token", "https://thnn.ut.edu.vn"),
        ),
    ),
)
def test_app_controller_only_exposes_token_with_exact_client_and_issuer_provenance(
    monkeypatch, base_url, client_origin, token_origin, expected
):
    controller = AppController.__new__(AppController)
    controller.orchestrator = type(
        "Orchestrator",
        (), {"client": type("Client", (), {"moodle_site_origin": client_origin})()},
    )()
    monkeypatch.setattr("config.settings.MOODLE_BASE_URL", base_url)
    monkeypatch.setattr("config.settings.MOODLE_WS_TOKEN", "thnn-token")
    monkeypatch.setattr("config.settings.MOODLE_WS_TOKEN_ORIGIN", token_origin)

    assert controller._native_background_credentials() == expected
