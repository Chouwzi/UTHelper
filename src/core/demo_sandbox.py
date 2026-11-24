"""Pure-Python Reviewer Demo Sandbox for CH Play / Store review (`uthelper_demo`).

Provides offline, date-relative Moodle WS/AJAX/HTML fixtures, in-memory PDF/ZIP/Markdown
documents, weekly/daily schedules, multi-semester Academic Radar transcripts, and
read-only mutation guards without importing any GUI or Flet modules.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
import hashlib
import hmac
from typing import Any
from urllib.parse import urlsplit

from core.demo.academic import (
    DailyScheduleSnapshot,
    WeekScheduleSnapshot,
    build_demo_credit_progress,
    build_demo_current_semester_courses,
    build_demo_day_schedule,
    build_demo_full_transcript,
    build_demo_week_schedule,
)
from core.demo.files import (
    COURSES_ORIGIN,
    THNN_ORIGIN,
    lookup_demo_file_bytes,
)
from core.demo.moodle_html import (
    render_grade_report_html,
    render_session_html,
    render_thnn_calendar_ics,
)
from core.demo.moodle_router import (
    handle_ajax_call,
    handle_ws_call,
)

DEMO_USERNAME: str = "uthelper_demo"
DEMO_USER_ID: int = 990_001

_PBKDF2_ITERATIONS: int = 200_000
_DEMO_PASSWORD_SALT: bytes = b"uthelper-reviewer-demo-v1-salt-2026"
_DEMO_PASSWORD_DIGEST_HEX: str = (
    "e61538f99cf036a6b444937413d7e6632d86d39c96dd9768e1f3be47e85ab531"
)

__all__ = [
    "DEMO_USERNAME",
    "DEMO_USER_ID",
    "DailyScheduleSnapshot",
    "WeekScheduleSnapshot",
    "get_demo_credit_progress",
    "get_demo_current_semester_courses",
    "get_demo_day_schedule",
    "get_demo_file_bytes",
    "get_demo_full_transcript",
    "get_demo_week_schedule",
    "handle_demo_ajax_call",
    "handle_demo_ws_call",
    "is_demo_account",
    "is_demo_credentials",
    "normalize_demo_username",
    "render_demo_grade_report_html",
    "render_demo_session_html",
]


def normalize_demo_username(username: str | None) -> str:
    """Normalize a username string, canonicalizing Reviewer Demo variants to `uthelper_demo`."""
    if not isinstance(username, str):
        return ""
    stripped = username.strip()
    if stripped.casefold() == DEMO_USERNAME:
        return DEMO_USERNAME
    return stripped


def is_demo_account(username: str | None) -> bool:
    """Return True iff `username` matches the Reviewer Demo account (case-insensitive, trimmed)."""
    if not isinstance(username, str):
        return False
    return username.strip().casefold() == DEMO_USERNAME


def is_demo_credentials(username: str | None, password: str | None) -> bool:
    """Verify Reviewer Demo username and password via constant-time PBKDF2-HMAC-SHA256."""
    if not is_demo_account(username) or not isinstance(password, str) or not password:
        return False
    candidate_hex = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        _DEMO_PASSWORD_SALT,
        _PBKDF2_ITERATIONS,
    ).hex()
    return hmac.compare_digest(candidate_hex, _DEMO_PASSWORD_DIGEST_HEX)


def handle_demo_ws_call(
    function: str,
    params: Mapping[str, Any],
    *,
    site_origin: str = COURSES_ORIGIN,
    now: datetime | None = None,
) -> Any:
    """Return date-relative Moodle REST WS payload or read-only Moodle exception dict."""
    return handle_ws_call(
        function,
        params or {},
        user_id=DEMO_USER_ID,
        username=DEMO_USERNAME,
        site_origin=site_origin,
        now=now,
    )


def handle_demo_ajax_call(
    function: str,
    params: Mapping[str, Any],
    *,
    site_origin: str = THNN_ORIGIN,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return date-relative THNN Moodle AJAX payload or read-only AJAX error dict."""
    return handle_ajax_call(
        function,
        params or {},
        site_origin=site_origin,
        now=now,
    )


def render_demo_session_html(
    url: str,
    *,
    now: datetime | None = None,
) -> tuple[str, str, bool]:
    """Render `(final_url, html_or_ics_text, redirected_cross_origin)` for Demo Mode."""
    return render_session_html(url, now=now)


def render_demo_grade_report_html(course_id: int, *, site_origin: str) -> str:
    """Render `/grade/report/user/index.php` HTML for a Demo Mode course."""
    return render_grade_report_html(course_id, site_origin=site_origin)


def get_demo_file_bytes(url: str) -> bytes | None:
    """Return valid in-memory PDF/ZIP/Markdown/ICS bytes for Demo Mode URLs, or `None`."""
    direct = lookup_demo_file_bytes(url)
    if direct is not None:
        return direct
    parsed = urlsplit(str(url or "").strip())
    origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}" if parsed.netloc else ""
    if origin == THNN_ORIGIN and parsed.path == "/calendar/export.php":
        return render_thnn_calendar_ics().encode("utf-8")
    return None


def get_demo_day_schedule(target_date: date) -> DailyScheduleSnapshot:
    """Return deterministic daily class schedule snapshot for `target_date`."""
    return build_demo_day_schedule(target_date)


def get_demo_week_schedule(week_anchor: date) -> WeekScheduleSnapshot:
    """Return Monday-anchored weekly class schedule snapshot containing `week_anchor`."""
    return build_demo_week_schedule(week_anchor)


def get_demo_credit_progress() -> dict[str, Any]:
    """Return graduation credit progress body (`78 / 132` credits) for Demo Mode."""
    return build_demo_credit_progress()


def get_demo_full_transcript() -> list[dict[str, Any]]:
    """Return 3-semester fictional UTH CNTT transcript for Demo Mode."""
    return build_demo_full_transcript()


def get_demo_current_semester_courses() -> tuple[str, list[dict[str, Any]]]:
    """Return `(semester_label, registered_courses)` for Portal current-term enrichment."""
    return build_demo_current_semester_courses()
