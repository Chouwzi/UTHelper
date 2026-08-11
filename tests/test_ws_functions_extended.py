"""Extended tests for core.ws_functions — coverage gap filling.

Tests cover missing functions:
- get_calendar_events (legacy)
- get_quizzes_by_courses
- get_assign_details_via_ws (assign + quiz + other paths)
- upload_file_to_draft
- save_assignment_submission
- submit_for_grading
- get_course_updates_since
- get_course_contents
- ws_events_to_assignments (comprehensive event conversion)
"""
import os
import sys
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core import ws_functions


class TestGetCalendarEvents:
    """get_calendar_events() — legacy calendar endpoint."""

    def test_success(self):
        mock_api = Mock(return_value={"events": [{"id": 10}]})
        result = ws_functions.get_calendar_events(mock_api, time_start=1000, time_end=2000)
        assert len(result) == 1

    def test_no_time_params(self):
        mock_api = Mock(return_value={"events": []})
        ws_functions.get_calendar_events(mock_api)
        mock_api.assert_called_once()

    def test_error_returns_none(self):
        mock_api = Mock(side_effect=Exception("Error"))
        assert ws_functions.get_calendar_events(mock_api) is None

    def test_no_events_key(self):
        mock_api = Mock(return_value={"other": 123})
        assert ws_functions.get_calendar_events(mock_api) is None


class TestGetQuizzesByCourses:
    """get_quizzes_by_courses() tests."""

    def test_success(self):
        mock_api = Mock(return_value={"quizzes": [{"id": 1, "name": "Quiz 1"}]})
        result = ws_functions.get_quizzes_by_courses(mock_api, [101, 102])
        assert len(result) == 1
        call_kwargs = mock_api.call_args[1]
        assert call_kwargs.get('courseids[0]') == 101

    def test_error_returns_none(self):
        mock_api = Mock(side_effect=Exception("Error"))
        assert ws_functions.get_quizzes_by_courses(mock_api, [1]) is None

    def test_no_quizzes_key(self):
        mock_api = Mock(return_value={})
        assert ws_functions.get_quizzes_by_courses(mock_api, [1]) is None


class TestQuizAttemptStatus:
    @staticmethod
    def _details_for_attempts(attempts):
        def mock_api(function, **_params):
            if function == "mod_quiz_get_quizzes_by_courses":
                return {"quizzes": [{"id": 7, "coursemodule": 77}]}
            if function == "mod_quiz_get_user_attempts":
                return {"attempts": attempts}
            if function == "core_enrol_get_users_courses":
                return []
            return None

        ws_functions.clear_all_caches()
        return ws_functions.get_assign_details_via_ws(mock_api, 77, 12, "quiz")

    def test_empty_authoritative_attempt_list_means_quiz_not_started(self):
        details = self._details_for_attempts([])

        assert details["quiz_attempt_status"] == "not_submitted"
        assert details["quiz_info"] == []

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ("finished", "submitted"),
            ("inprogress", "in_progress"),
            ("overdue", "overdue"),
            ("abandoned", "abandoned"),
            ("unexpected", "attempted"),
        ],
    )
    def test_last_attempt_state_is_normalized(self, state, expected):
        details = self._details_for_attempts([{"attempt": 1, "state": state}])

        assert details["quiz_attempt_status"] == expected

    def test_attempt_api_failure_does_not_claim_quiz_is_not_started(self):
        def mock_api(function, **_params):
            if function == "mod_quiz_get_quizzes_by_courses":
                return {"quizzes": [{"id": 7, "coursemodule": 77}]}
            if function == "mod_quiz_get_user_attempts":
                raise RuntimeError("temporary failure")
            if function == "core_enrol_get_users_courses":
                return []
            return None

        ws_functions.clear_all_caches()
        details = ws_functions.get_assign_details_via_ws(mock_api, 77, 12, "quiz")

        assert details["quiz_attempt_status"] is None


class TestGetCourseUpdatesSince:
    """get_course_updates_since() tests."""

    def test_success(self):
        mock_api = Mock(return_value={"instances": [{"id": 1}]})
        result = ws_functions.get_course_updates_since(mock_api, courseid=1, since=1000)
        assert len(result) == 1

    def test_no_instances(self):
        mock_api = Mock(return_value={"instances": []})
        result = ws_functions.get_course_updates_since(mock_api, courseid=1, since=1000)
        assert result == []

    def test_error_returns_none(self):
        mock_api = Mock(side_effect=Exception("Error"))
        assert ws_functions.get_course_updates_since(mock_api, courseid=1, since=1000) is None


