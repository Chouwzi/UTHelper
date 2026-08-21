"""Tests for core.data_orchestrator — detail cache and smart polling.

Tests verify:
- Detail LRU cache: get/set, TTL expiry, max entries eviction
- get_updates_since: detects changes, returns empty, handles errors
- get_cached_details_snapshot returns copy
"""
import os
import sys
import threading
import time
from datetime import datetime
import pytest
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestDetailCache:
    """DataOrchestrator detail cache (LRU, TTL)."""

    def _make_orchestrator(self):
        """Create DataOrchestrator with mocked dependencies."""
        with patch("core.data_orchestrator.MoodleClient"), \
             patch("core.data_orchestrator.GradeMonitor"):
            from core.data_orchestrator import DataOrchestrator
            orch = DataOrchestrator()
            # Override TTL for testing
            orch._detail_cache_ttl_seconds = 2  # 2 seconds for fast expiry
            orch._detail_cache_max_entries = 3
            return orch

    def test_set_and_get_cached_detail(self):
        orch = self._make_orchestrator()
        orch._set_cached_detail("http://test.com/1", {"title": "Test"})
        result = orch._get_cached_detail("http://test.com/1")
        assert result is not None
        assert result["title"] == "Test"

    def test_get_missing_returns_none(self):
        orch = self._make_orchestrator()
        result = orch._get_cached_detail("http://nonexistent.com")
        assert result is None

    def test_cache_ttl_expiry(self):
        orch = self._make_orchestrator()
        orch._detail_cache_ttl_seconds = 0  # Expire immediately
        orch._set_cached_detail("http://test.com/1", {"title": "Old"})
        # Force time to pass (TTL = 0, any time > 0 is expired)
        time.sleep(0.01)
        result = orch._get_cached_detail("http://test.com/1")
        assert result is None  # Should be expired

    def test_cache_max_entries_eviction(self):
        orch = self._make_orchestrator()
        orch._detail_cache_max_entries = 2
        orch._set_cached_detail("http://test.com/1", {"title": "First"})
        orch._set_cached_detail("http://test.com/2", {"title": "Second"})
        orch._set_cached_detail("http://test.com/3", {"title": "Third"})
        # First entry should be evicted (LRU)
        assert orch._get_cached_detail("http://test.com/1") is None
        assert orch._get_cached_detail("http://test.com/2") is not None
        assert orch._get_cached_detail("http://test.com/3") is not None

    def test_get_cached_details_snapshot(self):
        orch = self._make_orchestrator()
        orch._set_cached_detail("http://a.com", {"x": 1})
        orch._set_cached_detail("http://b.com", {"x": 2})
        snapshot = orch.get_cached_details_snapshot()
        assert isinstance(snapshot, dict)
        assert len(snapshot) == 2
        # Snapshot should be a copy
        snapshot["http://c.com"] = {"x": 3}
        assert "http://c.com" not in orch._detail_cache

    def test_clear_during_prefetch_prevents_late_cache_repopulation(self):
        orch = self._make_orchestrator()
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        activity = {
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=9",
            "course_id": 7,
            "type": "assignment",
        }

        def blocking_fetch(_activity):
            started.set()
            assert release.wait(2)
            return {**activity, "details": {"version": "old"}}

        orch._fetch_detail_via_ws = blocking_fetch

        def run_prefetch():
            try:
                with patch(
                    "core.data_orchestrator.settings.MOODLE_WS_TOKEN", "token"
                ):
                    orch.prefetch_all_details([activity], workers=1, force_refresh=True)
            finally:
                finished.set()

        worker = threading.Thread(target=run_prefetch)
        worker.start()
        assert started.wait(1)
        orch.clear_detail_cache()
        release.set()
        assert finished.wait(1)
        worker.join(timeout=1)

        assert orch.get_cached_details_snapshot() == {}

    def test_prefetch_cancellation_returns_without_waiting_for_running_io(self):
        orch = self._make_orchestrator()
        started = threading.Event()
        release = threading.Event()
        cancelled = threading.Event()
        finished = threading.Event()

        def blocking_prefetch(*_args):
            started.set()
            assert release.wait(2)
            return True

        orch.prefetch_one_detail = blocking_prefetch

        def run_prefetch():
            try:
                orch.prefetch_all_details(
                    [{"url": f"https://example.test/{index}"} for index in range(4)],
                    workers=2,
                    cancel_flag=cancelled.is_set,
                    force_refresh=True,
                )
            finally:
                finished.set()

        caller = threading.Thread(target=run_prefetch)
        caller.start()
        assert started.wait(1)
        cancelled.set()
        returned_promptly = finished.wait(0.25)
        release.set()
        caller.join(timeout=2)

        assert returned_promptly


