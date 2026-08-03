import asyncio
from types import MethodType, SimpleNamespace

from gui.components.settings.system_section import init_system_controls
from gui.components.settings_view import SettingsView
from gui.controllers.autostart_settings import AutostartUiState


class FakeCoordinator:
    def __init__(self, load_result, change_result=None):
        self.load_result = load_result
        self.change_result = change_result or load_result
        self.requests = []

    async def load(self):
        return self.load_result

    async def change(self, enabled):
        self.requests.append(enabled)
        return self.change_result


def control(**values):
    values.setdefault("update", lambda: None)
    return SimpleNamespace(**values)


def view_for(result):
    view = SimpleNamespace(
        _autostart_coordinator=FakeCoordinator(result),
        _sw_start_with_windows=control(value=False, disabled=False),
        _sw_start_minimized=control(value=True, disabled=True),
        _autostart_status=control(value="", color=None),
        update=lambda: None,
    )
    view._sync_autostart_dependency = MethodType(
        SettingsView._sync_autostart_dependency, view
    )
    view._apply_autostart_ui = MethodType(SettingsView._apply_autostart_ui, view)
    return view


def test_system_controls_scope_hidden_option_to_windows_autostart():
    view = SimpleNamespace(_on_autostart_toggle=lambda _event: None)

    init_system_controls(view)

    assert view._sw_start_with_windows.label == "Khởi động cùng Windows"
    assert (
        view._sw_start_minimized.label
        == "Khi khởi động cùng Windows: Ẩn xuống khay hệ thống"
    )
    assert view._sw_start_minimized.disabled is (not view._sw_start_with_windows.value)
    assert view._sw_start_with_windows.on_change is not None


def test_reconcile_uses_real_windows_state_and_control_editability(monkeypatch):
    result = AutostartUiState(
        enabled=False,
        editable=False,
        success=False,
        message="Bật lại trong Task Manager.",
    )
    view = view_for(result)
    saved = []
    monkeypatch.setattr(
        "gui.components.settings_view.save_settings", lambda: saved.append(True)
    )
    monkeypatch.setattr(
        "gui.components.settings_view.settings.START_WITH_WINDOWS", True
    )

    asyncio.run(SettingsView._reconcile_autostart(view))

    assert view._sw_start_with_windows.value is False
    assert view._sw_start_with_windows.disabled is True
    assert view._sw_start_minimized.disabled is True
    assert view._autostart_status.value == "Bật lại trong Task Manager."
    assert saved == [True]


def test_apply_autostart_rolls_control_back_when_windows_rejects(monkeypatch):
    current = AutostartUiState(False, True, True, "")
    rejected = AutostartUiState(
        False, False, False, "Windows đã tắt mục này trong Task Manager."
    )
    view = view_for(current)
    view._autostart_coordinator.change_result = rejected
    view._sw_start_with_windows.value = True
    monkeypatch.setattr(
        "gui.components.settings_view.settings.START_WITH_WINDOWS", False
    )

    result = asyncio.run(SettingsView._apply_autostart_change(view))

    assert result is False
    assert view._autostart_coordinator.requests == [True]
    assert view._sw_start_with_windows.value is False
    assert view._autostart_status.value.startswith("Windows đã tắt")


def test_apply_autostart_accepts_confirmed_state(monkeypatch):
    current = AutostartUiState(False, True, True, "")
    enabled = AutostartUiState(True, True, True, "")
    view = view_for(current)
    view._autostart_coordinator.change_result = enabled
    view._sw_start_with_windows.value = True
    monkeypatch.setattr(
        "gui.components.settings_view.settings.START_WITH_WINDOWS", False
    )

    result = asyncio.run(SettingsView._apply_autostart_change(view))

    assert result is True
    assert view._autostart_coordinator.requests == [True]
    assert view._sw_start_with_windows.value is True


def test_apply_skips_mutation_when_os_already_matches(monkeypatch):
    enabled = AutostartUiState(True, True, True, "")
    view = view_for(enabled)
    view._sw_start_with_windows.value = True
    monkeypatch.setattr(
        "gui.components.settings_view.settings.START_WITH_WINDOWS", False
    )

    result = asyncio.run(SettingsView._apply_autostart_change(view))

    assert result is True
    assert view._autostart_coordinator.requests == []


def test_save_path_calls_transactional_autostart_before_persisting():
    source = __import__(
        "inspect"
    ).getsource(SettingsView._save)

    assert "await self._apply_autostart_change()" in source
    assert source.index("await self._apply_autostart_change()") < source.index(
        "settings.START_WITH_WINDOWS ="
    )
    assert source.index("settings.START_WITH_WINDOWS =") < source.index(
        "save_settings()"
    )
