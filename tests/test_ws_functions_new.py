"""Tests for NEW ws_functions: submission flow, grades, completion, notifications.

Covers: submit_for_grading, save_and_submit, check_needs_submit,
        get_grade_items, get_course_grades, get_completion_status,
        get_unread_notification_count.
"""
import pytest
import sys
import os
from unittest.mock import MagicMock, patch, call

# Setup path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ws_functions import (
    submit_for_grading,
    save_and_submit,
    check_needs_submit,
    get_grade_items,
    get_course_grades,
    get_completion_status,
    get_unread_notification_count,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_api():
    """A fresh MagicMock callable for call_api."""
    return MagicMock()


# ===========================================================================
# submit_for_grading
# ===========================================================================

class TestSubmitForGrading:
    """mod_assign_submit_for_grading wrapper."""

    def test_success_empty_list(self, mock_api):
        """Moodle returns [] on success → True."""
        mock_api.return_value = []
        assert submit_for_grading(mock_api, assign_id=42) is True
        mock_api.assert_called_once_with(
            'mod_assign_submit_for_grading',
            assignmentid=42,
            acceptsubmissionstatement=1,
        )

    def test_success_empty_dict(self, mock_api):
        """Dict without warnings → True."""
        mock_api.return_value = {}
        assert submit_for_grading(mock_api, assign_id=7) is True

    def test_warnings_in_response(self, mock_api):
        """Warnings present → False."""
        mock_api.return_value = {
            'warnings': [{'message': 'Submission not open'}]
        }
        assert submit_for_grading(mock_api, assign_id=99) is False

    def test_exception_handling(self, mock_api):
        """API raises → False, no propagation."""
        mock_api.side_effect = ConnectionError("timeout")
        assert submit_for_grading(mock_api, assign_id=1) is False

    def test_unexpected_non_empty_list(self, mock_api):
        """Non-empty list (undocumented) → still True (fallback)."""
        mock_api.return_value = [1, 2, 3]
        assert submit_for_grading(mock_api, assign_id=5) is True


# ===========================================================================
# save_and_submit
# ===========================================================================

class TestSaveAndSubmit:
    """Combined save + optional submit_for_grading."""

    @patch('core.ws_functions.submit_for_grading', return_value=True)
    @patch('core.ws_functions.save_assignment_submission', return_value=False)
    def test_save_fails_returns_false(self, mock_save, mock_submit, mock_api):
        """Save fails → immediately False, submit never called."""
        result = save_and_submit(mock_api, assign_id=10, draft_itemid=999)
        assert result is False
        mock_save.assert_called_once_with(mock_api, 10, 999)
        mock_submit.assert_not_called()

    @patch('core.ws_functions.submit_for_grading', return_value=True)
    @patch('core.ws_functions.save_assignment_submission', return_value=True)
    def test_save_ok_submit_ok(self, mock_save, mock_submit, mock_api):
        """Both save and submit succeed → True."""
        result = save_and_submit(mock_api, assign_id=10, draft_itemid=888)
        assert result is True
        mock_save.assert_called_once_with(mock_api, 10, 888)
        mock_submit.assert_called_once_with(mock_api, 10)

    @patch('core.ws_functions.submit_for_grading', return_value=True)
    @patch('core.ws_functions.save_assignment_submission', return_value=True)
    def test_needs_submit_false_skips_submit(self, mock_save, mock_submit, mock_api):
        """needs_submit=False → save only, submit skipped."""
        result = save_and_submit(mock_api, assign_id=10, draft_itemid=777, needs_submit=False)
        assert result is True
        mock_save.assert_called_once()
        mock_submit.assert_not_called()

    @patch('core.ws_functions.submit_for_grading', return_value=False)
    @patch('core.ws_functions.save_assignment_submission', return_value=True)
    def test_save_ok_submit_fails(self, mock_save, mock_submit, mock_api):
        """Save OK but submit fails → False."""
        result = save_and_submit(mock_api, assign_id=10, draft_itemid=666)
        assert result is False


