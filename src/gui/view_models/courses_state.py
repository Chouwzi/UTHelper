"""View-model state, layout breakpoints, and pure filters for Courses UI."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, CData, NavigableString

from core.courses.models import (
    CourseAttachment,
    CourseGradeItem,
    CourseModuleItem,
    CourseSection,
    CourseSummary,
    RecentMaterialEntry,
)
from core.html_compat import BS4_PARSER

CoursesLayoutMode = Literal["mobile", "tablet_portrait", "wide"]

_RESOURCE_MODNAMES = frozenset({"resource", "folder", "url", "googlemeet"})
_LINK_MODNAMES = frozenset({"url", "googlemeet"})
_OFFICE_KINDS = frozenset({"docx", "xlsx", "csv", "pptx"})
_ARCHIVE_EXTS = frozenset({"zip", "rar", "7z", "tar", "gz", "tgz"})

_MODULE_TYPE_MAP: dict[str, frozenset[str]] = {
    "materials": frozenset({"resource", "folder", "page", "book"}),
    "tasks": frozenset({"assign", "quiz", "scorm", "vpl"}),
    "attendance": frozenset({"attendance", "choice", "feedback"}),
    "groups": frozenset({"groupselect", "choicegroup", "choice"}),
    "links": _LINK_MODNAMES,
}

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


def compute_courses_layout_mode(width: float) -> CoursesLayoutMode:
    """Classify viewport width into adaptive Courses layout breakpoints."""
    if width is None or isinstance(width, bool):
        return "mobile"
    try:
        numeric = float(width)
    except (TypeError, ValueError):
        return "mobile"
    if not math.isfinite(numeric) or numeric < 0:
        return "mobile"
    if numeric >= 980:
        return "wide"
    if numeric >= 600:
        return "tablet_portrait"
    return "mobile"


def format_file_size(num_bytes: int) -> str:
    """Format byte counts into concise human-readable file sizes."""
    if num_bytes is None or isinstance(num_bytes, bool):
        return ""
    try:
        size = int(num_bytes)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 ** 3):.1f} GB"


def safe_http_url(url: object) -> str:
    """Return stripped URL only if it is a valid http/https URL without control chars."""
    if not isinstance(url, str):
        return ""
    cleaned = url.strip()
    if not cleaned or any(
        ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in cleaned
    ):
        return ""
    try:
        parsed = urlsplit(cleaned)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return cleaned


def compute_course_origin(course: CourseSummary) -> str:
    """Derive Moodle base origin from course.web_url with safe source fallback."""
    safe_web = safe_http_url(course.web_url)
    if safe_web:
        parsed = urlsplit(safe_web)
        return f"{parsed.scheme.lower()}://{parsed.netloc}"
    return (
        "https://thnn.ut.edu.vn"
        if course.source == "thnn"
        else "https://courses.ut.edu.vn"
    )


def extract_readable_html_and_links(raw_html: str) -> tuple[str, list[tuple[str, str]]]:
    """Strip unsafe tags from Moodle HTML and extract readable text plus safe links."""
    if not raw_html:
        return "", []
    soup = BeautifulSoup(raw_html, BS4_PARSER)
    for tag in soup.find_all(("script", "style", "noscript", "template", "iframe", "object")):
        tag.decompose()

    links: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        safe_url = safe_http_url(str(anchor["href"]))
        if safe_url and safe_url not in seen_urls:
            seen_urls.add(safe_url)
            label = anchor.get_text(" ", strip=True) or safe_url
            links.append((label, safe_url))

    for node in list(soup.descendants):
        node_type = type(node)
        if node_type is NavigableString or node_type is CData:
            normalized = re.sub(r"\s+", " ", str(node))
            if normalized != str(node):
                node.replace_with(
                    CData(normalized) if node_type is CData else NavigableString(normalized)
                )

    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")

    lines = [
        re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        for line in soup.get_text().splitlines()
    ]
    text = "\n".join(line for line in lines if line)
    return text, links


def _attachment_ext(att: CourseAttachment) -> str:
    ext = (att.extension or "").strip().lstrip(".").lower()
    if ext:
        return ext
    name = (att.file_name or "").strip().lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def _attachment_matches_kind(att: CourseAttachment, kind: str) -> bool:
    ext = _attachment_ext(att)
    if kind == "pdf":
        return att.preview_kind == "pdf" or ext == "pdf"
    if kind == "office":
        return att.preview_kind in _OFFICE_KINDS or ext in _OFFICE_KINDS
    if kind == "archive":
        return att.is_archive or att.preview_kind == "archive" or ext in _ARCHIVE_EXTS
    return False


def flatten_course_resources(
    sections: list[CourseSection], *, kind_filter: str = "all"
) -> list[tuple[str, CourseModuleItem]]:
    """Collect downloadable resources and external links across sections by kind."""
    kind = (kind_filter or "all").strip().lower()
    results: list[tuple[str, CourseModuleItem]] = []
    for sec in sections:
        for mod in sec.modules:
            if mod.modname not in _RESOURCE_MODNAMES:
                continue
            if kind == "link":
                if mod.modname in _LINK_MODNAMES:
                    results.append((sec.name, mod))
            elif kind in {"pdf", "office", "archive"}:
                if mod.modname in {"resource", "folder"} and any(
                    _attachment_matches_kind(att, kind) for att in mod.attachments
                ):
                    results.append((sec.name, mod))
            else:
                results.append((sec.name, mod))
    return results


def module_matches_type_filter(mod: CourseModuleItem, type_filter: str) -> bool:
    """Return True if a course module matches the selected content filter chip."""
    key = (type_filter or "all").strip().lower()
    allowed = _MODULE_TYPE_MAP.get(key)
    if allowed is None:
        return True
    return mod.modname in allowed


@dataclass
class CoursesState:
    """Mutable presentation state shared across Courses Master-Detail panes."""

    courses: list[CourseSummary] = field(default_factory=list)
    recent_items: list[RecentMaterialEntry] = field(default_factory=list)
    selected_course_key: Optional[str] = None
    semester_filter: str = "all"
    search_query: str = ""
    module_filter: str = "all"
    resource_kind_filter: str = "all"
    grade_filter: str = "all"
    active_subtab: int = 0
    sections: list[CourseSection] = field(default_factory=list)
    grades: list[CourseGradeItem] = field(default_factory=list)
    last_synced_at: int = 0
    loading_catalog: bool = False
    loading_detail: bool = False
    offline_notice: str = ""
    in_detail_mobile: bool = False
    pane3_expanded: bool = False
    user_groups: list[dict[str, Any]] = field(default_factory=list)

    def set_courses(self, courses: list[CourseSummary]) -> None:
        """Replace the catalog course summaries."""
        self.courses = list(courses)

    def available_semesters(self) -> list[str]:
        """Return unique semester labels ordered newest first."""
        best_sort: dict[str, tuple[int, int]] = {}
        for course in self.courses:
            label = (course.semester_label or "").strip()
            if not label:
                continue
            prev = best_sort.get(label)
            if prev is None or course.semester_sort_key > prev:
                best_sort[label] = course.semester_sort_key
        return sorted(best_sort.keys(), key=lambda lbl: (-best_sort[lbl][0], -best_sort[lbl][1], lbl))

    def selected_course(self) -> Optional[CourseSummary]:
        """Return the currently selected CourseSummary if present in catalog."""
        if not self.selected_course_key:
            return None
        for course in self.courses:
            if course.course_key == self.selected_course_key:
                return course
        return None

    def select_course(self, course_key: str) -> None:
        """Select a course by key and mark mobile detail view active."""
        self.selected_course_key = course_key
        self.in_detail_mobile = True

    def current_term_courses(self) -> list[CourseSummary]:
        """Return registered courses for the current term (Portal-matched, or newest semester fallback)."""
        explicit = [c for c in self.courses if c.is_current_term]
        if explicit:
            return explicit
        semesters = [s for s in self.available_semesters() if s != "Khác"]
        if semesters:
            top_sem = semesters[0]
            return [c for c in self.courses if c.semester_label == top_sem]
        return list(self.courses)

    def filtered_courses(self) -> list[CourseSummary]:
        """Filter catalog courses by active semester chip and search query."""
        sem = (self.semester_filter or "all").strip()
        sem_lower = sem.lower()
        query = (self.search_query or "").strip().casefold()
        current_keys: Optional[set[str]] = None
        if sem_lower == "current":
            current_keys = {c.course_key for c in self.current_term_courses()}

        matched: list[CourseSummary] = []
        for course in self.courses:
            if sem_lower and sem_lower != "all":
                if sem_lower == "current":
                    if current_keys is not None and course.course_key not in current_keys:
                        continue
                elif sem_lower == "thnn":
                    if course.source != "thnn" and course.semester_label.lower() != "thnn":
                        continue
                elif course.semester_label != sem:
                    continue

            if query:
                haystack = " ".join(
                    part
                    for part in (
                        course.display_name,
                        course.fullname,
                        course.shortname,
                        course.course_code,
                        course.teacher_name,
                        course.program_tag,
                    )
                    if part
                ).casefold()
                if query not in haystack:
                    continue

            matched.append(course)
        return matched
