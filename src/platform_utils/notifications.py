"""
Platform-aware notification factory.
Auto-selects the right notifier backend based on current platform.
"""
import logging
from notifiers.base import BaseNotifier

logger = logging.getLogger(__name__)


class LogNotifier(BaseNotifier):
    """Fallback notifier that only logs notifications."""

    def notify(self, assignments):
        for a in assignments:
            title = getattr(a, 'title', '') or (a.get('title', '') if isinstance(a, dict) else '')
            logger.info("NOTIFICATION (log-only): %s", title)
        return True


def get_platform_notifier(tray_app=None) -> BaseNotifier:
    """
    Factory: returns the right notifier for the current platform.
    
    - Windows → WindowsNotifier (toast notifications)
    - Android → MobileNotifier (local notifications via flet_notifications)
    - Fallback → LogNotifier (log-only)
    """
    from platform_utils import IS_WINDOWS, IS_MOBILE

    if IS_WINDOWS:
        try:
            from notifiers.windows import WindowsNotifier
            return WindowsNotifier(tray_app=tray_app)
        except ImportError:
            logger.warning("WindowsNotifier not available, falling back to log-only")
            return LogNotifier()

    if IS_MOBILE:
        try:
            from notifiers.mobile import MobileNotifier
            return MobileNotifier()
        except ImportError:
            logger.warning("MobileNotifier not available, falling back to log-only")
            return LogNotifier()

    # Other platforms (Linux, macOS)
    return LogNotifier()
