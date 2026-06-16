"""
Display utilities shared between core and GUI layers.
Functions here are pure-logic (no Flet imports) so both the core
filter service and the GUI layer can use them without circular deps.
"""
import re


def clean_course_name(course: str) -> str:
    """Strip prefixes/suffixes, keep only human-readable course name."""
    # Định dạng ban đầu: [ID]_HKII..._Tên_Môn MãHP
    cleaned = re.sub(r'^\[.*?\]_HKII\d{4}-\d{4}_', '', course)
    cleaned = re.sub(r'_\d{9,}$', '', cleaned)
    # Định dạng hiển thị bằng dấu gạch ngang
    dash_match = re.match(r'^\[.*?\]\s*-\s*(.+?)\s*-\s*[\dA-Z]{6,}$', cleaned)
    if dash_match:
        cleaned = dash_match.group(1)
    # Nếu không khớp, xoá tiền tố ngoặc vuông
    cleaned = re.sub(r'^\[.*?\]\s*[-_]?\s*', '', cleaned)
    return cleaned.strip() or course


def urgency_str(urgency) -> str:
    """Normalise UrgencyLevel enum or plain string to lowercase value."""
    raw = str(urgency)
    for v in ("critical", "warning", "safe"):
        if v in raw.lower():
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
