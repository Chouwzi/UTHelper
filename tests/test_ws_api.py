"""Tests cho Moodle Web Services API layer.

Test WS token acquisition, API calls, và event conversion.
"""
import pytest
import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime

# Setup path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestWSToken:
    """Test _get_ws_token trong MoodleClient."""
    
    @patch('core.client.settings')
    def test_cached_token_returned(self, mock_settings):
        """Nếu đã có token trong settings, không gọi API."""
        mock_settings.MOODLE_WS_TOKEN = "cached_token_abc"
        mock_settings.MOODLE_WS_TOKEN_ORIGIN = "https://courses.ut.edu.vn"
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.UTH_USERNAME = "test"
        mock_settings.UTH_PASSWORD = "pass"
        mock_settings.PREFETCH_WORKERS = 4
        
        from core.client import MoodleClient
        client = MoodleClient()
        
        token = client._get_ws_token()
        assert token == "cached_token_abc"

    @patch('core.client.settings')
    def test_token_from_another_moodle_origin_is_not_reused(self, mock_settings):
        mock_settings.MOODLE_WS_TOKEN = "courses-token"
        mock_settings.MOODLE_WS_TOKEN_ORIGIN = "https://courses.ut.edu.vn"
        mock_settings.MOODLE_BASE_URL = "https://thnn.ut.edu.vn"
        mock_settings.UTH_USERNAME = ""
        mock_settings.UTH_PASSWORD = ""

        from core.client import MoodleClient

        client = MoodleClient()

        assert client.moodle_site_origin == "https://thnn.ut.edu.vn"
        assert client.has_site_credentials is False
        assert client._get_ws_token() == ""

    @patch('core.client.settings')
    def test_legacy_unstamped_credentials_are_not_sent_to_thnn(self, mock_settings):
        mock_settings.MOODLE_WS_TOKEN = ""
        mock_settings.MOODLE_WS_TOKEN_ORIGIN = ""
        mock_settings.MOODLE_BASE_URL = "https://thnn.ut.edu.vn"
        mock_settings.UTH_USERNAME = "legacy-user"
        mock_settings.UTH_PASSWORD = "legacy-pass"
        mock_settings.UTH_CREDENTIALS_ORIGIN = ""

        from core.client import MoodleClient

        client = MoodleClient()
        client._post = MagicMock()

        assert client.has_site_credentials is False
        assert client._get_ws_token() == ""
        client._post.assert_not_called()

    @patch('core.client.settings')
    def test_matching_stamped_credentials_authenticate_only_their_site(
        self, mock_settings
    ):
        mock_settings.MOODLE_WS_TOKEN = ""
        mock_settings.MOODLE_WS_TOKEN_ORIGIN = ""
        mock_settings.MOODLE_BASE_URL = "https://thnn.ut.edu.vn"
        mock_settings.UTH_USERNAME = "thnn-user"
        mock_settings.UTH_PASSWORD = "thnn-pass"
        mock_settings.UTH_CREDENTIALS_ORIGIN = "https://thnn.ut.edu.vn"

        from core.client import MoodleClient

        client = MoodleClient()
        client._post = MagicMock(return_value=(200, {"token": "thnn-token"}))
        with patch("config.save_settings"):
            assert client._get_ws_token() == "thnn-token"

        assert client.has_site_credentials is True
        assert client._post.call_args.args[0] == (
            "https://thnn.ut.edu.vn/login/token.php"
        )
        assert mock_settings.UTH_CREDENTIALS_ORIGIN == "https://thnn.ut.edu.vn"

    @patch('core.client.settings')
    def test_mismatched_stamped_credentials_make_zero_auth_requests(
        self, mock_settings
    ):
        mock_settings.MOODLE_WS_TOKEN = ""
        mock_settings.MOODLE_WS_TOKEN_ORIGIN = ""
        mock_settings.MOODLE_BASE_URL = "https://thnn.ut.edu.vn"
        mock_settings.UTH_USERNAME = "courses-user"
        mock_settings.UTH_PASSWORD = "courses-pass"
        mock_settings.UTH_CREDENTIALS_ORIGIN = "https://courses.ut.edu.vn"

        from core.client import MoodleClient

        client = MoodleClient()
        client._post = MagicMock()

        assert client.has_site_credentials is False
        assert client._get_ws_token() == ""
        client._post.assert_not_called()

    @patch('core.client.settings')
    def test_explicit_successful_login_stamps_stored_credential_origin(
        self, mock_settings
    ):
        mock_settings.MOODLE_WS_TOKEN = ""
        mock_settings.MOODLE_WS_TOKEN_ORIGIN = ""
        mock_settings.MOODLE_BASE_URL = "https://thnn.ut.edu.vn"
        mock_settings.UTH_USERNAME = ""
        mock_settings.UTH_PASSWORD = ""
        mock_settings.UTH_CREDENTIALS_ORIGIN = ""

        from core.client import MoodleClient

        client = MoodleClient()
        client._post = MagicMock(return_value=(200, {"token": "thnn-token"}))
        with patch("config.save_settings"):
            assert client.login("thnn-user", "thnn-pass", force=True) is True

        assert mock_settings.UTH_USERNAME == "thnn-user"
        assert mock_settings.UTH_PASSWORD == "thnn-pass"
        assert mock_settings.UTH_CREDENTIALS_ORIGIN == "https://thnn.ut.edu.vn"
    





