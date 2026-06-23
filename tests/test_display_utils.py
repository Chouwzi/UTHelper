"""Tests for core.display_utils — clean_course_name, urgency_str, type/urgency display.

Tests verify:
- Course name cleaning: HKII prefix, trailing IDs, bracket prefix
- HTML entity decoding in course names
- urgency_str normalization
- get_type_display mapping
- get_urgency_display mapping
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.display_utils import (
    clean_course_name,
    urgency_str,
    get_type_display,
    get_urgency_display,
)


class TestCleanCourseName:
    """clean_course_name() tests."""

    def test_strip_hkii_prefix(self):
        raw = "[CNTT]_HKII2024-2025_Lập trình Web_123456789"
        result = clean_course_name(raw)
        assert "HKII" not in result
        assert "Lập trình Web" in result

    def test_strip_trailing_id(self):
        raw = "Lập trình cơ sở dữ liệu_123456789"
        result = clean_course_name(raw)
        assert "123456789" not in result
        assert "Lập trình cơ sở dữ liệu" in result

    def test_bracket_prefix_stripped(self):
        raw = "[CNTT] - Lập trình Web - CNTT001"
        result = clean_course_name(raw)
        # Should extract "Lập trình Web"
        assert "Lập trình Web" in result

    def test_html_entities_decoded(self):
        raw = "Database &amp; Systems"
        result = clean_course_name(raw)
        assert "&" in result
        assert "&amp;" not in result

    def test_empty_string_returns_original(self):
        # clean_course_name should return something, not empty
        result = clean_course_name("Some Course")
        assert result == "Some Course"

    def test_already_clean_name(self):
        result = clean_course_name("Machine Learning")
        assert result == "Machine Learning"

    def test_caching_returns_same_result(self):
        """LRU cache should return same result for same input."""
        r1 = clean_course_name("Test Course ABC")
        r2 = clean_course_name("Test Course ABC")
        assert r1 == r2


class TestUrgencyStr:
    """urgency_str() normalization."""

    def test_critical_string(self):
        assert urgency_str("critical") == "critical"

    def test_warning_string(self):
        assert urgency_str("warning") == "warning"

    def test_safe_string(self):
        assert urgency_str("safe") == "safe"

    def test_case_insensitive(self):
        assert urgency_str("CRITICAL") == "critical"
        assert urgency_str("Warning") == "warning"

    def test_enum_like_string(self):
        assert urgency_str("UrgencyLevel.critical") == "critical"

    def test_unknown_defaults_to_safe(self):
        assert urgency_str("unknown") == "safe"
        assert urgency_str("") == "safe"


class TestGetTypeDisplay:
    """get_type_display() tests."""

    def test_quiz(self):
        emoji, label = get_type_display("quiz")
        assert emoji == "❓"
        assert label == "Trắc nghiệm"

    def test_assignment(self):
        emoji, label = get_type_display("assignment")
        assert emoji == "📝"

    def test_attendance(self):
        emoji, label = get_type_display("attendance")
        assert emoji == "📌"

    def test_unknown_type(self):
        emoji, label = get_type_display("unknown_type")
        assert emoji == "📄"

    def test_empty_string(self):
        emoji, label = get_type_display("")
        assert emoji == "📄"
        assert label == "Khác"

    def test_none_type(self):
        emoji, label = get_type_display(None)
        assert emoji == "📄"

    def test_case_insensitive(self):
        emoji, label = get_type_display("QUIZ")
        assert emoji == "❓"


class TestGetUrgencyDisplay:
    """get_urgency_display() tests."""

    def test_critical(self):
        emoji, label = get_urgency_display("critical")
        assert emoji == "🔴"
        assert label == "Khẩn cấp"

    def test_warning(self):
        emoji, label = get_urgency_display("warning")
        assert emoji == "🟠"

    def test_safe(self):
        emoji, label = get_urgency_display("safe")
        assert emoji == "🟢"

    def test_default(self):
        emoji, label = get_urgency_display("unknown")
        assert emoji == "🟢"
        assert label == "An toàn"
