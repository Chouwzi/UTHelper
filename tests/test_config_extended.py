"""Extended tests for config.py — coverage gap filling.

Tests cover:
- save_settings() with mocked file I/O
- load_settings() from file
- _read_secret / _write_secret with mocked backends
- _has_any_secure_backend
"""
import json
import os
import sys
import pytest
from unittest.mock import patch, Mock, MagicMock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_activation_and_crash_reporting_defaults_round_trip_through_json():
    from config import Settings

    settings = Settings()
    assert settings.AUTO_UPDATE_ENABLED is True
    assert settings.CRASH_REPORTING_CONSENT == "enabled"

    restored = Settings.model_validate_json(settings.model_dump_json())
    assert restored.AUTO_UPDATE_ENABLED is True
    assert restored.CRASH_REPORTING_CONSENT == "enabled"


@pytest.mark.parametrize("consent", ["not_asked", "enabled", "disabled"])
def test_crash_reporting_consent_accepts_supported_values(consent):
    from config import Settings

    assert Settings(CRASH_REPORTING_CONSENT=consent).CRASH_REPORTING_CONSENT == consent


def test_crash_reporting_consent_rejects_unsupported_values():
    from config import Settings

    with pytest.raises(Exception):
        Settings(CRASH_REPORTING_CONSENT="maybe")


class TestLoadSettings:
    """load_settings() tests."""

    @patch("config._HAS_KEYRING", False)
    def test_load_from_file(self, tmp_path):
        import config
        from config import load_settings
        test_file = tmp_path / "settings.json"
        data = {
            "POLL_INTERVAL_MINUTES": 10,
            "SMART_POLL_ENABLED": True,
            "MOODLE_BASE_URL": "https://test.ut.edu.vn",
        }
        test_file.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(config, "CONFIG_FILE", test_file):
            s = load_settings()
        assert s.POLL_INTERVAL_MINUTES == 10
        assert s.SMART_POLL_ENABLED is True

    @patch("config._HAS_KEYRING", False)
    def test_load_with_invalid_json(self, tmp_path):
        import config
        from config import load_settings
        test_file = tmp_path / "settings.json"
        test_file.write_text("NOT VALID JSON", encoding="utf-8")
        with patch.object(config, "CONFIG_FILE", test_file):
            s = load_settings()
        assert s.POLL_INTERVAL_MINUTES == 15

    @patch("config._HAS_KEYRING", False)
    def test_load_no_file(self, tmp_path):
        import config
        from config import load_settings
        nonexistent = tmp_path / "nonexistent.json"
        with patch.object(config, "CONFIG_FILE", nonexistent):
            s = load_settings()
        assert s is not None


class TestSaveSettings:
    """save_settings() tests."""

    @patch("config._HAS_KEYRING", False)
    @patch("config._has_any_secure_backend", return_value=False)
    def test_save_writes_json(self, mock_has_secure, tmp_path):
        from config import save_settings, settings
        import config
        test_file = tmp_path / "settings.json"
        with patch.object(config, "CONFIG_FILE", test_file):
            save_settings()
        assert test_file.exists()
        data = json.loads(test_file.read_text(encoding="utf-8"))
        assert "POLL_INTERVAL_MINUTES" in data

    @patch("config._HAS_KEYRING", False)
    @patch("config._has_any_secure_backend", return_value=False)
    def test_save_includes_secrets_without_backend(self, mock_has_secure, tmp_path):
        from config import save_settings, settings
        import config
        test_file = tmp_path / "settings.json"
        settings.UTH_PASSWORD = "test_password"
        with patch.object(config, "CONFIG_FILE", test_file):
            save_settings()
        data = json.loads(test_file.read_text(encoding="utf-8"))
        assert data.get("UTH_PASSWORD") == "test_password"
        # Cleanup
        settings.UTH_PASSWORD = ""


