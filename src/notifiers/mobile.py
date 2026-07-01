"""
Mobile (Android/iOS) local notification handler.

On Android:
  - Uses `flet-android-notifications` for native AlarmManager scheduling,
    foreground services, and immediate push notifications.
  - Falls back to `flet_notifications` if android package unavailable.
On iOS:
  - Uses `flet_notifications` when available.
  - Falls back to logging.
"""
import logging
from notifiers.base import BaseNotifier
from platform_utils import IS_ANDROID
from core.display_utils import clean_course_name

logger = logging.getLogger(__name__)


class MobileNotifier(BaseNotifier):
    """
    Android/iOS local notification notifier.
    Prefers flet-android-notifications on Android for background support.
    Falls back to flet_notifications, then log-only mode.
    """

    def __init__(self):
        self._notifier = None
        self._android_notif = None
        self._backend = "none"

        # Try Android-specific package first (has AlarmManager + Foreground Service)
        if IS_ANDROID:
            try:
                from flet_android_notifications import FletAndroidNotifications
                self._android_notif = FletAndroidNotifications()
                self._backend = "flet-android-notifications"
                logger.info("MobileNotifier: using flet-android-notifications (Android)")
            except ImportError:
                logger.info(
                    "MobileNotifier: flet-android-notifications not installed, "
                    "trying flet_notifications fallback"
                )

        # Fallback to generic flet_notifications
        if self._android_notif is None:
            try:
                from flet_notifications import FletNotifications
                self._notifier = FletNotifications()
                self._backend = "flet_notifications"
                logger.info("MobileNotifier: using flet_notifications")
            except ImportError:
                logger.warning(
                    "MobileNotifier: no notification package available. "
                    "Install flet-android-notifications (Android) or flet-notifications."
                )

    @property
    def backend_name(self) -> str:
        """Return the active backend name for UI display."""
        return self._backend

    async def setup(self, page):
        """Request notification permissions on mobile."""
        if self._android_notif and hasattr(self._android_notif, 'request_permissions'):
            try:
                await self._android_notif.request_permissions()
                logger.info("Android notification permissions granted")
            except Exception as e:
                logger.warning("Failed to request Android permissions: %s", e)
        elif self._notifier and hasattr(self._notifier, 'request_permissions'):
            try:
                await self._notifier.request_permissions()
                logger.info("Notification permissions granted")
            except Exception as e:
                logger.warning("Failed to request notification permissions: %s", e)

    def notify(self, assignments) -> bool:
        """Send local notifications for each assignment."""
        if not assignments:
            return True

        success = False
        for i, a in enumerate(assignments[:5]):  # Max 5 notifications
            title = _get_str(a, 'title')
            course = clean_course_name(_get_str(a, 'course_name') or _get_str(a, 'course'))
            remaining = _get_str(a, 'remaining')

            notif_title = title
            notif_body = course
            if remaining:
                notif_body += f" - Còn {remaining}"

            if self._android_notif:
                try:
                    self._android_notif.show_notification(
                        id=5000 + i,  # Unique per notification
                        title=notif_title,
                        body=notif_body,
                    )
                    success = True
                except Exception as e:
                    logger.warning("Android notification failed: %s", e)
            elif self._notifier:
                try:
                    self._notifier.show_notification(
                        title=notif_title,
                        body=notif_body,
                    )
                    success = True
                except Exception as e:
                    logger.warning("Mobile notification failed: %s", e)
            else:
                logger.info("MOBILE (log-only): %s - %s", notif_title, notif_body)
                success = True

        return success


def _get_str(obj, key, default='') -> str:
    """Get string attribute from Assignment or dict."""
    if isinstance(obj, dict):
        return str(obj.get(key, default) or default)
    return str(getattr(obj, key, default) or default)
