"""
Background notification scheduler for Android.

Uses flet-android-notifications AlarmManager to schedule periodic
Moodle checks even when the app is minimized/backgrounded.
On non-Android platforms, this module is a no-op.
"""

import logging
from platform_utils import IS_ANDROID

logger = logging.getLogger(__name__)

# Singleton reference
_scheduler = None


class BackgroundScheduler:
    """Schedule periodic background notifications on Android via AlarmManager."""

    def __init__(self):
        self._android_notif = None
        self._is_active = False

        if not IS_ANDROID:
            logger.debug("BackgroundScheduler: Not Android, skipping init")
            return

        try:
            from flet_android_notifications import FletAndroidNotifications
            self._android_notif = FletAndroidNotifications()
            logger.info("BackgroundScheduler: flet-android-notifications initialized")
        except ImportError:
            logger.warning(
                "BackgroundScheduler: flet-android-notifications not installed"
            )
        except Exception as e:
            logger.warning("BackgroundScheduler: init failed: %s", e)

    @property
    def is_available(self) -> bool:
        """True if Android notification backend is loaded."""
        return self._android_notif is not None

    async def request_permissions(self):
        """Request POST_NOTIFICATIONS permission (Android 13+)."""
        if not self._android_notif:
            return
        try:
            await self._android_notif.request_permissions()
            logger.info("BackgroundScheduler: notification permissions granted")
        except Exception as e:
            logger.warning("BackgroundScheduler: permission request failed: %s", e)

    async def start_periodic_check(self, interval_minutes: int = 30):
        """Schedule a repeating notification/check via AlarmManager.

        This survives app minimize and even Doze windows (with SCHEDULE_EXACT_ALARM).
        The notification acts as a reminder — when tapped, it opens the app.
        """
        if not self._android_notif:
            logger.debug("BackgroundScheduler: no backend, skip start")
            return

        if self._is_active:
            logger.debug("BackgroundScheduler: already active, skip")
            return

        interval_seconds = max(300, interval_minutes * 60)  # Min 5 min

        try:
            await self._android_notif.periodically_show_with_duration(
                id=9001,  # Unique ID for background check notification
                title="UTHelper đang theo dõi deadline",
                body="Nhấn để xem danh sách bài tập mới nhất",
                duration_seconds=interval_seconds,
            )
            self._is_active = True
            logger.info(
                "BackgroundScheduler: periodic check started (every %d min)",
                interval_minutes,
            )
        except Exception as e:
            logger.error("BackgroundScheduler: failed to start periodic: %s", e)

    async def start_foreground_service(self):
        """Start a foreground service to keep the app alive in background.

        Shows a persistent notification. Use sparingly — drains battery.
        """
        if not self._android_notif:
            return

        try:
            await self._android_notif.start_foreground_service(
                notification_id=9002,
                title="UTHelper",
                body="Đang theo dõi deadline bài tập...",
            )
            logger.info("BackgroundScheduler: foreground service started")
        except Exception as e:
            logger.error("BackgroundScheduler: foreground service failed: %s", e)

    async def stop_foreground_service(self):
        """Stop the foreground service."""
        if not self._android_notif:
            return

        try:
            if hasattr(self._android_notif, 'stop_foreground_service'):
                await self._android_notif.stop_foreground_service()
                logger.info("BackgroundScheduler: foreground service stopped")
        except Exception as e:
            logger.warning("BackgroundScheduler: stop foreground failed: %s", e)

    async def cancel_periodic(self):
        """Cancel any scheduled periodic notifications."""
        if not self._android_notif:
            return

        try:
            if hasattr(self._android_notif, 'cancel'):
                await self._android_notif.cancel(9001)
            self._is_active = False
            logger.info("BackgroundScheduler: periodic check cancelled")
        except Exception as e:
            logger.warning("BackgroundScheduler: cancel failed: %s", e)

    async def send_immediate(self, title: str, body: str):
        """Send a one-shot notification immediately."""
        if not self._android_notif:
            return

        try:
            await self._android_notif.show_notification(
                id=9003,
                title=title,
                body=body,
            )
        except Exception as e:
            logger.warning("BackgroundScheduler: immediate notification failed: %s", e)


def get_scheduler() -> BackgroundScheduler:
    """Get or create the singleton BackgroundScheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
    return _scheduler
