"""
Platform-aware autostart for Windows.
Delegates to platform.autostart which handles conditional winreg import.
Kept for backward compatibility with existing imports.
"""
from platform_utils.autostart import add_to_startup, remove_from_startup

__all__ = ["add_to_startup", "remove_from_startup"]
