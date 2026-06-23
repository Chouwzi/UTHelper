"""Tests for core.notification_history — NotificationHistory CRUD.

Tests verify:
- add() creates entries with correct structure
- get_all() returns entries in reverse chronological order
- clear() empties history
- Max history limit (_MAX_HISTORY=100)
- Graceful handling of dict and object assignments
"""
import json
import os
import sys
import pytest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.notification_history import NotificationHistory


class _MockAssignment:
    """Mock assignment object with attributes."""
    def __init__(self, title="Test", course_name="Web", url="http://test", 
                 deadline="2026-06-30", event_type="assignment"):
        self.title = title
        self.course_name = course_name
        self.url = url
        self.deadline = deadline
        self.event_type = event_type


class TestNotificationHistoryAdd:
    """NotificationHistory.add() tests."""

    def test_add_object_assignment(self, tmp_path):
        history = NotificationHistory(history_dir=tmp_path)
        history.add([_MockAssignment()], channels=["windows"])
        entries = history.get_all()
        assert len(entries) == 1
        assert entries[0]["title"] == "Test"
        assert entries[0]["course"] == "Web"
        assert "windows" in entries[0]["channels"]

    def test_add_dict_assignment(self, tmp_path):
        history = NotificationHistory(history_dir=tmp_path)
        assignment = {
            "title": "Quiz 1",
            "course": "Math",
            "url": "http://example.com",
            "deadline": "2026-07-01",
            "type": "quiz",
        }
        history.add([assignment], channels=["discord"])
        entries = history.get_all()
        assert len(entries) == 1
        assert entries[0]["title"] == "Quiz 1"

    def test_add_multiple_entries(self, tmp_path):
        history = NotificationHistory(history_dir=tmp_path)
        history.add([_MockAssignment(title="A1")], channels=["ch1"])
        history.add([_MockAssignment(title="A2")], channels=["ch2"])
        entries = history.get_all()
        assert len(entries) == 2
        # Newest first
        assert entries[0]["title"] == "A2"
        assert entries[1]["title"] == "A1"

    def test_entries_have_sent_at(self, tmp_path):
        history = NotificationHistory(history_dir=tmp_path)
        history.add([_MockAssignment()], channels=["windows"])
        entries = history.get_all()
        assert "sent_at" in entries[0]

    def test_max_history_limit(self, tmp_path):
        history = NotificationHistory(history_dir=tmp_path)
        for i in range(110):
            history.add([_MockAssignment(title=f"A{i}")], channels=["test"])
        entries = history.get_all()
        assert len(entries) == 100  # _MAX_HISTORY = 100


class TestNotificationHistoryGetAndClear:
    """NotificationHistory.get_all() and clear() tests."""

    def test_get_all_empty(self, tmp_path):
        history = NotificationHistory(history_dir=tmp_path)
        entries = history.get_all()
        assert entries == []

    def test_clear(self, tmp_path):
        history = NotificationHistory(history_dir=tmp_path)
        history.add([_MockAssignment()], channels=["windows"])
        assert len(history.get_all()) == 1
        history.clear()
        assert len(history.get_all()) == 0

    def test_clear_no_file(self, tmp_path):
        history = NotificationHistory(history_dir=tmp_path)
        # Should not raise
        history.clear()

    def test_corrupted_file_returns_empty(self, tmp_path):
        history = NotificationHistory(history_dir=tmp_path)
        bad_file = tmp_path / "notification_history.json"
        bad_file.write_text("CORRUPTED!", encoding="utf-8")
        entries = history.get_all()
        assert entries == []
