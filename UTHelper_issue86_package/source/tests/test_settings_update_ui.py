from gui.components.settings.system_section import init_system_controls
from gui.components.settings_view import SettingsView


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