class TestGetUpdatesSince:
    """DataOrchestrator.get_updates_since() — smart polling."""

    def _make_orchestrator(self):
        with patch("core.data_orchestrator.MoodleClient"), \
             patch("core.data_orchestrator.GradeMonitor"):
            from core.data_orchestrator import DataOrchestrator
            orch = DataOrchestrator()
            return orch

    @patch("core.ws_functions.get_course_updates_since")
    @patch("core.ws_functions.get_enrolled_courses")
    @patch("core.ws_functions.get_site_info")
    def test_returns_none_for_zero_timestamp(self, mock_site, mock_courses, mock_updates):
        orch = self._make_orchestrator()
        result = orch.get_updates_since(0)
        assert result is None
        mock_courses.assert_not_called()

    @patch("core.ws_functions.get_course_updates_since")
    @patch("core.ws_functions.get_enrolled_courses")
    @patch("core.ws_functions.get_site_info")
    def test_returns_changed_courses(self, mock_site, mock_courses, mock_updates):
        orch = self._make_orchestrator()
        mock_site.return_value = {"userid": 1}
        mock_courses.return_value = [
            {"id": 101},
            {"id": 102},
        ]
        # Course 101 has updates, 102 doesn't
        mock_updates.side_effect = [
            [{"something": "changed"}],  # 101 changed
            [],  # 102 unchanged
        ]
        result = orch.get_updates_since(1000)
        assert result == [101]

    @patch("core.ws_functions.get_course_updates_since")
    @patch("core.ws_functions.get_enrolled_courses")
    @patch("core.ws_functions.get_site_info")
    def test_returns_empty_list_no_changes(self, mock_site, mock_courses, mock_updates):
        orch = self._make_orchestrator()
        mock_site.return_value = {"userid": 1}
        mock_courses.return_value = [{"id": 101}]
        mock_updates.return_value = []  # No updates
        result = orch.get_updates_since(1000)
        assert result == []

    @patch("core.ws_functions.get_enrolled_courses")
    @patch("core.ws_functions.get_site_info")
    def test_returns_none_on_error(self, mock_site, mock_courses):
        orch = self._make_orchestrator()
        mock_site.return_value = {"userid": 1}
        mock_courses.side_effect = Exception("Network error")
        result = orch.get_updates_since(1000)
        assert result is None

    @patch("core.ws_functions.get_enrolled_courses")
    @patch("core.ws_functions.get_site_info")
    def test_returns_none_when_no_courses(self, mock_site, mock_courses):
        orch = self._make_orchestrator()
        mock_site.return_value = {"userid": 1}
        mock_courses.return_value = None
        result = orch.get_updates_since(1000)
        assert result is None


class TestGetUserId:
    """DataOrchestrator._get_userid() caching."""

    def _make_orchestrator(self):
        with patch("core.data_orchestrator.MoodleClient"), \
             patch("core.data_orchestrator.GradeMonitor"):
            from core.data_orchestrator import DataOrchestrator
            orch = DataOrchestrator()
            return orch

    @patch("core.ws_functions.get_site_info")
    def test_caches_userid(self, mock_site):
        orch = self._make_orchestrator()
        mock_site.return_value = {"userid": 42}
        uid1 = orch._get_userid()
        uid2 = orch._get_userid()
        assert uid1 == 42
        assert uid2 == 42
        # Should only call API once (cached)
        assert mock_site.call_count == 1

    @patch("core.ws_functions.get_site_info")
    def test_returns_none_on_error(self, mock_site):
        orch = self._make_orchestrator()
        mock_site.side_effect = Exception("Error")
        result = orch._get_userid()
        assert result is None


