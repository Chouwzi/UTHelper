from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EligibilityStatus = Literal["academic", "conditional", "transferred", "unknown"]
ConfidenceLevel = Literal["complete", "partial", "insufficient"]


@dataclass(frozen=True)
class CourseEligibility:
    """Course classification for GPA & radar eligibility."""

    status: EligibilityStatus
    reason: str = ""


@dataclass(frozen=True)
class CourseGradeItem:
    """Normalized grade item for a single course in a semester."""

    course_id: str
    course_code: str
    course_name: str
    credits: float
    process_grade: float | None = None  # diemTBThuongKy
    exam_grade: float | None = None     # diemThi
    final_grade: float | None = None    # diemTongKet
    credit_grade: float | None = None   # diemTinChi (he 4)
    letter_grade: str = ""              # diemChu (A, B+, C, ...)
    is_passed: bool = True
    is_transferred: bool = False
    eligibility: CourseEligibility = field(
        default_factory=lambda: CourseEligibility(status="academic")
    )


@dataclass(frozen=True)
class RadarMetric:
    """Single metric on the academic radar (0..100)."""

    key: str
    label: str
    score: float | None
    display_score: int | None
    raw_value: float | None
    raw_unit: str
    coverage: float
    confidence: ConfidenceLevel
    status: str
    color: str
    description: str
    calculation_text: str
    positive_courses: list[dict[str, Any]] = field(default_factory=list)
    negative_courses: list[dict[str, Any]] = field(default_factory=list)
    delta_vs_previous: float | None = None


@dataclass(frozen=True)
class RadarProfile:
    """Complete academic radar profile for a semester."""

    semester_id: int | str
    semester_name: str
    metrics: dict[str, RadarMetric]
    ordered_metrics: list[RadarMetric]
    strongest_metrics: list[RadarMetric]
    weakest_metrics: list[RadarMetric]
    overall_coverage: float
    is_polygon_viable: bool
    gpa_10: float | None = None
    gpa_4: float | None = None
    credits_earned: float | None = None
    classification: str | None = None
    training_point: float | None = None
    insight_text: str = ""
    scoring_version: int = 1


@dataclass(frozen=True)
class SemesterGradeData:
    """Normalized raw container for a single semester."""

    id: int | str
    name: str
    summary: dict[str, Any] = field(default_factory=dict)
    courses: list[CourseGradeItem] = field(default_factory=list)
