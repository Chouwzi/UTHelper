"""Tests for core.ws_functions — WS API wrapper functions.

Tests verify:
- get_site_info: success, error handling
- get_calendar_action_events: success, default params, error
- get_enrolled_courses: success, error, extraction
- get_assignments: success with course_ids
- get_submission_status: success, exception in result, error
- get_submitted_files: file extraction from nested structure
- get_quiz_attempts: success, error
- resolve_cmid_to_assign_id: found, not found
- ws_events_to_assignments: event conversion
- get_course_grades, get_grade_items: success
- get_unread_notification_count: success
"""
import os
import sys
import pytest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import ws_functions


class TestGetSiteInfo:
    """get_site_info() tests."""

    def test_success(self):
        mock_api = Mock(return_value={"userid": 42, "sitename": "UTH Moodle"})
        result = ws_functions.get_site_info(mock_api)
        assert result["userid"] == 42
        mock_api.assert_called_once_with('core_webservice_get_site_info')

    def test_api_error_returns_none(self):
        mock_api = Mock(side_effect=Exception("Network"))
        result = ws_functions.get_site_info(mock_api)
        assert result is None


class TestGetCalendarActionEvents:
    """get_calendar_action_events() tests."""

    def test_success_with_events(self):
        mock_api = Mock(return_value={"events": [{"id": 1}, {"id": 2}]})
        result = ws_functions.get_calendar_action_events(mock_api, timesort_from=1000, timesort_to=2000)
        assert len(result) == 2
        assert result[0]["id"] == 1

    def test_default_params(self):
        mock_api = Mock(return_value={"events": []})
        ws_functions.get_calendar_action_events(mock_api)
        call_kwargs = mock_api.call_args[1]
        assert 'timesortfrom' in call_kwargs
        assert 'timesortto' in call_kwargs

    def test_api_error_returns_none(self):
        mock_api = Mock(side_effect=Exception("Timeout"))
        result = ws_functions.get_calendar_action_events(mock_api)
        assert result is None

    def test_no_events_key_returns_none(self):
        mock_api = Mock(return_value={"something_else": "data"})
        result = ws_functions.get_calendar_action_events(mock_api)
        assert result is None


class TestGetEnrolledCourses:
    """get_enrolled_courses() tests."""

    def test_success(self):
        mock_api = Mock(return_value={"courses": [{"id": 101, "fullname": "Web"}]})
        result = ws_functions.get_enrolled_courses(mock_api)
        assert len(result) == 1
        assert result[0]["id"] == 101

    def test_error_returns_none(self):
        mock_api = Mock(side_effect=Exception("Error"))
        result = ws_functions.get_enrolled_courses(mock_api)
        assert result is None

    def test_no_courses_key_returns_none(self):
        mock_api = Mock(return_value={})
        result = ws_functions.get_enrolled_courses(mock_api)
        assert result is None


class TestGetAssignments:
    """get_assignments() tests."""

    def test_success(self):
        mock_api = Mock(return_value={"courses": [{"id": 1, "assignments": []}]})
        result = ws_functions.get_assignments(mock_api, course_ids=[1, 2])
        assert result is not None
        # Check course_ids are passed correctly
        call_kwargs = mock_api.call_args[1]
        assert call_kwargs.get('courseids[0]') == 1
        assert call_kwargs.get('courseids[1]') == 2

    def test_no_course_ids(self):
        mock_api = Mock(return_value={"courses": []})
        result = ws_functions.get_assignments(mock_api)
        assert result is not None

    def test_error_returns_none(self):
        mock_api = Mock(side_effect=Exception("Error"))
        result = ws_functions.get_assignments(mock_api, course_ids=[1])
        assert result is None


class TestGetSubmissionStatus:
    """get_submission_status() tests."""

    def test_success(self):
        mock_api = Mock(return_value={"lastattempt": {"submission": {}}})
        result = ws_functions.get_submission_status(mock_api, assign_id=42)
        assert result is not None
        mock_api.assert_called_once_with('mod_assign_get_submission_status', assignid=42)

    def test_exception_in_result_returns_none(self):
        mock_api = Mock(return_value={"exception": "access_denied", "message": "No access"})
        result = ws_functions.get_submission_status(mock_api, assign_id=42)
        assert result is None

    def test_api_error_returns_none(self):
        mock_api = Mock(side_effect=Exception("Error"))
        result = ws_functions.get_submission_status(mock_api, assign_id=42)
        assert result is None


