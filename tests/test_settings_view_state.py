import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gui.components.settings_view import SettingsView
from gui.app_controller import AppController
from gui.view_manager import ViewManager
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


def _make_loading_view(coordinator):
    view = _make_state_only_view()
    view._autostart_coordinator = coordinator
    view._load_generation = 0
    view._loading = False
    view._baseline_snapshot = None
    view._tiles = []
    view._test_login_status = _control("")
    view._test_login_btn = _control(text="", icon=None)
    view._test_loading_bar = _control(visible=True)
    view._unsaved_dot = _control(visible=True)
    view._save_status = _control("", color=None)
    view._autostart_status = _control("", color=None)
    view._orchestrator = SimpleNamespace(get_cached_details_snapshot=lambda: {})
    view._refresh_section_colors = Mock()
    view._update_drp_options = Mock()
    view.update = Mock()
    view._apply_snapshot_to_controls = lambda snapshot: (
        SettingsView._apply_snapshot_to_controls(view, snapshot)
    )
    view._apply_autostart_ui = lambda result: SettingsView._apply_autostart_ui(
        view, result
    )
    view._load_autostart_state = lambda generation: (
        SettingsView._load_autostart_state(view, generation)
    )
    return view


def _make_save_view(baseline, draft, coordinator=None):
    view = _make_state_only_view()
    SettingsView._apply_snapshot_to_controls(view, draft)
    view._baseline_snapshot = baseline
    view._original_theme = baseline.theme
    view._loading = False
    view._autostart_coordinator = coordinator
    view._save_status = _control("", color=None)
    view._autostart_status = _control("", color=None)
    view._unsaved_dot = _control(visible=True)
    view._page = SimpleNamespace(
        window=SimpleNamespace(always_on_top=baseline.always_on_top),
        show_dialog=Mock(),
        pop_dialog=Mock(),
        update=Mock(),
    )
    view._on_saved = Mock()
    view._on_close_cb = Mock()
    view._on_theme_preview = Mock()
    view.update = Mock()
    view._apply_snapshot_to_controls = lambda snapshot: (
        SettingsView._apply_snapshot_to_controls(view, snapshot)
    )
    view._apply_autostart_ui = lambda result: SettingsView._apply_autostart_ui(
        view, result
    )
    view._persist_snapshot_to_settings = lambda snapshot: (
        SettingsView._persist_snapshot_to_settings(view, snapshot)
    )
    view.has_changes = lambda: SettingsView.has_changes(view)
    view._discard_and_close = lambda: SettingsView._discard_and_close(view)
    async def save(event):
        return await SettingsView._save(view, event)

    view._save = save
    return view


def _changed_snapshot(baseline, *, start_with_windows=True):
    return replace(
        baseline,
        theme="solarized_dark",
        color_critical="#010101",
        color_warning="#020202",
        color_safe="#030303",
        color_quiz="#040404",
        color_assignment="#050505",
        color_attendance="#060606",
        color_open="#070707",
        color_other="#080808",
        uth_username="student-01",
        uth_password="uth-secret",
        always_on_top=not baseline.always_on_top,
        include_submitted=not baseline.include_submitted,
        include_graded=not baseline.include_graded,
        start_with_windows=start_with_windows,
        start_minimized=not baseline.start_minimized,
        minimize_to_tray=not baseline.minimize_to_tray,
        auto_update_enabled=not baseline.auto_update_enabled,
        crash_reporting_consent="enabled",
        background_check_android=not baseline.background_check_android,
        enable_gmail=True,
        gmail_address="mail-01@example.com",
        gmail_app_password="mail-secret",
        enable_discord=True,
        discord_webhook_url="discord-secret",
        enable_telegram=True,
        telegram_bot_token="telegram-secret",
        telegram_chat_id="chat-01",
        debug_mode=True,
        check_interval_minutes=17,
        fetch_months=3,
        urgency_critical_hours=11,
        urgency_warning_hours=22,
        opening_soon_hours=33,
        prefetch_workers=7,
        notify_dnd_enable=True,
        notify_dnd_start=4,
        notify_dnd_end=19,
        notify_ignore_submitted=False,
        notification_profile="exam_week",
        notify_types=("alpha", "zeta"),
        notify_milestones_minutes=(31, 7),
        notify_muted_courses=("Algebra", "Zoology"),
    )


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


