from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional


@dataclass(frozen=True)
class FetchOutcome:
    """Typed Moodle fetch result; partial snapshots are never authoritative."""

    success: bool
    snapshot_complete: bool
    activities: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def can_commit(self) -> bool:
        return self.success and self.snapshot_complete

    @classmethod
    def complete(cls, activities: list[dict[str, Any]]) -> "FetchOutcome":
        return cls(True, True, list(activities))

    @classmethod
    def failed(cls, error: str) -> "FetchOutcome":
        return cls(False, False, [], str(error))

    @classmethod
    def partial(
        cls, activities: list[dict[str, Any]], error: str
    ) -> "FetchOutcome":
        return cls(False, False, list(activities), str(error))


@dataclass(frozen=True)
class SyncRequestResult:
    trigger: str
    started: bool
    coalesced: bool = False
    skipped_not_due: bool = False
    value: Any = None


def parse_timestamp(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.timestamp()
    except (TypeError, ValueError, OSError):
        return None


def next_sync_at(
    *,
    opened_at: float,
    last_successful_sync_at: Optional[float],
    interval_minutes: int,
    startup_grace_seconds: int = 60,
) -> Optional[float]:
    """Calculate startup due time without making cache display wait on I/O."""
    interval = max(0, int(interval_minutes))
    if interval == 0:
        return None
    earliest = opened_at + max(0, startup_grace_seconds)
    if last_successful_sync_at is None:
        return earliest
    return max(earliest, last_successful_sync_at + interval * 60)


class ActivitySyncCoordinator:
    """Coalesce foreground/timer triggers and dynamically reschedule on wake."""

    def __init__(
        self,
        sync_once: Callable[[str], Awaitable[Any]],
        interval_provider: Callable[[], int],
        last_success_provider: Callable[[], Optional[float]],
        *,
        startup_grace_seconds: int = 60,
        retry_delay_seconds: int = 60,
        clock: Callable[[], float] = time.time,
    ):
        self._sync_once = sync_once
        self._interval_provider = interval_provider
        self._last_success_provider = last_success_provider
        self._startup_grace_seconds = startup_grace_seconds
        self._retry_delay_seconds = max(1, retry_delay_seconds)
        self._clock = clock
        self._opened_at = clock()
        self._guard = asyncio.Lock()
        self._in_flight: Optional[asyncio.Task] = None
        self._wake_event = asyncio.Event()
        self._closed = False
        self._last_attempt_at: Optional[float] = None

    def wake(self) -> None:
        """Recompute the next due time after settings/data changes."""
        self._wake_event.set()

    def close(self) -> None:
        self._closed = True
        self._wake_event.set()

    def due_at(self) -> Optional[float]:
        due_at = next_sync_at(
            opened_at=self._opened_at,
            last_successful_sync_at=self._last_success_provider(),
            interval_minutes=self._interval_provider(),
            startup_grace_seconds=self._startup_grace_seconds,
        )
        if due_at is not None and self._last_attempt_at is not None:
            due_at = max(due_at, self._last_attempt_at + self._retry_delay_seconds)
        return due_at

    async def request(self, trigger: str, *, force: bool = False) -> SyncRequestResult:
        now = self._clock()
        due_at = self.due_at()
        if not force and (due_at is None or now < due_at):
            return SyncRequestResult(trigger, False, skipped_not_due=True)

        async with self._guard:
            coalesced = self._in_flight is not None and not self._in_flight.done()
            if not coalesced:
                self._last_attempt_at = self._clock()
                self._in_flight = asyncio.create_task(self._sync_once(trigger))
            task = self._in_flight

        try:
            value = await asyncio.shield(task)
            return SyncRequestResult(trigger, not coalesced, coalesced, value=value)
        finally:
            async with self._guard:
                if self._in_flight is task and task.done():
                    self._in_flight = None
            self.wake()

    async def run(self) -> None:
        """Run until closed, waking promptly when the interval changes."""
        try:
            while not self._closed:
                due_at = self.due_at()
                if due_at is None:
                    timeout = None
                else:
                    timeout = max(0.0, due_at - self._clock())

                self._wake_event.clear()
                try:
                    if timeout is None:
                        await self._wake_event.wait()
                    else:
                        await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    await self.request("timer")
        except asyncio.CancelledError:
            self.close()
            raise
