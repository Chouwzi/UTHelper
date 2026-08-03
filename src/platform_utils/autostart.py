"""Truthful Windows autostart state for unpackaged and MSIX distributions."""

from __future__ import annotations

import asyncio
import ctypes
import logging
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "UTHelper"
LEGACY_APP_NAME = "UTHElearningAlert"
AUTOSTART_RUNNER_NAME = "UTHelperAutostart.exe"
STARTUP_TASK_ID = "UTHelperStartup"


class AutostartConfigurationError(RuntimeError):
    """Raised when no safe launch command can be built."""


class AutostartState(str, Enum):
    ENABLED = "enabled"
    ENABLED_BY_POLICY = "enabled_by_policy"
    DISABLED = "disabled"
    DISABLED_BY_USER = "disabled_by_user"
    DISABLED_BY_POLICY = "disabled_by_policy"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AutostartStatus:
    backend: str
    state: AutostartState
    message: str = ""
    technical_detail: str = ""

    @property
    def enabled(self) -> bool:
        return self.state in {
            AutostartState.ENABLED,
            AutostartState.ENABLED_BY_POLICY,
        }

    @property
    def changeable(self) -> bool:
        return self.state not in {
            AutostartState.DISABLED_BY_USER,
            AutostartState.DISABLED_BY_POLICY,
            AutostartState.ENABLED_BY_POLICY,
            AutostartState.UNAVAILABLE,
            AutostartState.ERROR,
        }


class AutostartServiceProtocol(Protocol):
    async def get_status(self) -> AutostartStatus: ...

    async def set_enabled(self, enabled: bool) -> AutostartStatus: ...


def get_current_process_executable(platform_name: str | None = None) -> Path:
    """Return the current process image; never inspect the parent process."""
    platform_name = platform_name or sys.platform
    if platform_name != "win32":
        return Path(sys.executable).resolve()

    kernel32 = ctypes.windll.kernel32
    size = 260
    while size <= 32768:
        buffer = ctypes.create_unicode_buffer(size)
        length = kernel32.GetModuleFileNameW(None, buffer, size)
        if length == 0:
            raise ctypes.WinError()
        if length < size - 1:
            return Path(buffer.value)
        size *= 2
    raise AutostartConfigurationError("Current executable path exceeds Windows limit")


def build_autostart_command(
    *,
    executable: Path | None = None,
    argv0: str | None = None,
) -> str:
    """Build the canonical Run value for source or packaged execution."""
    executable = Path(executable or get_current_process_executable())
    stem = executable.stem.casefold()
    if stem in {"python", "pythonw"}:
        script = Path(argv0 if argv0 is not None else sys.argv[0]).resolve()
        if not script.is_file():
            raise AutostartConfigurationError(
                f"Development entry script does not exist: {script}"
            )
        pythonw = executable.with_name("pythonw.exe")
        interpreter = pythonw if pythonw.is_file() else executable
        return subprocess.list2cmdline(
            [str(interpreter), str(script), "--autostart"]
        )

    alias = (
        executable
        if executable.name.casefold() == AUTOSTART_RUNNER_NAME.casefold()
        else executable.with_name(AUTOSTART_RUNNER_NAME)
    )
    return subprocess.list2cmdline([str(alias)])


def _read_run_value(name: str) -> str | None:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ
        ) as key:
            value, _value_type = winreg.QueryValueEx(key, name)
            return str(value)
    except FileNotFoundError:
        return None


def _write_run_value(name: str, value: str) -> None:
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _delete_run_value(name: str) -> None:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        return


class RunKeyAutostartBackend:
    backend = "run_key"

    def __init__(
        self,
        *,
        command: str,
        app_name: str = APP_NAME,
        legacy_app_name: str = LEGACY_APP_NAME,
        reader: Callable[[str], str | None] = _read_run_value,
        writer: Callable[[str, str], None] = _write_run_value,
        deleter: Callable[[str], None] = _delete_run_value,
    ) -> None:
        self.command = command
        self.app_name = app_name
        self.legacy_app_name = legacy_app_name
        self._reader = reader
        self._writer = writer
        self._deleter = deleter

    async def get_status(self) -> AutostartStatus:
        try:
            value = self._reader(self.app_name)
        except Exception as exc:
            logger.exception("Cannot read Windows autostart Run value")
            return AutostartStatus(
                self.backend,
                AutostartState.ERROR,
                "Không thể đọc trạng thái khởi động cùng Windows.",
                repr(exc),
            )
        if value is None:
            return AutostartStatus(self.backend, AutostartState.DISABLED)
        if value.strip().casefold() == self.command.strip().casefold():
            return AutostartStatus(self.backend, AutostartState.ENABLED)
        return AutostartStatus(
            self.backend,
            AutostartState.DISABLED,
            "Cấu hình khởi động cũ không còn hợp lệ; hãy bật lại để sửa.",
            "Run value does not match the canonical command",
        )

    async def set_enabled(self, enabled: bool) -> AutostartStatus:
        try:
            if enabled:
                self._writer(self.app_name, self.command)
            else:
                self._deleter(self.app_name)
            self._deleter(self.legacy_app_name)
        except Exception as exc:
            action = "bật" if enabled else "tắt"
            logger.exception("Cannot %s Windows autostart Run value", action)
            return AutostartStatus(
                self.backend,
                AutostartState.ERROR,
                f"Không thể {action} khởi động cùng Windows.",
                repr(exc),
            )

        status = await self.get_status()
        if status.state is AutostartState.ERROR:
            return status
        if status.enabled != enabled:
            return AutostartStatus(
                self.backend,
                AutostartState.ERROR,
                "Windows không lưu được trạng thái khởi động đã yêu cầu.",
                f"Read-back enabled={status.enabled}, requested={enabled}",
            )
        return status


