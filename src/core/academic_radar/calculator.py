from __future__ import annotations

from typing import Any, Literal
from core.academic_radar.config import RADAR_CONFIG, RadarConfig
from core.academic_radar.color_scale import score_to_color, score_to_status
from core.academic_radar.models import (
    ConfidenceLevel,
    CourseGradeItem,
    RadarMetric,
    RadarProfile,
    SemesterGradeData,
)

METRIC_KEYS_ORDER = [
    "total_power",   # 12h: Tổng Lực
    "foundation",    # 2h:  Nền Tảng
    "breakthrough",  # 4h:  Bứt Phá
    "rhythm",        # 6h:  Giữ Nhịp
    "finish",        # 8h:  Về Đích
    "resilience",    # 10h: Trụ Vững
]

METRIC_LABELS = {
    "total_power": "Tổng Lực",
    "foundation": "Nền Tảng",
    "breakthrough": "Bứt Phá",
    "rhythm": "Giữ Nhịp",
    "finish": "Về Đích",
    "resilience": "Trụ Vững",
}

METRIC_DESCRIPTIONS = {
    "total_power": "Kết quả học tập tổng thể trong học kỳ.",
    "foundation": "Mức điểm bạn xây dựng trong quá trình học.",
    "breakthrough": "Mức điểm của nhóm 1/3 tín chỉ học phần có kết quả tốt nhất.",
    "rhythm": "Mức độ duy trì phong độ từ điểm quá trình sang điểm thi cuối kỳ.",
    "finish": "Kết quả ở phần đánh giá và thi kết thúc học phần.",
    "resilience": "Mặt bằng điểm số của nhóm 1/3 tín chỉ học phần thấp nhất.",
}

METRIC_CALCULATION_TEXTS = {
    "total_power": "Tổng Lực được lấy trực tiếp từ điểm trung bình học lực học kỳ (GPA hệ 10 quy đổi thang 100).",
    "foundation": "Nền Tảng được tính bằng trung bình có trọng số theo tín chỉ của điểm quá trình (thường kỳ).",
    "breakthrough": "Bứt Phá được tính từ 1/3 số tín chỉ có điểm tổng kết cao nhất, có trọng số theo số tín chỉ từng học phần.",
    "rhythm": "Giữ Nhịp đo mức hụt điểm trung bình từ quá trình sang thi cuối kỳ (100 - AvgDrop × 15). Điểm thi cao hơn quá trình không bị trừ điểm.",
    "finish": "Về Đích được tính bằng trung bình có trọng số theo tín chỉ của điểm thi kết thúc học phần.",
    "resilience": "Trụ Vững được tính từ 1/3 số tín chỉ có điểm tổng kết thấp nhất, có trọng số theo số tín chỉ từng học phần.",
}


def weighted_mean(grades_and_credits: list[tuple[float, float]]) -> float | None:
    """Calculates credit-weighted mean. Returns None if total credits is 0."""
    valid = [(g, c) for g, c in grades_and_credits if g is not None and c > 0]
    if not valid:
        return None
    total_credits = sum(c for _, c in valid)
    if total_credits <= 0:
        return None
    return sum(g * c for g, c in valid) / total_credits


def weighted_tail_mean(
    courses: list[CourseGradeItem],
    direction: Literal["highest", "lowest"] = "highest",
    fraction: float = 1.0 / 3.0,
) -> tuple[float | None, list[dict[str, Any]]]:
    """Calculates weighted mean of a fractional tail (e.g. top 1/3 or bottom 1/3 credits).

    Handles boundary courses with precise fractional credits to avoid abrupt jumps.
    Returns (mean_score, contributing_courses).
    """
    eligible = [
        c for c in courses
        if c.final_grade is not None and c.credits > 0 and c.eligibility.status in ("academic", "unknown")
    ]
    if not eligible:
        return None, []

    total_credits = sum(c.credits for c in eligible)
    target_credits = total_credits * fraction
    if target_credits <= 0:
        return None, []

    # Sort descending for highest, ascending for lowest
    sorted_courses = sorted(
        eligible,
        key=lambda x: (x.final_grade or 0.0),
        reverse=(direction == "highest"),
    )

    accumulated_credits = 0.0
    weighted_sum = 0.0
    contributors: list[dict[str, Any]] = []

    for c in sorted_courses:
        remaining = target_credits - accumulated_credits
        if remaining <= 0.0001:
            break
        credits_to_take = min(c.credits, remaining)
        weighted_sum += (c.final_grade or 0.0) * credits_to_take
        accumulated_credits += credits_to_take
        contributors.append({
            "course_name": c.course_name,
            "course_code": c.course_code,
            "grade": c.final_grade,
            "credits_used": credits_to_take,
            "total_credits": c.credits,
        })

    if accumulated_credits <= 0:
        return None, []

    return weighted_sum / accumulated_credits, contributors


