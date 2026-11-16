from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock
from core.academic_radar import AcademicGradeService, SemesterGradeData


def test_academic_grade_service_daily_cache(tmp_path):
    cache_file = tmp_path / "portal_grades_cache.json"

    mock_portal = MagicMock()
    service = AcademicGradeService(portal_client=mock_portal, cache_dir=tmp_path)

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

    # 3. Force refresh -> bypasses cache and calls portal again
    result_forced = service.load_academic_data("student1", "pass1", force_refresh=True)
    assert len(result_forced) == 1
    assert mock_portal.fetch_full_transcript.call_count == 2


def test_academic_grade_service_fallback_to_stale_cache(tmp_path):
    cache_file = tmp_path / "portal_grades_cache.json"
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

    mock_portal = MagicMock()
    mock_portal.fetch_full_transcript.side_effect = ConnectionError("Network offline")

    service = AcademicGradeService(portal_client=mock_portal, cache_dir=tmp_path)
    result = service.load_academic_data("student1", "pass1")

    # Gracefully returned stale cache instead of crashing
    assert len(result) == 1
    assert result[0].name == "HK1 2025-2026"
