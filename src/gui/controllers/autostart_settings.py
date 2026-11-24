"""Map platform autostart state into deterministic Settings UI state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.autostart import is_startup_approved_disabled_by_user
from platform_utils.autostart import (
    APP_NAME,
    AUTOSTART_RUNNER_NAME,
    AutostartServiceProtocol,
    AutostartState,
    AutostartStatus,
)


@dataclass(frozen=True, slots=True)
class AutostartUiState:
    enabled: bool
    editable: bool
    success: bool
    message: str = ""
    confirmed: bool = True


def _to_ui_state(
    status: AutostartStatus, *, requested: bool | None = None
) -> AutostartUiState:
    default_messages = {
        AutostartState.DISABLED_BY_USER: (
            "Windows đã tắt mục này. Hãy bật lại trong Startup Apps hoặc "
            "Task Manager."
        ),
        AutostartState.DISABLED_BY_POLICY: (
            "Quản trị viên đang chặn ứng dụng khởi động cùng Windows."
        ),
        AutostartState.ENABLED_BY_POLICY: (
            "Ứng dụng đang được bật bởi chính sách Windows và không thể đổi tại đây."
        ),
        AutostartState.UNAVAILABLE: "Khởi động cùng Windows không khả dụng.",
        AutostartState.ERROR: "Không thể đọc hoặc thay đổi trạng thái Windows.",
    }
    regular = status.state in {
        AutostartState.ENABLED,
        AutostartState.DISABLED,
    }
    result = AutostartUiState(
        enabled=status.enabled,
        editable=regular,
        success=regular,
        message=status.message or default_messages.get(status.state, ""),
        confirmed=status.state not in {
            AutostartState.UNAVAILABLE,
            AutostartState.ERROR,
        },
    )
    if requested is not None and result.enabled != requested:
        return AutostartUiState(
            enabled=result.enabled,
            editable=result.editable,
            success=False,
            message=result.message
            or "Windows không xác nhận trạng thái khởi động đã yêu cầu.",
            confirmed=result.confirmed,
        )
    return result


class AutostartSettingsCoordinator:
    def __init__(self, service: AutostartServiceProtocol) -> None:
        self._service = service

    async def load(self) -> AutostartUiState:
        return _to_ui_state(await self._service.get_status())

    async def change(self, enabled: bool) -> AutostartUiState:
        status = await self._service.set_enabled(enabled)
        return _to_ui_state(status, requested=enabled)


def _is_packaged_windows_run_key_service(service: AutostartServiceProtocol) -> bool:
    if getattr(service, "backend", None) != "run_key":
        return False
    command = str(getattr(service, "command", "")).strip().casefold()
    if not command or "--autostart" in command:
        return False
    return AUTOSTART_RUNNER_NAME.casefold() in command


async def apply_initial_autostart_default(
    service: AutostartServiceProtocol,
    settings_value,
    persist,
    *,
    startup_approved_disabled_probe: Callable[[str], bool] = (
        is_startup_approved_disabled_by_user
    ),
) -> AutostartUiState | None:
    """Apply the Windows default once without overriding an existing choice.

    On unpackaged Windows builds where ``AUTOSTART_DEFAULT_APPLIED`` and
    ``START_WITH_WINDOWS`` are both ``True``, also self-heal a missing or legacy
    ``--autostart`` ``HKCU\\...\\Run\\UTHelper`` entry (for example after an
    older MSI ``MajorUpgrade`` deleted ``Run\\UTHelper``), unless the user
    explicitly disabled startup in Windows Startup Apps / Task Manager
    (``StartupApproved\\Run``).
    """
    if getattr(settings_value, "AUTOSTART_DEFAULT_APPLIED", False):
        if not getattr(settings_value, "START_WITH_WINDOWS", False):
            return None
        if not _is_packaged_windows_run_key_service(service):
            return None
        app_name = str(getattr(service, "app_name", APP_NAME) or APP_NAME)
        if startup_approved_disabled_probe(app_name):
            return None
        current = await service.get_status()
        if current.state is not AutostartState.DISABLED:
            return None
        repaired = await service.set_enabled(True)
        return _to_ui_state(repaired, requested=True)

    result = _to_ui_state(await service.set_enabled(True), requested=True)
    if not result.confirmed:
        # A transient backend/readback failure is not a user decision. Leave
        # the marker unset so a later launch can retry safely.
        return result
    previous_enabled = settings_value.START_WITH_WINDOWS
    settings_value.START_WITH_WINDOWS = result.enabled
    settings_value.AUTOSTART_DEFAULT_APPLIED = True
    if persist():
        return result

    settings_value.START_WITH_WINDOWS = previous_enabled
    settings_value.AUTOSTART_DEFAULT_APPLIED = False
    return AutostartUiState(
        enabled=previous_enabled,
        editable=result.editable,
        success=False,
        message="Không thể lưu mặc định khởi động cùng Windows.",
        confirmed=result.confirmed,
    )
