"""Policy for opening a Moodle quiz in the embedded player.

The player opens Moodle's own quiz overview page. Moodle remains responsible
for authentication, access rules, and starting an attempt.
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import parse_qsl, urlsplit

from core.moodle_sites import (
    MoodleSite,
    TRUSTED_MOODLE_SITES,
    moodle_site_from_url,
)


class QuizLaunchPolicyError(ValueError):
    """A quiz URL or its known Moodle security requirements block launch."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class QuizLaunchTarget:
    """A canonical trusted quiz page and the account/site session it uses."""

    site_identity: str
    course_module_id: int
    url: str
    session_key: str
    secure_window: bool = False


def _trusted_site(site: str | MoodleSite) -> MoodleSite | None:
    if isinstance(site, MoodleSite):
        return next(
            (
                known
                for known in TRUSTED_MOODLE_SITES
                if known.identity == site.identity and known.origin == site.origin
            ),
            None,
        )
    if not isinstance(site, str):
        return None
    return next(
        (known for known in TRUSTED_MOODLE_SITES if known.identity == site),
        None,
    ) or next(
        (known for known in TRUSTED_MOODLE_SITES if known.origin == site.rstrip("/")),
        None,
    )


def quiz_session_key(site: str | MoodleSite, account_id: str) -> str:
    """Return a nonreversible, in-memory coordination key for a site/account.

    The account identifier is used only as hash input and is never returned or
    persisted. Passwords, Moodle cookies, and WS tokens are not accepted here.
    """
    trusted_site = _trusted_site(site)
    if trusted_site is None:
        raise ValueError("site must be a trusted Moodle site")
    if not isinstance(account_id, str) or not account_id.strip():
        raise ValueError("account_id must be a non-empty account identifier")
    normalized_account = account_id.strip().casefold()
    material = (
        f"uth-quiz-session-v1\0{trusted_site.identity}\0"
        f"{trusted_site.origin}\0{normalized_account}"
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _browser_security_value(metadata: Mapping[str, object] | None) -> str:
    if not isinstance(metadata, Mapping):
        return ""
    value = metadata.get("browsersecurity")
    if value is None:
        value = metadata.get("browser_security")
    return value.strip().casefold() if isinstance(value, str) else ""


def _active_rule_names(metadata: Mapping[str, object] | None) -> set[str]:
    if not isinstance(metadata, Mapping):
        return set()
    values = metadata.get("activerulenames")
    if values is None:
        values = metadata.get("active_rule_names")
    if not isinstance(values, (tuple, list, set, frozenset)):
        return set()
    return {
        rule.strip().casefold()
        for rule in values
        if isinstance(rule, str) and rule.strip()
    }


def _metadata_requires_seb(metadata: Mapping[str, object] | None) -> bool:
    """Recognize only explicit Moodle core Safe Exam Browser evidence."""
    return _browser_security_value(metadata) == "safebrowser" or bool(
        _active_rule_names(metadata)
        & {"quizaccess_seb", "quizaccess_safebrowser"}
    )


def build_quiz_launch_target(
    url: str,
    account_id: str,
    *,
    metadata: Mapping[str, object] | None = None,
) -> QuizLaunchTarget:
    """Validate and canonicalize a trusted Moodle ``mod/quiz/view.php`` URL.

    ``id`` in this route is the course-module ID (cmid). Unknown query values
    are discarded so attempt IDs or actions cannot affect the launched URL.
    Moodle's own page performs permission and access checks.
    """
    if not isinstance(url, str) or not url or url != url.strip():
        raise QuizLaunchPolicyError("invalid_url")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except (TypeError, ValueError):
        raise QuizLaunchPolicyError("invalid_url") from None

    site = moodle_site_from_url(url)
    if site is None or port is not None:
        raise QuizLaunchPolicyError("unsupported_site")
    if parsed.path != "/mod/quiz/view.php":
        raise QuizLaunchPolicyError("not_quiz_overview")

    cmid_values = [value for name, value in query if name == "id"]
    if len(cmid_values) != 1 or re.fullmatch(r"[0-9]+", cmid_values[0]) is None:
        raise QuizLaunchPolicyError("invalid_course_module_id")
    try:
        cmid = int(cmid_values[0])
    except ValueError:
        raise QuizLaunchPolicyError("invalid_course_module_id") from None
    if cmid <= 0:
        raise QuizLaunchPolicyError("invalid_course_module_id")

    if not isinstance(account_id, str) or not account_id.strip():
        raise QuizLaunchPolicyError("missing_account")
    if _metadata_requires_seb(metadata):
        raise QuizLaunchPolicyError("requires_safe_exam_browser")

    key = quiz_session_key(site, account_id)
    return QuizLaunchTarget(
        site_identity=site.identity,
        course_module_id=cmid,
        url=f"{site.origin}/mod/quiz/view.php?id={cmid}",
        session_key=key,
        secure_window=(
            _browser_security_value(metadata) == "securewindow"
            or "quizaccess_securewindow" in _active_rule_names(metadata)
        ),
    )


_session_state = threading.Condition(threading.RLock())
_active_leases: dict[str, int] = {}
_inflight_logins: dict[str, int] = {}
_login_waiters: dict[str, list[threading.Event]] = {}


class QuizSessionLease:
    """An idempotently releasable hold on one Moodle site/account session."""

    def __init__(self, session_key: str, ready: threading.Event):
        self.session_key = session_key
        self._ready = ready
        self._released = False
        self._release_lock = threading.Lock()

    @property
    def is_ready(self) -> bool:
        """Whether an HTML login already in progress has finished."""
        return self._ready.is_set()

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        """Wait until a login that predates this lease has completed."""
        return self._ready.wait(timeout)

    def release(self) -> None:
        with self._release_lock:
            if self._released:
                return
            self._released = True
        with _session_state:
            count = _active_leases.get(self.session_key, 0)
            if count <= 1:
                _active_leases.pop(self.session_key, None)
            else:
                _active_leases[self.session_key] = count - 1
            _session_state.notify_all()

    def __enter__(self) -> "QuizSessionLease":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.release()


def acquire_quiz_session_lease(session_key: str) -> QuizSessionLease:
    """Hold the session until release; wait for any earlier login to finish.

    Acquiring the hold immediately blocks new HTML logins. ``wait_until_ready``
    lets a caller that acquires from a UI thread avoid blocking that thread
    while an already-running login completes.
    """
    if not isinstance(session_key, str) or re.fullmatch(r"[0-9a-f]{64}", session_key) is None:
        raise ValueError("session_key must be a quiz session hash")
    ready = threading.Event()
    with _session_state:
        _active_leases[session_key] = _active_leases.get(session_key, 0) + 1
        if _inflight_logins.get(session_key, 0):
            _login_waiters.setdefault(session_key, []).append(ready)
        else:
            ready.set()
    return QuizSessionLease(session_key, ready)


def _begin_quiz_session_login(session_key: str) -> bool:
    """Reserve a short-lived HTML login slot unless a quiz holds the session."""
    with _session_state:
        if _active_leases.get(session_key, 0):
            return False
        _inflight_logins[session_key] = _inflight_logins.get(session_key, 0) + 1
        return True


def _end_quiz_session_login(session_key: str) -> None:
    with _session_state:
        count = _inflight_logins.get(session_key, 0)
        if count <= 1:
            _inflight_logins.pop(session_key, None)
            for event in _login_waiters.pop(session_key, []):
                event.set()
        else:
            _inflight_logins[session_key] = count - 1
        _session_state.notify_all()


def _is_quiz_session_leased(session_key: str) -> bool:
    with _session_state:
        return _active_leases.get(session_key, 0) > 0
