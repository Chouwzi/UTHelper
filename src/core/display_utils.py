"""
Display utilities shared between core and GUI layers.
Functions here are pure-logic (no Flet imports) so both the core
filter service and the GUI layer can use them without circular deps.
"""
import html
import re
from functools import lru_cache

# Pre-compiled regex patterns — avoid re-compiling on every call
_RE_HKII = re.compile(r'^\[.*?\]_HKII\d{4}-\d{4}_')
_RE_TRAILING_ID = re.compile(r'_\d{9,}$')
_RE_DASH_FORMAT = re.compile(r'^\[.*?\]\s*-\s*(.+?)\s*-\s*[\dA-Z]{6,}$')
_RE_BRACKET_PREFIX = re.compile(r'^\[.*?\]\s*[-_]?\s*')


@lru_cache(maxsize=256)
def clean_course_name(course: str) -> str:
    """Strip prefixes/suffixes, decode HTML entities, keep only human-readable course name."""
    # Decode HTML entities first (Moodle returns &amp; etc.)
    course = html.unescape(course)
    cleaned = _RE_HKII.sub('', course)
    cleaned = _RE_TRAILING_ID.sub('', cleaned)
    dash_match = _RE_DASH_FORMAT.match(cleaned)
    if dash_match:
        cleaned = dash_match.group(1)
    cleaned = _RE_BRACKET_PREFIX.sub('', cleaned)
    return cleaned.strip() or course


def urgency_str(urgency) -> str:
    """Normalise UrgencyLevel enum or plain string to lowercase value."""
    raw_lower = str(urgency).lower()
    for v in ("critical", "warning", "safe"):
        if v in raw_lower:
            return v
    return "safe"


# Type filter mapping — used by FilterService and GUI dropdowns
_TYPE_FILTER_MAP = {
    "quiz":       {"quiz"},
    "assignment": {"assignment", "deadline"},
    "attendance": {"attendance"},
    "open":       {"open"},
    "other":      {"other"},
}

# Type display mapping — used by all notifiers
_TYPE_DISPLAY_MAP = {
    "quiz":       ("❓", "Trắc nghiệm"),
    "assign":     ("📝", "Bài tập"),
    "assignment": ("📝", "Bài tập"),
    "deadline":   ("📝", "Bài tập"),
    "forum":      ("💬", "Thảo luận"),
    "attendance": ("📌", "Điểm danh"),
    "resource":   ("📄", "Tài liệu"),
    "url":        ("🔗", "Liên kết"),
    "choice":     ("📊", "Khảo sát"),
    "open":       ("📂", "Đang mở"),
}

# Urgency display mapping — used by all notifiers
_URGENCY_DISPLAY_MAP = {
    "critical": ("🔴", "Khẩn cấp"),
    "warning":  ("🟠", "Sắp tới hạn"),
    "safe":     ("🟢", "An toàn"),
}


def get_type_display(raw_type: str) -> tuple:
    """Returns (emoji, label_vi) for activity type. Used by all notifiers."""
    if not raw_type:
        return ("📄", "Khác")
    key = str(raw_type).lower().strip()
    return _TYPE_DISPLAY_MAP.get(key, ("📄", raw_type))


def get_urgency_display(urgency) -> tuple:
    """Returns (emoji, label_vi) for urgency level. Used by all notifiers."""
    normalized = urgency_str(urgency)
    return _URGENCY_DISPLAY_MAP.get(normalized, ("🟢", "An toàn"))