class TestAssignmentMerge:
    def _make_orchestrator(self):
        with patch("core.data_orchestrator.MoodleClient"), \
             patch("core.data_orchestrator.GradeMonitor"):
            from core.data_orchestrator import DataOrchestrator
            return DataOrchestrator()

    def test_matching_assignment_backfills_missing_course_id_only(self):
        orch = self._make_orchestrator()
        calendar_item = {
            "id": "calendar_123",
            "title": "Tiêu đề lịch mới hơn",
            "course": "Tên môn từ lịch mới hơn",
            "deadline": "2030-01-01 10:00:00",
            "urgency": "warning",
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
            "type": "assignment",
            "details": {"fresh": True},
            "course_id": None,
        }
        orch.moodle_service = Mock()
        orch.moodle_service.get_assignments.return_value = [
            {
                "id": 456,
                "fullname": "Tên môn cũ từ assign API",
                "assignments": [
                    {
                        "id": 7,
                        "cmid": 123,
                        "name": "Tiêu đề cũ từ assign API",
                        "duedate": 1,
                        "cutoffdate": 0,
                    }
                ],
            }
        ]

        result = orch._merge_all_assignments([calendar_item], course_ids=[456])

        assert result == [calendar_item]
        assert calendar_item == {
            "id": "calendar_123",
            "title": "Tiêu đề lịch mới hơn",
            "course": "Tên môn từ lịch mới hơn",
            "deadline": "2030-01-01 10:00:00",
            "urgency": "warning",
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
            "type": "assignment",
            "details": {"fresh": True},
            "course_id": 456,
            "cutoff_date": 0,
            "late_status": "closed",
        }

    def test_supplemental_assignment_without_action_event_has_unknown_status(self):
        orch = self._make_orchestrator()
        duedate = int(datetime(datetime.now().year, datetime.now().month, 2).timestamp())
        orch.moodle_service = Mock()
        orch.moodle_service.get_assignments.return_value = [
            {
                "id": 456,
                "fullname": "Môn học Courses",
                "assignments": [
                    {
                        "id": 7,
                        "cmid": 123,
                        "name": "Bài tập không có action event",
                        "duedate": duedate,
                        "cutoffdate": 0,
                    }
                ],
            }
        ]

        result = orch._merge_all_assignments([], course_ids=[456])

        assert len(result) == 1
        assert result[0]["submission_status"] == "unknown"

    def test_same_cmid_from_thnn_does_not_consume_courses_assignment(self):
        orch = self._make_orchestrator()
        duedate = int(datetime(datetime.now().year, datetime.now().month, 2).timestamp())
        thnn_item = {
            "id": "thnn-event-9",
            "title": "Bài tập THNN",
            "course": "THNN",
            "course_id": 777,
            "url": "https://thnn.ut.edu.vn/mod/assign/view.php?id=123",
            "type": "assignment",
            "site_identity": "thnn",
        }
        original_thnn = dict(thnn_item)
        orch.moodle_service = Mock()
        orch.moodle_service.get_assignments.return_value = [
            {
                "id": 456,
                "fullname": "Môn học Courses",
                "assignments": [
                    {
                        "id": 7,
                        "cmid": 123,
                        "name": "Bài tập Courses",
                        "duedate": duedate,
                        "cutoffdate": 0,
                    }
                ],
            }
        ]

        result = orch._merge_all_assignments([thnn_item], course_ids=[456])

        assert len(result) == 2
        assert result[0] == original_thnn
        assert result[1]["title"] == "Bài tập Courses"
        assert result[1]["course_id"] == 456


class _CalendarResponse:
    def __init__(self, body: str, *, url: str, content_type: str, status: int = 200):
        self._body = body.encode("utf-8")
        self._url = url
        self.status = status
        self.headers = MagicMock()
        self.headers.get_content_type.return_value = content_type

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self._url

    def read(self, _size=-1):
        return self._body


