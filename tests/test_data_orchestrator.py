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

    def test_cache_hit_preserves_the_clicked_card_identity_for_a_shared_url(self):
        orch = self._make_orchestrator()
        url = "https://courses.test/mod/quiz/view.php?id=42"
        orch._set_cached_detail(
            url,
            {
                "id": "quiz-open-event",
                "url": url,
                "title": "Quiz opens",
                "deadline": "2026-08-20 08:00:00",
                "is_open": True,
                "submission_status": "submitted",
                "details": {"description_html": "<p>Shared detail</p>"},
            },
        )
        clicked = {
            "id": "quiz-close-event",
            "url": url,
            "title": "Quiz closes",
            "deadline": "2026-08-21 20:00:00",
            "is_open": False,
            "submission_status": "unknown",
            "details": {},
        }

        result = orch.fetch_full_details(clicked)

        assert result["id"] == "quiz-close-event"
        assert result["title"] == "Quiz closes"
        assert result["deadline"] == "2026-08-21 20:00:00"
        assert result["is_open"] is False
        assert result["submission_status"] == "submitted"
        assert result["details"]["description_html"] == "<p>Shared detail</p>"

    def test_get_missing_returns_none(self):
        orch = self._make_orchestrator()
        result = orch._get_cached_detail("http://nonexistent.com")
        assert result is None

    def test_cache_ttl_expiry(self):
        orch = self._make_orchestrator()
        orch._detail_cache_ttl_seconds = 0  # Expire immediately
        with patch(
            "core.data_orchestrator.time.monotonic",
            side_effect=[100.0, 100.01],
        ):
            orch._set_cached_detail("http://test.com/1", {"title": "Old"})
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

    def test_concurrent_cold_detail_requests_for_same_quiz_share_one_fetch(self):
        """Foreground navigation must join an in-flight background warmup."""
        orch = self._make_orchestrator()
        started = threading.Event()
        release = threading.Event()
        fetch_count = 0
        fetch_count_lock = threading.Lock()
        activity = {
            "url": "https://courses.test/mod/quiz/view.php?id=42",
            "course_id": 7,
            "type": "quiz",
        }

        def blocking_fetch(data):
            nonlocal fetch_count
            with fetch_count_lock:
                fetch_count += 1
            started.set()
            assert release.wait(2)
            return {**data, "details": {"quiz_info": {"id": 42}}}

        orch._fetch_detail_via_ws = blocking_fetch
        results = []

        with patch("core.data_orchestrator.settings.MOODLE_WS_TOKEN", "token"):
            first = threading.Thread(
                target=lambda: results.append(orch.fetch_full_details(activity))
            )
            second = threading.Thread(
                target=lambda: results.append(orch.fetch_full_details(activity))
            )
            first.start()
            assert started.wait(1)
            second.start()
            time.sleep(0.05)
            release.set()
            first.join(timeout=2)
            second.join(timeout=2)

        assert fetch_count == 1
        assert len(results) == 2
        assert all(result["details"]["quiz_info"]["id"] == 42 for result in results)

    def test_quiz_status_hydration_refreshes_an_existing_full_detail_cache(self):
        orch = self._make_orchestrator()
        url = "https://courses.test/mod/quiz/view.php?id=42"
        cached = {
            "url": url,
            "type": "quiz",
            "submission_status": "not_submitted",
            "details": {
                "quiz_attempt_status": "not_submitted",
                "status_data": {"Trạng thái nộp bài": "not_submitted"},
            },
        }
        orch._set_cached_detail(url, cached)
        orch.moodle_service.get_quiz_attempts = lambda _quiz_id: [
            {"state": "finished"}
        ]

        result = orch._fetch_one_activity_status(
            {"url": url, "type": "quiz", "quiz_id": 42}
        )
        refreshed = orch._get_cached_detail(url)

        assert result["submission_status"] == "submitted"
        assert refreshed["submission_status"] == "submitted"
        assert refreshed["details"]["quiz_attempt_status"] == "submitted"
        assert (
            refreshed["details"]["status_data"]["Trạng thái nộp bài"]
            == "submitted"
        )

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
            "assignment_id": 7,
            "cutoff_date": 0,
            "late_status": "late_allowed",
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
        assert result[0]["assignment_id"] == 7

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
                "quiz_id": 87658,
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
        recovered_existing = {**existing, "quiz_id": 9001}
        orch._fetch_calendar_quizzes = Mock(return_value=[recovered_existing, missing])

        result = orch._fetch_via_ws_api()

        assert result == [existing, missing]
        assert existing["quiz_id"] == 9001

    def test_catalog_backfills_action_quiz_id_outside_recovery_window(self):
        orch = self._make_orchestrator()
        orch._courses_cache = [
            {"id": 22, "fullname": "Calendar course"},
            {"id": 33, "fullname": "Action course"},
        ]
        orch.moodle_service = Mock()
        orch.moodle_service.get_calendar_events.return_value = [
            {"modulename": "quiz", "courseid": 22}
        ]
        orch.moodle_service.get_quizzes_by_courses.return_value = [
            {
                "id": 991,
                "coursemodule": 771,
                "course": 33,
                "name": "Quiz outside recovery window",
                "timeclose": 2_100_000_000,
            }
        ]
        action_url = "https://courses.ut.edu.vn/mod/quiz/view.php?id=771"

        result = orch._fetch_calendar_quizzes(
            [22, 33],
            time_start=1_800_000_000,
            time_end=2_000_000_000,
            action_quiz_course_ids={33},
            action_quiz_urls={action_url},
        )

        orch.moodle_service.get_quizzes_by_courses.assert_called_once_with([22, 33])
        assert result == [
            {"url": action_url, "quiz_id": 991, "catalog_only": True}
        ]


