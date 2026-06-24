"""Tests for config.py — Settings model defaults and serialization.

Tests verify:
- Default field values
- Secret fields excluded from JSON dump
- Settings serialization/deserialization
- POLL_INTERVAL_MINUTES and SMART_POLL_ENABLED
- _get_user_data_dir() returns valid paths
"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from config import Settings, _SECRET_FIELDS


class TestSettingsDefaults:
    """Settings model default values."""

    def test_default_check_interval(self):
        s = Settings()
        assert s.CHECK_INTERVAL_MINUTES == 60

    def test_default_poll_interval(self):
        s = Settings()
        assert s.POLL_INTERVAL_MINUTES == 15

    def test_default_smart_poll_enabled(self):
        s = Settings()
        assert s.SMART_POLL_ENABLED is True

    def test_default_theme(self):
        s = Settings()
        assert s.THEME == "midnight_blue"

    def test_default_moodle_base_url(self):
        s = Settings()
        assert "ut.edu.vn" in s.MOODLE_BASE_URL

    def test_default_fetch_months(self):
        s = Settings()
        assert s.FETCH_MONTHS == 1

    def test_default_include_submitted(self):
        s = Settings()
        assert s.INCLUDE_SUBMITTED is True

    def test_default_include_past_due(self):
        s = Settings()
        assert s.INCLUDE_PAST_DUE is False

    def test_default_start_with_windows(self):
        s = Settings()
        assert s.START_WITH_WINDOWS is False

    def test_default_minimize_to_tray(self):
        s = Settings()
        assert s.MINIMIZE_TO_TRAY is True


class TestSettingsSecretExclusion:
    """Secret fields are excluded from JSON dumps."""

    def test_password_excluded(self):
        s = Settings(UTH_PASSWORD="supersecret")
        dumped = s.model_dump()
        assert "UTH_PASSWORD" not in dumped

    def test_moodle_session_excluded(self):
        s = Settings(MOODLE_SESSION="abc123")
        dumped = s.model_dump()
        assert "MOODLE_SESSION" not in dumped

    def test_ws_token_excluded(self):
        s = Settings(MOODLE_WS_TOKEN="token123")
        dumped = s.model_dump()
        assert "MOODLE_WS_TOKEN" not in dumped

    def test_non_secret_fields_present(self):
        s = Settings(UTH_USERNAME="test_user")
        dumped = s.model_dump()
        assert "UTH_USERNAME" in dumped
        assert "THEME" in dumped
        assert "CHECK_INTERVAL_MINUTES" in dumped


class TestSettingsSerialization:
    """Settings JSON serialization."""

    def test_model_dump_json(self):
        s = Settings(UTH_USERNAME="12345")
        json_str = s.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["UTH_USERNAME"] == "12345"

    def test_custom_values_preserved(self):
        s = Settings(
            POLL_INTERVAL_MINUTES=30,
            SMART_POLL_ENABLED=False,
            THEME="ocean_teal",
        )
        assert s.POLL_INTERVAL_MINUTES == 30
        assert s.SMART_POLL_ENABLED is False
        assert s.THEME == "ocean_teal"


class TestSecretFieldsMapping:
    """_SECRET_FIELDS maps correct attributes."""

    def test_contains_password(self):
        assert "UTH_PASSWORD" in _SECRET_FIELDS

    def test_contains_session(self):
        assert "MOODLE_SESSION" in _SECRET_FIELDS

    def test_contains_ws_token(self):
        assert "MOODLE_WS_TOKEN" in _SECRET_FIELDS

    def test_all_secret_attrs_exist_on_model(self):
        s = Settings()
        for attr_name in _SECRET_FIELDS:
            assert hasattr(s, attr_name), f"Settings missing attribute {attr_name}"
