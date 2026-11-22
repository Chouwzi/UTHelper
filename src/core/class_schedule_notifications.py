"""Schedule local reminders for online and in-person Portal class sessions.

The module consumes Portal sessions already fetched by the foreground app. It
never infers a delivery mode from an empty location: explicit online markers
identify remote sessions, while a room/campus identifies an in-person session.
Ambiguous sessions are skipped until Portal provides enough information.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from core.today_schedule import ClassSession

SUPPORTED_LEAD_HOURS = (1, 2, 3)

# The reconciler only arms classes starting within this many days of ``now``
# (today's sessions already arrive with the dashboard snapshot). The window
# slides forward on every reconcile, so a student who keeps the app closed for
# a whole week still receives the reminders that were persisted on the last
# foreground run instead of losing them after tomorrow.
REMINDER_LOOKAHEAD_DAYS = 14

_ONLINE_MARKERS = (
    "truc tuyen",
    "zoom",
    "google meet",
    "meet.google.com",
    "microsoft teams",
    "ms teams",
    "teams.microsoft.com",
    "google classroom",
    "classroom.google.com",
    "webex",
    "hinh thuc online",
)

# The location fields may say "Online" outright, so they accept the broad
# words as well. Notes deliberately do not: "nộp bài trên elearning" or
# "không học online" describe homework, not the delivery mode of a class.
_LOCATION_ONLINE_MARKERS = _ONLINE_MARKERS + ("online", "elearning", "e-learning")


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFD", value.casefold())
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return " ".join(value.replace("đ", "d").split())


def detect_delivery_mode(session: ClassSession) -> str | None:
    """Return ``online``, ``in_person`` or None for ambiguous Portal rows.

    A physical room/campus wins any conflict: the note may mention an online
    platform without the class itself being remote. Only an explicit online
    marker inside the location fields, or — when no location exists at all —
    inside the note, makes a session online. Broad words such as bare
    "online"/"elearning" are not note markers because they appear in negated
    or unrelated notes; ambiguous rows are skipped instead of guessed.
    """
    room = _normalize((session.room or "").strip())
    campus = _normalize((session.campus or "").strip())
    note = _normalize((session.note or "").strip())
    location = " ".join(part for part in (room, campus) if part)
    if location:
        if any(marker in location for marker in _LOCATION_ONLINE_MARKERS):
            return "online"
        return "in_person"
    if note and any(marker in note for marker in _ONLINE_MARKERS):
        return "online"
    return None


@dataclass(frozen=True, slots=True)
class ClassScheduleReminder:
    reminder_id: str
    session: ClassSession
    scheduled_at: datetime
    lead_hours: int
    delivery_mode: str

    @property
    def start_at(self) -> datetime:
        return self.session.start_at

    def to_platform_payload(self) -> dict[str, object]:
        """Return primitive data accepted by Android/iOS notification bridges."""
        return {
            "reminder_id": self.reminder_id,
            "session_id": self.session.session_id,
            "subject": self.session.subject,
            "course_code": self.session.course_code,
            "room": self.session.room,
            "campus": self.session.campus,
            "note": self.session.note,
            "delivery_mode": self.delivery_mode,
            "lead_hours": self.lead_hours,
            "start_at_epoch_seconds": int(self.session.start_at.timestamp()),
            "scheduled_at_epoch_seconds": int(self.scheduled_at.timestamp()),
        }


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
    if start > end:
        return moment.hour >= start or moment.hour < end
    return start <= moment.hour < end


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


def _stable_reminder_id(session: ClassSession, scheduled_at: datetime) -> str:
    """Identify one reminder slot: session identity plus the fire time.

    The DND shift can collapse several lead times onto the same instant, so
    the id derives from ``scheduled_at`` (not the original lead) and lets the
    caller merge duplicates by id.
    """
    identity = session.session_id.strip() or "|".join(
        (
            session.course_code,
            session.subject,
            session.start_at.isoformat(),
            session.room,
            session.campus,
            session.note,
        )
    )
    raw = f"{identity}|{session.start_at.isoformat()}|{scheduled_at.isoformat()}|class-schedule"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"class:{digest}"


def _effective_lead_hours(class_start: datetime, scheduled_at: datetime) -> int:
    """Label a reminder with the lead that is true when it actually fires.

    DND moves a reminder later, so the original 1/2/3-hour label can be stale
    ("Còn 3 giờ nữa" thirty minutes before class). The payload contract only
    accepts integers 1..3, hence the half-up rounding and clamping.
    """
    actual = (class_start - scheduled_at).total_seconds() / 3600.0
    return max(1, min(3, int(actual + 0.5)))


def build_class_schedule_reminders(
    sessions: Iterable[ClassSession],
    *,
    now: datetime | None = None,
    enabled: bool = False,
    lead_hours: Iterable[int] = SUPPORTED_LEAD_HOURS,
    dnd_enabled: bool = False,
    dnd_start: int = 22,
    dnd_end: int = 7,
) -> tuple[ClassScheduleReminder, ...]:
    """Create deduplicated reminders at each selected 1/2/3-hour lead time.

    Sessions starting more than ``REMINDER_LOOKAHEAD_DAYS`` after ``now`` are
    skipped: the next reconcile slides the window forward and arms them then.
    """
    if not enabled:
        return ()
    current = now or datetime.now()
    selected: set[int] = set()
    for value in lead_hours:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed in SUPPORTED_LEAD_HOURS:
            selected.add(parsed)
    selected_leads = tuple(sorted(selected))
    if not selected_leads:
        return ()

    reminders: list[ClassScheduleReminder] = []
    seen: set[str] = set()
    horizon = current + timedelta(days=REMINDER_LOOKAHEAD_DAYS)
    for session in sessions:
        if session.cancelled or session.start_at <= current:
            continue
        if session.start_at > horizon:
            continue
        mode = detect_delivery_mode(session)
        if mode is None:
            continue
        for lead in selected_leads:
            scheduled_at = session.start_at - timedelta(hours=lead)
            scheduled_at = _move_out_of_dnd(
                scheduled_at,
                session.start_at,
                dnd_enabled=dnd_enabled,
                dnd_start=dnd_start,
                dnd_end=dnd_end,
            )
            if scheduled_at is None or scheduled_at <= current or scheduled_at >= session.start_at:
                continue
            # Several leads can collapse onto one instant after the DND shift;
            # the slot-derived id merges them and the label follows the real
            # remaining time instead of the original lead.
            reminder_id = _stable_reminder_id(session, scheduled_at)
            if reminder_id in seen:
                continue
            seen.add(reminder_id)
            reminders.append(
                ClassScheduleReminder(
                    reminder_id=reminder_id,
                    session=session,
                    scheduled_at=scheduled_at,
                    lead_hours=_effective_lead_hours(session.start_at, scheduled_at),
                    delivery_mode=mode,
                )
            )

    reminders.sort(key=lambda item: (item.scheduled_at, item.reminder_id))
    return tuple(reminders)