def test_settings_load_is_awaited_transaction_with_clean_baseline(monkeypatch):
    from gui.controllers.autostart_settings import AutostartUiState

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        class Coordinator:
            async def load(self):
                started.set()
                await release.wait()
                return AutostartUiState(False, True, True, "Đã đọc Windows.")

        view = _make_loading_view(Coordinator())
        task = asyncio.create_task(SettingsView.load_current_settings(view))
        await started.wait()

        assert view._load_generation == 1
        assert view._loading is True
        assert SettingsView.has_changes(view) is False
        assert view._baseline_snapshot is None

        release.set()
        await task

        assert view._loading is False
        assert view._baseline_snapshot == view._capture_form_snapshot()
        assert view._baseline_snapshot.start_with_windows is False
        assert SettingsView.has_changes(view) is False
        assert view._autostart_status.value == "Đã đọc Windows."

    monkeypatch.setattr("gui.components.settings_view.settings.START_WITH_WINDOWS", True)
    asyncio.run(scenario())


def test_late_settings_load_generation_cannot_overwrite_newer_result(monkeypatch):
    from gui.controllers.autostart_settings import AutostartUiState

    async def scenario():
        futures = [
            asyncio.get_running_loop().create_future(),
            asyncio.get_running_loop().create_future(),
        ]
        calls = 0

        class Coordinator:
            async def load(self):
                nonlocal calls
                future = futures[calls]
                calls += 1
                return await future

        view = _make_loading_view(Coordinator())
        first = asyncio.create_task(SettingsView.load_current_settings(view))
        await asyncio.sleep(0)
        second = asyncio.create_task(SettingsView.load_current_settings(view))
        await asyncio.sleep(0)

        futures[1].set_result(
            AutostartUiState(False, True, True, "Thế hệ mới.")
        )
        await second
        final_baseline = view._baseline_snapshot

        futures[0].set_result(AutostartUiState(True, True, True, "Thế hệ cũ."))
        await first

        assert view._load_generation == 2
        assert view._sw_start_with_windows.value is False
        assert view._baseline_snapshot is final_baseline
        assert view._baseline_snapshot.start_with_windows is False
        assert view._autostart_status.value == "Thế hệ mới."
        assert view._loading is False

    monkeypatch.setattr("gui.components.settings_view.settings.START_WITH_WINDOWS", True)
    asyncio.run(scenario())
    assert SettingsFormSnapshot.from_settings(
        __import__("gui.components.settings_view", fromlist=["settings"]).settings
    ).start_with_windows is True


def test_cancel_pending_load_invalidates_generation_without_dirty_prompt():
    view = SimpleNamespace(_load_generation=7, _loading=True, _baseline_snapshot=None)

    SettingsView.cancel_pending_load(view)

    assert view._load_generation == 8
    assert view._loading is False
    assert SettingsView.has_changes(view) is False


def test_successful_save_persists_every_field_and_rebaselines(monkeypatch):
    import gui.components.settings_view as settings_view_module
    from gui.controllers.autostart_settings import AutostartUiState

    baseline = SettingsFormSnapshot.from_form_values({})
    draft = _changed_snapshot(baseline)
    fake_settings = SimpleNamespace(
        **baseline.to_settings_values(),
        UTH_CREDENTIALS_ORIGIN="",
        MOODLE_WS_TOKEN="",
        MOODLE_WS_TOKEN_ORIGIN="",
    )
    save = Mock(return_value=True)

    class Coordinator:
        async def change(self, enabled):
            return AutostartUiState(enabled, True, True, "Đã cập nhật Windows.")

    view = _make_save_view(baseline, draft, Coordinator())
    monkeypatch.setattr(settings_view_module, "settings", fake_settings)
    monkeypatch.setattr(settings_view_module, "save_settings", save)
    monkeypatch.setattr(settings_view_module._pu, "IS_MOBILE", False)

    assert asyncio.run(SettingsView._save(view, None)) is True
    assert SettingsFormSnapshot.from_settings(fake_settings) == draft
    assert view._baseline_snapshot == draft
    assert view._capture_form_snapshot() == draft
    assert SettingsView.has_changes(view) is False
    assert view._original_theme == draft.theme
    assert view._page.window.always_on_top == draft.always_on_top
    save.assert_called_once_with()
    view._on_saved.assert_called_once_with()


