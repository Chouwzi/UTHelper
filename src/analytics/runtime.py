"""Process-scoped PostHog analytics runtime with zero-stall shutdown and consent purge."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version as pkg_version
import json
import logging
import os
from pathlib import Path
import platform
import re
import sys
import threading
import time
import tomllib
from typing import Any

from analytics.identity import InstallationIdentityStore
from analytics.models import (
    ActivitySourceSite,
    AnalyticsConfig,
    AnalyticsConfigError,
    AnalyticsConsent,
    AnalyticsEvent,
    BackgroundReason,
    CourseActivityKind,
    CourseActivityOpenedProperties,
    DeliveredCountBucket,
    DeviceClass,
    DistributionChannelName,
    DocumentFormat,
    DocumentOpenedProperties,
    DocumentSourceSite,
    DurationBucket,
    EventName,
    FileCountBucket,
    FilterKind,
    FilterSurface,
    GradesInteractionKind,
    GradesInteractionProperties,
    ItemCountBucket,
    LaunchTrigger,
    NavSource,
    NotificationSummaryKind,
    NotificationSummaryProperties,
    OsFamily,
    PackageFormatName,
    QuizActionKind,
    QuizActionProperties,
    ReleaseChannel,
    ScheduledCountBucket,
    ScreenName,
    SessionDurationBucket,
    SubmissionActionKind,
    SubmissionActionProperties,
    SubmissionOutcome,
    SyncCompletedProperties,
    SyncOutcome,
    SyncProvider,
    SyncTrigger,
    ViewerKind,
    delivered_count_bucket,
    duration_bucket,
    file_count_bucket,
    item_count_bucket,
    scheduled_count_bucket,
    validate_posthog_api_key,
    validate_posthog_host,
)
from analytics.session import (
    FILTER_DEBOUNCE_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    IDLE_TIMEOUT_SECONDS,
    MAX_EVENTS_PER_24H,
    MAX_EVENTS_PER_SESSION,
    MAX_SESSION_AGE_SECONDS,
    SCREEN_DEBOUNCE_SECONDS,
    ForegroundSessionTracker,
)
from analytics.spool import AnalyticsSpool
from analytics.transport import (
    AnalyticsConsentEpochGate,
    AnalyticsDeliveryWorker,
    AnalyticsFlushSummary,
    MAX_DELIVERY_TIMEOUT_SECONDS,
)

DEFAULT_DEV_POSTHOG_HOST = "https://us.i.posthog.com"
RAM_COALESCE_INTERVAL_SECONDS = 2.0
RAM_COALESCE_BATCH_THRESHOLD = 5
NETWORK_FLUSH_INTERVAL_SECONDS = 30.0
SESSION_START_FLUSH_DELAY_SECONDS = 2.0
WORKER_JOIN_TIMEOUT_SECONDS = 0.05

_SOURCE_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_PROJECT_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
_PUBLIC_CONFIG_FILENAME = "diagnostics-config.json"
_EXPECTED_PUBLIC_CONFIG_V3_KEYS = frozenset(
    {
        "schema_version",
        "sentry_dsn",
        "build_environment",
        "package_format",
        "posthog_project_api_key",
        "posthog_host",
    }
)
_DISTRIBUTION_FILENAME = "distribution-channel.json"
_RELEASE_VERSION_FILENAME = "release-version"
_MAX_ASSET_BYTES = 4096
_NUMERIC_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _detect_os_family() -> OsFamily:
    if (
        bool(getattr(sys, "_ANDROID_", False))
        or hasattr(sys, "getandroidapilevel")
        or "android" in sys.platform.lower()
    ):
        return OsFamily.ANDROID
    if bool(getattr(sys, "_IOS_", False)) or sys.platform == "ios":
        return OsFamily.IOS
    system_name = platform.system()
    for member in OsFamily:
        if member.value == system_name:
            return member
    return OsFamily.UNKNOWN


def _detect_device_class(family: OsFamily) -> DeviceClass:
    if family in {OsFamily.ANDROID, OsFamily.IOS}:
        return DeviceClass.MOBILE
    if family in {OsFamily.WINDOWS, OsFamily.LINUX, OsFamily.DARWIN}:
        return DeviceClass.DESKTOP
    return DeviceClass.UNKNOWN


def _read_small_json_asset(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or path.stat().st_size > _MAX_ASSET_BYTES:
            return None
        raw = path.read_bytes()
        if len(raw) > _MAX_ASSET_BYTES:
            return None
        parsed = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _resolve_default_app_version(assets_root: Path) -> str:
    try:
        pyproject_data = tomllib.loads(_PROJECT_PYPROJECT.read_text(encoding="utf-8"))
        candidate = pyproject_data.get("project", {}).get("version")
        if isinstance(candidate, str) and _NUMERIC_VERSION_RE.fullmatch(candidate):
            return candidate
    except (OSError, TypeError, tomllib.TOMLDecodeError):
        pass

    try:
        raw_ver = (assets_root / _RELEASE_VERSION_FILENAME).read_bytes()
        if len(raw_ver) <= 64:
            text_ver = raw_ver.decode("ascii").strip()
            if _NUMERIC_VERSION_RE.fullmatch(text_ver):
                return text_ver
    except (OSError, UnicodeDecodeError):
        pass

    try:
        installed = pkg_version("uthelper")
        if _NUMERIC_VERSION_RE.fullmatch(installed):
            return installed
    except PackageNotFoundError:
        pass
    return "unknown"


def _read_schema_v3_asset_fields(
    assets_root: Path,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Read and validate PostHog + build metadata from ``diagnostics-config.json``."""
    payload = _read_small_json_asset(assets_root / _PUBLIC_CONFIG_FILENAME)
    if payload is None:
        return None, None, None, None

    schema_ver = payload.get("schema_version")
    build_env = payload.get("build_environment")
    pkg_fmt = payload.get("package_format")
    resolved_env = build_env if isinstance(build_env, str) else None
    resolved_fmt = pkg_fmt if isinstance(pkg_fmt, str) else None

    if type(schema_ver) is not int or schema_ver != 3:
        return None, None, resolved_env, resolved_fmt
    if set(payload.keys()) != _EXPECTED_PUBLIC_CONFIG_V3_KEYS:
        return None, None, None, None

    raw_key = payload.get("posthog_project_api_key")
    raw_host = payload.get("posthog_host")
    if not isinstance(raw_key, str) or not isinstance(raw_host, str):
        return None, None, resolved_env, resolved_fmt

    file_key: str | None = None
    file_host: str | None = None
    try:
        if raw_key:
            file_key = validate_posthog_api_key(raw_key)
        if raw_host:
            file_host = validate_posthog_host(raw_host, allow_private_dev=False)
    except AnalyticsConfigError:
        return None, None, resolved_env, resolved_fmt

    return file_key, file_host, resolved_env, resolved_fmt


