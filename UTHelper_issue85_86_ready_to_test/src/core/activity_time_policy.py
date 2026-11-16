from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping, Optional

from core.activity_status import normalize_submission_status
from core.time_utils import parse_datetime


def is_late_submission_active(activity: Mapping, now_dt: Optional[datetime] = None) -> bool:
    """Return True if an activity's deadline has passed but late submission is still allowed."""
    sub_status = normalize_submission_status(activity.get("submission_status", "unknown"))
    if sub_status in ("submitted", "graded"):
        return False

    late_status = activity.get("late_status", "")
    if late_status == "closed":
        return False

    cutoff = activity.get("cutoff_date") or activity.get("cutoffdate")
    now = now_dt or datetime.now()
    deadline = activity.get("deadline") or activity.get("deadline_str")
    parsed_deadline = parse_datetime(str(deadline)) if deadline else None
    if parsed_deadline is None or parsed_deadline >= now:
        return False
    now_ts = now.timestamp()

    cutoff_ts: Optional[float] = None
    if isinstance(cutoff, (int, float)) and cutoff > 0:
        cutoff_ts = float(cutoff)
    elif isinstance(cutoff, str) and cutoff.strip():
        try:
            cutoff_ts = float(cutoff)
        except ValueError:
            parsed = parse_datetime(cutoff)
            if parsed:
                cutoff_ts = parsed.timestamp()

    if cutoff_ts is not None and cutoff_ts > 0:
        return now_ts < cutoff_ts

    return late_status == "late_allowed"


@dataclass(frozen=True)
class ActivityTimeState:
    remaining_seconds: Optional[int]
    urgency: str
    overdue: bool
    progress: float


class ActivityTimePolicy:
    """Derive volatile activity state from an absolute deadline and a clock."""

    def __init__(
        self,
        critical_hours: int = 24,
        warning_hours: int = 72,
        *,
        clock: Callable[[], datetime] = datetime.now,
    ):
        self.critical_seconds = max(1, int(critical_hours)) * 3600
        self.warning_seconds = max(1, int(warning_hours)) * 3600
        self.clock = clock

    def evaluate(self, activity: Mapping) -> ActivityTimeState:
        deadline = activity.get("deadline") or activity.get("deadline_str")
        parsed = parse_datetime(str(deadline)) if deadline else None
        if parsed is None or parsed.year >= 2099:
            return ActivityTimeState(None, "safe", False, 1.0)

        now_dt = self.clock()
        remaining = int((parsed - now_dt).total_seconds())
        if remaining < 0:
            if is_late_submission_active(activity, now_dt):
                urgency = "critical"
            else:
                urgency = "overdue"
        elif remaining < self.critical_seconds:
            urgency = "critical"
        elif remaining < self.warning_seconds:
            urgency = "warning"
        else:
            urgency = "safe"
        progress = 0.0 if remaining <= 0 else min(remaining / (7 * 86400), 1.0)
        return ActivityTimeState(remaining, urgency, remaining < 0, progress)

    def refresh(self, activity: Mapping) -> dict:
        """Return a copy with current derived fields; never persist countdown text."""
        state = self.evaluate(activity)
        result = dict(activity)
        result["urgency"] = state.urgency
        result["_remaining_seconds"] = state.remaining_seconds
        result["_overdue"] = state.overdue
        return result
