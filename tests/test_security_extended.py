"""Extended tests for core.security — HTMLSanitizer comprehensive coverage.

Tests verify:
- Whitelist tag behavior: safe tags kept, unsafe stripped
- Event handler removal (onclick, onload, etc.)
- srcdoc attribute removal
- Unsafe URI scheme removal (javascript:, data:, vbscript:)
- Safe URI schemes preserved (http, https, mailto)
- Style attribute removal
- sanitize_soup() in-place sanitization
- Edge cases: empty string, None-like, nested unsafe tags
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.security import HTMLSanitizer


class TestHTMLSanitizerWhitelist:
    """Tag whitelist behavior."""

    def test_safe_tags_preserved(self):
        html = "<p>Hello <strong>world</strong></p>"
        result = HTMLSanitizer.sanitize(html)
        assert "<p>" in result
        assert "<strong>" in result

    def test_unsafe_script_stripped(self):
        html = "<p>Safe</p><script>alert('xss')</script>"
        result = HTMLSanitizer.sanitize(html)
        assert "<script>" not in result
        assert "alert" in result  # text content preserved

    def test_unsafe_iframe_stripped(self):
        html = '<iframe src="http://evil.com"></iframe>'
        result = HTMLSanitizer.sanitize(html)
        assert "<iframe" not in result

    def test_unsafe_form_stripped(self):
        html = '<form action="http://evil.com"><input type="text"></form>'
        result = HTMLSanitizer.sanitize(html)
        assert "<form" not in result
        assert "<input" not in result

    def test_a_tag_preserved(self):
        html = '<a href="https://courses.ut.edu.vn">Link</a>'
        result = HTMLSanitizer.sanitize(html)
        assert "<a" in result
        assert "https://courses.ut.edu.vn" in result

    def test_img_tag_preserved(self):
        html = '<img src="https://example.com/image.png" alt="test">'
        result = HTMLSanitizer.sanitize(html)
        assert "<img" in result

    def test_table_tags_preserved(self):
        html = "<table><tr><td>Cell</td></tr></table>"
        result = HTMLSanitizer.sanitize(html)
        assert "<table>" in result
        assert "<td>" in result


class TestHTMLSanitizerAttributes:
    """Attribute cleaning."""

    def test_event_handlers_removed(self):
        html = '<div onclick="alert(1)" onmouseover="steal()">Content</div>'
        result = HTMLSanitizer.sanitize(html)
        assert "onclick" not in result
        assert "onmouseover" not in result
        assert "Content" in result

    def test_onload_removed(self):
        html = '<img src="https://ok.com/img.png" onload="evil()">'
        result = HTMLSanitizer.sanitize(html)
        assert "onload" not in result

    def test_style_attribute_removed(self):
        html = '<p style="color:red">Styled</p>'
        result = HTMLSanitizer.sanitize(html)
        assert "style" not in result
        assert "Styled" in result

    def test_srcdoc_removed(self):
        html = '<iframe srcdoc="<script>evil()</script>">X</iframe>'
        result = HTMLSanitizer.sanitize(html)
        assert "srcdoc" not in result


class TestHTMLSanitizerURISchemes:
    """URI scheme validation."""

    def test_javascript_scheme_removed(self):
        html = '<a href="javascript:alert(1)">Click</a>'
        result = HTMLSanitizer.sanitize(html)
        assert "javascript:" not in result

    def test_data_scheme_removed(self):
        html = '<a href="data:text/html,<script>alert(1)</script>">X</a>'
        result = HTMLSanitizer.sanitize(html)
        assert "data:" not in result

    def test_vbscript_scheme_removed(self):
        html = '<a href="vbscript:msgbox">X</a>'
        result = HTMLSanitizer.sanitize(html)
        assert "vbscript:" not in result

    def test_http_scheme_preserved(self):
        html = '<a href="http://example.com">Link</a>'
        result = HTMLSanitizer.sanitize(html)
        assert "http://example.com" in result

    def test_https_scheme_preserved(self):
        html = '<a href="https://secure.example.com">Link</a>'
        result = HTMLSanitizer.sanitize(html)
        assert "https://secure.example.com" in result

    def test_mailto_scheme_preserved(self):
        html = '<a href="mailto:test@example.com">Email</a>'
        result = HTMLSanitizer.sanitize(html)
        assert "mailto:" in result


class TestHTMLSanitizerEdgeCases:
    """Edge cases."""

    def test_empty_string(self):
        assert HTMLSanitizer.sanitize("") == ""

    def test_none_returns_empty(self):
        assert HTMLSanitizer.sanitize(None) == ""

    def test_plain_text_passthrough(self):
        result = HTMLSanitizer.sanitize("Just plain text")
        assert "Just plain text" in result

    def test_nested_unsafe_tags(self):
        html = '<div><script><script>double</script></script></div>'
        result = HTMLSanitizer.sanitize(html)
        assert "<script>" not in result

    def test_sanitize_soup_none_input(self):
        # Should not raise
        HTMLSanitizer.sanitize_soup(None)

    def test_sanitize_soup_in_place(self):
        from bs4 import BeautifulSoup
        from core.html_compat import BS4_PARSER
        soup = BeautifulSoup('<p onclick="x">Hello <script>evil</script></p>', BS4_PARSER)
        HTMLSanitizer.sanitize_soup(soup)
        result = str(soup)
        assert "<script>" not in result
        assert "onclick" not in result
        assert "Hello" in result
