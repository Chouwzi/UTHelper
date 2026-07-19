from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping, Optional

from core.time_utils import parse_datetime


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

        remaining = int((parsed - self.clock()).total_seconds())
        if remaining < 0:
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
