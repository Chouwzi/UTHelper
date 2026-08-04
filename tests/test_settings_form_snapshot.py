"""Tests for the canonical, immutable settings form state."""

import dataclasses
from types import SimpleNamespace
import traceback

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


def test_explicit_empty_notify_types_is_preserved_while_blank_uses_default():
    assert SettingsFormSnapshot.from_form_values({"notify_types": []}).notify_types == ()
    assert SettingsFormSnapshot.from_form_values({"notify_types": ""}).notify_types == (
        "assignment",
        "attendance",
        "quiz",
    )
    assert SettingsFormSnapshot.from_form_values({}).notify_types == (
        "assignment",
        "attendance",
        "quiz",
    )


@pytest.mark.parametrize("value", [1.5, "1.5", 2.25])
def test_fractional_milestones_are_rejected_without_an_exception_chain(value):
    secret = "fractional-secret"
    with pytest.raises(SettingsFormValidationError) as exc_info:
        SettingsFormSnapshot.from_form_values(
            {"notify_milestones_minutes": value, "gmail_app_password": secret}
        )

    error = exc_info.value
    rendered = "".join(traceback.format_exception(error))
    assert secret not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


def test_invalid_numeric_error_does_not_retain_or_render_secret_exception_context():
    secret = "numeric-secret"
    with pytest.raises(SettingsFormValidationError) as exc_info:
        SettingsFormSnapshot.from_form_values({"check_interval_minutes": secret})

    error = exc_info.value
    rendered = "".join(traceback.format_exception(error))
    assert secret not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


def test_muted_course_representative_is_deterministic_for_reversed_duplicates():
    left = SettingsFormSnapshot.from_form_values(
        {"notify_muted_courses": ["math", "Physics", "Math"]}
    )
    right = SettingsFormSnapshot.from_form_values(
        {"notify_muted_courses": ["Math", "Physics", "math"]}
    )

    assert left == right
    assert left.notify_muted_courses == ("Math", "Physics")


SENTINEL_SETTINGS_VALUES = {
    "THEME": "solarized_dark",
    "COLOR_CRITICAL": "#010101",
    "COLOR_WARNING": "#020202",
    "COLOR_SAFE": "#030303",
    "COLOR_QUIZ": "#040404",
    "COLOR_ASSIGNMENT": "#050505",
    "COLOR_ATTENDANCE": "#060606",
    "COLOR_OPEN": "#070707",
    "COLOR_OTHER": "#080808",
    "UTH_USERNAME": "student-01",
    "UTH_PASSWORD": "uth-secret",
    "ALWAYS_ON_TOP": True,
    "INCLUDE_SUBMITTED": False,
    "INCLUDE_GRADED": False,
    "START_WITH_WINDOWS": True,
    "START_MINIMIZED": False,
    "MINIMIZE_TO_TRAY": False,
    "AUTO_UPDATE_ENABLED": False,
    "CRASH_REPORTING_CONSENT": "disabled",
    "BACKGROUND_CHECK_ANDROID": False,
    "ENABLE_GMAIL": True,
    "GMAIL_ADDRESS": "mail-01@example.com",
    "GMAIL_APP_PASSWORD": "mail-secret",
    "ENABLE_DISCORD": True,
    "DISCORD_WEBHOOK_URL": "discord-secret",
    "ENABLE_TELEGRAM": True,
    "TELEGRAM_BOT_TOKEN": "telegram-secret",
    "TELEGRAM_CHAT_ID": "chat-01",
    "DEBUG_MODE": True,
    "CHECK_INTERVAL_MINUTES": 17,
    "FETCH_MONTHS": 3,
    "URGENCY_CRITICAL_HOURS": 11,
    "URGENCY_WARNING_HOURS": 22,
    "OPENING_SOON_HOURS": 33,
    "PREFETCH_WORKERS": 7,
    "NOTIFY_DND_ENABLE": True,
    "NOTIFY_DND_START": 4,
    "NOTIFY_DND_END": 19,
    "NOTIFY_IGNORE_SUBMITTED": False,
    "NOTIFICATION_PROFILE": "exam_week",
    "NOTIFY_TYPES": ["zeta", "alpha"],
    "NOTIFY_MILESTONES_MINUTES": [31, 7],
    "NOTIFY_MUTED_COURSES": ["Zoology", "Algebra"],
}


EXPECTED_SETTINGS_VALUES = {
    "THEME": "solarized_dark",
    "COLOR_CRITICAL": "#010101",
    "COLOR_WARNING": "#020202",
    "COLOR_SAFE": "#030303",
    "COLOR_QUIZ": "#040404",
    "COLOR_ASSIGNMENT": "#050505",
    "COLOR_ATTENDANCE": "#060606",
    "COLOR_OPEN": "#070707",
    "COLOR_OTHER": "#080808",
    "UTH_USERNAME": "student-01",
    "UTH_PASSWORD": "uth-secret",
    "ALWAYS_ON_TOP": True,
    "INCLUDE_SUBMITTED": False,
    "INCLUDE_GRADED": False,
    "START_WITH_WINDOWS": True,
    "START_MINIMIZED": False,
    "MINIMIZE_TO_TRAY": False,
    "AUTO_UPDATE_ENABLED": False,
    "CRASH_REPORTING_CONSENT": "disabled",
    "BACKGROUND_CHECK_ANDROID": False,
    "ENABLE_GMAIL": True,
    "GMAIL_ADDRESS": "mail-01@example.com",
    "GMAIL_APP_PASSWORD": "mail-secret",
    "ENABLE_DISCORD": True,
    "DISCORD_WEBHOOK_URL": "discord-secret",
    "ENABLE_TELEGRAM": True,
    "TELEGRAM_BOT_TOKEN": "telegram-secret",
    "TELEGRAM_CHAT_ID": "chat-01",
    "DEBUG_MODE": True,
    "CHECK_INTERVAL_MINUTES": 17,
    "FETCH_MONTHS": 3,
    "URGENCY_CRITICAL_HOURS": 11,
    "URGENCY_WARNING_HOURS": 22,
    "OPENING_SOON_HOURS": 33,
    "PREFETCH_WORKERS": 7,
    "NOTIFY_DND_ENABLE": True,
    "NOTIFY_DND_START": 4,
    "NOTIFY_DND_END": 19,
    "NOTIFY_IGNORE_SUBMITTED": False,
    "NOTIFICATION_PROFILE": "exam_week",
    "NOTIFY_TYPES": ["alpha", "zeta"],
    "NOTIFY_MILESTONES_MINUTES": [31, 7],
    "NOTIFY_MUTED_COURSES": ["Algebra", "Zoology"],
}


def test_all_fields_round_trip_through_the_explicit_config_mapping():
    snapshot = SettingsFormSnapshot.from_settings(
        SimpleNamespace(**SENTINEL_SETTINGS_VALUES)
    )
    mapped_values = snapshot.to_settings_values()

    assert mapped_values == EXPECTED_SETTINGS_VALUES
    assert (
        SettingsFormSnapshot.from_settings(SimpleNamespace(**mapped_values)) == snapshot
    )
