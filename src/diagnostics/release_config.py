"""Fail-closed loading of the public diagnostics ingestion configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import unquote, urlsplit

PUBLIC_CONFIG_SCHEMA_VERSION = 1
PUBLIC_CONFIG_SCHEMA_V2 = 2
PUBLIC_CONFIG_FILENAME = "diagnostics-config.json"
VALID_BUILD_ENVIRONMENTS = frozenset({"release", "develop", "development"})
VALID_PACKAGE_FORMATS = frozenset(
    {"aab", "apk", "msix", "exe_msi", "exe", "msi", "ipa", "ios_app", "source"}
)
_SOURCE_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_EXPECTED_KEYS = frozenset(("schema_version", "sentry_dsn"))
_EXPECTED_KEYS_V2 = frozenset(
    ("schema_version", "sentry_dsn", "build_environment", "package_format")
)
_MAX_CONFIG_BYTES = 4096
_MAX_DSN_LENGTH = 2048
_PUBLIC_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_PROJECT_ID_RE = re.compile(r"^[0-9]{1,20}$")
_FORBIDDEN_TOKEN_MARKERS = (
    "auth_token",
    "authtoken",
    "management_token",
    "management-token",
    "sentry_auth_token",
    "sntrys_",
)


class PublicConfigError(ValueError):
    """The generated public config is unsafe or malformed."""


@dataclass(frozen=True, slots=True)
class PublicDiagnosticsConfig:
    """Validated public Sentry ingestion DSN and build-track metadata."""

    dsn: str | None
    build_environment: str
    package_format: str | None = None


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
    if parsed.query or parsed.fragment:
        raise PublicConfigError("Sentry DSN must not contain query or fragment data")
    if parsed.password is not None:
        raise PublicConfigError("Sentry DSN must not contain a password")
    if not parsed.hostname or parsed.hostname != parsed.hostname.casefold():
        raise PublicConfigError("Sentry DSN host must be canonical lowercase")
    if port is not None and not 1 <= port <= 65535:
        raise PublicConfigError("Sentry DSN port is invalid")
    if not parsed.username or not _PUBLIC_KEY_RE.fullmatch(parsed.username):
        raise PublicConfigError("Sentry DSN must contain only a public ingestion key")

    path_parts = [part for part in unquote(parsed.path).split("/") if part]
    if not path_parts or not _PROJECT_ID_RE.fullmatch(path_parts[-1]):
        raise PublicConfigError("Sentry DSN must end in a numeric project id")
    if any(part in (".", "..") for part in path_parts):
        raise PublicConfigError("Sentry DSN path is invalid")
    return value


def load_public_config(
    asset_path: str | os.PathLike[str],
    *,
    development: bool,
    environ: Mapping[str, str] | None = None,
) -> PublicDiagnosticsConfig:
    """Load public diagnostics metadata without consulting build env in packaged mode."""
    environment = os.environ if environ is None else environ
    default_env = "development" if development else "release"
    default_fmt = "source" if development else None
    fallback = PublicDiagnosticsConfig(
        dsn=None,
        build_environment=default_env,
        package_format=default_fmt,
    )
    if development:
        override = environment.get("UTH_SENTRY_DSN", "")
        if override:
            try:
                return PublicDiagnosticsConfig(
                    dsn=validate_public_sentry_dsn(override),
                    build_environment="development",
                    package_format="source",
                )
            except PublicConfigError:
                return fallback

    path = Path(asset_path)
    try:
        if path.is_symlink():
            return fallback
        if path.stat().st_size > _MAX_CONFIG_BYTES:
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

    if schema_version == PUBLIC_CONFIG_SCHEMA_VERSION:
        if set(payload) != _EXPECTED_KEYS:
            return fallback
        raw_dsn = payload.get("sentry_dsn")
        if not isinstance(raw_dsn, str):
            return fallback
        if raw_dsn == "":
            dsn = None
        else:
            try:
                dsn = validate_public_sentry_dsn(raw_dsn)
            except PublicConfigError:
                return fallback
        return PublicDiagnosticsConfig(
            dsn=dsn,
            build_environment=default_env,
            package_format=default_fmt,
        )

    if schema_version == PUBLIC_CONFIG_SCHEMA_V2:
        if set(payload) != _EXPECTED_KEYS_V2:
            return fallback
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
            return fallback
        if raw_dsn == "":
            dsn = None
        else:
            try:
                dsn = validate_public_sentry_dsn(raw_dsn)
            except PublicConfigError:
                return fallback
        return PublicDiagnosticsConfig(
            dsn=dsn,
            build_environment="development" if development else build_env,
            package_format="source" if development else pkg_fmt,
        )

    return fallback


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
) -> Path:
    """Atomically generate the public runtime diagnostics asset (schema 1 or 2)."""
    if not isinstance(sentry_dsn, str):
        raise PublicConfigError("Sentry DSN must be text")
    dsn = validate_public_sentry_dsn(sentry_dsn) if sentry_dsn else ""
    if build_environment is None and package_format is None:
        payload: dict[str, object] = {
            "schema_version": PUBLIC_CONFIG_SCHEMA_VERSION,
            "sentry_dsn": dsn,
        }
    else:
        if build_environment is None or package_format is None:
            raise PublicConfigError(
                "build_environment and package_format must be provided together"
            )
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
        payload = {
            "build_environment": build_environment,
            "package_format": package_format,
            "schema_version": PUBLIC_CONFIG_SCHEMA_V2,
            "sentry_dsn": dsn,
        }
    encoded = (
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")

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
    "PUBLIC_CONFIG_FILENAME",
    "PUBLIC_CONFIG_SCHEMA_V2",
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
    "validate_public_sentry_dsn",
]