class TestStatusPrefetch:
    def _make_orchestrator(self):
        with patch("core.data_orchestrator.MoodleClient"), \
             patch("core.data_orchestrator.GradeMonitor"):
            from core.data_orchestrator import DataOrchestrator
            return DataOrchestrator()

    def test_direct_ids_avoid_reloading_assignment_and_quiz_catalogs(self):
        orch = self._make_orchestrator()
        orch.moodle_service = Mock()
        orch.moodle_service.get_submission_status.return_value = {
            "lastattempt": {"submission": {"status": "submitted"}}
        }
        orch.moodle_service.get_quiz_attempts.return_value = [
            {"state": "finished", "timefinish": 1_700_000_000}
        ]
        activities = [
            {
                "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=101",
                "type": "assignment",
                "assignment_id": 7001,
                "submission_status": "unknown",
            },
            {
                "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=202",
                "type": "quiz",
                "quiz_id": 8002,
                "submission_status": "not_submitted",
            },
        ]

        result = orch.prefetch_activity_statuses(activities, workers=2)

        assert result[activities[0]["url"]]["submission_status"] == "submitted"
        assert result[activities[1]["url"]]["submission_status"] == "submitted"
        orch.moodle_service.get_submission_status.assert_called_once_with(7001)
        orch.moodle_service.get_quiz_attempts.assert_called_once_with(8002)
        orch.moodle_service.get_assignments.assert_not_called()
        orch.moodle_service.get_quizzes_by_courses.assert_not_called()

    def test_failed_status_lookup_preserves_unknown_instead_of_claiming_not_submitted(self):
        orch = self._make_orchestrator()
        orch.moodle_service = Mock()
        orch.moodle_service.get_quiz_attempts.return_value = None
        activity = {
            "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=202",
            "type": "quiz",
            "quiz_id": 8002,
            "submission_status": "unknown",
        }

        result = orch.prefetch_activity_statuses([activity], workers=1)

        assert result == {}


def test_thnn_cache_isolated_by_account_and_cleared_on_auth_failure_or_clear_caches(monkeypatch):
    from config import settings
    from core.data_orchestrator import DataOrchestrator

    with patch("core.data_orchestrator.MoodleClient"), patch("core.data_orchestrator.GradeMonitor"):
        orch = DataOrchestrator()
        orch.thnn_client.moodle_site_origin = "https://thnn.ut.edu.vn"

    monkeypatch.setattr(settings, "UTH_USERNAME", "student-a")
    orch._thnn_activities_cache = [{"id": "thnn-a", "site_identity": "thnn"}]
    orch._last_thnn_fetch_ts = 5000.0

    # Switching active account must NOT expose student-a's THNN cache
    monkeypatch.setattr(settings, "UTH_USERNAME", "student-b")
    assert orch._thnn_activities_cache == []
    assert orch._last_thnn_fetch_ts == 0.0
    assert orch._should_sync_thnn(5001.0) is True

    # Switching back sees student-a's bucket until auth failure or clear_caches()
    monkeypatch.setattr(settings, "UTH_USERNAME", "student-a")
    assert len(orch._thnn_activities_cache) == 1

    # THNN login failure (non-quiz-lease) clears active account's THNN cache
    orch.thnn_client._sesskey = ""
    orch.thnn_client._last_login_error = "invalid_credentials"
    orch.thnn_client.last_login_error = "invalid_credentials"
    orch.thnn_client.login_session.return_value = False
    assert orch._fetch_thnn_activities(6000) == []
    assert orch._thnn_activities_cache == []
    assert orch._last_thnn_fetch_ts == 0.0

    # clear_caches() wipes all account buckets, detail caches, and userid/login flags
    orch._thnn_activities_cache = [{"id": "thnn-a2"}]
    orch._set_cached_detail("https://courses.ut.edu.vn/mod/assign/view.php?id=1", {"title": "T"})
    orch._userid_cache = 77
    orch.is_logged_in = True
    orch.clear_caches()
    assert orch._thnn_activities_cache == []
    assert orch.get_cached_details_snapshot() == {}
    assert orch._userid_cache is None
    assert orch.is_logged_in is False