def _resolve_confidence(coverage: float, config: RadarConfig) -> ConfidenceLevel:
    if coverage >= config.complete_coverage:
        return "complete"
    if coverage >= config.minimum_coverage:
        return "partial"
    return "insufficient"


def calculate_total_power(summary: dict[str, Any], courses: list[CourseGradeItem]) -> tuple[float | None, float]:
    """Calculates Total Power (0..100) from summary GPA 10. Returns (score, coverage)."""
    raw_gpa = summary.get("diemTBHocLuc")
    if raw_gpa is not None:
        try:
            score = float(raw_gpa) * 10.0
            return max(0.0, min(100.0, score)), 1.0
        except (ValueError, TypeError):
            pass

    # Fallback to credit-weighted final grades if summary GPA is absent
    academic_courses = [c for c in courses if c.eligibility.status in ("academic", "unknown") and c.credits > 0]
    if not academic_courses:
        return None, 0.0

    pairs = [(c.final_grade, c.credits) for c in academic_courses if c.final_grade is not None]
    mean = weighted_mean(pairs)
    if mean is None:
        return None, 0.0

    total_credits = sum(c.credits for c in academic_courses)
    covered_credits = sum(c for _, c in pairs)
    coverage = covered_credits / total_credits if total_credits > 0 else 0.0
    return max(0.0, min(100.0, mean * 10.0)), coverage


def calculate_foundation(courses: list[CourseGradeItem]) -> tuple[float | None, float, list[dict[str, Any]]]:
    """Calculates Foundation score (WeightedMean of process grades). Returns (score, coverage, contributors)."""
    academic_courses = [
        c for c in courses
        if c.eligibility.status in ("academic", "unknown") and c.credits > 0 and not c.is_transferred
    ]
    if not academic_courses:
        return None, 0.0, []

    pairs = [(c.process_grade, c.credits) for c in academic_courses if c.process_grade is not None]
    mean = weighted_mean(pairs)
    total_credits = sum(c.credits for c in academic_courses)
    covered_credits = sum(c for _, c in pairs)
    coverage = covered_credits / total_credits if total_credits > 0 else 0.0

    if mean is None:
        return None, 0.0, []

    contributors = [
        {"course_name": c.course_name, "grade": c.process_grade, "credits": c.credits}
        for c in academic_courses if c.process_grade is not None
    ]
    return max(0.0, min(100.0, mean * 10.0)), coverage, contributors


def calculate_finish(courses: list[CourseGradeItem]) -> tuple[float | None, float, list[dict[str, Any]]]:
    """Calculates Finish score (WeightedMean of exam grades). Returns (score, coverage, contributors)."""
    academic_courses = [
        c for c in courses
        if c.eligibility.status in ("academic", "unknown") and c.credits > 0 and not c.is_transferred
    ]
    if not academic_courses:
        return None, 0.0, []

    pairs = [(c.exam_grade, c.credits) for c in academic_courses if c.exam_grade is not None]
    mean = weighted_mean(pairs)
    total_credits = sum(c.credits for c in academic_courses)
    covered_credits = sum(c for _, c in pairs)
    coverage = covered_credits / total_credits if total_credits > 0 else 0.0

    if mean is None:
        return None, 0.0, []

    contributors = [
        {"course_name": c.course_name, "grade": c.exam_grade, "credits": c.credits}
        for c in academic_courses if c.exam_grade is not None
    ]
    return max(0.0, min(100.0, mean * 10.0)), coverage, contributors


def calculate_breakthrough(
    courses: list[CourseGradeItem], config: RadarConfig
) -> tuple[float | None, float, list[dict[str, Any]]]:
    """Calculates Breakthrough score (Upper 1/3 tail mean * 10). Returns (score, coverage, contributors)."""
    academic_courses = [
        c for c in courses
        if c.eligibility.status in ("academic", "unknown") and c.credits > 0 and not c.is_transferred
    ]
    if not academic_courses:
        return None, 0.0, []

    total_credits = sum(c.credits for c in academic_courses)
    covered_credits = sum(c.credits for c in academic_courses if c.final_grade is not None)
    coverage = covered_credits / total_credits if total_credits > 0 else 0.0

    tail_mean, contributors = weighted_tail_mean(
        academic_courses, direction="highest", fraction=config.tail_fraction
    )
    if tail_mean is None:
        return None, 0.0, []

    return max(0.0, min(100.0, tail_mean * 10.0)), coverage, contributors


