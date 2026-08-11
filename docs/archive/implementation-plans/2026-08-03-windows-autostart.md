# Windows Autostart and Launch Visibility Implementation Plan

> **Archived:** Planning snapshot retained for provenance. It is not the current
> task tracker; use tests and current operator documentation as the source of truth.

**Goal:** Make the Settings UI truthfully enable/disable Windows autostart for both Inno and MSIX distributions and let users choose a visible or tray-hidden autostart launch.

**Architecture:** A platform service selects a canonical HKCU Run-key backend for unpackaged Flet/Inno processes or a manifest-backed `Windows.ApplicationModel.StartupTask` backend for MSIX package identity. A small GUI coordinator maps OS state to actionable UI state; launch visibility is a pure policy evaluated only after tray setup reports readiness.

**Tech Stack:** Python 3.11+, ctypes/winreg, `winrt-Windows.ApplicationModel==3.2.1`, Flet, pytest, Inno Setup, MSIX desktop manifest extensions.

## Global Constraints

- Never use the parent process as the UTHelper executable.
- The canonical packaged command is `"<bundle>/UTHelperAutostart.exe"` with no arguments; development mode may include a real entry script and `--autostart` only when the current process is Python/PythonW.
- MSIX uses `TaskId="UTHelperStartup"`, `Enabled="false"`, and `Executable="UTHelperAutostart.exe"` with no parameters.
- A user- or policy-disabled startup task must not be reported as enabled or programmatically overridden.
- Persist `START_WITH_WINDOWS` only after the requested Windows state is read back successfully.
- Manual launches are visible. Only `--autostart` plus `START_MINIMIZED=true` may hide the window, and only after tray readiness is confirmed.
- `START_MINIMIZED` remains the persisted compatibility field; do not introduce a duplicate setting.
- Unit tests fake Windows APIs. The only real Registry test uses a unique value and removes it in `finally`.
- Use `apply_patch` for source changes and commit each independently testable task on `feature/windows-startup-stability`.

---

## File map

- Rewrite `src/platform_utils/autostart.py`: status model, current-process resolution, Run-key backend, StartupTask backend, and backend factory.
- Modify `src/core/autostart.py`: compatibility re-exports for the structured service API.
- Create `tests/test_windows_autostart.py`: deterministic backend, command, state, and factory tests.
- Create `src/gui/controllers/autostart_settings.py`: OS-to-UI state coordinator.
- Create `tests/test_autostart_settings_coordinator.py`: coordinator state/error tests.
- Modify `src/gui/components/settings/system_section.py`: scoped copy, dependency state, and status text.
- Modify `src/gui/components/settings_view.py`: dependency injection, async reconciliation, transactional save.
- Create `tests/test_settings_autostart_ui.py`: Settings control and persistence/rollback tests.
- Create `src/gui/controllers/startup_visibility.py`: pure launch visibility policy.
- Modify `src/gui/tray.py`: observable tray setup success.
- Modify `src/gui/app_controller.py`: apply visibility policy after tray readiness.
- Modify `tests/test_autostart_and_tray.py`: focused tray/visibility tests without production Registry writes.
- Create `tests/test_windows_autostart_integration.py`: opt-in unique HKCU Run integration test.
- Modify `pyproject.toml`: Windows-only WinRT projection and pytest marker.
- Modify `scripts/package_msix.ps1`: manifest StartupTask declaration and parameters.
- Modify `tests/test_release_hardening.py`: dependency and manifest contract tests.
- Modify `docs/WINDOWS_EXE_PACKAGING.md` and `REFAC_KNOWLEDGE.md`: operator behavior and evidence.

### Task 1: Structured state model and canonical Run-key backend

**Files:**
- Rewrite: `src/platform_utils/autostart.py`
- Modify: `src/core/autostart.py`
- Create: `tests/test_windows_autostart.py`

**Interfaces:**
- Produces: `AutostartBackendKind(str, Enum)` values `run_key`, `startup_task`, `unavailable`.
- Produces: `AutostartState(str, Enum)` values `enabled`, `disabled`, `disabled_by_user`, `disabled_by_policy`, `unavailable`, `error`.
- Produces: immutable `AutostartStatus(backend, state, message="", technical_detail="")` with `enabled` and `successful` properties.
- Produces: `get_current_process_executable() -> Path` using `GetModuleFileNameW`.
- Produces: `build_startup_command(executable: Path, argv0: str | None = None) -> str`.
- Produces: `RunKeyAutostartBackend(command: str, app_name: str = "UTHelper")` with async `get_status()` and `set_enabled(enabled: bool)`.

