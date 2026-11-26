from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import pytest

from core.academic_radar import (
    AcademicGradeService,
    CourseGradeItem,
    SemesterGradeData,
    SemesterNormalizer,
)
from core.academic_radar.gpa_planner import cumulative_gpa4


def test_academic_grade_service_daily_cache(tmp_path):
    mock_portal = MagicMock()
    service = AcademicGradeService(portal_client=mock_portal, cache_dir=tmp_path)
    cache_file, _ = service._cache_paths("student1")

    # 1. First fetch with no cache -> calls portal
    mock_portal.fetch_full_transcript.return_value = [
        {
            "id": 75,
            "tenDot": "HK2 2025-2026",
            "tongKetDot": {"diemTBHocLuc": 8.5},
            "diems": [],
        }
    ]

    result = service.load_academic_data("student1", "pass1")
    assert len(result) == 1
    assert result[0].name == "HK2 2025-2026"
    assert mock_portal.fetch_full_transcript.call_count == 1
    assert cache_file.exists()

    # 2. Second fetch today -> uses cache, portal is NOT called again
    result_cached = service.load_academic_data("student1", "pass1")
    assert len(result_cached) == 1
    assert mock_portal.fetch_full_transcript.call_count == 1  # Still 1!

    # 3. Different user never reuses student1's cache
    result_other = service.load_academic_data("student2", "pass2")
    assert len(result_other) == 1
    assert mock_portal.fetch_full_transcript.call_count == 2

    # 4. Force refresh -> bypasses cache and calls portal again
    result_forced = service.load_academic_data("student1", "pass1", force_refresh=True)
    assert len(result_forced) == 1
    assert mock_portal.fetch_full_transcript.call_count == 3


def test_academic_grade_service_fallback_to_stale_cache_and_redacts_username(
    tmp_path, caplog
):
    mock_portal = MagicMock()
    mock_portal.fetch_full_transcript.side_effect = ConnectionError("Network offline")
    service = AcademicGradeService(portal_client=mock_portal, cache_dir=tmp_path)
    cache_file, _ = service._cache_paths("2251120099")
    payload = {
        "cached_date": "2026-01-01",  # Old date
        "updated_at": "2026-01-01T00:00:00",
        "transcript": [
            {
                "id": 74,
                "tenDot": "HK1 2025-2026",
                "tongKetDot": {"diemTBHocLuc": 8.0},
                "diems": [],
            }
        ],
    }
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)

    with caplog.at_level(logging.INFO, logger="core.academic_radar.service"):
        result = service.load_academic_data("2251120099", "pass1")

    # Gracefully returned stale cache instead of crashing and never logged username
    assert len(result) == 1
    assert result[0].name == "HK1 2025-2026"
    assert "2251120099" not in caplog.text


def test_normalizer_converts_heso_factors_to_percentages_proportionally():
    normalizer = SemesterNormalizer()
    semesters = normalizer.normalize_transcript(
        [
            {
                "id": 80,
                "tenDot": "HK1",
                "tongKetDot": {},
                "diems": [
                    {
                        "maLopHocPhan": "CS101",
                        "tenMonHoc": "Algorithms",
                        "soTinChi": 3,
                        "diemThanhPhan": [
                            {"ten": "Thường kỳ", "heSo": 2, "diem": 8.0},
                            {"ten": "Cuối kỳ", "heSo": 3, "diem": 9.0},
                        ],
                    }
                ],
            }
        ]
    )
    comps = semesters[0].courses[0].components
    assert [c.weight for c in comps] == [40.0, 60.0]


def test_cumulative_gpa4_deduplicates_retaken_courses_by_highest_grade():
    sem1 = SemesterGradeData(
        id=1,
        name="HK1",
        summary={},
        courses=[
            CourseGradeItem("1", "CS101", "Intro", 3, None, None, 4.0, credit_grade=1.0, letter_grade="D"),
            CourseGradeItem("2", "MA101", "Calc", 3, None, None, 8.5, credit_grade=3.5, letter_grade="B+"),
        ],
    )
    sem2 = SemesterGradeData(
        id=2,
        name="HK2",
        summary={},
        courses=[
            CourseGradeItem("3", "CS101", "Intro", 3, None, None, 9.0, credit_grade=4.0, letter_grade="A+"),
        ],
    )
    # Deduplicated: CS101 takes 4.0 (3cr) + MA101 3.5 (3cr) -> (12 + 10.5) / 6 = 3.75
    gpa, kind = cumulative_gpa4([sem1, sem2])
    assert gpa == pytest.approx(3.75)
    assert kind == "computed"


