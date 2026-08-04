"""Atomic, bounded, local queue for validated diagnostic reports."""

from __future__ import annotations

import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from diagnostics.models import DiagnosticReport

MAX_EVENTS = 20
MAX_TOTAL_BYTES = 1024 * 1024
MAX_AGE = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    """Outcome of attempting to add one report to the queue."""

    stored: bool = False
    deduplicated: bool = False
    too_large: bool = False


@dataclass(frozen=True, slots=True)
class QueuedReport:
    """A validated report and its owned direct-child queue path."""

    report: DiagnosticReport
    path: Path


@dataclass(frozen=True, slots=True)
class _ValidatedFile:
    queued: QueuedReport
    size: int
    identity: os.stat_result


_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _lock_for(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root))
    with _LOCKS_GUARD:
        return _ROOT_LOCKS.setdefault(key, threading.RLock())


class DiagnosticSpool:
    """Store only schema-valid reports under strict count, size, and age caps."""

    def __init__(
        self,
        root: Path,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        requested_root = Path(root)
        if requested_root.is_symlink():
            raise ValueError("diagnostic spool root must not be a symlink")
        requested_root.mkdir(parents=True, exist_ok=True)
        if requested_root.is_symlink() or not requested_root.is_dir():
            raise ValueError("diagnostic spool root must be a regular directory")

        self.root = requested_root.resolve(strict=True)
        self._clock = clock
        self._lock = _lock_for(self.root)

    def enqueue(self, report: DiagnosticReport) -> EnqueueResult:
        """Atomically persist one report, deduplicating its fingerprint."""
        if not isinstance(report, DiagnosticReport):
            raise TypeError("report must be a validated DiagnosticReport")

        payload = report.model_dump_json().encode("utf-8")
        DiagnosticReport.model_validate_json(payload)
        if len(payload) > MAX_TOTAL_BYTES:
            return EnqueueResult(too_large=True)

        with self._lock:
            self._assert_root()
            current = self._prune_locked()
            if any(
                item.queued.report.fingerprint == report.fingerprint
                for item in current
            ):
                return EnqueueResult(deduplicated=True)

            temporary = self.root / f".{report.event_id}.tmp"
            final = self.root / self._filename(report)
            try:
                with temporary.open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, final)
            finally:
                self._safe_unlink_regular(temporary)

            remaining = self._prune_locked()
            stored = any(
                item.queued.report.event_id == report.event_id
                and item.queued.path == final
                for item in remaining
            )
            return EnqueueResult(stored=stored)

    def pending(self) -> tuple[QueuedReport, ...]:
        """Return validated pending reports in deterministic oldest-first order."""
        with self._lock:
            self._assert_root()
            return tuple(item.queued for item in self._prune_locked())

    def acknowledge(self, event_id: UUID) -> None:
        """Remove only validated queue entries matching ``event_id``."""
        if not isinstance(event_id, UUID):
            raise TypeError("event_id must be a UUID")
        with self._lock:
            self._assert_root()
            for item in self._prune_locked():
                if item.queued.report.event_id == event_id:
                    self._safe_unlink_regular(
                        item.queued.path,
                        expected=item.identity,
                    )

    def clear(self) -> None:
        """Delete owned direct regular JSON/temp children without following links."""
        with self._lock:
            self._assert_root()
            for path, identity in self._owned_regular_files(include_temps=True):
                self._safe_unlink_regular(path, expected=identity)

    @staticmethod
    def _filename(report: DiagnosticReport) -> str:
        timestamp = _as_utc(report.occurred_at).strftime("%Y%m%dT%H%M%S.%fZ")
        return f"{timestamp}-{report.event_id}.json"

    def _assert_root(self) -> None:
        try:
            identity = self.root.lstat()
        except OSError as exc:
            raise RuntimeError("diagnostic spool root is unavailable") from exc
        if stat.S_ISLNK(identity.st_mode) or not stat.S_ISDIR(identity.st_mode):
            raise RuntimeError("diagnostic spool root is not a safe directory")
        try:
            resolved = self.root.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("diagnostic spool root is unavailable") from exc
        if resolved != self.root:
            raise RuntimeError("diagnostic spool root changed unexpectedly")

    def _owned_regular_files(
        self,
        *,
        include_temps: bool,
    ) -> tuple[tuple[Path, os.stat_result], ...]:
        owned: list[tuple[Path, os.stat_result]] = []
        with os.scandir(self.root) as entries:
            for entry in entries:
                is_json = entry.name.endswith(".json")
                is_temp = entry.name.startswith(".") and entry.name.endswith(".tmp")
                if not is_json and not (include_temps and is_temp):
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    identity = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(identity.st_mode):
                    continue
                path = self.root / entry.name
                if path.parent != self.root:
                    continue
                owned.append((path, identity))
        return tuple(owned)

    def _prune_locked(self) -> tuple[_ValidatedFile, ...]:
        for path, identity in self._owned_regular_files(include_temps=True):
            if path.name.startswith(".") and path.name.endswith(".tmp"):
                self._safe_unlink_regular(path, expected=identity)

        cutoff = _as_utc(self._clock()) - MAX_AGE
        valid: list[_ValidatedFile] = []
        for path, identity in self._owned_regular_files(include_temps=False):
            item = self._read_validated(path, identity)
            if item is None:
                self._safe_unlink_regular(path, expected=identity)
                continue
            if _as_utc(item.queued.report.occurred_at) < cutoff:
                self._safe_unlink_regular(path, expected=item.identity)
                continue
            valid.append(item)

        valid.sort(
            key=lambda item: (
                _as_utc(item.queued.report.occurred_at),
                str(item.queued.report.event_id),
                item.queued.path.name,
            )
        )

        deduplicated: list[_ValidatedFile] = []
        fingerprints: set[str] = set()
        for item in valid:
            fingerprint = item.queued.report.fingerprint
            if fingerprint in fingerprints:
                self._safe_unlink_regular(item.queued.path, expected=item.identity)
                continue
            fingerprints.add(fingerprint)
            deduplicated.append(item)

        total_bytes = sum(item.size for item in deduplicated)
        excess = max(0, len(deduplicated) - MAX_EVENTS)
        remove_count = excess
        retained_bytes = total_bytes - sum(
            item.size for item in deduplicated[:remove_count]
        )
        while remove_count < len(deduplicated) and retained_bytes > MAX_TOTAL_BYTES:
            retained_bytes -= deduplicated[remove_count].size
            remove_count += 1

        for item in deduplicated[:remove_count]:
            self._safe_unlink_regular(item.queued.path, expected=item.identity)
        return tuple(deduplicated[remove_count:])

    def _read_validated(
        self,
        path: Path,
        identity: os.stat_result,
    ) -> _ValidatedFile | None:
        if identity.st_size > MAX_TOTAL_BYTES:
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            return None
        try:
            opened_identity = os.fstat(descriptor)
            if not stat.S_ISREG(opened_identity.st_mode):
                return None
            if not self._same_identity(identity, opened_identity):
                return None
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                payload = stream.read(MAX_TOTAL_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(payload) > MAX_TOTAL_BYTES:
            return None
        try:
            current_identity = path.lstat()
        except OSError:
            return None
        if stat.S_ISLNK(current_identity.st_mode) or not stat.S_ISREG(
            current_identity.st_mode
        ):
            return None
        if not self._same_identity(identity, current_identity):
            return None
        try:
            report = DiagnosticReport.model_validate_json(payload)
        except (TypeError, ValueError):
            return None
        return _ValidatedFile(
            queued=QueuedReport(report=report, path=path),
            size=len(payload),
            identity=opened_identity,
        )

    def _safe_unlink_regular(
        self,
        path: Path,
        *,
        expected: os.stat_result | None = None,
    ) -> bool:
        if path.parent != self.root:
            return False
        try:
            current = path.lstat()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
            return False
        if expected is not None and not self._same_identity(expected, current):
            return False
        try:
            path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    @staticmethod
    def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
        if os.name == "nt":
            # CPython obtains directory-entry/path and open-handle identities via
            # different Win32 APIs; their synthetic st_ino/st_dev values are not
            # guaranteed to agree. The caller checks the path for a reparse point
            # both before and after opening; size then rejects a changed payload.
            return left.st_size == right.st_size
        return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


__all__ = [
    "MAX_AGE",
    "MAX_EVENTS",
    "MAX_TOTAL_BYTES",
    "DiagnosticSpool",
    "EnqueueResult",
    "QueuedReport",
]
