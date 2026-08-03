import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import platform_utils.autostart as autostart
from platform_utils.autostart import (
    AutostartState,
    RunKeyAutostartBackend,
    StartupTaskAutostartBackend,
    build_autostart_command,
)


def run(coro):
    return asyncio.run(coro)


def test_packaged_flet_command_targets_argument_free_sibling_alias(tmp_path):
    executable = tmp_path / "Program Files" / "UTHelper" / "UTHelper.exe"

    command = build_autostart_command(executable=executable)

    assert command == subprocess.list2cmdline(
        [str(executable.with_name("UTHelperAutostart.exe"))]
    )
    assert "--autostart" not in command


def test_packaged_alias_resolves_to_itself(tmp_path):
    executable = tmp_path / "UTHelperAutostart.exe"

    assert build_autostart_command(executable=executable) == subprocess.list2cmdline(
        [str(executable)]
    )


def test_development_command_uses_pythonw_real_script_and_flag(tmp_path):
    python = tmp_path / "Python" / "python.exe"
    pythonw = python.with_name("pythonw.exe")
    script = tmp_path / "project" / "main.py"
    python.parent.mkdir(parents=True)
    script.parent.mkdir()
    python.write_bytes(b"")
    pythonw.write_bytes(b"")
    script.write_text("pass", encoding="utf-8")

    command = build_autostart_command(executable=python, argv0=str(script))

    assert command == subprocess.list2cmdline(
        [str(pythonw), str(script.resolve()), "--autostart"]
    )


def test_development_command_rejects_missing_entry_script(tmp_path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"")

    try:
        build_autostart_command(executable=python, argv0=str(tmp_path / "missing.py"))
    except autostart.AutostartConfigurationError as exc:
        assert "entry script" in str(exc)
    else:
        raise AssertionError("missing development entry script was accepted")


def backend(values, command='"C:\\Program Files\\UTHelper\\UTHelperAutostart.exe"'):
    def delete(name):
        values.pop(name, None)

    return RunKeyAutostartBackend(
        command=command,
        reader=values.get,
        writer=values.__setitem__,
        deleter=delete,
    )


def test_run_key_reports_only_matching_command_as_enabled():
    command = '"C:\\Program Files\\UTHelper\\UTHelperAutostart.exe"'
    values = {"UTHelper": command.lower()}

    assert run(backend(values, command).get_status()).state is AutostartState.ENABLED

    values["UTHelper"] = '"C:\\Temp\\pythonw.exe" "main.py" --autostart'
    status = run(backend(values, command).get_status())
    assert status.state is AutostartState.DISABLED
    assert "không còn hợp lệ" in status.message


def test_run_key_enable_reads_back_and_removes_legacy_value():
    values = {"UTHElearningAlert": '"C:\\Old\\UTHelper.exe" --autostart'}
    service = backend(values)

    status = run(service.set_enabled(True))

    assert status.state is AutostartState.ENABLED
    assert values == {
        "UTHelper": '"C:\\Program Files\\UTHelper\\UTHelperAutostart.exe"'
    }


def test_run_key_write_failure_is_reported_without_false_success():
    def fail_write(name, value):
        raise PermissionError("denied")

    service = RunKeyAutostartBackend(
        command='"C:\\UTHelperAutostart.exe"',
        reader=lambda name: None,
        writer=fail_write,
        deleter=lambda name: None,
    )

    status = run(service.set_enabled(True))

    assert status.state is AutostartState.ERROR
    assert not status.enabled
    assert "Không thể bật" in status.message
    assert "denied" in status.technical_detail


def test_run_key_disable_is_idempotent_and_cleans_legacy():
    values = {"UTHElearningAlert": "legacy"}
    service = backend(values)

    assert run(service.set_enabled(False)).state is AutostartState.DISABLED
    assert run(service.set_enabled(False)).state is AutostartState.DISABLED
    assert values == {}


class FakeStartupTask:
    def __init__(self, state, enable_result=None):
        self.state = state
        self.enable_result = enable_result
        self.disable_calls = 0

    async def request_enable_async(self):
        if self.enable_result is not None:
            self.state = self.enable_result
        return self.state

    def disable(self):
        self.disable_calls += 1
        self.state = "disabled"


async def task_loader(task):
    return task


def test_startup_task_maps_all_actionable_states():
    expected = {
        "enabled": AutostartState.ENABLED,
        "enabled_by_policy": AutostartState.ENABLED_BY_POLICY,
        "disabled": AutostartState.DISABLED,
        "disabled_by_user": AutostartState.DISABLED_BY_USER,
        "disabled_by_policy": AutostartState.DISABLED_BY_POLICY,
    }
    for native_state, state in expected.items():
        task = FakeStartupTask(native_state)
        service = StartupTaskAutostartBackend(loader=lambda _task_id, t=task: task_loader(t))
        assert run(service.get_status()).state is state


def test_startup_task_does_not_override_user_rejection():
    task = FakeStartupTask("disabled", enable_result="disabled_by_user")
    service = StartupTaskAutostartBackend(loader=lambda _task_id: task_loader(task))

    status = run(service.set_enabled(True))

    assert status.state is AutostartState.DISABLED_BY_USER
    assert "Windows" in status.message


def test_startup_task_disable_reads_back_result():
    task = FakeStartupTask("enabled")
    service = StartupTaskAutostartBackend(loader=lambda _task_id: task_loader(task))

    status = run(service.set_enabled(False))

    assert status.state is AutostartState.DISABLED
    assert task.disable_calls == 1


def test_factory_selects_startup_task_for_msix(monkeypatch):
    monkeypatch.setattr(autostart, "has_package_identity", lambda: True)

    service = autostart.create_autostart_service(platform_name="win32")

    assert isinstance(service, StartupTaskAutostartBackend)


def test_factory_selects_run_key_for_unpacked_windows(monkeypatch, tmp_path):
    runner = tmp_path / "UTHelper.exe"
    monkeypatch.setattr(autostart, "has_package_identity", lambda: False)
    monkeypatch.setattr(autostart, "get_current_process_executable", lambda: runner)

    service = autostart.create_autostart_service(platform_name="win32")

    assert isinstance(service, RunKeyAutostartBackend)
    assert service.command.endswith("UTHelperAutostart.exe")


def test_factory_returns_unavailable_service_off_windows():
    status = run(autostart.create_autostart_service(platform_name="linux").get_status())

    assert status.state is AutostartState.UNAVAILABLE
    assert not status.enabled


def test_current_process_lookup_uses_windows_api_not_parent(monkeypatch):
    class Kernel32:
        @staticmethod
        def GetModuleFileNameW(module, buffer, size):
            buffer.value = r"C:\Apps\UTHelper.exe"
            return len(buffer.value)

    monkeypatch.setattr(autostart.ctypes, "windll", SimpleNamespace(kernel32=Kernel32()))

    assert autostart.get_current_process_executable(platform_name="win32") == Path(
        r"C:\Apps\UTHelper.exe"
    )
