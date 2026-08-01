"""Pure policy for activity filtering, milestones, DND, and native schedules."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from core.notification_types import (
    ActivityNotification,
    NotificationCandidate,
    ScheduledReminder,
)


@dataclass(frozen=True)
class NotificationPolicyConfig:
    notify_types: tuple[str, ...] = ()
    muted_courses: tuple[str, ...] = ()
    ignore_submitted: bool = True
    milestone_minutes: tuple[int, ...] = ()
    # Compatibility with settings written before schema v2.
    milestones: tuple[int, ...] = (72, 24, 3)
    minutes_before: int = 0
    dnd_enabled: bool = False
    dnd_start: int = 22
    dnd_end: int = 7

    @property
    def effective_milestone_minutes(self) -> tuple[int, ...]:
        if self.milestone_minutes:
            values = self.milestone_minutes
        else:
            values = tuple(value * 60 for value in self.milestones)
            if self.minutes_before > 0:
                values += (self.minutes_before,)
        return tuple(sorted({int(value) for value in values if int(value) > 0}))

    @property
    def milestone_specs(self) -> tuple[tuple[int | str, int], ...]:
        """Return (receipt identifier, minutes) with legacy compatibility."""
        if self.milestone_minutes:
            return tuple((value, value) for value in self.effective_milestone_minutes)
        specs: list[tuple[int | str, int]] = [
            (value, value * 60)
            for value in sorted({int(item) for item in self.milestones if int(item) > 0})
        ]
        if self.minutes_before > 0:
            specs.append((f"_min_{self.minutes_before}", self.minutes_before))
        return tuple(sorted(specs, key=lambda item: item[1]))


class ActivityNotificationPolicy:
    """Deterministic notification rules with no platform dependencies."""

    _IGNORED_STATUSES = {"submitted", "graded", "đã nộp", "đã chấm"}

    def __init__(self, policy_config: NotificationPolicyConfig):
        self.config = policy_config

    def is_dnd(self, at: datetime) -> bool:
        if not self.config.dnd_enabled:
            return False
        start = self.config.dnd_start
        end = self.config.dnd_end
        if start == end:
            return True
        if start > end:
            return at.hour >= start or at.hour < end
        return start <= at.hour < end

    def accepts(self, activity: ActivityNotification) -> bool:
        muted_courses = {
            value.strip().casefold()
            for value in self.config.muted_courses
            if value and value.strip()
        }
        if activity.course_name.strip().casefold() in muted_courses:
            return False
        if (
            self.config.ignore_submitted
            and activity.submission_status in self._IGNORED_STATUSES
        ):
            return False
        if self.config.notify_types and activity.event_type:
            if activity.event_type not in self.config.notify_types:
                return False
        return activity.deadline is not None

    def due_candidate(
        self,
        activity: ActivityNotification,
        sent_milestones: Iterable[int | str],
        now: datetime,
    ) -> NotificationCandidate | None:
        if not self.accepts(activity) or not activity.deadline:
            return None
        remaining = activity.deadline - now
        if remaining.total_seconds() < 0:
            return None

        sent = set(sent_milestones)
        matched: int | str | None = None
        remaining_minutes = remaining.total_seconds() / 60
        for milestone, minutes in self.config.milestone_specs:
            if remaining_minutes <= minutes:
                matched = milestone
                break

        if matched is None or matched in sent:
            return None
        return NotificationCandidate(activity=activity, milestone=matched)

    def desired_schedules(
        self,
        activities: Iterable[ActivityNotification],
        now: datetime,
    ) -> list[ScheduledReminder]:
        # Equal DND endpoints intentionally mean quiet for the full day.  There
        # is no valid time to move a reminder to without violating that rule.
        if (
            self.config.dnd_enabled
            and self.config.dnd_start == self.config.dnd_end
        ):
            return []
        reminders: list[ScheduledReminder] = []
        for activity in activities:
            if not self.accepts(activity) or not activity.deadline:
                continue
            reminder_specs: list[tuple[int | str, datetime]] = [
                (milestone, activity.deadline - timedelta(minutes=minutes))
                for milestone, minutes in self.config.milestone_specs
            ]

            seen_times: set[datetime] = set()
            for milestone, scheduled_at in reminder_specs:
                scheduled_at = self._move_out_of_dnd(scheduled_at, activity.deadline)
                if scheduled_at <= now or scheduled_at >= activity.deadline:
                    continue
                # Multiple milestones inside DND may move to the same instant;
                # emit one reminder instead of a burst when quiet time ends.
                if scheduled_at in seen_times:
                    continue
                seen_times.add(scheduled_at)
                reminders.append(
                    ScheduledReminder(
                        activity=activity,
                        milestone=milestone,
                        notification_id=stable_notification_id(
                            activity.key, milestone
                        ),
                        scheduled_at=scheduled_at,
                    )
                )
        return reminders

    def _move_out_of_dnd(self, scheduled_at: datetime, deadline: datetime) -> datetime:
        if not self.is_dnd(scheduled_at) or self.config.dnd_start == self.config.dnd_end:
            return scheduled_at
        end = self.config.dnd_end
        candidate = scheduled_at.replace(hour=end, minute=0, second=0, microsecond=0)
        if self.config.dnd_start > end and scheduled_at.hour >= self.config.dnd_start:
            candidate += timedelta(days=1)
        return min(candidate, deadline)


def stable_notification_id(activity_key: str, milestone: int | str) -> int:
    """Return a stable positive 31-bit Android notification identifier."""
    digest = hashlib.blake2s(
        f"{activity_key}|{milestone}".encode("utf-8"), digest_size=4
    ).digest()
    value = int.from_bytes(digest, "big") & 0x7FFFFFFF
    return value or 1
