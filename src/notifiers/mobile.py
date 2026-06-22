"""
Mobile (Android/iOS) local notification handler.

On Android:
  - Uses `flet-android-notifications` for native AlarmManager scheduling,
    foreground services, and immediate push notifications.
  - Falls back to `flet_notifications` if android package unavailable.
On iOS:
  - Uses `flet_notifications` (LocalNotifications) when available.
  - Falls back to logging.
"""
import logging
from notifiers.base import BaseNotifier
from platform_utils import IS_ANDROID
from core.display_utils import clean_course_name

logger = logging.getLogger(__name__)

# Android notification channel constants
_CHANNEL_ID = "uthelper_deadlines"
_CHANNEL_NAME = "Deadline Reminders"
_CHANNEL_DESC = "Thông báo nhắc deadline bài tập UTHelper"


class MobileNotifier(BaseNotifier):
    """
    Android/iOS local notification notifier.
    Prefers flet-android-notifications on Android for background support.
    Falls back to flet_notifications (LocalNotifications), then log-only mode.
    """

    def __init__(self):
        self._notifier = None
        self._android_notif = None
        self._backend = "none"
        self._channel_created = False

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

        # Fallback to generic flet_notifications (iOS / Android fallback)
        if self._android_notif is None:
            try:
                from flet_notifications import LocalNotifications
                self._notifier = LocalNotifications()
                self._backend = "flet_notifications"
                logger.info("MobileNotifier: using flet_notifications (LocalNotifications)")
            except ImportError:
                logger.warning(
                    "MobileNotifier: no notification package available. "
                    "Install flet-android-notifications (Android) or flet-notifications (iOS)."
                )

    @property
    def backend_name(self) -> str:
        """Return the active backend name for UI display."""
        return self._backend

    async def setup(self, page):
        """Request notification permissions and create channels on mobile."""
        if self._android_notif:
            # Android: request permissions + create notification channel (required for Android 8+)
            try:
                if hasattr(self._android_notif, 'request_permissions'):
                    await self._android_notif.request_permissions()
                    logger.info("Android notification permissions granted")
            except Exception as e:
                logger.warning("Failed to request Android permissions: %s", e)

            # Create notification channel (REQUIRED for Android 8.0+ / API 26+)
            if not self._channel_created:
                try:
                    if hasattr(self._android_notif, 'create_notification_channel'):
                        self._android_notif.create_notification_channel(
                            channel_id=_CHANNEL_ID,
                            channel_name=_CHANNEL_NAME,
                        )
                        self._channel_created = True
                        logger.info("Android notification channel '%s' created", _CHANNEL_ID)
                except Exception as e:
                    logger.warning("Failed to create notification channel: %s", e)

        elif self._notifier:
            # iOS: append to page overlay (REQUIRED for flet_notifications)
            try:
                if page and hasattr(page, 'overlay'):
                    page.overlay.append(self._notifier)
                    logger.info("flet_notifications appended to page overlay")
            except Exception as e:
                logger.warning("Failed to append notifier to page overlay: %s", e)

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
                notif_body += f" — Còn {remaining}"

            if self._android_notif:
                try:
                    # BUG FIX: param is "notification_id" not "id"
                    kwargs = {
                        "notification_id": 5000 + i,
                        "title": notif_title,
                        "body": notif_body,
                    }
                    # Include channel_id if channel was created
                    if self._channel_created:
                        kwargs["channel_id"] = _CHANNEL_ID
                    self._android_notif.show_notification(**kwargs)
                    success = True
                except Exception as e:
                    logger.warning("Android notification failed: %s", e)
            elif self._notifier:
                try:
                    self._notifier.show_notification(
                        id=5000 + i,
                        title=notif_title,
                        body=notif_body,
                    )
                    success = True
                except Exception as e:
                    logger.warning("Mobile notification failed: %s", e)
            else:
                logger.info("MOBILE (log-only): %s — %s", notif_title, notif_body)
                success = True

        return success


def _get_str(obj, key, default='') -> str:
    """Get string attribute from Assignment or dict."""
    if isinstance(obj, dict):
        return str(obj.get(key, default) or default)
    return str(getattr(obj, key, default) or default)
