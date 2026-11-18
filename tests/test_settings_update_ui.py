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