def test_cumulative_gpa4_and_overall_deduplicate_retakes_by_mahocphan_across_sections():
    from core.academic_radar.overall import calculate_overall_gpa

    normalizer = SemesterNormalizer()
    semesters = normalizer.normalize_transcript(
        [
            {
                "id": 1,
                "tenDot": "HK1",
                "tongKetDot": {},
                "diems": [
                    {
                        "id": 10,
                        "maHocPhan": "0120005105",
                        "maLopHocPhan": "012000510501",
                        "tenMonHoc": "Triết học",
                        "soTinChi": 3,
                        "diemTongKet": 4.0,
                        "diemTinChi": 1.0,
                        "diemChu": "D",
                    }
                ],
            },
            {
                "id": 2,
                "tenDot": "HK2",
                "tongKetDot": {},
                "diems": [
                    {
                        "id": 20,
                        "maHocPhan": "0120005105",
                        "maLopHocPhan": "012000510502",
                        "tenMonHoc": "Triết học",
                        "soTinChi": 3,
                        "diemTongKet": 9.0,
                        "diemTinChi": 4.0,
                        "diemChu": "A+",
                    }
                ],
            },
        ]
    )

    gpa4, kind = cumulative_gpa4(semesters)
    assert gpa4 == pytest.approx(4.0)
    assert kind == "computed"

    overall = calculate_overall_gpa(semesters)
    assert overall.course_count == 1
    assert overall.total_credits == 3.0
    assert overall.gpa_10 == pytest.approx(9.0)
    assert overall.gpa_4 == pytest.approx(4.0)


def test_normalizer_preserves_zero_scores_credits_and_summary_zeros():
    """F11: 0.0 scores and 0 credits must not be dropped to None by truthiness `or` chains."""
    normalizer = SemesterNormalizer()
    sem = normalizer.normalize_semester(
        {
            "id": 90,
            "tenDot": "HK1",
            "tongKetDot": {
                "diemTBHocLuc": 0.0,
                "diemTBTinChi": 0.0,
                "stcdatHocKy": 0,
                "soTCKhongDat": 3,
            },
            "diems": [
                {
                    "maHocPhan": "0120001",
                    "maLopHocPhan": "012000101",
                    "tenMonHoc": "Mon F",
                    "soTinChi": 3,
                    "diemTBThuongKy": 0.0,
                    "diemThi": 0.0,
                    "diemTongKet": 0.0,
                    "diemTinChi": 0.0,
                    "diemChu": "F",
                    "isDat": False,
                }
            ],
        }
    )

    assert sem.summary["diemTBHocLuc"] == 0.0
    assert sem.summary["diemTBTinChi"] == 0.0
    assert sem.summary["stcdatHocKy"] == 0.0
    c = sem.courses[0]
    assert c.process_grade == 0.0
    assert c.exam_grade == 0.0
    assert c.final_grade == 0.0
    assert c.credit_grade == 0.0
    assert c.is_passed is False
    assert c.pass_status is False