# ===========================================================================
# check_needs_submit
# ===========================================================================

class TestCheckNeedsSubmit:
    """Determine whether assignment requires explicit submit_for_grading."""

    @patch('core.ws_functions.get_assignments')
    def test_submissiondrafts_1_returns_true(self, mock_get_assigns, mock_api):
        """submissiondrafts=1 → True (needs manual submit)."""
        mock_get_assigns.return_value = [
            {'assignments': [{'id': 42, 'submissiondrafts': 1}]}
        ]
        assert check_needs_submit(mock_api, assign_id=42, course_id=1) is True

    @patch('core.ws_functions.get_assignments')
    def test_submissiondrafts_0_returns_false(self, mock_get_assigns, mock_api):
        """submissiondrafts=0 → False (auto-submit on save)."""
        mock_get_assigns.return_value = [
            {'assignments': [{'id': 42, 'submissiondrafts': 0}]}
        ]
        assert check_needs_submit(mock_api, assign_id=42, course_id=1) is False

    @patch('core.ws_functions.get_assignments')
    def test_assignment_not_found_returns_true(self, mock_get_assigns, mock_api):
        """Assignment not in response → True (safe default)."""
        mock_get_assigns.return_value = [
            {'assignments': [{'id': 99, 'submissiondrafts': 0}]}
        ]
        assert check_needs_submit(mock_api, assign_id=42, course_id=1) is True

    @patch('core.ws_functions.get_assignments')
    def test_api_error_returns_true(self, mock_get_assigns, mock_api):
        """get_assignments returns None → True (safe default)."""
        mock_get_assigns.return_value = None
        assert check_needs_submit(mock_api, assign_id=42, course_id=1) is True

    @patch('core.ws_functions.get_assignments')
    def test_missing_submissiondrafts_key_defaults_true(self, mock_get_assigns, mock_api):
        """Assignment found but no submissiondrafts key → defaults to 1 → True."""
        mock_get_assigns.return_value = [
            {'assignments': [{'id': 42}]}  # no submissiondrafts key
        ]
        assert check_needs_submit(mock_api, assign_id=42, course_id=1) is True


# ===========================================================================
# get_grade_items
# ===========================================================================

class TestGetGradeItems:
    """gradereport_user_get_grade_items wrapper."""

    def test_valid_response_parsing(self, mock_api):
        """Parses usergrades[0].gradeitems correctly."""
        grade_items = [
            {'itemname': 'Quiz 1', 'graderaw': 8.5, 'grademax': 10.0},
            {'itemname': 'Assignment 2', 'graderaw': 9.0, 'grademax': 10.0},
        ]
        mock_api.return_value = {
            'usergrades': [{'gradeitems': grade_items}]
        }
        result = get_grade_items(mock_api, course_id=5)
        assert result == grade_items
        assert len(result) == 2
        assert result[0]['itemname'] == 'Quiz 1'

    def test_empty_usergrades(self, mock_api):
        """usergrades=[] → None."""
        mock_api.return_value = {'usergrades': []}
        assert get_grade_items(mock_api, course_id=5) is None

    def test_api_error(self, mock_api):
        """Exception → None."""
        mock_api.side_effect = RuntimeError("network")
        assert get_grade_items(mock_api, course_id=5) is None

    def test_with_user_id(self, mock_api):
        """userid param is forwarded correctly."""
        mock_api.return_value = {
            'usergrades': [{'gradeitems': []}]
        }
        get_grade_items(mock_api, course_id=5, user_id=123)
        mock_api.assert_called_once_with(
            'gradereport_user_get_grade_items',
            courseid=5, userid=123,
        )

    def test_none_result(self, mock_api):
        """API returns None → None."""
        mock_api.return_value = None
        assert get_grade_items(mock_api, course_id=5) is None

    def test_missing_usergrades_key(self, mock_api):
        """Dict without 'usergrades' → None."""
        mock_api.return_value = {'other': 'data'}
        assert get_grade_items(mock_api, course_id=5) is None


