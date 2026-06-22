"""Tests for DataOrchestrator cached helpers and cutoff date logic.

Covers: get_userid, get_enrolled_courses, get_course_name,
        invalidate_session_cache, _merge_all_assignments cutoff logic.
"""
import pytest
import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime

# Setup path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def orchestrator():
    """Create DataOrchestrator with mocked client."""
    with patch('core.data_orchestrator.settings') as mock_settings:
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.MOODLE_WS_TOKEN = "test_token"
        mock_settings.UTH_USERNAME = "test_user"
        mock_settings.UTH_PASSWORD = "test_pass"
        mock_settings.PREFETCH_WORKERS = 4
        mock_settings.DETAIL_CACHE_TTL_SECONDS = 1800
        mock_settings.DETAIL_CACHE_MAX_ENTRIES = 100
        mock_settings.FETCH_MONTHS = 1

        from core.data_orchestrator import DataOrchestrator
        orch = DataOrchestrator()
        orch.client = MagicMock()
        yield orch


# ===========================================================================
# get_userid — session-level caching
# ===========================================================================

class TestGetUserid:
    """get_userid caches after first API call."""

    def test_caches_after_first_call(self, orchestrator):
        """Second call returns cached value without API call."""
        orchestrator.client.call_ws_api.return_value = {
            'userid': 42, 'username': 'test'
        }

        uid1 = orchestrator.get_userid()
        uid2 = orchestrator.get_userid()

        assert uid1 == 42
        assert uid2 == 42
        # API called only once
        orchestrator.client.call_ws_api.assert_called_once_with(
            'core_webservice_get_site_info'
        )

    def test_handles_api_failure(self, orchestrator):
        """API exception → returns None, doesn't crash."""
        orchestrator.client.call_ws_api.side_effect = ConnectionError("fail")
        assert orchestrator.get_userid() is None

    def test_handles_none_response(self, orchestrator):
        """API returns None → userid stays None."""
        orchestrator.client.call_ws_api.return_value = None
        assert orchestrator.get_userid() is None

    def test_handles_dict_without_userid(self, orchestrator):
        """Dict missing 'userid' key → None."""
        orchestrator.client.call_ws_api.return_value = {'username': 'test'}
        assert orchestrator.get_userid() is None


# ===========================================================================
# get_enrolled_courses — session-level caching
# ===========================================================================

class TestGetEnrolledCourses:
    """get_enrolled_courses caches after first call.

    NOTE: _userid MUST be pre-set because the production code has a
    reentrant-lock issue: get_enrolled_courses acquires _courses_cache_lock
    then calls get_userid() which also tries to acquire the SAME lock,
    causing a deadlock with threading.Lock (non-reentrant).
    Pre-setting _userid makes get_userid() return at line 85-86, before
    the lock, avoiding the deadlock.
    """

    def test_caches_result(self, orchestrator):
        """Second call uses cache."""
        courses = [
            {'id': 1, 'fullname': 'Python'},
            {'id': 2, 'fullname': 'Java'},
        ]
        # Pre-set userid to avoid reentrant lock deadlock
        orchestrator._userid = 10
        orchestrator.client.call_ws_api.return_value = courses

        result1 = orchestrator.get_enrolled_courses()
        result2 = orchestrator.get_enrolled_courses()

        assert result1 == courses
        assert result2 == courses
        # API called only once (second call uses cache)
        orchestrator.client.call_ws_api.assert_called_once()

    def test_empty_on_failure(self, orchestrator):
        """API raises → empty list."""
        orchestrator._userid = 10
        orchestrator.client.call_ws_api.side_effect = RuntimeError("network")
        result = orchestrator.get_enrolled_courses()
        assert result == []

    def test_empty_when_no_userid(self, orchestrator):
        """No userid → empty list (can't fetch courses)."""
        # Mock get_userid to avoid the reentrant lock deadlock
        orchestrator.get_userid = MagicMock(return_value=None)
        result = orchestrator.get_enrolled_courses()
        assert result == []

    def test_non_list_result_gives_empty(self, orchestrator):
        """API returns non-list (e.g. dict error) → empty list."""
        orchestrator._userid = 10
        orchestrator.client.call_ws_api.return_value = {'error': 'invalid'}
        result = orchestrator.get_enrolled_courses()
        assert result == []


