from datetime import datetime, timedelta
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.class_schedule_notifications import (
    build_class_schedule_reminders,
    is_in_person_session,
)
from core.today_schedule import ClassSession


def _session(now: datetime, **overrides) -> ClassSession:
    values = {
        "session_id": "session-123",
        "subject": "Mạng máy tính",
        "course_code": "CN123",
        "start_at": now + timedelta(hours=3),
        "end_at": now + timedelta(hours=5),
        "room": "F.302",
        "campus": "Cơ sở Võ Oanh",
        "period_start": 1,
        "period_end": 3,
        "cancelled": False,
        "note": "",
    }
    values.update(overrides)
    return ClassSession(**values)


def test_class_schedule_reminders_are_disabled_by_default():
    now = datetime(2026, 11, 16, 8, 0)
    assert build_class_schedule_reminders([_session(now)], now=now) == ()


def test_in_person_class_is_scheduled_exactly_one_hour_before_start():
    now = datetime(2026, 11, 16, 8, 0)
    session = _session(now)

    reminders = build_class_schedule_reminders(
        [session], now=now, enabled=True
    )

    assert len(reminders) == 1
    assert reminders[0].scheduled_at == session.start_at - timedelta(hours=1)
    payload = reminders[0].to_platform_payload()
    assert payload["subject"] == "Mạng máy tính"
    assert payload["room"] == "F.302"
    assert payload["campus"] == "Cơ sở Võ Oanh"
    assert payload["start_at_epoch_seconds"] == int(session.start_at.timestamp())


def test_online_or_locationless_sessions_are_not_classified_as_in_person():
    now = datetime(2026, 11, 16, 8, 0)
    assert not is_in_person_session(_session(now, room="", campus=""))
    assert not is_in_person_session(_session(now, room="Online", campus=""))
    assert not is_in_person_session(_session(now, room="Google Meet", campus=""))
    assert not is_in_person_session(_session(now, room="", campus="Học trực tuyến"))
    assert not is_in_person_session(_session(now, note="Buổi học trực tuyến qua Zoom"))


def test_cancelled_and_already_started_sessions_are_ignored():
    now = datetime(2026, 11, 16, 8, 0)
    cancelled = _session(now, cancelled=True)
    started = _session(now, start_at=now - timedelta(minutes=1))

    reminders = build_class_schedule_reminders(
        [cancelled, started], now=now, enabled=True
    )

    assert reminders == ()


def test_reminder_is_not_sent_late_when_one_hour_mark_has_passed():
    now = datetime(2026, 11, 16, 9, 30)
    session = _session(now, start_at=now + timedelta(minutes=30))

    reminders = build_class_schedule_reminders(
        [session], now=now, enabled=True
    )

    assert reminders == ()


def test_dnd_moves_reminder_to_end_of_quiet_period_when_still_before_class():
    now = datetime(2026, 11, 16, 6, 0)
    session = _session(now, start_at=datetime(2026, 11, 16, 8, 0))

    reminders = build_class_schedule_reminders(
        [session],
        now=now,
        enabled=True,
        dnd_enabled=True,
        dnd_start=22,
        dnd_end=7,
    )

    assert len(reminders) == 1
    assert reminders[0].scheduled_at == datetime(2026, 11, 16, 7, 0)


def test_dnd_suppresses_reminder_when_next_allowed_time_is_after_class_start():
    now = datetime(2026, 11, 16, 6, 0)
    session = _session(now, start_at=datetime(2026, 11, 16, 7, 0))

    reminders = build_class_schedule_reminders(
        [session],
        now=now,
        enabled=True,
        dnd_enabled=True,
        dnd_start=22,
        dnd_end=7,
    )

    assert reminders == ()


def test_reminder_id_is_stable_across_reconciliation():
    now = datetime(2026, 11, 16, 8, 0)
    session = _session(now)

    first = build_class_schedule_reminders([session], now=now, enabled=True)
    second = build_class_schedule_reminders([session], now=now, enabled=True)

    assert first[0].reminder_id == second[0].reminder_id
