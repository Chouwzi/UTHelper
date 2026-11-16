from __future__ import annotations

import pytest
from core.academic_radar import (
    RADAR_CONFIG,
    CourseEligibility,
    CourseGradeItem,
    SemesterGradeData,
    SemesterNormalizer,
    calculate_radar_profile,
)
from core.academic_radar.calculator import (
    calculate_breakthrough,
    calculate_finish,
    calculate_foundation,
    calculate_resilience,
    calculate_rhythm,
    calculate_total_power,
    weighted_mean,
    weighted_tail_mean,
)
from core.academic_radar.color_scale import score_to_color, score_to_status


def _make_hk2_fixture() -> SemesterGradeData:
    """Official test fixture for HK2 2025–2026 specified in the requirements."""
    raw = {
        "id": 75,
        "tenDot": "Học kỳ 2 năm học 2025-2026",
        "tongKetDot": {
            "diemTBHocLuc": 8.77,
            "diemTBTinChi": 3.75,
            "stcdatHocKy": 18,
            "soDiemRenLuyen": 83,
            "xepLoaiHocLuc": "Xuất sắc",
        },
        "diems": [
            {
                "maLopHocPhan": "0120122001",
                "tenMonHoc": "Kiến trúc máy tính",
                "soTinChi": 3,
                "diemTBThuongKy": 8.9,
                "diemThi": 9.8,
                "diemTongKet": 9.5,
                "diemChu": "A+",
            },
            {
                "maLopHocPhan": "0120122002",
                "tenMonHoc": "Lập trình Java",
                "soTinChi": 3,
                "diemTBThuongKy": 7.7,
                "diemThi": 7.0,
                "diemTongKet": 7.4,
                "diemChu": "B",
            },
            {
                "maLopHocPhan": "0120122003",
                "tenMonHoc": "Lập trình Web",
                "soTinChi": 3,
                "diemTBThuongKy": 7.9,
                "diemThi": 8.5,
                "diemTongKet": 8.2,
                "diemChu": "B+",
            },
            {
                "maLopHocPhan": "0120122004",
                "tenMonHoc": "An toàn thông tin",
                "soTinChi": 3,
                "diemTBThuongKy": 9.0,
                "diemThi": 9.5,
                "diemTongKet": 9.3,
                "diemChu": "A",
            },
            {
                "maLopHocPhan": "0120122005",
                "tenMonHoc": "Lập trình mạng",
                "soTinChi": 3,
                "diemTBThuongKy": 9.8,
                "diemThi": 9.1,
                "diemTongKet": 9.3,
                "diemChu": "A",
            },
            {
                "maLopHocPhan": "0120122006",
                "tenMonHoc": "Cơ sở dữ liệu phân tán",
                "soTinChi": 3,
                "diemTBThuongKy": 8.5,
                "diemThi": 9.8,
                "diemTongKet": 9.3,
                "diemChu": "A",
            },
            {
                # Zero-credit conditional course that must be excluded from calculations
                "maLopHocPhan": "TT04",
                "tenMonHoc": "General English 4",
                "soTinChi": 0,
                "diemTBThuongKy": 9.0,
                "diemThi": 9.0,
                "diemTongKet": 9.0,
            },
        ],
    }
    return SemesterNormalizer().normalize_semester(raw)


