from datetime import datetime, timedelta
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.class_schedule_notifications import (
    build_class_schedule_reminders,
    detect_delivery_mode,
)
from core.today_schedule import ClassSession


def _session(now: datetime, **overrides) -> ClassSession:
    values = {
        "session_id": "session-123",
        "subject": "Mạng máy tính",
        "course_code": "CN123",
        "start_at": now + timedelta(hours=5),
        "end_at": now + timedelta(hours=7),
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


def test_in_person_session_supports_each_selected_lead_time():
    now = datetime(2026, 11, 16, 8, 0)
    session = _session(now)
    reminders = build_class_schedule_reminders(
        [session], now=now, enabled=True, lead_hours=(1, 2, 3)
    )
    assert [item.lead_hours for item in reminders] == [3, 2, 1]
    assert [item.scheduled_at for item in reminders] == [
        session.start_at - timedelta(hours=3),
        session.start_at - timedelta(hours=2),
        session.start_at - timedelta(hours=1),
    ]
    assert {item.delivery_mode for item in reminders} == {"in_person"}
    payload = reminders[0].to_platform_payload()
    assert payload["subject"] == "Mạng máy tính"
    assert payload["room"] == "F.302"
    assert payload["campus"] == "Cơ sở Võ Oanh"
    assert payload["lead_hours"] == 3


def test_online_session_is_scheduled_without_physical_room_or_campus():
    now = datetime(2026, 11, 16, 8, 0)
    session = _session(
        now,
        room="",
        campus="",
        note="Học trực tuyến qua Zoom: https://zoom.us/j/123456",
    )
    assert detect_delivery_mode(session) == "online"
    reminders = build_class_schedule_reminders(
        [session], now=now, enabled=True, lead_hours=(1, 2, 3)
    )
    assert len(reminders) == 3
    assert {item.delivery_mode for item in reminders} == {"online"}
    assert all(item.session.note for item in reminders)


def test_online_markers_take_precedence_over_room_or_campus_text():
    now = datetime(2026, 11, 16, 8, 0)
    session = _session(
        now,
        room="Online",
        campus="Cơ sở Võ Oanh",
        note="Google Meet",
    )
    assert detect_delivery_mode(session) == "online"


def test_ambiguous_session_without_room_or_online_marker_is_skipped():
    now = datetime(2026, 11, 16, 8, 0)
    session = _session(now, room="", campus="", note="")
    assert detect_delivery_mode(session) is None
    assert build_class_schedule_reminders([session], now=now, enabled=True) == ()


def test_online_variants_are_recognized_with_or_without_diacritics():
    now = datetime(2026, 11, 16, 8, 0)
    for marker in ("Trực tuyến", "truc tuyen", "Google Meet", "Microsoft Teams", "Webex"):
        session = _session(now, room=marker, campus="", note="")
        assert detect_delivery_mode(session) == "online"


def test_cancelled_and_already_started_sessions_are_ignored():
    now = datetime(2026, 11, 16, 8, 0)
    cancelled = _session(now, cancelled=True)
    started = _session(now, start_at=now - timedelta(minutes=1))
    reminders = build_class_schedule_reminders(
        [cancelled, started], now=now, enabled=True
    )
    assert reminders == ()


def test_reminder_id_is_stable_and_distinct_for_each_lead_time():
    now = datetime(2026, 11, 16, 8, 0)
    session = _session(now)
    first = build_class_schedule_reminders([session], now=now, enabled=True)
    second = build_class_schedule_reminders([session], now=now, enabled=True)
    assert [item.reminder_id for item in first] == [item.reminder_id for item in second]
    assert len({item.reminder_id for item in first}) == 3


def test_dnd_moves_reminder_to_end_of_quiet_period_when_still_before_class():
    now = datetime(2026, 11, 16, 6, 0)
    session = _session(now, start_at=datetime(2026, 11, 16, 9, 0))
    reminders = build_class_schedule_reminders(
        [session], now=now, enabled=True, lead_hours=(2,),
        dnd_enabled=True, dnd_start=22, dnd_end=7,
    )
    assert len(reminders) == 1
    assert reminders[0].scheduled_at == datetime(2026, 11, 16, 7, 0)


def test_no_reminder_is_moved_to_or_after_class_start():
    now = datetime(2026, 11, 16, 6, 0)
    session = _session(now, start_at=datetime(2026, 11, 16, 7, 0))
    reminders = build_class_schedule_reminders(
        [session], now=now, enabled=True, lead_hours=(1,),
        dnd_enabled=True, dnd_start=22, dnd_end=7,
    )
    assert reminders == ()


def test_dnd_shift_collapses_leads_into_one_reminder_with_true_label():
    """All three leads shift into 07:00 → one reminder, labelled honestly."""
    now = datetime(2026, 11, 16, 6, 0)
    session = _session(now, start_at=datetime(2026, 11, 16, 8, 0))
    reminders = build_class_schedule_reminders(
        [session], now=now, enabled=True, lead_hours=(1, 2, 3),
        dnd_enabled=True, dnd_start=22, dnd_end=7,
    )
    # 05:00, 06:00 and 07:00 all sit inside/among the quiet hours, so the
    # shift moves them to the single 07:00 boundary instant.
    assert len(reminders) == 1
    assert reminders[0].scheduled_at == datetime(2026, 11, 16, 7, 0)
    # The label follows the real remaining time (one hour), not the stale
    # three-hour lead the user originally selected.
    assert reminders[0].lead_hours == 1
    # The dedupe key is stable across rebuilds so no duplicate fires.
    again = build_class_schedule_reminders(
        [session], now=now, enabled=True, lead_hours=(1, 2, 3),
        dnd_enabled=True, dnd_start=22, dnd_end=7,
    )
    assert [item.reminder_id for item in again] == [
        item.reminder_id for item in reminders
    ]


def test_physical_room_wins_over_note_mentioning_an_online_platform():
    """A room number in the note/photo of an in-person class stays in-person."""
    now = datetime(2026, 11, 16, 8, 0)
    session = _session(
        now,
        room="F.302",
        campus="Cơ sở Võ Oanh",
        note="Nộp bài tập qua Google Classroom trước giờ học",
    )
    assert detect_delivery_mode(session) == "in_person"


def test_broad_online_words_in_note_without_location_are_skipped():
    """'online'/'elearning' alone in a note is ambiguous → not scheduled."""
    now = datetime(2026, 11, 16, 8, 0)
    for note in ("Học online", "Nộp bài trên elearning", "E-learning mở bài"):
        session = _session(now, room="", campus="", note=note)
        assert detect_delivery_mode(session) is None
    # An explicit platform marker in the note still counts as online.
    explicit = _session(
        now, room="", campus="", note="Vào lớp qua Zoom: https://zoom.us/j/1"
    )
    assert detect_delivery_mode(explicit) == "online"


def test_location_online_word_still_marks_online():
    now = datetime(2026, 11, 16, 8, 0)
    session = _session(now, room="Online", campus="", note="Bài tập elearning")
    assert detect_delivery_mode(session) == "online"
