"""Runtime capability detection and independently selectable UI profiles.

``IS_*`` flags describe the real runtime and must guard native integrations.
``UI_*`` flags describe presentation only and may be overridden for desktop
preview with ``UTHELPER_UI_PLATFORM=android|ios|desktop``.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping

logger = logging.getLogger(__name__)

_UI_PLATFORM_ENV = "UTHELPER_UI_PLATFORM"
_VALID_UI_PLATFORMS = frozenset({"android", "ios", "desktop"})


# Runtime capability flags. These must never be changed by the UI override.
IS_WINDOWS = sys.platform == "win32"
IS_ANDROID = hasattr(sys, "_ANDROID_") or "android" in sys.platform.lower()
IS_IOS = hasattr(sys, "_IOS_")
IS_MOBILE = IS_ANDROID or IS_IOS
IS_DESKTOP = not IS_MOBILE


def _runtime_ui_platform() -> str:
    if IS_ANDROID:
        return "android"
    if IS_IOS:
        return "ios"
    return "desktop"


def _read_ui_override(environ: Mapping[str, str] | None = None) -> str | None:
    source = os.environ if environ is None else environ
    value = source.get(_UI_PLATFORM_ENV, "").strip().lower()
    if not value:
        return None
    if value not in _VALID_UI_PLATFORMS:
        logger.warning(
            "Ignoring invalid %s; expected android, ios, or desktop",
            _UI_PLATFORM_ENV,
        )
        return None
    return value


def _sync_ui_profile(
    environ: Mapping[str, str] | None = None,
    *,
    detected_platform: str | None = None,
) -> None:
    global UI_PLATFORM_OVERRIDE, UI_PLATFORM
    global UI_IS_ANDROID, UI_IS_IOS, UI_IS_MOBILE, UI_IS_DESKTOP

    UI_PLATFORM_OVERRIDE = _read_ui_override(environ)
    UI_PLATFORM = (
        UI_PLATFORM_OVERRIDE or detected_platform or _runtime_ui_platform()
    )
    UI_IS_ANDROID = UI_PLATFORM == "android"
    UI_IS_IOS = UI_PLATFORM == "ios"
    UI_IS_MOBILE = UI_IS_ANDROID or UI_IS_IOS
    UI_IS_DESKTOP = not UI_IS_MOBILE


UI_PLATFORM_OVERRIDE: str | None
UI_PLATFORM: str
UI_IS_ANDROID: bool
UI_IS_IOS: bool
UI_IS_MOBILE: bool
UI_IS_DESKTOP: bool
_sync_ui_profile()


def _page_ui_platform(page) -> str | None:
    if not page or not hasattr(page, "platform"):
        return None
    try:
        import flet as ft

        if page.platform == ft.PagePlatform.ANDROID:
            return "android"
        if page.platform == ft.PagePlatform.IOS:
            return "ios"
        return "desktop"
    except Exception:
        logger.debug("Flet UI platform detection failed", exc_info=True)
        return None


def detect_platform(page=None):
    """Resolve presentation from Flet without changing runtime capabilities.

    A UI override changes only ``UI_*``. Native notification, background,
    update, tray, and filesystem decisions continue to use the real ``IS_*``
    capability flags.
    """
    _sync_ui_profile(detected_platform=_page_ui_platform(page))
    runtime_name = (
        "android"
        if IS_ANDROID
        else "ios"
        if IS_IOS
        else "windows"
        if IS_WINDOWS
        else "other"
    )
    logger.info(
        "Platform detected: runtime=%s, ui=%s, ui_override=%s",
        runtime_name,
        UI_PLATFORM,
        UI_PLATFORM_OVERRIDE is not None,
    )
