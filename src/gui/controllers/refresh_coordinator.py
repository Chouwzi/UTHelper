from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from core.activity_status import (
    COMPLETED_SUBMISSION_STATUSES,
    extract_canonical_submission_status,
    is_ws_verifiable_courses_module,
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


@dataclass
class RefreshDataset:
    items: list[dict[str, Any]]
    pending_updates: dict[str, tuple[float, str]]
    total_count: int
    submitted_count: int


def _is_thnn_item(item: dict[str, Any]) -> bool:
    if item.get("site_identity") == "thnn" or item.get("source") == "thnn_sync":
        return True
    url = str(item.get("url") or "").strip()
    if not url:
        return False
    host = (urlsplit(url).hostname or "").casefold()
    return host == "thnn.ut.edu.vn" or host.endswith(".thnn.ut.edu.vn")


def _extract_item_cmid(item: dict[str, Any]) -> int | None:
    raw_cmid = item.get("cmid")
    if raw_cmid is not None and not isinstance(raw_cmid, bool):
        try:
            parsed = int(raw_cmid)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
    url = str(item.get("url") or "")
    if "id=" in url:
        try:
            parsed = int(url.split("id=")[-1].split("&")[0])
            if parsed > 0:
                return parsed
        except (ValueError, IndexError):
            pass
    return None


def status_hydration_candidates(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only Courses cards whose student status still requires Moodle WS verification."""
    candidates = []
    for item in items:
        if not item.get("url"):
            continue
        if _is_thnn_item(item):
            continue
        activity_type = item.get("type")
        if activity_type not in {"assignment", "assign", "quiz"}:
            continue
        if not is_ws_verifiable_courses_module(
            str(item.get("url") or ""),
            str(item.get("activity_key") or item.get("id") or ""),
            str(activity_type or ""),
        ):
            continue
        raw_status = item.get("submission_status")
        raw_norm = str(raw_status or "").strip().casefold()
        status = normalize_submission_status(raw_status)
        details = item.get("details")
        has_verified_status_data = bool(
            isinstance(details, dict) and details.get("status_data")
        )
        is_unverified_action_assign = (
            activity_type in {"assignment", "assign"}
            and (
                raw_norm == "new"
                or (
                    item.get("source") == "ws_api"
                    and status == "not_submitted"
                    and not has_verified_status_data
                )
            )
        )
        if (
            status == "unknown"
            or (activity_type == "quiz" and status == "not_submitted")
            or is_unverified_action_assign
        ):
            candidates.append(item)
    return candidates


def notification_status_hydration_candidates(
    items: list[dict[str, Any]],
    detail_cache: dict[str, dict[str, Any]],
    pending_updates: dict[str, tuple[float, str]],
    fetch_start_time: float,
    *,
    now: datetime | None = None,
    now_epoch: float | None = None,
    ttl_seconds: int = 300,
) -> list[dict[str, Any]]:
    """Return upcoming Courses activities that must be WS-verified before notifying."""
    reference_dt = now or datetime.now()
    current_epoch = time.time() if now_epoch is None else now_epoch
    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for item in status_hydration_candidates(items):
        url = str(item.get("url") or "")
        if not url or url in seen_urls:
            continue
        deadline_dt = item.get("_deadline_dt") or parse_datetime(
            item.get("deadline", "")
        )
        if deadline_dt is None or deadline_dt <= reference_dt:
            continue

        pending_entry = pending_updates.get(url)
        if (
            pending_entry is not None
            and current_epoch - pending_entry[0] < ttl_seconds
            and pending_entry[0] > fetch_start_time
        ):
            continue

        cached_entry = detail_cache.get(url)
        if cached_entry is not None:
            cached_status = extract_canonical_submission_status(cached_entry)
            if cached_status in COMPLETED_SUBMISSION_STATUSES:
                continue

        seen_urls.add(url)
        candidates.append(item)

    return candidates


def apply_pre_dispatch_status_hydration(
    items: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    hydrated_by_url: dict[str, dict[str, Any]],
) -> None:
    """Apply pre-dispatch status hydration in place; failed lookups degrade to 'unknown'."""
    candidate_urls = {
        str(item.get("url") or "") for item in candidates if item.get("url")
    }
    if not candidate_urls:
        return

    for item in items:
        url = str(item.get("url") or "")
        if not url or url not in candidate_urls:
            continue
        enriched = hydrated_by_url.get(url)
        if enriched is not None:
            resolved = normalize_submission_status(
                enriched.get("submission_status")
            )
            item["submission_status"] = resolved
            enriched_details = enriched.get("details")
            if isinstance(enriched_details, dict) and enriched_details:
                cur_details = item.get("details")
                cur_details = dict(cur_details) if isinstance(cur_details, dict) else {}
                cur_details.update(enriched_details)
                item["details"] = cur_details
        else:
            # Never leave an unverified action_events 'new' ('not_submitted') guess
            # when Moodle WS status lookup failed!
            item["submission_status"] = "unknown"


def apply_cached_thnn_completions(
    items: list[dict[str, Any]],
    completed_thnn_cmids: set[int],
) -> None:
    """Promote THNN activities to 'submitted' when CourseCacheStore already marks cmid completed."""
    if not completed_thnn_cmids:
        return
    for item in items:
        if not _is_thnn_item(item):
            continue
        if normalize_submission_status(item.get("submission_status")) != "unknown":
            continue
        cmid = _extract_item_cmid(item)
        if cmid is not None and cmid in completed_thnn_cmids:
            item["submission_status"] = "submitted"


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
        if _is_thnn_item(item):
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
    pending_updates: dict[str, tuple[float, str]] | None = None,
    prefetch_start_time: float = 0.0,
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Build the display dataset after background detail prefetch completes.

    When ``pending_updates`` is supplied, optimistic local submission statuses
    recorded after ``prefetch_start_time`` are preserved over stale prefetched
    values and expired entries are pruned in place (intended to be called under
    the controller's ``_data_lock``).
    """
    data = merge_cached_details(items, detail_cache)
    if pending_updates is not None:
        data, fresh_pending = apply_pending_submission_updates(
            data,
            pending_updates,
            prefetch_start_time,
            now=now,
        )
        pending_updates.clear()
        pending_updates.update(fresh_pending)
    sort_activity_items(data)
    prepare_activity_hot_fields(data)
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
        1
        for item in data
        if normalize_submission_status(item.get("submission_status"))
        in COMPLETED_SUBMISSION_STATUSES
    )
    return RefreshDataset(
        items=data,
        pending_updates=fresh_pending,
        total_count=len(data),
        submitted_count=submitted_count,
    )


def format_refresh_status(clock_label: str, total_count: int, submitted_count: int = 0) -> str:
    """Format the dashboard refresh status line."""
    return f"Cập nhật lúc {clock_label} · {total_count} hoạt động"