- [ ] **Step 1: Write failing state, executable, quoting, and Registry tests**

```python
import subprocess
from pathlib import Path

import pytest

from platform_utils.autostart import (
    AutostartBackendKind,
    AutostartState,
    RunKeyAutostartBackend,
    build_startup_command,
)


def test_flet_runner_command_uses_only_current_executable():
    executable = Path(r"C:\Program Files\Ứng dụng\UTHelper.exe")
    assert build_startup_command(executable, r"C:\Temp\serious_python\main.py") == (
        subprocess.list2cmdline([str(executable), "--autostart"])
    )


def test_python_development_command_requires_existing_script(tmp_path):
    pythonw = tmp_path / "pythonw.exe"
    pythonw.write_bytes(b"exe")
    script = tmp_path / "main.py"
    script.write_text("print('ok')", encoding="utf-8")
    assert build_startup_command(pythonw, str(script)) == subprocess.list2cmdline(
        [str(pythonw), str(script), "--autostart"]
    )


def test_python_development_command_rejects_missing_script(tmp_path):
    with pytest.raises(ValueError, match="entry script"):
        build_startup_command(tmp_path / "pythonw.exe", str(tmp_path / "missing.py"))


def test_run_key_enable_reads_back_exact_canonical_command():
    values: dict[str, str] = {}
    backend = RunKeyAutostartBackend(
        command='"C:\\Program Files\\UTHelper\\UTHelperAutostart.exe"',
        reader=values.get,
        writer=values.__setitem__,
        deleter=lambda name: values.pop(name, None),
    )
    result = asyncio.run(backend.set_enabled(True))
    assert result.backend is AutostartBackendKind.RUN_KEY
    assert result.state is AutostartState.ENABLED
    assert values["UTHelper"] == backend.command


def test_run_key_stale_command_is_disabled_until_explicit_enable():
    values = {"UTHelper": '"C:\\Temp\\pythonw.exe" "main.py" --autostart'}
    backend = RunKeyAutostartBackend(
        command='"C:\\Program Files\\UTHelper\\UTHelperAutostart.exe"',
        reader=values.get,
        writer=values.__setitem__,
        deleter=lambda name: values.pop(name, None),
    )
    assert asyncio.run(backend.get_status()).state is AutostartState.DISABLED
```

```python
def test_run_key_writer_failure_returns_error():
    def fail_write(name, value):
        raise PermissionError("denied")

    backend = RunKeyAutostartBackend(
        command='"C:\\UTHelperAutostart.exe"',
        reader=lambda name: None,
        writer=fail_write,
        deleter=lambda name: None,
    )
    assert asyncio.run(backend.set_enabled(True)).state is AutostartState.ERROR


def test_run_key_disable_is_idempotent():
    backend = RunKeyAutostartBackend(
        command='"C:\\UTHelperAutostart.exe"',
        reader=lambda name: None,
        writer=lambda name, value: None,
        deleter=lambda name: None,
    )
    assert asyncio.run(backend.set_enabled(False)).state is AutostartState.DISABLED


def test_enable_removes_legacy_value_after_read_back():
    values = {"UTHElearningAlert": '"C:\\Old\\UTHelper.exe" --autostart'}
    backend = RunKeyAutostartBackend(
        command='"C:\\UTHelperAutostart.exe"',
        reader=values.get,
        writer=values.__setitem__,
        deleter=lambda name: values.pop(name, None),
    )
    assert asyncio.run(backend.set_enabled(True)).state is AutostartState.ENABLED
    assert "UTHElearningAlert" not in values
```

- [ ] **Step 2: Run the tests and confirm missing structured APIs**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_windows_autostart.py -q`

Expected: collection fails on missing enums/classes/functions.

- [ ] **Step 3: Implement the model, current-process lookup, and Run-key backend**

Use `subprocess.list2cmdline` for command construction. A current executable whose
lowercase stem is `python` or `pythonw` is development mode. A packaged runner
resolves the sibling alias and passes no arguments because Flet 0.86.5 treats any
desktop argument as a development-server launch and skips embedded Python.

The current-process lookup must use this Windows API shape:

```python
buffer = ctypes.create_unicode_buffer(32768)
length = ctypes.WinDLL("kernel32", use_last_error=True).GetModuleFileNameW(
    None, buffer, len(buffer)
)
if not length:
    raise OSError(ctypes.get_last_error(), "GetModuleFileNameW failed")
