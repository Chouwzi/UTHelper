"""Parse UTH Moodle shortnames, fullnames, and teacher/Meet metadata."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Iterable, Optional

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
_PHONE_RE = re.compile(r"(?<!\d)(0\d{2,3}[ .\-]?\d{3}[ .\-]?\d{3,4})(?!\d)")
_TEACHER_RE = re.compile(
    r"(?:Giảng[ \t]*viên|GV(?:[ \t]*phụ[ \t]*trách)?)[ \t]*[:\-][ \t]*([^\n\r(;,\|]{3,60})",
    re.IGNORECASE,
)
_TEACHER_TRAILING_MARKER_RE = re.compile(
    r"(?:\s+[-–]\s*(?:E-?mail|SĐT|SDT|ĐT|DĐ|Điện\s*thoại|Phone|Tel|Link(?:\s*học(?:\s*online)?)?|Meet|Zoom)\b"
    r"|\s+(?:E-?mail|SĐT|SDT|ĐT|DĐ|Điện\s*thoại|Phone|Tel|Link(?:\s*học(?:\s*online)?)?)\s*[:\-]).*$",
    re.IGNORECASE,
)
_MEET_URL_RE = re.compile(
    r"https://(?:meet\.google\.com|(?:[a-z0-9-]+\.)*zoom\.us|teams\.microsoft\.com)/[^\s\"'<>]+",
    re.IGNORECASE,
)
_ANY_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_URL_TRAILING_PUNCT = ".,;:!?)]}"
_BLOCK_TAGS = (
    "p",
    "div",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "dt",
    "dd",
    "section",
    "article",
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


def _find_valid_semester_match(
    pattern: re.Pattern[str], text: str, *, year_offset: int
) -> Optional[tuple[re.Match[str], str, tuple[int, int]]]:
    for match in pattern.finditer(text):
        y1 = year_offset + int(match.group("y1"))
        y2 = year_offset + int(match.group("y2"))
        # Academic years always span consecutive calendar years; rejects single 4-digit years like 2026 -> 20-26.
        if y2 != y1 + 1:
            continue
        label, sort_key = _normalize_term(match.group("term"), y1, y2)
        return match, label, sort_key
    return None


def _extract_display_name_from_short(
    without_prog: str, sem_match: re.Match[str], course_code: str
) -> str:
    remainder = f"{without_prog[:sem_match.start()]} {without_prog[sem_match.end():]}"
    if course_code:
        remainder = re.sub(rf"\[?{re.escape(course_code)}\]?", " ", remainder)
    return re.sub(r"\s+", " ", remainder.replace("_", " ")).strip(" -–_")


def parse_course_identity(shortname: str, fullname: str) -> ParsedCourseIdentity:
    """Extract clean display name, course code, semester metadata, and program tag."""
    short = html.unescape(shortname or "").strip()
    full = html.unescape(fullname or "").strip()

    prog_match = _PROGRAM_RE.match(short)
    program_tag = prog_match.group("prog").upper() if prog_match else ""
    short_without_prog = _PROGRAM_RE.sub("", short, count=1) if prog_match else short

    semester_label = "Khác"
    semester_sort_key = (0, 0)
    short_long = _find_valid_semester_match(_SEMESTER_LONG_RE, short_without_prog, year_offset=0)
    sem_found = (
        short_long
        or _find_valid_semester_match(_SEMESTER_LONG_RE, full, year_offset=0)
        or _find_valid_semester_match(_SEMESTER_COMPACT_RE, short, year_offset=2000)
        or _find_valid_semester_match(_SEMESTER_COMPACT_RE, full, year_offset=2000)
    )
    if sem_found:
        _, semester_label, semester_sort_key = sem_found

    code_match = _CODE_RE.search(short) or _CODE_RE.search(full)
    course_code = code_match.group(1) if code_match else ""

    # Prefer structured segment from UTH shortname when it contains a valid long semester span.
    display_name = ""
    if "_" in short and short_long:
        display_name = _extract_display_name_from_short(
            short_without_prog, short_long[0], course_code
        )

    if not display_name:
        cleaned = _BRACKET_PREFIX_RE.sub("", full)
        if course_code:
            cleaned = cleaned.replace(f"[{course_code}]", "").replace(course_code, "")
        cleaned = _TRAILING_CAMPUS_RE.sub("", cleaned)
        cleaned = re.sub(r"\s+[-–](?:\s*[-–])*\s+", " - ", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        cleaned = re.sub(r"^[\s\-–_]+|[\s\-–_]+$", "", cleaned)
        display_name = cleaned or full or short

    return ParsedCourseIdentity(
        display_name=display_name,
        course_code=course_code,
        semester_label=semester_label,
        semester_sort_key=semester_sort_key,
        program_tag=program_tag,
    )


def _soup_to_lines(soup: BeautifulSoup) -> str:
    """Convert parsed HTML to newline-delimited block text without splitting inline tags."""
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    for cell in soup.find_all(("td", "th")):
        cell.insert_after(" ")
    raw_text = soup.get_text()
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in raw_text.splitlines()]
    return "\n".join(line for line in lines if line)


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
            elif not teacher_email and href.lower().startswith("mailto:"):
                candidate = href[7:].split("?", 1)[0].strip()
                m_mailto = _EMAIL_RE.search(candidate)
                if m_mailto:
                    teacher_email = m_mailto.group(0)

        text = _soup_to_lines(soup)
        non_url_text = _ANY_URL_RE.sub(" ", text)

        if not teacher_name:
            m_teacher = _TEACHER_RE.search(non_url_text)
            if m_teacher:
                raw_name = _TEACHER_TRAILING_MARKER_RE.sub("", m_teacher.group(1))
                teacher_name = raw_name.strip(" .;-:")
        if not teacher_email:
            m_email = _EMAIL_RE.search(text)
            if m_email:
                teacher_email = m_email.group(0)
        if not teacher_phone:
            m_phone = _PHONE_RE.search(non_url_text)
            if m_phone:
                teacher_phone = re.sub(r"\D", "", m_phone.group(1))
        if not meet_url:
            m_meet = _MEET_URL_RE.search(text)
            if m_meet:
                meet_url = m_meet.group(0).rstrip(_URL_TRAILING_PUNCT)

    return TeacherContactInfo(
        teacher_name=teacher_name,
        teacher_email=teacher_email,
        teacher_phone=teacher_phone,
        meet_url=meet_url,
    )
