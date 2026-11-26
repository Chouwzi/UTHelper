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
    assert profile.credits_registered is not None
    assert profile.credits_registered >= profile.credits_earned
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


def test_current_semester_gpa_fallback_when_summary_empty_without_credit_grade():
    """F10: empty summary computes weighted gpa_10, gpa_4 (via letter/final), and credits_earned."""
    courses = [
        CourseGradeItem(
            course_id="1",
            course_code="CS101",
            course_name="DSA",
            credits=4,
            final_grade=8.5,
            credit_grade=None,
            letter_grade="A",
            is_passed=True,
        ),
        CourseGradeItem(
            course_id="2",
            course_code="CS102",
            course_name="OS",
            credits=2,
            final_grade=7.0,
            credit_grade=None,
            letter_grade="",
            is_passed=True,
        ),
    ]
    sem = SemesterGradeData(id=99, name="HK1 2026-2027", summary={}, courses=courses)
    profile = calculate_radar_profile(sem)

    # (8.5*4 + 7.0*2) / 6 = 48.0 / 6 = 8.0
    assert profile.gpa_10 == pytest.approx(8.0)
    # (4.0*4 + 3.0*2) / 6 = 22.0 / 6 = 3.6666...
    assert profile.gpa_4 == pytest.approx(22.0 / 6.0)
    assert profile.credits_earned == pytest.approx(6.0)
    assert profile.credits_registered == pytest.approx(6.0)


def test_current_semester_gpa_fallback_is_per_field_and_preserves_zero_summary_credits():
    """F10: authoritative summary fields (including explicit 0) are preserved while None fields fall back."""
    courses = [
        CourseGradeItem(
            course_id="1",
            course_code="CS101",
            course_name="DSA",
            credits=3,
            final_grade=9.0,
            credit_grade=4.0,
            letter_grade="A+",
            is_passed=True,
        ),
    ]
    sem = SemesterGradeData(
        id=100,
        name="HK1",
        summary={
            "diemTBHocLuc": 8.2,
            "diemTBTinChi": None,
            "stcdatHocKy": 0,
            "soTCDat": 12,
        },
        courses=courses,
    )
    profile = calculate_radar_profile(sem)

    assert profile.gpa_10 == pytest.approx(8.2)
    assert profile.gpa_4 == pytest.approx(4.0)
    assert profile.credits_earned == pytest.approx(0.0)


def test_current_semester_gpa_fallback_excludes_failed_credits_and_handles_all_failed():
    """F10: failed courses affect gpa_10/gpa_4 but contribute 0.0 to credits_earned."""
    failed_only = SemesterGradeData(
        id=101,
        name="HK1",
        summary={},
        courses=[
            CourseGradeItem(
                course_id="1",
                course_code="CS1",
                course_name="Math",
                credits=3,
                final_grade=3.0,
                credit_grade=0.0,
                letter_grade="F",
                is_passed=False,
            ),
        ],
    )
    failed_profile = calculate_radar_profile(failed_only)
    assert failed_profile.gpa_10 == pytest.approx(3.0)
    assert failed_profile.gpa_4 == pytest.approx(0.0)
    assert failed_profile.credits_earned == pytest.approx(0.0)

    mixed = SemesterGradeData(
        id=102,
        name="HK1",
        summary={},
        courses=[
            CourseGradeItem(
                course_id="1",
                course_code="CS1",
                course_name="Math",
                credits=3,
                final_grade=3.0,
                credit_grade=0.0,
                letter_grade="F",
                is_passed=False,
            ),
            CourseGradeItem(
                course_id="2",
                course_code="CS2",
                course_name="Algo",
                credits=3,
                final_grade=8.5,
                credit_grade=4.0,
                letter_grade="A",
                is_passed=True,
            ),
        ],
    )
    mixed_profile = calculate_radar_profile(mixed)
    assert mixed_profile.gpa_10 == pytest.approx(5.75)
    assert mixed_profile.gpa_4 == pytest.approx(2.0)
    assert mixed_profile.credits_earned == pytest.approx(3.0)


def test_current_semester_gpa_fallback_excludes_conditional_and_transferred():
    """F10: conditional (0 TC / GDTC) and transferred courses do not affect GPA fallback."""
    courses = [
        CourseGradeItem(
            course_id="1",
            course_code="CS1",
            course_name="Core",
            credits=3,
            final_grade=8.0,
            credit_grade=3.5,
            is_passed=True,
        ),
        CourseGradeItem(
            course_id="2",
            course_code="GDTC1",
            course_name="PE",
            credits=2,
            final_grade=4.0,
            credit_grade=1.0,
            eligibility=CourseEligibility(status="conditional", reason="GDTC"),
        ),
        CourseGradeItem(
            course_id="3",
            course_code="ENG1",
            course_name="English",
            credits=3,
            final_grade=10.0,
            credit_grade=4.0,
            is_transferred=True,
        ),
    ]
    sem = SemesterGradeData(id=103, name="HK1", summary={}, courses=courses)
    profile = calculate_radar_profile(sem)

    assert profile.gpa_10 == pytest.approx(8.0)
    assert profile.gpa_4 == pytest.approx(3.5)
    assert profile.credits_earned == pytest.approx(3.0)