return Path(buffer.value)
```

Use context-managed `winreg.OpenKey` helpers for read/write/delete under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. `get_status()` reports enabled
only for an exact canonical command. `set_enabled()` mutates, reads back, and returns
`ERROR` when read-back does not match.

Keep compatibility functions as async structured wrappers:

```python
async def get_autostart_status() -> AutostartStatus:
    return await create_autostart_service().get_status()


async def set_autostart_enabled(enabled: bool) -> AutostartStatus:
    return await create_autostart_service().set_enabled(enabled)
```

Re-export the enums, dataclass, factory, and two async functions from
`src/core/autostart.py`.

- [ ] **Step 4: Run backend tests and lint**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_windows_autostart.py -q`

Expected: all Task 1 tests pass.

Run: `.\.venv\Scripts\python.exe -m ruff check src\platform_utils\autostart.py src\core\autostart.py tests\test_windows_autostart.py`

Expected: pass.

- [ ] **Step 5: Commit the Run-key backend**

```powershell
git add src/platform_utils/autostart.py src/core/autostart.py tests/test_windows_autostart.py
git commit -m "fix: register the current Flet executable for autostart"
```

### Task 2: Package identity and MSIX StartupTask backend

**Files:**
- Modify: `src/platform_utils/autostart.py`
- Modify: `tests/test_windows_autostart.py`

**Interfaces:**
- Consumes: `AutostartStatus` model from Task 1.
- Produces: `has_package_identity() -> bool` using `GetCurrentPackageFamilyName`.
- Produces: `StartupTaskAutostartBackend(task_loader, task_id="UTHelperStartup")`.
- Produces: `UnavailableAutostartBackend` for non-Windows or missing projection.
- Produces: `create_autostart_service() -> AutostartServiceProtocol`.

- [ ] **Step 1: Add failing StartupTask state/factory tests**

```python
class FakeTask:
    def __init__(self, state):
        self.state = state
        self.disable_calls = 0

    async def request_enable_async(self):
        self.state = "enabled"
        return self.state

    def disable(self):
        self.disable_calls += 1
        self.state = "disabled"


async def async_value(value):
    return value


@pytest.mark.parametrize(
    ("native_state", "expected"),
    [
        ("enabled", AutostartState.ENABLED),
        ("disabled", AutostartState.DISABLED),
        ("disabled_by_user", AutostartState.DISABLED_BY_USER),
        ("disabled_by_policy", AutostartState.DISABLED_BY_POLICY),
    ],
)
def test_startup_task_maps_native_states(native_state, expected):
    task = FakeTask(native_state)
    backend = StartupTaskAutostartBackend(task_loader=lambda task_id: async_value(task))
    assert asyncio.run(backend.get_status()).state is expected


def test_startup_task_does_not_override_disabled_by_user():
    task = FakeTask("disabled_by_user")
    backend = StartupTaskAutostartBackend(task_loader=lambda task_id: async_value(task))
    result = asyncio.run(backend.set_enabled(True))
    assert result.state is AutostartState.DISABLED_BY_USER


def test_factory_prefers_startup_task_for_package_identity(monkeypatch):
    monkeypatch.setattr(autostart, "has_package_identity", lambda: True)
    service = autostart.create_autostart_service(platform_name="win32")
    assert isinstance(service, StartupTaskAutostartBackend)


def test_factory_uses_run_key_without_package_identity(monkeypatch):
    monkeypatch.setattr(autostart, "has_package_identity", lambda: False)
    monkeypatch.setattr(
        autostart,
        "get_current_process_executable",
        lambda: Path(r"C:\Program Files\UTHelper\UTHelper.exe"),
    )
    service = autostart.create_autostart_service(platform_name="win32")
    assert isinstance(service, RunKeyAutostartBackend)


def test_factory_is_unavailable_outside_windows():
    service = autostart.create_autostart_service(platform_name="linux")
    assert isinstance(service, UnavailableAutostartBackend)
```

- [ ] **Step 2: Run tests and verify StartupTask cases fail**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_windows_autostart.py -q`

Expected: failures name missing StartupTask backend and factory routing.

- [ ] **Step 3: Implement package identity and lazy WinRT loading**

`has_package_identity()` calls `GetCurrentPackageFamilyName`; return false only for
`APPMODEL_ERROR_NO_PACKAGE` (15700), true on success, and log/return false for other
errors.

The default task loader imports lazily so non-Windows imports remain safe:

```python
async def _load_startup_task(task_id: str):
    from winrt.windows.applicationmodel import StartupTask

    return await StartupTask.get_async(task_id)