def test_fetch_via_ws_api_timeout_does_not_wait_for_hung_worker():
    import time as _time
    from core.data_orchestrator import DataOrchestrator

    with patch("core.data_orchestrator.MoodleClient"), patch("core.data_orchestrator.GradeMonitor"):
        orch = DataOrchestrator()

    orch._ws_parallel_timeout = 0.05
    orch._get_userid = Mock(return_value=42)
    orch.moodle_service = Mock()

    def hung_calendar(*_args, **_kwargs):
        _time.sleep(1.0)
        return {"events": [], "moreevents": False}

    orch.moodle_service.get_action_events_by_timesort.side_effect = hung_calendar
    orch.moodle_service.get_user_courses.return_value = []

    t0 = _time.perf_counter()
    result = orch._fetch_via_ws_api()
    elapsed = _time.perf_counter() - t0

    assert result is None
    assert elapsed < 0.4


def test_merge_all_assignments_cutoff_zero_is_late_allowed_and_uses_time_policy(monkeypatch):
    from datetime import timedelta
    from config import settings
    from core.data_orchestrator import DataOrchestrator

    with patch("core.data_orchestrator.MoodleClient"), patch("core.data_orchestrator.GradeMonitor"):
        orch = DataOrchestrator()

    monkeypatch.setattr(settings, "URGENCY_CRITICAL_HOURS", 48)
    monkeypatch.setattr(settings, "URGENCY_WARNING_HOURS", 120)
    monkeypatch.setattr(settings, "FETCH_MONTHS", 3)

    now = datetime.now()
    due_in_36h = int((now + timedelta(hours=36)).timestamp())
    past_due_this_month = int(datetime(now.year, now.month, 1, 0, 1).timestamp())
    if past_due_this_month >= int(now.timestamp()):
        past_due_this_month = int((now - timedelta(seconds=60)).timestamp())

    orch.moodle_service = Mock()
    orch.moodle_service.get_assignments.return_value = [
        {
            "id": 101,
            "fullname": "CS",
            "assignments": [
                {
                    "id": 1,
                    "cmid": 11,
                    "name": "Custom Critical Threshold 36h",
                    "duedate": due_in_36h,
                    "cutoffdate": 0,
                },
                {
                    "id": 2,
                    "cmid": 12,
                    "name": "Past Due Open Cutoff",
                    "duedate": past_due_this_month,
                    "cutoffdate": 0,
                },
                {
                    "id": 3,
                    "cmid": 13,
                    "name": "Past Due Prevent Late",
                    "duedate": past_due_this_month,
                    "cutoffdate": 0,
                    "configs": [{"name": "preventlatesubmissions", "value": "1"}],
                },
            ],
        }
    ]

    merged = orch._merge_all_assignments([], course_ids=[101])
    by_id = {item["assignment_id"]: item for item in merged}

    assert by_id[1]["urgency"] == "critical"
    assert by_id[1]["late_status"] == "open"
    if past_due_this_month >= int(datetime(now.year, now.month, 1).timestamp()):
        assert by_id[2]["late_status"] == "late_allowed"
        assert by_id[2]["urgency"] == "critical"
        assert by_id[3]["late_status"] == "closed"
        assert by_id[3]["urgency"] == "overdue"