def test_rejected_autostart_still_persists_and_rebaselines_unrelated_changes(
    monkeypatch,
):
    import gui.components.settings_view as settings_view_module
    from gui.controllers.autostart_settings import AutostartUiState

    baseline = SettingsFormSnapshot.from_form_values({"start_with_windows": False})
    requested = replace(baseline, theme="solarized_dark", start_with_windows=True)
    fake_settings = SimpleNamespace(
        **baseline.to_settings_values(),
        UTH_CREDENTIALS_ORIGIN="",
        MOODLE_WS_TOKEN="",
        MOODLE_WS_TOKEN_ORIGIN="",
    )
    save = Mock(return_value=True)

    class Coordinator:
        async def change(self, enabled):
            assert enabled is True
            return AutostartUiState(
                False,
                False,
                False,
                "Windows đã từ chối thay đổi.",
            )

    view = _make_save_view(baseline, requested, Coordinator())
    monkeypatch.setattr(settings_view_module, "settings", fake_settings)
    monkeypatch.setattr(settings_view_module, "save_settings", save)
    monkeypatch.setattr(settings_view_module._pu, "IS_MOBILE", False)

    assert asyncio.run(SettingsView._save(view, None)) is False
    persisted = replace(requested, start_with_windows=False)
    assert SettingsFormSnapshot.from_settings(fake_settings) == persisted
    assert view._capture_form_snapshot() == persisted
    assert view._baseline_snapshot == persisted
    assert SettingsView.has_changes(view) is False
    assert "Windows đã từ chối" in view._save_status.value
    assert view._autostart_status.value == "Windows đã từ chối thay đổi."
    save.assert_called_once_with()

    asyncio.run(SettingsView._handle_back(view, None))
    view._page.show_dialog.assert_not_called()
    view._on_close_cb.assert_called_once_with()


def test_persistence_failure_keeps_old_baseline_and_settings_open(monkeypatch):
    import gui.components.settings_view as settings_view_module

    baseline = SettingsFormSnapshot.from_form_values({})
    draft = replace(baseline, theme="solarized_dark")
    fake_settings = SimpleNamespace(
        **baseline.to_settings_values(),
        UTH_CREDENTIALS_ORIGIN="",
        MOODLE_WS_TOKEN="",
        MOODLE_WS_TOKEN_ORIGIN="",
    )
    view = _make_save_view(baseline, draft)
    monkeypatch.setattr(settings_view_module, "settings", fake_settings)
    monkeypatch.setattr(settings_view_module, "save_settings", Mock(return_value=False))
    monkeypatch.setattr(settings_view_module._pu, "IS_MOBILE", False)

    assert asyncio.run(SettingsView._save_and_close_if_valid(view, None)) is False
    assert view._baseline_snapshot == baseline
    assert SettingsFormSnapshot.from_settings(fake_settings) == baseline
    assert SettingsView.has_changes(view) is True
    assert "Không thể lưu" in view._save_status.value
    view._on_close_cb.assert_not_called()


def test_discard_restores_entire_baseline_and_theme_without_logging_secrets(
    monkeypatch,
    caplog,
):
    import gui.components.settings_view as settings_view_module

    baseline = _changed_snapshot(
        SettingsFormSnapshot.from_form_values({}),
        start_with_windows=False,
    )
    draft = SettingsFormSnapshot.from_form_values(
        {"theme": "midnight_blue", "start_with_windows": True}
    )
    view = _make_save_view(baseline, draft)
    applied_theme = Mock()
    applied_page_theme = Mock()
    state_at_close = []
    view._on_close_cb = lambda: state_at_close.append(view._capture_form_snapshot())
    monkeypatch.setattr(settings_view_module, "apply_theme", applied_theme)
    monkeypatch.setattr("gui.core.theme.set_page_theme", applied_page_theme)

    with caplog.at_level("DEBUG"):
        SettingsView._discard_and_close(view)

    assert state_at_close == [baseline]
    assert view._capture_form_snapshot() == baseline
    assert SettingsView.has_changes(view) is False
    applied_theme.assert_called_once_with(baseline.theme)
    applied_page_theme.assert_called_once_with(view._page)
    view._on_theme_preview.assert_called_once_with()
    for secret in (
        baseline.uth_password,
        baseline.gmail_app_password,
        baseline.discord_webhook_url,
        baseline.telegram_bot_token,
    ):
        assert secret not in caplog.text