```

Normalize native enum names with `str(value).rsplit(".", 1)[-1].lower()`. Before
requesting enable, read state; return user/policy-disabled state unchanged. After
enable or disable, load/read the task again and return the observed state.

If the WinRT projection or manifest task is unavailable, return `UNAVAILABLE` with a
safe Vietnamese message and log the exception detail.

- [ ] **Step 4: Run all backend tests and lint**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_windows_autostart.py -q`

Expected: pass.

Run: `.\.venv\Scripts\python.exe -m ruff check src\platform_utils\autostart.py tests\test_windows_autostart.py`

Expected: pass.

- [ ] **Step 5: Commit StartupTask routing**

```powershell
git add src/platform_utils/autostart.py tests/test_windows_autostart.py
git commit -m "feat: support MSIX StartupTask autostart state"
```

### Task 3: Declare and bundle the MSIX startup capability

**Files:**
- Modify: `pyproject.toml`
- Modify: `scripts/package_msix.ps1`
- Modify: `tests/test_release_hardening.py`

**Interfaces:**
- Consumes: `StartupTaskAutostartBackend` and fixed TaskId from Task 2.
- Produces: bundled `winrt-Windows.ApplicationModel==3.2.1` on Windows.
- Produces: validated manifest extension for `UTHelperAutostart.exe` without parameters.

- [ ] **Step 1: Add failing dependency and manifest assertions**

```python
def test_windows_build_bundles_startup_task_projection():
    config = tomllib.loads(_read("pyproject.toml"))
    required = "winrt-Windows.ApplicationModel==3.2.1"
    assert required in config["project"]["optional-dependencies"]["windows"]
    assert required in config["tool"]["flet"]["windows"]["dependencies"]
    assert required not in config["project"]["dependencies"]


def test_msix_manifest_declares_disabled_full_trust_startup_task():
    script = _read("scripts/package_msix.ps1")
    assert 'xmlns:desktop="http://schemas.microsoft.com/appx/manifest/desktop/windows10"' in script
    assert 'xmlns:uap10="http://schemas.microsoft.com/appx/manifest/uap/windows10/10"' in script
    assert 'IgnorableNamespaces="uap rescap desktop uap10"' in script
    assert 'Category="windows.startupTask"' in script
    assert 'Executable="UTHelperAutostart.exe"' in script
    assert 'EntryPoint="Windows.FullTrustApplication"' in script
    assert 'uap10:Parameters=' not in script
    assert 'TaskId="UTHelperStartup"' in script
    assert 'Enabled="false"' in script
```

- [ ] **Step 2: Run release tests and confirm missing projection/manifest**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_release_hardening.py -q`

Expected: failures identify the missing Windows dependency and extension.

- [ ] **Step 3: Add exact Windows dependency and manifest namespaces/extension**

Add `winrt-Windows.ApplicationModel==3.2.1` to both Windows dependency lists. Extend
the manifest root with `desktop` and `uap10`, then add this child under
`Application/Extensions`:

```xml
<desktop:Extension Category="windows.startupTask"
                   Executable="UTHelperAutostart.exe"
                   EntryPoint="Windows.FullTrustApplication">
  <desktop:StartupTask TaskId="UTHelperStartup"
                       Enabled="false"
                       DisplayName="UTHelper" />
</desktop:Extension>
```

- [ ] **Step 4: Run release tests and MakeAppx validation using a verified bundle**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_release_hardening.py -q`

Expected: pass.

Run: `.\scripts\package_msix.ps1 -BundleDir build\windows -Version 2.1.0.0 -Publisher 'CN=UTHelper Development' -Output build\UTHelper-test.msix`

Expected: `makeappx pack` and `makeappx validate` exit 0 when the environment publisher
matches the test/release identity.

- [ ] **Step 5: Commit MSIX capability**

```powershell
git add pyproject.toml scripts/package_msix.ps1 tests/test_release_hardening.py
git commit -m "feat: declare MSIX startup task capability"
```

### Task 4: GUI autostart state coordinator

**Files:**
- Create: `src/gui/controllers/autostart_settings.py`
- Create: `tests/test_autostart_settings_coordinator.py`

