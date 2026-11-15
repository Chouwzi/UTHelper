"""Pure immutable data models for the Courses & THNN feature."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

CourseSource = Literal["courses", "thnn"]
OpenStrategy = Literal[
    "preview_file",
    "archive_tree",
    "folder_files",
    "assignment_detail",
    "webview_activity",
    "external_url",
    "inline_page",
    "web_fallback",
]
PreviewKind = Literal[
    "pdf",
    "docx",
    "xlsx",
    "csv",
    "pptx",
    "image",
    "text",
    "archive",
    "unsupported",
]


@dataclass(frozen=True, slots=True)
class CourseAttachment:
    """Downloadable or inline file attachment belonging to a course module."""

    file_name: str
    file_url: str
    file_size: int = 0
    mime_type: str = ""
    time_modified: int = 0
    extension: str = ""
    preview_kind: PreviewKind = "unsupported"
    is_archive: bool = False


@dataclass(frozen=True, slots=True)
class CourseModuleItem:
    """Single Moodle module/activity inside a course section."""

    cmid: int
    instance_id: int
    modname: str
    name: str
    section_id: int
    url: str = ""
    web_url: str = ""
    description_html: str = ""
    inline_html: str = ""
    completion_mode: int = 0
    completion_state: int = 0
    timemodified: int = 0
    attachments: tuple[CourseAttachment, ...] = ()
    open_strategy: OpenStrategy = "web_fallback"
    is_new: bool = False


@dataclass(frozen=True, slots=True)
class CourseSection:
    """Course topic/week section containing ordered course modules."""

    id: int
    course_id: int
    source: CourseSource
    section_num: int
    name: str
    summary_html: str = ""
    visible: bool = True
    modules: tuple[CourseModuleItem, ...] = ()


@dataclass(frozen=True, slots=True)
class CourseSummary:
    """Normalized summary of an enrolled course across Courses and THNN sites."""

    id: int
    source: CourseSource
    fullname: str
    shortname: str
    display_name: str
    course_code: str = ""
    semester_label: str = "Khác"
    semester_sort_key: tuple[int, int] = (0, 0)
    program_tag: str = ""
    teacher_name: str = ""
    teacher_email: str = ""
    teacher_phone: str = ""
    meet_url: str = ""
    progress: Optional[float] = None
    completed_count: int = 0
    total_trackable: int = 0
    summary_text: str = ""
    web_url: str = ""
    last_synced_at: int = 0
    has_unseen_updates: bool = False
    is_pinned: bool = False

    @property
    def course_key(self) -> str:
        """Stable cross-site identifier combining source and Moodle course ID."""
        return f"{self.source}:{self.id}"


@dataclass(frozen=True, slots=True)
class CourseGradeItem:
    """Single gradebook row within a course."""

    id: int
    item_name: str
    item_module: str = ""
    grade_formatted: str = "-"
    range_formatted: str = ""
    percentage: str = ""
    weight: str = ""
    feedback_html: str = ""


@dataclass(frozen=True, slots=True)
class RecentMaterialEntry:
    """Recently opened material entry persisted for quick resume."""

    course_key: str
    course_name: str
    cmid: int
    title: str
    attachment_url: str
    file_name: str
    opened_at: int
    source: CourseSource = "courses"
    course_id: int = 0
