"""Domain models for the Learning Roadmap feature (stdlib only)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class CourseStatus(str, Enum):
    COMPLETED = "completed"      # passed, or exempted / credit-transferred
    IN_PROGRESS = "in_progress"  # registered this semester, no final result yet
    FAILED = "failed"            # failed and not (yet) passed / retaken -> outstanding
    NOT_TAKEN = "not_taken"      # in the curriculum, no record at all


class RequirementStatus(str, Enum):
    MET = "met"
    IN_PROGRESS = "in_progress"
    NOT_MET = "not_met"
    UNKNOWN = "unknown"          # no evidence either way; never guessed


@dataclass(frozen=True)
class FrameworkCourse:
    """One course of the curriculum framework (chuongtrinhkhung)."""

    code: str
    name: str
    credits: float = 0.0
    semester_no: int | None = None
    required: bool | None = None


@dataclass(frozen=True)
class RoadmapCourse:
    code: str
    name: str
    credits: float
    status: CourseStatus
    grade_text: str = ""          # e.g. "8.5 · A"
    note: str = ""                # e.g. "Miễn / chuyển điểm", "Học lại"
    is_conditional: bool = False  # 0-credit / GDTC / GDQP / English-track
    required: bool | None = None
    in_framework: bool = True
    search_text: str = ""         # accent-folded "code name" for matching


@dataclass(frozen=True)
class RoadmapSemester:
    key: str                      # "hk-3", "unscheduled", "extra"
    label: str
    order: int
    courses: tuple[RoadmapCourse, ...] = ()

    def count(self, status: CourseStatus) -> int:
        return sum(1 for c in self.courses if c.status is status)


@dataclass(frozen=True)
class GraduationRequirement:
    key: str                      # "english" | "it" | "defense" | "pe"
    label: str
    status: RequirementStatus
    detail: str = ""
    progress_done: int | None = None    # e.g. 3 of "3/4" (English levels, GDQP courses)
    progress_total: int | None = None   # e.g. 4

    @property
    def has_progress(self) -> bool:
        return self.progress_done is not None and bool(self.progress_total)

    @property
    def progress_text(self) -> str:
        return f"{self.progress_done}/{self.progress_total}" if self.has_progress else ""

    @property
    def progress_ratio(self) -> float:
        if not self.has_progress:
            return 0.0
        return max(0.0, min(1.0, self.progress_done / self.progress_total))


@dataclass(frozen=True)
class CreditProgress:
    required: float | None
    earned: float | None

    @property
    def ratio(self) -> float | None:
        if not self.required or self.earned is None:
            return None
        return max(0.0, min(1.0, self.earned / self.required))


@dataclass(frozen=True)
class RoadmapSnapshot:
    semesters: tuple[RoadmapSemester, ...]
    requirements: tuple[GraduationRequirement, ...]
    credits: CreditProgress
    framework_available: bool
    portal_verdict: bool | None = None   # Portal's own graduation verdict, if exposed
    portal_message: str = ""
    endpoint_errors: dict[str, str] = field(default_factory=dict)
    generated_at: datetime | None = None
    anchor_term: str | None = None       # "2025-1": latest Portal semester (planner key)
    anchor_name: str = ""                # its raw Portal name, for display
    entry_year: int | None = None        # start year of the student's first semester
    history: tuple[RoadmapSemester, ...] = ()   # finished terms, newest first

    def all_courses(self) -> list[RoadmapCourse]:
        return [c for s in self.semesters for c in s.courses]

    def counts(self) -> dict[CourseStatus, int]:
        out = {s: 0 for s in CourseStatus}
        for c in self.all_courses():
            out[c.status] += 1
        return out
