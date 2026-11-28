"""Foreground session state machine, debouncing, and rate-budget enforcement."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import threading
import time
from typing import Any

from analytics.identity import InstallationIdentityStore
from analytics.models import (
    AnalyticsConfig,
    AnalyticsConsent,
    AnalyticsEvent,
    BackgroundReason,
    EventName,
    FilterAppliedProperties,
    FilterKind,
    FilterSurface,
    LaunchTrigger,
    NavSource,
    ScreenName,
    ScreenViewedProperties,
    SessionEndReason,
    SessionEndedProperties,
    SessionHeartbeatProperties,
    SessionStartedProperties,
    heartbeat_elapsed_bucket,
    screens_visited_bucket,
    session_duration_bucket,
)
from analytics.uuid7 import (
    MAX_BACKWARD_DRIFT_MS,
    datetime_to_unix_ms,
    format_utc_ms,
    resolve_monotonic_event_ms,
    uuid7_from_ms,
)

IDLE_TIMEOUT_SECONDS = 30 * 60.0
MAX_SESSION_AGE_SECONDS = 23 * 3600.0
HEARTBEAT_INTERVAL_SECONDS = 300.0
SCREEN_DEBOUNCE_SECONDS = 1.5
FILTER_DEBOUNCE_SECONDS = 2.0
MAX_EVENTS_PER_SESSION = 120
MAX_EVENTS_PER_24H = 300
ROLLING_24H_SECONDS = 86_400.0


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _ActiveSession:
    session_id: str
    distinct_id: str
    started_mono: float
    started_utc_ms: int
    last_event_utc_ms: int
    last_foreground_active_utc_ms: int
    last_interaction_mono: float
    last_heartbeat_mono: float
    paused_at_mono: float | None = None
    event_count: int = 0
    last_emitted_screen: ScreenName | None = None
    screens_visited: set[ScreenName] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _PendingScreenView:
    screen: ScreenName
    nav_source: NavSource
    due_mono: float


@dataclass(frozen=True, slots=True)
class _PendingFilterApplied:
    surface: FilterSurface
    filter_kind: FilterKind
    due_mono: float


class ForegroundSessionTracker:
    """Manage UUIDv7 ``$session_id`` lifecycle, debouncing, and quota budgets.

    Invariants:
    - Zero events and zero ``installation.json`` disk writes unless:
      ``config.active_eligible and consent == ENABLED and authenticated_non_demo
      and foreground_visible`` (except closing an already-open session at its
      ``last_foreground_active_utc`` on background timeout, account switch, or exit).
    - Deferred ``30m`` background expiry: minimizing or hiding to tray stops the
      ``300s`` heartbeat immediately and stamps ``last_foreground_active_utc``,
      resuming the same ``$session_id`` if restored within ``< 30m`` and ``< 23h``.
    - ``uth_session_ended`` is ALWAYS stamped at ``last_foreground_active_utc``
      (never ``now()``), eliminating midnight DAU spillover.
    """

    def __init__(
        self,
        *,
        config: AnalyticsConfig,
        identity_store: InstallationIdentityStore,
        initial_consent: AnalyticsConsent = AnalyticsConsent.ENABLED,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        idle_timeout_seconds: float = IDLE_TIMEOUT_SECONDS,
        max_session_age_seconds: float = MAX_SESSION_AGE_SECONDS,
        heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        screen_debounce_seconds: float = SCREEN_DEBOUNCE_SECONDS,
        filter_debounce_seconds: float = FILTER_DEBOUNCE_SECONDS,
        max_events_per_session: int = MAX_EVENTS_PER_SESSION,
        max_events_per_24h: int = MAX_EVENTS_PER_24H,
        max_backward_drift_ms: int = MAX_BACKWARD_DRIFT_MS,
    ) -> None:
        self.config = config
        self.identity_store = identity_store
        self._consent = AnalyticsConsent(initial_consent)
        self._clock = clock
        self._monotonic = monotonic
        self._idle_timeout_seconds = max(1.0, float(idle_timeout_seconds))
        self._max_session_age_seconds = max(10.0, float(max_session_age_seconds))
        self._heartbeat_interval_seconds = max(1.0, float(heartbeat_interval_seconds))
        self._screen_debounce_seconds = max(0.0, float(screen_debounce_seconds))
        self._filter_debounce_seconds = max(0.0, float(filter_debounce_seconds))
        self._max_events_per_session = max(1, int(max_events_per_session))
        self._max_events_per_24h = max(1, int(max_events_per_24h))
        self._max_backward_drift_ms = max(0, int(max_backward_drift_ms))

        self._lock = threading.RLock()
        self._authenticated = False
        self._is_demo = False
        self._disabled_for_process = False
        self._foreground_visible = False
        self._current_screen = ScreenName.HOME
        self._session: _ActiveSession | None = None
        self._pending_screen: _PendingScreenView | None = None
        self._pending_filter: _PendingFilterApplied | None = None
        self._rolling_24h_mono: deque[float] = deque()

    @property
    def active_session_id(self) -> str | None:
        with self._lock:
            return self._session.session_id if self._session is not None else None

    @property
    def foreground_visible(self) -> bool:
        with self._lock:
            return self._foreground_visible

    @property
    def authenticated_non_demo(self) -> bool:
        with self._lock:
            return self._authenticated and not self._is_demo

    def _can_emit_foreground_locked(self) -> bool:
        return (
            self.config.active_eligible
            and not self._disabled_for_process
            and self._consent is AnalyticsConsent.ENABLED
            and self._authenticated
            and not self._is_demo
            and self._foreground_visible
        )

    def _can_emit_close_existing_locked(self) -> bool:
        return (
            self.config.active_eligible
            and not self._disabled_for_process
            and self._consent is AnalyticsConsent.ENABLED
            and self._session is not None
        )

    def disable_for_process(self) -> None:
        """Permanently disable capture for this process run (e.g. HTTP 401 invalid key)."""
        with self._lock:
            self._disabled_for_process = True
            self._session = None
            self._pending_screen = None
            self._pending_filter = None

    def purge_in_memory_state(self) -> None:
        """Immediately clear session, debounce, and rate-window state in RAM."""
        with self._lock:
            self._session = None
            self._pending_screen = None
            self._pending_filter = None
            self._rolling_24h_mono.clear()

    def set_consent(
        self,
        consent: AnalyticsConsent | str,
    ) -> list[AnalyticsEvent]:
        """Update consent; purge state if disabled, or start session if newly enabled in fg."""
        selected = AnalyticsConsent(consent)
        with self._lock:
            self._consent = selected
            if selected is not AnalyticsConsent.ENABLED:
                self._session = None
                self._pending_screen = None
                self._pending_filter = None
                self._rolling_24h_mono.clear()
                return []

            out: list[AnalyticsEvent] = []
            if self._can_emit_foreground_locked() and self._session is None:
                now_mono = self._monotonic()
                self._start_session_locked(
                    now_mono=now_mono,
                    trigger=LaunchTrigger.COLD_FOREGROUND,
                    screen=self._current_screen,
                    out=out,
                )
            return out

    def set_account_state(
        self,
        *,
        authenticated: bool,
        is_demo: bool = False,
        account_changed: bool = False,
    ) -> tuple[list[AnalyticsEvent], bool]:
        """Update login/demo status.

        Returns ``(events_to_spool_before_wipe, should_wipe_identity_after_spool)``.
        """
        with self._lock:
            now_mono = self._monotonic()
            was_eligible = self._authenticated and not self._is_demo
            new_eligible = bool(authenticated) and not bool(is_demo)
            losing_or_switching_account = bool(account_changed) or (
                was_eligible and not new_eligible
            )

            pre_wipe_events: list[AnalyticsEvent] = []
            should_wipe_identity = False

            if losing_or_switching_account:
                self._pending_screen = None
                self._pending_filter = None
                if self._can_emit_close_existing_locked():
                    self._flush_due_debounces_locked(now_mono, pre_wipe_events)
                    self._end_session_locked(
                        reason=SessionEndReason.ACCOUNT_CHANGE,
                        out=pre_wipe_events,
                    )
                else:
                    self._session = None
                should_wipe_identity = True

            self._authenticated = bool(authenticated)
            self._is_demo = bool(is_demo)
            if not new_eligible:
                self._session = None
                self._pending_screen = None
                self._pending_filter = None

            return pre_wipe_events, should_wipe_identity

    def start_after_account_ready(self) -> list[AnalyticsEvent]:
        """Start a new session with ``login_completed`` if already foreground-visible."""
        with self._lock:
            out: list[AnalyticsEvent] = []
            if self._can_emit_foreground_locked() and self._session is None:
                now_mono = self._monotonic()
                self._start_session_locked(
                    now_mono=now_mono,
                    trigger=LaunchTrigger.LOGIN_COMPLETED,
                    screen=self._current_screen,
                    out=out,
                )
            return out

    def on_foreground(
        self,
        trigger: LaunchTrigger | str = LaunchTrigger.COLD_FOREGROUND,
        screen: ScreenName | str | None = None,
    ) -> list[AnalyticsEvent]:
        """Mark UI foreground-visible; start or seamlessly resume UUIDv7 session."""
        selected_trigger = LaunchTrigger(trigger)
        selected_screen = (
            ScreenName(screen) if screen is not None else None
        )
        with self._lock:
            now_mono = self._monotonic()
            if selected_screen is not None:
                self._current_screen = selected_screen
            self._foreground_visible = True

            out: list[AnalyticsEvent] = []
            if not self._can_emit_foreground_locked():
                return out

            if self._session is not None:
                session = self._session
                # Check background timeout (>= 30m), idle timeout (>= 30m), or max age (>= 23h)
                bg_expired = (
                    session.paused_at_mono is not None
                    and (now_mono - session.paused_at_mono) >= self._idle_timeout_seconds
                )
                idle_expired = (
                    session.paused_at_mono is None
                    and (now_mono - session.last_interaction_mono)
                    >= self._idle_timeout_seconds
                )
                age_expired = (
                    now_mono - session.started_mono
                ) >= self._max_session_age_seconds

                candidate_ms = datetime_to_unix_ms(self._clock())
                effective_ms, clock_rotate = resolve_monotonic_event_ms(
                    candidate_ms,
                    session.last_event_utc_ms,
                    session_start_ms=session.started_utc_ms,
                    max_backward_drift_ms=self._max_backward_drift_ms,
                )

                if bg_expired:
                    self._end_session_locked(
                        reason=SessionEndReason.BACKGROUND_TIMEOUT,
                        out=out,
                    )
                elif idle_expired:
                    self._end_session_locked(
                        reason=SessionEndReason.IDLE_TIMEOUT,
                        out=out,
                    )
                elif age_expired or clock_rotate:
                    self._end_session_locked(
                        reason=SessionEndReason.MAX_AGE_ROTATE,
                        out=out,
                    )
                else:
                    # Seamless <30m foreground resume on the SAME $session_id!
                    session.paused_at_mono = None
                    session.last_interaction_mono = now_mono
                    session.last_foreground_active_utc_ms = max(
                        session.last_foreground_active_utc_ms,
                        effective_ms,
                    )
                    if selected_screen is not None:
                        session.screens_visited.add(selected_screen)
                    return out

            self._start_session_locked(
                now_mono=now_mono,
                trigger=selected_trigger,
                screen=self._current_screen,
                out=out,
            )
            return out

    def on_background(
        self,
        reason: BackgroundReason | str = BackgroundReason.TRAY_HIDE,
    ) -> list[AnalyticsEvent]:
        """Pause foreground tracking, stamp ``last_foreground_active_utc``, halt heartbeat."""
        BackgroundReason(reason)
        with self._lock:
            now_mono = self._monotonic()
            out: list[AnalyticsEvent] = []
            if self._foreground_visible and self._can_emit_foreground_locked():
                if self._session is not None and (
                    now_mono - self._session.last_interaction_mono
                ) >= self._idle_timeout_seconds:
                    self._pending_screen = None
                    self._pending_filter = None
                    self._end_session_locked(
                        reason=SessionEndReason.IDLE_TIMEOUT,
                        out=out,
                    )
                else:
                    self._flush_due_debounces_locked(now_mono, out)
                    if (
                        self._session is not None
                        and self._session.paused_at_mono is None
                    ):
                        candidate_ms = datetime_to_unix_ms(self._clock())
                        effective_ms, clock_rotate = resolve_monotonic_event_ms(
                            candidate_ms,
                            self._session.last_event_utc_ms,
                            session_start_ms=self._session.started_utc_ms,
                            max_backward_drift_ms=self._max_backward_drift_ms,
                        )
                        if not clock_rotate:
                            self._session.last_foreground_active_utc_ms = max(
                                self._session.last_foreground_active_utc_ms,
                                effective_ms,
                            )
                        self._session.paused_at_mono = now_mono

            # Drop un-matured dwell screen view when hiding window before 1.5s dwell.
            self._pending_screen = None
            self._foreground_visible = False
            return out

    def record_interaction(
        self,
        screen: ScreenName | str | None = None,
    ) -> list[AnalyticsEvent]:
        """Record discrete human interaction in the foreground."""
        selected_screen = ScreenName(screen) if screen is not None else None
        with self._lock:
            if selected_screen is not None:
                self._current_screen = selected_screen
            if not self._can_emit_foreground_locked():
                return []
            now_mono = self._monotonic()
            out: list[AnalyticsEvent] = []
            self._ensure_active_session_for_event_locked(
                now_mono=now_mono,
                human_interaction=True,
                out=out,
            )
            if self._session is not None and selected_screen is not None:
                self._session.screens_visited.add(selected_screen)
            return out

    def capture_screen_viewed(
        self,
        screen: ScreenName | str,
        nav_source: NavSource | str,
    ) -> list[AnalyticsEvent]:
        """Schedule a ``1.5s`` dwell/dedupe-debounced ``uth_screen_viewed`` event."""
        selected_screen = ScreenName(screen)
        selected_source = NavSource(nav_source)
        with self._lock:
            self._current_screen = selected_screen
            if not self._can_emit_foreground_locked():
                return []
            now_mono = self._monotonic()
            out: list[AnalyticsEvent] = []
            self._flush_due_debounces_locked(now_mono, out)
            if not self._ensure_active_session_for_event_locked(
                now_mono=now_mono,
                human_interaction=True,
                out=out,
            ):
                return out
            assert self._session is not None
            if self._session.last_emitted_screen == selected_screen:
                self._pending_screen = None
                return out
            if self._screen_debounce_seconds <= 0.0:
                self._emit_screen_viewed_now_locked(
                    now_mono=now_mono,
                    screen=selected_screen,
                    nav_source=selected_source,
                    out=out,
                )
            else:
                self._pending_screen = _PendingScreenView(
                    screen=selected_screen,
                    nav_source=selected_source,
                    due_mono=now_mono + self._screen_debounce_seconds,
                )
            return out

    def capture_filter_applied(
        self,
        surface: FilterSurface | str,
        filter_kind: FilterKind | str,
    ) -> list[AnalyticsEvent]:
        """Schedule a ``2.0s`` debounced ``uth_filter_applied`` event."""
        selected_surface = FilterSurface(surface)
        selected_kind = FilterKind(filter_kind)
        with self._lock:
            if not self._can_emit_foreground_locked():
                return []
            now_mono = self._monotonic()
            out: list[AnalyticsEvent] = []
            self._flush_due_debounces_locked(now_mono, out)
            if not self._ensure_active_session_for_event_locked(
                now_mono=now_mono,
                human_interaction=True,
                out=out,
            ):
                return out
            if self._filter_debounce_seconds <= 0.0:
                self._emit_event_locked(
                    now_mono=now_mono,
                    event=EventName.FILTER_APPLIED,
                    props=FilterAppliedProperties(
                        surface=selected_surface,
                        filter_kind=selected_kind,
                    ),
                    out=out,
                )
            else:
                self._pending_filter = _PendingFilterApplied(
                    surface=selected_surface,
                    filter_kind=selected_kind,
                    due_mono=now_mono + self._filter_debounce_seconds,
                )
            return out

    def capture_event(
        self,
        event: EventName,
        props: Any,
        *,
        human_interaction: bool = True,
    ) -> list[AnalyticsEvent]:
        """Emit one immediate allow-listed feature event if foreground-eligible."""
        with self._lock:
            if not self._can_emit_foreground_locked():
                return []
            now_mono = self._monotonic()
            out: list[AnalyticsEvent] = []
            self._flush_due_debounces_locked(now_mono, out)
            if not self._ensure_active_session_for_event_locked(
                now_mono=now_mono,
                human_interaction=human_interaction,
                out=out,
            ):
                return out
            self._emit_event_locked(
                now_mono=now_mono,
                event=event,
                props=props,
                out=out,
            )
            return out

    def poll_timers(self) -> list[AnalyticsEvent]:
        """Advance debounces, 30m background/idle expiry, 23h rotation, and 300s heartbeat."""
        with self._lock:
            if (
                not self.config.active_eligible
                or self._disabled_for_process
                or self._consent is not AnalyticsConsent.ENABLED
            ):
                return []

            now_mono = self._monotonic()
            out: list[AnalyticsEvent] = []

            if self._foreground_visible and self._authenticated and not self._is_demo:
                self._flush_due_debounces_locked(now_mono, out)

            session = self._session
            if session is None:
                return out

            # 1. Background 30m deferred expiry
            if not self._foreground_visible or session.paused_at_mono is not None:
                paused_since = (
                    session.paused_at_mono
                    if session.paused_at_mono is not None
                    else session.last_interaction_mono
                )
                if (now_mono - paused_since) >= self._idle_timeout_seconds:
                    self._pending_screen = None
                    self._pending_filter = None
                    self._end_session_locked(
                        reason=SessionEndReason.BACKGROUND_TIMEOUT,
                        out=out,
                    )
                return out

            # 2. Foreground 30m idle expiry (stops heartbeat cleanly)
            if (now_mono - session.last_interaction_mono) >= self._idle_timeout_seconds:
                self._pending_screen = None
                self._pending_filter = None
                self._end_session_locked(
                    reason=SessionEndReason.IDLE_TIMEOUT,
                    out=out,
                )
                return out

            # 3. Max age 23h rotation
            if (now_mono - session.started_mono) >= self._max_session_age_seconds:
                self._end_session_locked(
                    reason=SessionEndReason.MAX_AGE_ROTATE,
                    out=out,
                )
                if self._can_emit_foreground_locked():
                    self._start_session_locked(
                        now_mono=now_mono,
                        trigger=LaunchTrigger.IDLE_RESUME,
                        screen=self._current_screen,
                        out=out,
                    )
                return out

            # 4. Foreground-only 300s heartbeat (only while visible & interacted < 30m)
            if (
                now_mono - session.last_heartbeat_mono
            ) >= self._heartbeat_interval_seconds:
                elapsed_seconds = max(0.0, now_mono - session.started_mono)
                emitted = self._emit_event_locked(
                    now_mono=now_mono,
                    event=EventName.SESSION_HEARTBEAT,
                    props=SessionHeartbeatProperties(
                        elapsed_bucket=heartbeat_elapsed_bucket(elapsed_seconds),
                        active_screen=self._current_screen,
                    ),
                    out=out,
                )
                if emitted and self._session is not None:
                    self._session.last_heartbeat_mono = now_mono

            return out

    def prepare_shutdown(self) -> list[AnalyticsEvent]:
        """Emit ``uth_session_ended(app_exit)`` stamped at ``last_foreground_active_utc``."""
        with self._lock:
            now_mono = self._monotonic()
            out: list[AnalyticsEvent] = []
            if not self._can_emit_close_existing_locked():
                self._session = None
                self._pending_screen = None
                self._pending_filter = None
                return out

            if (
                self._foreground_visible
                and self._session is not None
                and self._session.paused_at_mono is None
                and (now_mono - self._session.last_interaction_mono)
                < self._idle_timeout_seconds
            ):
                self._flush_due_debounces_locked(now_mono, out)

            self._pending_screen = None
            self._pending_filter = None
            # Stamp uth_session_ended strictly at last_foreground_active_utc (never now()).
            self._end_session_locked(reason=SessionEndReason.APP_EXIT, out=out)
            return out

    # ── Internal Helpers ──

    def _prune_24h_window_locked(self, now_mono: float) -> None:
        cutoff = now_mono - ROLLING_24H_SECONDS
        while self._rolling_24h_mono and self._rolling_24h_mono[0] <= cutoff:
            self._rolling_24h_mono.popleft()

    def _has_quota_locked(self, now_mono: float, session: _ActiveSession) -> bool:
        self._prune_24h_window_locked(now_mono)
        if len(self._rolling_24h_mono) >= self._max_events_per_24h:
            return False
        if session.event_count >= self._max_events_per_session:
            return False
        return True

    def _start_session_locked(
        self,
        *,
        now_mono: float,
        trigger: LaunchTrigger,
        screen: ScreenName,
        out: list[AnalyticsEvent],
    ) -> bool:
        if not self._can_emit_foreground_locked():
            return False
        self._prune_24h_window_locked(now_mono)
        if len(self._rolling_24h_mono) >= self._max_events_per_24h:
            return False

        distinct_id = self.identity_store.get_or_create(allow_create=True)
        if distinct_id is None:
            return False

        start_ms = datetime_to_unix_ms(self._clock())
        session_id = uuid7_from_ms(start_ms)
        session = _ActiveSession(
            session_id=session_id,
            distinct_id=distinct_id,
            started_mono=now_mono,
            started_utc_ms=start_ms,
            last_event_utc_ms=start_ms,
            last_foreground_active_utc_ms=start_ms,
            last_interaction_mono=now_mono,
            last_heartbeat_mono=now_mono,
            paused_at_mono=None,
            event_count=0,
            last_emitted_screen=screen,
            screens_visited={screen},
        )
        self._session = session
        return self._build_and_append_locked(
            now_mono=now_mono,
            session=session,
            event=EventName.SESSION_STARTED,
            props=SessionStartedProperties(
                launch_trigger=trigger,
                initial_screen=screen,
            ),
            timestamp_ms=start_ms,
            out=out,
        )

    def _ensure_active_session_for_event_locked(
        self,
        *,
        now_mono: float,
        human_interaction: bool,
        out: list[AnalyticsEvent],
    ) -> bool:
        if not self._can_emit_foreground_locked():
            return False

        session = self._session
        if session is not None:
            if (now_mono - session.last_interaction_mono) >= self._idle_timeout_seconds:
                self._end_session_locked(
                    reason=SessionEndReason.IDLE_TIMEOUT,
                    out=out,
                )
                session = None
            elif (now_mono - session.started_mono) >= self._max_session_age_seconds:
                self._end_session_locked(
                    reason=SessionEndReason.MAX_AGE_ROTATE,
                    out=out,
                )
                session = None

        if session is None:
            if not human_interaction:
                return False
            return self._start_session_locked(
                now_mono=now_mono,
                trigger=LaunchTrigger.IDLE_RESUME,
                screen=self._current_screen,
                out=out,
            )

        candidate_ms = datetime_to_unix_ms(self._clock())
        effective_ms, should_rotate = resolve_monotonic_event_ms(
            candidate_ms,
            session.last_event_utc_ms,
            session_start_ms=session.started_utc_ms,
            max_backward_drift_ms=self._max_backward_drift_ms,
        )
        if should_rotate:
            self._end_session_locked(
                reason=SessionEndReason.MAX_AGE_ROTATE,
                out=out,
            )
            if not human_interaction:
                return False
            return self._start_session_locked(
                now_mono=now_mono,
                trigger=LaunchTrigger.IDLE_RESUME,
                screen=self._current_screen,
                out=out,
            )

        if human_interaction:
            session.last_interaction_mono = now_mono
            session.last_foreground_active_utc_ms = max(
                session.last_foreground_active_utc_ms,
                effective_ms,
            )
        return True

    def _emit_screen_viewed_now_locked(
        self,
        *,
        now_mono: float,
        screen: ScreenName,
        nav_source: NavSource,
        out: list[AnalyticsEvent],
    ) -> None:
        if self._session is None:
            return
        if self._session.last_emitted_screen == screen:
            return
        emitted = self._emit_event_locked(
            now_mono=now_mono,
            event=EventName.SCREEN_VIEWED,
            props=ScreenViewedProperties(
                screen=screen,
                nav_source=nav_source,
            ),
            out=out,
        )
        if emitted and self._session is not None:
            self._session.last_emitted_screen = screen
            self._session.screens_visited.add(screen)

    def _flush_due_debounces_locked(
        self,
        now_mono: float,
        out: list[AnalyticsEvent],
    ) -> None:
        due_items: list[tuple[float, str, Any]] = []
        if (
            self._pending_screen is not None
            and now_mono >= self._pending_screen.due_mono
        ):
            due_items.append((self._pending_screen.due_mono, "screen", self._pending_screen))
            self._pending_screen = None
        if (
            self._pending_filter is not None
            and now_mono >= self._pending_filter.due_mono
        ):
            due_items.append((self._pending_filter.due_mono, "filter", self._pending_filter))
            self._pending_filter = None

        due_items.sort(key=lambda item: item[0])
        for _due_mono, kind, item in due_items:
            if not self._ensure_active_session_for_event_locked(
                now_mono=now_mono,
                human_interaction=False,
                out=out,
            ):
                continue
            if kind == "screen":
                self._emit_screen_viewed_now_locked(
                    now_mono=now_mono,
                    screen=item.screen,
                    nav_source=item.nav_source,
                    out=out,
                )
            else:
                self._emit_event_locked(
                    now_mono=now_mono,
                    event=EventName.FILTER_APPLIED,
                    props=FilterAppliedProperties(
                        surface=item.surface,
                        filter_kind=item.filter_kind,
                    ),
                    out=out,
                )

    def _emit_event_locked(
        self,
        *,
        now_mono: float,
        event: EventName,
        props: Any,
        out: list[AnalyticsEvent],
    ) -> bool:
        session = self._session
        if session is None:
            return False
        candidate_ms = datetime_to_unix_ms(self._clock())
        effective_ms, should_rotate = resolve_monotonic_event_ms(
            candidate_ms,
            session.last_event_utc_ms,
            session_start_ms=session.started_utc_ms,
            max_backward_drift_ms=self._max_backward_drift_ms,
        )
        if should_rotate:
            self._end_session_locked(
                reason=SessionEndReason.MAX_AGE_ROTATE,
                out=out,
            )
            if not self._start_session_locked(
                now_mono=now_mono,
                trigger=LaunchTrigger.IDLE_RESUME,
                screen=self._current_screen,
                out=out,
            ):
                return False
            session = self._session
            if session is None:
                return False
            effective_ms = session.started_utc_ms

        return self._build_and_append_locked(
            now_mono=now_mono,
            session=session,
            event=event,
            props=props,
            timestamp_ms=effective_ms,
            out=out,
        )

    def _end_session_locked(
        self,
        *,
        reason: SessionEndReason,
        out: list[AnalyticsEvent],
    ) -> None:
        session = self._session
        self._session = None
        if session is None:
            return
        now_mono = self._monotonic()
        end_ms = max(session.last_foreground_active_utc_ms, session.last_event_utc_ms)
        duration_sec = max(0.0, (end_ms - session.started_utc_ms) / 1000.0)
        self._build_and_append_locked(
            now_mono=now_mono,
            session=session,
            event=EventName.SESSION_ENDED,
            props=SessionEndedProperties(
                end_reason=reason,
                duration_bucket=session_duration_bucket(duration_sec),
                screens_visited_bucket=screens_visited_bucket(
                    max(1, len(session.screens_visited))
                ),
            ),
            timestamp_ms=end_ms,
            out=out,
        )

    def _build_and_append_locked(
        self,
        *,
        now_mono: float,
        session: _ActiveSession,
        event: EventName,
        props: Any,
        timestamp_ms: int,
        out: list[AnalyticsEvent],
    ) -> bool:
        if not self._has_quota_locked(now_mono, session):
            return False
        built = AnalyticsEvent.build(
            event=event,
            event_properties=props,
            distinct_id=session.distinct_id,
            session_id=session.session_id,
            timestamp=format_utc_ms(timestamp_ms),
            config=self.config,
        )
        session.event_count += 1
        session.last_event_utc_ms = max(session.last_event_utc_ms, timestamp_ms)
        if self._foreground_visible and session.paused_at_mono is None:
            session.last_foreground_active_utc_ms = max(
                session.last_foreground_active_utc_ms,
                timestamp_ms,
            )
        self._rolling_24h_mono.append(now_mono)
        out.append(built)
        return True


__all__ = [
    "FILTER_DEBOUNCE_SECONDS",
    "ForegroundSessionTracker",
    "HEARTBEAT_INTERVAL_SECONDS",
    "IDLE_TIMEOUT_SECONDS",
    "MAX_EVENTS_PER_24H",
    "MAX_EVENTS_PER_SESSION",
    "MAX_SESSION_AGE_SECONDS",
    "ROLLING_24H_SECONDS",
    "SCREEN_DEBOUNCE_SECONDS",
]