class TestGetCourseContents:
    """get_course_contents() tests."""

    def test_success(self):
        mock_api = Mock(return_value=[{"name": "Section 1", "modules": []}])
        result = ws_functions.get_course_contents(mock_api, courseid=1)
        assert len(result) == 1

    def test_non_list_returns_none(self):
        mock_api = Mock(return_value={"error": "access denied"})
        assert ws_functions.get_course_contents(mock_api, courseid=1) is None

    def test_error_returns_none(self):
        mock_api = Mock(side_effect=Exception("Error"))
        assert ws_functions.get_course_contents(mock_api, courseid=1) is None


class TestUploadFileToDraft:
    """upload_file_to_draft() tests."""

    def test_success(self):
        mock_api = Mock(return_value={"itemid": 999})
        result = ws_functions.upload_file_to_draft(
            mock_api, filename="test.pdf", file_content_b64="dGVzdA==", user_id=1
        )
        assert result == 999

    def test_exception_in_result(self):
        mock_api = Mock(return_value={"exception": "error", "message": "No permission"})
        result = ws_functions.upload_file_to_draft(
            mock_api, filename="test.pdf", file_content_b64="dGVzdA==", user_id=1
        )
        assert result is None

    def test_api_error(self):
        mock_api = Mock(side_effect=Exception("Network error"))
        result = ws_functions.upload_file_to_draft(
            mock_api, filename="test.pdf", file_content_b64="dGVzdA==", user_id=1
        )
        assert result is None

    def test_none_result(self):
        mock_api = Mock(return_value=None)
        result = ws_functions.upload_file_to_draft(
            mock_api, filename="test.pdf", file_content_b64="dGVzdA==", user_id=1
        )
        assert result is None


class TestSaveAssignmentSubmission:
    """save_assignment_submission() tests."""

    def test_success_empty_list(self):
        """The legacy wrapper declines a save without a text snapshot."""
        mock_api = Mock(return_value=[])
        result = ws_functions.save_assignment_submission(mock_api, assign_id=1, draft_itemid=999)
        assert result is False
        mock_api.assert_not_called()

    def test_success_dict_no_warnings(self):
        mock_api = Mock(return_value={})
        result = ws_functions.save_assignment_submission(mock_api, assign_id=1, draft_itemid=999)
        assert result is False

    def test_failure_with_warnings(self):
        mock_api = Mock(return_value={"warnings": [{"message": "Too late"}]})
        result = ws_functions.save_assignment_submission(mock_api, assign_id=1, draft_itemid=999)
        assert result is False

    def test_none_result(self):
        mock_api = Mock(return_value=None)
        result = ws_functions.save_assignment_submission(mock_api, assign_id=1, draft_itemid=999)
        assert result is False

    def test_api_error(self):
        mock_api = Mock(side_effect=Exception("Error"))
        result = ws_functions.save_assignment_submission(mock_api, assign_id=1, draft_itemid=999)
        assert result is False

    def test_could_not_save_warning_is_failure(self):
        mock_api = Mock(return_value=[{"warningcode": "couldnotsavesubmission", "message": "closed"}])

        result = ws_functions.save_assignment_submission_result(mock_api, 77, 900, "", 1, 0)

        assert result.ok is False
        assert result.warnings[0].code == "couldnotsavesubmission"

    def test_result_uses_snapshot_online_text_without_status_lookup(self):
        mock_api = Mock(return_value=[])

        result = ws_functions.save_assignment_submission_result(
            mock_api, 77, 900, "<p>Keep this</p>", 1, 901
        )

        assert result.ok is True
        assert mock_api.call_args.args == ("mod_assign_save_submission",)
        assert mock_api.call_args.kwargs == {
            "assignmentid": 77,
            "plugindata[files_filemanager]": 900,
            "plugindata[onlinetext_editor][text]": "<p>Keep this</p>",
            "plugindata[onlinetext_editor][format]": 1,
            "plugindata[onlinetext_editor][itemid]": 901,
        }

    def test_legacy_wrapper_preserves_explicit_existing_online_text(self):
        mock_api = Mock(return_value=[])

        assert ws_functions.save_assignment_submission(
            mock_api, 77, 900, "<p>Existing text</p>", 1, 901
        ) is True
        assert mock_api.call_args.kwargs["plugindata[onlinetext_editor][text]"] == "<p>Existing text</p>"


class TestSubmitForGrading:
    """submit_for_grading() tests."""

    def test_explicit_acceptance_is_required_for_legacy_finalize_wrapper(self):
        mock_api = Mock(return_value=[])
        assert ws_functions.submit_for_grading(mock_api, assign_id=1) is False
        mock_api.assert_not_called()
        assert ws_functions.submit_for_grading(mock_api, assign_id=1, accept_submission_statement=True) is True
        assert mock_api.call_args.kwargs["acceptsubmissionstatement"] == 1

    def test_exception_in_result(self):
        mock_api = Mock(return_value={"exception": "error", "message": "Not allowed"})
        assert ws_functions.submit_for_grading(mock_api, assign_id=1) is False

    def test_none_result(self):
        mock_api = Mock(return_value=None)
        assert ws_functions.submit_for_grading(mock_api, assign_id=1) is False

    def test_api_error(self):
        mock_api = Mock(side_effect=Exception("Error"))
        assert ws_functions.submit_for_grading(mock_api, assign_id=1) is False

    def test_passes_explicit_statement_choice(self):
        mock_api = Mock(return_value=[])

        result = ws_functions.submit_for_grading_result(mock_api, 77, False)

        assert result.ok is True
        assert mock_api.call_args.kwargs["acceptsubmissionstatement"] == 0