# ===========================================================================
# get_course_grades
# ===========================================================================

class TestGetCourseGrades:
    """gradereport_overview_get_course_grades wrapper."""

    def test_valid_response(self, mock_api):
        """Returns grades list."""
        grades = [
            {'courseid': 1, 'grade': '8.5', 'rawgrade': 8.5},
            {'courseid': 2, 'grade': '7.0', 'rawgrade': 7.0},
        ]
        mock_api.return_value = {'grades': grades}
        result = get_course_grades(mock_api)
        assert result == grades

    def test_api_error(self, mock_api):
        """Exception → None."""
        mock_api.side_effect = TimeoutError("slow")
        assert get_course_grades(mock_api) is None

    def test_with_user_id(self, mock_api):
        """userid forwarded."""
        mock_api.return_value = {'grades': []}
        get_course_grades(mock_api, user_id=456)
        mock_api.assert_called_once_with(
            'gradereport_overview_get_course_grades', userid=456,
        )

    def test_no_grades_key(self, mock_api):
        """Dict missing 'grades' → None."""
        mock_api.return_value = {'something': 'else'}
        assert get_course_grades(mock_api) is None


# ===========================================================================
# get_completion_status
# ===========================================================================

class TestGetCompletionStatus:
    """core_completion_get_activities_completion_status wrapper."""

    def test_valid_statuses(self, mock_api):
        """Returns statuses list."""
        statuses = [
            {'cmid': 100, 'modname': 'assign', 'state': 1},
            {'cmid': 101, 'modname': 'quiz', 'state': 0},
        ]
        mock_api.return_value = {'statuses': statuses}
        result = get_completion_status(mock_api, course_id=5, user_id=10)
        assert result == statuses
        mock_api.assert_called_once_with(
            'core_completion_get_activities_completion_status',
            courseid=5, userid=10,
        )

    def test_api_error(self, mock_api):
        """Exception → None."""
        mock_api.side_effect = Exception("oops")
        assert get_completion_status(mock_api, course_id=5, user_id=10) is None

    def test_no_statuses_key(self, mock_api):
        """Missing 'statuses' → None."""
        mock_api.return_value = {'other': []}
        assert get_completion_status(mock_api, course_id=5, user_id=10) is None


# ===========================================================================
# get_unread_notification_count
# ===========================================================================

class TestGetUnreadNotificationCount:
    """message_popup_get_unread_popup_notification_count wrapper."""

    def test_int_response(self, mock_api):
        """API returns bare int → returned as-is."""
        mock_api.return_value = 5
        assert get_unread_notification_count(mock_api, user_id=10) == 5

    def test_dict_response(self, mock_api):
        """API returns {'count': N} → extract N."""
        mock_api.return_value = {'count': 12}
        assert get_unread_notification_count(mock_api, user_id=10) == 12

    def test_api_error_returns_0(self, mock_api):
        """Exception → 0 (graceful degradation)."""
        mock_api.side_effect = ConnectionError("offline")
        assert get_unread_notification_count(mock_api, user_id=10) == 0

    @pytest.mark.parametrize("response,expected", [
        (None, 0),
        ("unexpected", 0),
        ({'count': 0}, 0),
        ({}, 0),
        (0, 0),
    ])
    def test_edge_case_responses(self, mock_api, response, expected):
        """Various unusual API responses → safe int result."""
        mock_api.return_value = response
        assert get_unread_notification_count(mock_api, user_id=10) == expected

    def test_dict_missing_count_key(self, mock_api):
        """Dict without 'count' → 0."""
        mock_api.return_value = {'other': 99}
        assert get_unread_notification_count(mock_api, user_id=10) == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
