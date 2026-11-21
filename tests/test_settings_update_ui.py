from gui.components.settings.system_section import init_system_controls
from gui.components.settings.system_section import build_system_section
from gui.components.settings_view import SettingsView
from types import SimpleNamespace
import flet as ft


class _View:
    def _on_autostart_toggle(self, _event):
        pass

    def _toggle_bg_check_ui(self):
        pass

    def _handle_check_update(self, _event):
        pass


def test_system_settings_exposes_default_on_update_and_manual_check_button():
    view = _View()

    init_system_controls(view)

    assert view._sw_auto_update.value is True
    assert view._sw_auto_update.label == "Tự động kiểm tra cập nhật"
    assert view._check_update_btn.content == "Kiểm tra ngay"


def test_manual_check_button_delegates_without_update_business_logic():
    calls = []
    view = SettingsView.__new__(SettingsView)
    view._on_check_update = lambda: calls.append("check")

    view._handle_check_update(None)

    assert calls == ["check"]


def test_update_action_and_cancel_delegate_to_coordinator_callbacks():
    calls = []
    view = SettingsView.__new__(SettingsView)
    view._on_update_action = lambda: calls.append("action")
    view._on_cancel_update = lambda: calls.append("cancel")

    view._handle_update_action(None)
    view._handle_cancel_update(None)

    assert calls == ["action", "cancel"]


def test_update_status_displays_phase_progress_and_recovery_action():
    view = SettingsView.__new__(SettingsView)
    view._update_status_text = SimpleNamespace(value="", visible=False, color=None)
    view._update_progress = SimpleNamespace(value=0, visible=False)
    view._update_action_btn = SimpleNamespace(content="", visible=False)
    view._update_cancel_btn = SimpleNamespace(visible=False)
    view._update_action_row = SimpleNamespace(visible=False)
    view._update_status_group = SimpleNamespace(visible=False)
    view.update = lambda: None

    view.set_update_status(
        "Đang tải bản cập nhật…",
        progress=0.4,
        show_progress=True,
        action_label="Cài đặt",
        can_cancel=True,
    )

    assert view._update_status_text.value == "Đang tải bản cập nhật…"
    assert view._update_progress.visible is True
    assert view._update_progress.value == 0.4
    assert view._update_action_btn.content == "Cài đặt"
    assert view._update_action_btn.visible is True
    assert view._update_cancel_btn.visible is True

    view.set_update_status("Đã mở trình cài đặt.")

    assert view._update_status_group.visible is True
    assert view._update_progress.visible is False
    assert view._update_action_btn.visible is False
    assert view._update_cancel_btn.visible is False


def test_system_settings_places_update_status_with_update_controls():
    view = _View()
    init_system_controls(view)
    view._make_themed_label = lambda label: ft.Text(label)
    view._build_hint = lambda message: ft.Container(content=ft.Text(message))
    view._update_status_group = ft.Column([])
    view._interval_field = ft.Dropdown(options=[])
    view._fetch_months_field = ft.TextField()

    section = build_system_section(view)

    assert view._update_status_group in section.controls
    status_index = section.controls.index(view._update_status_group)
    assert view._check_update_btn in section.controls[status_index - 1].controls


def _collect_text_values(control) -> list[str]:
    found: list[str] = []
    if isinstance(control, ft.Text) and isinstance(control.value, str):
        found.append(control.value)
    content = getattr(control, "content", None)
    if isinstance(content, ft.BaseControl):
        found.extend(_collect_text_values(content))
    elif isinstance(content, str):
        found.append(content)
    for child in getattr(control, "controls", None) or ():
        found.extend(_collect_text_values(child))
    return found


def _collect_controls(control) -> list:
    items = [control]
    content = getattr(control, "content", None)
    if isinstance(content, ft.BaseControl):
        items.extend(_collect_controls(content))
    for child in getattr(control, "controls", None) or ():
        items.extend(_collect_controls(child))
    return items


def test_chplay_system_section_hides_github_controls_and_shows_google_play_link():
    from core.distribution import DistributionChannel, build_distribution_profile

    opened: list[str] = []
    view = _View()
    view._distribution_profile = build_distribution_profile(DistributionChannel.CHPLAY)
    view._handle_open_store_url = lambda _event=None: opened.append("store")
    init_system_controls(view)
    view._make_themed_label = lambda label: ft.Text(label)
    view._build_hint = lambda message: ft.Container(content=ft.Text(message))
    view._update_status_group = ft.Column([])
    view._interval_field = ft.Dropdown(options=[])
    view._fetch_months_field = ft.TextField()

    section = build_system_section(view)
    all_controls = _collect_controls(section)
    texts = _collect_text_values(section)

    assert view._sw_auto_update not in all_controls
    assert view._check_update_btn not in all_controls
    assert view._update_status_group not in all_controls
    assert view._sw_auto_update.visible is False
    assert view._check_update_btn.visible is False
    assert "Cập nhật được quản lý bởi Google Play" in texts
    assert view._store_link_btn in all_controls
    assert view._store_link_btn.visible is True
    assert view._store_link_btn.content == "Mở trên Google Play"

    view._store_link_btn.on_click(None)
    assert opened == ["store"]