def test_hk2_official_fixture_scores_and_labels():
    """Verify official HK2 2025–2026 fixture produces expected exact scores."""
    semester = _make_hk2_fixture()
    profile = calculate_radar_profile(semester)

    assert profile.semester_name == "Học kỳ 2 năm học 2025-2026"
    assert profile.scoring_version == 1

    # Exact expected scores:
    # Tổng Lực: 87.7 -> UI 88
    # Nền Tảng: 86.333 -> UI 86
    # Bứt Phá: 94.0 -> UI 94
    # Giữ Nhịp: 96.5 -> UI 97
    # Về Đích: 89.5 -> UI 90
    # Trụ Vững: 78.0 -> UI 78

    m = profile.metrics
    assert pytest.approx(m["total_power"].score, 0.1) == 87.7
    assert m["total_power"].display_score == 88

    assert pytest.approx(m["foundation"].score, 0.1) == 86.3
    assert m["foundation"].display_score == 86

    assert pytest.approx(m["breakthrough"].score, 0.1) == 94.0
    assert m["breakthrough"].display_score == 94

    assert pytest.approx(m["rhythm"].score, 0.1) == 96.5
    assert m["rhythm"].display_score == 97

    assert pytest.approx(m["finish"].score, 0.1) == 89.5
    assert m["finish"].display_score == 90

    assert pytest.approx(m["resilience"].score, 0.1) == 78.0
    assert m["resilience"].display_score == 78

    # Fixed Axis Order
    expected_order = ["total_power", "foundation", "breakthrough", "rhythm", "finish", "resilience"]
    assert [metric.key for metric in profile.ordered_metrics] == expected_order

    # Summary strongest / weakest
    strongest_keys = [s.key for s in profile.strongest_metrics]
    assert "rhythm" in strongest_keys or "breakthrough" in strongest_keys

    weakest_keys = [w.key for w in profile.weakest_metrics]
    assert "resilience" in weakest_keys

    # KPIs
    assert profile.gpa_10 == 8.77
    assert profile.gpa_4 == 3.75
    assert profile.credits_earned == 18.0
    assert profile.training_point == 83.0
    assert profile.classification == "Xuất sắc"
    assert profile.is_polygon_viable is True
    assert profile.overall_coverage == 1.0


def test_null_grades_are_not_converted_to_zero():
    """Courses with missing exam or process grades are safely excluded from individual metrics."""
    courses = [
        CourseGradeItem(
            course_id="1",
            course_code="CS1",
            course_name="Course 1",
            credits=3,
            process_grade=8.0,
            exam_grade=None,     # missing exam
            final_grade=None,    # missing final
        ),
        CourseGradeItem(
            course_id="2",
            course_code="CS2",
            course_name="Course 2",
            credits=3,
            process_grade=9.0,
            exam_grade=8.0,
            final_grade=8.5,
        ),
    ]
    sem = SemesterGradeData(id=1, name="Sem 1", summary={}, courses=courses)
    profile = calculate_radar_profile(sem)

    # Foundation should include both: (8*3 + 9*3)/6 = 8.5 -> score 85
    assert profile.metrics["foundation"].score == 85.0
    assert profile.metrics["foundation"].coverage == 1.0

    # Finish should only include Course 2: 8.0 -> score 80
    assert profile.metrics["finish"].score == 80.0
    assert profile.metrics["finish"].coverage == 0.5
    assert profile.metrics["finish"].confidence == "partial"

    # Rhythm should only evaluate Course 2: drop = 9.0 - 8.0 = 1.0 -> score 100 - 15 = 85
    assert profile.metrics["rhythm"].score == 85.0


def test_zero_credit_and_conditional_courses_do_not_skew_mean():
    """Zero credit courses or transferred courses are ignored by weighted mean."""
    courses = [
        CourseGradeItem(
            course_id="1",
            course_code="CS1",
            course_name="Real Course",
            credits=3,
            process_grade=8.0,
            exam_grade=8.0,
            final_grade=8.0,
        ),
        CourseGradeItem(
            course_id="2",
            course_code="TT04",
            course_name="English 0 TC",
            credits=0,
            process_grade=2.0,
            exam_grade=2.0,
            final_grade=2.0,
            eligibility=CourseEligibility(status="conditional"),
        ),
    ]
    sem = SemesterGradeData(id=1, name="Sem 1", summary={}, courses=courses)
    profile = calculate_radar_profile(sem)

    assert profile.metrics["foundation"].score == 80.0
    assert profile.metrics["finish"].score == 80.0
    assert profile.metrics["breakthrough"].score == 80.0
    assert profile.metrics["resilience"].score == 80.0


