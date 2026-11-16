"""Pure scheduling policy for reminders before in-person class sessions.

The policy consumes the Portal snapshot already available in the foreground.
It does not fetch Portal data, keep credentials, or infer a class location when
room/campus data is missing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from core.today_schedule import ClassSession

CLASS_SCHEDULE_LEAD_MINUTES = 60

_ONLINE_MARKERS = (
    "online",
    "truc tuyen",
    "trực tuyến",
    "zoom",
    "google meet",
    "microsoft teams",
    "ms teams",
    "e-learning",
    "elearning",
    "hoc truc tuyen",
    "học trực tuyến",
)


@dataclass(frozen=True, slots=True)
class ClassScheduleReminder:
    """One locally schedulable reminder for a Portal class session."""

    reminder_id: str
    session: ClassSession
    scheduled_at: datetime

    @property
    def start_at(self) -> datetime:
        return self.session.start_at

    def to_platform_payload(self) -> dict[str, object]:
        """Return a primitive-only payload accepted by Android/iOS bridges."""
        return {
            "reminder_id": self.reminder_id,
            "session_id": self.session.session_id,
            "subject": self.session.subject,
            "course_code": self.session.course_code,
            "room": self.session.room,
            "campus": self.session.campus,
            "note": self.session.note,
            "start_at_epoch_seconds": int(self.session.start_at.timestamp()),
            "scheduled_at_epoch_seconds": int(self.scheduled_at.timestamp()),
        }


def is_in_person_session(session: ClassSession) -> bool:
    """Accept only sessions with a plausible physical room/campus location.

    Portal's current schedule payload does not expose a dedicated delivery-mode
    field.  The safe rule is therefore fail-closed: missing location data and
    common online-learning labels are excluded.
    """
    room = (session.room or "").strip()
    campus = (session.campus or "").strip()
    note = (session.note or "").strip()
    location = f"{room} {campus} {note}".casefold()
    if not room and not campus:
        return False
    normalized = " ".join(location.split())
    return not any(marker in normalized for marker in _ONLINE_MARKERS)


def _is_in_dnd(
    moment: datetime,
    *,
    enabled: bool,
    start_hour: int,
    end_hour: int,
) -> bool:
    if not enabled:
        return False
    start = max(0, min(23, int(start_hour)))
    end = max(0, min(23, int(end_hour)))
    if start == end:
        return True
    hour = moment.hour
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end


def _move_out_of_dnd(
    scheduled_at: datetime,
    class_start: datetime,
    *,
    dnd_enabled: bool,
    dnd_start: int,
    dnd_end: int,
) -> datetime | None:
    if not _is_in_dnd(
        scheduled_at,
        enabled=dnd_enabled,
        start_hour=dnd_start,
        end_hour=dnd_end,
    ):
        return scheduled_at

    if dnd_start == dnd_end:
        return None

    next_allowed = scheduled_at.replace(
        hour=max(0, min(23, int(dnd_end))), minute=0, second=0, microsecond=0
    )
    if dnd_start > dnd_end and scheduled_at.hour >= dnd_start:
        next_allowed += timedelta(days=1)
    if next_allowed >= class_start:
        return None
    return next_allowed


def _stable_reminder_id(session: ClassSession) -> str:
    identity = session.session_id.strip() or "|".join(
        (session.course_code, session.subject, session.start_at.isoformat(), session.room)
    )
    raw = f"{identity}|{session.start_at.isoformat()}|class-60m"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"class:{digest}"


def build_class_schedule_reminders(
    sessions: Iterable[ClassSession],
    *,
    now: datetime | None = None,
    enabled: bool = False,
    lead_minutes: int = CLASS_SCHEDULE_LEAD_MINUTES,
    dnd_enabled: bool = False,
    dnd_start: int = 22,
    dnd_end: int = 7,
) -> tuple[ClassScheduleReminder, ...]:
    """Build future reminders, defaulting to one hour before each class.

    A class whose one-hour-before moment has already passed is not sent late;
    the next successful refresh will not create a misleading overdue reminder.
    """
    if not enabled:
        return ()
    current = now or datetime.now()
    lead = max(1, min(24 * 60, int(lead_minutes)))
    reminders: list[ClassScheduleReminder] = []
    for session in sessions:
        if session.cancelled or not is_in_person_session(session):
            continue
        if session.start_at <= current:
            continue
        scheduled_at = session.start_at - timedelta(minutes=lead)
        scheduled_at = _move_out_of_dnd(
            scheduled_at,
            session.start_at,
            dnd_enabled=dnd_enabled,
            dnd_start=dnd_start,
            dnd_end=dnd_end,
        )
        if scheduled_at is None or scheduled_at <= current or scheduled_at >= session.start_at:
            continue
        reminders.append(
            ClassScheduleReminder(
                reminder_id=_stable_reminder_id(session),
                session=session,
                scheduled_at=scheduled_at,
            )
        )
    reminders.sort(key=lambda reminder: (reminder.scheduled_at, reminder.reminder_id))
    return tuple(reminders)
