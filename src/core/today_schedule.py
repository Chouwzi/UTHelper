from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from core.maintenance import MaintenanceNotice, UthMaintenanceError
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
    # Not persisted: Portal links embed short-lived personal tokens.
    link: str = ""
    meeting_url: str = ""
    subject_code: str = ""
    lecturer: str = ""
    has_outline: bool = False
    outline: str = ""

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
            subject_code=str(payload.get("subject_code", "")),
            lecturer=str(payload.get("lecturer", "")),
            has_outline=bool(payload.get("has_outline", False)),
            outline=str(payload.get("outline", "")),
        )

    def to_cache_dict(self) -> dict[str, Any]:
        """Like :meth:`to_dict` plus the display-only detail fields.

        ``link``/``meeting_url`` stay out of the cache on purpose (they carry
        short-lived personal tokens); a live refresh restores them.
        """
        payload = self.to_dict()
        payload.update(
            {
                "subject_code": self.subject_code,
                "lecturer": self.lecturer,
                "has_outline": self.has_outline,
                "outline": self.outline,
            }
        )
        return payload


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
    maintenance_notice: MaintenanceNotice | None = None


def select_spotlight_session(
    sessions: tuple[ClassSession, ...],
    now: datetime,
) -> ClassSession | None:
    """Return the class that is most relevant at ``now`` for the dashboard."""
    candidates = [
        session
        for session in sessions
        if session.phase_at(now)
        in {SchedulePhase.IN_PROGRESS, SchedulePhase.UPCOMING}
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda session: (
            0
            if session.phase_at(now) is SchedulePhase.IN_PROGRESS
            else 1,
            session.start_at,
            session.end_at,
            session.session_id,
        ),
    )


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return default


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
                cancelled=_parse_bool(row.get("isTamNgung", False)),
                note=str(row.get("ghiChu") or row.get("noteToDisplay") or "").strip(),
            )
        )
    sessions.sort(key=lambda item: (item.start_at, item.subject.casefold()))
    return tuple(sessions)


def _parse_row_date(row: dict[str, Any]) -> date | None:
    for key in ("ngayBatDauHoc", "ngayHoc", "ngay", "date"):
        value = row.get(key)
        if not isinstance(value, str):
            continue
        text = value.strip()[:10]
        for pattern in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, pattern).date()
            except ValueError:
                continue
    return None