def test_current_semester_gpa_fallback_preserves_none_when_no_eligible_course_graded():
    """F10: in-progress semester with no final grades keeps GPA KPIs as None."""
    courses = [
        CourseGradeItem("1", "C1", "Course 1", 3, process_grade=8.0, exam_grade=None, final_grade=None),
        CourseGradeItem(
            "2",
            "TT04",
            "English 0 TC",
            0,
            final_grade=9.0,
            eligibility=CourseEligibility(status="conditional"),
        ),
    ]
    sem = SemesterGradeData(id=104, name="In-progress", summary={}, courses=courses)
    profile = calculate_radar_profile(sem)

    assert profile.gpa_10 is None
    assert profile.gpa_4 is None
    assert profile.credits_earned is None


def test_calculate_radar_profile_handles_none_summary_and_nonfinite_or_comma_gpa():
    """F10: summary=None never crashes, and nan/inf/bool/comma-decimals match _optional_float."""
    courses = [
        CourseGradeItem(
            course_id="1",
            course_code="CS1",
            course_name="Algo",
            credits=3,
            final_grade=8.0,
            is_passed=True,
        ),
    ]

    none_sem = SemesterGradeData(id=105, name="HK1", summary=None, courses=courses)  # type: ignore[arg-type]
    none_profile = calculate_radar_profile(none_sem)
    assert none_profile.gpa_10 == pytest.approx(8.0)
    assert none_profile.gpa_4 == pytest.approx(3.5)
    assert none_profile.credits_earned == pytest.approx(3.0)
    assert none_profile.metrics["total_power"].score == pytest.approx(80.0)

    tp_score, tp_cov = calculate_total_power(None, courses)
    assert tp_score == pytest.approx(80.0)
    assert tp_cov == pytest.approx(1.0)

    for bad_val in (float("nan"), float("inf"), True, False):
        bad_sem = SemesterGradeData(
            id=106,
            name="HK1",
            summary={
                "diemTBHocLuc": bad_val,
                "diemTBTinChi": bad_val,
                "stcdatHocKy": bad_val,
            },
            courses=courses,
        )
        bad_profile = calculate_radar_profile(bad_sem)
        assert bad_profile.gpa_10 == pytest.approx(8.0)
        assert bad_profile.metrics["total_power"].score == pytest.approx(80.0)
        assert bad_profile.gpa_4 == pytest.approx(3.5)
        assert bad_profile.credits_earned == pytest.approx(3.0)

    comma_sem = SemesterGradeData(
        id=107,
        name="HK1",
        summary={
            "diemTBHocLuc": "7,5",
            "diemTBTinChi": "3,25",
            "stcdatHocKy": "3",
        },
        courses=courses,
    )
    comma_profile = calculate_radar_profile(comma_sem)
    assert comma_profile.gpa_10 == pytest.approx(7.5)
    assert comma_profile.metrics["total_power"].score == pytest.approx(75.0)
    assert comma_profile.metrics["total_power"].coverage == pytest.approx(1.0)
    assert comma_profile.gpa_4 == pytest.approx(3.25)
    assert comma_profile.credits_earned == pytest.approx(3.0)


def test_eligibility_resolver_checks_mahocphan_behind_section_code_and_truthy_transferred():
    from core.academic_radar.eligibility_resolver import CourseEligibilityResolver

    resolver = CourseEligibilityResolver()
    gdtc = resolver.resolve(
        {"maLopHocPhan": "0101GDTC101", "maHocPhan": "GDTC1", "soTinChi": 2}
    )
    assert gdtc.status == "conditional"

    english = resolver.resolve(
        {"maLopHocPhan": "202601TT0105", "maHocPhan": "TT01", "soTinChi": 3}
    )
    assert english.status == "conditional"

    transferred_int = resolver.resolve(
        {"maHocPhan": "CS201", "soTinChi": 3, "isChuyenDiem": 1}
    )
    transferred_str = resolver.resolve(
        {"maHocPhan": "CS202", "soTinChi": 3, "isChuyenDiem": "true"}
    )
    assert transferred_int.status == "transferred"
    assert transferred_str.status == "transferred"


def test_total_power_excludes_transferred_and_parse_credit_progress_rejects_nan_inf_bool():
    from core.academic_radar.gpa_planner import (
        CreditProgress,
        calculate_gpa_target,
        calculate_graduation_target,
        parse_credit_progress,
    )

    courses = [
        CourseGradeItem("1", "CS1", "Core", 3.0, final_grade=7.0, is_transferred=False),
        CourseGradeItem("2", "TR1", "Transfer", 3.0, final_grade=10.0, is_transferred=True),
    ]
    tp_score, tp_cov = calculate_total_power({}, courses)
    assert tp_score == pytest.approx(70.0)
    assert tp_cov == pytest.approx(1.0)

    for bad_body in (
        {"canDat": "nan", "hienTai": 60},
        {"canDat": float("inf"), "hienTai": 60},
        {"canDat": 120, "hienTai": float("nan")},
        {"canDat": True, "hienTai": False},
    ):
        assert parse_credit_progress(bad_body) is None

    valid_prog = parse_credit_progress({"canDat": "120,5", "hienTai": "60"})
    assert valid_prog == CreditProgress(required_credits=120.5, earned_credits=60.0)

    sem = SemesterGradeData(id=1, name="HK1", summary={"diemTBTinChiTichLuy": 3.2}, courses=courses)
    assert calculate_gpa_target(sem, float("nan")).status == "invalid"
    assert calculate_graduation_target([sem], valid_prog, float("nan")).status == "invalid"
    bad_prog = CreditProgress(required_credits=float("nan"), earned_credits=60.0)
    assert calculate_graduation_target([sem], bad_prog, 3.5).status == "no_progress"
