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
import inspect
from pathlib import Path

from core.notification_types import ScheduleResult, ScheduledReminder
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
        self._notifications_enabled = False
        self._exact_alarm_enabled = False
        from config import _USER_DATA_DIR
        self._schedule_state_path = Path(_USER_DATA_DIR) / "notification_schedules.json"

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
                self._notifications_enabled = bool(
                    await self._android_notif.request_permissions()
                )
                can_exact = getattr(
                    self._android_notif, "can_schedule_exact_notifications", None
                )
                if can_exact:
                    self._exact_alarm_enabled = bool(await can_exact())
                if not self._exact_alarm_enabled:
                    request_exact = getattr(
                        self._android_notif, "request_exact_alarm_permission", None
                    )
                    if request_exact:
                        self._exact_alarm_enabled = bool(await request_exact())
                logger.info(
                    "Android permissions: notifications=%s exact_alarm=%s",
                    self._notifications_enabled,
                    self._exact_alarm_enabled,
                )
            except Exception as e:
                logger.warning("Failed to request Android permissions: %s", e)
        elif self._notifier and hasattr(self._notifier, 'request_permissions'):
            try:
                await self._notifier.request_permissions()
                logger.info("Notification permissions granted")
            except Exception as e:
                logger.warning("Failed to request notification permissions: %s", e)

    async def notify(self, assignments) -> bool:
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
                    await self._android_notif.show_notification(
                        notification_id=5000 + i,
                        title=notif_title,
                        body=notif_body,
                    )
                    success = True
                except Exception as e:
                    logger.warning("Android notification failed: %s", e)
            elif self._notifier:
                try:
                    result = self._notifier.show_notification(
                        title=notif_title,
                        body=notif_body,
                    )
                    if inspect.isawaitable(result):
                        await result
                    success = True
                except Exception as e:
                    logger.warning("Mobile notification failed: %s", e)
            else:
                logger.info("MOBILE (log-only): %s - %s", notif_title, notif_body)
                success = True

        return success

    async def reconcile_schedules(
        self, reminders: list[ScheduledReminder]
    ) -> ScheduleResult:
        """Make Android AlarmManager schedules match the current activity feed."""
        result = ScheduleResult(desired=len(reminders))
        if not self._android_notif:
            return result

        from core.safe_file_io import SafeFileIO

        previous = SafeFileIO.read_json_safe(self._schedule_state_path, dict)
        desired = {
            reminder.state_key: self._serialize_reminder(reminder)
            for reminder in reminders
        }

        # Cancel removed reminders and changed deadlines before re-scheduling them.
        for state_key, old_value in previous.items():
            if state_key not in desired or desired[state_key] != old_value:
                try:
                    await self._android_notif.cancel(int(state_key))
                    result.cancelled += 1
                except Exception as exc:
                    result.failed += 1
                    result.errors.append(f"cancel {state_key}: {exc}")

        persisted = {
            key: value
            for key, value in previous.items()
            if key in desired and desired[key] == value
        }
        for state_key, value in desired.items():
            if persisted.get(state_key) == value:
                continue
            reminder = next(item for item in reminders if item.state_key == state_key)
            try:
                await self._android_notif.schedule_notification(
                    notification_id=reminder.notification_id,
                    title=reminder.activity.title,
                    body=self._schedule_body(reminder),
                    scheduled_time=reminder.scheduled_at,
                    payload=reminder.activity.url,
                    schedule_mode=(
                        "exact_allow_while_idle"
                        if self._exact_alarm_enabled
                        else "inexact_allow_while_idle"
                    ),
                )
                persisted[state_key] = value
                result.scheduled += 1
            except Exception as exc:
                result.failed += 1
                result.errors.append(f"schedule {state_key}: {exc}")

        SafeFileIO.write_json_atomic(self._schedule_state_path, persisted)
        return result

    async def cancel_activity(self, activity_id: str) -> int:
        if not self._android_notif:
            return 0
        from core.safe_file_io import SafeFileIO

        state = SafeFileIO.read_json_safe(self._schedule_state_path, dict)
        matching = [
            key
            for key, value in state.items()
            if str(value.get("activity_id", "")) == str(activity_id)
        ]
        cancelled = 0
        for key in matching:
            try:
                await self._android_notif.cancel(int(key))
                state.pop(key, None)
                cancelled += 1
            except Exception as exc:
                logger.warning("Cannot cancel Android reminder %s: %s", key, exc)
        SafeFileIO.write_json_atomic(self._schedule_state_path, state)
        return cancelled

    @staticmethod
    def _serialize_reminder(reminder: ScheduledReminder) -> dict:
        return {
            "activity_id": reminder.activity.activity_id,
            "activity_key": reminder.activity.key,
            "milestone": reminder.milestone,
            "scheduled_at": reminder.scheduled_at.isoformat(),
            "deadline": (
                reminder.activity.deadline.isoformat()
                if reminder.activity.deadline
                else ""
            ),
            "revision": reminder.activity.revision,
        }

    @staticmethod
    def _schedule_body(reminder: ScheduledReminder) -> str:
        course = clean_course_name(reminder.activity.course_name)
        if isinstance(reminder.milestone, str):
            value = reminder.milestone.removeprefix("_min_")
            return f"{course} - Còn {value} phút"
        return f"{course} - Còn {reminder.milestone} giờ"


def _get_str(obj, key, default='') -> str:
    """Get string attribute from Assignment or dict."""
    if isinstance(obj, dict):
        return str(obj.get(key, default) or default)
    return str(getattr(obj, key, default) or default)
