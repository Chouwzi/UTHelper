import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
import json
import os
import pytest
from datetime import datetime, timedelta
from notifiers.manager import NotificationManager
from config import settings as config

@pytest.fixture
def mock_config(monkeypatch):
    monkeypatch.setattr(config, "NOTIFY_MILESTONES", [72, 24, 3])
    monkeypatch.setattr(config, "NOTIFY_MUTED_COURSES", ["Xác suất thống kê"])
    monkeypatch.setattr(config, "NOTIFY_DND_ENABLE", True)
    monkeypatch.setattr(config, "NOTIFY_DND_START", 23)
    monkeypatch.setattr(config, "NOTIFY_DND_END", 6)
    monkeypatch.setattr(config, "NOTIFY_IGNORE_SUBMITTED", True)

@pytest.fixture
def manager(tmp_path):
    cache_file = tmp_path / "test_notifications.json"
    mgr = NotificationManager(cache_file=str(cache_file))
    mgr._cache_path = str(cache_file)
    return mgr

def test_dnd_logic(manager, mock_config, monkeypatch):
    mock_now = datetime(2025, 1, 1, 1, 30, 0)
    import notifiers.manager
    
    # Simple monkeypatch
    class MockDatetime:
        @classmethod
        def now(cls):
            return mock_now
            
    monkeypatch.setattr(notifiers.manager, "datetime", MockDatetime)
    assert manager._is_in_dnd() is True

    mock_now = datetime(2025, 1, 1, 10, 30, 0)
    MockDatetime.now = classmethod(lambda cls: datetime(2025, 1, 1, 10, 30, 0))
    assert manager._is_in_dnd() is False

def test_milestone_filtering(manager, mock_config):
    now = datetime.now()
    
    task_25h = {
        "url": "http://test.com/1",
        "title": "Task 1",
        "course_name": "Toán 1",
        "deadline": (now + timedelta(hours=25)).isoformat(),
        "submission_status": "not_submitted",
        "is_open": True
    }
    
    task_overdue = {
        "url": "http://test.com/2",
        "title": "Task 2",
        "course_name": "Toán 2",
        "deadline": (now - timedelta(hours=1)).isoformat(),
        "submission_status": "not_submitted",
        "is_open": True
    }
    
    filtered = manager._filter_assignments([task_25h, task_overdue])
    
    assert len(filtered) == 1
    assert filtered[0]["assignment"]["url"] == "http://test.com/1"
    assert filtered[0]["milestone"] == 72

    manager._mark_assignments_notified([filtered[0]])
    filtered_again = manager._filter_assignments([task_25h])
    assert len(filtered_again) == 0

def test_muted_courses(manager, mock_config):
    now = datetime.now()
    task_muted = {
        "url": "http://test.com/3",
        "title": "Bài tập XSTK",
        "course_name": "Xác suất thống kê",
        "deadline": (now + timedelta(hours=20)).isoformat(),
        "submission_status": "not_submitted",
        "is_open": True
    }
    
    task_normal = {
        "url": "http://test.com/4",
        "title": "Bài tập Toán",
        "course_name": "Toán 2",
        "deadline": (now + timedelta(hours=20)).isoformat(),
        "submission_status": "not_submitted",
        "is_open": True
    }

    filtered = manager._filter_assignments([task_muted, task_normal])
    assert len(filtered) == 1
    assert filtered[0]["assignment"]["url"] == "http://test.com/4"

def test_ignore_submitted(manager, mock_config):
    now = datetime.now()
    task_submitted = {
        "url": "http://test.com/5",
        "title": "Bài tập đã nộp",
        "course": "Cơ sở dữ liệu",
        "deadline": (now + timedelta(hours=5)).isoformat(),
        "submission_status": "submitted",
        "is_open": True
    }
    
    task_graded = {
        "url": "http://test.com/6",
        "title": "Bài tập đã chấm",
        "course": "Cơ sở dữ liệu",
        "deadline": (now + timedelta(hours=5)).isoformat(),
        "submission_status": "graded",
        "is_open": True
    }

    filtered = manager._filter_assignments([task_submitted, task_graded])
    assert len(filtered) == 0
