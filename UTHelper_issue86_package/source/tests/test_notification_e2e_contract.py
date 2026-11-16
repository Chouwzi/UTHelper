"""Executable cross-platform contract for UTHelper deadline notifications."""

from datetime import datetime, timedelta

from core.notification_policy import ActivityNotificationPolicy, NotificationPolicyConfig
from core.notification_types import ActivityNotification


def activity(**overrides) -> ActivityNotification:
    values = {
        "activity_id": "42",
        "course_id": "7",
        "course_name": "Lập Trình Python",
        "title": "Bài tập lớn",
        "event_type": "assignment",
        "deadline": datetime(2026, 8, 8, 12, 0),
        "url": "https://courses.example/mod/assign/view.php?id=42",
        "submission_status": "unknown",
        "revision": "server-revision-1",
    }
    values.update(overrides)
    return ActivityNotification(**values)


def test_canonical_day_hour_minute_milestones_schedule_at_literal_times():
    now = datetime(2026, 8, 1, 12, 0)
    item = activity(deadline=datetime(2026, 8, 5, 12, 0))
    policy = ActivityNotificationPolicy(
        NotificationPolicyConfig(milestone_minutes=(4320, 1440, 180, 60, 30, 5))
    )

    reminders = policy.desired_schedules([item], now)

    assert [(r.milestone, r.scheduled_at) for r in reminders] == [
        (5, datetime(2026, 8, 5, 11, 55)),
        (30, datetime(2026, 8, 5, 11, 30)),
        (60, datetime(2026, 8, 5, 11, 0)),
        (180, datetime(2026, 8, 5, 9, 0)),
        (1440, datetime(2026, 8, 4, 12, 0)),
        (4320, datetime(2026, 8, 2, 12, 0)),
    ]


def test_muted_course_matching_is_case_and_whitespace_insensitive():
    policy = ActivityNotificationPolicy(
        NotificationPolicyConfig(muted_courses=("  lập trình python  ",))
    )

    assert policy.accepts(activity()) is False


def test_unknown_submission_state_remains_eligible_on_every_platform():
    policy = ActivityNotificationPolicy(
        NotificationPolicyConfig(ignore_submitted=True)
    )

    assert policy.accepts(activity(submission_status="unknown")) is True
    assert policy.accepts(activity(submission_status="submitted")) is False


def test_deadline_move_changes_revision_even_when_server_revision_is_unchanged():
    first = activity(deadline=datetime(2026, 8, 8, 12, 0))
    moved = activity(deadline=datetime(2026, 8, 8, 13, 0))

    assert first.deadline_revision != moved.deadline_revision


def test_dnd_collapses_multiple_milestones_moved_to_same_wakeup_time():
    now = datetime(2026, 8, 1, 18, 0)
    item = activity(deadline=datetime(2026, 8, 2, 12, 0))
    policy = ActivityNotificationPolicy(
        NotificationPolicyConfig(
            milestone_minutes=(600, 840),
            dnd_enabled=True,
            dnd_start=22,
            dnd_end=7,
        )
    )

    reminders = policy.desired_schedules([item], now)

    assert [(r.milestone, r.scheduled_at) for r in reminders] == [
        (600, datetime(2026, 8, 2, 7, 0))
    ]


def test_malformed_deadline_never_becomes_a_notification_candidate():
    malformed = ActivityNotification.from_value(
        {
            "id": "42",
            "title": "Bad date",
            "deadline": "not-a-date",
            "url": "https://courses.example/42",
        }
    )
    policy = ActivityNotificationPolicy(NotificationPolicyConfig(milestone_minutes=(60,)))

    assert malformed.deadline is None
    assert policy.due_candidate(malformed, (), datetime(2026, 8, 1, 12, 0)) is None
