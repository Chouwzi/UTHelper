"""Strict HTTPS and trusted UTH origin validation for user-launched links."""

from __future__ import annotations

import urllib.parse

from core.moodle_sites import moodle_site_from_url


def validate_https_url(url: object) -> str | None:
    """Return ``url`` if it is a clean ``https://`` URL with a valid host, else ``None``."""
    if not isinstance(url, str) or not url or url != url.strip():
        return None
    if "\\" in url or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        _ = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return url


def _configured_uth_hosts() -> set[str]:
    hosts: set[str] = set()
    try:
        from config import settings

        for attr in ("PORTAL_API_BASE", "MOODLE_BASE_URL"):
            raw = str(getattr(settings, attr, "") or "").strip()
            if not raw:
                continue
            parsed = urllib.parse.urlsplit(raw)
            if parsed.scheme.lower() == "https" and parsed.hostname:
                hosts.add(parsed.hostname.casefold())
    except Exception:
        pass
    return hosts


def is_trusted_uth_lms_url(url: object) -> bool:
    """Return True only when ``url`` is an HTTPS URL on a trusted UTH Moodle/Portal host."""
    validated = validate_https_url(url)
    if validated is None:
        return False
    if moodle_site_from_url(validated) is not None:
        return True
    parsed = urllib.parse.urlsplit(validated)
    host = (parsed.hostname or "").casefold()
    if host == "ut.edu.vn" or host.endswith(".ut.edu.vn"):
        return parsed.port is None
    return host in _configured_uth_hosts()
