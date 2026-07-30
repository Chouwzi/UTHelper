"""
Platform detection and abstraction layer for cross-platform support.
Provides IS_MOBILE, IS_WINDOWS, IS_DESKTOP flags and detect_platform().
"""
import sys
import logging

logger = logging.getLogger(__name__)

# Static detection (available at import time)
IS_WINDOWS = sys.platform == 'win32'
IS_ANDROID = hasattr(sys, '_ANDROID_') or 'android' in sys.platform.lower()
IS_IOS = hasattr(sys, '_IOS_')
IS_MOBILE = IS_ANDROID or IS_IOS
IS_DESKTOP = not IS_MOBILE


def detect_platform(page=None):
    """
    Runtime platform detection using Flet page object.
    Call this after page is available for most accurate detection.
    Updates module-level flags.
    """
    global IS_MOBILE, IS_DESKTOP, IS_ANDROID, IS_IOS

    if page and hasattr(page, 'platform'):
        try:
            import flet as ft
            if page.platform in (ft.PagePlatform.ANDROID,):
                IS_ANDROID = True
                IS_MOBILE = True
                IS_DESKTOP = False
            elif page.platform in (ft.PagePlatform.IOS,):
                IS_IOS = True
                IS_MOBILE = True
                IS_DESKTOP = False
            else:
                IS_MOBILE = False
                IS_DESKTOP = True
        except Exception:
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

    logger.info(
        "Platform detected: %s (mobile=%s, desktop=%s)",
        "android" if IS_ANDROID else "ios" if IS_IOS else "windows" if IS_WINDOWS else "other",
        IS_MOBILE, IS_DESKTOP,
    )
