from bs4 import BeautifulSoup
from urllib.parse import urlsplit
from core.html_compat import BS4_PARSER

class HTMLSanitizer:
    """
    Làm sạch nội dung HTML từ Moodle để ngăn chặn XSS và đảm bảo hiển thị sạch sẽ.
    Sử dụng WHITELIST: chỉ giữ lại các thẻ an toàn đã biết, loại bỏ tất cả thẻ khác.
    """

    SAFE_TAGS = frozenset({
        "p", "br", "ul", "ol", "li", "a", "strong", "em", "b", "i", "u",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "tr", "td", "th", "thead", "tbody", "tfoot",
        "span", "div", "img", "blockquote", "pre", "code",
        "dl", "dt", "dd", "hr", "sup", "sub",
        "caption", "col", "colgroup", "figure", "figcaption",
        "abbr", "cite", "small", "mark", "del", "ins", "s", "q",
        "time", "var", "samp", "kbd", "wbr",
    })

    # Attributes that can carry dangerous URIs
    _URI_ATTRS = frozenset({
        "href", "src", "data-src",
        "xlink:href", "action", "formaction", "data", "poster", "background",
    })

    @classmethod
    def _clean_tag(cls, tag) -> None:
        """Remove disallowed attributes from a single tag."""
        attrs = dict(tag.attrs)
        for attr, val in attrs.items():
            al = attr.lower()
            if al == "srcdoc":
                try:
                    del tag.attrs[attr]
                except Exception:
                    pass
                continue
            # Remove event handlers and inline styles
            if al.startswith("on") or al == "style":
                try:
                    del tag.attrs[attr]
                except Exception:
                    pass
                continue
            # Validate URI schemes in dangerous attributes
            if al in cls._URI_ATTRS:
                try:
                    v = val.strip() if isinstance(val, str) else ""
                    scheme = urlsplit(v).scheme.lower()
                    if scheme and scheme not in {"http", "https", "mailto"}:
                        try:
                            del tag.attrs[attr]
                        except Exception:
                            pass
                except Exception:
                    try:
                        del tag.attrs[attr]
                    except Exception:
                        pass

    @classmethod
    def _strip_disallowed_tags(cls, parent) -> None:
        """Unwrap tags not in the whitelist (keep their text content, remove the tag itself)."""
        for tag in list(parent.find_all(True)):
            if tag.name not in cls.SAFE_TAGS:
                tag.unwrap()

    @staticmethod
    def sanitize(html: str) -> str:
        if not html:
            return ""

        soup = BeautifulSoup(html, BS4_PARSER)

        # 1. Whitelist: chỉ giữ các thẻ an toàn, unwrap phần còn lại
        HTMLSanitizer._strip_disallowed_tags(soup)

        # 2. Làm sạch các thuộc tính trên các thẻ còn lại
        for tag in soup.find_all(True):
            HTMLSanitizer._clean_tag(tag)

        # 3. Xử lý Dark/Light mode: việc xóa thuộc tính 'style' ở trên là cách an toàn nhất để reset màu sắc.

        return str(soup)

    @staticmethod
    def sanitize_soup(tag) -> None:
        """
        Sanitize a pre-parsed BeautifulSoup tag IN-PLACE.
        Avoids the cost of serializing to string and re-parsing.
        Same logic as sanitize() but operates on the existing parse tree.
        """
        if tag is None:
            return

        # 1. Whitelist: chỉ giữ các thẻ an toàn, unwrap phần còn lại
        HTMLSanitizer._strip_disallowed_tags(tag)

        # 2. Làm sạch các thuộc tính trên các thẻ còn lại
        for child in tag.find_all(True):
            HTMLSanitizer._clean_tag(child)