class _CalendarOpener:
    def __init__(self, export_responses):
        self.export_responses = list(export_responses)
        self.export_calls = 0

    def open(self, url, data=None, timeout=None):
        del timeout
        if "calendar/export.php" in str(url):
            self.export_calls += 1
            return self.export_responses.pop(0)
        assert data is None
        return _CalendarResponse(
            "<html></html>",
            url=str(url),
            content_type="text/html",
        )


class TestThnnCalendarValidation:
    def _make_orchestrator(self):
        with patch("core.data_orchestrator.MoodleClient"), \
             patch("core.data_orchestrator.GradeMonitor"):
            from core.data_orchestrator import DataOrchestrator
            orch = DataOrchestrator()
            orch.thnn_client.moodle_site_origin = "https://thnn.ut.edu.vn"
            return orch

    @pytest.mark.parametrize(
        ("body", "content_type", "url", "status"),
        [
            (
                "<html><form action='/login/index.php'></form></html>",
                "text/html",
                "https://thnn.ut.edu.vn/login/index.php",
                200,
            ),
            (
                "BEGIN:VCALENDAR\nBEGIN:VEVENT\nEND:VEVENT",
                "text/calendar",
                "https://thnn.ut.edu.vn/calendar/export.php",
                200,
            ),
            (
                "BEGIN:VCALENDAR\nEND:VCALENDAR",
                "text/calendar",
                "https://evil.example/calendar/export.php",
                200,
            ),
            (
                "BEGIN:VCALENDAR\nEND:VCALENDAR",
                "text/calendar",
                "https://thnn.ut.edu.vn/calendar/export.php",
                500,
            ),
            (
                "BEGIN:VCALENDAR\n" + ("X" * (2 * 1024 * 1024)) + "\nEND:VCALENDAR",
                "text/calendar",
                "https://thnn.ut.edu.vn/calendar/export.php",
                200,
            ),
        ],
        ids=["login-html", "missing-end", "cross-origin", "http-500", "oversized"],
    )
    def test_invalid_calendar_preserves_last_known_good_and_freshness(
        self, body, content_type, url, status
    ):
        orch = self._make_orchestrator()
        cached = [{"id": "old", "site_identity": "thnn"}]
        orch._thnn_activities_cache = cached
        orch._last_thnn_fetch_ts = 1234.0
        orch.thnn_client._sesskey = "stale"
        responses = [
            _CalendarResponse(
                body,
                url=url,
                content_type=content_type,
                status=status,
            ),
            _CalendarResponse(
                body,
                url=url,
                content_type=content_type,
                status=status,
            ),
        ]
        opener = _CalendarOpener(responses)
        orch.thnn_client._ensure_html_opener.return_value = opener
        orch.thnn_client.login_session.return_value = True

        result = orch._fetch_thnn_activities(5678)

        assert result == cached
        assert result is not cached
        assert orch._thnn_activities_cache == cached
        assert orch._last_thnn_fetch_ts == 1234.0
        assert opener.export_calls == 2
        orch.thnn_client.login_session.assert_called_once_with(force=True)

    def test_valid_empty_calendar_updates_freshness_and_clears_old_cache(self):
        orch = self._make_orchestrator()
        orch._thnn_activities_cache = [{"id": "old", "site_identity": "thnn"}]
        orch._last_thnn_fetch_ts = 1234.0
        orch.thnn_client._sesskey = "valid"
        opener = _CalendarOpener(
            [
                _CalendarResponse(
                    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n",
                    url="https://thnn.ut.edu.vn/calendar/export.php",
                    content_type="text/calendar",
                )
            ]
        )
        orch.thnn_client._ensure_html_opener.return_value = opener

        result = orch._fetch_thnn_activities(5678)

        assert result == []
        assert orch._thnn_activities_cache == []
        assert orch._last_thnn_fetch_ts == 5678.0
        assert opener.export_calls == 1
        orch.thnn_client.login_session.assert_not_called()