class TestWSEventsConversion:
    """Test ws_events_to_assignments conversion."""
    
    def test_assign_event(self):
        """Assignment event → type='assignment'."""
        from core.ws_functions import ws_events_to_assignments
        
        now_ts = int(datetime.now().timestamp()) + 86400  # tomorrow
        events = [{
            'name': 'Bài tập 1',
            'modulename': 'assign',
            'timesort': now_ts,
            'course': {'id': 1, 'fullname': 'Lập trình Python'},
            'url': 'https://courses.ut.edu.vn/mod/assign/view.php?id=123',
        }]
        
        result = ws_events_to_assignments(events)
        assert len(result) == 1
        assert result[0]['type'] == 'assignment'
        assert result[0]['title'] == 'Bài tập 1'
        assert result[0]['source'] == 'ws_api'
    
    def test_quiz_event(self):
        """Quiz event → type='quiz'."""
        from core.ws_functions import ws_events_to_assignments
        
        now_ts = int(datetime.now().timestamp()) + 3600
        events = [{
            'name': 'Kiểm tra giữa kỳ',
            'modulename': 'quiz',
            'timesort': now_ts,
            'course': {'id': 2, 'fullname': 'Toán cao cấp'},
        }]
        
        result = ws_events_to_assignments(events)
        assert len(result) == 1
        assert result[0]['type'] == 'quiz'
    
    def test_empty_events(self):
        """Empty events list → empty result."""
        from core.ws_functions import ws_events_to_assignments
        
        result = ws_events_to_assignments([])
        assert result == []
    
    def test_missing_fields_handled(self):
        """Events thiếu field không gây crash."""
        from core.ws_functions import ws_events_to_assignments
        
        events = [{'name': 'Minimal event'}]  # Missing most fields
        result = ws_events_to_assignments(events)
        assert len(result) == 1
        assert result[0]['title'] == 'Minimal event'


class TestOrchestratorWSFallback:
    """Test orchestrator WS API → scraping fallback."""
    
    @patch('core.data_orchestrator.settings')
    def test_ws_api_success_skips_scraping(self, mock_settings):
        """WS API thành công → không gọi scraping."""
        mock_settings.USE_WS_API = True
        mock_settings.PREFETCH_WORKERS = 4
        mock_settings.DETAIL_CACHE_TTL_SECONDS = 1800
        mock_settings.DETAIL_CACHE_MAX_ENTRIES = 100
        
        from core.data_orchestrator import DataOrchestrator
        orch = DataOrchestrator()
        orch.client = MagicMock()
        orch.moodle_service = MagicMock()
        orch.moodle_service.get_site_info.return_value = {"userid": 42}
        orch.moodle_service.get_action_events_by_timesort.return_value = {
            "events": [{"name": "Test", "modulename": "assign", "timesort": 1234567890}]
        }
        orch.moodle_service.get_user_courses.return_value = []
        orch.moodle_service.ws_events_to_assignments.return_value = [
            {"title": "Test", "type": "assignment", "source": "ws_api"}
        ]
        
        result = orch.get_latest_activities()
        assert len(result) == 1
        assert result[0]['source'] == 'ws_api'
        # login() should NOT have been called
        orch.client.login.assert_not_called()
    




if __name__ == '__main__':
    pytest.main([__file__, '-v'])
