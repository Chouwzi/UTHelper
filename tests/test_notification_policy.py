import asyncio
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.notification_policy import (
    ActivityNotificationPolicy,
    NotificationPolicyConfig,
    stable_notification_id,
)
from core.notification_types import ActivityNotification, ScheduledReminder
from notifiers.mobile import MobileNotifier


def _activity(**overrides):
    values = {
        "activity_id": "42",
        "course_id": "7",
        "course_name": "Lập trình",
        "title": "Quiz 1",
        "event_type": "quiz",
        "deadline": datetime(2026, 7, 22, 12),
        "url": "https://moodle/mod/quiz/view.php?id=42",
        "submission_status": "not_submitted",
        "revision": "1",
    }
    values.update(overrides)
    return ActivityNotification(**values)


def test_activity_key_prefers_moodle_id_and_notification_id_is_stable():
    activity = _activity()
    assert activity.key == "activity:42"
    first = stable_notification_id(activity.key, 24)
    assert first == stable_notification_id(activity.key, 24)
    assert first != stable_notification_id(activity.key, 3)
    assert 0 < first <= 0x7FFFFFFF


def test_policy_filters_types_muted_and_submitted():
    policy = ActivityNotificationPolicy(
        NotificationPolicyConfig(
            notify_types=("quiz",),
            muted_courses=("Muted",),
            ignore_submitted=True,
        )
    )
    assert policy.accepts(_activity())
    assert not policy.accepts(_activity(event_type="assignment"))
    assert not policy.accepts(_activity(course_name="Muted"))
    assert not policy.accepts(_activity(submission_status="graded"))


def test_dnd_cross_midnight_and_schedule_moves_to_end():
    policy = ActivityNotificationPolicy(
        NotificationPolicyConfig(
            milestones=(3,), dnd_enabled=True, dnd_start=22, dnd_end=7
        )
    )
    assert policy.is_dnd(datetime(2026, 7, 20, 23))
    assert policy.is_dnd(datetime(2026, 7, 21, 6))
    assert not policy.is_dnd(datetime(2026, 7, 21, 12))

    activity = _activity(deadline=datetime(2026, 7, 21, 2))
    reminders = policy.desired_schedules(
        [activity], now=datetime(2026, 7, 20, 18)
    )
    # Moving a 23:00 reminder to 07:00 would pass the deadline, so it is omitted.
    assert reminders == []


def test_full_day_dnd_omits_all_native_schedules():
    policy = ActivityNotificationPolicy(
        NotificationPolicyConfig(
            milestones=(24,), dnd_enabled=True, dnd_start=7, dnd_end=7
        )
    )
    reminders = policy.desired_schedules(
        [_activity()], now=datetime(2026, 7, 20, 10)
    )
    assert reminders == []


def test_activity_pipeline_does_not_use_moodle_notification_feed():
    from inspect import getsource

    from notifiers.manager import NotificationManager

    assert "get_unread_notification_count" not in getsource(NotificationManager)


def test_due_candidate_uses_milestone_once():
    policy = ActivityNotificationPolicy(
        NotificationPolicyConfig(milestones=(72, 24, 3))
    )
    now = datetime(2026, 7, 20, 12)
    activity = _activity(deadline=now + timedelta(hours=20))
    candidate = policy.due_candidate(activity, (), now)
    assert candidate and candidate.milestone == 24
    assert policy.due_candidate(activity, (24,), now) is None


class _FakeAndroidNotifications:
    def __init__(self):
        self.scheduled = []
        self.cancelled = []
        self.shown = []

    async def show_notification(self, **kwargs):
        self.shown.append(kwargs)

    async def schedule_notification(self, **kwargs):
        self.scheduled.append(kwargs)

    async def cancel(self, notification_id):
        self.cancelled.append(notification_id)


def _mobile_notifier(tmp_path, backend):
    notifier = MobileNotifier.__new__(MobileNotifier)
    notifier._android_notif = backend
    notifier._notifier = None
    notifier._backend = "fake"
    notifier._notifications_enabled = True
    notifier._exact_alarm_enabled = True
    notifier._schedule_state_path = tmp_path / "schedules.json"
    return notifier


def test_android_schedule_reconciliation_is_idempotent_and_cancels_changes(tmp_path):
    backend = _FakeAndroidNotifications()
    notifier = _mobile_notifier(tmp_path, backend)
    activity = _activity()
    reminder = ScheduledReminder(
        activity=activity,
        milestone=24,
        notification_id=123,
        scheduled_at=datetime(2026, 7, 21, 12),
    )

    first = asyncio.run(notifier.reconcile_schedules([reminder]))
    second = asyncio.run(notifier.reconcile_schedules([reminder]))
    assert first.scheduled == 1
    assert second.scheduled == 0

    changed = ScheduledReminder(
        activity=activity,
        milestone=24,
        notification_id=123,
        scheduled_at=datetime(2026, 7, 21, 13),
    )
    result = asyncio.run(notifier.reconcile_schedules([changed]))
    assert result.cancelled == 1
    assert result.scheduled == 1

    removed = asyncio.run(notifier.reconcile_schedules([]))
    assert removed.cancelled == 1
    assert backend.cancelled == [123, 123]


def test_cancel_activity_only_removes_matching_native_schedules(tmp_path):
    backend = _FakeAndroidNotifications()
    notifier = _mobile_notifier(tmp_path, backend)
    reminders = [
        ScheduledReminder(
            activity=_activity(activity_id=activity_id),
            milestone=24,
            notification_id=notification_id,
            scheduled_at=datetime(2026, 7, 21, 12),
        )
        for activity_id, notification_id in (("42", 101), ("99", 202))
    ]
    asyncio.run(notifier.reconcile_schedules(reminders))
    assert asyncio.run(notifier.cancel_activity("42")) == 1
    assert backend.cancelled == [101]


def test_immediate_android_notification_uses_stable_activity_milestone_id(tmp_path):
    backend = _FakeAndroidNotifications()
    notifier = _mobile_notifier(tmp_path, backend)
    value = _activity(deadline=datetime.now() + timedelta(hours=2))

    assert asyncio.run(notifier.notify([value])) is True
    assert backend.shown[0]["notification_id"] == stable_notification_id(
        value.key, 3
    )
    assert backend.shown[0]["payload"] == value.url


def test_android_notification_tap_opens_activity_payload(tmp_path):
    notifier = _mobile_notifier(tmp_path, _FakeAndroidNotifications())
    notifier._page = MagicMock()
    notifier._on_notification_tap(
        SimpleNamespace(data='{"payload":"https://courses.example/activity/42"}')
    )
    notifier._page.launch_url.assert_called_once_with(
        "https://courses.example/activity/42"
    )