def _resolve_public_posthog_pair(
    assets_root: Path,
    *,
    development: bool,
    environ: Mapping[str, str],
) -> tuple[str | None, str | None, str | None, str | None, bool]:
    """Self-contained Schema v3 + dev env PostHog config reader (0 cross-package imports)."""
    if development:
        dev_key = environ.get("UTH_POSTHOG_KEY", "")
        dev_host = environ.get("UTH_POSTHOG_HOST", "")
        if dev_key and dev_host:
            try:
                valid_key = validate_posthog_api_key(dev_key)
                valid_host = validate_posthog_host(dev_host, allow_private_dev=True)
                return valid_key, valid_host, "development", "source", True
            except AnalyticsConfigError:
                return None, None, "development", "source", True

        if dev_key and not dev_host:
            try:
                valid_key = validate_posthog_api_key(dev_key)
            except AnalyticsConfigError:
                return None, None, "development", "source", True
            _fk, file_host, _fe, _ff = _read_schema_v3_asset_fields(assets_root)
            effective_host = file_host or DEFAULT_DEV_POSTHOG_HOST
            try:
                valid_host = validate_posthog_host(
                    effective_host,
                    allow_private_dev=True,
                )
                return valid_key, valid_host, "development", "source", True
            except AnalyticsConfigError:
                return None, None, "development", "source", True

        if dev_host and not dev_key:
            try:
                valid_host = validate_posthog_host(dev_host, allow_private_dev=True)
            except AnalyticsConfigError:
                return None, None, "development", "source", True
            file_key, _fh, _fe, _ff = _read_schema_v3_asset_fields(assets_root)
            if file_key is None:
                return None, None, "development", "source", True
            return file_key, valid_host, "development", "source", True

    file_key, file_host, resolved_env, resolved_fmt = _read_schema_v3_asset_fields(
        assets_root
    )
    if not file_key or not file_host:
        return None, None, resolved_env, resolved_fmt, False
    return file_key, file_host, resolved_env, resolved_fmt, False


def _resolve_distribution_channel_name(
    assets_root: Path,
    *,
    development: bool,
) -> DistributionChannelName:
    if development:
        return DistributionChannelName.SOURCE
    payload = _read_small_json_asset(assets_root / _DISTRIBUTION_FILENAME)
    if payload is not None:
        raw_channel = payload.get("channel")
        if isinstance(raw_channel, str):
            try:
                return DistributionChannelName(raw_channel)
            except ValueError:
                pass
    return DistributionChannelName.SOURCE


