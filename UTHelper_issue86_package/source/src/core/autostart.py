"""Compatibility exports for the platform-aware autostart service."""

from platform_utils.autostart import (
    AutostartState,
    AutostartStatus,
    add_to_startup,
    create_autostart_service,
    get_autostart_status,
    remove_from_startup,
    set_autostart_enabled,
)

__all__ = [
    "AutostartState",
    "AutostartStatus",
    "add_to_startup",
    "create_autostart_service",
    "get_autostart_status",
    "remove_from_startup",
    "set_autostart_enabled",
]
