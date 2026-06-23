"""Extended tests for notifiers.manager — coverage gap filling.

Tests cover:
- _load_cache / _save_cache / _evict_stale_entries
- _filter_assignments: milestones, muted courses, ignored submitted, type filter
- _mark_assignments_notified
- dispatch: full flow with notifiers
- dispatch_grade_alert: full flow with _GradeNotif
- history property
"""
import json
import os
import sys
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def _make_manager(tmp_path, dnd=False):
    """Create NotificationManager with mocked __init__."""
    with patch("notifiers.manager.NotificationManager.__init__", return_value=None):
        from notifiers.manager import NotificationManager
        import threading
        mgr = NotificationManager.__new__(NotificationManager)
        mgr.notifiers = []
        mgr._cache_path = str(tmp_path / "cache.json")
        mgr._cache_lock = threading.Lock()
        mgr._is_in_dnd = Mock(return_value=dnd)
        from core.notification_history import NotificationHistory
        mgr._history = NotificationHistory(history_dir=tmp_path)
        return mgr


class TestCacheLoadSave:
    """_load_cache() and _save_cache() tests."""

    def test_load_empty_cache(self, tmp_path):
        mgr = _make_manager(tmp_path)
        cache = mgr._load_cache()
        assert cache == {}

    def test_save_and_load(self, tmp_path):
        mgr = _make_manager(tmp_path)
        data = {"http://test.com": {"milestones": [24], "updated_at": datetime.now().isoformat()}}
        mgr._save_cache(data)
        loaded = mgr._load_cache()
        assert "http://test.com" in loaded
        assert 24 in loaded["http://test.com"]["milestones"]

    def test_load_corrupted_returns_empty(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with open(mgr._cache_path, "w") as f:
            f.write("NOT JSON")
        cache = mgr._load_cache()
        assert cache == {}

    def test_migrate_old_format(self, tmp_path):
        """Old format: {url: [milestones]} → new: {url: {milestones: [...], updated_at: ...}}"""
        mgr = _make_manager(tmp_path)
        old_data = {"http://test.com": [24, 12]}
        with open(mgr._cache_path, "w") as f:
            json.dump(old_data, f)
        cache = mgr._load_cache()
        assert isinstance(cache["http://test.com"], dict)
        assert cache["http://test.com"]["milestones"] == [24, 12]
        assert "updated_at" in cache["http://test.com"]


class TestEvictStaleEntries:
    """_evict_stale_entries() tests."""

    def test_evict_old_entries(self, tmp_path):
        mgr = _make_manager(tmp_path)
        old_date = (datetime.now() - timedelta(days=100)).isoformat()
        data = {
            "http://old.com": {"milestones": [24], "updated_at": old_date},
            "http://new.com": {"milestones": [12], "updated_at": datetime.now().isoformat()},
        }
        mgr._evict_stale_entries(data)
        assert "http://old.com" not in data
        assert "http://new.com" in data

    def test_evict_invalid_date(self, tmp_path):
        mgr = _make_manager(tmp_path)
        data = {
            "http://bad.com": {"milestones": [], "updated_at": "not-a-date"},
        }
        mgr._evict_stale_entries(data)
        assert "http://bad.com" not in data

    def test_evict_empty_updated_at(self, tmp_path):
        mgr = _make_manager(tmp_path)
        data = {
            "http://empty.com": {"milestones": [], "updated_at": ""},
        }
        mgr._evict_stale_entries(data)
        assert "http://empty.com" not in data

    def test_evict_non_dict_entry(self, tmp_path):
        mgr = _make_manager(tmp_path)
        data = {"http://old.com": "just a string"}
        mgr._evict_stale_entries(data)
        assert "http://old.com" not in data


class TestFilterAssignments:
    """_filter_assignments() tests."""

    def _make_assignment(self, title="A1", course="Web", deadline=None, url="http://test.com/1",
                         event_type="assignment", submission_status="unknown"):
        """Create a mock Assignment-like object."""
        a = Mock()
        a.course_name = course
        a.submission_status = submission_status
        a.event_type = event_type
        a.url = url
        if deadline is None:
            deadline = datetime.now() + timedelta(hours=3)
        a.deadline = deadline
        return a

    @patch("notifiers.manager.config")
    def test_filter_basic_milestone_match(self, mock_config, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_config.NOTIFY_MUTED_COURSES = []
        mock_config.NOTIFY_IGNORE_SUBMITTED = False
        mock_config.NOTIFY_TYPES = None
        mock_config.NOTIFY_MILESTONES = [24, 12, 6, 1]
        mock_config.NOTIFY_MINUTES_BEFORE = 0

        deadline = datetime.now() + timedelta(hours=5)
        a = self._make_assignment(deadline=deadline)
        result = mgr._filter_assignments([a])
        assert len(result) == 1
        assert result[0]["milestone"] == 6

    @patch("notifiers.manager.config")
    def test_filter_muted_course_excluded(self, mock_config, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_config.NOTIFY_MUTED_COURSES = ["Muted Course"]
        mock_config.NOTIFY_IGNORE_SUBMITTED = False
        mock_config.NOTIFY_TYPES = None
        mock_config.NOTIFY_MILESTONES = [24]
        mock_config.NOTIFY_MINUTES_BEFORE = 0

        a = self._make_assignment(course="Muted Course", deadline=datetime.now() + timedelta(hours=3))
        result = mgr._filter_assignments([a])
        assert len(result) == 0

    @patch("notifiers.manager.config")
    def test_filter_submitted_ignored(self, mock_config, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_config.NOTIFY_MUTED_COURSES = []
        mock_config.NOTIFY_IGNORE_SUBMITTED = True
        mock_config.NOTIFY_TYPES = None
        mock_config.NOTIFY_MILESTONES = [24]
        mock_config.NOTIFY_MINUTES_BEFORE = 0

        a = self._make_assignment(submission_status="submitted", deadline=datetime.now() + timedelta(hours=3))
        result = mgr._filter_assignments([a])
        assert len(result) == 0

    @patch("notifiers.manager.config")
    def test_filter_type_restriction(self, mock_config, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_config.NOTIFY_MUTED_COURSES = []
        mock_config.NOTIFY_IGNORE_SUBMITTED = False
        mock_config.NOTIFY_TYPES = ["quiz"]
        mock_config.NOTIFY_MILESTONES = [24]
        mock_config.NOTIFY_MINUTES_BEFORE = 0

        a = self._make_assignment(event_type="assignment", deadline=datetime.now() + timedelta(hours=3))
        result = mgr._filter_assignments([a])
        assert len(result) == 0

    @patch("notifiers.manager.config")
    def test_no_deadline_skipped(self, mock_config, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_config.NOTIFY_MUTED_COURSES = []
        mock_config.NOTIFY_IGNORE_SUBMITTED = False
        mock_config.NOTIFY_TYPES = None
        mock_config.NOTIFY_MILESTONES = [24]
        mock_config.NOTIFY_MINUTES_BEFORE = 0

        a = self._make_assignment()
        a.deadline = None
        result = mgr._filter_assignments([a])
        assert len(result) == 0

    @patch("notifiers.manager.config")
    def test_past_deadline_skipped(self, mock_config, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_config.NOTIFY_MUTED_COURSES = []
        mock_config.NOTIFY_IGNORE_SUBMITTED = False
        mock_config.NOTIFY_TYPES = None
        mock_config.NOTIFY_MILESTONES = [24]
        mock_config.NOTIFY_MINUTES_BEFORE = 0

        a = self._make_assignment(deadline=datetime.now() - timedelta(hours=2))
        result = mgr._filter_assignments([a])
        assert len(result) == 0


class TestMarkNotified:
    """_mark_assignments_notified() tests."""

    @patch("notifiers.manager.config")
    def test_mark_creates_cache_entry(self, mock_config, tmp_path):
        mgr = _make_manager(tmp_path)
        items = [{"url": "http://test.com/1", "milestone": 24, "assignment": Mock()}]
        mgr._mark_assignments_notified(items)
        cache = mgr._load_cache()
        assert "http://test.com/1" in cache
        assert 24 in cache["http://test.com/1"]["milestones"]

    @patch("notifiers.manager.config")
    def test_mark_appends_milestone(self, mock_config, tmp_path):
        mgr = _make_manager(tmp_path)
        # First mark
        mgr._mark_assignments_notified([{"url": "http://x.com", "milestone": 24, "assignment": Mock()}])
        # Second mark
        mgr._mark_assignments_notified([{"url": "http://x.com", "milestone": 12, "assignment": Mock()}])
        cache = mgr._load_cache()
        assert 24 in cache["http://x.com"]["milestones"]
        assert 12 in cache["http://x.com"]["milestones"]


class TestDispatch:
    """dispatch() full flow tests."""

    def test_dispatch_dnd_active_skips(self, tmp_path):
        mgr = _make_manager(tmp_path, dnd=True)
        mock_notifier = Mock()
        mgr.notifiers = [mock_notifier]
        mgr.dispatch([Mock()])
        mock_notifier.notify.assert_not_called()

    @patch("notifiers.manager.config")
    def test_dispatch_no_matching_assignments(self, mock_config, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_config.NOTIFY_MUTED_COURSES = []
        mock_config.NOTIFY_IGNORE_SUBMITTED = False
        mock_config.NOTIFY_TYPES = None
        mock_config.NOTIFY_MILESTONES = [24]
        mock_config.NOTIFY_MINUTES_BEFORE = 0

        a = Mock()
        a.course_name = "X"
        a.submission_status = "unknown"
        a.event_type = "assignment"
        a.url = "http://x.com"
        a.deadline = None  # No deadline → skipped
        mgr.dispatch([a])

    @patch("notifiers.manager.config")
    def test_dispatch_success_marks_and_records_history(self, mock_config, tmp_path):
        """Full success: notifier returns True → mark cache + record history."""
        mgr = _make_manager(tmp_path)
        mock_config.NOTIFY_MUTED_COURSES = []
        mock_config.NOTIFY_IGNORE_SUBMITTED = False
        mock_config.NOTIFY_TYPES = None
        mock_config.NOTIFY_MILESTONES = [24, 12, 6, 1]
        mock_config.NOTIFY_MINUTES_BEFORE = 0

        n = Mock()
        n.notify.return_value = True
        mgr.notifiers = [n]

        a = Mock()
        a.course_name = "Web"
        a.submission_status = "unknown"
        a.event_type = "assignment"
        a.url = "http://test.com/assign/1"
        a.deadline = datetime.now() + timedelta(hours=5)

        mgr.dispatch([a])

        # Notifier was called
        assert n.notify.call_count == 1
        # Cache was updated (milestone marked)
        cache = mgr._load_cache()
        assert "http://test.com/assign/1" in cache
        # History was recorded
        assert len(mgr.history.get_all()) >= 1

    @patch("notifiers.manager.config")
    def test_dispatch_all_fail_no_mark(self, mock_config, tmp_path):
        """All notifiers fail → don't mark, retry later."""
        mgr = _make_manager(tmp_path)
        mock_config.NOTIFY_MUTED_COURSES = []
        mock_config.NOTIFY_IGNORE_SUBMITTED = False
        mock_config.NOTIFY_TYPES = None
        mock_config.NOTIFY_MILESTONES = [24, 12, 6, 1]
        mock_config.NOTIFY_MINUTES_BEFORE = 0

        n = Mock()
        n.notify.return_value = False
        mgr.notifiers = [n]

        a = Mock()
        a.course_name = "Web"
        a.submission_status = "unknown"
        a.event_type = "assignment"
        a.url = "http://test.com/assign/2"
        a.deadline = datetime.now() + timedelta(hours=5)

        mgr.dispatch([a])

        # Notifier was called
        assert n.notify.call_count == 1
        # Cache NOT updated (will retry next time)
        cache = mgr._load_cache()
        assert "http://test.com/assign/2" not in cache

    @patch("notifiers.manager.config")
    def test_dispatch_notifier_exception_handled(self, mock_config, tmp_path):
        """Notifier raises exception → caught, treated as failure."""
        mgr = _make_manager(tmp_path)
        mock_config.NOTIFY_MUTED_COURSES = []
        mock_config.NOTIFY_IGNORE_SUBMITTED = False
        mock_config.NOTIFY_TYPES = None
        mock_config.NOTIFY_MILESTONES = [24]
        mock_config.NOTIFY_MINUTES_BEFORE = 0

        n = Mock()
        n.notify.side_effect = Exception("Connection refused")
        mgr.notifiers = [n]

        a = Mock()
        a.course_name = "Math"
        a.submission_status = "unknown"
        a.event_type = "quiz"
        a.url = "http://test.com/quiz/1"
        a.deadline = datetime.now() + timedelta(hours=5)

        mgr.dispatch([a])  # Should not raise


class TestDispatchGradeAlert:
    """dispatch_grade_alert() full flow tests."""

    def test_full_flow(self, tmp_path):
        mgr = _make_manager(tmp_path)
        n = Mock()
        mgr.notifiers = [n]

        @dataclass
        class GC:
            course_name: str
            item_name: str
            old_grade: str
            new_grade: str

        mgr.dispatch_grade_alert([GC("Web", "CK", "7.0", "8.0")])
        assert n.notify.call_count == 1
        call_args = n.notify.call_args[0][0]
        assert len(call_args) == 1
        assert "Điểm mới" in call_args[0].title

    def test_old_grade_none(self, tmp_path):
        mgr = _make_manager(tmp_path)
        n = Mock()
        mgr.notifiers = [n]

        @dataclass
        class GC:
            course_name: str
            item_name: str
            old_grade: str
            new_grade: str

        mgr.dispatch_grade_alert([GC("Math", "GK", None, "9.0")])
        assert n.notify.call_count == 1


class TestHistoryProperty:
    """history property tests."""

    def test_history_accessible(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr.history is not None
        assert mgr.history.get_all() == []