def calculate_rhythm(
    courses: list[CourseGradeItem], config: RadarConfig
) -> tuple[float | None, float, list[dict[str, Any]]]:
    """Calculates Rhythm score based on drop from process to exam grade. Returns (score, coverage, contributors)."""
    academic_courses = [
        c for c in courses
        if c.eligibility.status in ("academic", "unknown") and c.credits > 0 and not c.is_transferred
    ]
    if not academic_courses:
        return None, 0.0, []

    courses_with_both = [
        c for c in academic_courses
        if c.process_grade is not None and c.exam_grade is not None
    ]
    total_credits = sum(c.credits for c in academic_courses)
    covered_credits = sum(c.credits for c in courses_with_both)
    coverage = covered_credits / total_credits if total_credits > 0 else 0.0

    if not courses_with_both:
        return None, 0.0, []

    drops = []
    contributors = []
    for c in courses_with_both:
        diff = (c.process_grade or 0.0) - (c.exam_grade or 0.0)
        drop = max(0.0, diff)
        drops.append((drop, c.credits))
        if drop > 0.0:
            contributors.append({
                "course_name": c.course_name,
                "process_grade": c.process_grade,
                "exam_grade": c.exam_grade,
                "drop": drop,
                "credits": c.credits,
            })

    contributors.sort(key=lambda x: x["drop"], reverse=True)
    avg_drop = weighted_mean(drops)
    if avg_drop is None:
        avg_drop = 0.0

    raw_score = 100.0 - (avg_drop * config.rhythm_penalty)
    clamped_score = max(0.0, min(100.0, raw_score))
    return clamped_score, coverage, contributors


def calculate_resilience(
    courses: list[CourseGradeItem], config: RadarConfig
) -> tuple[float | None, float, list[dict[str, Any]]]:
    """Calculates Resilience score (Lower 1/3 tail mean * 10). Returns (score, coverage, contributors)."""
    academic_courses = [
        c for c in courses
        if c.eligibility.status in ("academic", "unknown") and c.credits > 0 and not c.is_transferred
    ]
    if not academic_courses:
        return None, 0.0, []

    total_credits = sum(c.credits for c in academic_courses)
    covered_credits = sum(c.credits for c in academic_courses if c.final_grade is not None)
    coverage = covered_credits / total_credits if total_credits > 0 else 0.0

    tail_mean, contributors = weighted_tail_mean(
        academic_courses, direction="lowest", fraction=config.tail_fraction
    )
    if tail_mean is None:
        return None, 0.0, []

    return max(0.0, min(100.0, tail_mean * 10.0)), coverage, contributors


def _build_deterministic_insight(metrics: dict[str, RadarMetric]) -> str:
    """Generates an honest, deterministic summary insight based on score relationships."""
    foundation = metrics.get("foundation")
    finish = metrics.get("finish")
    breakthrough = metrics.get("breakthrough")
    resilience = metrics.get("resilience")

    f_score = foundation.score if foundation and foundation.score is not None else None
    e_score = finish.score if finish and finish.score is not None else None
    b_score = breakthrough.score if breakthrough and breakthrough.score is not None else None
    r_score = resilience.score if resilience and resilience.score is not None else None

    # Rule 1: Accelerating at the finish
    if f_score is not None and e_score is not None and (e_score - f_score) >= 5.0:
        return "Bạn tăng tốc tốt ở phần đánh giá cuối kỳ so với mặt bằng quá trình."

    # Rule 2: Dropping at the finish
    if f_score is not None and e_score is not None and (f_score - e_score) >= 5.0:
        return "Kết quả cuối kỳ thấp hơn mặt bằng quá trình, cần chú ý ôn tập kỳ thi."

    # Rule 3: High dispersion between top and bottom
    if b_score is not None and r_score is not None and (b_score - r_score) >= 15.0:
        return "Kết quả đang phân hóa khá rõ giữa nhóm học phần thế mạnh và nhóm học phần cần chú ý."

    # Rule 4: Very solid baseline across all courses
    if r_score is not None and r_score >= 85.0:
        return "Mặt bằng điểm số của tất cả các học phần rất vững chắc và đồng đều."

    if b_score is not None and b_score >= 85.0:
        return "Bạn có những học phần thế mạnh bứt phá xuất sắc trong học kỳ."

    return "Hồ sơ học tập thể hiện đầy đủ phong độ qua từng giai đoạn học phần."


def _round_half_up(val: float | None) -> int | None:
    if val is None:
        return None
    import math
    return int(math.floor(val + 0.5))


