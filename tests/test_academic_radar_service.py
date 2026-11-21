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