def _coerce_enum_or_default(enum_cls: Any, value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return enum_cls(value)
    except (TypeError, ValueError):
        return default


class AnalyticsRuntime:
    """Thread-safe, non-blocking PostHog analytics orchestrator for UTHelper.

    Key guarantees:
    - UI capture calls only append to ``_ram_buffer`` (``O(1)``, zero per-click disk I/O).
    - Background ``uthelper-analytics`` loop coalesces RAM -> spool every ``2.0s`` (or ``5``
      events) and immediately on ``uth_session_started``; HTTP ``/batch/`` flushes run every
      ``30.0s`` (plus ``2.0s`` after session start).
    - ``set_consent(DISABLED)`` and ``reset_local_state()`` purge RAM + ``pending/`` +
      ``installation.json`` in ``0ms`` via ``AnalyticsConsentEpochGate`` without waiting
      on in-flight socket I/O.
    - ``close()`` writes ``uth_session_ended`` (stamped at ``last_foreground_active_utc``)
      synchronously to local spool (``< 2ms``, ``fsync=False``) and calls
      ``executor.shutdown(wait=False, cancel_futures=True)``.
    """

    def __init__(
        self,
        *,
        data_dir: Path | str,
        config: AnalyticsConfig,
        initial_consent: AnalyticsConsent | str = AnalyticsConsent.ENABLED,
        consent_provider: Callable[[], AnalyticsConsent | str] | None = None,
        spool: AnalyticsSpool | None = None,
        identity_store: InstallationIdentityStore | None = None,
        consent_gate: AnalyticsConsentEpochGate | None = None,
        delivery: AnalyticsDeliveryWorker | None = None,
        delivery_executor: Executor | None = None,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        coalesce_interval_seconds: float = RAM_COALESCE_INTERVAL_SECONDS,
        network_flush_interval_seconds: float = NETWORK_FLUSH_INTERVAL_SECONDS,
        idle_timeout_seconds: float = IDLE_TIMEOUT_SECONDS,
        max_session_age_seconds: float = MAX_SESSION_AGE_SECONDS,
        heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        screen_debounce_seconds: float = SCREEN_DEBOUNCE_SECONDS,
        filter_debounce_seconds: float = FILTER_DEBOUNCE_SECONDS,
        max_events_per_session: int = MAX_EVENTS_PER_SESSION,
        max_events_per_24h: int = MAX_EVENTS_PER_24H,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.analytics_dir = self.data_dir / "analytics"
        self.pending_dir = self.analytics_dir / "pending"
        self.config = config
        self._clock = clock
        self._monotonic = monotonic
        self._coalesce_interval = max(0.05, float(coalesce_interval_seconds))
        self._network_flush_interval = max(0.5, float(network_flush_interval_seconds))

        resolved_consent = _coerce_enum_or_default(
            AnalyticsConsent,
            initial_consent,
            AnalyticsConsent.DISABLED,
        )
        self._consent_provider = consent_provider
        self.consent_gate = consent_gate or AnalyticsConsentEpochGate(resolved_consent)
        self.identity_store = identity_store or InstallationIdentityStore(
            self.analytics_dir
        )
        self.spool = spool or AnalyticsSpool(
            self.pending_dir,
            clock=clock,
            create_dir=False,
        )
        self.tracker = ForegroundSessionTracker(
            config=config,
            identity_store=self.identity_store,
            initial_consent=self.consent_gate.current,
            clock=clock,
            monotonic=monotonic,
            idle_timeout_seconds=idle_timeout_seconds,
            max_session_age_seconds=max_session_age_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            screen_debounce_seconds=screen_debounce_seconds,
            filter_debounce_seconds=filter_debounce_seconds,
            max_events_per_session=max_events_per_session,
            max_events_per_24h=max_events_per_24h,
        )
        self.delivery = delivery or AnalyticsDeliveryWorker(
            self.spool,
            api_key=config.api_key if config.active_eligible else None,
            host=config.host if config.active_eligible else None,
            app_version=config.app_version,
            allow_private_host=config.allow_private_host,
            consent_gate=self.consent_gate,
            monotonic=monotonic,
            on_permanent_disable=self._handle_permanent_disable,
        )

        self._executor = delivery_executor
        self._lifecycle_lock = threading.RLock()
        self._spool_io_lock = threading.RLock()
        self._buffer_lock = threading.Lock()
        self._ram_buffer: deque[AnalyticsEvent] = deque()
        self._immediate_spool_needed = False
        self._pending_disk_purge = False
        self._spool_write_seq = 1
        self._spool_flushed_seq = 0
        self._next_network_flush_mono = 0.0
        self._delivery_in_flight = False

        self._wakeup_event = threading.Event()
        self.shutdown_event = threading.Event()
        self._loop_thread: threading.Thread | None = None
        self._consent_unsubscribe: Callable[[], None] | None = None
        self._started = False
        self._closed = False

        if self.consent_gate.current is not AnalyticsConsent.ENABLED:
            self._purge_local_files_best_effort()

    @property
    def started(self) -> bool:
        with self._lifecycle_lock:
            return self._started

    @property
    def closed(self) -> bool:
        with self._lifecycle_lock:
            return self._closed

    @property
    def current_consent(self) -> AnalyticsConsent:
        return self.consent_gate.current

    @property
    def active_session_id(self) -> str | None:
        return self.tracker.active_session_id

    # ── Lifecycle & Consent ──

    def start(self) -> None:
        """Start background coalescer/heartbeat loop and queue prior pending spool flush.

        Emits ZERO events at startup until ``set_account_state`` + ``on_foreground``.
        """
        with self._lifecycle_lock:
            if self._started or self._closed:
                return
            self._started = True

            if self._consent_provider is not None:
                try:
                    live_consent = AnalyticsConsent(self._consent_provider())
                except Exception:
                    live_consent = self.consent_gate.current
                if live_consent != self.consent_gate.current:
                    self.set_consent(live_consent)

            if self.config.active_eligible:
                now_mono = self._monotonic()
                if (
                    self.tracker.authenticated_non_demo
                    and self.tracker.foreground_visible
                ):
                    self._next_network_flush_mono = now_mono
                else:
                    self._next_network_flush_mono = (
                        now_mono + self._network_flush_interval
                    )
                loop_thread = threading.Thread(
                    target=self._maintenance_loop,
                    name="uthelper-analytics-loop",
                    daemon=True,
                )
                self._loop_thread = loop_thread
                loop_thread.start()

    def bind_consent_changes(
        self,
        subscribe: Callable[[Callable[[], None]], Callable[[], None]],
    ) -> None:
        """Bind Settings save notifications to live consent updates once."""
        if not callable(subscribe):
            return
        with self._lifecycle_lock:
            if self._closed or self._consent_unsubscribe is not None:
                return
        try:
            unsubscribe = subscribe(self._on_settings_saved_consent_check)
        except Exception:
            return
        if not callable(unsubscribe):
            return
        release_now = False
        with self._lifecycle_lock:
            if self._closed or self._consent_unsubscribe is not None:
                release_now = True
            else:
                self._consent_unsubscribe = unsubscribe
        if release_now:
            try:
                unsubscribe()
            except Exception:
                pass

    def _on_settings_saved_consent_check(self) -> None:
        if self._consent_provider is None:
            return
        try:
            latest = AnalyticsConsent(self._consent_provider())
        except Exception:
            return
        with self._lifecycle_lock:
            if self._closed:
                return
            with self.consent_gate._lock, self.tracker._lock:
                if (
                    latest is self.consent_gate.current
                    and self.tracker.consent is latest
                    and not self._pending_disk_purge
                ):
                    return
        self.set_consent(latest)

    def set_consent(self, consent: AnalyticsConsent | str) -> None:
        """Apply live consent immediately; ``0ms`` non-blocking purge when disabled."""
        try:
            selected = AnalyticsConsent(consent)
        except (TypeError, ValueError):
            return

        with self._lifecycle_lock:
            if self._closed:
                return
            with self.consent_gate._lock, self.tracker._lock:
                if (
                    selected is self.consent_gate.current
                    and self.tracker.consent is selected
                    and not self._pending_disk_purge
                ):
                    return

        if selected is not AnalyticsConsent.ENABLED:
            with self._lifecycle_lock:
                if self._closed:
                    return
                with self.consent_gate._lock, self.tracker._lock, self._buffer_lock:
                    self.consent_gate.set_consent(selected)
                    self.tracker.set_consent(selected)
                    self._ram_buffer.clear()
                    self._immediate_spool_needed = False
                    self._pending_disk_purge = True
            self._drain_pending_disk_purge()
            self._wakeup_event.set()
            return

        events: list[AnalyticsEvent] = []
        captured_epoch: int | None = None
        with self._lifecycle_lock:
            if self._closed:
                return
            with self._spool_io_lock:
                self._drain_pending_disk_purge_locked()
                with self.consent_gate._lock, self.tracker._lock, self._buffer_lock:
                    if (
                        selected is self.consent_gate.current
                        and self.tracker.consent is selected
                    ):
                        return
                    captured_epoch = self.consent_gate.set_consent(selected)
                    events = self.tracker.set_consent(selected)

        if events:
            self._enqueue_ram_events(
                events,
                urgent_spool=True,
                expected_epoch=captured_epoch,
            )
        if (
            self._started
            and self.config.active_eligible
            and self.tracker.authenticated_non_demo
            and self.tracker.foreground_visible
        ):
            self._submit_network_flush()
        self._wakeup_event.set()

    apply_consent = set_consent

    def reset_local_state(self) -> None:
        """Purge RAM queue, session state, spool batches, and ``installation.json``."""
        with self._lifecycle_lock:
            if self._closed:
                return
            with self.consent_gate._lock, self.tracker._lock, self._buffer_lock:
                self.consent_gate.bump_epoch()
                self.tracker.purge_in_memory_state()
                self._ram_buffer.clear()
                self._immediate_spool_needed = False
                self._pending_disk_purge = True
        self._drain_pending_disk_purge()
        self._wakeup_event.set()

    def set_account_state(
        self,
        *,
        authenticated: bool,
        is_demo: bool = False,
        account_changed: bool = False,
    ) -> None:
        """Update authenticated non-demo gate; rotate identity on logout/account switch."""
        with self._lifecycle_lock:
            if self._closed:
                return
        try:
            was_auth_fg = (
                self.tracker.authenticated_non_demo and self.tracker.foreground_visible
            )
            _, pre_epoch = self.consent_gate.snapshot()
            pre_wipe_events, should_wipe = self.tracker.set_account_state(
                authenticated=authenticated,
                is_demo=is_demo,
                account_changed=account_changed,
            )
            if pre_wipe_events:
                self._enqueue_ram_events(
                    pre_wipe_events,
                    urgent_spool=False,
                    expected_epoch=pre_epoch,
                )
            if should_wipe:
                self.flush_ram_to_spool(fsync=False)
                with self._buffer_lock:
                    self._ram_buffer.clear()
                    self._immediate_spool_needed = False
                with self._spool_io_lock:
                    self.identity_store.wipe()
            elif pre_wipe_events:
                self.flush_ram_to_spool(fsync=False)

            _, post_epoch = self.consent_gate.snapshot()
            post_events = self.tracker.start_after_account_ready()
            if post_events:
                self._enqueue_ram_events(
                    post_events,
                    urgent_spool=True,
                    expected_epoch=post_epoch,
                )
            now_auth_fg = (
                self.tracker.authenticated_non_demo and self.tracker.foreground_visible
            )
            if self._started and now_auth_fg and not was_auth_fg:
                self._submit_network_flush()
            self._wakeup_event.set()
        except Exception:
            _LOGGER.debug("Analytics set_account_state suppressed error", exc_info=True)

    def on_foreground(
        self,
        trigger: LaunchTrigger | str = LaunchTrigger.COLD_FOREGROUND,
        screen: ScreenName | str | None = None,
    ) -> None:
        """Notify that UI entered visible foreground."""
        with self._lifecycle_lock:
            if self._closed:
                return
        try:
            was_auth_fg = (
                self.tracker.authenticated_non_demo and self.tracker.foreground_visible
            )
            _, epoch = self.consent_gate.snapshot()
            events = self.tracker.on_foreground(trigger=trigger, screen=screen)
            if events:
                self._enqueue_ram_events(
                    events,
                    urgent_spool=True,
                    expected_epoch=epoch,
                )
            now_auth_fg = (
                self.tracker.authenticated_non_demo and self.tracker.foreground_visible
            )
            if self._started and now_auth_fg and not was_auth_fg:
                self._submit_network_flush()
            self._wakeup_event.set()
        except Exception:
            _LOGGER.debug("Analytics on_foreground suppressed error", exc_info=True)

    def on_background(
        self,
        reason: BackgroundReason | str = BackgroundReason.TRAY_HIDE,
    ) -> None:
        """Notify that UI entered hidden/minimized/background state; flush RAM to spool."""
        with self._lifecycle_lock:
            if self._closed:
                return
        try:
            _, epoch = self.consent_gate.snapshot()
            events = self.tracker.on_background(reason=reason)
            if events:
                self._enqueue_ram_events(
                    events,
                    urgent_spool=False,
                    expected_epoch=epoch,
                )
            self.flush_ram_to_spool(fsync=False)
            self._wakeup_event.set()
        except Exception:
            _LOGGER.debug("Analytics on_background suppressed error", exc_info=True)

    def record_interaction(self, screen: ScreenName | str | None = None) -> None:
        """Record foreground user activity without emitting a discrete feature event."""
        with self._lifecycle_lock:
            if self._closed:
                return
        try:
            _, epoch = self.consent_gate.snapshot()
            events = self.tracker.record_interaction(screen=screen)
            if events:
                self._enqueue_ram_events(
                    events,
                    urgent_spool=True,
                    expected_epoch=epoch,
                )
            self._wakeup_event.set()
        except Exception:
            _LOGGER.debug("Analytics record_interaction suppressed error", exc_info=True)

    # ── Typed Capture Methods (Zero-PII, Never Raise) ──

    def capture_screen_viewed(
        self,
        screen: ScreenName | str,
        nav_source: NavSource | str = NavSource.TAB_BAR,
    ) -> bool:
        """Record a ``1.5s`` dwell/dedupe-debounced ``uth_screen_viewed``."""
        with self._lifecycle_lock:
            if self._closed:
                return False
        try:
            was_eligible = self.tracker.authenticated_non_demo and self.tracker.foreground_visible
            _, epoch = self.consent_gate.snapshot()
            events = self.tracker.capture_screen_viewed(screen, nav_source)
            enqueued = False
            if events:
                enqueued = self._enqueue_ram_events(events, expected_epoch=epoch)
            self._wakeup_event.set()
            return bool(
                enqueued
                or (
                    was_eligible
                    and self.config.active_eligible
                    and self.consent_gate.is_enabled_at(epoch)
                )
            )
        except Exception:
            return False

    def capture_filter_applied(
        self,
        surface: FilterSurface | str,
        filter_kind: FilterKind | str,
    ) -> bool:
        """Record a ``2.0s`` debounced ``uth_filter_applied``."""
        with self._lifecycle_lock:
            if self._closed:
                return False
        try:
            was_eligible = self.tracker.authenticated_non_demo and self.tracker.foreground_visible
            _, epoch = self.consent_gate.snapshot()
            events = self.tracker.capture_filter_applied(surface, filter_kind)
            enqueued = False
            if events:
                enqueued = self._enqueue_ram_events(events, expected_epoch=epoch)
            self._wakeup_event.set()
            return bool(
                enqueued
                or (
                    was_eligible
                    and self.config.active_eligible
                    and self.consent_gate.is_enabled_at(epoch)
                )
            )
        except Exception:
            return False

    def capture_sync_completed(
        self,
        *,
        provider: SyncProvider | str,
        trigger: SyncTrigger | str,
        outcome: SyncOutcome | str,
        duration_bucket: DurationBucket | str | None = None,
        duration_seconds: float | None = None,
        item_count_bucket: ItemCountBucket | str | None = None,
        item_count: int | None = None,
    ) -> bool:
        """Record ``uth_sync_completed`` (automatically dropped when hidden in tray)."""
        try:
            selected_trigger = SyncTrigger(trigger)
            resolved_duration = (
                DurationBucket(duration_bucket)
                if duration_bucket is not None
                else duration_bucket_or_default(duration_seconds)
            )
            resolved_items = (
                ItemCountBucket(item_count_bucket)
                if item_count_bucket is not None
                else item_count_bucket_or_default(item_count)
            )
            props = SyncCompletedProperties(
                provider=SyncProvider(provider),
                trigger=selected_trigger,
                outcome=SyncOutcome(outcome),
                duration_bucket=resolved_duration,
                item_count_bucket=resolved_items,
            )
        except Exception:
            return False
        return self._capture_immediate(
            EventName.SYNC_COMPLETED,
            props,
            human_interaction=(selected_trigger is SyncTrigger.MANUAL),
        )

    def capture_document_opened(
        self,
        *,
        doc_format: DocumentFormat | str,
        viewer_kind: ViewerKind | str,
        source_site: DocumentSourceSite | str,
        cache_hit: bool,
        load_duration_bucket: DurationBucket | str | None = None,
        load_duration_seconds: float | None = None,
    ) -> bool:
        """Record ``uth_document_opened``."""
        try:
            resolved_duration = (
                DurationBucket(load_duration_bucket)
                if load_duration_bucket is not None
                else duration_bucket_or_default(load_duration_seconds)
            )
            props = DocumentOpenedProperties(
                doc_format=DocumentFormat(doc_format),
                viewer_kind=ViewerKind(viewer_kind),
                source_site=DocumentSourceSite(source_site),
                cache_hit=cache_hit,
                load_duration_bucket=resolved_duration,
            )
        except Exception:
            return False
        return self._capture_immediate(
            EventName.DOCUMENT_OPENED,
            props,
            human_interaction=True,
        )

    def capture_course_activity_opened(
        self,
        *,
        activity_kind: CourseActivityKind | str,
        source_site: ActivitySourceSite | str,
    ) -> bool:
        """Record ``uth_course_activity_opened``."""
        try:
            props = CourseActivityOpenedProperties(
                activity_kind=CourseActivityKind(activity_kind),
                source_site=ActivitySourceSite(source_site),
            )
        except Exception:
            return False
        return self._capture_immediate(
            EventName.COURSE_ACTIVITY_OPENED,
            props,
            human_interaction=True,
        )

    def capture_quiz_action(
        self,
        *,
        action: QuizActionKind | str,
        source_site: DocumentSourceSite | str,
        duration_bucket: DurationBucket | SessionDurationBucket | str | None = None,
        duration_seconds: float | None = None,
    ) -> bool:
        """Record ``uth_quiz_action``."""
        try:
            resolved_dur: DurationBucket | SessionDurationBucket | None
            if duration_bucket is not None:
                try:
                    resolved_dur = DurationBucket(duration_bucket)
                except ValueError:
                    resolved_dur = SessionDurationBucket(duration_bucket)
            elif duration_seconds is not None:
                resolved_dur = duration_bucket_or_default(duration_seconds)
            else:
                resolved_dur = None
            props = QuizActionProperties(
                action=QuizActionKind(action),
                source_site=DocumentSourceSite(source_site),
                duration_bucket=resolved_dur,
            )
        except Exception:
            return False
        return self._capture_immediate(
            EventName.QUIZ_ACTION,
            props,
            human_interaction=True,
        )

    def capture_submission_action(
        self,
        *,
        action: SubmissionActionKind | str,
        outcome: SubmissionOutcome | str,
        file_count_bucket: FileCountBucket | str | None = None,
        file_count: int | None = None,
    ) -> bool:
        """Record ``uth_submission_action``."""
        try:
            resolved_files = (
                FileCountBucket(file_count_bucket)
                if file_count_bucket is not None
                else file_count_bucket_helper(1 if file_count is None else file_count)
            )
            props = SubmissionActionProperties(
                action=SubmissionActionKind(action),
                outcome=SubmissionOutcome(outcome),
                file_count_bucket=resolved_files,
            )
        except Exception:
            return False
        return self._capture_immediate(
            EventName.SUBMISSION_ACTION,
            props,
            human_interaction=True,
        )

    def capture_grades_interaction(
        self,
        action: GradesInteractionKind | str,
    ) -> bool:
        """Record ``uth_grades_interaction``."""
        try:
            props = GradesInteractionProperties(
                action=GradesInteractionKind(action),
            )
        except Exception:
            return False
        return self._capture_immediate(
            EventName.GRADES_INTERACTION,
            props,
            human_interaction=True,
        )

    def capture_notification_summary(
        self,
        *,
        kind: NotificationSummaryKind | str,
        delivered_bucket: DeliveredCountBucket | str | None = None,
        delivered_count: int | None = None,
        scheduled_bucket: ScheduledCountBucket | str | None = None,
        scheduled_count: int | None = None,
        had_channel_failure: bool,
        channels_mask: int,
    ) -> bool:
        """Record ``uth_notification_summary`` (passive; dropped when hidden in tray)."""
        try:
            resolved_delivered = (
                DeliveredCountBucket(delivered_bucket)
                if delivered_bucket is not None
                else delivered_count_bucket(delivered_count or 0)
            )
            resolved_scheduled = (
                ScheduledCountBucket(scheduled_bucket)
                if scheduled_bucket is not None
                else scheduled_count_bucket(scheduled_count or 0)
            )
            props = NotificationSummaryProperties(
                kind=NotificationSummaryKind(kind),
                delivered_bucket=resolved_delivered,
                scheduled_bucket=resolved_scheduled,
                had_channel_failure=had_channel_failure,
                channels_mask=channels_mask,
            )
        except Exception:
            return False
        return self._capture_immediate(
            EventName.NOTIFICATION_SUMMARY,
            props,
            human_interaction=False,
        )

    # ── Spool & Network Flush Controls ──

    def poll_timers(self) -> int:
        """Advance session timers (debounce, idle/bg expiry, heartbeat) into RAM buffer."""
        with self._lifecycle_lock:
            if self._closed:
                return 0
        consent, epoch = self.consent_gate.snapshot()
        if consent is not AnalyticsConsent.ENABLED:
            return 0
        events = self.tracker.poll_timers()
        if events:
            has_session_transition = any(
                ev.event in {EventName.SESSION_STARTED, EventName.SESSION_ENDED}
                for ev in events
            )
            if not self._enqueue_ram_events(
                events,
                urgent_spool=has_session_transition,
                expected_epoch=epoch,
            ):
                return 0
        return len(events)

    def flush_ram_to_spool(self, *, fsync: bool = False) -> int:
        """Drain buffered RAM events into atomic local spool files."""
        with self._spool_io_lock:
            self._drain_pending_disk_purge_locked()
            consent, epoch = self.consent_gate.snapshot()
            if consent is not AnalyticsConsent.ENABLED or not self.config.active_eligible:
                with self._buffer_lock:
                    self._ram_buffer.clear()
                    self._immediate_spool_needed = False
                return 0

            with self._buffer_lock:
                if not self._ram_buffer:
                    self._immediate_spool_needed = False
                    return 0
                drained = list(self._ram_buffer)
                self._ram_buffer.clear()
                self._immediate_spool_needed = False

            if not self.consent_gate.is_enabled_at(epoch):
                return 0
            try:
                result = self.spool.enqueue(drained, fsync=fsync)
            except Exception:
                _LOGGER.debug("Analytics spool write suppressed error", exc_info=True)
                return 0

            if not self.consent_gate.is_enabled_at(epoch):
                self._purge_local_files_unlocked()
                return 0
            if result.stored:
                self._spool_write_seq += 1
            return result.events_stored

    def flush_now(
        self,
        *,
        deadline_seconds: float = MAX_DELIVERY_TIMEOUT_SECONDS,
    ) -> AnalyticsFlushSummary:
        """Advance due timers, flush RAM -> spool, and execute one HTTP delivery pass."""
        self.poll_timers()
        self.flush_ram_to_spool(fsync=False)
        return self.delivery.flush_once(deadline_seconds=deadline_seconds)

    def close(self) -> None:
        """Persist ``uth_session_ended`` to local spool (``< 2ms``) and exit without network wait."""
        consent_unsubscribe: Callable[[], None] | None = None
        loop_thread: threading.Thread | None = None

        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            consent_unsubscribe = self._consent_unsubscribe
            self._consent_unsubscribe = None
            self.shutdown_event.set()
            self._wakeup_event.set()
            loop_thread = self._loop_thread
            self._loop_thread = None

            consent, epoch = self.consent_gate.snapshot()
            if consent is AnalyticsConsent.ENABLED and self.config.active_eligible:
                try:
                    closing_events = self.tracker.prepare_shutdown()
                    if closing_events:
                        self._enqueue_ram_events(
                            closing_events,
                            urgent_spool=True,
                            expected_epoch=epoch,
                        )
                    self.flush_ram_to_spool(fsync=False)
                except Exception:
                    pass
            else:
                with self._buffer_lock:
                    self._ram_buffer.clear()

        if consent_unsubscribe is not None:
            try:
                consent_unsubscribe()
            except Exception:
                pass
        if loop_thread is not None and loop_thread is not threading.current_thread():
            loop_thread.join(timeout=WORKER_JOIN_TIMEOUT_SECONDS)

    # ── Internal Worker & Queue Plumbing ──

    def _capture_immediate(
        self,
        event: EventName,
        props: Any,
        *,
        human_interaction: bool,
    ) -> bool:
        with self._lifecycle_lock:
            if self._closed:
                return False
        try:
            consent, epoch = self.consent_gate.snapshot()
            if consent is not AnalyticsConsent.ENABLED:
                return False
            events = self.tracker.capture_event(
                event,
                props,
                human_interaction=human_interaction,
            )
            if not events:
                return False
            has_session_start = any(
                ev.event is EventName.SESSION_STARTED for ev in events
            )
            return self._enqueue_ram_events(
                events,
                urgent_spool=has_session_start,
                expected_epoch=epoch,
            )
        except Exception:
            return False

    def _enqueue_ram_events(
        self,
        events: list[AnalyticsEvent],
        *,
        urgent_spool: bool = False,
        expected_epoch: int | None = None,
    ) -> bool:
        if not events:
            return False
        has_session_start = any(
            ev.event is EventName.SESSION_STARTED for ev in events
        )
        with self.consent_gate._lock, self._buffer_lock:
            if expected_epoch is not None:
                if not self.consent_gate.is_enabled_at(expected_epoch):
                    return False
            elif self.consent_gate.current is not AnalyticsConsent.ENABLED:
                return False
            self._ram_buffer.extend(events)
            if (
                urgent_spool
                or has_session_start
                or len(self._ram_buffer) >= RAM_COALESCE_BATCH_THRESHOLD
            ):
                self._immediate_spool_needed = True
        if has_session_start:
            now_mono = self._monotonic()
            self._next_network_flush_mono = min(
                self._next_network_flush_mono
                or (now_mono + SESSION_START_FLUSH_DELAY_SECONDS),
                now_mono + SESSION_START_FLUSH_DELAY_SECONDS,
            )
        self._wakeup_event.set()
        return True

    def _submit_network_flush(self) -> None:
        if (
            not self.config.active_eligible
            or self.consent_gate.current is not AnalyticsConsent.ENABLED
            or self.delivery.disabled_for_process
            or not self.tracker.authenticated_non_demo
            or not self.tracker.foreground_visible
        ):
            return
        executor: Executor | None = None
        with self._lifecycle_lock:
            if self._closed or self._delivery_in_flight:
                return
            self._delivery_in_flight = True
            executor = self._executor
        try:
            if executor is not None:
                executor.submit(self._run_network_flush_task)
            else:
                worker = threading.Thread(
                    target=self._run_network_flush_task,
                    name="uthelper-analytics-flush",
                    daemon=True,
                )
                worker.start()
        except Exception:
            with self._lifecycle_lock:
                self._delivery_in_flight = False

    def _run_network_flush_task(self) -> None:
        try:
            seq_before = self._spool_write_seq
            summary = self.delivery.flush_once(
                deadline_seconds=MAX_DELIVERY_TIMEOUT_SECONDS
            )
            if (
                summary.retained_batches == 0
                and not summary.skipped_consent
                and not summary.skipped_backoff
                and not summary.deadline_exhausted
                and not summary.skipped_unconfigured
                and not summary.disabled_invalid_key
            ):
                with self._spool_io_lock:
                    if self._spool_flushed_seq < seq_before:
                        self._spool_flushed_seq = seq_before
        except Exception:
            _LOGGER.debug("Analytics delivery worker suppressed error")
        finally:
            with self._lifecycle_lock:
                self._delivery_in_flight = False

    def _compute_loop_wait_timeout(self) -> float | None:
        if (
            not self.config.active_eligible
            or self.consent_gate.current is not AnalyticsConsent.ENABLED
            or self.delivery.disabled_for_process
        ):
            return None

        with self._buffer_lock:
            buffered_count = len(self._ram_buffer)
            urgent = self._immediate_spool_needed
        if urgent:
            return 0.0

        has_timers = self.tracker.has_pending_timers()
        can_network_flush = (
            self.tracker.authenticated_non_demo
            and self.tracker.foreground_visible
            and self._spool_write_seq > self._spool_flushed_seq
        )
        if buffered_count == 0 and not has_timers and not can_network_flush:
            return None

        base_tick = min(self._coalesce_interval, 1.0)
        if buffered_count > 0 or has_timers:
            return base_tick

        now_mono = self._monotonic()
        if self._next_network_flush_mono <= 0.0:
            return self._network_flush_interval
        return min(
            max(0.05, self._next_network_flush_mono - now_mono),
            self._network_flush_interval,
        )

    def _maintenance_loop(self) -> None:
        last_coalesce_mono = self._monotonic()
        while not self.shutdown_event.is_set():
            wait_timeout = self._compute_loop_wait_timeout()
            self._wakeup_event.wait(timeout=wait_timeout)
            self._wakeup_event.clear()
            if self.shutdown_event.is_set():
                return

            now_mono = self._monotonic()
            try:
                self.poll_timers()
            except Exception:
                pass

            with self._buffer_lock:
                buffered_count = len(self._ram_buffer)
                urgent = self._immediate_spool_needed

            if buffered_count > 0 and (
                urgent
                or buffered_count >= RAM_COALESCE_BATCH_THRESHOLD
                or (now_mono - last_coalesce_mono) >= self._coalesce_interval
            ):
                self.flush_ram_to_spool(fsync=False)
                last_coalesce_mono = now_mono

            if (
                self._next_network_flush_mono > 0.0
                and now_mono >= self._next_network_flush_mono
            ):
                self._next_network_flush_mono = now_mono + self._network_flush_interval
                self._submit_network_flush()

    def _handle_permanent_disable(self) -> None:
        self.tracker.disable_for_process()
        with self._buffer_lock:
            self._ram_buffer.clear()
            self._immediate_spool_needed = False
        self._wakeup_event.set()

    def _purge_local_files_unlocked(self) -> None:
        try:
            self.spool.clear()
            self._spool_flushed_seq = self._spool_write_seq
        except Exception:
            pass
        try:
            self.identity_store.wipe()
        except Exception:
            pass

    def _drain_pending_disk_purge_locked(self) -> None:
        with self.consent_gate._lock:
            if not self._pending_disk_purge:
                return
            self._pending_disk_purge = False
        self._purge_local_files_unlocked()

    def _drain_pending_disk_purge(self) -> None:
        with self._spool_io_lock:
            self._drain_pending_disk_purge_locked()

    def _purge_local_files_best_effort(self) -> None:
        with self._spool_io_lock:
            self._purge_local_files_unlocked()

    def _purge_all_state_and_files(self) -> None:
        with self._buffer_lock:
            self._ram_buffer.clear()
            self._immediate_spool_needed = False
        self.tracker.purge_in_memory_state()
        self._purge_local_files_best_effort()


def duration_bucket_or_default(seconds: float | None) -> DurationBucket:
    if seconds is None:
        return DurationBucket.LT_250MS
    return duration_bucket(seconds)


def item_count_bucket_or_default(count: int | None) -> ItemCountBucket:
    if count is None:
        return ItemCountBucket.ZERO
    return item_count_bucket(count)


def file_count_bucket_helper(count: int) -> FileCountBucket:
    return file_count_bucket(count)


_UNSET: Any = object()


def create_default_analytics_runtime(
    data_dir: Path | str,
    *,
    development: bool = False,
    api_key: str | None = _UNSET,
    host: str | None = _UNSET,
    app_version: str | None = None,
    release_channel: ReleaseChannel | str | None = None,
    distribution_channel: DistributionChannelName | str | None = None,
    package_format: PackageFormatName | str | None = None,
    os_family: OsFamily | str | None = None,
    device_class: DeviceClass | str | None = None,
    web_mode: bool = False,
    test_mode: bool | None = None,
    consent: AnalyticsConsent | str | None = None,
    consent_provider: Callable[[], AnalyticsConsent | str] | None = None,
    subscribe_consent_changes: (
        Callable[[Callable[[], None]], Callable[[], None]] | None
    ) = None,
    environ: Mapping[str, str] | None = None,
    assets_dir: Path | str | None = None,
    delivery_executor: Executor | None = None,
    clock: Callable[[], datetime] = _utc_now,
    monotonic: Callable[[], float] = time.monotonic,
) -> AnalyticsRuntime:
    """Construct the production ``AnalyticsRuntime`` without importing ``gui``/``core``/``diagnostics``."""
    env = os.environ if environ is None else environ
    resolved_assets = (
        Path(assets_dir)
        if assets_dir is not None
        else Path(env["FLET_ASSETS_DIR"])
        if env.get("FLET_ASSETS_DIR")
        else _SOURCE_ASSETS_DIR
    )

    needs_public_config_asset = (
        api_key is _UNSET
        or host is _UNSET
        or (not development and (release_channel is None or package_format is None))
    )
    if needs_public_config_asset:
        asset_key, asset_host, asset_env, asset_fmt, allow_private = (
            _resolve_public_posthog_pair(
                resolved_assets,
                development=development,
                environ=env,
            )
        )
    else:
        asset_key = None
        asset_host = None
        asset_env = None
        asset_fmt = None
        allow_private = bool(development)

    candidate_key = asset_key if api_key is _UNSET else api_key
    candidate_host = asset_host if host is _UNSET else host
    if (
        development
        and candidate_key
        and not candidate_host
        and api_key is not _UNSET
        and host is _UNSET
    ):
        candidate_host = DEFAULT_DEV_POSTHOG_HOST
        allow_private = True

    validated_key: str | None = None
    validated_host: str | None = None
    if candidate_key and candidate_host:
        try:
            validated_key = validate_posthog_api_key(candidate_key)
            validated_host = validate_posthog_host(
                candidate_host,
                allow_private_dev=(development or allow_private),
            )
        except AnalyticsConfigError:
            validated_key = None
            validated_host = None

    resolved_os = (
        _coerce_enum_or_default(OsFamily, os_family, OsFamily.UNKNOWN)
        if os_family is not None
        else _detect_os_family()
    )
    resolved_device = (
        _coerce_enum_or_default(DeviceClass, device_class, DeviceClass.UNKNOWN)
        if device_class is not None
        else _detect_device_class(resolved_os)
    )
    resolved_release_channel = _coerce_enum_or_default(
        ReleaseChannel,
        release_channel
        if release_channel is not None
        else ("development" if development else asset_env),
        ReleaseChannel.DEVELOPMENT if development else ReleaseChannel.RELEASE,
    )
    if distribution_channel is not None:
        resolved_dist = _coerce_enum_or_default(
            DistributionChannelName,
            distribution_channel,
            DistributionChannelName.SOURCE,
        )
    else:
        resolved_dist = _resolve_distribution_channel_name(
            resolved_assets,
            development=development,
        )
    resolved_pkg = _coerce_enum_or_default(
        PackageFormatName,
        package_format
        if package_format is not None
        else ("source" if development else asset_fmt),
        PackageFormatName.SOURCE,
    )
    resolved_version = (
        app_version
        if app_version is not None
        else _resolve_default_app_version(resolved_assets)
    )
    resolved_test_mode = (
        bool(env.get("UTH_TEST_MODE") == "1")
        if test_mode is None
        else bool(test_mode)
    )

    try:
        config = AnalyticsConfig(
            api_key=validated_key,
            host=validated_host,
            app_version=resolved_version,
            release_channel=resolved_release_channel,
            distribution_channel=resolved_dist,
            package_format=resolved_pkg,
            os_family=resolved_os,
            device_class=resolved_device,
            allow_private_host=bool(development or allow_private),
            web_mode=bool(web_mode),
            test_mode=resolved_test_mode,
        )
    except (AnalyticsConfigError, ValueError):
        config = AnalyticsConfig(
            api_key=None,
            host=None,
            app_version="unknown",
            release_channel=resolved_release_channel,
            distribution_channel=resolved_dist,
            package_format=resolved_pkg,
            os_family=resolved_os,
            device_class=resolved_device,
            web_mode=bool(web_mode),
            test_mode=resolved_test_mode,
        )

    effective_provider = consent_provider
    effective_subscribe = subscribe_consent_changes
    if effective_provider is None and consent is None:
        try:
            from config import settings, subscribe_settings_saved

            def _settings_consent_provider() -> AnalyticsConsent:
                raw_val = getattr(settings, "ANALYTICS_CONSENT", None)
                if raw_val is None:
                    crash_val = getattr(settings, "CRASH_REPORTING_CONSENT", "enabled")
                    return (
                        AnalyticsConsent.DISABLED
                        if crash_val == "disabled"
                        else AnalyticsConsent.ENABLED
                    )
                return _coerce_enum_or_default(
                    AnalyticsConsent,
                    raw_val,
                    AnalyticsConsent.DISABLED,
                )

            effective_provider = _settings_consent_provider
            if effective_subscribe is None:
                effective_subscribe = subscribe_settings_saved
        except Exception:
            effective_provider = None

    initial_choice: AnalyticsConsent | str
    if consent is not None:
        initial_choice = consent
    elif effective_provider is not None:
        try:
            initial_choice = effective_provider()
        except Exception:
            initial_choice = AnalyticsConsent.ENABLED
    else:
        initial_choice = AnalyticsConsent.ENABLED

    runtime = AnalyticsRuntime(
        data_dir=data_dir,
        config=config,
        initial_consent=initial_choice,
        consent_provider=effective_provider,
        delivery_executor=delivery_executor,
        clock=clock,
        monotonic=monotonic,
    )
    if effective_subscribe is not None:
        runtime.bind_consent_changes(effective_subscribe)
    return runtime


__all__ = [
    "AnalyticsRuntime",
    "DEFAULT_DEV_POSTHOG_HOST",
    "NETWORK_FLUSH_INTERVAL_SECONDS",
    "RAM_COALESCE_BATCH_THRESHOLD",
    "RAM_COALESCE_INTERVAL_SECONDS",
    "SESSION_START_FLUSH_DELAY_SECONDS",
    "create_default_analytics_runtime",
]