class TestReadWriteSecret:
    """_read_secret() and _write_secret() tests."""

    @patch("config._HAS_KEYRING", False)
    def test_read_secret_no_backend(self):
        from config import _read_secret
        val = _read_secret("password")
        assert val == ""

    @patch("config.keyring.get_password")
    @patch("config._HAS_KEYRING", True)
    def test_read_secret_from_secure_storage(self, mock_get_pw):
        mock_get_pw.return_value = "my_secret"
        from config import _read_secret
        val = _read_secret("password")
        assert val == "my_secret"

    @patch("config.keyring.get_password")
    @patch("config._HAS_KEYRING", True)
    def test_read_secret_ss_returns_none(self, mock_get_pw):
        mock_get_pw.return_value = None
        from config import _read_secret
        val = _read_secret("password")
        assert val == ""

    @patch("config.keyring.set_password")
    @patch("config._HAS_KEYRING", True)
    def test_write_secret_to_ss(self, mock_set_pw):
        from config import _write_secret
        assert _write_secret("password", "my_secret") is True
        mock_set_pw.assert_called_once_with("UTHelper", "password", "my_secret")

    @patch("config._HAS_KEYRING", True)
    def test_write_empty_deletes_secret_instead_of_storing_an_empty_value(self):
        from config import _write_secret

        stored = {"password": "old-secret"}
        stored_values = []

        def delete_password(service, key):
            assert service == "UTHelper"
            stored.pop(key, None)

        def set_password(service, key, value):
            stored_values.append((service, key, value))

        with (
            patch("config.keyring.delete_password", delete_password),
            patch("config.keyring.set_password", set_password),
        ):
            assert _write_secret("password", "") is True

        assert "password" not in stored
        assert stored_values == []

    @patch("config.keyring.set_password", side_effect=RuntimeError("backend failed"))
    @patch("config._HAS_KEYRING", True)
    def test_write_secret_reports_backend_failure(self, _set_password):
        from config import _write_secret

        assert _write_secret("password", "synthetic-secret") is False


def test_save_settings_reports_json_failure(monkeypatch):
    import config

    monkeypatch.setattr(config, "_has_any_secure_backend", lambda: False)
    monkeypatch.setattr(
        "core.safe_file_io.SafeFileIO.write_json_atomic", lambda *_args, **_kwargs: False
    )

    assert config.save_settings() is False


def test_save_settings_reports_one_secure_secret_failure(monkeypatch):
    import config

    monkeypatch.setattr(config, "_has_any_secure_backend", lambda: True)
    monkeypatch.setattr(
        config,
        "_snapshot_secure_secrets",
        lambda: {key: "" for key in config._SECRET_FIELDS.values()},
    )
    monkeypatch.setattr(config.settings, "GMAIL_APP_PASSWORD", "new-secret")
    monkeypatch.setattr(
        "core.safe_file_io.SafeFileIO.write_json_atomic", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        "core.safe_file_io.SafeFileIO.read_json_safe", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        config,
        "_write_secret",
        lambda key, _value: key != "gmail_app_password",
    )

    assert config.save_settings() is False


def test_failed_secret_write_restores_prior_keyring_and_keeps_json(monkeypatch, tmp_path):
    import config

    config_file = tmp_path / "settings.json"
    config_file.write_text('{"THEME": "midnight_blue"}', encoding="utf-8")
    stored = {
        "password": "old-password",
        "gmail_app_password": "old-mail-secret",
    }

    def get_password(_service, key):
        return stored.get(key)

    def set_password(_service, key, value):
        if key == "gmail_app_password" and value == "new-mail-secret":
            raise RuntimeError("synthetic later write failure")
        stored[key] = value

    def delete_password(_service, key):
        stored.pop(key, None)

    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config, "_HAS_KEYRING", True)
    monkeypatch.setattr(config.keyring, "get_password", get_password)
    monkeypatch.setattr(config.keyring, "set_password", set_password)
    monkeypatch.setattr(config.keyring, "delete_password", delete_password)
    monkeypatch.setattr(
        config,
        "settings",
        config.Settings(
            THEME="sakura_pink",
            UTH_PASSWORD="",
            GMAIL_APP_PASSWORD="new-mail-secret",
        ),
    )

    assert config.save_settings() is False
    assert stored == {
        "password": "old-password",
        "gmail_app_password": "old-mail-secret",
    }
    assert config_file.read_text(encoding="utf-8") == '{"THEME": "midnight_blue"}'


