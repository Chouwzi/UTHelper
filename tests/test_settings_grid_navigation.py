import flet as ft
from unittest.mock import Mock
from gui.components.settings_view import SettingsView


def _make_dummy_page():
    return Mock(spec=ft.Page, window=Mock(), theme_mode="dark", on_route_change=None)


def test_hub_view_renders_eight_category_cards():
    page = _make_dummy_page()
    on_close = Mock()
    view = SettingsView(page=page, orchestrator=None, on_close=on_close)

    assert len(view._category_defs) == 8
    expected_categories = [
        "account", "display", "notification", "urgency",
        "theme", "system", "privacy", "debug"
    ]
    actual_categories = [cat[0] for cat in view._category_defs]
    assert actual_categories == expected_categories
    assert len(view._category_cards) == 8
    assert view._current_screen == "hub"


def test_navigation_to_detail_and_back_to_hub():
    page = _make_dummy_page()
    on_close = Mock()
    view = SettingsView(page=page, orchestrator=None, on_close=on_close)
    view.update = Mock()

    # Navigate to notification detail
    view._navigate_to_detail("notification")
    assert view._current_screen == "notification"
    assert view._active_category_id == "notification"
    assert view._detail_title.value == "Thông báo"
    assert len(view._detail_content_holder.controls) == 1

    # Navigate back to hub
    view._navigate_to_hub()
    assert view._current_screen == "hub"
    assert view._active_category_id is None
    assert view._view_switcher.content == view._hub_container


def test_all_detail_sections_render_properly():
    page = _make_dummy_page()
    on_close = Mock()
    view = SettingsView(page=page, orchestrator=None, on_close=on_close)
    view.update = Mock()

    for cat_id in ["account", "display", "notification", "urgency", "theme", "system", "privacy", "debug"]:
        view._navigate_to_detail(cat_id)
        assert view._current_screen == cat_id
        assert len(view._detail_content_holder.controls) == 1
        assert view._detail_title.value != ""


def test_dnd_visibility_toggles_with_switch():
    page = _make_dummy_page()
    on_close = Mock()
    view = SettingsView(page=page, orchestrator=None, on_close=on_close)
    view.update = Mock()

    view._sw_dnd_enable.value = False
    view._navigate_to_detail("notification")
    assert view._dnd_time_row.visible is False

    view._sw_dnd_enable.value = True
    view._update_dnd_summary()
    assert view._dnd_time_row.visible is True


def test_theme_refresh_updates_hub_cards_and_detail_controls():
    page = _make_dummy_page()
    on_close = Mock()
    view = SettingsView(page=page, orchestrator=None, on_close=on_close)

    view._refresh_section_colors()
    assert len(view._category_cards) == 8
    for card in view._category_cards.values():
        assert card.bgcolor is not None
        assert card.border is not None


def test_dirty_state_machine_and_save_button():
    page = _make_dummy_page()
    on_close = Mock()
    view = SettingsView(page=page, orchestrator=None, on_close=on_close)
    view.update = Mock()

    # Initial state (clean baseline)
    view._baseline_snapshot = view._capture_form_snapshot()
    view._update_save_button_state()
    assert view._save_btn.disabled is True
    assert view._save_text.value == "Đã lưu"

    # Dirty state (form changed)
    view._sw_submitted.value = not view._sw_submitted.value
    view._on_form_field_change()
    assert view._save_btn.disabled is False
    assert view._save_text.value == "Lưu thay đổi"

    # Saving state
    view._saving = True
    view._update_save_button_state()
    assert view._save_btn.disabled is True
    assert view._save_text.value == "Đang lưu..."

    # Revert saving
    view._saving = False
    view._update_save_button_state()
    assert view._save_btn.disabled is False
    assert view._save_text.value == "Lưu thay đổi"


def test_account_page_hides_save_footer_others_show():
    page = _make_dummy_page()
    on_close = Mock()
    view = SettingsView(page=page, orchestrator=None, on_close=on_close)
    view.update = Mock()

    # Account hides footer
    view._navigate_to_detail("account")
    assert view._sticky_footer.visible is False

    # Other pages show footer
    for cat_id in ["display", "notification", "urgency", "theme", "system", "privacy", "debug"]:
        view._navigate_to_detail(cat_id)
        assert view._sticky_footer.visible is True


def test_urgency_validation_relationship_and_blocks_save():
    import asyncio
    page = _make_dummy_page()
    on_close = Mock()
    view = SettingsView(page=page, orchestrator=None, on_close=on_close)
    view.update = Mock()
    view._baseline_snapshot = view._capture_form_snapshot()

    # Set invalid thresholds: critical (48) >= warning (24)
    view._critical_hours_field.value = "48"
    view._warning_hours_field.value = "24"
    from gui.components.settings.urgency_section import validate_urgency_thresholds
    assert validate_urgency_thresholds(view) is False
    assert view._urgency_error_text.visible is True

    # Save should be rejected
    save_result = asyncio.run(view._save(None))
    assert save_result is False
    assert "Lỗi" in view._save_status.value

    # Set valid thresholds: critical (12) < warning (24)
    view._critical_hours_field.value = "12"
    view._warning_hours_field.value = "24"
    assert validate_urgency_thresholds(view) is True
    assert view._urgency_error_text.visible is False


