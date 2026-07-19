import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from notifiers.windows import WindowsNotifier
from core.notification_types import ActivityNotification, ScheduledReminder


def _scheduled_reminder(*, notification_id=42, scheduled_at=None, deadline=None):
    deadline = deadline or datetime.now() + timedelta(hours=2)
    activity = ActivityNotification(
        activity_id="quiz-42",
        course_id="course-7",
        course_name="Course",
        title="Quiz 1",
        event_type="quiz",
        deadline=deadline,
        url="https://courses.example/mod/quiz/view.php?id=42",
        submission_status="not_submitted",
        revision="1",
    )
    return ScheduledReminder(
        activity=activity,
        milestone=30,
        notification_id=notification_id,
        scheduled_at=scheduled_at or datetime.now() + timedelta(hours=1),
    )


def _scheduler_notifier(tmp_path):
    notifier = WindowsNotifier(start_scheduler=False)
    notifier._schedule_state_path = tmp_path / "windows-schedules.json"
    notifier._schedule_state = {}
    return notifier


def test_single_activity_toast_opens_exact_activity_url():
    toaster = MagicMock()
    module = MagicMock()
    module.InteractableWindowsToaster.return_value = toaster
    toast = MagicMock()
    module.Toast.return_value = toast
    activity = {
        "title": "Quiz 1",
        "course_name": "Course",
        "event_type": "quiz",
        "deadline": None,
        "url": "https://courses.example/mod/quiz/view.php?id=42",
    }

    notifier = WindowsNotifier.__new__(WindowsNotifier)
    notifier.app_id = "UTHelper"
    notifier.aumid = "Package_family!UTHelper"
    notifier.tray_app = None
    notifier.last_error = ""
    with patch.dict(sys.modules, {"windows_toasts": module}):
        assert notifier.notify([activity]) is True

    assert toast.launch_action == activity["url"]
    toaster.show_toast.assert_called_once_with(toast)


def test_reconcile_windows_schedules_is_durable_and_idempotent(tmp_path):
    notifier = _scheduler_notifier(tmp_path)
    reminder = _scheduled_reminder()

    first = notifier.reconcile_schedules([reminder])
    second = notifier.reconcile_schedules([reminder])

    assert first.scheduled == 1
    assert second.scheduled == 0
    assert notifier.get_diagnostics()["pending_schedules"] == 1
    assert notifier._schedule_state_path.exists()
    notifier.close()


def test_reconcile_reschedules_changed_deadline_and_cancels_removed(tmp_path):
    notifier = _scheduler_notifier(tmp_path)
    original = _scheduled_reminder()
    notifier.reconcile_schedules([original])
    changed = _scheduled_reminder(
        scheduled_at=original.scheduled_at + timedelta(hours=1),
        deadline=original.activity.deadline + timedelta(hours=1),
    )

    result = notifier.reconcile_schedules([changed])
    removed = notifier.reconcile_schedules([])

    assert result.cancelled == 1
    assert result.scheduled == 1
    assert removed.cancelled == 1
    assert notifier.get_diagnostics()["pending_schedules"] == 0
    notifier.close()


def test_due_windows_schedule_records_delivery_and_removes_pending(tmp_path):
    notifier = _scheduler_notifier(tmp_path)
    reminder = _scheduled_reminder(scheduled_at=datetime.now() - timedelta(seconds=1))
    notifier.reconcile_schedules([reminder])
    key = reminder.state_key
    value = notifier._schedule_state[key]
    notifier.notify = MagicMock(return_value=True)

    notifier._deliver_pending(key, value)

    notifier.notify.assert_called_once()
    diagnostics = notifier.get_diagnostics()
    assert diagnostics["pending_schedules"] == 0
    assert diagnostics["scheduled_delivered"] == 1
    assert diagnostics["last_scheduled_delivery_at"]
    notifier.close()


def test_failed_windows_schedule_retries_before_deadline(tmp_path):
    notifier = _scheduler_notifier(tmp_path)
    reminder = _scheduled_reminder(scheduled_at=datetime.now() - timedelta(seconds=1))
    notifier.reconcile_schedules([reminder])
    key = reminder.state_key
    value = notifier._schedule_state[key]
    notifier.notify = MagicMock(return_value=False)
    notifier.last_error = "toast unavailable"

    notifier._deliver_pending(key, value)

    assert key in notifier._schedule_state
    assert notifier._schedule_state[key]["retry_at"]
    assert notifier.get_diagnostics()["last_schedule_error"] == "toast unavailable"
    notifier.close()


def test_cancel_activity_only_removes_matching_windows_reminders(tmp_path):
    notifier = _scheduler_notifier(tmp_path)
    first = _scheduled_reminder(notification_id=1)
    second = _scheduled_reminder(notification_id=2)
    second = ScheduledReminder(
        activity=ActivityNotification(
            **{**vars(second.activity), "activity_id": "quiz-99"}
        ),
        milestone=second.milestone,
        notification_id=second.notification_id,
        scheduled_at=second.scheduled_at,
    )
    notifier.reconcile_schedules([first, second])

    assert notifier.cancel_activity("quiz-42") == 1
    assert set(notifier._schedule_state) == {second.state_key}
    notifier.close()