**Interfaces:**
- Consumes: async service `get_status()` and `set_enabled(bool)` from Tasks 1-2.
- Produces: immutable `AutostartUiState(enabled, editable, success, message)`.
- Produces: `AutostartSettingsCoordinator.load() -> AutostartUiState`.
- Produces: `AutostartSettingsCoordinator.change(enabled: bool) -> AutostartUiState`.

- [ ] **Step 1: Write failing coordinator mapping tests**

```python
class FakeService:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.requests = []

    async def get_status(self):
        return self.statuses.pop(0)

    async def set_enabled(self, enabled):
        self.requests.append(enabled)
        return self.statuses.pop(0)


def status(state):
    return AutostartStatus(AutostartBackendKind.STARTUP_TASK, state)


def test_change_returns_confirmed_enabled_state():
    service = FakeService([status(AutostartState.ENABLED)])
    coordinator = AutostartSettingsCoordinator(service)
    result = asyncio.run(coordinator.change(True))
    assert result == AutostartUiState(True, True, True, "")
    assert service.requests == [True]


def test_user_disabled_state_is_not_editable_and_is_actionable():
    service = FakeService([status(AutostartState.DISABLED_BY_USER)])
    result = asyncio.run(AutostartSettingsCoordinator(service).load())
    assert result.enabled is False
    assert result.editable is False
    assert result.success is False
    assert "Task Manager" in result.message
```

```python
@pytest.mark.parametrize(
    ("state_value", "enabled", "editable", "success", "message_part"),
    [
        (AutostartState.DISABLED, False, True, True, ""),
        (AutostartState.DISABLED_BY_POLICY, False, False, False, "quản trị viên"),
        (AutostartState.UNAVAILABLE, False, False, False, "không khả dụng"),
        (AutostartState.ERROR, False, False, False, "không thể"),
    ],
)
def test_load_maps_all_non_enabled_states(
    state_value, enabled, editable, success, message_part
):
    service = FakeService([status(state_value)])
    result = asyncio.run(AutostartSettingsCoordinator(service).load())
    assert (result.enabled, result.editable, result.success) == (
        enabled,
        editable,
        success,
    )
    assert message_part in result.message
```

- [ ] **Step 2: Run the tests and confirm missing coordinator types**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_autostart_settings_coordinator.py -q`

Expected: collection fails on the missing module.

- [ ] **Step 3: Implement deterministic state mapping**

```python
@dataclass(frozen=True, slots=True)
class AutostartUiState:
    enabled: bool
    editable: bool
    success: bool
    message: str = ""


class AutostartSettingsCoordinator:
    def __init__(self, service):
        self._service = service

    async def load(self) -> AutostartUiState:
        return _to_ui_state(await self._service.get_status())

    async def change(self, enabled: bool) -> AutostartUiState:
        return _to_ui_state(await self._service.set_enabled(enabled))
```

Map `DISABLED_BY_USER` to a Task Manager/Windows Settings instruction and
`DISABLED_BY_POLICY` to an administrator-policy message. `UNAVAILABLE`/`ERROR` are
editable false and preserve the backend's safe message.

- [ ] **Step 4: Run coordinator tests and lint**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_autostart_settings_coordinator.py -q`

Expected: pass.

Run: `.\.venv\Scripts\python.exe -m ruff check src\gui\controllers\autostart_settings.py tests\test_autostart_settings_coordinator.py`

Expected: pass.

- [ ] **Step 5: Commit the GUI coordinator**

```powershell
git add src/gui/controllers/autostart_settings.py tests/test_autostart_settings_coordinator.py
git commit -m "feat: map Windows autostart state for settings UI"
```

### Task 5: Truthful Settings controls and transactional persistence

**Files:**
- Modify: `src/gui/components/settings/system_section.py`
- Modify: `src/gui/components/settings_view.py`
- Modify: `src/gui/app_controller.py`
- Create: `tests/test_settings_autostart_ui.py`

**Interfaces:**
- Consumes: `AutostartSettingsCoordinator` from Task 4.
- Produces: optional `autostart_coordinator` constructor dependency on `SettingsView`.
- Produces: async `SettingsView._reconcile_autostart()` and `_apply_autostart_change()`.

- [ ] **Step 1: Write failing control dependency and save-result tests**

