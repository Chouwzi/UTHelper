"""Tests for core.data_orchestrator — detail cache and smart polling.

Tests verify:
- Detail LRU cache: get/set, TTL expiry, max entries eviction
- get_updates_since: detects changes, returns empty, handles errors
- get_cached_details_snapshot returns copy
"""
import os
import sys
import time
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
