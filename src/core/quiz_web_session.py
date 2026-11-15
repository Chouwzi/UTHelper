"""Create an isolated native WebView session for a trusted Moodle quiz."""

from __future__ import annotations

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
        ):
            raise QuizWebSessionError("login_failed")
        cookies = _export_moodle_session_cookies(client, site, target)
        return QuizWebSession(
            site_identity=site.identity,
            origin=site.origin,
            cookies=cookies,
        )
    finally:
        login_lease.release()
