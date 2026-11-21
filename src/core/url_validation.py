"""Strict HTTPS and trusted UTH origin validation for user-launched links."""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse

from core.moodle_sites import moodle_site_from_url


def _is_unsafe_ip(ip_str: str) -> bool:
    raw = ip_str.split("%", 1)[0].strip("[]")
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
        or not ip.is_global
    )


def _parse_posix_inet_aton(host: str) -> str | None:
    """Parse POSIX ``inet_aton`` 1..4-part decimal/octal/hex IPv4 literals."""
    if not host or "." in (host[0], host[-1]):
        return None
    parts = host.split(".")
    if not (1 <= len(parts) <= 4):
        return None
    values: list[int] = []
    for part in parts:
        if not part:
            return None
        lower = part.lower()
        if lower.startswith("0x"):
            digits = lower[2:]
            if not digits or any(ch not in "0123456789abcdef" for ch in digits):
                return None
            val = int(digits, 16)
        elif len(lower) > 1 and lower.startswith("0"):
            if any(ch not in "01234567" for ch in lower):
                return None
            val = int(lower, 8)
        else:
            if any(ch not in "0123456789" for ch in lower):
                return None
            val = int(lower, 10)
        values.append(val)
    if len(values) == 1:
        if values[0] > 0xFFFFFFFF:
            return None
        addr_int = values[0]
    elif len(values) == 2:
        if values[0] > 0xFF or values[1] > 0xFFFFFF:
            return None
        addr_int = (values[0] << 24) | values[1]
    elif len(values) == 3:
        if values[0] > 0xFF or values[1] > 0xFF or values[2] > 0xFFFF:
            return None
        addr_int = (values[0] << 24) | (values[1] << 16) | values[2]
    else:
        if any(val > 0xFF for val in values):
            return None
        addr_int = (
            (values[0] << 24) | (values[1] << 16) | (values[2] << 8) | values[3]
        )
    return str(ipaddress.IPv4Address(addr_int))


def _parse_literal_ip(host: str) -> str | None:
    """Return canonical IP string for standard or alternative IPv4/IPv6 literals."""
    candidate = host.split("%", 1)[0].strip("[]")
    try:
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        pass
    host_no_dot = host.rstrip(".")
    if not host_no_dot:
        return None
    try:
        return socket.inet_ntoa(socket.inet_aton(host_no_dot))
    except OSError:
        return _parse_posix_inet_aton(host_no_dot)


def validate_public_https_url(
    url: object,
    *,
    resolve_dns: bool = True,
) -> str | None:
    """Return ``url`` only when it is HTTPS and does not target local/private IPs."""
    validated = validate_https_url(url)
    if validated is None:
        return None
    parsed = urllib.parse.urlsplit(validated)
    host = (parsed.hostname or "").strip()
    if not host:
        return None
    host_cf = host.rstrip(".").casefold()
    if (
        host_cf == "localhost"
        or host_cf.endswith(".localhost")
        or host_cf.endswith(".local")
    ):
        return None
    # Literal IP check (standard IPv4/IPv6, IPv4-mapped IPv6, and alt IPv4 forms).
    literal_ip = _parse_literal_ip(host)
    if literal_ip is not None:
        if _is_unsafe_ip(literal_ip):
            return None
        return validated
    if not resolve_dns:
        return validated
    port = parsed.port or 443
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return None
    if not infos:
        return None
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            return None
        if _is_unsafe_ip(str(sockaddr[0])):
            return None
    return validated


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
