"""Tính điểm cần đạt ở các môn chưa có điểm để kéo GPA học kỳ (hệ 4) lên mục tiêu.

Module thuần logic (không phụ thuộc Flet) để dễ test.

LƯU Ý: bảng quy đổi bên dưới theo Thông tư 08/2021/TT-BGDĐT (thang phổ biến).
Nếu trường áp dụng thang khác, chỉ cần sửa ``GRADE_SCALE``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.academic_radar.models import CourseGradeItem, SemesterGradeData

# (điểm hệ 10 tối thiểu, điểm chữ, điểm hệ 4) - sắp xếp giảm dần
GRADE_SCALE: tuple[tuple[float, str, float], ...] = (
    (8.5, "A", 4.0),
    (7.8, "B+", 3.5),
    (7.0, "B", 3.0),
    (6.3, "C+", 2.5),
    (5.5, "C", 2.0),
    (4.8, "D+", 1.5),
    (4.0, "D", 1.0),
    (0.0, "F", 0.0),
)

# Điểm hệ 4 thấp nhất vẫn tính là qua môn (D)
PASS_POINT = 1.0
_EPS = 1e-9


def score10_to_point4(score: float) -> float:
    for min10, _letter, point in GRADE_SCALE:
        if score + _EPS >= min10:
            return point
    return 0.0


def letter_to_point4(letter: str) -> float | None:
    key = (letter or "").strip().upper()
    if key == "A+":  # một số thang có A+, coi như 4.0
        return 4.0
    for _min10, ltr, point in GRADE_SCALE:
        if ltr == key:
            return point
    return None


def point_to_scale_row(point: float) -> tuple[float, str, float]:
    """Trả về dòng thang điểm nhỏ nhất có điểm hệ 4 >= ``point``."""
    for row in reversed(GRADE_SCALE):
        if row[2] + _EPS >= point:
            return row
    return GRADE_SCALE[0]


@dataclass(frozen=True)
class PendingCourseNeed:
    course: CourseGradeItem
    letter: str
    min_score10: float
    point4: float


@dataclass(frozen=True)
class GpaTargetResult:
    status: str  # "achieved" | "needed" | "impossible" | "no_pending" | "no_courses" | "invalid"
    target: float
    current_gpa4: float | None = None       # GPA các môn đã có điểm
    graded_credits: float = 0.0
    pending_credits: float = 0.0
    required_avg4: float | None = None      # điểm hệ 4 TB cần đạt ở các môn còn lại
    max_possible_gpa4: float | None = None  # GPA tối đa nếu mọi môn còn lại đạt 4.0
    uniform_letter: str = ""
    uniform_min_score10: float | None = None
    pending: list[PendingCourseNeed] = field(default_factory=list)
    message: str = ""


def course_point4(c: CourseGradeItem) -> float | None:
    """Điểm hệ 4 của môn đã có điểm; None nếu chưa có điểm."""
    if c.credit_grade is not None:
        return float(c.credit_grade)
    if c.letter_grade:
        p = letter_to_point4(c.letter_grade)
        if p is not None:
            return p
    if c.final_grade is not None:
        return score10_to_point4(float(c.final_grade))
    return None


_course_point4 = course_point4


def _is_gpa_course(c: CourseGradeItem) -> bool:
    return (
        c.credits > 0
        and not c.is_transferred
        and c.eligibility.status in ("academic", "unknown")
    )


def calculate_gpa_target(semester: SemesterGradeData | None, target: float) -> GpaTargetResult:
    if target is None or not (0.0 <= target <= 4.0):
        return GpaTargetResult(
            status="invalid", target=target or 0.0,
            message="Nhập GPA mục tiêu trong khoảng 0.0 – 4.0.",
        )

    courses = [c for c in (semester.courses if semester else []) if _is_gpa_course(c)]
    if not courses:
        return GpaTargetResult(
            status="no_courses", target=target,
            message="Học kỳ này chưa có học phần tính GPA.",
        )

    graded: list[tuple[CourseGradeItem, float]] = []
    pending_courses: list[CourseGradeItem] = []
    for c in courses:
        p = _course_point4(c)
        if p is None:
            pending_courses.append(c)
        else:
            graded.append((c, p))

    graded_credits = sum(c.credits for c, _ in graded)
    graded_points = sum(c.credits * p for c, p in graded)
    pending_credits = sum(c.credits for c in pending_courses)
    current_gpa = graded_points / graded_credits if graded_credits > 0 else None

    if not pending_courses:
        return GpaTargetResult(
            status="no_pending", target=target, current_gpa4=current_gpa,
            graded_credits=graded_credits,
            message="Tất cả học phần trong kỳ đã có điểm.",
        )

    total_credits = graded_credits + pending_credits
    required_points = target * total_credits - graded_points
    required_avg = required_points / pending_credits
    max_possible = (graded_points + 4.0 * pending_credits) / total_credits

    base = dict(
        target=target, current_gpa4=current_gpa, graded_credits=graded_credits,
        pending_credits=pending_credits, required_avg4=required_avg,
        max_possible_gpa4=max_possible,
    )

    if required_avg > 4.0 + _EPS:
        return GpaTargetResult(
            status="impossible", **base,
            message=f"Không thể đạt {target:.2f}: dù đạt A (4.0) hết các môn còn lại, GPA tối đa chỉ là {max_possible:.2f}.",
        )

    # Mức điểm đồng đều tối thiểu (ít nhất là D để qua môn)
    need_point = max(required_avg, PASS_POINT)
    min10, letter, point = point_to_scale_row(need_point)
    needs = [PendingCourseNeed(c, letter, min10, point) for c in pending_courses]
    status = "achieved" if required_avg <= PASS_POINT + _EPS else "needed"

    return GpaTargetResult(
        status=status, **base,
        uniform_letter=letter, uniform_min_score10=min10, pending=needs,
        message=(
            "Chỉ cần qua các môn còn lại là đã đạt mục tiêu."
            if status == "achieved" else ""
        ),
    )


# ---------------------------------------------------------------------------
# GPA ra trường kỳ vọng
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CreditProgress:
    """Tiến độ tín chỉ từ API hoctap/tiendo."""

    required_credits: float  # canDat: tổng tín chỉ cần để ra trường
    earned_credits: float    # hienTai: tín chỉ đã tích lũy


def parse_credit_progress(body: object) -> CreditProgress | None:
    """Phân tích ``body`` của API tiendo; None nếu dữ liệu không hợp lệ."""
    if not isinstance(body, dict):
        return None
    try:
        required = float(body.get("canDat"))
        earned = float(body.get("hienTai"))
    except (TypeError, ValueError):
        return None
    if required <= 0 or earned < 0:
        return None
    return CreditProgress(required_credits=required, earned_credits=earned)


def cumulative_gpa4(semesters: list[SemesterGradeData]) -> tuple[float | None, str]:
    """GPA tích lũy hệ 4 hiện tại và nguồn: ``portal`` | ``computed`` | ``none``.

    Ưu tiên giá trị Portal đã tổng hợp (``diemTBTinChiTichLuy``, học kỳ mới nhất
    có giá trị). Nếu thiếu thì tự tính từ các môn đã có điểm (môn học lại chỉ
    tính lần có điểm hệ 4 cao nhất).
    """
    for sem in semesters:
        val = sem.summary.get("diemTBTinChiTichLuy") if sem.summary else None
        if val is not None:
            return float(val), "portal"

    best_by_code: dict[str, tuple[float, float]] = {}
    anonymous: list[tuple[float, float]] = []
    for sem in semesters:
        for c in sem.courses:
            if not _is_gpa_course(c):
                continue
            p = _course_point4(c)
            if p is None:
                continue
            code = (c.subject_code or c.course_code or "").strip().upper()
            if not code:
                anonymous.append((c.credits, p))
                continue
            existing = best_by_code.get(code)
            if existing is None or p > existing[1]:
                best_by_code[code] = (c.credits, p)

    entries = [*best_by_code.values(), *anonymous]
    credits = sum(cred for cred, _ in entries)
    points = sum(cred * pt for cred, pt in entries)
    if credits > 0:
        return points / credits, "computed"
    return None, "none"


@dataclass(frozen=True)
class GraduationGpaResult:
    status: str  # invalid | no_progress | no_gpa | completed | impossible | achieved | needed
    target: float
    required_credits: float = 0.0
    earned_credits: float = 0.0
    remaining_credits: float = 0.0
    current_gpa4: float | None = None
    gpa_source: str = "none"
    required_avg4: float | None = None
    max_possible_gpa4: float | None = None
    letter: str = ""
    min_score10: float | None = None
    message: str = ""


def calculate_graduation_target(
    semesters: list[SemesterGradeData],
    progress: CreditProgress | None,
    target: float | None,
) -> GraduationGpaResult:
    """Điểm hệ 4 tối thiểu cần đạt ở số tín chỉ còn lại để GPA ra trường = ``target``."""
    if target is None or not (0.0 <= target <= 4.0):
        return GraduationGpaResult(
            status="invalid", target=target or 0.0,
            message="Nhập GPA ra trường mục tiêu trong khoảng 0.0 – 4.0.",
        )
    if progress is None:
        return GraduationGpaResult(
            status="no_progress", target=target,
            message="Chưa lấy được số tín chỉ ra trường từ Portal. Hãy làm mới bảng điểm.",
        )

    gpa, source = cumulative_gpa4(semesters)
    required, earned = progress.required_credits, progress.earned_credits
    remaining = required - earned
    base = dict(
        target=target, required_credits=required, earned_credits=earned,
        remaining_credits=max(remaining, 0.0), current_gpa4=gpa, gpa_source=source,
    )
    if gpa is None:
        return GraduationGpaResult(
            status="no_gpa", **base,
            message="Chưa có điểm để tính GPA tích lũy hiện tại.",
        )

    if remaining <= _EPS:
        return GraduationGpaResult(
            status="completed", **base,
            message=f"Đã đủ tín chỉ ra trường. GPA tích lũy hiện tại: {gpa:.2f}.",
        )

    required_avg = (target * required - gpa * earned) / remaining
    max_possible = (gpa * earned + 4.0 * remaining) / required
    base.update(required_avg4=required_avg, max_possible_gpa4=max_possible)

    if required_avg > 4.0 + _EPS:
        return GraduationGpaResult(
            status="impossible", **base,
            message=(
                f"Không thể đạt {target:.2f}: dù đạt A (4.0) hết {remaining:.0f} TC còn lại, "
                f"GPA ra trường tối đa chỉ là {max_possible:.2f}."
            ),
        )

    need_point = max(required_avg, PASS_POINT)
    min10, letter, _pt = point_to_scale_row(need_point)
    status = "achieved" if required_avg <= PASS_POINT + _EPS else "needed"
    return GraduationGpaResult(
        status=status, **base, letter=letter, min_score10=min10,
        message=(
            "Chỉ cần qua hết các tín chỉ còn lại là đạt mục tiêu."
            if status == "achieved" else ""
        ),
    )
