import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
import sys
import os

# Create path to src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from models import Assignment, UrgencyLevel, ActivityDetail
from config import settings

@pytest.fixture
def base_assignment():
    return Assignment(
        id="test_id",
        course_id="test_course",
        course_name="Test Course",
        title="Test Title",
        deadline=datetime(2025, 1, 1, 12, 0),
        url="http://test.url"
    )

@patch('models.datetime')
def test_hours_remaining(mock_datetime, base_assignment):
    # Setup mock time to be exactly 2 hours before the deadline
    mock_datetime.now.return_value = datetime(2025, 1, 1, 10, 0)
    
    # hours_remaining is a property returning float
    assert base_assignment.hours_remaining == 2.0

    mock_datetime.now.return_value = datetime(2025, 1, 1, 12, 0)
    assert base_assignment.hours_remaining == 0.0

    mock_datetime.now.return_value = datetime(2025, 1, 1, 13, 0)
    assert base_assignment.hours_remaining == -1.0

@patch('models.datetime')
def test_urgency_levels(mock_datetime, base_assignment):
    # Test CRITICAL (less than URGENCY_CRITICAL_HOURS, default 24)
    mock_datetime.now.return_value = base_assignment.deadline - timedelta(hours=settings.URGENCY_CRITICAL_HOURS - 1)
    assert base_assignment.urgency == UrgencyLevel.CRITICAL

    # Test WARNING (less than URGENCY_WARNING_HOURS, default 72, but >= CRITICAL)
    mock_datetime.now.return_value = base_assignment.deadline - timedelta(hours=settings.URGENCY_WARNING_HOURS - 1)
    assert base_assignment.urgency == UrgencyLevel.WARNING

    # Test SAFE (>= URGENCY_WARNING_HOURS)
    mock_datetime.now.return_value = base_assignment.deadline - timedelta(hours=settings.URGENCY_WARNING_HOURS + 1)
    assert base_assignment.urgency == UrgencyLevel.SAFE

def test_assignment_default_values():
    assignment = Assignment(
        id="test_id_2",
        course_id="c2",
        course_name="C2",
        title="T2",
        deadline=datetime(2024, 1, 1),
        url="http://url"
    )
    assert assignment.event_type == "other"
    assert assignment.submission_status == "unknown"
    assert assignment.details is None
