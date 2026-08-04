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
        _write_secret("password", "my_secret")
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
            _write_secret("password", "")

        assert "password" not in stored
        assert stored_values == []


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
