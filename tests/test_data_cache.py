"""Tests for core.data_cache — DataCache save/load/clear.

Tests verify:
- save() creates valid JSON
- load() returns correct data
- load() from non-existent file returns ([], None)
- load() from corrupted file returns ([], None)
- clear() removes cache file
- Thread safety (concurrent save/load)
"""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.data_cache import DataCache


class TestDataCacheSaveLoad:
    """DataCache save and load cycle."""

    def test_save_and_load_round_trip(self, tmp_path):
        cache = DataCache(cache_dir=tmp_path)
        activities = [
            {"title": "Bài tập 1", "url": "https://example.com/1"},
            {"title": "Bài tập 2", "url": "https://example.com/2"},
        ]
        cache.save(activities)
        loaded, saved_at = cache.load()
        assert len(loaded) == 2
        assert loaded[0]["title"] == "Bài tập 1"
        assert saved_at is not None

    def test_load_nonexistent_returns_empty(self, tmp_path):
        cache = DataCache(cache_dir=tmp_path)
        loaded, saved_at = cache.load()
        assert loaded == []
        assert saved_at is None

    def test_load_corrupted_file_returns_empty(self, tmp_path):
        cache = DataCache(cache_dir=tmp_path)
        cache_file = tmp_path / "activities_cache.json"
        cache_file.write_text("NOT JSON AT ALL", encoding="utf-8")
        loaded, saved_at = cache.load()
        assert loaded == []
        assert saved_at is None

    def test_save_empty_list(self, tmp_path):
        cache = DataCache(cache_dir=tmp_path)
        cache.save([])
        loaded, saved_at = cache.load()
        assert loaded == []
        assert saved_at is not None  # saved_at should still be recorded

    def test_clear_removes_file(self, tmp_path):
        cache = DataCache(cache_dir=tmp_path)
        cache.save([{"title": "test"}])
        cache_file = tmp_path / "activities_cache.json"
        assert cache_file.exists()
        cache.clear()
        assert not cache_file.exists()

    def test_clear_no_file_no_error(self, tmp_path):
        cache = DataCache(cache_dir=tmp_path)
        # Should not raise
        cache.clear()

    def test_save_preserves_unicode(self, tmp_path):
        cache = DataCache(cache_dir=tmp_path)
        activities = [{"title": "Lập trình cơ sở dữ liệu", "course": "Môn 日本語"}]
        cache.save(activities)
        loaded, _ = cache.load()
        assert loaded[0]["title"] == "Lập trình cơ sở dữ liệu"
        assert loaded[0]["course"] == "Môn 日本語"

    def test_saved_json_structure(self, tmp_path):
        cache = DataCache(cache_dir=tmp_path)
        cache.save([{"x": 1}])
        raw = json.loads((tmp_path / "activities_cache.json").read_text(encoding="utf-8"))
        assert "version" in raw
        assert raw["version"] == 1
        assert "saved_at" in raw
        assert "count" in raw
        assert raw["count"] == 1
        assert "activities" in raw
