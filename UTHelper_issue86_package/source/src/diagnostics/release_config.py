"""Fail-closed loading of the public diagnostics ingestion configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit

PUBLIC_CONFIG_SCHEMA_VERSION = 1
PUBLIC_CONFIG_FILENAME = "diagnostics-config.json"
_SOURCE_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_EXPECTED_KEYS = frozenset(("schema_version", "sentry_dsn"))
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


def load_public_dsn(
    asset_path: str | os.PathLike[str],
    *,
    development: bool,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Load an ingestion DSN without consulting build env in packaged mode."""
    environment = os.environ if environ is None else environ
    if development:
        override = environment.get("UTH_SENTRY_DSN", "")
        if override:
            try:
                return validate_public_sentry_dsn(override)
            except PublicConfigError:
                return None

    path = Path(asset_path)
    try:
        if path.stat().st_size > _MAX_CONFIG_BYTES:
            return None
        raw = path.read_bytes()
        if len(raw) > _MAX_CONFIG_BYTES:
            return None
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict) or set(payload) != _EXPECTED_KEYS:
        return None
    if payload.get("schema_version") != PUBLIC_CONFIG_SCHEMA_VERSION:
        return None
    dsn = payload.get("sentry_dsn")
    if dsn == "":
        return None
    try:
        return validate_public_sentry_dsn(dsn)
    except PublicConfigError:
        return None


def load_runtime_public_dsn(
    *,
    development: bool,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Load from Flet's packaged asset root or the source asset directory."""
    environment = os.environ if environ is None else environ
    assets_dir = environment.get("FLET_ASSETS_DIR")
    root = Path(assets_dir) if assets_dir else _SOURCE_ASSETS_DIR
    return load_public_dsn(
        root / PUBLIC_CONFIG_FILENAME,
        development=development,
        environ=environment,
    )


def generate_public_config(
    output_path: str | os.PathLike[str],
    sentry_dsn: str,
) -> Path:
    """Atomically generate the two-field public runtime asset."""
    if not isinstance(sentry_dsn, str):
        raise PublicConfigError("Sentry DSN must be text")
    dsn = validate_public_sentry_dsn(sentry_dsn) if sentry_dsn else ""
    payload = {
        "schema_version": PUBLIC_CONFIG_SCHEMA_VERSION,
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
    "PUBLIC_CONFIG_SCHEMA_VERSION",
    "PublicConfigError",
    "generate_public_config",
    "load_public_dsn",
    "load_runtime_public_dsn",
    "validate_public_sentry_dsn",
]