def test_msstore_system_section_with_and_without_product_id():
    from core.distribution import DistributionChannel, build_distribution_profile

    for product_id, expect_btn in ((None, False), ("9NBLGGH4NNS1", True)):
        view = _View()
        view._distribution_profile = build_distribution_profile(
            DistributionChannel.MSSTORE,
            store_product_id=product_id,
        )
        init_system_controls(view)
        view._make_themed_label = lambda label: ft.Text(label)
        view._build_hint = lambda message: ft.Container(content=ft.Text(message))
        view._update_status_group = ft.Column([])
        view._interval_field = ft.Dropdown(options=[])
        view._fetch_months_field = ft.TextField()

        section = build_system_section(view)
        all_controls = _collect_controls(section)
        texts = _collect_text_values(section)

        assert view._sw_auto_update not in all_controls
        assert view._check_update_btn not in all_controls
        assert "Cập nhật được quản lý bởi Microsoft Store" in texts
        if expect_btn:
            assert view._store_link_btn in all_controls
            assert view._store_link_btn.visible is True
            assert view._store_link_btn.content == "Mở trong Microsoft Store"
        else:
            assert view._store_link_btn not in all_controls
            assert view._store_link_btn.visible is False
            assert any("Thư viện" in text for text in texts)


def test_store_channel_hides_debug_force_check_and_blocks_github_fallback(monkeypatch):
    from unittest.mock import Mock
    import core.update_checker as update_checker
    from core.distribution import DistributionChannel, build_distribution_profile

    monkeypatch.setattr(
        update_checker,
        "check_for_update",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("check_for_update must never run on Store channels")
        ),
    )

    for channel in (DistributionChannel.CHPLAY, DistributionChannel.MSSTORE):
        page = Mock(spec=ft.Page, window=Mock(), theme_mode="dark", on_route_change=None)
        profile = build_distribution_profile(channel)
        view = SettingsView(
            page=page,
            orchestrator=None,
            on_close=lambda: None,
            distribution_profile=profile,
        )

        debug_texts = _collect_text_values(view._test_panel)
        assert "Force check update" not in debug_texts
        assert profile.store_display_name in view._hub_version.value

        # Calling handlers directly must remain strict no-ops.
        view._handle_check_update(None)
        view._handle_update_action(None)
        view._handle_cancel_update(None)
        view.set_update_status("Should not show")
        assert view._update_status_group.visible is False
        view._do_force_check_update()

        view._refresh_section_colors()
        assert view._store_update_info_text.color is not None


def test_store_channel_settings_dirty_state_remains_clean_without_auto_update_ui(
    monkeypatch,
):
    from unittest.mock import Mock
    import gui.components.settings_view as settings_view_module
    from core.distribution import DistributionChannel, build_distribution_profile
    from gui.view_models.settings_form import SettingsFormSnapshot

    page = Mock(spec=ft.Page, window=Mock(), theme_mode="dark", on_route_change=None)
    view = SettingsView(
        page=page,
        orchestrator=None,
        on_close=lambda: None,
        distribution_profile=build_distribution_profile(DistributionChannel.CHPLAY),
    )
    baseline = SettingsFormSnapshot.from_form_values({"auto_update_enabled": True})
    view._apply_snapshot_to_controls(baseline)
    view._baseline_snapshot = baseline
    view._loading = False

    assert view.has_changes() is False

    # Changing an unrelated field keeps auto_update_enabled preserved in snapshot.
    view._sw_submitted.value = not view._sw_submitted.value
    assert view.has_changes() is True
    captured = view._capture_form_snapshot()
    assert captured.auto_update_enabled is True
    assert settings_view_module.SettingsFormSnapshot.from_form_values(
        captured.to_settings_values()
    ).auto_update_enabled is True


def test_settings_view_store_url_falls_back_to_https_when_url_launcher_fails(
    monkeypatch,
):
    import asyncio
    from unittest.mock import AsyncMock, Mock
    from core.distribution import DistributionChannel, build_distribution_profile

    launched: list[tuple[str, object]] = []

    async def fake_url_launcher_launch(_self, url, *, mode=None, **_kwargs):
        launched.append((url, mode))
        if url.startswith("market://"):
            raise RuntimeError("No activity")

    monkeypatch.setattr(ft.UrlLauncher, "launch_url", fake_url_launcher_launch)

    def run_task(handler, *args):
        asyncio.run(handler(*args))
        return object()

    page = Mock(
        spec=ft.Page,
        window=Mock(),
        theme_mode="dark",
        on_route_change=None,
        launch_url=AsyncMock(),
        run_task=run_task,
    )
    view = SettingsView(
        page=page,
        orchestrator=None,
        on_close=lambda: None,
        distribution_profile=build_distribution_profile(DistributionChannel.CHPLAY),
        on_open_store_url=None,
    )

    assert view._handle_open_store_url() is True
    assert launched == [
        ("market://details?id=com.uthelper.uthelper", ft.LaunchMode.EXTERNAL_APPLICATION),
        (
            "https://play.google.com/store/apps/details?id=com.uthelper.uthelper",
            ft.LaunchMode.EXTERNAL_APPLICATION,
        ),
    ]
    assert page.launch_url.await_count == 0


def test_debug_force_check_update_updates_debug_text_when_wired_to_controller():
    from unittest.mock import Mock
    from core.distribution import DistributionChannel, build_distribution_profile

    checked = []
    page = Mock(spec=ft.Page, window=Mock(), theme_mode="dark", on_route_change=None)
    view = SettingsView(
        page=page,
        orchestrator=None,
        on_close=lambda: None,
        on_check_update=lambda: checked.append("check"),
        distribution_profile=build_distribution_profile(DistributionChannel.EXE_LTS),
    )

    view._do_force_check_update()

    assert checked == ["check"]
    assert view._debug_update_text.value == "Đang kiểm tra cập nhật..."

    view.set_update_status("Bạn đang dùng phiên bản mới nhất")
    assert view._debug_update_text.value == "Bạn đang dùng phiên bản mới nhất"

