"""Fail-closed loading of the public diagnostics and analytics ingestion configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import unquote, urlsplit

PUBLIC_CONFIG_SCHEMA_VERSION = 1
PUBLIC_CONFIG_SCHEMA_V2 = 2
PUBLIC_CONFIG_SCHEMA_V3 = 3
PUBLIC_CONFIG_FILENAME = "diagnostics-config.json"
DEFAULT_DEV_POSTHOG_HOST = "https://us.i.posthog.com"
VALID_BUILD_ENVIRONMENTS = frozenset({"release", "develop", "development"})
VALID_PACKAGE_FORMATS = frozenset(
    {"aab", "apk", "msix", "exe_msi", "exe", "msi", "ipa", "ios_app", "source"}
)
_SOURCE_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_EXPECTED_KEYS = frozenset(("schema_version", "sentry_dsn"))
_EXPECTED_KEYS_V2 = frozenset(
    ("schema_version", "sentry_dsn", "build_environment", "package_format")
)
_EXPECTED_KEYS_V3 = frozenset(
    (
        "schema_version",
        "sentry_dsn",
        "build_environment",
        "package_format",
        "posthog_project_api_key",
        "posthog_host",
    )
)
_MAX_CONFIG_BYTES = 4096
_MAX_DSN_LENGTH = 2048
_MAX_POSTHOG_HOST_LENGTH = 512
_PUBLIC_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_PROJECT_ID_RE = re.compile(r"^[0-9]{1,20}$")
_POSTHOG_PUBLIC_KEY_RE = re.compile(r"^phc_[A-Za-z0-9_-]{20,128}$")
_HOSTNAME_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
_CLEAN_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_POSTHOG_WEB_UI_HOSTS = frozenset(
    {"app.posthog.com", "us.posthog.com", "eu.posthog.com"}
)
_NAT64_WELL_KNOWN_NET = ipaddress.IPv6Network("64:ff9b::/96")
_NAT64_LOCAL_USE_NET = ipaddress.IPv6Network("64:ff9b:1::/48")
_IPV4_MAPPED_NET = ipaddress.IPv6Network("::ffff:0:0/96")
_SIIT_IPV4_TRANSLATED_NET = ipaddress.IPv6Network("::ffff:0:0:0/96")
_SIXTOFOUR_NET = ipaddress.IPv6Network("2002::/16")
_TEREDO_NET = ipaddress.IPv6Network("2001::/32")
_BENCHMARKING_IPV6_NET = ipaddress.IPv6Network("2001:2::/48")
_DISCARD_ONLY_IPV6_NET = ipaddress.IPv6Network("100::/64")
_NON_PUBLIC_IPV6_NETWORKS = (
    _NAT64_WELL_KNOWN_NET,
    _NAT64_LOCAL_USE_NET,
    _IPV4_MAPPED_NET,
    _SIIT_IPV4_TRANSLATED_NET,
    _SIXTOFOUR_NET,
    _TEREDO_NET,
    _BENCHMARKING_IPV6_NET,
    _DISCARD_ONLY_IPV6_NET,
)


def _has_dangling_empty_port(netloc_host_port: str, parsed_port: int | None) -> bool:
    if parsed_port is not None:
        return False
    if netloc_host_port.startswith("["):
        close_idx = netloc_host_port.find("]")
        if close_idx < 0:
            return True
        return ":" in netloc_host_port[close_idx + 1 :]
    return ":" in netloc_host_port


def _unwrap_embedded_ipv4(
    ip_addr: ipaddress.IPv6Address,
) -> tuple[ipaddress.IPv4Address, ...]:
    embedded: list[ipaddress.IPv4Address] = []
    if ip_addr.ipv4_mapped is not None:
        embedded.append(ip_addr.ipv4_mapped)
    if ip_addr.sixtofour is not None:
        embedded.append(ip_addr.sixtofour)
    if ip_addr.teredo is not None:
        server_v4, client_v4 = ip_addr.teredo
        embedded.append(server_v4)
        embedded.append(client_v4)
    if (
        ip_addr in _NAT64_WELL_KNOWN_NET
        or ip_addr in _NAT64_LOCAL_USE_NET
        or ip_addr in _SIIT_IPV4_TRANSLATED_NET
    ):
        embedded.append(ipaddress.IPv4Address(ip_addr.packed[12:16]))
    return tuple(embedded)
_FORBIDDEN_TOKEN_MARKERS = (
    "auth_token",
    "authtoken",
    "management_token",
    "management-token",
    "sentry_auth_token",
    "sntrys_",
)
_FORBIDDEN_POSTHOG_KEY_MARKERS = (
    *_FORBIDDEN_TOKEN_MARKERS,
    "phx_",
    "phs_",
    "secret",
    "personal",
    "bearer",
)


class PublicConfigError(ValueError):
    """The generated public config is unsafe or malformed."""


@dataclass(frozen=True, slots=True)
class PublicDiagnosticsConfig:
    """Validated public Sentry ingestion DSN, PostHog config, and build-track metadata."""

    dsn: str | None
    build_environment: str
    package_format: str | None = None
    posthog_project_api_key: str | None = None
    posthog_host: str | None = None

    @property
    def posthog_configured(self) -> bool:
        return bool(self.posthog_project_api_key and self.posthog_host)


def validate_public_sentry_dsn(value: str) -> str:
    """Return an HTTPS ingestion DSN, rejecting management credentials."""
    if not isinstance(value, str):
        raise PublicConfigError("Sentry DSN must be text")
    if not value or len(value) > _MAX_DSN_LENGTH:
        raise PublicConfigError("Sentry DSN is empty or too long")
    if value != value.strip() or any(ord(char) < 0x21 for char in value):
        raise PublicConfigError("Sentry DSN contains whitespace or control data")

    decoded = unquote(value).casefold()
    if any(marker in decoded for marker in _FORBIDDEN_TOKEN_MARKERS):
        raise PublicConfigError("management/auth tokens are not public runtime config")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise PublicConfigError("Sentry DSN is not a valid URL") from exc

    if parsed.scheme != "https":
        raise PublicConfigError("Sentry ingestion requires HTTPS")
    if parsed.query or parsed.fragment or "?" in value or "#" in value:
        raise PublicConfigError("Sentry DSN must not contain query or fragment data")
    if parsed.password is not None:
        raise PublicConfigError("Sentry DSN must not contain a password")
    if not parsed.hostname or parsed.hostname != parsed.hostname.casefold():
        raise PublicConfigError("Sentry DSN host must be canonical lowercase")
    raw_host_port = parsed.netloc.rpartition("@")[2]
    if _has_dangling_empty_port(raw_host_port, port) or (
        port is not None and not 1 <= port <= 65535
    ):
        raise PublicConfigError("Sentry DSN port is invalid")
    if not parsed.username or not _PUBLIC_KEY_RE.fullmatch(parsed.username):
        raise PublicConfigError("Sentry DSN must contain only a public ingestion key")

    path_parts = [part for part in unquote(parsed.path).split("/") if part]
    if not path_parts or not _PROJECT_ID_RE.fullmatch(path_parts[-1]):
        raise PublicConfigError("Sentry DSN must end in a numeric project id")
    if any(part in (".", "..") for part in path_parts):
        raise PublicConfigError("Sentry DSN path is invalid")
    return value


def validate_public_posthog_key(value: str) -> str:
    """Return a validated public PostHog project API key (``phc_...``)."""
    if not isinstance(value, str):
        raise PublicConfigError("PostHog project API key must be text")
    if not value:
        raise PublicConfigError("PostHog project API key is empty")
    if value != value.strip() or any(
        ord(char) < 0x21 or ord(char) > 0x7E for char in value
    ):
        raise PublicConfigError(
            "PostHog project API key contains whitespace or control data"
        )

    lowered = value.casefold()
    decoded = unquote(value).casefold()
    if any(
        marker in lowered or marker in decoded
        for marker in _FORBIDDEN_POSTHOG_KEY_MARKERS
    ):
        raise PublicConfigError(
            "secret, personal, or management tokens are not public runtime config"
        )
    if not _POSTHOG_PUBLIC_KEY_RE.fullmatch(value):
        raise PublicConfigError(
            "PostHog project API key must match ^phc_[A-Za-z0-9_-]{20,128}$"
        )
    return value


def validate_public_posthog_host(
    value: str,
    *,
    allow_private_dev: bool = False,
) -> str:
    """Return a canonical HTTPS PostHog ingestion base URL without trailing slash."""
    if not isinstance(value, str):
        raise PublicConfigError("PostHog host must be text")
    if not value or len(value) > _MAX_POSTHOG_HOST_LENGTH:
        raise PublicConfigError("PostHog host is empty or too long")
    if value != value.strip() or any(
        ord(char) < 0x21 or ord(char) > 0x7E for char in value
    ):
        raise PublicConfigError("PostHog host contains whitespace or control data")
    if "\\" in value:
        raise PublicConfigError("PostHog host must not contain backslashes")

    decoded_full = unquote(value).casefold()
    if any(
        marker in decoded_full
        for marker in (
            *_FORBIDDEN_TOKEN_MARKERS,
            "phx_",
            "phs_",
        )
    ):
        raise PublicConfigError("secret/management tokens must not appear in PostHog host")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise PublicConfigError("PostHog host is not a valid URL") from exc

    if parsed.scheme != "https":
        raise PublicConfigError("PostHog ingestion requires HTTPS")
    if (
        parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
    ):
        raise PublicConfigError("PostHog host must not contain credentials")
    if parsed.query or parsed.fragment or "?" in value or "#" in value:
        raise PublicConfigError("PostHog host must not contain query or fragment data")

    hostname = parsed.hostname
    if not hostname or parsed.netloc != parsed.netloc.casefold():
        raise PublicConfigError("PostHog host must be canonical lowercase")
    if _has_dangling_empty_port(parsed.netloc, port) or (
        port is not None and not 1 <= port <= 65535
    ):
        raise PublicConfigError("PostHog host port is invalid")
    if "%" in hostname or hostname.startswith(".") or hostname.endswith(".") or ".." in hostname:
        raise PublicConfigError("PostHog host name is invalid")
    if hostname in _POSTHOG_WEB_UI_HOSTS:
        raise PublicConfigError(
            f"PostHog web UI host {hostname!r} redirects; use https://us.i.posthog.com, "
            "https://eu.i.posthog.com, or a clean HTTPS reverse-proxy ingestion host"
        )

    try:
        ip_addr = ipaddress.ip_address(hostname)
    except ValueError:
        ip_addr = None

    if ip_addr is not None:
        effective_ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip_addr]
        if isinstance(ip_addr, ipaddress.IPv6Address):
            effective_ips.extend(_unwrap_embedded_ipv4(ip_addr))
        for candidate_ip in effective_ips:
            if candidate_ip.is_unspecified or candidate_ip.is_multicast:
                raise PublicConfigError("PostHog host IP literal is invalid")
            if not allow_private_dev and (
                candidate_ip.is_loopback
                or candidate_ip.is_private
                or candidate_ip.is_link_local
                or candidate_ip.is_reserved
                or not candidate_ip.is_global
                or (
                    isinstance(candidate_ip, ipaddress.IPv6Address)
                    and any(
                        candidate_ip in net for net in _NON_PUBLIC_IPV6_NETWORKS
                    )
                )
            ):
                raise PublicConfigError(
                    "PostHog host must not use loopback, private, reserved, or non-global IP literals"
                )
    else:
        if (
            not _HOSTNAME_RE.fullmatch(hostname)
            or hostname.rsplit(".", 1)[-1].isdigit()
            or (
                hostname.startswith("0x")
                and len(hostname) > 2
                and all(ch in "0123456789abcdef" for ch in hostname[2:])
            )
        ):
            raise PublicConfigError("PostHog host name is invalid")
        if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
            if not allow_private_dev:
                raise PublicConfigError(
                    "PostHog host must not use localhost or .local in production"
                )
        elif "." not in hostname and not allow_private_dev:
            raise PublicConfigError(
                "PostHog host must be a fully-qualified domain name"
            )

    raw_path = parsed.path
    if ";" in raw_path or "//" in raw_path:
        raise PublicConfigError("PostHog host path is invalid")
    if unquote(raw_path) != raw_path:
        raise PublicConfigError("PostHog host path must not use percent-encoding")
    segments = [seg for seg in raw_path.split("/") if seg != ""]
    for segment in segments:
        if segment in (".", "..") or ".." in segment:
            raise PublicConfigError("PostHog host path must not contain dot segments")
        if not _CLEAN_PATH_SEGMENT_RE.fullmatch(segment):
            raise PublicConfigError("PostHog host path contains unsafe characters")

    host_netloc = (
        f"[{hostname}]"
        if isinstance(ip_addr, ipaddress.IPv6Address)
        else hostname
    )
    if port is not None:
        host_netloc = f"{host_netloc}:{port}"
    clean_path = f"/{'/'.join(segments)}" if segments else ""
    return f"https://{host_netloc}{clean_path}"


def validate_public_config_bytes(
    raw: bytes,
    *,
    development: bool = False,
) -> PublicDiagnosticsConfig:
    """Strictly validate packaged ``diagnostics-config.json`` bytes (fail-closed)."""
    if not isinstance(raw, (bytes, bytearray)):
        raise PublicConfigError("diagnostics config bytes must be binary")
    if len(raw) == 0 or len(raw) > _MAX_CONFIG_BYTES:
        raise PublicConfigError("diagnostics config is empty or exceeds size limit")
    try:
        payload = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublicConfigError("diagnostics config is not valid UTF-8 JSON") from exc

    if not isinstance(payload, dict):
        raise PublicConfigError("diagnostics config must be a JSON object")
    schema_version = payload.get("schema_version")
    if type(schema_version) is not int:
        raise PublicConfigError("diagnostics config schema_version must be an integer")

    default_env = "development" if development else "release"
    default_fmt = "source" if development else None

    if schema_version == PUBLIC_CONFIG_SCHEMA_VERSION:
        if set(payload) != _EXPECTED_KEYS:
            raise PublicConfigError("schema v1 diagnostics config keys are invalid")
        raw_dsn = payload.get("sentry_dsn")
        if not isinstance(raw_dsn, str):
            raise PublicConfigError("sentry_dsn must be a string")
        dsn = validate_public_sentry_dsn(raw_dsn) if raw_dsn else None
        return PublicDiagnosticsConfig(
            dsn=dsn,
            build_environment=default_env,
            package_format=default_fmt,
        )

    if schema_version == PUBLIC_CONFIG_SCHEMA_V2:
        if set(payload) != _EXPECTED_KEYS_V2:
            raise PublicConfigError("schema v2 diagnostics config keys are invalid")
        raw_dsn = payload.get("sentry_dsn")
        build_env = payload.get("build_environment")
        pkg_fmt = payload.get("package_format")
        if (
            not isinstance(raw_dsn, str)
            or not isinstance(build_env, str)
            or build_env not in VALID_BUILD_ENVIRONMENTS
            or not isinstance(pkg_fmt, str)
            or pkg_fmt not in VALID_PACKAGE_FORMATS
        ):
            raise PublicConfigError("schema v2 diagnostics config fields are invalid")
        dsn = validate_public_sentry_dsn(raw_dsn) if raw_dsn else None
        return PublicDiagnosticsConfig(
            dsn=dsn,
            build_environment="development" if development else build_env,
            package_format="source" if development else pkg_fmt,
        )

    if schema_version == PUBLIC_CONFIG_SCHEMA_V3:
        if set(payload) != _EXPECTED_KEYS_V3:
            raise PublicConfigError("schema v3 diagnostics config keys are invalid")
        raw_dsn = payload.get("sentry_dsn")
        build_env = payload.get("build_environment")
        pkg_fmt = payload.get("package_format")
        raw_ph_key = payload.get("posthog_project_api_key")
        raw_ph_host = payload.get("posthog_host")
        if (
            not isinstance(raw_dsn, str)
            or not isinstance(build_env, str)
            or build_env not in VALID_BUILD_ENVIRONMENTS
            or not isinstance(pkg_fmt, str)
            or pkg_fmt not in VALID_PACKAGE_FORMATS
            or not isinstance(raw_ph_key, str)
            or not isinstance(raw_ph_host, str)
        ):
            raise PublicConfigError("schema v3 diagnostics config fields are invalid")
        dsn = validate_public_sentry_dsn(raw_dsn) if raw_dsn else None
        if bool(raw_ph_key) != bool(raw_ph_host):
            raise PublicConfigError(
                "posthog_project_api_key and posthog_host must both be set or both be empty"
            )
        ph_key = validate_public_posthog_key(raw_ph_key) if raw_ph_key else None
        ph_host = (
            validate_public_posthog_host(raw_ph_host, allow_private_dev=False)
            if raw_ph_host
            else None
        )
        return PublicDiagnosticsConfig(
            dsn=dsn,
            build_environment="development" if development else build_env,
            package_format="source" if development else pkg_fmt,
            posthog_project_api_key=ph_key,
            posthog_host=ph_host,
        )

    raise PublicConfigError(f"unsupported schema_version: {schema_version!r}")


def _load_file_config_fail_closed(
    asset_path: str | os.PathLike[str],
    *,
    development: bool,
    fallback: PublicDiagnosticsConfig,
) -> PublicDiagnosticsConfig:
    path = Path(asset_path)
    try:
        if path.is_symlink():
            return fallback
        st = path.stat()
        if st.st_size > _MAX_CONFIG_BYTES or (
            getattr(st, "st_file_attributes", 0) & 0x400
        ):
            return fallback
        raw = path.read_bytes()
        if len(raw) > _MAX_CONFIG_BYTES:
            return fallback
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return fallback

    if not isinstance(payload, dict):
        return fallback
    schema_version = payload.get("schema_version")
    if type(schema_version) is not int:
        return fallback

    if schema_version in (PUBLIC_CONFIG_SCHEMA_VERSION, PUBLIC_CONFIG_SCHEMA_V2):
        try:
            return validate_public_config_bytes(raw, development=development)
        except PublicConfigError:
            return fallback

    if schema_version == PUBLIC_CONFIG_SCHEMA_V3:
        if set(payload) != _EXPECTED_KEYS_V3:
            return fallback
        raw_dsn = payload.get("sentry_dsn")
        build_env = payload.get("build_environment")
        pkg_fmt = payload.get("package_format")
        raw_ph_key = payload.get("posthog_project_api_key")
        raw_ph_host = payload.get("posthog_host")
        if (
            not isinstance(raw_dsn, str)
            or not isinstance(build_env, str)
            or build_env not in VALID_BUILD_ENVIRONMENTS
            or not isinstance(pkg_fmt, str)
            or pkg_fmt not in VALID_PACKAGE_FORMATS
            or not isinstance(raw_ph_key, str)
            or not isinstance(raw_ph_host, str)
        ):
            return fallback
        if raw_dsn == "":
            dsn = None
        else:
            try:
                dsn = validate_public_sentry_dsn(raw_dsn)
            except PublicConfigError:
                return fallback

        ph_key: str | None = None
        ph_host: str | None = None
        if raw_ph_key and raw_ph_host:
            try:
                ph_key = validate_public_posthog_key(raw_ph_key)
                ph_host = validate_public_posthog_host(
                    raw_ph_host,
                    allow_private_dev=False,
                )
            except PublicConfigError:
                ph_key = None
                ph_host = None

        return PublicDiagnosticsConfig(
            dsn=dsn,
            build_environment="development" if development else build_env,
            package_format="source" if development else pkg_fmt,
            posthog_project_api_key=ph_key,
            posthog_host=ph_host,
        )

    return fallback


def load_public_config(
    asset_path: str | os.PathLike[str],
    *,
    development: bool,
    environ: Mapping[str, str] | None = None,
) -> PublicDiagnosticsConfig:
    """Load public diagnostics & analytics metadata without consulting env in packaged mode."""
    environment = os.environ if environ is None else environ
    default_env = "development" if development else "release"
    default_fmt = "source" if development else None
    fallback = PublicDiagnosticsConfig(
        dsn=None,
        build_environment=default_env,
        package_format=default_fmt,
    )
    file_config = _load_file_config_fail_closed(
        asset_path,
        development=development,
        fallback=fallback,
    )
    if not development:
        return file_config

    # Independent dev-only overrides for Sentry (UTH_SENTRY_DSN) and PostHog
    # (UTH_POSTHOG_KEY / UTH_POSTHOG_HOST).
    raw_dsn_override = environment.get("UTH_SENTRY_DSN", "")
    if raw_dsn_override:
        try:
            resolved_dsn: str | None = validate_public_sentry_dsn(raw_dsn_override)
        except PublicConfigError:
            resolved_dsn = None
    else:
        resolved_dsn = file_config.dsn

    raw_ph_key_override = environment.get("UTH_POSTHOG_KEY", "")
    raw_ph_host_override = environment.get("UTH_POSTHOG_HOST", "")
    if raw_ph_key_override or raw_ph_host_override:
        resolved_ph_key: str | None = None
        resolved_ph_host: str | None = None
        if raw_ph_key_override:
            try:
                valid_key = validate_public_posthog_key(raw_ph_key_override)
                valid_host = (
                    validate_public_posthog_host(
                        raw_ph_host_override,
                        allow_private_dev=True,
                    )
                    if raw_ph_host_override
                    else DEFAULT_DEV_POSTHOG_HOST
                )
            except PublicConfigError:
                resolved_ph_key = None
                resolved_ph_host = None
            else:
                resolved_ph_key = valid_key
                resolved_ph_host = valid_host
        elif file_config.posthog_project_api_key:
            try:
                resolved_ph_host = validate_public_posthog_host(
                    raw_ph_host_override,
                    allow_private_dev=True,
                )
            except PublicConfigError:
                resolved_ph_key = None
                resolved_ph_host = None
            else:
                resolved_ph_key = file_config.posthog_project_api_key
    else:
        resolved_ph_key = file_config.posthog_project_api_key
        resolved_ph_host = file_config.posthog_host

    return PublicDiagnosticsConfig(
        dsn=resolved_dsn,
        build_environment="development",
        package_format="source",
        posthog_project_api_key=resolved_ph_key,
        posthog_host=resolved_ph_host,
    )


def load_runtime_public_config(
    *,
    development: bool,
    environ: Mapping[str, str] | None = None,
) -> PublicDiagnosticsConfig:
    """Load public diagnostics config from Flet's packaged or source asset directory."""
    environment = os.environ if environ is None else environ
    assets_dir = environment.get("FLET_ASSETS_DIR")
    root = Path(assets_dir) if assets_dir else _SOURCE_ASSETS_DIR
    return load_public_config(
        root / PUBLIC_CONFIG_FILENAME,
        development=development,
        environ=environment,
    )


