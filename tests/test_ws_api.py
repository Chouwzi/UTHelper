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
        with patch.object(MoodleClient, '__init__', lambda self: None):
            client = MoodleClient()
        
        token = client._get_ws_token()
        assert token == "cached_token_abc"
    





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
    




if __name__ == '__main__':
    pytest.main([__file__, '-v'])
