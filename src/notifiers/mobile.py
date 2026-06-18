"""
Mobile (Android/iOS) local notification handler.
Uses flet_notifications package when available, falls back to logging.
"""
import logging
from notifiers.base import BaseNotifier

logger = logging.getLogger(__name__)


class MobileNotifier(BaseNotifier):
    """
    Android/iOS local notification notifier.
    Uses flet_notifications when available for native push notifications.
    Falls back to log-only mode if the package is not installed.
    """

    def __init__(self):
        self._notifier = None
        try:
            from flet_notifications import FletNotifications
            self._notifier = FletNotifications()
            logger.info("MobileNotifier: flet_notifications available")
        except ImportError:
            logger.warning(
                "MobileNotifier: flet_notifications not installed. "
                "Install with: pip install flet-notifications"
            )

    async def setup(self, page):
        """Request notification permissions on mobile."""
        if self._notifier and hasattr(self._notifier, 'request_permissions'):
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
        for a in assignments:
            title = getattr(a, 'title', '') or (a.get('title', '') if isinstance(a, dict) else '')
            course = getattr(a, 'course_name', '') or (a.get('course', '') if isinstance(a, dict) else '')
            remaining = getattr(a, 'remaining', '') or (a.get('remaining', '') if isinstance(a, dict) else '')

            notif_title = f"⏰ {title}"
            notif_body = f"📚 {course}"
            if remaining:
                notif_body += f" · Còn {remaining}"

            if self._notifier:
                try:
                    self._notifier.show_notification(
                        title=notif_title,
                        body=notif_body,
                    )
                    success = True
                except Exception as e:
                    logger.warning("Mobile notification failed: %s", e)
            else:
                logger.info("MOBILE NOTIFICATION (log-only): %s — %s", notif_title, notif_body)
                success = True

        return success
