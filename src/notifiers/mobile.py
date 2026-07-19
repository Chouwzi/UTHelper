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
import json
from datetime import datetime
from pathlib import Path

from core.notification_policy import (
    ActivityNotificationPolicy,
    NotificationPolicyConfig,
    stable_notification_id,
)
from core.notification_types import ScheduleResult, ScheduledReminder
from core.notification_types import ActivityNotification
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
        self._page = None
        from config import _USER_DATA_DIR
        self._schedule_state_path = Path(_USER_DATA_DIR) / "notification_schedules.json"

        # Try Android-specific package first (has AlarmManager + Foreground Service)
        if IS_ANDROID:
            try:
                from flet_android_notifications import FletAndroidNotifications
                self._android_notif = FletAndroidNotifications(
                    on_notification_tap=self._on_notification_tap
                )
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
        self._page = page
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

        all_succeeded = True
        for a in assignments:
            title = _get_str(a, 'title')
            course = clean_course_name(_get_str(a, 'course_name') or _get_str(a, 'course'))
            remaining = _get_str(a, 'remaining')

            notif_title = title
            notif_body = course
            if remaining:
                notif_body += f" - Còn {remaining}"

            if self._android_notif:
                try:
                    notification_id = self._notification_id(a)
                    await self._android_notif.show_notification(
                        notification_id=notification_id,
                        title=notif_title,
                        body=notif_body,
                        payload=_get_str(a, "url"),
                    )
                except Exception as e:
                    all_succeeded = False
                    logger.warning("Android notification failed: %s", e)
            elif self._notifier:
                try:
                    result = self._notifier.show_notification(
                        title=notif_title,
                        body=notif_body,
                    )
                    if inspect.isawaitable(result):
                        await result
                except Exception as e:
                    all_succeeded = False
                    logger.warning("Mobile notification failed: %s", e)
            else:
                logger.info("MOBILE (log-only): %s - %s", notif_title, notif_body)
                all_succeeded = False

        return all_succeeded

    def _on_notification_tap(self, event) -> None:
        """Open the Moodle activity carried by a native notification payload."""
        try:
            value = json.loads(getattr(event, "data", "{}") or "{}")
            url = str(value.get("payload", ""))
            if self._page and url.startswith(("https://", "http://")):
                self._page.launch_url(url)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Invalid Android notification payload: %s", exc)

    @staticmethod
    def _notification_id(value) -> int:
        """Use the same stable activity+milestone ID for immediate delivery."""
        from config import settings as config

        activity = ActivityNotification.from_value(value)
        policy = ActivityNotificationPolicy(
            NotificationPolicyConfig(
                milestone_minutes=tuple(
                    int(item)
                    for item in (
                        getattr(config, "NOTIFY_MILESTONES_MINUTES", ()) or ()
                    )
                ),
                milestones=tuple(
                    int(item)
                    for item in (getattr(config, "NOTIFY_MILESTONES", ()) or ())
                ),
                minutes_before=max(
                    0, int(getattr(config, "NOTIFY_MINUTES_BEFORE", 0) or 0)
                ),
                ignore_submitted=False,
            )
        )
        candidate = policy.due_candidate(activity, (), datetime.now())
        milestone = candidate.milestone if candidate else "immediate"
        return stable_notification_id(activity.key, milestone)

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
        minutes = int(reminder.milestone)
        if minutes % 1440 == 0:
            return f"{course} - Còn {minutes // 1440} ngày"
        if minutes % 60 == 0:
            return f"{course} - Còn {minutes // 60} giờ"
        return f"{course} - Còn {minutes} phút"


def _get_str(obj, key, default='') -> str:
    """Get string attribute from Assignment or dict."""
    if isinstance(obj, dict):
        return str(obj.get(key, default) or default)
    return str(getattr(obj, key, default) or default)
