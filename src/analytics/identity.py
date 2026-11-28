"""Lazy, reparse-safe app-scoped random installation UUIDv4 identity store."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from analytics._fs_safety import (
    FileIdentity,
    atomic_write_regular,
    delete_exact_regular,
    ensure_safe_directory,
    exclusive_directory_lock,
    open_binary_no_follow,
    path_identity,
    path_is_reparse,
    thread_lock_for,
    verify_non_reparse_target,
)
from analytics.models import ANALYTICS_SCHEMA_VERSION, validate_uuid4_string

INSTALLATION_FILENAME = "installation.json"
MAX_INSTALLATION_BYTES = 512
_EXPECTED_INSTALLATION_KEYS = frozenset({"schema_version", "distinct_id"})


class InstallationIdentityStore:
    """Persist a random ``UUIDv4`` ``distinct_id`` in ``<appdata>/analytics/installation.json``.

    Strict lazy invariant:
    - ``__init__`` creates zero directories or files on disk.
    - ``get_or_create(allow_create=True)`` creates/reads ``installation.json`` ONLY when
      the caller confirms ``configured and consent == ENABLED and authenticated_non_demo
      and foreground_visible``.
    - ``wipe()`` immediately clears cached RAM state and unlinks ``installation.json``
      under ``FILE_FLAG_OPEN_REPARSE_POINT`` / ``O_NOFOLLOW`` + handle verification.
    """

    def __init__(self, analytics_dir: Path | str) -> None:
        candidate = Path(analytics_dir)
        verify_non_reparse_target(candidate, label="analytics directory")
        self.analytics_dir = candidate
        self.installation_path = self.analytics_dir / INSTALLATION_FILENAME
        self._lock = thread_lock_for(self.analytics_dir)
        self._cached_distinct_id: str | None = None

    def peek_cached(self) -> str | None:
        """Return the in-memory ``distinct_id`` without performing disk I/O."""
        with self._lock:
            return self._cached_distinct_id

    def get_or_create(self, *, allow_create: bool = True) -> str | None:
        """Return existing or newly minted UUIDv4 ``distinct_id`` when ``allow_create=True``."""
        with self._lock:
            if not allow_create:
                return None
            if self._cached_distinct_id is not None:
                return self._cached_distinct_id

            resolved_dir, dir_identity = ensure_safe_directory(
                self.analytics_dir,
                label="analytics directory",
            )
            self.analytics_dir = resolved_dir
            self.installation_path = resolved_dir / INSTALLATION_FILENAME

            with exclusive_directory_lock(
                resolved_dir,
                expected=dir_identity,
                lock_prefix="UTHelperAnalyticsIdentity",
                lock_filename=".analytics-identity.lock",
                guard_filename=".analytics-identity.operation",
            ):
                existing = self._read_validated_locked(resolved_dir)
                if existing is not None:
                    self._cached_distinct_id = existing
                    return existing

                minted = str(uuid4())
                payload = (
                    json.dumps(
                        {
                            "distinct_id": minted,
                            "schema_version": ANALYTICS_SCHEMA_VERSION,
                        },
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8")
                atomic_write_regular(
                    resolved_dir,
                    dir_identity=dir_identity,
                    filename=INSTALLATION_FILENAME,
                    payload=payload,
                    fsync=False,
                )
                self._cached_distinct_id = minted
                return minted

    def wipe(self) -> None:
        """Clear RAM ``distinct_id`` immediately and safely delete ``installation.json``."""
        with self._lock:
            self._cached_distinct_id = None
            if not self.analytics_dir.exists() and not path_is_reparse(
                self.analytics_dir
            ):
                return
            if path_is_reparse(self.analytics_dir):
                return
            try:
                resolved_dir = self.analytics_dir.absolute().resolve(strict=True)
                dir_identity = path_identity(resolved_dir, expect_directory=True)
            except (OSError, ValueError):
                return

            try:
                with exclusive_directory_lock(
                    resolved_dir,
                    expected=dir_identity,
                    lock_prefix="UTHelperAnalyticsIdentity",
                    lock_filename=".analytics-identity.lock",
                    guard_filename=".analytics-identity.operation",
                ):
                    self._unlink_owned_identity_files_locked(resolved_dir)
            except (OSError, RuntimeError, TimeoutError, ValueError):
                return

    def rotate(self, *, allow_create: bool = False) -> str | None:
        """Wipe existing ``distinct_id`` and optionally mint a fresh ``UUIDv4``."""
        self.wipe()
        if not allow_create:
            return None
        return self.get_or_create(allow_create=True)

    def _read_validated_locked(self, resolved_dir: Path) -> str | None:
        target = resolved_dir / INSTALLATION_FILENAME
        if path_is_reparse(target):
            return None
        try:
            identity = path_identity(target, expect_directory=False)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None

        if identity.size > MAX_INSTALLATION_BYTES:
            delete_exact_regular(target, expected=identity)
            return None

        opened = open_binary_no_follow(target, expected=identity)
        if opened is None:
            return None
        stream, opened_identity = opened
        with stream:
            raw = stream.read(MAX_INSTALLATION_BYTES + 1)
        if len(raw) > MAX_INSTALLATION_BYTES:
            delete_exact_regular(target, expected=opened_identity)
            return None

        try:
            current_identity = path_identity(target, expect_directory=False)
        except (OSError, ValueError):
            return None
        if current_identity.key != opened_identity.key:
            return None

        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            delete_exact_regular(target, expected=opened_identity)
            return None

        if (
            not isinstance(parsed, dict)
            or set(parsed.keys()) != _EXPECTED_INSTALLATION_KEYS
            or type(parsed.get("schema_version")) is not int
            or parsed.get("schema_version") != ANALYTICS_SCHEMA_VERSION
        ):
            delete_exact_regular(target, expected=opened_identity)
            return None

        raw_id = parsed.get("distinct_id")
        if not isinstance(raw_id, str):
            delete_exact_regular(target, expected=opened_identity)
            return None
        try:
            return validate_uuid4_string(raw_id, field_name="distinct_id")
        except ValueError:
            delete_exact_regular(target, expected=opened_identity)
            return None

    @staticmethod
    def _unlink_owned_identity_files_locked(resolved_dir: Path) -> None:
        try:
            with os.scandir(resolved_dir) as entries:
                for entry in entries:
                    is_main = entry.name == INSTALLATION_FILENAME
                    is_temp = entry.name.startswith(
                        f".{INSTALLATION_FILENAME}."
                    ) and entry.name.endswith(".tmp")
                    if not (is_main or is_temp):
                        continue
                    candidate = resolved_dir / entry.name
                    if candidate.parent != resolved_dir:
                        continue
                    try:
                        file_id: FileIdentity = path_identity(
                            candidate,
                            expect_directory=False,
                        )
                    except (OSError, ValueError):
                        continue
                    delete_exact_regular(candidate, expected=file_id)
        except OSError:
            return


__all__ = [
    "INSTALLATION_FILENAME",
    "InstallationIdentityStore",
    "MAX_INSTALLATION_BYTES",
]