class TestCalendarPagination:
    """Moodle caps action-event pages at 50 records."""

    def _make_orchestrator(self):
        with patch("core.data_orchestrator.MoodleClient"), \
             patch("core.data_orchestrator.GradeMonitor"):
            from core.data_orchestrator import DataOrchestrator
            return DataOrchestrator()

    def test_fetch_uses_moodle_supported_page_size_and_cursor(self):
        orch = self._make_orchestrator()
        orch._get_userid = Mock(return_value=42)
        orch.moodle_service = Mock()
        orch.moodle_service.get_action_events_by_timesort.side_effect = [
            {
                "events": [{"id": value} for value in range(1, 51)],
                "moreevents": True,
                "lastid": 50,
            },
            {"events": [], "moreevents": False},
        ]
        orch.moodle_service.get_user_courses.return_value = []
        orch.moodle_service.ws_events_to_assignments.return_value = []
        orch._merge_all_assignments = Mock(return_value=[])

        assert orch._fetch_via_ws_api() == []

        calls = orch.moodle_service.get_action_events_by_timesort.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["limit"] == 50
        assert calls[0].kwargs["after_event_id"] == 0
        assert calls[1].kwargs["limit"] == 50
        assert calls[1].kwargs["after_event_id"] == 50


class TestCalendarQuizRecovery:
    """Completed quizzes must not disappear with Moodle action events."""

    def _make_orchestrator(self):
        with patch("core.data_orchestrator.MoodleClient"), \
             patch("core.data_orchestrator.GradeMonitor"):
            from core.data_orchestrator import DataOrchestrator
            return DataOrchestrator()

    def test_scopes_quiz_catalog_to_courses_with_calendar_quizzes(self):
        orch = self._make_orchestrator()
        orch._courses_cache = [
            {"id": 11, "fullname": "Môn không có quiz"},
            {"id": 22, "fullname": "Môn có BÀI 3"},
        ]
        orch.moodle_service = Mock()
        orch.moodle_service.get_calendar_events.return_value = [
            {
                "id": 420400,
                "name": "BÀI 3 closes",
                "modulename": "quiz",
                "instance": 87658,
                "courseid": 22,
                "timestart": 1_900_000_000,
            }
        ]
        orch.moodle_service.get_quizzes_by_courses.return_value = [
            {
                "id": 87658,
                "coursemodule": 675077,
                "course": 22,
                "name": "BÀI 3",
                "timeclose": 1_900_000_000,
            }
        ]

        result = orch._fetch_calendar_quizzes(
            [11, 22],
            time_start=1_800_000_000,
            time_end=2_000_000_000,
        )

        orch.moodle_service.get_calendar_events.assert_called_once_with(
            1_800_000_000,
            2_000_000_000,
            [11, 22],
        )
        orch.moodle_service.get_quizzes_by_courses.assert_called_once_with([22])
        assert result == [
            {
                "id": "quiz_87658",
                "title": "BÀI 3",
                "course_name": "Môn có BÀI 3",
                "course": "Môn có BÀI 3",
                "course_id": 22,
                "deadline": datetime.fromtimestamp(1_900_000_000).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "deadline_str": datetime.fromtimestamp(1_900_000_000).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=675077",
                "type": "quiz",
                "urgency": "safe",
                "source": "ws_quiz_calendar_api",
                "submission_status": "unknown",
                "details": {},
                "is_open": False,
                "site_identity": "courses",
            }
        ]

    def test_fetch_merges_quiz_missing_from_action_events_without_duplicates(self):
        orch = self._make_orchestrator()
        orch._get_userid = Mock(return_value=42)
        orch._should_sync_thnn = Mock(return_value=False)
        orch._thnn_activities_cache = []
        orch.moodle_service = Mock()
        orch.moodle_service.get_action_events_by_timesort.return_value = {
            "events": [{"id": 1}],
            "moreevents": False,
        }
        orch.moodle_service.get_user_courses.return_value = [{"id": 22}]
        existing = {
            "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=100",
            "type": "quiz",
        }
        missing = {
            "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=675077",
            "type": "quiz",
        }
        orch.moodle_service.ws_events_to_assignments.return_value = [existing]
        orch._merge_all_assignments = Mock(side_effect=lambda items, **_kwargs: items)
        orch._fetch_calendar_quizzes = Mock(return_value=[existing.copy(), missing])

        result = orch._fetch_via_ws_api()

        assert result == [existing, missing]
