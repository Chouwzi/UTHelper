from __future__ import annotations

from dataclasses import dataclass

from core.academic_radar.models import CourseGradeItem, SemesterGradeData


@dataclass(frozen=True)
class OverallGpa:
    """GPA tổng hợp từ toàn bộ các kỳ (trung bình có trọng số theo tín chỉ)."""

    gpa_10: float | None
    gpa_4: float | None
    total_credits: float  # tổng tín chỉ được tính vào GPA hệ 10
    semester_count: int
    course_count: int


def _eligible(course: CourseGradeItem) -> bool:
    return (
        course.credits > 0
        and course.final_grade is not None
        and not course.is_transferred
        and course.eligibility.status in ("academic", "unknown")
    )


def _dedupe_best(courses: list[CourseGradeItem]) -> list[CourseGradeItem]:
    """Học lại / cải thiện: mỗi mã học phần chỉ giữ lần có điểm tổng kết cao nhất."""
    best: dict[str, CourseGradeItem] = {}
    anonymous: list[CourseGradeItem] = []
    for c in courses:
        key = c.course_code.strip().upper()
        if not key:
            anonymous.append(c)
            continue
        current = best.get(key)
        if current is None or (c.final_grade or 0.0) > (current.final_grade or 0.0):
            best[key] = c
    return [*best.values(), *anonymous]


def _weighted(pairs: list[tuple[float, float]]) -> float | None:
    total = sum(c for _, c in pairs)
    if total <= 0:
        return None
    return sum(g * c for g, c in pairs) / total


def calculate_overall_gpa(semesters: list[SemesterGradeData]) -> OverallGpa:
    """Tính GPA hệ 10 và hệ 4 trên mọi kỳ.

    Quy tắc: bỏ môn 0 tín chỉ, môn chuyển điểm, môn chưa có điểm tổng kết;
    môn học lại chỉ tính lần điểm cao nhất.
    """
    courses = [c for s in semesters for c in s.courses if _eligible(c)]
    courses = _dedupe_best(courses)

    pairs_10 = [(c.final_grade, c.credits) for c in courses if c.final_grade is not None]
    pairs_4 = [(c.credit_grade, c.credits) for c in courses if c.credit_grade is not None]

    return OverallGpa(
        gpa_10=_weighted(pairs_10),
        gpa_4=_weighted(pairs_4),
        total_credits=sum(c for _, c in pairs_10),
        semester_count=len(semesters),
        course_count=len(courses),
    )


def required_average(
    current_gpa: float,
    current_credits: float,
    target_gpa: float,
    additional_credits: float,
) -> float | None:
    """Điểm trung bình cần đạt ở `additional_credits` tín chỉ sắp tới để GPA = target.

    required = (target * (C + A) - current * C) / A
    Trả về None nếu additional_credits <= 0. Kết quả có thể âm (đã vượt mục tiêu)
    hoặc vượt thang điểm (không khả thi); nơi gọi tự diễn giải.
    """
    if additional_credits <= 0 or current_credits < 0:
        return None
    total = current_credits + additional_credits
    return (target_gpa * total - current_gpa * current_credits) / additional_credits
