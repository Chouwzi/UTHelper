"""Explicit, normalized Moodle site identities used for native operations."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class MoodleSite:
    """A Moodle origin the application may use for authenticated APIs."""

    identity: str
    origin: str


COURSES_MOODLE_SITE = MoodleSite("courses", "https://courses.ut.edu.vn")
THNN_MOODLE_SITE = MoodleSite("thnn", "https://thnn.ut.edu.vn")
TRUSTED_MOODLE_SITES = (
    COURSES_MOODLE_SITE,
    THNN_MOODLE_SITE,
)

_SITES_BY_ORIGIN = {site.origin: site for site in TRUSTED_MOODLE_SITES}


def moodle_site_from_origin(origin: object) -> MoodleSite | None:
    """Return a trusted site only when ``origin`` is an exact HTTPS origin.

    A trailing slash and case-insensitive host are normalized. Explicit ports,
    paths, credentials, queries, fragments, and every host outside the explicit
    allow-list are rejected.
    """
    if not isinstance(origin, str) or origin != origin.strip():
        return None
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        return None
    return _SITES_BY_ORIGIN.get(f"https://{parsed.hostname.casefold()}")


def moodle_site_from_url(url: object) -> MoodleSite | None:
    """Return the trusted site identity for a URL, without trusting its path."""
    if not isinstance(url, str) or url != url.strip():
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        return None
    return _SITES_BY_ORIGIN.get(f"https://{parsed.hostname.casefold()}")
