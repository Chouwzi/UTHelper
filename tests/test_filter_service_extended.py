"""Extended tests for core.filter_service — coverage gap filling.

Tests cover uncovered branches:
- Overdue filtering (include/exclude)
- Type filter with _TYPE_FILTER_MAP
- Course filter specific course
- Search query matching
- Open time override
- Pre-cached datetime fields
"""
import os
import sys
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.filter_service import FilterService


def _make_activity(
    title="Test",
    course="Web",
    deadline=None,
    urgency="safe",
    event_type="assignment",
    is_open=False,
    details=None,
    submission_status="unknown",
):
    """Create a test activity dict."""
    if deadline is None:
        deadline = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "title": title,
        "course": course,
        "deadline": deadline,
        "urgency": urgency,
        "type": event_type,
        "is_open": is_open,
        "details": details or {},
        "submission_status": submission_status,
    }


class TestFilterOverdue:
    """Overdue item filtering."""

    def test_exclude_overdue_by_default(self):
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        activities = [_make_activity(deadline=past)]
        results, counts = FilterService.filter_and_count(activities, include_overdue=False)
        assert len(results) == 0

    def test_include_overdue_when_requested(self):
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        activities = [_make_activity(deadline=past)]
        results, counts = FilterService.filter_and_count(activities, include_overdue=True)
        assert len(results) == 1

    def test_overdue_filter_shows_overdue_items(self):
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        activities = [_make_activity(deadline=past)]
        results, counts = FilterService.filter_and_count(
            activities, active_urgency="overdue", include_overdue=False
        )
        # active_urgency == "overdue" should allow overdue items through
        assert len(results) == 1

    def test_overdue_counted_correctly(self):
        past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        future = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        activities = [
            _make_activity(title="Overdue", deadline=past),
            _make_activity(title="Active", deadline=future),
        ]
        results, counts = FilterService.filter_and_count(activities, include_overdue=True)
        assert counts["urgency"]["overdue"] == 1


class TestFilterType:
    """Type-based filtering."""

    def test_filter_assignment_type(self):
        activities = [
            _make_activity(title="A1", event_type="assignment"),
            _make_activity(title="Q1", event_type="quiz"),
        ]
        results, counts = FilterService.filter_and_count(activities, active_type="assignment")
        assert len(results) == 1
        assert results[0]["title"] == "A1"

    def test_filter_quiz_type(self):
        activities = [
            _make_activity(title="A1", event_type="assignment"),
            _make_activity(title="Q1", event_type="quiz"),
        ]
        results, counts = FilterService.filter_and_count(activities, active_type="quiz")
        assert len(results) == 1
        assert results[0]["title"] == "Q1"

    def test_open_type_filter(self):
        activities = [
            _make_activity(title="Regular", event_type="assignment", is_open=False),
            _make_activity(title="Open", event_type="assignment", is_open=True),
        ]
        results, counts = FilterService.filter_and_count(activities, active_type="open")
        assert len(results) == 1
        assert results[0]["title"] == "Open"


class TestFilterCourse:
    """Course-based filtering."""

    def test_specific_course(self):
        activities = [
            _make_activity(title="A1", course="Web"),
            _make_activity(title="A2", course="Math"),
        ]
        results, counts = FilterService.filter_and_count(activities, active_course="Web")
        assert len(results) == 1
        assert results[0]["title"] == "A1"


class TestFilterSearch:
    """Search query filtering."""

    def test_search_by_title(self):
        activities = [
            _make_activity(title="Bài tập lập trình"),
            _make_activity(title="Quiz toán"),
        ]
        results, _ = FilterService.filter_and_count(activities, search_query="lập trình")
        assert len(results) == 1

    def test_search_by_course(self):
        activities = [
            _make_activity(title="A1", course="Lập trình Web"),
            _make_activity(title="A2", course="Toán cao cấp"),
        ]
        results, _ = FilterService.filter_and_count(activities, search_query="toán")
        assert len(results) == 1

    def test_search_case_insensitive(self):
        activities = [_make_activity(title="KIỂM TRA")]
        results, _ = FilterService.filter_and_count(activities, search_query="kiểm tra")
        assert len(results) == 1

    def test_empty_search_returns_all(self):
        activities = [_make_activity(), _make_activity()]
        results, _ = FilterService.filter_and_count(activities, search_query="")
        assert len(results) == 2


class TestFilterCounts:
    """Count dict structure."""

    def test_counts_structure(self):
        activities = [_make_activity()]
        _, counts = FilterService.filter_and_count(activities)
        assert "urgency" in counts
        assert "type" in counts
        assert "course" in counts
        assert counts["urgency"]["all"] == 1
        assert counts["type"]["all"] == 1
        assert counts["course"]["all"] == 1

    def test_urgency_counts(self):
        activities = [
            _make_activity(urgency="critical"),
            _make_activity(urgency="warning"),
            _make_activity(urgency="safe"),
        ]
        _, counts = FilterService.filter_and_count(activities)
        assert counts["urgency"]["all"] == 3

    def test_type_counts_multiple_types(self):
        activities = [
            _make_activity(event_type="assignment"),
            _make_activity(event_type="assignment"),
            _make_activity(event_type="quiz"),
        ]
        _, counts = FilterService.filter_and_count(activities)
        assert counts["type"]["assignment"] == 2
        assert counts["type"]["quiz"] == 1


class TestFilterOpenTimeOverride:
    """Open time override from details."""

    def test_future_open_time_marks_as_open(self):
        future_open = (datetime.now() + timedelta(hours=2)).isoformat()
        activities = [
            _make_activity(
                title="Future Open",
                event_type="quiz",
                details={"open_time": future_open},
            )
        ]
        _, counts = FilterService.filter_and_count(activities)
        assert counts["type"].get("open", 0) == 1

    def test_past_open_time_not_override(self):
        past_open = (datetime.now() - timedelta(hours=2)).isoformat()
        activities = [
            _make_activity(
                title="Past Open",
                event_type="quiz",
                details={"open_time": past_open},
            )
        ]
        _, counts = FilterService.filter_and_count(activities)
        assert counts["type"].get("quiz", 0) == 1
