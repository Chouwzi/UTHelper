"""Create an isolated native WebView session for a trusted Moodle quiz."""

from __future__ import annotations

import copy
from contextlib import nullcontext
from dataclasses import dataclass, field
from http.cookiejar import Cookie
from typing import Any
from urllib.parse import urlsplit

from config import settings
from core.moodle_sites import TRUSTED_MOODLE_SITES, MoodleSite
from core.quiz_player import (
    QuizLaunchTarget,
    QuizSessionLease,
    acquire_quiz_session_lease,
    build_quiz_launch_target,
    quiz_session_key,
)


class QuizWebSessionError(ValueError):
    """A short, safe error code for an unavailable embedded quiz session."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class QuizWebCookie:
    """One Moodle session cookie with browser attributes retained.

    Cookie values are secrets. Their representation is deliberately redacted.
    """

    name: str
    value: str = field(repr=False)
    domain: str | None = None
    path: str = "/"
    secure: bool = False
    http_only: bool = False
    same_site: str | None = None
    expires: int | None = None

    def to_native_dict(self) -> dict[str, Any]:
        """Return the stable JSON shape used by the native WebView bridge."""
        return {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "secure": self.secure,
            "http_only": self.http_only,
            "same_site": self.same_site,
            "expires": self.expires,
        }


@dataclass(frozen=True)
class QuizWebSession:
    """An ephemeral, site-scoped Moodle WebView authentication session."""

    site_identity: str
    origin: str
    cookies: tuple[QuizWebCookie, ...] = field(repr=False)

    def to_native_cookies(self) -> list[dict[str, Any]]:
        """Return detached dictionaries for the native player control."""
        return [cookie.to_native_dict() for cookie in self.cookies]


def _site_for_target(target: QuizLaunchTarget) -> MoodleSite:
    if not isinstance(target, QuizLaunchTarget):
        raise QuizWebSessionError("session_site_mismatch")
    site = next(
        (site for site in TRUSTED_MOODLE_SITES if site.identity == target.site_identity),
        None,
    )
    if site is None:
        raise QuizWebSessionError("session_site_mismatch")
    try:
        validated = build_quiz_launch_target(target.url, "target-validation")
        parsed = urlsplit(target.url)
    except Exception:
        raise QuizWebSessionError("session_site_mismatch") from None
    if (
        validated.site_identity != site.identity
        or validated.course_module_id != target.course_module_id
        or validated.session_key != quiz_session_key(site, "target-validation")
        or target.url != validated.url
        or f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}" != site.origin
    ):
        raise QuizWebSessionError("session_site_mismatch")
    return site


def _cookie_attribute(cookie: Cookie, name: str) -> str | None:
    for key, value in cookie._rest.items():
        if str(key).casefold() == name.casefold():
            if value is None:
                return ""
            return str(value)
    return None


def _cookie_path_applies(cookie_path: str, request_path: str) -> bool:
    path = cookie_path or "/"
    if request_path == path:
        return True
    if not request_path.startswith(path):
        return False
    return path.endswith("/") or request_path[len(path) :].startswith("/")


def _export_moodle_session_cookies(
    client,
    site: MoodleSite,
    target: QuizLaunchTarget,
) -> tuple[QuizWebCookie, ...]:
    parsed = urlsplit(target.url)
    hostname = (parsed.hostname or "").casefold()
    found: list[QuizWebCookie] = []

    for cookie in client._cookie_jar:
        if cookie.name.casefold() != "moodlesession" or not cookie.value:
            continue
        cookie_domain = str(cookie.domain or "").lstrip(".").casefold()
        if cookie_domain != hostname:
            continue
        if not _cookie_path_applies(cookie.path, parsed.path):
            continue
        same_site = _cookie_attribute(cookie, "SameSite")
        found.append(
            QuizWebCookie(
                name=cookie.name,
                value=cookie.value,
                # A null domain keeps host-only cookies host-only in WebViews.
                domain=cookie.domain if cookie.domain_specified else None,
                path=cookie.path or "/",
                secure=bool(cookie.secure),
                http_only=_cookie_attribute(cookie, "HttpOnly") is not None,
                same_site=same_site or None,
                expires=int(cookie.expires) if cookie.expires is not None else None,
            )
        )

    if not found:
        raise QuizWebSessionError("session_cookie_missing")
    return tuple(found)


def _validate_quiz_session_request(
    target: QuizLaunchTarget,
    lease: QuizSessionLease,
) -> tuple[MoodleSite, str, str]:
    site = _site_for_target(target)
    if not isinstance(lease, QuizSessionLease) or not lease.is_active:
        raise QuizWebSessionError("session_lease_inactive")
    if lease.session_key != target.session_key:
        raise QuizWebSessionError("session_site_mismatch")
    if not lease.is_ready:
        raise QuizWebSessionError("session_not_ready")

    account_id = settings.UTH_USERNAME
    password = settings.UTH_PASSWORD
    if not isinstance(account_id, str) or not account_id.strip() or not password:
        raise QuizWebSessionError("missing_credentials")
    if quiz_session_key(site, account_id) != target.session_key:
        raise QuizWebSessionError("account_mismatch")
    return site, account_id, password


def clone_quiz_client_from(
    existing_client: Any,
    site: MoodleSite,
    account_id: str,
) -> tuple[Any, int] | None:
    """Clone an already-authenticated MoodleClient's cookies into a quiz-isolated client."""
    if existing_client is None or not isinstance(account_id, str) or not account_id.strip():
        return None
    target_host = (urlsplit(site.origin).hostname or "").casefold()
    if not target_host:
        return None

    lock = getattr(existing_client, "_session_lock", None)
    ctx = lock if lock is not None else nullcontext()
    with ctx:
        if getattr(existing_client, "moodle_site_origin", "") != site.origin:
            return None
        owner = str(getattr(existing_client, "_session_owner", "") or "")
        if not owner.strip() or owner.strip().casefold() != account_id.strip().casefold():
            return None
        source_jar = getattr(existing_client, "_cookie_jar", None)
        if source_jar is None:
            return None
        cookies = list(source_jar)
        has_session_cookie = any(
            getattr(cookie, "name", "").casefold() == "moodlesession"
            and bool(getattr(cookie, "value", ""))
            and str(getattr(cookie, "domain", "") or "").lstrip(".").casefold() == target_host
            and _cookie_path_applies(getattr(cookie, "path", "/") or "/", "/mod/quiz/view.php")
            for cookie in cookies
        )
        if not has_session_cookie:
            return None

        from core.client import MoodleClient

        cloned = MoodleClient(site.origin)
        if cloned.moodle_site_origin != site.origin:
            return None
        for cookie in cookies:
            cloned._cookie_jar.set_cookie(copy.deepcopy(cookie))
        cloned._session_owner = owner
        cloned._sesskey = str(getattr(existing_client, "_sesskey", "") or "")
        snapshot_gen = int(getattr(existing_client, "_session_generation", 0))
        return cloned, snapshot_gen