```python
class FakeCoordinator:
    def __init__(self, result):
        self.result = result
        self.requests = []

    async def load(self):
        return self.result

    async def change(self, enabled):
        self.requests.append(enabled)
        return self.result


def make_settings_view(result):
    view = SettingsView.__new__(SettingsView)
    view._autostart_coordinator = FakeCoordinator(result)
    view._sw_start_with_windows = SimpleNamespace(value=True, disabled=False)
    view._sw_start_minimized = SimpleNamespace(value=True, disabled=False)
    view._autostart_status = SimpleNamespace(value="", color=None)
    view.update = lambda: None
    return view


def test_hidden_start_switch_is_scoped_and_disabled_without_autostart():
    view = SimpleNamespace()
    init_system_controls(view)
    assert view._sw_start_minimized.label == (
        "Khi khởi động cùng Windows: Ẩn xuống khay hệ thống"
    )
    assert view._sw_start_minimized.disabled is (not view._sw_start_with_windows.value)


def test_reconcile_uses_actual_os_state_and_updates_dependency():
    view = make_settings_view(AutostartUiState(False, True, True, ""))
    asyncio.run(SettingsView._reconcile_autostart(view))
    assert view._sw_start_with_windows.value is False
    assert view._sw_start_minimized.disabled is True


def test_rejected_enable_is_not_persisted(monkeypatch):
    view = make_settings_view(
        AutostartUiState(False, False, False, "Hãy bật trong Task Manager")
    )
    view._sw_start_with_windows.value = True
    original = settings.START_WITH_WINDOWS
    try:
        assert asyncio.run(SettingsView._apply_autostart_change(view)) is False
        assert settings.START_WITH_WINDOWS is original
        assert view._sw_start_with_windows.value is False
        assert "Task Manager" in view._autostart_status.value
    finally:
        settings.START_WITH_WINDOWS = original
```

```python
def test_confirmed_enable_is_persisted_after_os_success():
    view = make_settings_view(AutostartUiState(True, True, True, ""))
    original = settings.START_WITH_WINDOWS
    settings.START_WITH_WINDOWS = False
    try:
        assert asyncio.run(SettingsView._apply_autostart_change(view)) is True
        assert view._autostart_coordinator.requests == [True]
        assert settings.START_WITH_WINDOWS is True
    finally:
        settings.START_WITH_WINDOWS = original
```

- [ ] **Step 2: Run focused tests and verify missing UI wiring**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_settings_autostart_ui.py -q`

Expected: failures identify old label, missing dependency state, and missing methods.

- [ ] **Step 3: Wire controls and async reconciliation**

Give the autostart switch an `on_change` callback that immediately updates the hidden
switch dependency. Create `_autostart_status` as a small text control below both
switches. Inject the coordinator from `AppController`:

```python
self._autostart_coordinator = AutostartSettingsCoordinator(
    create_autostart_service()
)
```

`load_current_settings()` sets cached values immediately and schedules
`_reconcile_autostart` with `page.run_task` on desktop. Reconciliation updates the
actual switch, its disabled state, and the safe status message.

- [ ] **Step 4: Make save honor OS confirmation**

Before mutating `settings.START_WITH_WINDOWS`, call:

```python
async def _apply_autostart_change(self) -> bool:
    requested = bool(self._sw_start_with_windows.value)
    if requested == settings.START_WITH_WINDOWS:
        return True
    result = await self._autostart_coordinator.change(requested)
    self._sw_start_with_windows.value = result.enabled
    self._sw_start_minimized.disabled = not result.enabled
    self._autostart_status.value = result.message
    if not result.success or result.enabled != requested:
        return False
    settings.START_WITH_WINDOWS = requested
    return True
```

Call it before the common `save_settings()`. If false, set the save status to the
actionable error, update controls, and return false without closing Settings.
Persist `START_MINIMIZED` only as the visibility preference; it must not cause an OS
startup mutation by itself.

- [ ] **Step 5: Run Settings tests and existing notification/settings tests**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_settings_autostart_ui.py tests/test_settings_notification_ui.py -q`

Expected: pass.

Run: `.\.venv\Scripts\python.exe -m ruff check src\gui\components\settings\system_section.py src\gui\components\settings_view.py src\gui\app_controller.py tests\test_settings_autostart_ui.py`

Expected: pass.

- [ ] **Step 6: Commit the truthful Settings flow**

```powershell
git add src/gui/components/settings/system_section.py src/gui/components/settings_view.py src/gui/app_controller.py tests/test_settings_autostart_ui.py
git commit -m "fix: reconcile Windows autostart settings with OS state"
```

### Task 6: Visible/hidden launch policy and tray fallback

