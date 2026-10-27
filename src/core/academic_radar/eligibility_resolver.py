from __future__ import annotations

from typing import Any
from core.academic_radar.models import CourseEligibility


# Known conditional subject code prefixes or specific codes verified in UTH curriculum
KNOWN_CONDITIONAL_CODES = {
    "006124",   # Tin học cơ bản (0 TC)
    "TT01",     # General English 1 (0 TC)
    "TT02",     # General English 2 (0 TC)
    "TT03",     # General English 3 (0 TC)
    "TT04",     # General English 4 (0 TC)
}

KNOWN_CONDITIONAL_PREFIXES = (
    "GDTC",  # Giáo dục thể chất
    "GDQP",  # Giáo dục quốc phòng - an ninh
)


class CourseEligibilityResolver:
    """Resolves whether a course is academic (GPA-contributing), conditional, transferred, or unknown."""

    def resolve(self, course_data: dict[str, Any]) -> CourseEligibility:
        # Priority 1: Transferred courses
        if course_data.get("isChuyenDiem") is True or course_data.get("is_transferred") is True:
            return CourseEligibility(
                status="transferred",
                reason="Học phần chuyển điểm / miễn điểm",
            )

        # Priority 2: Zero-credit / conditional courses
        credits = float(course_data.get("soTinChi") or course_data.get("credits") or 0.0)
        code = str(course_data.get("maLopHocPhan") or course_data.get("maHocPhan") or course_data.get("course_code") or "").strip()

        if credits == 0.0:
            return CourseEligibility(
                status="conditional",
                reason="Học phần điều kiện (0 tín chỉ)",
            )

        if code in KNOWN_CONDITIONAL_CODES or any(code.startswith(p) for p in KNOWN_CONDITIONAL_PREFIXES):
            return CourseEligibility(
                status="conditional",
                reason="Học phần điều kiện theo quy chế UTH (GDTC/GDQP/Anh văn tổng quát)",
            )

        # Priority 3: Standard academic courses
        if credits > 0.0:
            return CourseEligibility(
                status="academic",
                reason="Học phần tính điểm học lực tích lũy",
            )

        return CourseEligibility(
            status="unknown",
            reason="Chưa xác định loại học phần",
        )
