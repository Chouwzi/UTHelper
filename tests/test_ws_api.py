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
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.UTH_USERNAME = "test"
        mock_settings.UTH_PASSWORD = "pass"
        mock_settings.PREFETCH_WORKERS = 4
        
        from core.client import MoodleClient
        with patch.object(MoodleClient, '_load_cookies'):
            client = MoodleClient()
        
        token = client._get_ws_token()
        assert token == "cached_token_abc"
    
    @patch('core.client.settings')
    def test_force_refresh_ignores_cache(self, mock_settings):
        """force=True bỏ qua cached token."""
        mock_settings.MOODLE_WS_TOKEN = "old_token"
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.UTH_USERNAME = "test"
        mock_settings.UTH_PASSWORD = "pass"
        mock_settings.PREFETCH_WORKERS = 4
        
        from core.client import MoodleClient
        with patch.object(MoodleClient, '_load_cookies'):
            client = MoodleClient()
        
        # Mock the POST request
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"token": "new_token_xyz"}
        client.session.post = MagicMock(return_value=mock_resp)
        
        with patch('config.save_settings'):
            token = client._get_ws_token(force=True)
        
        assert token == "new_token_xyz"
        assert mock_settings.MOODLE_WS_TOKEN == "new_token_xyz"
    
    @patch('core.client.settings')
    def test_no_credentials_returns_empty(self, mock_settings):
        """Không có username/password → trả về empty string."""
        mock_settings.MOODLE_WS_TOKEN = ""
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.UTH_USERNAME = ""
        mock_settings.UTH_PASSWORD = ""
        mock_settings.PREFETCH_WORKERS = 4
        
        from core.client import MoodleClient
        with patch.object(MoodleClient, '_load_cookies'):
            client = MoodleClient()
        
        token = client._get_ws_token()
        assert token == ""


class TestCallWSAPI:
    """Test call_ws_api trong MoodleClient."""
    
    @patch('core.client.settings')
    def test_successful_api_call(self, mock_settings):
        """Gọi WS API thành công trả về dict."""
        mock_settings.MOODLE_WS_TOKEN = "valid_token"
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.PREFETCH_WORKERS = 4
        mock_settings.UTH_USERNAME = "test"
        mock_settings.UTH_PASSWORD = "pass"
        
        from core.client import MoodleClient
        with patch.object(MoodleClient, '_load_cookies'):
            client = MoodleClient()
        
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"events": [{"name": "Test"}]}
        client.session.post = MagicMock(return_value=mock_resp)
        
        result = client.call_ws_api('core_calendar_get_action_events_by_timesort')
        assert result is not None
        assert "events" in result
    
    @patch('core.client.settings')
    def test_expired_token_auto_refresh(self, mock_settings):
        """Token hết hạn → tự động refresh và retry."""
        mock_settings.MOODLE_WS_TOKEN = "expired_token"
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.PREFETCH_WORKERS = 4
        mock_settings.UTH_USERNAME = "test"
        mock_settings.UTH_PASSWORD = "pass"
        
        from core.client import MoodleClient
        with patch.object(MoodleClient, '_load_cookies'):
            client = MoodleClient()
        
        # First call returns invalidtoken, second (after refresh) returns success
        expired_resp = MagicMock()
        expired_resp.json.return_value = {"errorcode": "invalidtoken", "error": "Invalid token"}
        
        success_resp = MagicMock()
        success_resp.json.return_value = {"events": []}
        
        token_resp = MagicMock()
        token_resp.json.return_value = {"token": "fresh_token"}
        
        # First POST = API call (expired), second POST = token refresh, third POST = retry API
        client.session.post = MagicMock(side_effect=[expired_resp, token_resp, success_resp])
        
        with patch('config.save_settings'):
            result = client.call_ws_api('core_calendar_get_action_events_by_timesort')
        
        assert result is not None
    
    @patch('core.client.settings')
    def test_api_exception_error(self, mock_settings):
        """API trả exception → return None."""
        mock_settings.MOODLE_WS_TOKEN = "valid_token"
        mock_settings.MOODLE_BASE_URL = "https://courses.ut.edu.vn"
        mock_settings.PREFETCH_WORKERS = 4
        mock_settings.UTH_USERNAME = "test"
        mock_settings.UTH_PASSWORD = "pass"
        
        from core.client import MoodleClient
        with patch.object(MoodleClient, '_load_cookies'):
            client = MoodleClient()
        
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"exception": "error", "message": "Something failed"}
        client.session.post = MagicMock(return_value=mock_resp)
        
        result = client.call_ws_api('some_function')
        assert result is None


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
    @patch('core.data_orchestrator.ws_functions')
    def test_ws_api_success_skips_scraping(self, mock_ws, mock_settings):
        """WS API thành công → không gọi scraping."""
        mock_settings.USE_WS_API = True
        mock_settings.PREFETCH_WORKERS = 4
        mock_settings.DETAIL_CACHE_TTL_SECONDS = 1800
        mock_settings.DETAIL_CACHE_MAX_ENTRIES = 100
        
        mock_ws.ws_events_to_assignments.return_value = [
            {'title': 'Test', 'type': 'assignment', 'source': 'ws_api'}
        ]
        
        from core.data_orchestrator import DataOrchestrator
        orch = DataOrchestrator()
        orch.client = MagicMock()
        # Mock call_ws_api to return events dict directly
        orch.client.call_ws_api.return_value = {
            'events': [{'name': 'Test', 'modulename': 'assign', 'timesort': 1234567890}]
        }
        
        result = orch.get_latest_activities()
        assert len(result) == 1
        assert result[0]['source'] == 'ws_api'
        # login() should NOT have been called
        orch.client.login.assert_not_called()
    
    @patch('core.data_orchestrator.settings')
    @patch('core.data_orchestrator.ws_functions')
    def test_ws_api_fail_falls_back_to_scraping(self, mock_ws, mock_settings):
        """WS API thất bại → fallback sang scraping."""
        mock_settings.USE_WS_API = True
        mock_settings.PREFETCH_WORKERS = 4
        mock_settings.DETAIL_CACHE_TTL_SECONDS = 1800
        mock_settings.DETAIL_CACHE_MAX_ENTRIES = 100
        mock_settings.UTH_USERNAME = "test"
        mock_settings.UTH_PASSWORD = "pass"
        mock_settings.FETCH_MONTHS = 1
        
        mock_ws.get_calendar_action_events.return_value = None  # API failed
        
        from core.data_orchestrator import DataOrchestrator
        orch = DataOrchestrator()
        orch.client = MagicMock()
        orch.client.login.return_value = True
        orch.client.fetch_calendar.return_value = None
        
        result = orch.get_latest_activities()
        # Should have attempted login for scraping
        orch.client.login.assert_called()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
