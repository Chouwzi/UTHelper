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
        _baseline_snapshot=None,
        _loading=False,
        _load_generation=0,
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


def test_confirmed_load_uses_real_windows_state_and_control_editability(monkeypatch):
    import gui.components.settings_view as settings_view_module

    result = AutostartUiState(
        enabled=False,
        editable=False,
        success=False,
        message="Bật lại trong Task Manager.",
    )
    view = view_for(result)
    monkeypatch.setattr(
        "gui.components.settings_view.settings.START_WITH_WINDOWS", True
    )

    loaded = asyncio.run(SettingsView._load_autostart_state(view, 0))
    SettingsView._apply_autostart_ui(view, loaded)

    assert view._sw_start_with_windows.value is False
    assert view._sw_start_with_windows.disabled is True
    assert view._sw_start_minimized.disabled is True
    assert view._autostart_status.value == "Bật lại trong Task Manager."
    assert loaded.confirmed is True
    assert settings_view_module.settings.START_WITH_WINDOWS is True


def test_unconfirmed_autostart_load_preserves_persisted_value_without_dirty_state(
    monkeypatch,
):
    result = AutostartUiState(
        False,
        False,
        False,
        "Không thể xác nhận. Hãy thử lại.",
        confirmed=False,
    )
    from tests.test_settings_view_state import _make_loading_view

    view = _make_loading_view(FakeCoordinator(result))
    monkeypatch.setattr(
        "gui.components.settings_view.settings.START_WITH_WINDOWS", True
    )

    asyncio.run(SettingsView.load_current_settings(view))

    assert view._sw_start_with_windows.value is True
    assert view._sw_start_with_windows.disabled is True
    assert view._baseline_snapshot.start_with_windows is True
    assert view._autostart_status.value == "Không thể xác nhận. Hãy thử lại."
    assert SettingsView.has_changes(view) is False


def test_autostart_load_timeout_is_bounded_and_returns_retry_state(monkeypatch):
    view = view_for(AutostartUiState(True, True, True, ""))
    view._load_generation = 4
    observed = []

    async def timeout(awaitable, timeout):
        observed.append(timeout)
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr("gui.components.settings_view.asyncio.wait_for", timeout)

    result = asyncio.run(SettingsView._load_autostart_state(view, 4))

    assert observed == [2.0]
    assert result.confirmed is False
    assert result.editable is False
    assert "thử lại" in result.message.lower()


def test_save_bounds_autostart_mutation_and_persists_confirmed_actual(monkeypatch):
    from dataclasses import replace
    from gui.view_models.settings_form import SettingsFormSnapshot

    baseline = SettingsFormSnapshot.from_form_values({"start_with_windows": False})
    requested = replace(baseline, start_with_windows=True)
    rejected = AutostartUiState(
        False,
        False,
        False,
        "Windows đã tắt mục này trong Task Manager.",
    )
    coordinator = FakeCoordinator(AutostartUiState(False, True, True, ""), rejected)
    observed_timeouts = []
    persisted = []

    async def bounded(awaitable, timeout):
        observed_timeouts.append(timeout)
        return await awaitable

    view = SimpleNamespace(
        _baseline_snapshot=baseline,
        _capture_form_snapshot=lambda: requested,
        _autostart_coordinator=coordinator,
        _apply_autostart_ui=lambda state: setattr(
            view._autostart_status, "value", state.message
        ),
        _persist_snapshot_to_settings=lambda value: persisted.append(value) or True,
        _apply_snapshot_to_controls=lambda value: setattr(
            view, "visible_snapshot", value
        ),
        _save_status=SimpleNamespace(value="", color=None),
        _autostart_status=SimpleNamespace(value="", color=None),
        _unsaved_dot=SimpleNamespace(visible=True),
        _page=SimpleNamespace(window=SimpleNamespace(always_on_top=True)),
        _on_saved=None,
        update=lambda: None,
    )
    monkeypatch.setattr("gui.components.settings_view.asyncio.wait_for", bounded)
    monkeypatch.setattr("gui.components.settings_view._pu.IS_MOBILE", False)

    assert asyncio.run(SettingsView._save(view, None)) is False
    assert coordinator.requests == [True]
    assert observed_timeouts == [2.0]
    assert persisted == [replace(requested, start_with_windows=False)]
    assert view._baseline_snapshot == persisted[0]
    assert view.visible_snapshot == persisted[0]


def test_save_timeout_preserves_baseline_autostart_but_persists_other_fields(
    monkeypatch,
):
    from dataclasses import replace
    from gui.view_models.settings_form import SettingsFormSnapshot

    baseline = SettingsFormSnapshot.from_form_values({"start_with_windows": False})
    requested = replace(baseline, theme="solarized_dark", start_with_windows=True)
    persisted = []

    async def timeout(awaitable, timeout):
        assert timeout == 2.0
        awaitable.close()
        raise asyncio.TimeoutError

    view = SimpleNamespace(
        _baseline_snapshot=baseline,
        _capture_form_snapshot=lambda: requested,
        _autostart_coordinator=FakeCoordinator(
            AutostartUiState(False, True, True, "")
        ),
        _apply_autostart_ui=lambda state: setattr(
            view._autostart_status, "value", state.message
        ),
        _persist_snapshot_to_settings=lambda value: persisted.append(value) or True,
        _apply_snapshot_to_controls=lambda value: None,
        _save_status=SimpleNamespace(value="", color=None),
        _autostart_status=SimpleNamespace(value="", color=None),
        _unsaved_dot=SimpleNamespace(visible=True),
        _page=SimpleNamespace(window=SimpleNamespace(always_on_top=True)),
        _on_saved=None,
        update=lambda: None,
    )
    monkeypatch.setattr("gui.components.settings_view.asyncio.wait_for", timeout)
    monkeypatch.setattr("gui.components.settings_view._pu.IS_MOBILE", False)

    assert asyncio.run(SettingsView._save(view, None)) is False
    assert persisted == [replace(requested, start_with_windows=False)]
    assert "thời gian" in view._save_status.value.lower()


def test_persistence_failure_compensates_successful_windows_change(monkeypatch):
    from dataclasses import replace
    from gui.view_models.settings_form import SettingsFormSnapshot

    baseline = SettingsFormSnapshot.from_form_values({"start_with_windows": False})
    requested = replace(baseline, start_with_windows=True)
    coordinator = FakeCoordinator(
        AutostartUiState(False, True, True, ""),
        AutostartUiState(True, True, True, "Đã bật."),
    )

    async def change(enabled):
        coordinator.requests.append(enabled)
        return AutostartUiState(enabled, True, True, "")

    coordinator.change = change
    view = SimpleNamespace(
        _baseline_snapshot=baseline,
        _capture_form_snapshot=lambda: requested,
        _autostart_coordinator=coordinator,
        _apply_autostart_ui=lambda state: None,
        _persist_snapshot_to_settings=lambda value: False,
        _apply_snapshot_to_controls=lambda value: None,
        _save_status=SimpleNamespace(value="", color=None),
        _autostart_status=SimpleNamespace(value="", color=None),
        _unsaved_dot=SimpleNamespace(visible=True),
        _page=SimpleNamespace(window=SimpleNamespace(always_on_top=True)),
        _on_saved=None,
        update=lambda: None,
    )
    monkeypatch.setattr("gui.components.settings_view._pu.IS_MOBILE", False)

    assert asyncio.run(SettingsView._save(view, None)) is False
    assert coordinator.requests == [True, False]
    assert view._baseline_snapshot == baseline
