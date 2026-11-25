"""Compatibility exports and StartupApproved helpers for platform-aware autostart."""

from __future__ import annotations

import sys

from platform_utils.autostart import (
    APP_NAME,
    AutostartState,
    AutostartStatus,
    add_to_startup,
    create_autostart_service,
    get_autostart_status,
    remove_from_startup,
    set_autostart_enabled,
)

STARTUP_APPROVED_RUN_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
)


def is_startup_approved_disabled_by_user(app_name: str = APP_NAME) -> bool:
    """Return True when Windows Startup Apps / Task Manager disabled *app_name*.

    Windows records user startup toggles as binary values under
    ``StartupApproved\\Run`` where an odd leading status byte (e.g. ``0x03``,
    ``0x01``, ``0x05``) denotes disabled state and ``0x02`` / ``0x06`` denotes
    enabled state.
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(
                root, STARTUP_APPROVED_RUN_KEY, 0, winreg.KEY_READ
            ) as key:
                raw, _value_type = winreg.QueryValueEx(key, app_name)
        except OSError:
            continue
        if isinstance(raw, (bytes, bytearray)) and len(raw) >= 1:
            if (raw[0] & 0x01) == 1:
                return True
    return False


__all__ = [
    "AutostartState",
    "AutostartStatus",
    "STARTUP_APPROVED_RUN_KEY",
    "add_to_startup",
    "create_autostart_service",
    "get_autostart_status",
    "is_startup_approved_disabled_by_user",
    "remove_from_startup",
    "set_autostart_enabled",
]