def test_json_failure_restores_prior_keyring_values(monkeypatch):
    import config

    stored = {"password": "old-password"}

    monkeypatch.setattr(config, "_HAS_KEYRING", True)
    monkeypatch.setattr(config.keyring, "get_password", lambda _service, key: stored.get(key))
    monkeypatch.setattr(
        config.keyring,
        "set_password",
        lambda _service, key, value: stored.__setitem__(key, value),
    )
    monkeypatch.setattr(
        config.keyring,
        "delete_password",
        lambda _service, key: stored.pop(key, None),
    )
    monkeypatch.setattr(
        "core.safe_file_io.SafeFileIO.write_json_atomic",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        config,
        "settings",
        config.Settings(UTH_PASSWORD="new-password", THEME="sakura_pink"),
    )

    assert config.save_settings() is False
    assert stored == {"password": "old-password"}


def test_save_settings_reports_complete_success(monkeypatch):
    import config

    writes = []
    monkeypatch.setattr(config, "_has_any_secure_backend", lambda: True)
    monkeypatch.setattr(
        config,
        "_snapshot_secure_secrets",
        lambda: {
            key: getattr(config.settings, attr, "")
            for attr, key in config._SECRET_FIELDS.items()
        },
    )
    monkeypatch.setattr(config, "_write_secret", lambda *_args: True)
    monkeypatch.setattr(
        "core.safe_file_io.SafeFileIO.write_json_atomic",
        lambda *_args, **_kwargs: writes.append(True) or True,
    )
    assert config.save_settings() is True
    assert len(writes) == 1


def test_successful_save_notifies_subscribers_and_unsubscribe_is_exact(monkeypatch):
    import config

    calls = []
    monkeypatch.setattr(config, "_has_any_secure_backend", lambda: False)
    monkeypatch.setattr(
        "core.safe_file_io.SafeFileIO.write_json_atomic",
        lambda *_args, **_kwargs: True,
    )
    unsubscribe = config.subscribe_settings_saved(lambda: calls.append("saved"))
    try:
        assert config.save_settings() is True
        assert calls == ["saved"]
        unsubscribe()
        unsubscribe()
        assert config.save_settings() is True
        assert calls == ["saved"]
    finally:
        unsubscribe()


def test_failed_save_does_not_notify_subscribers(monkeypatch):
    import config

    calls = []
    monkeypatch.setattr(config, "_has_any_secure_backend", lambda: False)
    monkeypatch.setattr(
        "core.safe_file_io.SafeFileIO.write_json_atomic",
        lambda *_args, **_kwargs: False,
    )
    unsubscribe = config.subscribe_settings_saved(lambda: calls.append("saved"))
    try:
        assert config.save_settings() is False
        assert calls == []
    finally:
        unsubscribe()


@pytest.mark.parametrize(
    "settings_values",
    [
        {
            "UTH_USERNAME": "same-account",
            "UTH_PASSWORD": "new-password",
            "UTH_CREDENTIALS_ORIGIN": "",
            "MOODLE_BASE_URL": "https://courses.ut.edu.vn",
        },
        {
            "UTH_USERNAME": "same-account",
            "UTH_PASSWORD": "same-password",
            "UTH_CREDENTIALS_ORIGIN": "",
            "MOODLE_BASE_URL": "https://thnn.ut.edu.vn",
        },
        {
            "UTH_USERNAME": "",
            "UTH_PASSWORD": "",
            "UTH_CREDENTIALS_ORIGIN": "",
            "MOODLE_BASE_URL": "https://courses.ut.edu.vn",
        },
    ],
    ids=["credential-change", "site-change", "logout"],
)
def test_cleared_moodle_token_is_deleted_from_keyring_and_stays_cleared_after_restart(
    monkeypatch, tmp_path, settings_values
):
    """A save after identity invalidation must not resurrect the old token."""
    import config

    stored = {
        "ws_token": "old-token",
        "ws_token_origin": "https://courses.ut.edu.vn",
        "password": "old-password",
        "gmail_app_password": "unrelated-mail-secret",
    }

    def get_password(service, key):
        assert service == "UTHelper"
        return stored.get(key)

    def set_password(service, key, value):
        assert service == "UTHelper"
        assert value != ""
        stored[key] = value

    def delete_password(service, key):
        assert service == "UTHelper"
        stored.pop(key, None)

    monkeypatch.setattr(config, "_HAS_KEYRING", True)
    monkeypatch.setattr(config.keyring, "get_password", get_password)
    monkeypatch.setattr(config.keyring, "set_password", set_password)
    monkeypatch.setattr(config.keyring, "delete_password", delete_password)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    pending = config.Settings(
        **settings_values,
        MOODLE_WS_TOKEN="",
        MOODLE_WS_TOKEN_ORIGIN="",
        GMAIL_APP_PASSWORD="unrelated-mail-secret",
        THEME="sakura_pink",
    )
    monkeypatch.setattr(config, "settings", pending)

    config.save_settings()

    assert "ws_token" not in stored
    assert "ws_token_origin" not in stored
    assert stored["gmail_app_password"] == "unrelated-mail-secret"
    restarted = config.load_settings()
    assert restarted.MOODLE_WS_TOKEN == ""
    assert restarted.MOODLE_WS_TOKEN_ORIGIN == ""
    assert restarted.GMAIL_APP_PASSWORD == "unrelated-mail-secret"
    assert restarted.THEME == "sakura_pink"


