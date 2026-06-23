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

    @patch("config._get_secure_storage", return_value=None)
    @patch("config._HAS_KEYRING", False)
    def test_load_from_file(self, mock_ss, tmp_path):
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

    @patch("config._get_secure_storage", return_value=None)
    @patch("config._HAS_KEYRING", False)
    def test_load_with_invalid_json(self, mock_ss, tmp_path):
        import config
        from config import load_settings
        test_file = tmp_path / "settings.json"
        test_file.write_text("NOT VALID JSON", encoding="utf-8")
        with patch.object(config, "CONFIG_FILE", test_file):
            s = load_settings()
        # Should return default settings (15 is the default POLL_INTERVAL)
        assert s.POLL_INTERVAL_MINUTES == 15

    @patch("config._get_secure_storage", return_value=None)
    @patch("config._HAS_KEYRING", False)
    def test_load_no_file(self, mock_ss, tmp_path):
        import config
        from config import load_settings
        nonexistent = tmp_path / "nonexistent.json"
        with patch.object(config, "CONFIG_FILE", nonexistent):
            s = load_settings()
        assert s is not None


class TestSaveSettings:
    """save_settings() tests."""

    @patch("config._get_secure_storage", return_value=None)
    @patch("config._HAS_KEYRING", False)
    @patch("config._has_any_secure_backend", return_value=False)
    def test_save_writes_json(self, mock_has_secure, mock_ss, tmp_path):
        from config import save_settings, settings
        import config
        test_file = tmp_path / "settings.json"
        with patch.object(config, "CONFIG_FILE", test_file):
            save_settings()
        assert test_file.exists()
        data = json.loads(test_file.read_text(encoding="utf-8"))
        assert "POLL_INTERVAL_MINUTES" in data

    @patch("config._get_secure_storage", return_value=None)
    @patch("config._HAS_KEYRING", False)
    @patch("config._has_any_secure_backend", return_value=False)
    def test_save_includes_secrets_without_backend(self, mock_has_secure, mock_ss, tmp_path):
        """When no secure backend, secrets are saved in JSON."""
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

    @patch("config._get_secure_storage", return_value=None)
    @patch("config._HAS_KEYRING", False)
    def test_read_secret_no_backend(self, mock_ss):
        from config import _read_secret
        val = _read_secret("password")
        assert val == ""

    @patch("config._get_secure_storage")
    @patch("config._HAS_KEYRING", False)
    def test_read_secret_from_secure_storage(self, mock_get_ss):
        mock_ss = Mock()
        mock_ss.read.return_value = "my_secret"
        mock_get_ss.return_value = mock_ss
        from config import _read_secret
        val = _read_secret("password")
        assert val == "my_secret"

    @patch("config._get_secure_storage")
    @patch("config._HAS_KEYRING", False)
    def test_read_secret_ss_returns_none(self, mock_get_ss):
        mock_ss = Mock()
        mock_ss.read.return_value = None
        mock_get_ss.return_value = mock_ss
        from config import _read_secret
        val = _read_secret("password")
        assert val == ""

    @patch("config._get_secure_storage")
    @patch("config._HAS_KEYRING", False)
    def test_write_secret_to_ss(self, mock_get_ss):
        mock_ss = Mock()
        mock_get_ss.return_value = mock_ss
        from config import _write_secret
        _write_secret("password", "my_secret")
        mock_ss.write.assert_called_once_with(key="password", value="my_secret")

    @patch("config._get_secure_storage")
    @patch("config._HAS_KEYRING", False)
    def test_write_empty_deletes(self, mock_get_ss):
        mock_ss = Mock()
        mock_get_ss.return_value = mock_ss
        from config import _write_secret
        _write_secret("password", "")
        mock_ss.delete.assert_called_once_with(key="password")


class TestHasSecureBackend:
    """_has_any_secure_backend() tests."""

    @patch("config._get_secure_storage", return_value=None)
    @patch("config._HAS_KEYRING", False)
    def test_no_backend(self, mock_ss):
        from config import _has_any_secure_backend
        assert _has_any_secure_backend() is False

    @patch("config._get_secure_storage", return_value=Mock())
    @patch("config._HAS_KEYRING", False)
    def test_has_secure_storage(self, mock_ss):
        from config import _has_any_secure_backend
        assert _has_any_secure_backend() is True

    @patch("config._get_secure_storage", return_value=None)
    @patch("config._HAS_KEYRING", True)
    def test_has_keyring(self, mock_ss):
        from config import _has_any_secure_backend
        assert _has_any_secure_backend() is True