class TestGetSubmittedFiles:
    """get_submitted_files() tests."""

    def test_extracts_files(self):
        mock_api = Mock(return_value={
            "lastattempt": {
                "submission": {
                    "timecreated": 1700000000,
                    "plugins": [
                        {
                            "type": "file",
                            "fileareas": [
                                {
                                    "files": [
                                        {
                                            "filename": "report.pdf",
                                            "filesize": 12345,
                                            "fileurl": "https://example.com/report.pdf",
                                            "timemodified": 1700001000,
                                            "mimetype": "application/pdf",
                                            "filepath": "/",
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            }
        })
        files = ws_functions.get_submitted_files(mock_api, assign_id=1)
        assert len(files) == 1
        assert files[0]["name"] == "report.pdf"
        assert files[0]["size"] == 12345

    def test_no_file_plugin_returns_empty(self):
        mock_api = Mock(return_value={
            "lastattempt": {
                "submission": {
                    "plugins": [{"type": "onlinetext"}]
                }
            }
        })
        files = ws_functions.get_submitted_files(mock_api, assign_id=1)
        assert files == []

    def test_api_error_returns_empty(self):
        mock_api = Mock(side_effect=Exception("Error"))
        files = ws_functions.get_submitted_files(mock_api, assign_id=1)
        assert files == []


class TestGetQuizAttempts:
    """get_quiz_attempts() tests."""

    def test_success(self):
        mock_api = Mock(return_value={"attempts": [{"id": 1, "state": "finished"}]})
        result = ws_functions.get_quiz_attempts(mock_api, quiz_id=10)
        assert len(result) == 1

    def test_error_returns_none(self):
        mock_api = Mock(side_effect=Exception("Error"))
        result = ws_functions.get_quiz_attempts(mock_api, quiz_id=10)
        assert result is None


class TestResolveCmidToAssignId:
    """resolve_cmid_to_assign_id() tests."""

    def test_found(self):
        mock_api = Mock(return_value={
            "courses": [
                {
                    "assignments": [
                        {"cmid": 100, "id": 42},
                        {"cmid": 200, "id": 43},
                    ]
                }
            ]
        })
        result = ws_functions.resolve_cmid_to_assign_id(mock_api, cmid=100, course_id=1)
        assert result == 42

    def test_not_found(self):
        mock_api = Mock(return_value={
            "courses": [
                {"assignments": [{"cmid": 999, "id": 1}]}
            ]
        })
        result = ws_functions.resolve_cmid_to_assign_id(mock_api, cmid=100, course_id=1)
        assert result is None

    def test_api_returns_none(self):
        mock_api = Mock(return_value=None)
        result = ws_functions.resolve_cmid_to_assign_id(mock_api, cmid=100, course_id=1)
        assert result is None


class TestGetCourseGrades:
    """get_course_grades() tests."""

    def test_success(self):
        mock_api = Mock(return_value={
            "grades": [{"courseid": 1, "grade": "8.0"}]
        })
        result = ws_functions.get_course_grades(mock_api, userid=42)
        assert len(result) == 1

    def test_error_returns_none(self):
        mock_api = Mock(side_effect=Exception("Error"))
        result = ws_functions.get_course_grades(mock_api, userid=42)
        assert result is None


class TestGetGradeItems:
    """get_grade_items() tests."""

    def test_success(self):
        mock_api = Mock(return_value={
            "usergrades": [
                {"gradeitems": [{"itemname": "CK", "gradeformatted": "8.5"}]}
            ]
        })
        result = ws_functions.get_grade_items(mock_api, courseid=1, userid=42)
        assert len(result) == 1
        assert result[0]["itemname"] == "CK"

    def test_error_returns_none(self):
        mock_api = Mock(side_effect=Exception("Error"))
        result = ws_functions.get_grade_items(mock_api, courseid=1, userid=42)
        assert result is None


class TestGetUnreadNotificationCount:
    """get_unread_notification_count() tests."""

    def test_success_returns_int_directly(self):
        # get_unread_notification_count checks isinstance(result, int)
        mock_api = Mock(return_value=5)
        result = ws_functions.get_unread_notification_count(mock_api, userid=42)
        assert result == 5

    def test_error_returns_zero(self):
        mock_api = Mock(side_effect=Exception("Error"))
        result = ws_functions.get_unread_notification_count(mock_api, userid=42)
        assert result == 0
