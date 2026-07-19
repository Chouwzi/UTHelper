import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.controllers.refresh_coordinator import (
    apply_pending_submission_updates,
    build_prefetched_dataset,
    build_refresh_dataset,
    format_refresh_status,
    merge_cached_details,
    prepare_activity_hot_fields,
    sort_activity_items,
)


def test_merge_cached_details_overlays_prefetched_fields():
    items = [
        {
            "url": "https://example.test/a",
            "type": "other",
            "course": "Old",
            "deadline": "2026-07-02 10:00:00",
        }
    ]
    cache = {
        "https://example.test/a": {
            "type": "assignment",
            "course": "Clean Code",
            "submission_status": "submitted",
            "details": {"status_data": {}},
            "deadline": "2026-07-01 10:00:00",
            "is_open": True,
            "urgency": "critical",
        }
    }

    result = merge_cached_details(items, cache)

    assert result[0]["type"] == "assignment"
    assert result[0]["course"] == "Clean Code"
    assert result[0]["submission_status"] == "submitted"
    assert result[0]["deadline"] == "2026-07-01 10:00:00"
    assert items[0]["type"] == "other"


def test_apply_pending_submission_updates_keeps_new_local_status_and_prunes_old_items():
    items = [
        {"url": "fresh", "details": {"status_data": {"Trạng thái nộp bài": "old"}}},
        {"url": "old", "details": {}},
        {"url": "before-fetch", "details": {}},
    ]
    pending = {
        "fresh": (200.0, "submitted"),
        "old": (10.0, "submitted"),
        "before-fetch": (140.0, "submitted"),
    }

    result, remaining = apply_pending_submission_updates(
        items,
        pending,
        fetch_start_time=150.0,
        now=230.0,
        ttl_seconds=100,
    )

    assert result[0]["submission_status"] == "submitted"
    assert result[0]["details"]["status_data"]["Trạng thái nộp bài"] == "submitted"
    assert "submission_status" not in result[2]
    assert set(remaining) == {"fresh", "before-fetch"}


def test_sort_activity_items_orders_by_urgency_then_deadline():
    items = [
        {"title": "safe", "urgency": "safe", "deadline": "2026-07-01 09:00:00"},
        {"title": "warning", "urgency": "warning", "deadline": "2026-07-02 09:00:00"},
        {"title": "critical late", "urgency": "critical", "deadline": "2026-07-03 09:00:00"},
        {"title": "critical early", "urgency": "critical", "deadline": "2026-07-01 08:00:00"},
    ]

    sort_activity_items(items)

    assert [item["title"] for item in items] == [
        "critical early",
        "critical late",
        "warning",
        "safe",
    ]


def test_prepare_activity_hot_fields_adds_filter_cache_fields():
    items = [
        {
            "title": "Bài Tập Lớn",
            "course": "Lập Trình",
            "deadline": "2026-07-01 08:30:00",
        }
    ]

    prepare_activity_hot_fields(items)

    assert items[0]["_deadline_dt"] is not None
    assert items[0]["_title_lower"] == "bài tập lớn"
    assert items[0]["_course_lower"] == "lập trình"


def test_build_prefetched_dataset_merges_and_sorts():
    items = [
        {"url": "safe", "urgency": "safe", "deadline": "2026-07-02"},
        {"url": "critical", "urgency": "safe", "deadline": "2026-07-03"},
    ]
    cache = {"critical": {"urgency": "critical", "deadline": "2026-07-01"}}

    result = build_prefetched_dataset(items, cache)

    assert [item["url"] for item in result] == ["critical", "safe"]


def test_build_refresh_dataset_merges_pending_and_counts_submissions():
    items = [
        {
            "url": "submitted",
            "title": "A",
            "course": "Course",
            "deadline": "2026-07-01 10:00:00",
            "submission_status": "unknown",
        },
        {
            "url": "old",
            "title": "B",
            "course": "Course",
            "deadline": "2026-07-02 10:00:00",
            "submission_status": "unknown",
        },
    ]

    result = build_refresh_dataset(
        items,
        detail_cache={},
        pending_updates={"submitted": (200.0, "Đã nộp"), "old": (10.0, "Đã nộp")},
        fetch_start_time=150.0,
        now=230.0,
    )

    assert result.total_count == 2
    assert result.submitted_count == 1
    assert result.items[0]["_title_lower"] == "a"
    assert result.items[0]["submission_status"] == "Đã nộp"


def test_format_refresh_status_includes_submission_progress():
    assert format_refresh_status("09:30", 3, 2) == "Cập nhật lúc 09:30 · 3 hoạt động · 2/3 đã nộp ✓"
    assert format_refresh_status("09:30", 2, 2) == "Cập nhật lúc 09:30 · 2 hoạt động · Đã nộp hết ✓"