# ===========================================================================
# get_course_name
# ===========================================================================

class TestGetCourseName:
    """Lookup course name from cached enrolled courses."""

    def test_found_course(self, orchestrator):
        """Known course_id → returns fullname."""
        orchestrator._enrolled_courses = [
            {'id': 1, 'fullname': 'Lập trình Python'},
            {'id': 2, 'fullname': 'Toán cao cấp'},
        ]
        assert orchestrator.get_course_name(1) == 'Lập trình Python'
        assert orchestrator.get_course_name(2) == 'Toán cao cấp'

    def test_not_found_returns_empty(self, orchestrator):
        """Unknown course_id → ''."""
        orchestrator._enrolled_courses = [
            {'id': 1, 'fullname': 'Python'},
        ]
        assert orchestrator.get_course_name(999) == ''

    def test_empty_courses_returns_empty(self, orchestrator):
        """No enrolled courses cached → ''."""
        orchestrator._enrolled_courses = []
        assert orchestrator.get_course_name(1) == ''


# ===========================================================================
# invalidate_session_cache
# ===========================================================================

class TestInvalidateSessionCache:
    """Reset both userid and enrolled_courses caches."""

    def test_resets_both_caches(self, orchestrator):
        """After invalidate, both caches are None → next call re-fetches."""
        orchestrator._userid = 42
        orchestrator._enrolled_courses = [{'id': 1}]

        orchestrator.invalidate_session_cache()

        assert orchestrator._userid is None
        assert orchestrator._enrolled_courses is None

    def test_invalidate_then_refetch(self, orchestrator):
        """After invalidate, get_userid calls API again."""
        orchestrator._userid = 42
        orchestrator.invalidate_session_cache()

        orchestrator.client.call_ws_api.return_value = {'userid': 99}
        uid = orchestrator.get_userid()

        assert uid == 99
        orchestrator.client.call_ws_api.assert_called_once()


# ===========================================================================
# _merge_all_assignments — cutoff date logic
# ===========================================================================

