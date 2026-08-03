import asyncio

import pytest

from gui.controllers.autostart_settings import (
    AutostartSettingsCoordinator,
    AutostartUiState,
)
from platform_utils.autostart import AutostartState, AutostartStatus


class FakeService:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.requests = []

    async def get_status(self):
        return self.statuses.pop(0)

    async def set_enabled(self, enabled):
        self.requests.append(enabled)
        return self.statuses.pop(0)


def status(state, message=""):
    return AutostartStatus("test", state, message)


def test_change_returns_confirmed_enabled_state():
    service = FakeService([status(AutostartState.ENABLED)])
    coordinator = AutostartSettingsCoordinator(service)

    result = asyncio.run(coordinator.change(True))

    assert result == AutostartUiState(True, True, True, "")
    assert service.requests == [True]


def test_change_rejects_mismatched_readback():
    service = FakeService([status(AutostartState.DISABLED)])

    result = asyncio.run(AutostartSettingsCoordinator(service).change(True))

    assert not result.success
    assert not result.enabled
    assert "không xác nhận" in result.message


def test_user_disabled_state_is_not_editable_and_is_actionable():
    service = FakeService([status(AutostartState.DISABLED_BY_USER)])

    result = asyncio.run(AutostartSettingsCoordinator(service).load())

    assert result.enabled is False
    assert result.editable is False
    assert result.success is False
    assert "Task Manager" in result.message


@pytest.mark.parametrize(
    ("state_value", "enabled", "editable", "success", "message_part"),
    [
        (AutostartState.DISABLED, False, True, True, ""),
        (AutostartState.ENABLED_BY_POLICY, True, False, False, "chính sách"),
        (AutostartState.DISABLED_BY_POLICY, False, False, False, "quản trị viên"),
        (AutostartState.UNAVAILABLE, False, False, False, "không khả dụng"),
        (AutostartState.ERROR, False, False, False, "Không thể"),
    ],
)
def test_load_maps_nonstandard_states(
    state_value, enabled, editable, success, message_part
):
    service = FakeService([status(state_value)])

    result = asyncio.run(AutostartSettingsCoordinator(service).load())

    assert (result.enabled, result.editable, result.success) == (
        enabled,
        editable,
        success,
    )
    assert message_part.casefold() in result.message.casefold()


def test_backend_safe_message_is_preserved_for_error():
    service = FakeService(
        [status(AutostartState.ERROR, "Không thể đọc trạng thái Windows.")]
    )

    result = asyncio.run(AutostartSettingsCoordinator(service).load())

    assert result.message == "Không thể đọc trạng thái Windows."
