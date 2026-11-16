from datetime import date

import pytest

from gui.components.calendar_navigation import (
    MonthAnchor,
    month_slot_anchors,
    resolve_mouse_navigation,
    shift_month,
    shift_week,
    week_slot_anchors,
)


def test_shift_month_crosses_both_year_boundaries():
    assert shift_month(MonthAnchor(2026, 12), 1) == MonthAnchor(2027, 1)
    assert shift_month(MonthAnchor(2026, 1), -1) == MonthAnchor(2025, 12)


def test_period_slots_are_previous_current_next():
    assert month_slot_anchors(MonthAnchor(2026, 1)) == (
        MonthAnchor(2025, 12),
        MonthAnchor(2026, 1),
        MonthAnchor(2026, 2),
    )
    assert week_slot_anchors(date(2026, 12, 28)) == (
        date(2026, 12, 21),
        date(2026, 12, 28),
        date(2027, 1, 4),
    )


def test_period_shifts_stay_at_representable_date_boundaries():
    assert shift_month(MonthAnchor(1, 1), -1) == MonthAnchor(1, 1)
    assert shift_month(MonthAnchor(9999, 12), 1) == MonthAnchor(9999, 12)
    assert shift_week(date.min, -1) == date.min
    assert shift_week(date.max, 1) == date.max


@pytest.mark.parametrize(
    ("distance", "velocity", "expected"),
    [
        (-60, -100, 1),
        (60, 100, -1),
        (-8, -800, 1),
        (8, 800, -1),
        (20, 200, 0),
        (60, -800, 0),
        (0, None, 0),
    ],
)
def test_mouse_navigation_requires_distance_or_consistent_flick(
    distance, velocity, expected
):
    assert resolve_mouse_navigation(distance, velocity) == expected
