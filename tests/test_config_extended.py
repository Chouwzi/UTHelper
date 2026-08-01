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
    def test_write_empty_does_nothing(self):
        from config import _write_secret
        # _write_secret doesn't delete anymore when value is empty, it just returns if no value
        with patch("config.keyring.set_password") as mock_set_pw:
            _write_secret("password", "")
            mock_set_pw.assert_not_called()


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
