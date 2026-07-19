from __future__ import annotations

import logging
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_activities(activities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    volatile = {
        "_deadline_dt",
        "_title_lower",
        "_course_lower",
        "_remaining_seconds",
        "_overdue",
        "urgency",
    }
    return [
        {key: value for key, value in dict(activity).items() if key not in volatile}
        for activity in activities
    ]


@dataclass(frozen=True)
class ActivitySnapshot:
    """A persisted activity snapshot with separate local/server timestamps."""

    activities: List[Dict[str, Any]] = field(default_factory=list)
    generation: int = 0
    updated_at: Optional[str] = None
    last_successful_sync_at: Optional[str] = None
    last_attempt_at: Optional[str] = None
    last_error: Optional[str] = None


class DataCache:
    """Versioned offline activity repository backed by atomic JSON writes."""

    SCHEMA_VERSION = 2

    def __init__(self, cache_dir: Optional[Path] = None, namespace: Optional[str] = None):
        if cache_dir is None:
            from config import _USER_DATA_DIR

            cache_dir = _USER_DATA_DIR
        self._legacy_cache_path = cache_dir / "activities_cache.json"
        if namespace:
            digest = hashlib.sha256(namespace.strip().lower().encode("utf-8")).hexdigest()[:12]
            self._cache_path = cache_dir / f"activities_cache_{digest}.json"
        else:
            self._cache_path = self._legacy_cache_path

    def _read_payload(self) -> dict:
        from core.safe_file_io import SafeFileIO

        return SafeFileIO.read_json_safe(self._cache_path, dict)

    @staticmethod
    def _snapshot_from_payload(payload: dict) -> ActivitySnapshot:
        if not isinstance(payload, dict):
            return ActivitySnapshot()
        activities = payload.get("activities", [])
        if not isinstance(activities, list):
            activities = []
        version = int(payload.get("version", 1) or 1)
        saved_at = payload.get("saved_at")
        if version < 2:
            # A v1 save only happened after a full fetch or a local mutation.
            # Treat it as the best available server cursor once, then v2 keeps
            # local updates from moving this timestamp again.
            return ActivitySnapshot(
                activities=activities,
                generation=0,
                updated_at=saved_at,
                last_successful_sync_at=saved_at,
            )
        return ActivitySnapshot(
            activities=activities,
            generation=int(payload.get("generation", 0) or 0),
            updated_at=payload.get("updated_at") or saved_at,
            last_successful_sync_at=payload.get("last_successful_sync_at"),
            last_attempt_at=payload.get("last_attempt_at"),
            last_error=payload.get("last_error"),
        )

    def load_snapshot(self) -> ActivitySnapshot:
        # One-time compatibility migration into the current account namespace.
        if not self._cache_path.exists() and self._cache_path != self._legacy_cache_path:
            if self._legacy_cache_path.exists():
                from core.safe_file_io import SafeFileIO

                payload = SafeFileIO.read_json_safe(self._legacy_cache_path, dict)
                if payload and SafeFileIO.write_json_atomic(self._cache_path, payload):
                    try:
                        self._legacy_cache_path.unlink()
                    except OSError:
                        logger.warning("Không thể xóa cache legacy sau migration")
        if not self._cache_path.exists():
            return ActivitySnapshot()
        try:
            snapshot = self._snapshot_from_payload(self._read_payload())
            logger.info(
                "Đã tải cache %d activities (server sync lúc %s)",
                len(snapshot.activities),
                snapshot.last_successful_sync_at,
            )
            return snapshot
        except Exception as exc:
            logger.warning("Không thể đọc data cache: %s", exc)
            return ActivitySnapshot()

    def _write(self, activities: List[Dict[str, Any]], *, server_sync: bool) -> bool:
        from core.safe_file_io import SafeFileIO

        now = _utc_now_iso()
        persisted_activities = _sanitize_activities(activities)

        def build_payload() -> dict:
            current = self._snapshot_from_payload(self._read_payload())
            return {
                "version": self.SCHEMA_VERSION,
                "generation": current.generation + 1,
                "updated_at": now,
                "last_successful_sync_at": (
                    now if server_sync else current.last_successful_sync_at
                ),
                "last_attempt_at": now if server_sync else current.last_attempt_at,
                "last_error": None if server_sync else current.last_error,
                "count": len(persisted_activities),
                "activities": persisted_activities,
            }

        try:
            written = SafeFileIO.write_json_atomic(self._cache_path, build_payload)
            if written:
                logger.debug("Lưu cache %d activities", len(activities))
            return bool(written)
        except Exception as exc:
            logger.warning("Không thể lưu data cache: %s", exc)
            return False

    def save_server_snapshot(self, activities: List[Dict[str, Any]]) -> bool:
        """Commit an authoritative successful Moodle fetch."""
        return self._write(activities, server_sync=True)

    def save_local_activities(self, activities: List[Dict[str, Any]]) -> bool:
        """Persist a local mutation without making the server cursor fresher."""
        return self._write(activities, server_sync=False)

    def record_failed_attempt(self, error: str) -> bool:
        """Record diagnostics while preserving the last good activity snapshot."""
        from core.safe_file_io import SafeFileIO

        now = _utc_now_iso()

        def build_payload() -> dict:
            current = self._snapshot_from_payload(self._read_payload())
            return {
                "version": self.SCHEMA_VERSION,
                "generation": current.generation,
                "updated_at": current.updated_at,
                "last_successful_sync_at": current.last_successful_sync_at,
                "last_attempt_at": now,
                "last_error": str(error),
                "count": len(current.activities),
                "activities": current.activities,
            }

        return bool(SafeFileIO.write_json_atomic(self._cache_path, build_payload))

    def save(self, activities: List[Dict[str, Any]]) -> None:
        """Backward-compatible alias for an authoritative server snapshot."""
        self.save_server_snapshot(activities)

    def load(self) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Backward-compatible tuple API used by existing views/tests."""
        snapshot = self.load_snapshot()
        return snapshot.activities, snapshot.last_successful_sync_at

    def clear(self) -> None:
        from core.safe_file_io import SafeFileIO

        lock = SafeFileIO.get_file_lock(self._cache_path)
        try:
            with lock.acquire(timeout=2):
                if self._cache_path.exists():
                    self._cache_path.unlink()
                lock_file = self._cache_path.with_suffix(".lock")
                if lock_file.exists():
                    try:
                        lock_file.unlink()
                    except OSError:
                        pass
        except Exception:
            pass