class TestHasSecureBackend:
    """_has_any_secure_backend() tests."""

    @patch("config._HAS_KEYRING", False)
    def test_no_backend(self):
        from config import _has_any_secure_backend
        assert _has_any_secure_backend() is False

    @patch("config._HAS_KEYRING", True)
    def test_has_keyring(self):
        from config import _has_any_secure_backend
        assert _has_any_secure_backend() is True


class _FakeSecretBackend:
    def __init__(self, initial=None, *, fail_write=False):
        self.store = dict(initial or {})
        self.fail_write = fail_write
        self.write_calls = []

    def read_secret(self, key: str) -> str:
        return self.store.get(key, "")

    def write_secret(self, key: str, value: str) -> bool:
        if self.fail_write:
            return False
        if value:
            self.store[key] = value
        else:
            self.store.pop(key, None)
        return True

    def snapshot_secrets(self, keys):
        return {key: self.store.get(key, "") for key in keys}

    def restore_secrets(self, snapshot):
        self.store = {k: v for k, v in snapshot.items() if v}
        return True

    def write_all(self, values):
        self.write_calls.append(dict(values))
        if self.fail_write:
            return False
        self.store = {k: v for k, v in values.items() if v}
        return True


def test_is_real_android_excludes_ios(monkeypatch):
    import config

    monkeypatch.delattr(config.sys, "_ANDROID_", raising=False)
    monkeypatch.delattr(config.sys, "_IOS_", raising=False)
    monkeypatch.delattr(config.sys, "getandroidapilevel", raising=False)
    monkeypatch.setattr(config.sys, "platform", "ios")
    assert config.is_android() is True
    assert config._is_real_android() is False

    monkeypatch.setattr(config.sys, "_IOS_", True, raising=False)
    monkeypatch.setattr(config.sys, "_ANDROID_", True, raising=False)
    assert config._is_real_android() is False

    monkeypatch.delattr(config.sys, "_IOS_", raising=False)
    monkeypatch.setattr(config.sys, "platform", "linux")
    assert config._is_real_android() is True


def test_ios_ignores_keyring_until_native_keychain_backend_is_registered(
    monkeypatch,
):
    import config

    monkeypatch.delattr(config.sys, "_ANDROID_", raising=False)
    monkeypatch.setattr(config.sys, "_IOS_", True, raising=False)
    monkeypatch.setattr(config.sys, "platform", "ios")
    monkeypatch.setattr(config, "_HAS_KEYRING", True)
    config.register_secret_backend(None)

    assert config._is_real_ios() is True
    assert config._is_real_mobile() is True
    assert config._has_any_secure_backend() is False


