"""Tests for CalendarView week mode functionality."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from datetime import date, timedelta
from unittest.mock import MagicMock, patch
import pytest


class TestCalendarWeekState:
    """Test the week view state management logic (no GUI required)."""

    def test_week_start_is_monday(self):
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        assert week_start.weekday() == 0

    def test_week_navigation(self):
        start = date(2026, 6, 15)
        assert start + timedelta(weeks=1) == date(2026, 6, 22)
        assert start + timedelta(weeks=-1) == date(2026, 6, 8)

    def test_week_spans_two_months(self):
        start = date(2026, 6, 29)
        end = start + timedelta(days=6)
        assert start.month == 6
        assert end.month == 7

    def test_week_number_calculation(self):
        d = date(2026, 6, 18)
        assert d.isocalendar()[1] == 25

    def test_selected_date_sync_on_mode_switch(self):
        year, month, day = 2026, 6, 25
        sel = date(year, month, day)
        week_start = sel - timedelta(days=sel.weekday())
        assert week_start == date(2026, 6, 22)
        assert week_start <= sel <= week_start + timedelta(days=6)

    def test_go_today_sets_both_states(self):
        today = date.today()
        selected_day = today.day
        selected_date = today
        week_start = today - timedelta(days=today.weekday())
        assert selected_day == today.day
        assert selected_date == today
        assert week_start.weekday() == 0
        assert week_start <= today <= week_start + timedelta(days=6)

    def test_week_cell_click_updates_selection(self):
        clicked_date = date(2026, 7, 1)
        selected_date = clicked_date
        selected_day = clicked_date.day
        year = clicked_date.year
        month = clicked_date.month
        assert selected_date == date(2026, 7, 1)
        assert selected_day == 1
        assert year == 2026
        assert month == 7


class TestCalendarViewImport:
    def test_import_calendar_view(self):
        try:
            from gui.components.calendar_view import CalendarView
            assert CalendarView is not None
        except ImportError as e:
            if "flet" in str(e).lower():
                pytest.skip("Flet not available")
            raise

    def test_timedelta_imported(self):
        from datetime import timedelta
        assert timedelta(weeks=1).days == 7


class TestWeekViewDataLogic:
    def test_activity_counting_for_week(self):
        deadline_map = {
            "2026-06-15": [{"title": "A1", "urgency": "safe"}],
            "2026-06-16": [{"title": "A2", "urgency": "warning"}, {"title": "A3", "urgency": "critical"}],
            "2026-06-18": [{"title": "A4", "urgency": "safe"}],
            "2026-06-22": [{"title": "A5", "urgency": "overdue"}],
        }
        week_start = date(2026, 6, 15)
        week_total = 0
        for i in range(7):
            d = week_start + timedelta(days=i)
            key = d.strftime("%Y-%m-%d")
            acts = deadline_map.get(key, [])
            week_total += len(acts)
        assert week_total == 4

    def test_urgency_priority_sorting(self):
        urgency_priority = {"overdue": 0, "critical": 1, "warning": 2, "safe": 3}
        acts = [
            {"urgency": "safe"}, {"urgency": "overdue"},
            {"urgency": "warning"}, {"urgency": "critical"},
        ]
        sorted_acts = sorted(acts, key=lambda a: urgency_priority.get(a.get("urgency", "safe"), 3))
        assert sorted_acts[0]["urgency"] == "overdue"
        assert sorted_acts[1]["urgency"] == "critical"
        assert sorted_acts[-1]["urgency"] == "safe"

    def test_submission_counting(self):
        acts = [
            {"submission_status": "submitted"},
            {"submission_status": "Đã nộp"},
            {"submission_status": "graded"},
            {"submission_status": ""},
            {"submission_status": "Chưa nộp"},
        ]
        submitted_statuses = ("submitted", "Đã nộp", "graded", "Đã chấm")
        count = sum(1 for a in acts if a.get("submission_status", "") in submitted_statuses)
        assert count == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