def _normalize_startup_task_state(native_state: object) -> AutostartState:
    raw = getattr(native_state, "name", native_state)
    name = str(raw).rsplit(".", 1)[-1].casefold().replace("-", "_")
    compact = name.replace("_", "")
    mapping = {
        "enabled": AutostartState.ENABLED,
        "enabledbypolicy": AutostartState.ENABLED_BY_POLICY,
        "disabled": AutostartState.DISABLED,
        "disabledbyuser": AutostartState.DISABLED_BY_USER,
        "disabledbypolicy": AutostartState.DISABLED_BY_POLICY,
    }
    return mapping.get(compact, AutostartState.ERROR)


def _startup_task_status(native_state: object) -> AutostartStatus:
    state = _normalize_startup_task_state(native_state)
    messages = {
        AutostartState.DISABLED_BY_USER: (
            "Windows đã chặn mục khởi động này. Hãy bật lại trong Startup Apps "
            "hoặc Task Manager."
        ),
        AutostartState.DISABLED_BY_POLICY: (
            "Chính sách Windows đang chặn ứng dụng khởi động tự động."
        ),
        AutostartState.ENABLED_BY_POLICY: (
            "Chính sách Windows đang bắt buộc ứng dụng khởi động tự động."
        ),
        AutostartState.ERROR: "Windows trả về trạng thái StartupTask không xác định.",
    }
    return AutostartStatus(
        "startup_task",
        state,
        messages.get(state, ""),
        "" if state is not AutostartState.ERROR else repr(native_state),
    )


async def _load_startup_task(task_id: str):
    from winrt.windows.applicationmodel import StartupTask

    return await StartupTask.get_async(task_id)


class StartupTaskAutostartBackend:
    backend = "startup_task"

    def __init__(
        self,
        *,
        loader: Callable[[str], Awaitable[object]] = _load_startup_task,
        task_id: str = STARTUP_TASK_ID,
    ) -> None:
        self._loader = loader
        self.task_id = task_id

    async def _task(self):
        return await self._loader(self.task_id)

    async def get_status(self) -> AutostartStatus:
        try:
            task = await self._task()
            return _startup_task_status(task.state)
        except Exception as exc:
            logger.exception("Cannot read Windows StartupTask")
            return AutostartStatus(
                self.backend,
                AutostartState.ERROR,
                "Không thể đọc trạng thái Startup Apps của Windows.",
                repr(exc),
            )

    async def set_enabled(self, enabled: bool) -> AutostartStatus:
        try:
            task = await self._task()
            if enabled:
                result = await task.request_enable_async()
                status = _startup_task_status(result)
                if not status.enabled:
                    return status
            else:
                task.disable()
            status = _startup_task_status(task.state)
        except Exception as exc:
            logger.exception("Cannot change Windows StartupTask")
            action = "bật" if enabled else "tắt"
            return AutostartStatus(
                self.backend,
                AutostartState.ERROR,
                f"Không thể {action} khởi động cùng Windows.",
                repr(exc),
            )
        if status.enabled != enabled:
            return AutostartStatus(
                self.backend,
                AutostartState.ERROR,
                "Windows không lưu được trạng thái Startup Apps đã yêu cầu.",
                f"Read-back enabled={status.enabled}, requested={enabled}",
            )
        return status


class UnavailableAutostartBackend:
    backend = "unavailable"

    async def get_status(self) -> AutostartStatus:
        return AutostartStatus(
            self.backend,
            AutostartState.UNAVAILABLE,
            "Khởi động cùng Windows chỉ khả dụng trên Windows.",
        )

    async def set_enabled(self, enabled: bool) -> AutostartStatus:
        return await self.get_status()


def has_package_identity() -> bool:
    """Return whether the current process has Windows package identity."""
    if sys.platform != "win32":
        return False
    length = ctypes.c_uint32(0)
    result = ctypes.windll.kernel32.GetCurrentPackageFullName(
        ctypes.byref(length), None
    )
    appmodel_error_no_package = 15700
    error_insufficient_buffer = 122
    if result == appmodel_error_no_package:
        return False
    if result == error_insufficient_buffer:
        return True
    if result == 0:
        return True
    raise ctypes.WinError(result)


def create_autostart_service(
    *, platform_name: str | None = None
) -> AutostartServiceProtocol:
    platform_name = platform_name or sys.platform
    if platform_name != "win32":
        return UnavailableAutostartBackend()
    if has_package_identity():
        return StartupTaskAutostartBackend()
    command = build_autostart_command(
        executable=get_current_process_executable()
    )
    return RunKeyAutostartBackend(command=command)


async def get_autostart_status() -> AutostartStatus:
    return await create_autostart_service().get_status()


async def set_autostart_enabled(enabled: bool) -> AutostartStatus:
    return await create_autostart_service().set_enabled(enabled)


def _run_compat(coro, *, expected_enabled: bool) -> bool:
    try:
        status = asyncio.run(coro)
        return (
            status.state is not AutostartState.ERROR
            and status.enabled is expected_enabled
        )
    except Exception:
        logger.exception("Legacy synchronous autostart call failed")
        return False


def add_to_startup(app_name: str = APP_NAME) -> bool:
    """Compatibility wrapper for older synchronous callers."""
    service = create_autostart_service()
    if isinstance(service, RunKeyAutostartBackend):
        service.app_name = app_name
    return _run_compat(service.set_enabled(True), expected_enabled=True)


def remove_from_startup(app_name: str = APP_NAME) -> bool:
    """Compatibility wrapper for older synchronous callers."""
    service = create_autostart_service()
    if isinstance(service, RunKeyAutostartBackend):
        service.app_name = app_name
    return _run_compat(service.set_enabled(False), expected_enabled=False)
