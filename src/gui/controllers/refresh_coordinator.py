from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.activity_status import (
    normalize_submission_status,
    prefer_known_submission_status,
)
from core.time_utils import parse_datetime

_URGENCY_ORDER = {
    "critical": 0,
    "warning": 1,
    "safe": 2,
    "overdue": 3,
}
_SUBMITTED_STATUSES = ("submitted", "Đã nộp", "graded", "Đã chấm")


@dataclass
class RefreshDataset:
    items: list[dict[str, Any]]
    pending_updates: dict[str, tuple[float, str]]
    total_count: int
    submitted_count: int


def status_hydration_candidates(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only Courses cards whose student status can still change."""
    candidates = []
    for item in items:
        if not item.get("url"):
            continue
        if item.get("site_identity") == "thnn" or item.get("source") == "thnn_sync":
            continue
        activity_type = item.get("type")
        if activity_type not in {"assignment", "assign", "quiz"}:
            continue
        status = normalize_submission_status(item.get("submission_status"))
        if status == "unknown" or (
            activity_type == "quiz" and status == "not_submitted"
        ):
            candidates.append(item)
    return candidates


def card_detail_prefetch_candidates(
    items: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return unique Courses cards ordered by temporal proximity."""
    reference_time = now or datetime.now()
    candidates: list[tuple[tuple[float, int], dict[str, Any]]] = []
    seen_urls: set[str] = set()

    for item in items:
        url = item.get("url")
        if not url or url in seen_urls:
            continue
        if item.get("site_identity") == "thnn" or item.get("source") == "thnn_sync":
            continue

        seen_urls.add(url)
        deadline = parse_datetime(item.get("deadline", ""))
        if deadline is None:
            sort_key = (float("inf"), 1)
        else:
            distance = abs((deadline - reference_time).total_seconds())
            # At equal distance, an upcoming quiz is more actionable.
            sort_key = (distance, 0 if deadline >= reference_time else 1)
        candidates.append((sort_key, item))

    candidates.sort(key=lambda entry: entry[0])
    return [item for _, item in candidates]


def merge_cached_details(
    items: list[dict[str, Any]],
    detail_cache: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge prefetched detail cache into activity rows without mutating the input list."""
    merged = list(items)
    for index, item in enumerate(merged):
        url = item.get("url")
        if not url or url not in detail_cache:
            continue

        enriched = detail_cache[url]
        submission_status = prefer_known_submission_status(
            enriched.get("submission_status"),
            item.get("submission_status"),
        )
        enriched_details = enriched.get("details")
        merged[index] = {
            **item,
            "type": enriched.get("type", item.get("type", "other")),
            "course": enriched.get("course", item.get("course", "")),
            "submission_status": submission_status,
            "details": enriched_details or item.get("details", {}),
            # The latest catalog owns event identity. Two cards (for example,
            # opens/closes) can legitimately share one Moodle module URL.
            "deadline": item.get("deadline", enriched.get("deadline")),
            "is_open": item.get("is_open", enriched.get("is_open")),
            "urgency": item.get("urgency", enriched.get("urgency")),
        }
    return merged


def apply_pending_submission_updates(
    items: list[dict[str, Any]],
    pending_updates: dict[str, tuple[float, str]],
    fetch_start_time: float,
    *,
    now: float | None = None,
    ttl_seconds: int = 300,
) -> tuple[list[dict[str, Any]], dict[str, tuple[float, str]]]:
    """Preserve local submission changes that happened after the current fetch began."""
    current_time = time.time() if now is None else now
    fresh_pending = {
        url: update
        for url, update in pending_updates.items()
        if current_time - update[0] < ttl_seconds
    }

    for item in items:
        url = item.get("url")
        if not url or url not in fresh_pending:
            continue

        action_time, new_status = fresh_pending[url]
        if action_time <= fetch_start_time:
            continue

        canonical_status = normalize_submission_status(new_status)
        item["submission_status"] = canonical_status
        details = dict(item.get("details", {}))
        status_data = dict(details.get("status_data", {}))
        status_data["Trạng thái nộp bài"] = canonical_status
        details["status_data"] = status_data
        item["details"] = details

    return items, fresh_pending


def sort_activity_items(items: list[dict[str, Any]]) -> None:
    """Sort activities by urgency first, then by deadline."""
    items.sort(
        key=lambda item: (
            _URGENCY_ORDER.get(item.get("urgency"), 2),
            item.get("deadline", ""),
        )
    )


def prepare_activity_hot_fields(items: list[dict[str, Any]]) -> None:
    """Precompute fields used heavily by filtering and rendering."""
    for item in items:
        deadline = item.get("deadline", "")
        if deadline and "_deadline_dt" not in item:
            item["_deadline_dt"] = parse_datetime(deadline)
        if "_title_lower" not in item:
            item["_title_lower"] = str(item.get("title", "")).lower()
        if "_course_lower" not in item:
            item["_course_lower"] = str(item.get("course", "")).lower()


def build_prefetched_dataset(
    items: list[dict[str, Any]],
    detail_cache: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the display dataset after background detail prefetch completes."""
    data = merge_cached_details(items, detail_cache)
    sort_activity_items(data)
    return data


def build_refresh_dataset(
    items: list[dict[str, Any]],
    detail_cache: dict[str, dict[str, Any]],
    pending_updates: dict[str, tuple[float, str]],
    fetch_start_time: float,
    *,
    now: float | None = None,
) -> RefreshDataset:
    """Build the post-fetch activity dataset and preserve local submission updates."""
    data = merge_cached_details(items, detail_cache)
    data, fresh_pending = apply_pending_submission_updates(
        data,
        pending_updates,
        fetch_start_time,
        now=now,
    )
    sort_activity_items(data)
    prepare_activity_hot_fields(data)

    submitted_count = sum(
        1 for item in data if item.get("submission_status", "") in _SUBMITTED_STATUSES
    )
    return RefreshDataset(
        items=data,
        pending_updates=fresh_pending,
        total_count=len(data),
        submitted_count=submitted_count,
    )


def format_refresh_status(clock_label: str, total_count: int, submitted_count: int) -> str:
    """Format the dashboard refresh status line."""
    progress_text = ""
    if total_count > 0 and submitted_count > 0:
        if submitted_count == total_count:
            progress_text = " · Đã nộp hết ✓"
        else:
            progress_text = f" · {submitted_count}/{total_count} đã nộp ✓"
    return f"Cập nhật lúc {clock_label} · {total_count} hoạt động{progress_text}"
