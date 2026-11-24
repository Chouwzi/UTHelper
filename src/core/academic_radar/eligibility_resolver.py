from __future__ import annotations

import math
from typing import Any
from core.academic_radar.models import CourseEligibility


def _safe_float(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, str):
        stripped = val.strip()
        if not stripped or stripped == "-":
            return None
        val = stripped.replace(",", ".")
    try:
        numeric = float(val)
    except (ValueError, TypeError):
        return None
    return numeric if math.isfinite(numeric) else None


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


def _is_truthy_flag(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return math.isfinite(val) and val != 0
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes"}
    return False


def _matches_conditional_code(raw_code: Any) -> bool:
    code = str(raw_code or "").strip().upper()
    if not code:
        return False
    return code in KNOWN_CONDITIONAL_CODES or any(
        code.startswith(prefix) for prefix in KNOWN_CONDITIONAL_PREFIXES
    )


class CourseEligibilityResolver:
    """Resolves whether a course is academic (GPA-contributing), conditional, transferred, or unknown."""

    def resolve(self, course_data: dict[str, Any]) -> CourseEligibility:
        # Priority 1: Transferred courses
        if _is_truthy_flag(course_data.get("isChuyenDiem")) or _is_truthy_flag(
            course_data.get("is_transferred")
        ):
            return CourseEligibility(
                status="transferred",
                reason="Học phần chuyển điểm / miễn điểm",
            )

        # Priority 2: Zero-credit / conditional courses
        parsed_credits = _safe_float(course_data.get("soTinChi"))
        if parsed_credits is None:
            parsed_credits = _safe_float(course_data.get("credits"))
        if parsed_credits is None:
            parsed_credits = _safe_float(course_data.get("stc"))
        candidate_codes = (
            course_data.get("maHocPhan"),
            course_data.get("course_code"),
            course_data.get("maLopHocPhan"),
        )

        if parsed_credits == 0.0:
            return CourseEligibility(
                status="conditional",
                reason="Học phần điều kiện (0 tín chỉ)",
            )

        if any(_matches_conditional_code(c) for c in candidate_codes):
            return CourseEligibility(
                status="conditional",
                reason="Học phần điều kiện theo quy chế UTH (GDTC/GDQP/Anh văn tổng quát)",
            )

        # Priority 3: Standard academic courses
        if parsed_credits is not None and parsed_credits > 0.0:
            return CourseEligibility(
                status="academic",
                reason="Học phần tính điểm học lực tích lũy",
            )

        return CourseEligibility(
            status="unknown",
            reason="Chưa xác định loại học phần",
        )
