"""Map platform autostart state into deterministic Settings UI state."""

from __future__ import annotations

from dataclasses import dataclass

from platform_utils.autostart import (
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
