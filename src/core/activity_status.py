from __future__ import annotations

from collections.abc import Mapping
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

COMPLETED_SUBMISSION_STATUSES: frozenset[str] = frozenset({"submitted", "graded"})

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
    "released": "graded",
    "đã công bố": "graded",
    "not opened": "not_opened",
    "chưa mở": "not_opened",
    "inprogress": "in_progress",
    "đang làm": "in_progress",
    "quá hạn": "overdue",
    "chưa hoàn thành": "abandoned",
    "đã bắt đầu": "attempted",
}

_GRADING_STATUS_KEYS = ("Chấm điểm", "Grading status", "Trạng thái chấm")
_SUBMISSION_STATUS_KEYS = (
    "Trạng thái nộp bài",
    "Submission status",
    "Trạng thái",
)


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


def _has_valid_feedback_grade(feedback: Any) -> bool:
    if not isinstance(feedback, Mapping):
        return False
    grade_info = feedback.get("grade")
    if not isinstance(grade_info, Mapping):
        return False
    raw_grade = grade_info.get("grade")
    if raw_grade is None or isinstance(raw_grade, bool):
        return False
    text = str(raw_grade).strip()
    if not text or text == "-":
        return False
    try:
        # Moodle stores -1.00000 in mdl_assign_grades when a row exists without a grade.
        return float(text) >= 0.0
    except ValueError:
        return True


def resolve_moodle_assign_submission_status(
    response: Mapping[str, Any] | None,
) -> str:
    """Resolve canonical student status from ``mod_assign_get_submission_status``.

    Checks direct lecturer grading (``gradingstatus`` / ``feedback.grade``) and
    both individual ``submission`` and group ``teamsubmission`` records, keeping
    malformed or missing ``lastattempt`` responses as ``unknown``.
    """
    if not isinstance(response, Mapping) or "exception" in response:
        return "unknown"

    last_attempt = response.get("lastattempt")
    feedback = response.get("feedback")

    if isinstance(last_attempt, Mapping):
        grading_raw = last_attempt.get("gradingstatus")
        if normalize_submission_status(grading_raw) == "graded":
            return "graded"
    if _has_valid_feedback_grade(feedback):
        return "graded"

    if not isinstance(last_attempt, Mapping) or not last_attempt:
        return "unknown"

    raw_statuses: list[str] = []
    normalized_statuses: list[str] = []
    for key in ("submission", "teamsubmission"):
        sub = last_attempt.get(key)
        if not isinstance(sub, Mapping):
            continue
        raw = sub.get("status")
        if isinstance(raw, str) and raw.strip():
            raw_statuses.append(raw)
            normalized_statuses.append(normalize_submission_status(raw))

    if "graded" in normalized_statuses:
        return "graded"
    if "submitted" in normalized_statuses:
        return "submitted"
    if "draft" in normalized_statuses:
        return "draft"
    if "not_submitted" in normalized_statuses:
        return "not_submitted"
    if raw_statuses:
        for norm in normalized_statuses:
            if norm != "unknown":
                return norm
        return "unknown"
    return "not_submitted"


def _read_attr_or_key(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def extract_canonical_submission_status(value: Any) -> str:
    """Extract canonical submission status from an activity dict, DTO, or string."""
    if value is None:
        return "unknown"
    if isinstance(value, str):
        return normalize_submission_status(value)

    graded_flag = _read_attr_or_key(value, "graded")
    if isinstance(graded_flag, bool) and graded_flag:
        return "graded"

    raw_top = _read_attr_or_key(value, "submission_status")
    top_status = (
        normalize_submission_status(raw_top)
        if isinstance(raw_top, str)
        else "unknown"
    )

    details = _read_attr_or_key(value, "details")
    detail_candidates: list[str] = []
    if details is not None:
        status_data = _read_attr_or_key(details, "status_data")
        if isinstance(status_data, Mapping):
            for g_key in _GRADING_STATUS_KEYS:
                g_val = status_data.get(g_key)
                if isinstance(g_val, str) and g_val.strip():
                    if normalize_submission_status(g_val) == "graded":
                        return "graded"
            for s_key in _SUBMISSION_STATUS_KEYS:
                s_val = status_data.get(s_key)
                if isinstance(s_val, str) and s_val.strip():
                    s_norm = normalize_submission_status(s_val)
                    if s_norm != "unknown":
                        detail_candidates.append(s_norm)

        quiz_att = _read_attr_or_key(details, "quiz_attempt_status")
        if isinstance(quiz_att, str) and quiz_att.strip():
            q_norm = normalize_submission_status(quiz_att)
            if q_norm != "unknown":
                detail_candidates.insert(0, q_norm)

        det_sub = _read_attr_or_key(details, "submission_status")
        if isinstance(det_sub, str) and det_sub.strip():
            d_norm = normalize_submission_status(det_sub)
            if d_norm != "unknown":
                detail_candidates.insert(0, d_norm)

    if top_status == "graded" or "graded" in detail_candidates:
        return "graded"
    if top_status == "submitted" or "submitted" in detail_candidates:
        return "submitted"
    if top_status != "unknown":
        return top_status
    return detail_candidates[0] if detail_candidates else "unknown"