def calculate_radar_profile(
    semester: SemesterGradeData,
    previous_semester: SemesterGradeData | None = None,
    config: RadarConfig = RADAR_CONFIG,
) -> RadarProfile:
    """Computes the full 6-axis Academic Radar profile for a given semester."""
    summary = semester.summary
    courses = semester.courses

    # 1. Total Power
    tp_score, tp_cov = calculate_total_power(summary, courses)
    tp_raw = (tp_score / 10.0) if tp_score is not None else None

    # 2. Foundation
    f_score, f_cov, f_contrib = calculate_foundation(courses)
    f_raw = (f_score / 10.0) if f_score is not None else None

    # 3. Breakthrough
    b_score, b_cov, b_contrib = calculate_breakthrough(courses, config)
    b_raw = (b_score / 10.0) if b_score is not None else None

    # 4. Rhythm
    r_score, r_cov, r_contrib = calculate_rhythm(courses, config)
    r_raw = r_score

    # 5. Finish
    fin_score, fin_cov, fin_contrib = calculate_finish(courses)
    fin_raw = (fin_score / 10.0) if fin_score is not None else None

    # 6. Resilience
    res_score, res_cov, res_contrib = calculate_resilience(courses, config)
    res_raw = (res_score / 10.0) if res_score is not None else None

    raw_scores = {
        "total_power": (tp_score, tp_raw, "/10", tp_cov, [], []),
        "foundation": (f_score, f_raw, "/10", f_cov, f_contrib, []),
        "breakthrough": (b_score, b_raw, "/10", b_cov, b_contrib, []),
        "rhythm": (r_score, r_raw, "/100", r_cov, [], r_contrib),
        "finish": (fin_score, fin_raw, "/10", fin_cov, fin_contrib, []),
        "resilience": (res_score, res_raw, "/10", res_cov, [], res_contrib),
    }

    # If previous semester is provided, calculate its profile for delta
    prev_metrics: dict[str, RadarMetric] = {}
    if previous_semester is not None:
        prev_profile = calculate_radar_profile(previous_semester, None, config)
        prev_metrics = prev_profile.metrics

    metrics: dict[str, RadarMetric] = {}
    ordered_metrics: list[RadarMetric] = []

    for key in METRIC_KEYS_ORDER:
        score, raw_val, raw_unit, cov, pos_c, neg_c = raw_scores[key]
        conf = _resolve_confidence(cov, config)
        color = score_to_color(score)
        status = score_to_status(score)
        display_score = _round_half_up(score)

        delta = None
        if key in prev_metrics and prev_metrics[key].score is not None and score is not None:
            delta = round(score - prev_metrics[key].score, 1)

        metric = RadarMetric(
            key=key,
            label=METRIC_LABELS[key],
            score=score,
            display_score=display_score,
            raw_value=raw_val,
            raw_unit=raw_unit,
            coverage=cov,
            confidence=conf,
            status=status,
            color=color,
            description=METRIC_DESCRIPTIONS[key],
            calculation_text=METRIC_CALCULATION_TEXTS[key],
            positive_courses=pos_c,
            negative_courses=neg_c,
            delta_vs_previous=delta,
        )
        metrics[key] = metric
        ordered_metrics.append(metric)

    # Determine strongest and weakest metrics
    valid_metrics = [m for m in ordered_metrics if m.score is not None and m.confidence != "insufficient"]
    strongest: list[RadarMetric] = []
    weakest: list[RadarMetric] = []

    if valid_metrics:
        max_score = max(m.score for m in valid_metrics) # type: ignore
        strongest = [m for m in valid_metrics if m.score is not None and abs(m.score - max_score) <= 1.0][:2]

        min_score = min(m.score for m in valid_metrics) # type: ignore
        weakest = [m for m in valid_metrics if m.score is not None and abs(m.score - min_score) <= 1.0][:2]

    # Overall coverage
    cov_values = [m.coverage for m in ordered_metrics]
    overall_coverage = sum(cov_values) / len(cov_values) if cov_values else 0.0

    valid_count = len(valid_metrics)
    is_polygon_viable = valid_count >= config.minimum_metrics_for_polygon

    # Summary GPA fields
    gpa_10 = summary.get("diemTBHocLuc")
    gpa_4 = summary.get("diemTBTinChi")
    credits_earned = summary.get("stcdatHocKy") or summary.get("soTCDat")
    classification = summary.get("xepLoaiHocLuc") or summary.get("xepLoai")
    training_point = summary.get("soDiemRenLuyen") or summary.get("diemRenLuyen")

    insight = _build_deterministic_insight(metrics)

    return RadarProfile(
        semester_id=semester.id,
        semester_name=semester.name,
        metrics=metrics,
        ordered_metrics=ordered_metrics,
        strongest_metrics=strongest,
        weakest_metrics=weakest,
        overall_coverage=overall_coverage,
        is_polygon_viable=is_polygon_viable,
        gpa_10=float(gpa_10) if gpa_10 is not None else None,
        gpa_4=float(gpa_4) if gpa_4 is not None else None,
        credits_earned=float(credits_earned) if credits_earned is not None else None,
        classification=str(classification) if classification else None,
        training_point=float(training_point) if training_point is not None else None,
        insight_text=insight,
        scoring_version=config.scoring_version,
    )