def test_normalizer_handles_isdat_none_and_preserves_extra_portal_fields():
    """F11: isDat=None keeps is_passed=True while pass_status=None; extra Portal fields preserved."""
    normalizer = SemesterNormalizer()
    sem = normalizer.normalize_semester(
        {
            "id": 91,
            "tenDot": "HK1 2026-2027",
            "tongKetDot": {},
            "diems": [
                {
                    "id": 501,
                    "maHocPhan": "012000999",
                    "maLopHocPhan": "01200099901",
                    "tenMonHoc": "Cơ sở dữ liệu",
                    "tenLopHocPhan": "Cơ sở dữ liệu - Nhóm 01",
                    "soTinChi": 3,
                    "diemTBThuongKy": 8.0,
                    "diemThi": None,
                    "diemTongKet": None,
                    "isDat": None,
                    "diemThiLan1": 7.5,
                    "diemThiLan2": 8.5,
                    "diemChuyenCan": 9.0,
                    "hinhThucThi": "Trắc nghiệm",
                    "ghiChu": "Miễn thi giữa kỳ",
                    "isKhongTinhDiem": False,
                    "emptyIgnored": "",
                    "nullIgnored": None,
                }
            ],
        }
    )

    c = sem.courses[0]
    assert c.subject_code == "012000999"
    assert c.class_code == "01200099901"
    assert c.is_passed is True
    assert c.pass_status is None
    assert c.extra_portal_fields["diemThiLan1"] == 7.5
    assert c.extra_portal_fields["diemThiLan2"] == 8.5
    assert c.extra_portal_fields["diemChuyenCan"] == 9.0
    assert c.extra_portal_fields["hinhThucThi"] == "Trắc nghiệm"
    assert c.extra_portal_fields["ghiChu"] == "Miễn thi giữa kỳ"
    assert c.extra_portal_fields["isKhongTinhDiem"] is False
    assert c.extra_portal_fields["tenLopHocPhan"] == "Cơ sở dữ liệu - Nhóm 01"
    assert "emptyIgnored" not in c.extra_portal_fields
    assert "nullIgnored" not in c.extra_portal_fields


def test_normalizer_rejects_bool_nonfinite_supports_comma_and_guards_dash_credits():
    """F10/F11: _parse_float rejects bool/nan/inf, handles comma decimals, and resolver handles '-'."""
    normalizer = SemesterNormalizer()
    course = normalizer.normalize_course(
        {
            "maHocPhan": "X1",
            "tenMonHoc": "Môn thử nghiệm",
            "soTinChi": "-",
            "diemTBThuongKy": True,
            "diemThi": float("nan"),
            "diemTongKet": "7,5",
            "diemTinChi": float("inf"),
        }
    )

    assert course.credits == 0.0
    assert course.eligibility.status == "unknown"
    assert course.process_grade is None
    assert course.exam_grade is None
    assert course.final_grade == pytest.approx(7.5)
    assert course.credit_grade is None


def test_logged_out_load_does_not_raise_missing_moodle_username_and_clear_session_drops_token(
    tmp_path, monkeypatch
):
    from config import settings
    from core.portal_client import PortalClient

    monkeypatch.setattr(settings, "UTH_USERNAME", "")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")

    portal = PortalClient("https://portal.ut.edu.vn/api/v1")
    portal._store_token("JWT_ACCOUNT_A", "studentA")
    assert portal._cached_token("studentA") == "JWT_ACCOUNT_A"

    service = AcademicGradeService(portal_client=portal, cache_dir=tmp_path)
    assert service.load_academic_data() == []
    assert service.load_credit_progress() is None

    service.clear_session()
    assert portal._cached_token("studentA") == ""


def test_create_semester_from_moodle_grades_maps_schema_and_unknown_zero_credit_eligibility(tmp_path):
    service = AcademicGradeService(portal_client=MagicMock(), cache_dir=tmp_path)
    courses_grades = [
        {"courseid": 101, "coursename": "Cấu trúc dữ liệu", "shortname": "CTDL01", "grade": "8,50"},
        {"courseid": 202, "coursename": "Mạng máy tính", "grade": "-"},
    ]
    grade_items = {
        "101": [
            {"itemname": "Điểm quá trình", "gradeformatted": "8,00"},
            {"itemname": "Thi cuối kỳ", "graderaw": 9.0},
        ],
        "202": [
            {"itemname": "Chuyên cần", "gradeformatted": "9,5"},
        ],
    }

    semesters = service.create_semester_from_moodle_grades(courses_grades, grade_items)
    assert len(semesters) == 1
    c1, c2 = semesters[0].courses

    assert c1.course_id == "101"
    assert c1.course_name == "Cấu trúc dữ liệu"
    assert c1.course_code == "CTDL01"
    assert c1.credits == 0.0
    assert c1.eligibility.status == "unknown"
    assert c1.process_grade == pytest.approx(8.0)
    assert c1.exam_grade == pytest.approx(9.0)
    assert c1.final_grade == pytest.approx(8.5)

    assert c2.course_id == "202"
    assert c2.course_name == "Mạng máy tính"
    assert c2.process_grade == pytest.approx(9.5)
    assert c2.final_grade is None