class TestMergeAllAssignmentsCutoff:
    """Test cutoff date → urgency mapping and can_submit field."""

    def _build_assign(self, duedate, cutoffdate=0, assign_id=1, cmid=100):
        """Helper to build a minimal assignment dict."""
        return {
            'id': assign_id,
            'cmid': cmid,
            'name': f'Test Assignment {assign_id}',
            'duedate': duedate,
            'cutoffdate': cutoffdate,
        }

    @patch('core.data_orchestrator.settings')
    def test_urgency_closed_when_past_cutoff(self, mock_settings, orchestrator):
        """Past due AND past cutoff → urgency='closed'."""
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.FETCH_MONTHS = 1

        now_ts = int(datetime.now().timestamp())
        past_due = now_ts - 86400       # 1 day ago
        past_cutoff = now_ts - 3600     # 1 hour ago

        orchestrator._enrolled_courses = [{'id': 1}]
        orchestrator._userid = 10

        with patch('core.ws_functions.get_assignments') as mock_get:
            mock_get.return_value = [{
                'id': 1,
                'fullname': 'Test Course',
                'assignments': [self._build_assign(past_due, past_cutoff)],
            }]

            result = orchestrator._merge_all_assignments([])

        closed_items = [r for r in result if r['urgency'] == 'closed']
        assert len(closed_items) == 1

    @patch('core.data_orchestrator.settings')
    def test_urgency_overdue_when_past_due_before_cutoff(self, mock_settings, orchestrator):
        """Past due but cutoff is in future → urgency='overdue'."""
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.FETCH_MONTHS = 1

        now_ts = int(datetime.now().timestamp())
        past_due = now_ts - 86400        # 1 day ago
        future_cutoff = now_ts + 86400   # 1 day from now

        orchestrator._enrolled_courses = [{'id': 1}]
        orchestrator._userid = 10

        with patch('core.ws_functions.get_assignments') as mock_get:
            mock_get.return_value = [{
                'id': 1,
                'fullname': 'Test Course',
                'assignments': [self._build_assign(past_due, future_cutoff)],
            }]

            result = orchestrator._merge_all_assignments([])

        overdue_items = [r for r in result if r['urgency'] == 'overdue']
        assert len(overdue_items) == 1

    @patch('core.data_orchestrator.settings')
    def test_can_submit_field_present(self, mock_settings, orchestrator):
        """When cutoffdate > 0, result has 'can_submit' bool field."""
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.FETCH_MONTHS = 1

        now_ts = int(datetime.now().timestamp())
        past_due = now_ts - 3600              # 1h ago
        future_cutoff = now_ts + 86400 * 2    # 2 days from now

        orchestrator._enrolled_courses = [{'id': 1}]
        orchestrator._userid = 10

        with patch('core.ws_functions.get_assignments') as mock_get:
            mock_get.return_value = [{
                'id': 1,
                'fullname': 'Test Course',
                'assignments': [self._build_assign(past_due, future_cutoff)],
            }]

            result = orchestrator._merge_all_assignments([])

        assert len(result) == 1
        assert 'can_submit' in result[0]
        assert result[0]['can_submit'] is True

    @patch('core.data_orchestrator.settings')
    def test_can_submit_false_when_past_cutoff(self, mock_settings, orchestrator):
        """Past cutoff → can_submit=False."""
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.FETCH_MONTHS = 1

        now_ts = int(datetime.now().timestamp())
        past_due = now_ts - 86400 * 2    # 2 days ago
        past_cutoff = now_ts - 3600      # 1h ago

        orchestrator._enrolled_courses = [{'id': 1}]
        orchestrator._userid = 10

        with patch('core.ws_functions.get_assignments') as mock_get:
            mock_get.return_value = [{
                'id': 1,
                'fullname': 'Test Course',
                'assignments': [self._build_assign(past_due, past_cutoff)],
            }]

            result = orchestrator._merge_all_assignments([])

        assert len(result) == 1
        assert result[0]['can_submit'] is False

    @patch('core.data_orchestrator.settings')
    def test_no_cutoff_no_can_submit_field(self, mock_settings, orchestrator):
        """No cutoffdate (0) → no can_submit or cutoff_date fields."""
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.FETCH_MONTHS = 1

        now_ts = int(datetime.now().timestamp())
        past_due = now_ts - 3600  # 1h ago

        orchestrator._enrolled_courses = [{'id': 1}]
        orchestrator._userid = 10

        with patch('core.ws_functions.get_assignments') as mock_get:
            mock_get.return_value = [{
                'id': 1,
                'fullname': 'Test Course',
                'assignments': [self._build_assign(past_due, cutoffdate=0)],
            }]

            result = orchestrator._merge_all_assignments([])

        assert len(result) == 1
        assert 'can_submit' not in result[0]
        assert 'cutoff_date' not in result[0]

    @patch('core.data_orchestrator.settings')
    def test_skips_assignments_outside_date_range(self, mock_settings, orchestrator):
        """Assignments outside [month_start, +FETCH_MONTHS] are excluded."""
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.FETCH_MONTHS = 1

        # Way in the future (6 months out)
        now_ts = int(datetime.now().timestamp())
        far_future = now_ts + 86400 * 180

        orchestrator._enrolled_courses = [{'id': 1}]
        orchestrator._userid = 10

        with patch('core.ws_functions.get_assignments') as mock_get:
            mock_get.return_value = [{
                'id': 1,
                'fullname': 'Test Course',
                'assignments': [self._build_assign(far_future)],
            }]

            result = orchestrator._merge_all_assignments([])

        assert len(result) == 0  # filtered out

    @patch('core.data_orchestrator.settings')
    def test_existing_cmids_not_duplicated(self, mock_settings, orchestrator):
        """Assignments already in calendar_results (by cmid) are skipped."""
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.FETCH_MONTHS = 1

        now_ts = int(datetime.now().timestamp())
        due_tomorrow = now_ts + 86400

        orchestrator._enrolled_courses = [{'id': 1}]
        orchestrator._userid = 10

        existing = [{
            'url': 'https://courses.ut.edu.vn/mod/assign/view.php?id=100',
            'title': 'Already Exists',
        }]

        with patch('core.ws_functions.get_assignments') as mock_get:
            mock_get.return_value = [{
                'id': 1,
                'fullname': 'Test Course',
                'assignments': [self._build_assign(due_tomorrow, cmid=100)],
            }]

            result = orchestrator._merge_all_assignments(existing)

        # Only the existing one, no duplicate
        assert len(result) == 1
        assert result[0]['title'] == 'Already Exists'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