class TestWSEventsToAssignments:
    """ws_events_to_assignments() — comprehensive event conversion."""

    def test_empty_events(self):
        assert ws_functions.ws_events_to_assignments([]) == []

    def test_none_events(self):
        assert ws_functions.ws_events_to_assignments(None) == []

    def test_assign_event_conversion(self):
        events = [{
            "id": 1,
            "name": "Bài tập 1",
            "modulename": "assign",
            "timesort": int(datetime(2026, 12, 31).timestamp()),
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=100",
            "course": {"id": 101, "fullname": "Lập trình Web"},
        }]
        result = ws_functions.ws_events_to_assignments(events)
        assert len(result) == 1
        assert result[0]["type"] == "assignment"
        assert result[0]["title"] == "Bài tập 1"
        assert result[0]["source"] == "ws_api"
        assert result[0]["course_name"] == "Lập trình Web"

    def test_quiz_event_conversion(self):
        events = [{
            "id": 2,
            "name": "Quiz 1",
            "modulename": "quiz",
            "timesort": int(datetime(2026, 12, 31).timestamp()),
            "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=200",
            "course": {"id": 101, "fullname": "Math"},
        }]
        result = ws_functions.ws_events_to_assignments(events)
        assert result[0]["type"] == "quiz"

    def test_attendance_event_conversion(self):
        events = [{
            "id": 3,
            "name": "Attendance",
            "modulename": "attendance",
            "timesort": int(datetime(2026, 12, 31).timestamp()),
            "course": {"id": 101, "fullname": "Physics"},
        }]
        result = ws_functions.ws_events_to_assignments(events)
        assert result[0]["type"] == "attendance"

    def test_unknown_module(self):
        events = [{
            "id": 4,
            "name": "Custom Event",
            "modulename": "groupselect",
            "timesort": int(datetime(2026, 12, 31).timestamp()),
            "course": {"id": 101, "fullname": "Art"},
        }]
        result = ws_functions.ws_events_to_assignments(events)
        assert result[0]["type"] == "other"

    def test_html_entities_decoded(self):
        events = [{
            "id": 5,
            "name": "Lab &amp; Practice",
            "modulename": "assign",
            "timesort": int(datetime(2026, 12, 31).timestamp()),
            "course": {"id": 1, "fullname": "DB &amp; Systems"},
        }]
        result = ws_functions.ws_events_to_assignments(events)
        assert result[0]["title"] == "Lab & Practice"
        assert result[0]["course_name"] == "DB & Systems"

    def test_missing_name_defaults(self):
        events = [{
            "id": 6,
            "modulename": "assign",
            "timesort": int(datetime(2026, 12, 31).timestamp()),
        }]
        result = ws_functions.ws_events_to_assignments(events)
        assert result[0]["title"] == "Không tên"

    def test_non_dict_event_skipped(self):
        events = ["not a dict", 42, None]
        result = ws_functions.ws_events_to_assignments(events)
        assert result == []

    def test_url_construction_from_parts(self):
        """When url is missing, construct from modulename + instance."""
        events = [{
            "id": 7,
            "name": "Test",
            "modulename": "assign",
            "instance": 555,
            "timesort": int(datetime(2026, 12, 31).timestamp()),
            "course": {"id": 1, "fullname": "Course"},
        }]
        result = ws_functions.ws_events_to_assignments(events)
        assert "assign" in result[0]["url"]
        assert "555" in result[0]["url"]

    def test_string_timesort_handled(self):
        """timesort as string should be converted to int."""
        events = [{
            "id": 8,
            "name": "Event",
            "modulename": "assign",
            "timesort": str(int(datetime(2026, 12, 31).timestamp())),
            "course": {"id": 1, "fullname": "Course"},
        }]
        result = ws_functions.ws_events_to_assignments(events)
        assert result[0]["deadline"] != ""

    def test_no_course_data(self):
        events = [{
            "id": 9,
            "name": "Orphan Event",
            "modulename": "assign",
            "timesort": int(datetime(2026, 12, 31).timestamp()),
        }]
        result = ws_functions.ws_events_to_assignments(events)
        assert result[0]["course_name"] == ""
