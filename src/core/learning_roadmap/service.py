"""Loads the roadmap with an offline-first cache.

Policy
------
* Fresh cache (< ``ttl_hours``) and no force-refresh  -> use it, no network.
* Otherwise fetch from Portal; on success rewrite the cache.
* Fetch fails (offline / Portal down)                  -> serve the stale cache
  and say so (``source == "offline_cache"``); never an empty screen if we have
  *any* cached data.
* No credentials and no cache                           -> ``source == "none"``.

The cache stores the *raw* Portal bodies (never credentials or tokens), and the
snapshot is rebuilt from them on every load, so improvements to the parsers
apply to already-cached data without another network round trip.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from core.learning_roadmap.builder import build_snapshot
from core.learning_roadmap.models import RoadmapSnapshot
from core.safe_file_io import SafeFileIO

logger = logging.getLogger(__name__)

CACHE_SCHEMA = 1
CACHE_FILE_NAME = "learning_roadmap_cache.json"

SOURCE_NETWORK = "network"
SOURCE_CACHE = "cache"
SOURCE_OFFLINE_CACHE = "offline_cache"
SOURCE_NONE = "none"


@dataclass(frozen=True)
class RoadmapLoadResult:
    snapshot: Optional[RoadmapSnapshot]
    source: str
    fetched_at: Optional[datetime] = None
    error: str = ""

    @property
    def is_offline(self) -> bool:
        return self.source == SOURCE_OFFLINE_CACHE


class LearningRoadmapService:
    def __init__(
        self,
        cache_file: Path,
        portal_client: Any = None,
        *,
        ttl_hours: float = 12.0,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._client = portal_client
        self._cache_file = Path(cache_file)
        self._ttl = timedelta(hours=ttl_hours)
        self._clock = clock

    # ------------------------------------------------------------------ cache
    def _read_cache(self) -> Optional[dict[str, Any]]:
        data = SafeFileIO.read_json_safe(self._cache_file, default_factory=dict)
        if (
            isinstance(data, dict)
            and data.get("schema") == CACHE_SCHEMA
            and isinstance(data.get("bundle"), dict)
            and isinstance(data.get("fetched_at"), str)
        ):
            return data
        return None

    def _write_cache(self, bundle: dict[str, Any], fetched_at: datetime) -> None:
        payload = {
            "schema": CACHE_SCHEMA,
            "fetched_at": fetched_at.isoformat(),
            "bundle": bundle,
        }
        try:
            if not SafeFileIO.write_json_atomic(self._cache_file, payload):
                logger.warning("Roadmap cache write reported failure")
        except Exception as exc:  # cache is best-effort
            logger.warning("Roadmap cache write failed: %s", exc)

    def clear_cache(self) -> None:
        """Remove this account's persisted roadmap snapshot on logout/switch."""
        try:
            self._cache_file.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Roadmap cache removal failed: %s", exc)

    @staticmethod
    def _parse_time(raw: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    @staticmethod
    def _keep_cached_for_failures(
        bundle: dict[str, Any], previous: dict[str, Any]
    ) -> dict[str, Any]:
        """A partial fetch must not degrade a good cache: for every endpoint that
        failed this time, carry over the previously cached body. The error stays
        recorded so the UI can say some data was not refreshed."""
        errors = bundle.get("errors") or {}
        bodies = dict(bundle.get("bodies") or {})
        old_bodies = previous.get("bodies") or {}
        for key in errors:
            if key == "transcript":
                if not bundle.get("transcript") and previous.get("transcript"):
                    bundle["transcript"] = previous["transcript"]
            elif key in ("semesters", "current_semester"):
                if not bundle.get("current_semester") and previous.get("current_semester"):
                    bundle["current_semester"] = previous["current_semester"]
            elif bodies.get(key) is None and old_bodies.get(key) is not None:
                bodies[key] = old_bodies[key]
        bundle["bodies"] = bodies
        return bundle

    # ----------------------------------------------------------------- loading
    def _from_cache(self, cache: dict[str, Any], source: str, error: str = "") -> RoadmapLoadResult:
        fetched_at = self._parse_time(cache["fetched_at"])
        try:
            snapshot = build_snapshot(cache["bundle"], now=fetched_at)
        except Exception as exc:
            logger.warning("Roadmap cache could not be interpreted: %s", exc)
            return RoadmapLoadResult(None, SOURCE_NONE, None, error or "Dữ liệu đã lưu không hợp lệ")
        return RoadmapLoadResult(snapshot, source, fetched_at, error)

    def load(
        self,
        username: str = "",
        password: str = "",
        force_refresh: bool = False,
    ) -> RoadmapLoadResult:
        cache = self._read_cache()
        if cache and not force_refresh:
            fetched = self._parse_time(cache["fetched_at"])
            if fetched and self._clock() - fetched < self._ttl:
                return self._from_cache(cache, SOURCE_CACHE)

        error = ""
        if username and password and self._client is not None:
            try:
                bundle = self._client.fetch_roadmap_bundle(username, password)
                if cache:
                    bundle = self._keep_cached_for_failures(bundle, cache["bundle"])
                fetched_at = self._clock()
                snapshot = build_snapshot(bundle, now=fetched_at)
                self._write_cache(bundle, fetched_at)
                return RoadmapLoadResult(snapshot, SOURCE_NETWORK, fetched_at)
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
                logger.warning("Roadmap fetch failed (%s); trying cache", error)
        else:
            error = "Chưa đăng nhập Portal"

        if cache:
            return self._from_cache(cache, SOURCE_OFFLINE_CACHE, error)
        return RoadmapLoadResult(None, SOURCE_NONE, None, error)