**Files:**
- Create: `src/gui/controllers/startup_visibility.py`
- Modify: `src/gui/tray.py`
- Modify: `src/gui/app_controller.py`
- Modify: `tests/test_autostart_and_tray.py`
- Modify: `tests/test_tray_assets.py`

**Interfaces:**
- Produces: `should_hide_startup(argv, start_minimized, is_mobile, tray_ready) -> bool`.
- Produces: `TrayApp.setup() -> bool` and read-only `TrayApp.is_ready`.
- Consumes: policy in `AppController._init_window()` after tray setup.

- [ ] **Step 1: Replace broad controller construction with failing policy/tray tests**

```python
@pytest.mark.parametrize(
    ("argv", "start_minimized", "is_mobile", "tray_ready", "expected"),
    [
        (["UTHelper.exe"], True, False, True, False),
        (["UTHelperAutostart.exe"], False, False, True, False),
        (["UTHelperAutostart.exe"], True, False, True, True),
        (["UTHelperAutostart.exe"], True, False, False, False),
        (["UTHelperAutostart.exe"], True, True, True, False),
    ],
)
def test_startup_visibility_matrix(argv, start_minimized, is_mobile, tray_ready, expected):
    assert should_hide_startup(argv, start_minimized, is_mobile, tray_ready) is expected


def test_tray_setup_failure_is_observable(monkeypatch):
    tray = TrayApp()
    monkeypatch.setitem(sys.modules, "pystray", None)
    assert tray.setup() is False
    assert tray.is_ready is False
```

Keep the existing close-to-tray event test, but construct the controller with
`__new__` and explicitly set only `page`, `tray`, and `_tray_balloon_shown`; do not
start the entire controller/background runtime.

- [ ] **Step 2: Run policy and tray tests and verify failures**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_autostart_and_tray.py tests/test_tray_assets.py -q`

Expected: missing policy and false-return behavior fail.

- [ ] **Step 3: Implement pure policy and observable tray readiness**

```python
def should_hide_startup(
    argv: Sequence[str],
    start_minimized: bool,
    is_mobile: bool,
    tray_ready: bool,
) -> bool:
    return (
        not is_mobile
        and "--autostart" in argv
        and start_minimized
        and tray_ready
    )
```

`TrayApp.setup()` returns true only after `_icon` has been created and its daemon
thread has started. Every exception returns false. `is_ready` returns
`self._icon is not None`.

- [ ] **Step 4: Apply policy after tray setup in AppController**

Capture `tray_ready = self.tray.setup()` on Windows and false elsewhere. Set window
visibility exactly once:

```python
self.page.window.visible = not should_hide_startup(
    sys.argv,
    settings.START_MINIMIZED,
    _is_mobile,
    tray_ready,
)
```

If an autostart-hidden launch requested hiding but `tray_ready` is false, log a
warning that the window remains visible. Ordinary `MINIMIZE_TO_TRAY` close behavior
continues to use its own setting.

- [ ] **Step 5: Run policy/controller/tray tests and lint**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_autostart_and_tray.py tests/test_tray_assets.py tests/test_gui_app_controller.py -q`

Expected: pass.

Run: `.\.venv\Scripts\python.exe -m ruff check src\gui\controllers\startup_visibility.py src\gui\tray.py src\gui\app_controller.py tests\test_autostart_and_tray.py tests\test_tray_assets.py`

Expected: pass.

- [ ] **Step 6: Commit the visibility policy**

```powershell
git add src/gui/controllers/startup_visibility.py src/gui/tray.py src/gui/app_controller.py tests/test_autostart_and_tray.py tests/test_tray_assets.py
git commit -m "fix: hide autostart launches only with a ready tray"
```

### Task 7: Real Registry integration test with strict cleanup

**Files:**
- Create: `tests/test_windows_autostart_integration.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: Run-key backend from Task 1.
- Produces: opt-in `windows_integration` pytest marker.

- [ ] **Step 1: Add the isolated integration test**

```python
@pytest.mark.windows_integration
@pytest.mark.skipif(sys.platform != "win32", reason="Windows Registry required")
def test_real_hkcu_run_round_trip_uses_exact_command():
    app_name = f"UTHelper_Test_{uuid.uuid4().hex}"
    command = subprocess.list2cmdline(
        [r"C:\Program Files\UTHelper Test\UTHelperAutostart.exe"]
    )
    backend = RunKeyAutostartBackend(command=command, app_name=app_name)
    try:
        enabled = asyncio.run(backend.set_enabled(True))
        assert enabled.state is AutostartState.ENABLED
        assert _read_run_value(app_name) == command
    finally:
        asyncio.run(backend.set_enabled(False))
    assert _read_run_value(app_name) is None