def test_autostart_sub_switch_visibility_toggles():
    page = _make_dummy_page()
    on_close = Mock()
    view = SettingsView(page=page, orchestrator=None, on_close=on_close)
    view.update = Mock()

    view._sw_start_with_windows.value = False
    view._sync_autostart_dependency()
    assert view._sw_start_minimized_container.visible is False

    view._sw_start_with_windows.value = True
    view._sync_autostart_dependency()
    assert view._sw_start_minimized_container.visible is True


def test_notification_profile_and_theo_sat():
    page = _make_dummy_page()
    on_close = Mock()
    view = SettingsView(page=page, orchestrator=None, on_close=on_close)
    view.update = Mock()

    # Check profile handler for Theo sát (exam_week)
    handler = view._handle_profile_select("exam_week")
    handler(None)
    assert view._current_profile == "exam_week"
    assert view._sw_dnd_enable.value is False
    assert 4320 in view._milestone_chips and view._milestone_chips[4320].selected is True


def test_selecting_theme_updates_dirty_dot_save_button_and_color_swatches():
    from gui.core.theme import THEME_PRESETS, apply_theme

    page = _make_dummy_page()
    on_close = Mock()
    view = SettingsView(page=page, orchestrator=None, on_close=on_close)
    view.update = Mock()

    initial_theme = "midnight_blue"
    alternate_theme = "nord_frost"
    try:
        view._selected_theme = initial_theme
        view._apply_theme_colors(initial_theme)
        view._baseline_snapshot = view._capture_form_snapshot()
        view._update_save_button_state()
        assert view.has_changes() is False
        assert view._save_btn.disabled is True
        assert view._save_text.value == "Đã lưu"

        view._on_theme_select(alternate_theme)
        assert view.has_changes() is True
        assert view._unsaved_dot.visible is True
        assert view._save_btn.disabled is False
        assert view._save_text.value == "Lưu thay đổi"
        assert (
            view._c_tb_critical._swatch_box.bgcolor
            == THEME_PRESETS[alternate_theme]["critical"]
        )

        # Selecting back to the baseline theme clears dirty state and relocks Save
        view._on_theme_select(initial_theme)
        assert view.has_changes() is False
        assert view._unsaved_dot.visible is False
        assert view._save_btn.disabled is True
        assert view._save_text.value == "Đã lưu"
    finally:
        apply_theme(initial_theme)


def test_settings_logout_and_account_login_seams_and_hub_back_navigation():
    import asyncio
    from types import SimpleNamespace

    page = _make_dummy_page()
    closed_calls: list[bool] = []
    logout_calls: list[bool] = []
    account_logins: list[tuple[str, str]] = []
    login_persist_flags: list[object] = []

    async def _on_logout() -> None:
        logout_calls.append(True)

    async def _on_account_login(u: str, p: str) -> None:
        account_logins.append((u, p))

    class _ProbeClient:
        def login(
            self,
            username: str,
            password: str,
            *,
            force: bool = False,
            persist: bool = True,
        ) -> bool:
            login_persist_flags.append((force, persist))
            return username == "sv999" and password == "pw999"

    orch = SimpleNamespace(client=_ProbeClient(), is_logged_in=False)
    view = SettingsView(
        page=page,
        orchestrator=orch,
        on_close=lambda: closed_calls.append(True),
        on_logout=_on_logout,
        on_account_login=_on_account_login,
    )
    view.update = Mock()

    # 1. ExpansionTile affinity must never be set to ft.Theme.color_scheme
    def _walk(ctrl):
        yield ctrl
        for attr in ("content", "title", "subtitle", "leading", "trailing"):
            child = getattr(ctrl, attr, None)
            if isinstance(child, ft.Control):
                yield from _walk(child)
        for attr in ("controls", "items", "actions"):
            seq = getattr(ctrl, attr, None)
            if isinstance(seq, (list, tuple)):
                for item in seq:
                    if isinstance(item, ft.Control):
                        yield from _walk(item)

    for c in _walk(view):
        if isinstance(c, ft.ExpansionTile):
            assert c.affinity is None or isinstance(c.affinity, ft.TileAffinity)

    # 2. Sub-screen Back when on_closed is None returns to hub first instead of closing Settings
    view._navigate_to_detail("account")
    assert view._current_screen == "account"
    profile_opens = []
    view._on_open_profile = lambda: profile_opens.append(True)
    view._open_profile_btn.on_click(None)
    assert profile_opens == [True]
    asyncio.run(view._handle_back(None, on_closed=None))
    assert view._current_screen == "hub"
    assert closed_calls == []
    asyncio.run(view._handle_back(None, on_closed=None))
    assert closed_calls == [True]

    # 3. _handle_test_login probes persist=False and delegates to on_account_login seam
    view._username_field.value = "sv999"
    view._password_field.value = "pw999"
    asyncio.run(view._handle_test_login(None))
    assert login_persist_flags == [(True, False)]
    assert account_logins == [("sv999", "pw999")]

    # 4. Explicit "Đăng xuất" button invokes on_logout seam and clears form fields
    assert hasattr(view, "_logout_btn")
    asyncio.run(view._handle_logout(None))
    assert logout_calls == [True]
    assert view._username_field.value == ""
    assert view._password_field.value == ""

