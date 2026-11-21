from __future__ import annotations

import pytest

from core.url_validation import is_trusted_uth_lms_url, validate_https_url


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    (
        (
            "https://courses.ut.edu.vn/mod/quiz/view.php?id=123",
            "https://courses.ut.edu.vn/mod/quiz/view.php?id=123",
        ),
        (
            "https://portal.ut.edu.vn/home",
            "https://portal.ut.edu.vn/home",
        ),
        (
            "https://us02web.zoom.us/j/123456789?pwd=abc",
            "https://us02web.zoom.us/j/123456789?pwd=abc",
        ),
    ),
)
def test_validate_https_url_accepts_clean_https(raw_url, expected):
    assert validate_https_url(raw_url) == expected


@pytest.mark.parametrize(
    "bad_url",
    (
        None,
        "",
        "   ",
        "  https://portal.ut.edu.vn/home  ",
        123,
        "http://courses.ut.edu.vn/mod/quiz/view.php?id=1",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,<script>alert(1)</script>",
        "https:///missing-host",
        "https://user:pass@courses.ut.edu.vn/course",
        "https://courses.ut.edu.vn/path\ninjected",
    ),
)
def test_validate_https_url_rejects_unsafe_or_malformed(bad_url):
    assert validate_https_url(bad_url) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    (
        ("https://courses.ut.edu.vn/course/view.php?id=10", True),
        ("https://PORTAL.UT.EDU.VN/schedule", True),
        ("https://thnn.ut.edu.vn/my/", True),
        ("https://elearning.ut.edu.vn/", True),
        ("http://courses.ut.edu.vn/course/view.php?id=10", False),
        ("https://evil.example/courses.ut.edu.vn", False),
        ("https://courses.ut.edu.vn.evil.example/login", False),
        ("https://user@courses.ut.edu.vn/", False),
    ),
)
def test_is_trusted_uth_lms_url_pins_known_uth_hosts(url, expected):
    assert is_trusted_uth_lms_url(url) is expected
