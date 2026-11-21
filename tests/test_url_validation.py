from __future__ import annotations

import pytest

from core.url_validation import (
    is_trusted_uth_lms_url,
    validate_https_url,
    validate_public_https_url,
)


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


@pytest.mark.parametrize(
    "ssrf_url",
    (
        "https://127.0.0.1/secret",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/admin",
        "https://[::ffff:127.0.0.1]/admin",
        "https://10.0.0.1/internal",
        "https://172.16.0.1/internal",
        "https://192.168.1.1/router",
        "https://0.0.0.0/",
        "https://localhost/debug",
        "https://api.localhost/debug",
        "https://printer.local/",
    ),
)
def test_validate_public_https_url_blocks_literal_local_and_private_targets(ssrf_url):
    assert validate_public_https_url(ssrf_url, resolve_dns=False) is None
    assert validate_public_https_url(ssrf_url, resolve_dns=True) is None


def test_validate_public_https_url_checks_dns_resolution(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    assert validate_public_https_url("https://rebind.example.com/admin", resolve_dns=True) is None

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    assert (
        validate_public_https_url("https://example.com/course", resolve_dns=True)
        == "https://example.com/course"
    )
