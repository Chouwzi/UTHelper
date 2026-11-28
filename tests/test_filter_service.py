import pytest
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from core.filter_service import FilterService

@pytest.fixture
def sample_data():
    from datetime import datetime, timedelta
    now = datetime.now()
    return [
        {
            "id": "1",
            "course": "Math",
            "title": "Assignment 1",
            "deadline": (now + timedelta(hours=10)).isoformat(),
            "url": "http://test",
            "hours_remaining": 10.0,
            "urgency": "critical",
            "submission_status": "not_submitted",
            "type": "assignment"
        },
        {
            "id": "2",
            "course": "Math",
            "title": "Assignment 2",
            "deadline": (now + timedelta(hours=40)).isoformat(),
            "url": "http://test2",
            "hours_remaining": 40.0,
            "urgency": "warning",
            "submission_status": "submitted",
            "type": "assignment"
        },
        {
            "id": "3",
            "course": "Physics",
            "title": "Quiz 1",
            "deadline": (now + timedelta(hours=100)).isoformat(),
            "url": "http://test3",
            "hours_remaining": 100.0,
            "urgency": "safe",
            "submission_status": "graded",
            "type": "quiz"
        }
    ]

def test_filter_service_filtering(sample_data):
    # Thử lọc theo mức độ khẩn cấp xem sao
    filtered, _ = FilterService.filter_and_count(sample_data, active_urgency="critical")
    assert len(filtered) == 1
    assert filtered[0]['id'] == "1"

    # Giờ thử lọc theo tên môn học
    filtered, _ = FilterService.filter_and_count(sample_data, active_course="Math")
    assert len(filtered) == 2
    assert set([f['id'] for f in filtered]) == {"1", "2"}

def test_filter_service_counting(sample_data):
    # Kiểm tra xem đếm số lượng các loại có đúng không
    _, counts = FilterService.filter_and_count(sample_data)

    assert counts["urgency"]["critical"] == 1
    assert counts["urgency"]["warning"] == 1
    assert counts["urgency"]["safe"] == 1
    
    assert counts["type"]["assignment"] == 2
    assert counts["type"]["quiz"] == 1

    assert counts["course"]["Math"] == 2
    assert counts["course"]["Physics"] == 1

def test_filter_service_sorting(sample_data):
    # Sắp xếp theo thời gian còn lại tăng dần (cái nào gấp hơn lên đầu)
    # Should sort by hours_remaining ascending by default
    filtered, _ = FilterService.filter_and_count(sample_data)
    assert filtered[0]['id'] == "1" # 10h
    assert filtered[1]['id'] == "2" # 40h
    assert filtered[2]['id'] == "3" # 100h


def test_filter_service_string_deadline_no_crash():
    """Regression: str deadline in _deadline_dt should not crash (TypeError)."""
    data = [
        {
            "id": "bad",
            "course": "Test",
            "title": "String deadline",
            "deadline": "2026-01-01 23:59",
            "_deadline_dt": "2026-01-01 23:59",  # str instead of datetime
            "type": "assignment",
            "submission_status": "",
        },
        {
            "id": "none_dt",
            "course": "Test",
            "title": "None deadline_dt",
            "deadline": "invalid-date",
            "_deadline_dt": None,
            "type": "quiz",
            "submission_status": "",
        },
    ]
    # Should not raise TypeError
    filtered, counts = FilterService.filter_and_count(data, include_overdue=True)
    assert counts["urgency"]["all"] >= 0
