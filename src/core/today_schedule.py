from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from core.safe_file_io import SafeFileIO

logger = logging.getLogger(__name__)


class SchedulePhase(str, Enum):
    UPCOMING = "upcoming"
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class ScheduleLoadStatus(str, Enum):
    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    AUTH_REQUIRED = "auth_required"
    ERROR = "error"


class ScheduleAuthenticationError(RuntimeError):
    """Credential rejection recognized by schedule coordinators."""


@dataclass(frozen=True)
class ClassSession:
    session_id: str
    subject: str
    course_code: str
    start_at: datetime
    end_at: datetime
    room: str
    campus: str
    period_start: int | None
    period_end: int | None
    cancelled: bool
    note: str

    def phase_at(self, now: datetime) -> SchedulePhase:
        if self.cancelled:
            return SchedulePhase.CANCELLED
        if now < self.start_at:
            return SchedulePhase.UPCOMING
        if now < self.end_at:
            return SchedulePhase.IN_PROGRESS
        return SchedulePhase.FINISHED

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "subject": self.subject,
            "course_code": self.course_code,
            "start_at": self.start_at.isoformat(),
            "end_at": self.end_at.isoformat(),
            "room": self.room,
            "campus": self.campus,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "cancelled": self.cancelled,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ClassSession:
        return cls(
            session_id=str(payload.get("session_id", "")),
            subject=str(payload.get("subject", "")),
            course_code=str(payload.get("course_code", "")),
            start_at=datetime.fromisoformat(str(payload["start_at"])),
            end_at=datetime.fromisoformat(str(payload["end_at"])),
            room=str(payload.get("room", "")),
            campus=str(payload.get("campus", "")),
            period_start=_optional_int(payload.get("period_start")),
            period_end=_optional_int(payload.get("period_end")),
            cancelled=bool(payload.get("cancelled", False)),
            note=str(payload.get("note", "")),
        )


@dataclass(frozen=True)
class TodayScheduleSnapshot:
    schedule_date: date
    sessions: tuple[ClassSession, ...]
    fetched_at: datetime


@dataclass(frozen=True)
class TodayScheduleViewState:
    status: ScheduleLoadStatus
    snapshot: TodayScheduleSnapshot | None = None
    from_cache: bool = False
    error_code: str = ""


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_clock(value: Any) -> time | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(normalized, pattern).time()
        except ValueError:
            continue
    return None


def parse_portal_day(
    payload: Any, target_date: date
) -> tuple[ClassSession, ...]:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("unsuccessful Portal schedule envelope")
    rows = payload.get("body")
    if not isinstance(rows, list):
        raise ValueError("Portal schedule body is not a list")
    sessions: list[ClassSession] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        start_clock = _parse_clock(row.get("tuGio"))
        end_clock = _parse_clock(row.get("denGio"))
        subject = str(row.get("tenMonHoc") or row.get("nameToDisplay") or "").strip()
        if not subject or start_clock is None or end_clock is None:
            continue
        start_at = datetime.combine(target_date, start_clock)
        end_at = datetime.combine(target_date, end_clock)
        if end_at <= start_at:
            end_at += timedelta(days=1)
        session_id = str(
            row.get("id")
            or f"{row.get('maLopHocPhan', '')}|{start_at.isoformat()}|{subject}"
        )
        sessions.append(
            ClassSession(
                session_id=session_id,
                subject=subject,
                course_code=str(row.get("maLopHocPhan") or "").strip(),
                start_at=start_at,
                end_at=end_at,
                room=str(row.get("tenPhong") or row.get("roomToDisplay") or "").strip(),
                campus=str(row.get("coSoToDisplay") or "").strip(),
                period_start=_optional_int(row.get("tuTiet")),
                period_end=_optional_int(row.get("denTiet")),
                cancelled=bool(row.get("isTamNgung", False)),
                note=str(row.get("ghiChu") or row.get("noteToDisplay") or "").strip(),
            )
        )
    sessions.sort(key=lambda item: (item.start_at, item.subject.casefold()))
    return tuple(sessions)


class TodayScheduleCache:
    SCHEMA_VERSION = 1

    def __init__(self, cache_dir: Path | None = None, namespace: str = "") -> None:
        if cache_dir is None:
            from config import _USER_DATA_DIR

            cache_dir = _USER_DATA_DIR
        digest = hashlib.sha256(namespace.strip().casefold().encode("utf-8")).hexdigest()[:12]
        self._path = cache_dir / f"today_schedule_{digest}.json"

    def load_for(self, target_date: date) -> TodayScheduleSnapshot | None:
        payload = SafeFileIO.read_json_safe(self._path, dict)
        if not isinstance(payload, dict) or payload.get("version") != self.SCHEMA_VERSION:
            return None
        if payload.get("schedule_date") != target_date.isoformat():
            return None
        rows = payload.get("sessions")
        if not isinstance(rows, list):
            return None
        try:
            sessions = tuple(
                ClassSession.from_dict(row) for row in rows if isinstance(row, dict)
            )
            fetched_at = datetime.fromisoformat(str(payload["fetched_at"]))
        except (KeyError, TypeError, ValueError):
            logger.warning("Today schedule cache is invalid")
            return None
        return TodayScheduleSnapshot(target_date, sessions, fetched_at)

    def save(self, snapshot: TodayScheduleSnapshot) -> bool:
        payload = {
            "version": self.SCHEMA_VERSION,
            "schedule_date": snapshot.schedule_date.isoformat(),
            "fetched_at": snapshot.fetched_at.isoformat(),
            "count": len(snapshot.sessions),
            "sessions": [session.to_dict() for session in snapshot.sessions],
        }
        return bool(SafeFileIO.write_json_atomic(self._path, payload))