def test_fractional_credit_tail_calculation():
    """Fractional tail test: 14 credits total -> top 1/3 = 4.6667 credits."""
    courses = [
        CourseGradeItem(course_id="A", course_code="A", course_name="A", credits=3, final_grade=10.0),
        CourseGradeItem(course_id="B", course_code="B", course_name="B", credits=3, final_grade=9.0),
        CourseGradeItem(course_id="C", course_code="C", course_name="C", credits=4, final_grade=8.0),
        CourseGradeItem(course_id="D", course_code="D", course_name="D", credits=4, final_grade=7.0),
    ]
    # Total = 14 credits. Top 1/3 target = 4.66667 credits.
    # Take 3 credits of A (10.0) + 1.66667 credits of B (9.0).
    # Weighted sum = 3*10.0 + 1.66667*9.0 = 30 + 15 = 45.
    # Tail mean = 45 / 4.66667 = 9.642857...
    score, contribs = weighted_tail_mean(courses, direction="highest", fraction=1.0 / 3.0)
    assert score is not None
    assert pytest.approx(score, 0.001) == (3.0 * 10.0 + (14.0 / 3.0 - 3.0) * 9.0) / (14.0 / 3.0)
    assert len(contribs) == 2
    assert contribs[0]["credits_used"] == 3.0
    assert pytest.approx(contribs[1]["credits_used"], 0.001) == 1.66667


def test_single_course_semester():
    """A semester with only 1 course gives Breakthrough == Resilience == course grade."""
    courses = [
        CourseGradeItem(
            course_id="1",
            course_code="CS1",
            course_name="Only Course",
            credits=3,
            process_grade=8.5,
            exam_grade=8.5,
            final_grade=8.5,
        ),
    ]
    sem = SemesterGradeData(id=1, name="Summer Sem", summary={"diemTBHocLuc": 8.5}, courses=courses)
    profile = calculate_radar_profile(sem)

    assert profile.metrics["breakthrough"].score == 85.0
    assert profile.metrics["resilience"].score == 85.0
    assert profile.metrics["total_power"].score == 85.0
    assert profile.metrics["foundation"].score == 85.0
    assert profile.metrics["finish"].score == 85.0
    assert profile.metrics["rhythm"].score == 100.0


def test_color_scale_interpolation_and_status():
    """Verify continuous color interpolation and discrete status wording."""
    assert score_to_status(35.0) == "Nguy Hiểm"
    assert score_to_status(45.0) == "Cầm Cự"
    assert score_to_status(60.0) == "Tạm Ổn"
    assert score_to_status(78.0) == "Vững Vàng"
    assert score_to_status(95.0) == "Nổi Bật"

    # Color stops
    assert score_to_color(0.0) == "#EF4444"
    assert score_to_color(100.0) == "#10B981"
    assert score_to_color(40.0) == "#F97316"
    assert score_to_color(85.0) == "#22C55E"

    # Midpoints interpolate smoothly
    c_mid = score_to_color(78.0)
    assert c_mid.startswith("#")
    assert len(c_mid) == 7

    # Clamping
    assert score_to_color(-10.0) == "#EF4444"
    assert score_to_color(150.0) == "#10B981"


def test_comparison_delta_between_semesters():
    """Verify delta calculation between current and previous semester."""
    current = SemesterGradeData(
        id=2,
        name="HK2",
        summary={"diemTBHocLuc": 9.4},
        courses=[
            CourseGradeItem("1", "C1", "C1", 3, 9.4, 9.4, 9.4),
        ],
    )
    previous = SemesterGradeData(
        id=1,
        name="HK1",
        summary={"diemTBHocLuc": 8.8},
        courses=[
            CourseGradeItem("1", "C1", "C1", 3, 8.8, 8.8, 8.8),
        ],
    )

    profile = calculate_radar_profile(current, previous_semester=previous)
    assert profile.metrics["total_power"].score == 94.0
    assert profile.metrics["total_power"].delta_vs_previous == 6.0


def test_polygon_viability_threshold():
    """Polygon requires at least 4 valid metrics."""
    # Only 2 metrics available
    courses = [
        CourseGradeItem("1", "C1", "C1", 3, process_grade=8.0, exam_grade=None, final_grade=None),
    ]
    sem = SemesterGradeData(id=1, name="In-progress", summary={}, courses=courses)
    profile = calculate_radar_profile(sem)

    assert profile.is_polygon_viable is False
