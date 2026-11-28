"""Atomic, reparse-safe, bounded local batch spool for PostHog analytics."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from analytics._fs_safety import (
    FileIdentity,
    assert_directory_identity,
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
from analytics.models import AnalyticsEvent, AnalyticsSpoolBatch

MAX_BATCHES = 20
MAX_EVENTS_PER_BATCH = 25
MAX_BATCH_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 512 * 1024
MAX_AGE = timedelta(hours=48)

# Reserve headroom for top-level {"api_key":"phc_<128>","batch":[...]} wire framing.
_WIRE_ENVELOPE_OVERHEAD_BYTES = 256


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class BatchEnqueueResult:
    """Outcome of attempting to persist one or more events into the local spool."""

    stored: bool = False
    batches_written: int = 0
    events_stored: int = 0
    deduplicated: bool = False
    too_large: bool = False


@dataclass(frozen=True, slots=True)
class QueuedBatch:
    """A validated batch envelope and its owned direct-child spool path."""

    batch: AnalyticsSpoolBatch
    path: Path


@dataclass(frozen=True, slots=True)
class _ValidatedBatchFile:
    queued: QueuedBatch
    size: int
    identity: FileIdentity


class AnalyticsSpool:
    """Bounded local queue at ``<appdata_dir>/analytics/pending/``.

    Enforces:
    - ``MAX_BATCHES = 20``
    - ``MAX_EVENTS_PER_BATCH = 25``
    - ``MAX_BATCH_BYTES = 64 KiB``
    - ``MAX_TOTAL_BYTES = 512 KiB``
    - ``MAX_AGE = 48h``
    - Reparse-point/symlink/junction rejection and handle identity verification.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        clock: Callable[[], datetime] = _utc_now,
        create_dir: bool = True,
    ) -> None:
        requested = Path(root)
        verify_non_reparse_target(
            requested,
            check_parent=True,
            label="analytics spool root",
        )
        self._requested_root = requested
        self._clock = clock
        self._lock = thread_lock_for(requested.absolute())
        self.root: Path = requested.absolute()
        self._root_identity: FileIdentity | None = None

        if create_dir or requested.exists():
            self._ensure_initialized()

    def _ensure_initialized(self) -> tuple[Path, FileIdentity]:
        if self._root_identity is not None:
            assert_directory_identity(
                self.root,
                expected=self._root_identity,
                label="analytics spool root",
            )
            return self.root, self._root_identity

        resolved_root, resolved_identity = ensure_safe_directory(
            self._requested_root,
            check_parent=True,
            label="analytics spool root",
        )
        self.root = resolved_root
        self._root_identity = resolved_identity
        return resolved_root, resolved_identity

    def _has_existing_root(self) -> bool:
        if self._root_identity is not None:
            return True
        if path_is_reparse(self._requested_root) or path_is_reparse(
            self._requested_root.parent
        ):
            raise ValueError(
                "analytics spool root must not be a symlink, junction, or reparse point"
            )
        return self._requested_root.exists()

    @contextmanager
    def _exclusive(self, *, create_if_missing: bool):
        with self._lock:
            if not create_if_missing and not self._has_existing_root():
                yield False
                return
            resolved_root, root_identity = self._ensure_initialized()
            with exclusive_directory_lock(
                resolved_root,
                expected=root_identity,
                lock_prefix="UTHelperAnalyticsSpool",
                lock_filename=".analytics-spool.lock",
                guard_filename=".analytics-spool.operation",
            ):
                assert_directory_identity(
                    resolved_root,
                    expected=root_identity,
                    label="analytics spool root",
                )
                yield True

    def enqueue(
        self,
        events: Sequence[AnalyticsEvent],
        *,
        fsync: bool = True,
    ) -> BatchEnqueueResult:
        """Validate, deduplicate, chunk (<=25 events / <=64 KiB), and persist batches."""
        if not events:
            return BatchEnqueueResult()

        validated_events: list[AnalyticsEvent] = []
        seen_incoming: set[str] = set()
        had_duplicate = False
        for candidate in events:
            if not isinstance(candidate, AnalyticsEvent):
                raise TypeError("all items must be validated AnalyticsEvent instances")
            revalidated = AnalyticsEvent.model_validate_json(
                candidate.model_dump_json()
            )
            if revalidated.uuid in seen_incoming:
                had_duplicate = True
                continue
            seen_incoming.add(revalidated.uuid)
            validated_events.append(revalidated)

        with self._exclusive(create_if_missing=True) as ready:
            assert ready and self._root_identity is not None
            current = self._prune_locked()
            existing_uuids = {
                ev.uuid
                for item in current
                for ev in item.queued.batch.events
            }
            fresh_events = [
                ev for ev in validated_events if ev.uuid not in existing_uuids
            ]
            if not fresh_events:
                return BatchEnqueueResult(deduplicated=True)
            if len(fresh_events) < len(validated_events):
                had_duplicate = True

            now = _as_utc(self._clock())
            if current:
                latest_existing = max(
                    _as_utc(item.queued.batch.created_at) for item in current
                )
                if now <= latest_existing:
                    now = latest_existing + timedelta(microseconds=1)
            chunks, any_too_large = self._chunk_events(fresh_events, created_at=now)
            if not chunks:
                return BatchEnqueueResult(
                    deduplicated=had_duplicate,
                    too_large=any_too_large,
                )

            written_ids: dict[UUID, Path] = {}
            for batch_obj, payload_bytes in chunks:
                filename = self._filename(batch_obj)
                atomic_write_regular(
                    self.root,
                    dir_identity=self._root_identity,
                    filename=filename,
                    payload=payload_bytes,
                    fsync=fsync,
                )
                written_ids[batch_obj.batch_id] = self.root / filename

            remaining = self._prune_locked()
            retained_batches = [
                item.queued.batch
                for item in remaining
                if item.queued.batch.batch_id in written_ids
                and item.queued.path == written_ids[item.queued.batch.batch_id]
            ]
            events_stored = sum(len(b.events) for b in retained_batches)
            return BatchEnqueueResult(
                stored=bool(retained_batches),
                batches_written=len(retained_batches),
                events_stored=events_stored,
                deduplicated=had_duplicate and not retained_batches,
                too_large=any_too_large,
            )

    def pending(self) -> tuple[QueuedBatch, ...]:
        """Return validated pending batches in deterministic oldest-first order."""
        with self._exclusive(create_if_missing=False) as ready:
            if not ready:
                return ()
            return tuple(item.queued for item in self._prune_locked())

    def acknowledge(self, batch_id: UUID) -> None:
        """Delete only the validated spool file matching ``batch_id``."""
        if not isinstance(batch_id, UUID):
            raise TypeError("batch_id must be a UUID")
        with self._exclusive(create_if_missing=False) as ready:
            if not ready:
                return
            for item in self._prune_locked():
                if item.queued.batch.batch_id == batch_id:
                    self._safe_unlink_regular(
                        item.queued.path,
                        expected=item.identity,
                    )

    def drop_poison(self, batch_id: UUID) -> None:
        """Drop a rejected (HTTP 400/413) poison batch by ``batch_id``."""
        self.acknowledge(batch_id)

    def clear(self) -> None:
        """Delete all owned direct regular JSON and temp children without following links."""
        with self._exclusive(create_if_missing=False) as ready:
            if not ready:
                return
            for path, identity in self._owned_regular_files(include_temps=True):
                self._safe_unlink_regular(path, expected=identity)

    @staticmethod
    def _filename(batch: AnalyticsSpoolBatch) -> str:
        timestamp = _as_utc(batch.created_at).strftime("%Y%m%dT%H%M%S.%fZ")
        return f"{timestamp}-{batch.batch_id}.json"

    @staticmethod
    def _wire_events_bytes(events: Sequence[AnalyticsEvent]) -> int:
        encoded = json.dumps(
            [ev.to_wire_dict() for ev in events],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return len(encoded) + _WIRE_ENVELOPE_OVERHEAD_BYTES

    @classmethod
    def _serialize_batch(cls, batch: AnalyticsSpoolBatch) -> bytes:
        payload = (
            json.dumps(
                batch.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        AnalyticsSpoolBatch.model_validate_json(payload)
        return payload

    @classmethod
    def _chunk_events(
        cls,
        events: Sequence[AnalyticsEvent],
        *,
        created_at: datetime,
    ) -> tuple[list[tuple[AnalyticsSpoolBatch, bytes]], bool]:
        chunks: list[tuple[AnalyticsSpoolBatch, bytes]] = []
        current_group: list[AnalyticsEvent] = []
        any_too_large = False

        def flush_group(group: list[AnalyticsEvent], offset_us: int) -> None:
            nonlocal any_too_large
            batch_obj = AnalyticsSpoolBatch(
                batch_id=uuid4(),
                created_at=created_at + timedelta(microseconds=offset_us),
                events=tuple(group),
            )
            payload = cls._serialize_batch(batch_obj)
            if (
                len(payload) > MAX_BATCH_BYTES
                or cls._wire_events_bytes(group) > MAX_BATCH_BYTES
            ):
                any_too_large = True
                return
            chunks.append((batch_obj, payload))

        for ev in events:
            if cls._wire_events_bytes([ev]) > MAX_BATCH_BYTES:
                any_too_large = True
                continue
            tentative = [*current_group, ev]
            fits_batch = False
            if (
                len(tentative) <= MAX_EVENTS_PER_BATCH
                and cls._wire_events_bytes(tentative) <= MAX_BATCH_BYTES
            ):
                tentative_batch = AnalyticsSpoolBatch(
                    batch_id=uuid4(),
                    created_at=created_at,
                    events=tuple(tentative),
                )
                tentative_bytes = cls._serialize_batch(tentative_batch)
                fits_batch = len(tentative_bytes) <= MAX_BATCH_BYTES

            if fits_batch:
                current_group.append(ev)
            else:
                if current_group:
                    flush_group(current_group, len(chunks))
                current_group = [ev]

        if current_group:
            flush_group(current_group, len(chunks))

        return chunks, any_too_large

    def _owned_regular_files(
        self,
        *,
        include_temps: bool,
    ) -> tuple[tuple[Path, FileIdentity], ...]:
        owned: list[tuple[Path, FileIdentity]] = []
        with os.scandir(self.root) as entries:
            for entry in entries:
                is_json = entry.name.endswith(".json")
                is_temp = entry.name.startswith(".") and entry.name.endswith(".tmp")
                if not is_json and not (include_temps and is_temp):
                    continue
                path = self.root / entry.name
                if path.parent != self.root:
                    continue
                try:
                    identity = path_identity(path, expect_directory=False)
                except (OSError, ValueError):
                    continue
                owned.append((path, identity))
        return tuple(owned)

    def _prune_locked(self) -> tuple[_ValidatedBatchFile, ...]:
        for path, identity in self._owned_regular_files(include_temps=True):
            if path.name.startswith(".") and path.name.endswith(".tmp"):
                self._safe_unlink_regular(path, expected=identity)

        cutoff = _as_utc(self._clock()) - MAX_AGE
        valid: list[_ValidatedBatchFile] = []
        for path, identity in self._owned_regular_files(include_temps=False):
            item = self._read_validated(path, identity)
            if item is None:
                self._safe_unlink_regular(path, expected=identity)
                continue
            batch = item.queued.batch
            if _as_utc(batch.created_at) < cutoff or any(
                ev.timestamp_utc() < cutoff for ev in batch.events
            ):
                self._safe_unlink_regular(path, expected=item.identity)
                continue
            valid.append(item)

        valid.sort(
            key=lambda item: (
                _as_utc(item.queued.batch.created_at),
                str(item.queued.batch.batch_id),
                item.queued.path.name,
            )
        )

        seen_batch_ids: set[UUID] = set()
        deduplicated: list[_ValidatedBatchFile] = []
        for item in valid:
            bid = item.queued.batch.batch_id
            if bid in seen_batch_ids:
                self._safe_unlink_regular(item.queued.path, expected=item.identity)
                continue
            seen_batch_ids.add(bid)
            deduplicated.append(item)

        total_bytes = sum(item.size for item in deduplicated)
        remove_count = max(0, len(deduplicated) - MAX_BATCHES)
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
        identity: FileIdentity,
    ) -> _ValidatedBatchFile | None:
        if identity.size > MAX_BATCH_BYTES:
            return None
        opened = open_binary_no_follow(path, expected=identity)
        if opened is None:
            return None
        stream, opened_identity = opened
        with stream:
            payload = stream.read(MAX_BATCH_BYTES + 1)
        if len(payload) > MAX_BATCH_BYTES:
            return None
        try:
            current_identity = path_identity(path, expect_directory=False)
        except (OSError, ValueError):
            return None
        if current_identity.key != identity.key:
            return None
        try:
            batch = AnalyticsSpoolBatch.model_validate_json(payload)
        except (TypeError, ValueError):
            return None
        if self._wire_events_bytes(batch.events) > MAX_BATCH_BYTES:
            return None
        return _ValidatedBatchFile(
            queued=QueuedBatch(batch=batch, path=path),
            size=len(payload),
            identity=opened_identity,
        )

    def _safe_unlink_regular(
        self,
        path: Path,
        *,
        expected: FileIdentity | None = None,
    ) -> bool:
        if path.parent != self.root:
            return False
        return delete_exact_regular(path, expected=expected)


__all__ = [
    "AnalyticsSpool",
    "BatchEnqueueResult",
    "MAX_AGE",
    "MAX_BATCHES",
    "MAX_BATCH_BYTES",
    "MAX_EVENTS_PER_BATCH",
    "MAX_TOTAL_BYTES",
    "QueuedBatch",
]