def next_schedule_refresh_at(now: datetime) -> datetime:
    midnight = datetime.combine(now.date(), time(0, 0))
    six_am = datetime.combine(now.date(), time(6, 0))
    if now < six_am:
        return six_am
    return midnight + timedelta(days=1)


class TodayScheduleCoordinator:
    """Own daily Portal cache, startup fetch, and 00:00/06:00 refreshes."""

    def __init__(
        self,
        *,
        fetch_day: Callable[[date, str, str], tuple[ClassSession, ...]],
        cache: TodayScheduleCache,
        credentials_provider: Callable[[], tuple[str, str]],
        state_sink: Callable[[TodayScheduleViewState], None],
        now_provider: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._fetch_day = fetch_day
        self._cache = cache
        self._credentials_provider = credentials_provider
        self._state_sink = state_sink
        self._now_provider = now_provider
        self._guard = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._closed = False
        self.state = TodayScheduleViewState(ScheduleLoadStatus.IDLE)

    def _publish(self, state: TodayScheduleViewState) -> None:
        self.state = state
        self._state_sink(state)

    def cached_state(self) -> TodayScheduleViewState:
        snapshot = self._cache.load_for(self._now_provider().date())
        if snapshot is None:
            return TodayScheduleViewState(ScheduleLoadStatus.IDLE)
        return TodayScheduleViewState(
            ScheduleLoadStatus.READY,
            snapshot=snapshot,
            from_cache=True,
        )

    async def ensure_today(self) -> TodayScheduleViewState:
        cached = self._cache.load_for(self._now_provider().date())
        if cached is not None:
            state = TodayScheduleViewState(
                ScheduleLoadStatus.READY,
                snapshot=cached,
                from_cache=True,
            )
            self._publish(state)
            return state
        return await self.refresh("startup")

    async def refresh(self, trigger: str = "manual") -> TodayScheduleViewState:
        async with self._guard:
            target_date = self._now_provider().date()
            existing = self._cache.load_for(target_date)
            # Startup and a fast first expansion can race before either task
            # observes the cache. The second task re-checks under the guard and
            # publishes the completed first result instead of logging in twice.
            if trigger == "startup" and existing is not None:
                state = TodayScheduleViewState(
                    ScheduleLoadStatus.READY,
                    snapshot=existing,
                    from_cache=True,
                )
                self._publish(state)
                return state
            username, password = self._credentials_provider()
            if not username or not password:
                state = TodayScheduleViewState(
                    ScheduleLoadStatus.AUTH_REQUIRED,
                    snapshot=existing,
                    from_cache=existing is not None,
                )
                self._publish(state)
                return state
            self._publish(
                TodayScheduleViewState(
                    ScheduleLoadStatus.LOADING,
                    snapshot=existing,
                    from_cache=existing is not None,
                )
            )
            try:
                sessions = await asyncio.to_thread(
                    self._fetch_day,
                    target_date,
                    username,
                    password,
                )
                snapshot = TodayScheduleSnapshot(
                    target_date,
                    tuple(sessions),
                    self._now_provider(),
                )
                self._cache.save(snapshot)
                state = TodayScheduleViewState(
                    ScheduleLoadStatus.READY,
                    snapshot=snapshot,
                )
            except Exception as exc:
                auth_failure = isinstance(exc, ScheduleAuthenticationError)
                logger.warning(
                    "Portal schedule refresh failed (%s)",
                    "authentication" if auth_failure else "request",
                )
                state = TodayScheduleViewState(
                    ScheduleLoadStatus.AUTH_REQUIRED if auth_failure else ScheduleLoadStatus.ERROR,
                    snapshot=existing,
                    from_cache=existing is not None,
                    error_code="authentication" if auth_failure else "request_failed",
                )
            self._publish(state)
            return state

    def replace_cache(self, cache: TodayScheduleCache) -> None:
        self._cache = cache
        self._wake_event.set()

    def close(self) -> None:
        self._closed = True
        self._wake_event.set()

    async def run(self) -> None:
        try:
            await self.ensure_today()
            while not self._closed:
                now = self._now_provider()
                delay = max(
                    0.0,
                    (next_schedule_refresh_at(now) - now).total_seconds(),
                )
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    await self.refresh("scheduled")
        except asyncio.CancelledError:
            self.close()
            raise