def clone_authenticated_quiz_client(
    target: QuizLaunchTarget,
    lease: QuizSessionLease,
    existing_client: Any,
):
    """Return a cloned quiz client and active lease when ``existing_client`` holds a valid session."""
    site, account_id, _password = _validate_quiz_session_request(target, lease)
    cloned_pair = clone_quiz_client_from(existing_client, site, account_id)
    if cloned_pair is None:
        return None
    cloned, snapshot_gen = cloned_pair
    operation_lease = acquire_quiz_session_lease(target.session_key)
    try:
        if not operation_lease.is_active or not operation_lease.is_ready:
            raise QuizWebSessionError("session_not_ready")
        return cloned, site, operation_lease, snapshot_gen
    except Exception:
        cloned._cookie_jar.clear()
        operation_lease.release()
        raise


def warm_existing_client_from_quiz_login(
    existing_client: Any,
    quiz_client: Any,
    site: MoodleSite,
    account_id: str,
    html: str,
    *,
    initial_gen: int | None = None,
    fallback_sesskey: str = "",
    replace_stale: bool = False,
) -> None:
    """Seed an unauthenticated shared client after a cold quiz login without sharing CookieJar."""
    if (
        existing_client is None
        or quiz_client is None
        or not isinstance(account_id, str)
        or not account_id.strip()
        or getattr(existing_client, "moodle_site_origin", "") != site.origin
    ):
        return

    from core.client import MoodleClient

    sesskey = (
        MoodleClient._extract_sesskey_from_html(html)
        or str(fallback_sesskey or "").strip()
        or str(getattr(quiz_client, "_sesskey", "") or "")
    )
    if not sesskey:
        return
    source_jar = getattr(quiz_client, "_cookie_jar", None)
    if source_jar is None:
        return
    cookies = list(source_jar)
    if not any(
        getattr(c, "name", "").casefold() == "moodlesession" and bool(getattr(c, "value", ""))
        for c in cookies
    ):
        return

    lock = getattr(existing_client, "_session_lock", None)
    ctx = lock if lock is not None else nullcontext()
    with ctx:
        if (
            initial_gen is not None
            and int(getattr(existing_client, "_session_generation", 0)) != initial_gen
        ):
            return
        current_user = str(getattr(settings, "UTH_USERNAME", "") or "")
        if (
            current_user.strip()
            and current_user.strip().casefold() != account_id.strip().casefold()
        ):
            return
        existing_owner = str(getattr(existing_client, "_session_owner", "") or "")
        if (
            existing_owner.strip()
            and existing_owner.strip().casefold() != account_id.strip().casefold()
        ):
            return
        if not replace_stale and getattr(existing_client, "_sesskey", ""):
            return
        existing_jar = getattr(existing_client, "_cookie_jar", None)
        if existing_jar is None:
            return
        existing_jar.clear()
        for cookie in cookies:
            existing_jar.set_cookie(copy.deepcopy(cookie))
        existing_client._sesskey = sesskey
        existing_client._session_owner = account_id.strip()
        existing_client._session_generation = (
            int(getattr(existing_client, "_session_generation", 0)) + 1
        )


