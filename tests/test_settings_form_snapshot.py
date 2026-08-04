"""Tests for the canonical, immutable settings form state."""

import dataclasses

import pytest

from gui.view_models.settings_form import (
    SettingsFormSnapshot,
    SettingsFormValidationError,
)


EXPECTED_FORM_FIELDS = {
    "theme", "color_critical", "color_warning", "color_safe", "color_quiz",
    "color_assignment", "color_attendance", "color_open", "color_other",
    "uth_username", "uth_password", "always_on_top", "include_submitted",
    "include_graded", "start_with_windows", "start_minimized", "minimize_to_tray",
    "auto_update_enabled", "crash_reporting_consent", "background_check_android",
    "enable_gmail", "gmail_address", "gmail_app_password", "enable_discord",
    "discord_webhook_url", "enable_telegram", "telegram_bot_token",
    "telegram_chat_id", "debug_mode", "check_interval_minutes", "fetch_months",
    "urgency_critical_hours", "urgency_warning_hours", "opening_soon_hours",
    "prefetch_workers", "notify_dnd_enable", "notify_dnd_start", "notify_dnd_end",
    "notify_ignore_submitted", "notification_profile", "notify_types",
    "notify_milestones_minutes", "notify_muted_courses",
}


def test_snapshot_has_exactly_every_editable_settings_control():
    assert {field.name for field in dataclasses.fields(SettingsFormSnapshot)} == EXPECTED_FORM_FIELDS


def test_snapshot_is_frozen_slotted_and_secrets_do_not_appear_in_repr():
    snapshot = SettingsFormSnapshot.from_form_values({"uth_password": "private"})

    assert hasattr(snapshot, "__slots__")
    assert "private" not in repr(snapshot)
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.theme = "ocean_teal"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ({"check_interval_minutes": "60", "fetch_months": "2"}, {"check_interval_minutes": 60, "fetch_months": 2}),
        ({"color_critical": "#ef4444"}, {"color_critical": "#EF4444"}),
        ({"notify_types": "Quiz, assignment, quiz"}, {"notify_types": ["assignment", "quiz"]}),
        ({"notify_milestones_minutes": "30, 60, 30"}, {"notify_milestones_minutes": [60, 30]}),
        ({"notify_muted_courses": " Math, physics, math "}, {"notify_muted_courses": ["physics", "Math"]}),
        ({"check_interval_minutes": "", "fetch_months": "", "notify_types": "", "notify_milestones_minutes": ""}, {}),
    ],
)
def test_equivalent_form_representations_produce_equal_snapshots(left, right):
    assert SettingsFormSnapshot.from_form_values(left) == SettingsFormSnapshot.from_form_values(right)


@pytest.mark.parametrize(
    "values",
    [
        {"check_interval_minutes": "not-a-number", "uth_password": "private"},
        {"fetch_months": 4, "telegram_bot_token": "private"},
        {"notify_dnd_start": -1, "discord_webhook_url": "private"},
        {"notify_milestones_minutes": "60, nope", "gmail_app_password": "private"},
    ],
)
def test_invalid_numeric_controls_are_field_safe(values):
    with pytest.raises(SettingsFormValidationError) as exc_info:
        SettingsFormSnapshot.from_form_values(values)

    assert "private" not in str(exc_info.value)


def test_invalid_consent_is_rejected_without_leaking_a_secret():
    with pytest.raises(SettingsFormValidationError) as exc_info:
        SettingsFormSnapshot.from_form_values(
            {"crash_reporting_consent": "perhaps", "uth_password": "private"}
        )

    assert "crash_reporting_consent" in str(exc_info.value)
    assert "private" not in str(exc_info.value)


def test_from_settings_and_explicit_uppercase_mapping_cover_all_fields():
    class PersistedSettings:
        THEME = "ocean_teal"
        COLOR_CRITICAL = "#ef4444"
        UTH_USERNAME = "student"
        UTH_PASSWORD = "private"
        ENABLE_GMAIL = True
        GMAIL_ADDRESS = "student@example.com"
        GMAIL_APP_PASSWORD = "mail-private"
        ENABLE_DISCORD = True
        DISCORD_WEBHOOK_URL = "hook-private"
        ENABLE_TELEGRAM = True
        TELEGRAM_BOT_TOKEN = "token-private"
        TELEGRAM_CHAT_ID = "123"
        DEBUG_MODE = True

    snapshot = SettingsFormSnapshot.from_settings(PersistedSettings())
    values = snapshot.to_settings_values()

    assert values["THEME"] == "ocean_teal"
    assert values["COLOR_CRITICAL"] == "#EF4444"
    assert values["UTH_PASSWORD"] == "private"
    assert values["GMAIL_APP_PASSWORD"] == "mail-private"
    assert values["DISCORD_WEBHOOK_URL"] == "hook-private"
    assert values["TELEGRAM_BOT_TOKEN"] == "token-private"
    assert set(values) == {name.upper() for name in EXPECTED_FORM_FIELDS}