def parse_portal_week(
    payload: Any, week_anchor: date
) -> dict[date, tuple[ClassSession, ...]]:
    """Parse ``lichhoc/lichTuan`` into sessions grouped by calendar date.

    The date of each row comes from ``ngayBatDauHoc`` (dd/MM/yyyy); rows
    without it fall back to ``thu`` (2=Monday ... 8=Sunday) of the Monday-based
    week containing ``week_anchor``.
    """
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("unsuccessful Portal schedule envelope")
    rows = payload.get("body")
    if not isinstance(rows, list):
        raise ValueError("Portal schedule body is not a list")
    monday = week_anchor - timedelta(days=week_anchor.weekday())
    grouped: dict[date, list[ClassSession]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _parse_row_date(row)
        if day is None:
            thu = _optional_int(row.get("thu"))
            if thu is None or not 2 <= thu <= 8:
                continue
            day = monday + timedelta(days=thu - 2)
        sessions = parse_portal_day(
            {"success": True, "body": [row]}, day
        )
        if not sessions:
            continue
        session = sessions[0]
        session = replace(
            session,
            link=str(row.get("link") or "").strip(),
            meeting_url=str(row.get("text1") or "").strip(),
            subject_code=str(row.get("maMonHoc") or "").strip(),
            lecturer=str(row.get("giangVien") or "").strip(),
            has_outline=bool(row.get("haveOutlineFile")),
            outline=str(row.get("outLineDescription") or "").strip(),
        )
        grouped.setdefault(day, []).append(session)
    return {
        day: tuple(
            sorted(items, key=lambda item: (item.start_at, item.subject.casefold()))
        )
        for day, items in grouped.items()
    }


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


WEEK_CACHE_RADIUS = 4  # weeks kept before and after the current week


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


@dataclass(frozen=True)
class WeekScheduleEntry:
    monday: date
    days: dict[date, tuple[ClassSession, ...]]
    fetched_at: datetime


class WeekScheduleCache:
    """Disk cache of weekly timetables (``lichhoc/lichTuan``).

    Keeps the current week plus ``WEEK_CACHE_RADIUS`` weeks on each side;
    everything else is pruned on save.
    """

    SCHEMA_VERSION = 1

    def __init__(self, cache_dir: Path | None = None, namespace: str = "") -> None:
        self._cache_dir = cache_dir
        self._path = self._resolve_path(namespace)

    def _resolve_path(self, namespace: str) -> Path:
        cache_dir = self._cache_dir
        if cache_dir is None:
            from config import _USER_DATA_DIR

            cache_dir = _USER_DATA_DIR
        digest = hashlib.sha256(
            namespace.strip().casefold().encode("utf-8")
        ).hexdigest()[:12]
        return cache_dir / f"week_schedule_{digest}.json"

    def set_namespace(self, namespace: str) -> None:
        self._path = self._resolve_path(namespace)

    @staticmethod
    def window(today: date | None = None) -> list[date]:
        """Mondays to cache, ordered by closeness to the current week."""
        base = monday_of(today or date.today())
        order = [base]
        for step in range(1, WEEK_CACHE_RADIUS + 1):
            order.append(base + timedelta(weeks=step))
            order.append(base - timedelta(weeks=step))
        return order

    def _read(self) -> dict[str, Any]:
        payload = SafeFileIO.read_json_safe(self._path, dict)
        if not isinstance(payload, dict) or payload.get("version") != self.SCHEMA_VERSION:
            return {}
        weeks = payload.get("weeks")
        return weeks if isinstance(weeks, dict) else {}

    @staticmethod
    def _decode(monday: date, raw: Any) -> WeekScheduleEntry | None:
        if not isinstance(raw, dict):
            return None
        try:
            fetched_at = datetime.fromisoformat(str(raw["fetched_at"]))
            days: dict[date, tuple[ClassSession, ...]] = {}
            for day_key, rows in (raw.get("days") or {}).items():
                days[date.fromisoformat(day_key)] = tuple(
                    ClassSession.from_dict(row) for row in rows if isinstance(row, dict)
                )
        except (KeyError, TypeError, ValueError, AttributeError):
            logger.warning("Week schedule cache entry is invalid")
            return None
        return WeekScheduleEntry(monday, days, fetched_at)

    def load_window(self, today: date | None = None) -> dict[date, WeekScheduleEntry]:
        weeks = self._read()
        entries: dict[date, WeekScheduleEntry] = {}
        for monday in self.window(today):
            entry = self._decode(monday, weeks.get(monday.isoformat()))
            if entry is not None:
                entries[monday] = entry
        return entries

    def save_week(
        self,
        monday: date,
        days: dict[date, tuple[ClassSession, ...]],
        fetched_at: datetime,
        today: date | None = None,
    ) -> bool:
        keep = {m.isoformat() for m in self.window(today)}
        if monday.isoformat() not in keep:
            return False  # outside the ±4 week window: memory only
        weeks = {k: v for k, v in self._read().items() if k in keep}
        weeks[monday.isoformat()] = {
            "fetched_at": fetched_at.isoformat(),
            "days": {
                day.isoformat(): [s.to_cache_dict() for s in sessions]
                for day, sessions in sorted(days.items())
            },
        }
        payload = {"version": self.SCHEMA_VERSION, "weeks": weeks}
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
        on_maintenance: Callable[[MaintenanceNotice], None] | None = None,
    ) -> None:
        self._fetch_day = fetch_day
        self._cache = cache
        self._credentials_provider = credentials_provider
        self._state_sink = state_sink
        self._now_provider = now_provider
        self._on_maintenance = on_maintenance
        self._guard = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        # Bumped by replace_cache(): an in-flight refresh started under a
        # previous account must discard its result instead of publishing a
        # stale READY into the new account's view.
        self._generation = 0
        self.state = TodayScheduleViewState(ScheduleLoadStatus.IDLE)

    def _capture_loop(self) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    def _signal_wake(self) -> None:
        loop = self._loop
        if loop is not None:
            if loop.is_closed():
                return
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not loop:
                try:
                    loop.call_soon_threadsafe(self._wake_event.set)
                except RuntimeError:
                    pass
                return
        try:
            self._wake_event.set()
        except RuntimeError:
            pass

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
        self._capture_loop()
        # The cache is only a valid stand-in for a live fetch while the same
        # account can still authenticate. With missing/blank credentials the
        # cached snapshot belongs to nobody, so fall through to refresh(),
        # which reports AUTH_REQUIRED instead of publishing a stale READY.
        generation = self._generation
        cache = self._cache
        target_date = self._now_provider().date()
        username, password = self._credentials_provider()
        cached = (
            await asyncio.to_thread(cache.load_for, target_date)
            if username and password
            else None
        )
        if self._generation != generation:
            return self.state
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
        self._capture_loop()
        async with self._guard:
            generation = self._generation
            cache = self._cache
            target_date = self._now_provider().date()
            existing = await asyncio.to_thread(cache.load_for, target_date)
            if self._generation != generation:
                return self.state
            # Credentials are validated BEFORE the startup cache shortcut: with
            # an absent/expired identity a cached snapshot can never be
            # refreshed, so serving it as READY would mask the login prompt.
            username, password = self._credentials_provider()
            if not username or not password:
                state = TodayScheduleViewState(
                    ScheduleLoadStatus.AUTH_REQUIRED,
                    snapshot=existing,
                    from_cache=existing is not None,
                )
                self._publish(state)
                return state
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
                if self._generation != generation:
                    # The account/cache changed while fetching (login or
                    # settings save): this snapshot belongs to the old
                    # account, so drop it without touching the new cache.
                    logger.info(
                        "Discarding stale schedule refresh (%s): account changed mid-flight",
                        trigger,
                    )
                    return self.state
                snapshot = TodayScheduleSnapshot(
                    target_date,
                    tuple(sessions),
                    self._now_provider(),
                )
                await asyncio.to_thread(cache.save, snapshot)
                if self._generation != generation:
                    return self.state
                state = TodayScheduleViewState(
                    ScheduleLoadStatus.READY,
                    snapshot=snapshot,
                )
            except Exception as exc:
                if self._generation != generation:
                    logger.info(
                        "Discarding stale schedule failure (%s): account changed mid-flight",
                        trigger,
                    )
                    return self.state
                maint_notice = (
                    exc.notice
                    if isinstance(exc, UthMaintenanceError)
                    else getattr(exc, "notice", None)
                )
                if maint_notice is not None:
                    logger.info(
                        "Portal schedule refresh deferred: UTH maintenance (%s)",
                        getattr(maint_notice, "window_text", "") or "maintenance",
                    )
                    state = TodayScheduleViewState(
                        ScheduleLoadStatus.ERROR,
                        snapshot=existing,
                        from_cache=existing is not None,
                        error_code="maintenance",
                        maintenance_notice=maint_notice,
                    )
                    self._publish(state)
                    if callable(self._on_maintenance):
                        try:
                            self._on_maintenance(maint_notice)
                        except Exception:
                            logger.debug("Today schedule maintenance hook failed", exc_info=True)
                    return state
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
        self._generation += 1
        self._signal_wake()

    def close(self) -> None:
        self._closed = True
        self._signal_wake()

    async def run(self) -> None:
        self._capture_loop()
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
