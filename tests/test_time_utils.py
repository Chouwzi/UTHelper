"""Tests for core.time_utils — parse_datetime, format_remaining_time.

Tests verify:
- ISO format parsing
- Timezone-aware → naive conversion
- Empty/None input handling
- format_remaining_time: future, past, edge cases
"""
import os
import sys
import pytest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.time_utils import parse_datetime, format_remaining_time


class TestParseDatetime:
    """parse_datetime() tests."""

    def test_parse_iso_format(self):
        result = parse_datetime("2026-06-23T10:30:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 6
        assert result.day == 23
        assert result.hour == 10
        assert result.minute == 30

    def test_parse_iso_with_timezone(self):
        result = parse_datetime("2026-06-23T10:30:00+07:00")
        assert result is not None
        # Should be converted to local naive
        assert result.tzinfo is None

    def test_parse_empty_string_returns_none(self):
        assert parse_datetime("") is None

    def test_parse_none_returns_none(self):
        assert parse_datetime(None) is None

    def test_parse_invalid_string_returns_none(self):
        assert parse_datetime("not a date") is None

    def test_parse_date_only(self):
        result = parse_datetime("2026-06-23")
        assert result is not None
        assert result.year == 2026
        assert result.month == 6

    def test_parse_utc_timezone(self):
        result = parse_datetime("2026-06-23T03:30:00Z")
        assert result is not None
        assert result.tzinfo is None  # Should strip timezone

    def test_parse_returns_naive_datetime(self):
        result = parse_datetime("2026-01-15T14:00:00+00:00")
        assert result is not None
        assert result.tzinfo is None


class TestFormatRemainingTime:
    """format_remaining_time() tests."""

    def test_none_deadline(self):
        assert format_remaining_time(None) == "Không rõ"

    def test_past_deadline(self):
        past = datetime.now() - timedelta(hours=2)
        result = format_remaining_time(past)
        assert result == "Quá hạn!"

    def test_future_deadline_days(self):
        future = datetime.now() + timedelta(days=3, hours=5)
        result = format_remaining_time(future)
        assert "3 ngày" in result

    def test_future_deadline_hours(self):
        future = datetime.now() + timedelta(hours=5, minutes=30)
        result = format_remaining_time(future)
        assert "5 giờ" in result

    def test_future_deadline_minutes_only(self):
        future = datetime.now() + timedelta(minutes=25)
        result = format_remaining_time(future)
        assert "phút" in result

    def test_string_deadline_iso(self):
        future = (datetime.now() + timedelta(days=2)).isoformat()
        result = format_remaining_time(future)
        assert "ngày" in result or "giờ" in result

    def test_invalid_string_deadline(self):
        result = format_remaining_time("garbage")
        assert result == "Không rõ"

    def test_empty_string_deadline(self):
        result = format_remaining_time("")
        assert result == "Không rõ"
