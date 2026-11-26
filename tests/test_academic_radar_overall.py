import pytest

from core.academic_radar.models import CourseGradeItem, CourseEligibility, SemesterGradeData
from core.academic_radar.overall import calculate_overall_gpa, required_average


def _c(code, credits, final, c4, **kw):
    return CourseGradeItem(
        course_id=code, course_code=code, course_name=code,
        credits=credits, final_grade=final, credit_grade=c4, **kw,
    )


def test_overall_weighted_across_semesters():
    s1 = SemesterGradeData(id=1, name="HK1", courses=[_c("A", 3, 8.0, 3.0), _c("B", 2, 9.0, 4.0)])
    s2 = SemesterGradeData(id=2, name="HK2", courses=[_c("C", 5, 7.0, 2.5)])
    o = calculate_overall_gpa([s1, s2])
    assert o.gpa_10 == pytest.approx((8 * 3 + 9 * 2 + 7 * 5) / 10)
    assert o.gpa_4 == pytest.approx((3 * 3 + 4 * 2 + 2.5 * 5) / 10)
    assert o.total_credits == 10
    assert o.semester_count == 2


def test_overall_skips_conditional_transferred_and_retake_keeps_best():
    cond = _c("D", 0, 9.0, 4.0, eligibility=CourseEligibility(status="conditional"))
    moved = _c("E", 3, 10.0, 4.0, is_transferred=True)
    s1 = SemesterGradeData(id=1, name="HK1", courses=[_c("A", 3, 5.0, 1.5), cond, moved])
    s2 = SemesterGradeData(id=2, name="HK2", courses=[_c("A", 3, 8.0, 3.0)])
    o = calculate_overall_gpa([s1, s2])
    assert o.course_count == 1
    assert o.gpa_10 == pytest.approx(8.0)


def test_overall_empty():
    o = calculate_overall_gpa([])
    assert o.gpa_10 is None and o.gpa_4 is None and o.total_credits == 0


def test_required_average():
    # 60 tín chỉ GPA 3.0, học thêm 20 tín chỉ để đạt 3.2 -> cần 3.8
    assert required_average(3.0, 60, 3.2, 20) == pytest.approx(3.8)
    assert required_average(3.0, 60, 3.2, 0) is None
    assert required_average(3.5, 60, 2.5, 20) < 0


def test_overall_gpa_derives_gpa4_via_course_point4_when_credit_grade_missing():
    s1 = SemesterGradeData(
        id=1,
        name="HK1",
        courses=[
            CourseGradeItem("1", "C1", "Algo", 3.0, final_grade=8.5, credit_grade=None, letter_grade=""),
            CourseGradeItem("2", "C2", "DB", 3.0, final_grade=7.2, credit_grade=None, letter_grade="B"),
        ],
    )
    overall = calculate_overall_gpa([s1])
    assert overall.gpa_10 == pytest.approx((8.5 * 3 + 7.2 * 3) / 6.0)
    assert overall.gpa_4 == pytest.approx((4.0 * 3 + 3.0 * 3) / 6.0)
    assert overall.total_credits == pytest.approx(6.0)
