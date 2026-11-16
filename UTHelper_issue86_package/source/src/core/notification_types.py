"""Platform-neutral notification data contracts.

These types intentionally do not import Flet, platform adapters, or global
settings.  They are shared by the notification policy, manager, tests, and
future background workers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from typing import Any

from core.time_utils import parse_datetime


def _read(value: Any, *names: str, default: Any = "") -> Any:
    for name in names:
        if isinstance(value, dict):
            result = value.get(name)
        else:
            result = getattr(value, name, None)
        if result not in (None, ""):
            return result
    return default


def _as_text(value: Any, default: str = "") -> str:
    if isinstance(value, (str, int, float)):
        return str(value)
    return default


@dataclass(frozen=True)
class ActivityNotification:
    """Minimal activity snapshot required by notification policies."""

    activity_id: str
    course_id: str
    course_name: str
    title: str
    event_type: str
    deadline: datetime | None
    url: str
    submission_status: str
    revision: str = ""
    site: str = ""
    user_id: str = ""
    source_kind: str = ""
    module_id: str = ""
    source: Any = field(default=None, compare=False, repr=False)

    @property
    def key(self) -> str:
        """Stable cache key, preferring Moodle activity/event identifiers."""
        namespace = ":".join(
            value.strip().lower()
            for value in (self.site, self.user_id)
            if value and value.strip()
        )
        source_identity = self.module_id or self.activity_id
        if namespace and source_identity:
            kind = self.source_kind or self.event_type or "activity"
            return f"{namespace}:{kind}:{source_identity}"
        if self.activity_id:
            return f"activity:{self.activity_id}"
        if self.url:
            # Preserve legacy URL cache keys while preferring activity IDs.
            return self.url
        return f"fallback:{self.course_id}:{self.event_type}:{self.title}"

    @property
    def deadline_revision(self) -> str:
        """Revision used by delivery receipts when a deadline is moved."""
        deadline_value = self.deadline.isoformat() if self.deadline else ""
        raw = f"{self.revision}|{deadline_value}"
        return hashlib.blake2s(raw.encode("utf-8"), digest_size=8).hexdigest()

    @classmethod
    def from_value(cls, value: Any) -> "ActivityNotification":
        deadline = _read(value, "deadline", default=None)
        if deadline and not isinstance(deadline, datetime):
            deadline = parse_datetime(deadline)

        return cls(
            activity_id=_as_text(_read(value, "id", "activity_id", "event_id")),
            course_id=_as_text(_read(value, "course_id", "courseid")),
            course_name=_as_text(_read(value, "course_name", "course")),
            title=_as_text(_read(value, "title", "name")),
            event_type=_as_text(_read(value, "event_type", "type")),
            deadline=deadline if isinstance(deadline, datetime) else None,
            url=_as_text(_read(value, "url")),
            submission_status=_as_text(
                _read(value, "submission_status", default="unknown"), "unknown"
            ).lower(),
            revision=_as_text(
                _read(value, "revision", "timemodified", "updated_at")
            ),
            site=_as_text(_read(value, "site", "moodle_site", "base_url")),
            user_id=_as_text(_read(value, "user_id", "userid")),
            source_kind=_as_text(_read(value, "source_kind", "source_type")),
            module_id=_as_text(_read(value, "cmid", "module_id", "instance")),
            source=value,
        )


@dataclass(frozen=True)
class NotificationCandidate:
    activity: ActivityNotification
    milestone: int | str


@dataclass(frozen=True)
class ScheduledReminder:
    activity: ActivityNotification
    milestone: int | str
    notification_id: int
    scheduled_at: datetime

    @property
    def state_key(self) -> str:
        return str(self.notification_id)

    @property
    def delivery_key(self) -> str:
        return (
            f"{self.activity.key}|{self.activity.deadline_revision}|"
            f"{self.milestone}"
        )


@dataclass
class DispatchResult:
    attempted: int = 0
    delivered: int = 0
    filtered: int = 0
    successful_channels: list[str] = field(default_factory=list)
    failed_channels: dict[str, str] = field(default_factory=dict)
    milestones: list[int | str] = field(default_factory=list)
    dnd_active: bool = False

    @property
    def succeeded(self) -> bool:
        return self.delivered > 0


@dataclass
class ScheduleResult:
    desired: int = 0
    scheduled: int = 0
    cancelled: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class NotificationDiagnostics:
    backend_names: list[str] = field(default_factory=list)
    last_fetch_at: str = ""
    activities_seen: int = 0
    activities_matched: int = 0
    delivered: int = 0
    skipped: int = 0
    scheduled: int = 0
    cancelled: int = 0
    pending_schedules: int = 0
    scheduled_delivered: int = 0
    last_scheduled_delivery_at: str = ""
    last_error: str = ""
