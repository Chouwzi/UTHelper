import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core.security import HTMLSanitizer


def test_sanitizer_removes_srcdoc_content():
    html = '<iframe srcdoc="<script>alert(1)</script>"></iframe><p>safe</p>'

    sanitized = HTMLSanitizer.sanitize(html)

    assert "srcdoc" not in sanitized.lower()
    assert "script" not in sanitized.lower()
    assert "safe" in sanitized


def test_sanitizer_removes_unsafe_url_schemes():
    html = (
        '<a href="vbscript:msgbox(1)">bad</a>'
        '<img src="data:text/html,<script>alert(1)</script>">'
        '<a href="https://courses.ut.edu.vn/mod/assign/view.php?id=1">safe</a>'
    )

    sanitized = HTMLSanitizer.sanitize(html)

    assert "vbscript:" not in sanitized.lower()
    assert "data:text/html" not in sanitized.lower()
    assert "https://courses.ut.edu.vn/mod/assign/view.php?id=1" in sanitized