def _authenticated_quiz_client(
    target: QuizLaunchTarget,
    lease: QuizSessionLease,
    *,
    require_sesskey: bool = True,
):
    """Open a fresh, account-scoped HTML session and return its temporary hold.

    The caller releases the returned hold only after its full operation (for
    example cookie export or quiz entry) completes. This covers a UI lease
    being released while a blocking operation is still in flight.
    """
    site, account_id, password = _validate_quiz_session_request(target, lease)

    # Imported here so core.client can continue to use the launch lease guard
    # without creating a client <-> web-session import cycle.
    from core.client import MoodleClient

    client = MoodleClient(site.origin)
    if client.moodle_site_origin != site.origin:
        raise QuizWebSessionError("session_site_mismatch")

    login_lease = acquire_quiz_session_lease(target.session_key)
    try:
        if not login_lease.is_active or not login_lease.is_ready:
            raise QuizWebSessionError("session_not_ready")
        if not client.login_session(
            username=account_id,
            password=password,
            force=True,
            quiz_lease=login_lease,
            require_sesskey=require_sesskey,
        ):
            raise QuizWebSessionError("login_failed")
        return client, site, login_lease
    except Exception:
        login_lease.release()
        raise


def prepare_quiz_web_session(
    target: QuizLaunchTarget,
    lease: QuizSessionLease,
) -> QuizWebSession:
    """Authenticate to the target Moodle site and export its auth cookie.

    Uses the stored UTH account through Moodle's normal HTML form on either
    trusted UTH Moodle site. It never converts WS tokens or carries cookies
    between sites. The additional lease protects an in-flight login if the UI
    cancels the native player and releases its own lease.
    """
    client, site, operation_lease = _authenticated_quiz_client(target, lease)
    try:
        cookies = _export_moodle_session_cookies(client, site, target)
        return QuizWebSession(
            site_identity=site.identity,
            origin=site.origin,
            cookies=cookies,
        )
    finally:
        operation_lease.release()
