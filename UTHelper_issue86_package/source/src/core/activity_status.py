from __future__ import annotations

from typing import Any


_CANONICAL_STATUSES = {
    "unknown",
    "not_opened",
    "not_submitted",
    "draft",
    "submitted",
    "graded",
    "in_progress",
    "overdue",
    "abandoned",
    "attempted",
}

_STATUS_ALIASES = {
    "new": "not_submitted",
    "reopened": "not_submitted",
    "not submitted": "not_submitted",
    "no submission": "not_submitted",
    "no submissions": "not_submitted",
    "chưa nộp": "not_submitted",
    "chưa làm": "not_submitted",
    "được mở lại": "not_submitted",
    "bản nháp": "draft",
    "submitted for grading": "submitted",
    "đã nộp": "submitted",
    "đã nộp, chờ chấm": "submitted",
    "finished": "submitted",
    "đã làm": "submitted",
    "đã hoàn thành": "submitted",
    "đã chấm": "graded",
    "đã chấm điểm": "graded",
    "not opened": "not_opened",
    "chưa mở": "not_opened",
    "inprogress": "in_progress",
    "đang làm": "in_progress",
    "quá hạn": "overdue",
    "chưa hoàn thành": "abandoned",
    "đã bắt đầu": "attempted",
}


def normalize_submission_status(value: Any) -> str:
    """Map Moodle/UI status variants to the activity-domain vocabulary.

    Unknown server values deliberately remain ``unknown``. A missing status is
    not evidence that an activity has not been submitted or attempted.
    """
    if value is None:
        return "unknown"
    normalized = " ".join(str(value).strip().casefold().split())
    if not normalized:
        return "unknown"
    if normalized in _CANONICAL_STATUSES:
        return normalized
    return _STATUS_ALIASES.get(normalized, "unknown")


def prefer_known_submission_status(preferred: Any, fallback: Any) -> str:
    """Use a new authoritative status unless it carries no information."""
    normalized = normalize_submission_status(preferred)
    if normalized != "unknown":
        return normalized
    return normalize_submission_status(fallback)