@pytest.mark.parametrize(
    ("control_name", "invalid_value"),
    [
        ("_interval_field", "invalid-number-private-marker"),
        ("_c_tb_critical", "invalid-color-private-marker"),
    ],
)
def test_back_with_invalid_form_still_allows_safe_discard(
    monkeypatch,
    caplog,
    control_name,
    invalid_value,
):
    import gui.components.settings_view as settings_view_module

    baseline = _changed_snapshot(
        SettingsFormSnapshot.from_form_values({}),
        start_with_windows=False,
    )
    view = _make_save_view(baseline, baseline)
    getattr(view, control_name).value = invalid_value
    dialogs = []
    view._page.show_dialog = dialogs.append
    monkeypatch.setattr(settings_view_module, "apply_theme", Mock())
    monkeypatch.setattr("gui.core.theme.set_page_theme", Mock())

    with caplog.at_level("DEBUG"):
        asyncio.run(SettingsView._handle_back(view, None))
        assert len(dialogs) == 1
        dialogs[0].actions[1].on_click(None)

    assert view._capture_form_snapshot() == baseline
    view._on_close_cb.assert_called_once_with()
    assert invalid_value not in caplog.text
    for secret in (
        baseline.uth_password,
        baseline.gmail_app_password,
        baseline.discord_webhook_url,
        baseline.telegram_bot_token,
    ):
        assert secret not in caplog.text


def _view_manager(settings_view):
    page = SimpleNamespace(update=Mock())
    dashboard = _control(visible=True, opacity=1.0)
    detail = _control(visible=False)
    calendar = _control(visible=False)
    grades = _control(visible=False)
    controller = SimpleNamespace()
    manager = ViewManager(
        page,
        dashboard,
        detail,
        settings_view,
        calendar,
        grades,
        controller,
    )
    return manager, dashboard


def test_view_manager_awaits_settings_load_before_showing_view():
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        settings_view = _control(
            visible=False,
            offset=None,
            opacity=0.0,
            cancel_pending_load=Mock(),
        )

        async def load():
            started.set()
            await release.wait()

        settings_view.load_current_settings = load
        manager, dashboard = _view_manager(settings_view)

        pending = asyncio.create_task(manager.show_settings())
        await started.wait()
        assert dashboard.visible is True
        assert settings_view.visible is False

        release.set()
        await pending
        assert dashboard.visible is False
        assert settings_view.visible is True

    asyncio.run(scenario())


def test_closing_settings_cancels_pending_show_and_prevents_late_reveal(monkeypatch):
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        settings_view = _control(
            visible=False,
            offset=None,
            opacity=0.0,
            cancel_pending_load=Mock(),
        )

        async def load():
            started.set()
            await release.wait()

        settings_view.load_current_settings = load
        manager, dashboard = _view_manager(settings_view)
        monkeypatch.setattr("gui.view_manager.asyncio.sleep", AsyncMock())

        pending = asyncio.create_task(manager.show_settings())
        await started.wait()
        await manager.close_settings()
        release.set()
        await pending

        settings_view.cancel_pending_load.assert_called_once_with()
        assert dashboard.visible is True
        assert settings_view.visible is False

    asyncio.run(scenario())


def test_disconnect_invalidation_cancels_pending_show_without_page_update():
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        settings_view = _control(
            visible=False,
            offset=None,
            opacity=0.0,
            cancel_pending_load=Mock(),
        )

        async def load():
            started.set()
            await release.wait()

        settings_view.load_current_settings = load
        manager, dashboard = _view_manager(settings_view)

        pending = asyncio.create_task(manager.show_settings())
        await started.wait()
        manager.cancel_pending_settings_navigation()
        release.set()
        await pending

        settings_view.cancel_pending_load.assert_called_once_with()
        assert dashboard.visible is True
        assert settings_view.visible is False
        manager.page.update.assert_not_called()

    asyncio.run(scenario())


def test_app_controller_awaits_view_manager_settings_initialization():
    async def scenario():
        manager = SimpleNamespace(show_settings=AsyncMock())
        controller = SimpleNamespace(view_manager=manager)

        await AppController._show_settings(controller)

        manager.show_settings.assert_awaited_once_with()

    asyncio.run(scenario())
