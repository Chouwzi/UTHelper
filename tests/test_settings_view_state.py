from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gui.components.settings_view import SettingsView
from gui.view_models.settings_form import SettingsFormSnapshot


def _control(value=None, **values):
    return SimpleNamespace(value=value, **values)


def _make_state_only_view():
    view = SimpleNamespace(
        _selected_theme="midnight_blue",
        _c_tb_critical=_control(),
        _c_tb_warning=_control(),
        _c_tb_safe=_control(),
        _c_tb_quiz=_control(),
        _c_tb_ass=_control(),
        _c_tb_att=_control(),
        _c_tb_open=_control(),
        _c_tb_other=_control(),
        _username_field=_control(),
        _password_field=_control(),
        _sw_always_on_top=_control(),
        _sw_submitted=_control(),
        _sw_graded=_control(),
        _sw_start_with_windows=_control(),
        _sw_start_minimized=_control(disabled=False),
        _sw_minimize_to_tray=_control(),
        _sw_auto_update=_control(),
        _dd_crash_reporting_consent=_control(),
        _sw_bg_check=_control(),
        _sw_email=_control(),
        _gmail_addr_field=_control(visible=False),
        _gmail_pw_field=_control(visible=False),
        _sw_discord=_control(),
        _discord_wh_field=_control(visible=False),
        _sw_telegram=_control(),
        _tel_token_field=_control(visible=False),
        _tel_chat_field=_control(visible=False),
        _sw_debug=_control(),
        _interval_field=_control(),
        _fetch_months_field=_control(),
        _critical_hours_field=_control(),
        _warning_hours_field=_control(),
        _opening_soon_hours_field=_control(),
        _workers_field=_control(),
        _sw_dnd_enable=_control(),
        _dnd_start_field=_control(),
        _dnd_end_field=_control(),
        _sw_ignore_sub=_control(),
        _notify_type_checks={
            key: _control(False)
            for key in ("quiz", "assignment", "attendance", "forum", "resource", "choice", "zeta", "alpha")
        },
        _milestones_field=_control(),
        _milestone_chips={31: _control(selected=False), 7: _control(selected=False)},
        _milestone_summary=_control(),
        _muted_courses_field=_control(),
        _profile_cards={
            "quiet": _control(border=None, bgcolor=None),
            "balanced": _control(border=None, bgcolor=None),
            "exam_week": _control(border=None, bgcolor=None),
        },
        _test_panel=_control(visible=False),
        _safe_update=lambda *_controls: None,
    )
    view._sync_autostart_dependency = Mock()
    view._toggle_bg_check_ui = Mock()
    view._toggle_integration_ui = Mock()
    view._toggle_telegram_ui = Mock()
    view._toggle_debug_ui = Mock()
    view._rebuild_theme_cards = Mock()
    view._update_profile_summary = Mock()
    view._update_dnd_summary = Mock()
    view._capture_form_snapshot = lambda: SettingsView._capture_form_snapshot(view)
    return view


@pytest.mark.parametrize("consent", ["not_asked", "enabled", "disabled"])
def test_complete_settings_snapshot_round_trips_through_every_control(consent):
    snapshot = SettingsFormSnapshot.from_form_values(
        {
            "theme": "solarized_dark",
            "color_critical": "#010101",
            "color_warning": "#020202",
            "color_safe": "#030303",
            "color_quiz": "#040404",
            "color_assignment": "#050505",
            "color_attendance": "#060606",
            "color_open": "#070707",
            "color_other": "#080808",
            "uth_username": "student-01",
            "uth_password": "uth-secret",
            "always_on_top": True,
            "include_submitted": False,
            "include_graded": False,
            "start_with_windows": True,
            "start_minimized": False,
            "minimize_to_tray": False,
            "auto_update_enabled": False,
            "crash_reporting_consent": consent,
            "background_check_android": False,
            "enable_gmail": True,
            "gmail_address": "mail-01@example.com",
            "gmail_app_password": "mail-secret",
            "enable_discord": True,
            "discord_webhook_url": "discord-secret",
            "enable_telegram": True,
            "telegram_bot_token": "telegram-secret",
            "telegram_chat_id": "chat-01",
            "debug_mode": True,
            "check_interval_minutes": 17,
            "fetch_months": 3,
            "urgency_critical_hours": 11,
            "urgency_warning_hours": 22,
            "opening_soon_hours": 33,
            "prefetch_workers": 7,
            "notify_dnd_enable": True,
            "notify_dnd_start": 4,
            "notify_dnd_end": 19,
            "notify_ignore_submitted": False,
            "notification_profile": "exam_week",
            "notify_types": ["zeta", "alpha"],
            "notify_milestones_minutes": [31, 7],
            "notify_muted_courses": ["Zoology", "Algebra"],
        }
    )
    view = _make_state_only_view()

    SettingsView._apply_snapshot_to_controls(view, snapshot)
    captured = SettingsView._capture_form_snapshot(view)

    assert captured == snapshot
    assert view._dd_crash_reporting_consent.value == consent
    assert view._sw_auto_update.value is False
    assert view._sw_debug.value is True
    assert view._gmail_pw_field.value == "mail-secret"
    assert view._discord_wh_field.value == "discord-secret"
    assert view._tel_token_field.value == "telegram-secret"
    view._sync_autostart_dependency.assert_called_once_with()
    view._toggle_integration_ui.assert_called_once_with()
    view._toggle_telegram_ui.assert_called_once_with()
    view._toggle_debug_ui.assert_called_once_with()


def test_has_changes_compares_against_transactional_load_baseline():
    baseline = SettingsFormSnapshot.from_form_values({"crash_reporting_consent": "not_asked"})
    view = _make_state_only_view()
    SettingsView._apply_snapshot_to_controls(view, baseline)
    view._loading = False
    view._baseline_snapshot = baseline

    assert SettingsView.has_changes(view) is False

    view._dd_crash_reporting_consent.value = "enabled"

    assert SettingsView.has_changes(view) is True


def test_persist_snapshot_rolls_back_every_setting_and_provenance_on_failure(monkeypatch):
    import gui.components.settings_view as settings_view_module

    old = SettingsFormSnapshot.from_settings(settings_view_module.settings)
    old_provenance = (
        settings_view_module.settings.UTH_CREDENTIALS_ORIGIN,
        settings_view_module.settings.MOODLE_WS_TOKEN,
        settings_view_module.settings.MOODLE_WS_TOKEN_ORIGIN,
    )
    pending = SettingsFormSnapshot.from_form_values(
        {
            **old.to_settings_values(),
            "uth_username": old.uth_username + "-changed",
            "uth_password": "changed-secret",
            "auto_update_enabled": not old.auto_update_enabled,
            "crash_reporting_consent": "disabled",
        }
    )
    monkeypatch.setattr(settings_view_module, "save_settings", lambda: False)

    assert SettingsView._persist_snapshot_to_settings(SimpleNamespace(), pending) is False
    assert SettingsFormSnapshot.from_settings(settings_view_module.settings) == old
    assert (
        settings_view_module.settings.UTH_CREDENTIALS_ORIGIN,
        settings_view_module.settings.MOODLE_WS_TOKEN,
        settings_view_module.settings.MOODLE_WS_TOKEN_ORIGIN,
    ) == old_provenance