```

Register the marker:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["windows_integration: mutates a uniquely named temporary Windows resource"]
```

- [ ] **Step 2: Run the real integration test**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_windows_autostart_integration.py -q -m windows_integration`

Expected: pass and no `UTHelper_Test_*` value remains under the current user's Run
key.

- [ ] **Step 3: Commit the integration test**

```powershell
git add tests/test_windows_autostart_integration.py pyproject.toml
git commit -m "test: verify isolated Windows Run-key round trip"
```

### Task 8: Full rebuild, E2E, documentation, and completion evidence

**Files:**
- Modify: `docs/WINDOWS_EXE_PACKAGING.md`
- Modify: `REFAC_KNOWLEDGE.md`

**Interfaces:**
- Consumes: both approved implementation plans and every prior task.
- Produces: fresh bundle/installers and requirement-by-requirement evidence.

- [ ] **Step 1: Install exact Windows dependencies and run targeted tests**

Run: `.\.venv\Scripts\python.exe -m pip install -e ".[windows]"`

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_windows_autostart.py tests/test_autostart_settings_coordinator.py tests/test_settings_autostart_ui.py tests/test_autostart_and_tray.py tests/test_windows_autostart_integration.py tests/test_windows_bundle_verifier.py tests/test_release_hardening.py -q`

Expected: pass, including the unique Registry round trip on Windows.

- [ ] **Step 2: Run the full quality baseline**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src;.'; .\.venv\Scripts\python.exe -m pytest tests -q --tb=short`

Expected: all non-environment tests pass; every skip is reviewed and reported.

Run: `.\.venv\Scripts\python.exe -m ruff check src tests scripts\verify_windows_bundle.py`

Expected: pass.

- [ ] **Step 3: Rebuild from a clean Windows output**

```powershell
$bundlePath = (Join-Path (Resolve-Path .).Path "build\windows")
if (Test-Path -LiteralPath $bundlePath) {
    Remove-Item -LiteralPath $bundlePath -Recurse -Force
}
flet build windows --output build\windows --verbose
if ($LASTEXITCODE -ne 0) { throw "Flet Windows build failed" }
```

- [ ] **Step 4: Verify static content and all visibility modes**

Run: `.\.venv\Scripts\python.exe scripts\verify_windows_bundle.py build\windows`

Run: `.\scripts\test_windows_bundle_e2e.ps1 -BundleDir build\windows -ObservationSeconds 8`

Expected: static verifier and manual-visible/autostart-visible/autostart-hidden modes
all pass.

- [ ] **Step 5: Build and validate distribution artifacts**

Run: `& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' scripts\UTHelper_Setup.iss`

Run: `.\scripts\package_msix.ps1 -BundleDir build\windows -Version 2.1.0.0 -Publisher 'CN=UTHelper Development' -Output build\UTHelper-test.msix`

Expected: Inno compilation succeeds. MakeAppx pack and validation succeed, and the
staged manifest contains the StartupTask extension and `--autostart` parameter.

- [ ] **Step 6: Update operator docs and refactoring knowledge**

Document the two backends, Task Manager-disabled recovery, hidden/visible semantics,
exact build/verifier/E2E commands, and safe uninstall behavior in
`docs/WINDOWS_EXE_PACKAGING.md`.

Append the actual date, touched files, test counts, Registry result, Flet build result,
three E2E modes, Inno result, and MSIX result to `REFAC_KNOWLEDGE.md`. Record failures
or environment limitations verbatim rather than converting them to passes.

- [ ] **Step 7: Commit final evidence**

```powershell
git add docs/WINDOWS_EXE_PACKAGING.md REFAC_KNOWLEDGE.md
git commit -m "docs: record Windows autostart and packaging verification"
```

- [ ] **Step 8: Audit the completed objective**

Run: `git status --short --branch`

Run: `git log --oneline --decorate main..HEAD`

Confirm evidence exists for: crash fixed, direct bundle stable, Inno bundle stable,
Run-key enable/disable, MSIX StartupTask declaration/API mapping, truthful GUI state,
visible/hidden startup choice, tray fallback, unit tests, Registry integration, full
suite, Ruff, Flet build, Inno compilation, MakeAppx validation, E2E modes, docs, and
incremental commits. Do not mark the goal complete while any item lacks direct
evidence.
