"""Pure calendar period and pointer-navigation policy."""

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True, slots=True)
class MonthAnchor:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.year <= 9999:
            raise ValueError("year must be between 1 and 9999")
        if not 1 <= self.month <= 12:
            raise ValueError("month must be between 1 and 12")


def shift_month(anchor: MonthAnchor, delta: int) -> MonthAnchor:
    ordinal = anchor.year * 12 + anchor.month - 1 + delta
    year, zero_based_month = divmod(ordinal, 12)
    if not 1 <= year <= 9999:
        return anchor
    return MonthAnchor(year, zero_based_month + 1)


def month_slot_anchors(
    anchor: MonthAnchor,
) -> tuple[MonthAnchor, MonthAnchor, MonthAnchor]:
    return shift_month(anchor, -1), anchor, shift_month(anchor, 1)


def shift_week(week_start: date, delta: int) -> date:
    try:
        return week_start + timedelta(weeks=delta)
    except OverflowError:
        return week_start


def week_slot_anchors(week_start: date) -> tuple[date, date, date]:
    return shift_week(week_start, -1), week_start, shift_week(week_start, 1)


def resolve_mouse_navigation(
    distance_x: float,
    velocity_x: float | None,
    *,
    distance_threshold: float = 52.0,
    velocity_threshold: float = 700.0,
) -> int:
    """Resolve pointer motion to previous (-1), next (1), or cancel (0)."""
    velocity_x = velocity_x or 0.0
    distance_direction = _sign(distance_x)
    velocity_direction = _sign(velocity_x)
    if (
        distance_direction
        and velocity_direction
        and distance_direction != velocity_direction
    ):
        return 0
    accepted = (
        abs(distance_x) >= distance_threshold
        or abs(velocity_x) >= velocity_threshold
    )
    pointer_direction = distance_direction or velocity_direction
    return -pointer_direction if accepted else 0


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0