def test_ios_queues_legacy_plaintext_secrets_and_never_saves_them_back(
    monkeypatch, tmp_path
):
    import config

    config_file = tmp_path / "settings.json"
    config_file.write_text(
        json.dumps(
            {
                "UTH_USERNAME": "2251120001",
                "UTH_PASSWORD": "legacy-ios-password",
                "THEME": "sakura_pink",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delattr(config.sys, "_ANDROID_", raising=False)
    monkeypatch.setattr(config.sys, "_IOS_", True, raising=False)
    monkeypatch.setattr(config.sys, "platform", "ios")
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    config.register_secret_backend(None)
    config.pop_pending_plaintext_migration()

    loaded = config.load_settings()
    assert loaded.UTH_PASSWORD == "legacy-ios-password"
    assert config.pop_pending_plaintext_migration() == {
        "password": "legacy-ios-password"
    }
    monkeypatch.setattr(config, "settings", loaded)
    assert config.save_settings() is False
    stored = json.loads(config_file.read_text(encoding="utf-8"))
    assert stored["UTH_PASSWORD"] == "legacy-ios-password"

    config.register_secret_backend(_FakeSecretBackend())
    try:
        assert config.save_settings() is True
        scrubbed = json.loads(config_file.read_text(encoding="utf-8"))
        assert "UTH_PASSWORD" not in scrubbed
    finally:
        config.register_secret_backend(None)


def test_android_save_settings_fails_closed_without_backend_when_secrets_non_empty(
    monkeypatch, tmp_path
):
    import config

    config_file = tmp_path / "settings.json"
    monkeypatch.setattr(config.sys, "_ANDROID_", True, raising=False)
    monkeypatch.delattr(config.sys, "_IOS_", raising=False)
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    config.register_secret_backend(None)
    monkeypatch.setattr(
        config,
        "settings",
        config.Settings(UTH_USERNAME="2251120001", UTH_PASSWORD="android-secret", THEME="sakura_pink"),
    )

    assert config.save_settings() is False
    assert not config_file.exists()


def test_android_save_settings_allows_non_secret_save_when_all_secrets_empty(
    monkeypatch, tmp_path
):
    import config

    config_file = tmp_path / "settings.json"
    monkeypatch.setattr(config.sys, "_ANDROID_", True, raising=False)
    monkeypatch.delattr(config.sys, "_IOS_", raising=False)
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    config.register_secret_backend(None)
    monkeypatch.setattr(
        config,
        "settings",
        config.Settings(UTH_USERNAME="2251120001", THEME="midnight_blue"),
    )

    assert config.save_settings() is True
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["THEME"] == "midnight_blue"
    for secret_attr in config._SECRET_FIELDS:
        assert secret_attr not in saved


def test_android_load_stashes_plaintext_migration_and_backend_save_scrubs_json(
    monkeypatch, tmp_path
):
    import config

    config_file = tmp_path / "settings.json"
    config_file.write_text(
        json.dumps(
            {
                "UTH_USERNAME": "2251120001",
                "UTH_PASSWORD": "legacy-password",
                "TELEGRAM_BOT_TOKEN": "123:legacy-tg",
                "THEME": "steel_light",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config.sys, "_ANDROID_", True, raising=False)
    monkeypatch.delattr(config.sys, "_IOS_", raising=False)
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    config.register_secret_backend(None)

    loaded = config.load_settings()
    assert loaded.UTH_PASSWORD == "legacy-password"
    assert loaded.TELEGRAM_BOT_TOKEN == "123:legacy-tg"

    pending = config.pop_pending_plaintext_migration()
    assert pending == {
        "password": "legacy-password",
        "telegram_bot_token": "123:legacy-tg",
    }
    assert config.pop_pending_plaintext_migration() == {}

    backend = _FakeSecretBackend()
    config.register_secret_backend(backend)
    monkeypatch.setattr(config, "settings", loaded)
    try:
        assert config.save_settings() is True
        assert backend.store == {
            "password": "legacy-password",
            "telegram_bot_token": "123:legacy-tg",
        }
        scrubbed = json.loads(config_file.read_text(encoding="utf-8"))
        assert scrubbed["UTH_USERNAME"] == "2251120001"
        for secret_attr in config._SECRET_FIELDS:
            assert secret_attr not in scrubbed
    finally:
        config.register_secret_backend(None)


def test_android_secret_backend_rollback_on_json_write_failure(monkeypatch, tmp_path):
    import config

    backend = _FakeSecretBackend({"password": "old-password"})
    monkeypatch.setattr(config.sys, "_ANDROID_", True, raising=False)
    monkeypatch.delattr(config.sys, "_IOS_", raising=False)
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(
        "core.safe_file_io.SafeFileIO.write_json_atomic",
        lambda *_args, **_kwargs: False,
    )
    config.register_secret_backend(backend)
    monkeypatch.setattr(
        config,
        "settings",
        config.Settings(UTH_PASSWORD="new-password", THEME="sakura_pink"),
    )
    try:
        assert config.save_settings() is False
        assert backend.store == {"password": "old-password"}
    finally:
        config.register_secret_backend(None)