def test_fetch_thnn_activities_discards_result_when_account_switches_mid_flight(monkeypatch):
    from config import settings
    from core.data_orchestrator import DataOrchestrator

    with patch("core.data_orchestrator.MoodleClient"), patch("core.data_orchestrator.GradeMonitor"):
        orch = DataOrchestrator()
        orch.thnn_client.moodle_site_origin = "https://thnn.ut.edu.vn"
        orch.thnn_client._sesskey = "alice-sesskey"

    started = threading.Event()
    release = threading.Event()
    ics_payload = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\n"
        "UID:101@thnn.ut.edu.vn\n"
        "SUMMARY:Alice Secret THNN\n"
        "DTEND:20261201T120000Z\n"
        "CATEGORIES:THNN Course\n"
        "END:VEVENT\n"
        "END:VCALENDAR\n"
    )
    month_html = (
        b'<a data-action="view-event" data-event-id="101" '
        b'href="https://thnn.ut.edu.vn/mod/assign/view.php?id=555">Open</a>'
    )

    class _FakeResp:
        def read(self):
            return month_html

    class _FakeOpener:
        def open(self, _url, timeout=10):
            return _FakeResp()

    orch.thnn_client._ensure_html_opener = Mock(return_value=_FakeOpener())

    def slow_download(_opener, _data):
        started.set()
        assert release.wait(2.0)
        return ics_payload

    orch._download_thnn_calendar = slow_download

    monkeypatch.setattr(settings, "UTH_USERNAME", "alice")
    out: list = []
    worker = threading.Thread(target=lambda: out.append(orch._fetch_thnn_activities(1_700_000_000)))
    worker.start()
    assert started.wait(1.0)

    # Account teardown & switch to Bob while Alice's THNN fetch is in flight
    monkeypatch.setattr(settings, "UTH_USERNAME", "bob")
    orch.clear_caches()
    release.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    assert out == [[]]
    assert orch._thnn_activities_cache == []
    assert orch._last_thnn_fetch_ts == 0.0
    assert "bob" not in orch._thnn_cache_by_account


def test_fetch_thnn_auth_failure_after_account_switch_does_not_wipe_new_account_cache(monkeypatch):
    from config import settings
    from core.data_orchestrator import DataOrchestrator

    with patch("core.data_orchestrator.MoodleClient"), patch("core.data_orchestrator.GradeMonitor"):
        orch = DataOrchestrator()
        orch.thnn_client.moodle_site_origin = "https://thnn.ut.edu.vn"
        orch.thnn_client._sesskey = ""
        orch.thnn_client._last_login_error = "invalid_credentials"
        orch.thnn_client.last_login_error = "invalid_credentials"

    started = threading.Event()
    release = threading.Event()

    def slow_failed_login(*_args, **_kwargs):
        started.set()
        assert release.wait(2.0)
        return False

    orch.thnn_client.login_session = slow_failed_login

    monkeypatch.setattr(settings, "UTH_USERNAME", "alice")
    out: list = []
    worker = threading.Thread(target=lambda: out.append(orch._fetch_thnn_activities(1_700_000_000)))
    worker.start()
    assert started.wait(1.0)

    monkeypatch.setattr(settings, "UTH_USERNAME", "bob")
    orch.clear_caches()
    orch._thnn_activities_cache = [{"id": "bob-thnn"}]
    orch._last_thnn_fetch_ts = 1_700_000_123.0
    release.set()
    worker.join(timeout=2.0)

    assert out == [[]]
    assert orch._thnn_activities_cache == [{"id": "bob-thnn"}]
    assert orch._last_thnn_fetch_ts == 1_700_000_123.0


def test_clear_caches_does_not_block_on_refresh_course_grades_network_io():
    from core.data_orchestrator import DataOrchestrator

    with patch("core.data_orchestrator.MoodleClient"), patch("core.data_orchestrator.GradeMonitor"):
        orch = DataOrchestrator()

    orch.course_grade_monitor = Mock()
    orch.course_grade_monitor.update.return_value = [{"changed": True}]

    started = threading.Event()
    release = threading.Event()

    def slow_fetch(_orch):
        started.set()
        assert release.wait(2.0)
        return [{"course_id": 10, "name": "Alice Course"}]

    result_box: list = []

    def run_refresh():
        with patch(
            "core.course_grades_service.fetch_current_courses_with_grades",
            side_effect=slow_fetch,
        ):
            result_box.append(orch.refresh_course_grades())

    worker = threading.Thread(target=run_refresh)
    worker.start()
    assert started.wait(1.0)

    t0 = time.perf_counter()
    orch.clear_caches()
    clear_elapsed = time.perf_counter() - t0

    release.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    assert clear_elapsed < 0.15
    assert result_box == [([], [])]
    assert orch.latest_course_grades == []
    orch.course_grade_monitor.update.assert_not_called()