def load_public_dsn(
    asset_path: str | os.PathLike[str],
    *,
    development: bool,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Load an ingestion DSN without consulting build env in packaged mode."""
    return load_public_config(
        asset_path,
        development=development,
        environ=environ,
    ).dsn


def load_runtime_public_dsn(
    *,
    development: bool,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Load from Flet's packaged asset root or the source asset directory."""
    return load_runtime_public_config(
        development=development,
        environ=environ,
    ).dsn


def generate_public_config(
    output_path: str | os.PathLike[str],
    sentry_dsn: str,
    *,
    build_environment: str | None = None,
    package_format: str | None = None,
    posthog_project_api_key: str | None = None,
    posthog_host: str | None = None,
    require_posthog_configured: bool = False,
) -> Path:
    """Atomically generate the public runtime diagnostics/analytics asset (schema 1, 2, or 3)."""
    if not isinstance(sentry_dsn, str):
        raise PublicConfigError("Sentry DSN must be text")
    dsn = validate_public_sentry_dsn(sentry_dsn) if sentry_dsn else ""

    has_posthog_params = (
        posthog_project_api_key is not None
        or posthog_host is not None
        or require_posthog_configured
    )

    if build_environment is None and package_format is None and not has_posthog_params:
        payload: dict[str, object] = {
            "schema_version": PUBLIC_CONFIG_SCHEMA_VERSION,
            "sentry_dsn": dsn,
        }
    else:
        if (build_environment is None) != (package_format is None):
            raise PublicConfigError(
                "build_environment and package_format must be provided together"
            )
        if build_environment is None and package_format is None:
            build_environment = "release"
            package_format = "source"
        if (
            not isinstance(build_environment, str)
            or build_environment not in VALID_BUILD_ENVIRONMENTS
        ):
            raise PublicConfigError(
                f"unsupported build_environment: {build_environment!r}"
            )
        if (
            not isinstance(package_format, str)
            or package_format not in VALID_PACKAGE_FORMATS
        ):
            raise PublicConfigError(
                f"unsupported package_format: {package_format!r}"
            )

        if not has_posthog_params:
            payload = {
                "build_environment": build_environment,
                "package_format": package_format,
                "schema_version": PUBLIC_CONFIG_SCHEMA_V2,
                "sentry_dsn": dsn,
            }
        else:
            raw_ph_key = "" if posthog_project_api_key is None else posthog_project_api_key
            raw_ph_host = "" if posthog_host is None else posthog_host
            if not isinstance(raw_ph_key, str):
                raise PublicConfigError("PostHog project API key must be text")
            if not isinstance(raw_ph_host, str):
                raise PublicConfigError("PostHog host must be text")
            if require_posthog_configured and not (raw_ph_key and raw_ph_host):
                raise PublicConfigError(
                    "both PostHog project API key and PostHog host are required"
                )
            if bool(raw_ph_key) != bool(raw_ph_host):
                raise PublicConfigError(
                    "posthog_project_api_key and posthog_host must both be set or both be empty"
                )
            ph_key = validate_public_posthog_key(raw_ph_key) if raw_ph_key else ""
            ph_host = (
                validate_public_posthog_host(raw_ph_host, allow_private_dev=False)
                if raw_ph_host
                else ""
            )
            payload = {
                "build_environment": build_environment,
                "package_format": package_format,
                "posthog_host": ph_host,
                "posthog_project_api_key": ph_key,
                "schema_version": PUBLIC_CONFIG_SCHEMA_V3,
                "sentry_dsn": dsn,
            }

    encoded = (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    if len(encoded) > _MAX_CONFIG_BYTES:
        raise PublicConfigError("generated diagnostics config exceeds size limit")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.{secrets.token_hex(8)}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    except OSError as exc:
        raise PublicConfigError("cannot write public diagnostics config") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return output


__all__ = [
    "DEFAULT_DEV_POSTHOG_HOST",
    "PUBLIC_CONFIG_FILENAME",
    "PUBLIC_CONFIG_SCHEMA_V2",
    "PUBLIC_CONFIG_SCHEMA_V3",
    "PUBLIC_CONFIG_SCHEMA_VERSION",
    "PublicConfigError",
    "PublicDiagnosticsConfig",
    "VALID_BUILD_ENVIRONMENTS",
    "VALID_PACKAGE_FORMATS",
    "generate_public_config",
    "load_public_config",
    "load_public_dsn",
    "load_runtime_public_config",
    "load_runtime_public_dsn",
    "validate_public_config_bytes",
    "validate_public_posthog_host",
    "validate_public_posthog_key",
    "validate_public_sentry_dsn",
]
