"""Parse UTH Moodle shortnames, fullnames, and teacher/Meet metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from bs4 import BeautifulSoup

from core.html_compat import BS4_PARSER

_PROGRAM_RE = re.compile(r"^\[(?P<prog>[A-Za-z0-9_-]{2,10})\]_")
_SEMESTER_LONG_RE = re.compile(
    r"(?:^|[_\s\[-])(?P<term>HK(?:I{1,2}|H|HE|3))[-_\s]*(?P<y1>20\d{2})[-–](?P<y2>20\d{2})(?:$|[_\s\]-])",
    re.IGNORECASE,
)
_SEMESTER_COMPACT_RE = re.compile(
    r"(?:^|[\[\s_])(?P<term>HK(?:I{1,2}|H|HE|3))\s*(?P<y1>\d{2})[-–]?(?P<y2>\d{2})(?:$|[\]\s_-])",
    re.IGNORECASE,
)
_CODE_RE = re.compile(r"(?<!\d)(\d{10,14})(?!\d)")
_TRAILING_CAMPUS_RE = re.compile(
    r"\s*-\s*(?:CNS_CS\d|HTHN_[A-Z0-9]+|HOI TRUONG [A-Z0-9]+|CS\d)\s*$",
    re.IGNORECASE,
)
_BRACKET_PREFIX_RE = re.compile(r"^(?:\[[^\]]+\]\s*[-–_]?\s*)+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\b0\d{9,10}\b")
_TEACHER_RE = re.compile(
    r"(?:Giảng\s*viên|GV(?:\s*phụ\s*trách)?)\s*[:\-]\s*([^\n\r(;,\|]{3,60})",
    re.IGNORECASE,
)
_MEET_URL_RE = re.compile(
    r"https://(?:meet\.google\.com|zoom\.us|teams\.microsoft\.com)/[^\s\"'<>]+",
    re.IGNORECASE,
)

_TERM_ORDER = {"HKI": 3, "HKII": 2, "HKH": 1, "HKHE": 1, "HK3": 1}
_TERM_DISPLAY = {"HKI": "HKI", "HKII": "HKII", "HKH": "HK Hè", "HKHE": "HK Hè", "HK3": "HK Hè"}


@dataclass(frozen=True, slots=True)
class ParsedCourseIdentity:
    """Normalized identity fields extracted from a course shortname and fullname."""

    display_name: str
    course_code: str
    semester_label: str
    semester_sort_key: tuple[int, int]
    program_tag: str


@dataclass(frozen=True, slots=True)
class TeacherContactInfo:
    """Lecturer contact details and online meeting link parsed from course HTML."""

    teacher_name: str = ""
    teacher_email: str = ""
    teacher_phone: str = ""
    meet_url: str = ""


def _normalize_term(raw_term: str, y1: int, y2: int) -> tuple[str, tuple[int, int]]:
    upper = raw_term.upper()
    label_term = _TERM_DISPLAY.get(upper, upper)
    order = _TERM_ORDER.get(upper, 0)
    return f"{label_term} {y1}-{y2}", (y1, order)


def parse_course_identity(shortname: str, fullname: str) -> ParsedCourseIdentity:
    """Extract clean display name, course code, semester metadata, and program tag."""
    short = (shortname or "").strip()
    full = (fullname or "").strip()
    combined = f"{short} {full}"

    prog_match = _PROGRAM_RE.match(short)
    program_tag = prog_match.group("prog").upper() if prog_match else ""

    semester_label = "Khác"
    semester_sort_key = (0, 0)
    m_long = _SEMESTER_LONG_RE.search(combined)
    if m_long:
        semester_label, semester_sort_key = _normalize_term(
            m_long.group("term"), int(m_long.group("y1")), int(m_long.group("y2"))
        )
    else:
        m_compact = _SEMESTER_COMPACT_RE.search(combined)
        if m_compact:
            y1 = 2000 + int(m_compact.group("y1"))
            y2 = 2000 + int(m_compact.group("y2"))
            semester_label, semester_sort_key = _normalize_term(
                m_compact.group("term"), y1, y2
            )

    code_match = _CODE_RE.search(short) or _CODE_RE.search(full)
    course_code = code_match.group(1) if code_match else ""

    # Prefer structured segment from UTH shortname if formatted cleanly with underscores.
    display_name = ""
    if "_" in short and _SEMESTER_LONG_RE.search(short):
        parts = [p.strip() for p in short.split("_") if p.strip()]
        candidates = [
            p
            for p in parts
            if (not program_tag or p.upper() != f"[{program_tag}]")
            and not _SEMESTER_LONG_RE.search(f"_{p}_")
            and p != course_code
        ]
        if candidates:
            display_name = max(candidates, key=len)

    if not display_name:
        cleaned = _BRACKET_PREFIX_RE.sub("", full)
        if course_code:
            cleaned = cleaned.replace(f"[{course_code}]", "").replace(course_code, "")
        cleaned = _TRAILING_CAMPUS_RE.sub("", cleaned)
        cleaned = re.sub(r"^[\s\-–_]+|[\s\-–_]+$", "", cleaned)
        display_name = cleaned or full or short

    return ParsedCourseIdentity(
        display_name=display_name,
        course_code=course_code,
        semester_label=semester_label,
        semester_sort_key=semester_sort_key,
        program_tag=program_tag,
    )


def extract_teacher_and_meet_info(html_fragments: Iterable[str]) -> TeacherContactInfo:
    """Scan Moodle HTML fragments for teacher name, email, phone, and meeting URL."""
    teacher_name = ""
    teacher_email = ""
    teacher_phone = ""
    meet_url = ""

    for fragment in html_fragments:
        if not fragment:
            continue
        soup = BeautifulSoup(fragment, BS4_PARSER)
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"]).strip()
            if not meet_url and _MEET_URL_RE.match(href):
                meet_url = href

        text = soup.get_text(" ", strip=True)
        if not teacher_name:
            m_teacher = _TEACHER_RE.search(text)
            if m_teacher:
                teacher_name = m_teacher.group(1).strip(" .;-:")
        if not teacher_email:
            m_email = _EMAIL_RE.search(text)
            if m_email:
                teacher_email = m_email.group(0)
        if not teacher_phone:
            m_phone = _PHONE_RE.search(text)
            if m_phone:
                teacher_phone = m_phone.group(0)
        if not meet_url:
            m_meet = _MEET_URL_RE.search(text)
            if m_meet:
                meet_url = m_meet.group(0)

    return TeacherContactInfo(
        teacher_name=teacher_name,
        teacher_email=teacher_email,
        teacher_phone=teacher_phone,
        meet_url=meet_url,
    )
