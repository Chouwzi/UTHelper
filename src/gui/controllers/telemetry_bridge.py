"""Fail-safe coordinator bridging presentation/lifecycle seams to AnalyticsRuntime.

Keeps ``AppController`` and GUI views thin, enforces strict non-human DAU
suppression (hidden autostart, system-tray background polls, pre-login dialog,
and ``uthelper_demo``), and ensures analytics errors never affect UX.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from analytics.models import (
    AnalyticsConsent,
    channels_bitmask,
    classify_document_format,
    duration_bucket,
    session_duration_bucket,
)
from config import settings
from core.demo_sandbox import is_demo_account

if TYPE_CHECKING:
    from analytics.runtime import AnalyticsRuntime

logger = logging.getLogger(__name__)

_FOREGROUND_LIFECYCLE_STATES = frozenset({"show", "resume"})
_BACKGROUND_LIFECYCLE_STATES = frozenset(
    {"hide", "hidden", "inactive", "pause", "paused", "detach", "detached"}
)


def _normalize_lifecycle_state_name(event: Any) -> str:
    raw = getattr(event, "state", None)
    if raw is None:
        raw = getattr(event, "data", "")
    value = getattr(raw, "value", None)
    if isinstance(value, str) and value:
        text = value
    else:
        name = getattr(raw, "name", None)
        text = name if isinstance(name, str) and name else str(raw or "")
    cleaned = text.strip().casefold()
    if "." in cleaned:
        cleaned = cleaned.rsplit(".", 1)[-1]
    return cleaned


def _normalize_sync_trigger(raw_trigger: str) -> str:
    cleaned = str(raw_trigger or "").strip().casefold()
    if cleaned in {"manual", "retry", "force", "user"}:
        return "manual"
    if cleaned in {"startup", "startup_fg", "login", "ensure_today", "initial"}:
        return "startup_fg"
    return "foreground_poll"


def _normalize_doc_source_site(source_site: str) -> str:
    return "thnn" if str(source_site or "").strip().casefold() == "thnn" else "courses"


def _normalize_activity_source_site(source_site: str) -> str:
    cleaned = str(source_site or "").strip().casefold()
    if cleaned in {"courses", "thnn", "portal", "external"}:
        return cleaned
    return "courses"


class TelemetryBridge:
    """Thin, exception-safe presentation bridge over ``AnalyticsRuntime``."""

    def __init__(
        self,
        runtime: AnalyticsRuntime | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runtime = runtime
        self._monotonic = monotonic
        self._hidden_in_tray: bool = False
        self._today_schedule_started_mono: float | None = None
        self._today_schedule_manual: bool = False
        self._today_schedule_seen_ready: bool = False

    @property
    def runtime(self) -> AnalyticsRuntime | None:
        return self._runtime

    @property
    def foreground_visible(self) -> bool:
        rt = self._runtime
        if rt is None:
            return False
        try:
            return bool(rt.tracker.foreground_visible)
        except Exception:
            return False

    @property
    def active_session_id(self) -> str | None:
        rt = self._runtime
        if rt is None:
            return None
        try:
            return rt.active_session_id
        except Exception:
            return None

    # ── Consent, Local Reset & Account State ──

    def _sync_tracker_consent(self) -> bool:
        """Keep tracker consent aligned with consent_gate; return True iff enabled."""
        rt = self._runtime
        if rt is None:
            return False
        current = rt.current_consent
        if rt.tracker.consent is not current:
            events = rt.tracker.set_consent(current)
            if events:
                rt._enqueue_ram_events(events, urgent_spool=True)
        return current is AnalyticsConsent.ENABLED

    def apply_consent(self, consent: str) -> None:
        """Apply live user analytics consent choice (``enabled`` | ``disabled``)."""
        rt = self._runtime
        if rt is None:
            return
        try:
            rt.set_consent(consent)
            self._sync_tracker_consent()
        except Exception:
            logger.debug("TelemetryBridge.apply_consent suppressed error", exc_info=True)

    def reset_state(self) -> None:
        """Purge local analytics spool and rotate anonymous installation UUID."""
        rt = self._runtime
        if rt is None:
            return
        try:
            rt.reset_local_state()
        except Exception:
            logger.debug("TelemetryBridge.reset_state suppressed error", exc_info=True)

    def sync_account_state(
        self,
        *,
        username: str | None,
        has_credentials: bool,
        account_changed: bool = False,
    ) -> None:
        """Synchronize authenticated non-demo eligibility and account rotation."""
        rt = self._runtime
        if rt is None:
            return
        try:
            clean_user = str(username or "").strip()
            demo = bool(clean_user and is_demo_account(clean_user))
            authenticated = bool(clean_user and has_credentials and not demo)
            rt.set_account_state(
                authenticated=authenticated,
                is_demo=demo,
                account_changed=bool(account_changed),
            )
        except Exception:
            logger.debug(
                "TelemetryBridge.sync_account_state suppressed error", exc_info=True
            )

    # ── Foreground / Background / Tray Lifecycle ──

    def on_startup_visibility(
        self,
        *,
        is_autostart: bool,
        force_visible: bool,
        window_hidden_or_minimized: bool,
        screen: str = "home",
    ) -> None:
        """Apply cold-boot visibility; never enter foreground when hidden/autostart."""
        rt = self._runtime
        if rt is None:
            return
        try:
            self._sync_tracker_consent()
            if (is_autostart and not force_visible) or window_hidden_or_minimized:
                self._hidden_in_tray = True
                return
            self._hidden_in_tray = False
            rt.on_foreground("cold_foreground", screen=screen)
        except Exception:
            logger.debug(
                "TelemetryBridge.on_startup_visibility suppressed error", exc_info=True
            )

    def on_window_hidden(self, *, to_tray: bool) -> None:
        """Notify runtime that desktop window minimized or hid into system tray."""
        rt = self._runtime
        self._hidden_in_tray = bool(to_tray)
        if rt is None:
            return
        try:
            rt.on_background("tray_hide" if to_tray else "window_minimize")
        except Exception:
            logger.debug(
                "TelemetryBridge.on_window_hidden suppressed error", exc_info=True
            )

    def on_window_restored(self, *, screen: str = "home") -> None:
        """Notify runtime when a hidden/minimized window becomes visible again."""
        rt = self._runtime
        was_in_tray = self._hidden_in_tray
        self._hidden_in_tray = False
        if rt is None:
            return
        try:
            self._sync_tracker_consent()
            if not rt.tracker.foreground_visible or rt.active_session_id is None:
                trigger = "tray_restore" if was_in_tray else "window_restore"
                rt.on_foreground(trigger, screen=screen)
        except Exception:
            logger.debug(
                "TelemetryBridge.on_window_restored suppressed error", exc_info=True
            )

    def install_page_lifecycle_hook(
        self,
        page: Any,
        screen_getter: Callable[[], str],
    ) -> None:
        """Chain-safely attach ``page.on_app_lifecycle_state_change`` on Flet/mobile."""
        if page is None:
            return
        previous = getattr(page, "on_app_lifecycle_state_change", None)

        def _handle_state(event: Any) -> None:
            rt = self._runtime
            if rt is None:
                return
            try:
                self._sync_tracker_consent()
                state_name = _normalize_lifecycle_state_name(event)
                if state_name in _FOREGROUND_LIFECYCLE_STATES:
                    self._hidden_in_tray = False
                    rt.on_foreground("mobile_resume", screen=screen_getter())
                elif state_name in _BACKGROUND_LIFECYCLE_STATES:
                    rt.on_background("mobile_background")
            except Exception:
                logger.debug(
                    "TelemetryBridge lifecycle handler suppressed error", exc_info=True
                )

        if callable(previous) and inspect.iscoroutinefunction(previous):

            async def _chained_async(event: Any) -> Any:
                _handle_state(event)
                return await previous(event)

            try:
                page.on_app_lifecycle_state_change = _chained_async
            except Exception:
                logger.debug("Unable to attach async lifecycle hook", exc_info=True)
            return

        def _chained_sync(event: Any) -> Any:
            _handle_state(event)
            if callable(previous):
                res = previous(event)
                if inspect.isawaitable(res):
                    run_task = getattr(page, "run_task", None)
                    if callable(run_task):
                        try:
                            run_task(lambda: res)
                            return None
                        except Exception:
                            pass
                return res
            return None

        try:
            page.on_app_lifecycle_state_change = _chained_sync
        except Exception:
            logger.debug("Unable to attach sync lifecycle hook", exc_info=True)

    # ── Allow-Listed Discrete Events ──

    def record_interaction(self, screen: str | None = None) -> None:
        rt = self._runtime
        if rt is None:
            return
        try:
            if not self._sync_tracker_consent():
                return
            rt.record_interaction(screen=screen)
        except Exception:
            logger.debug(
                "TelemetryBridge.record_interaction suppressed error", exc_info=True
            )

    def record_screen_viewed(
        self,
        screen: str,
        nav_source: str = "tab_bar",
    ) -> None:
        rt = self._runtime
        if rt is None:
            return
        try:
            if not self._sync_tracker_consent():
                return
            rt.capture_screen_viewed(screen, nav_source)
        except Exception:
            logger.debug(
                "TelemetryBridge.record_screen_viewed suppressed error", exc_info=True
            )

    def record_filter_applied(self, surface: str, filter_kind: str) -> None:
        rt = self._runtime
        if rt is None:
            return
        try:
            if not self._sync_tracker_consent():
                return
            rt.capture_filter_applied(surface, filter_kind)
        except Exception:
            logger.debug(
                "TelemetryBridge.record_filter_applied suppressed error", exc_info=True
            )

    def record_sync_completed(
        self,
        *,
        provider: str,
        trigger: str,
        outcome: str,
        duration_seconds: float | None = None,
        item_count: int | None = None,
    ) -> None:
        """Emit ``uth_sync_completed`` strictly for foreground-visible sessions."""
        rt = self._runtime
        if rt is None:
            return
        try:
            if not self._sync_tracker_consent() or not rt.tracker.foreground_visible:
                return
            rt.capture_sync_completed(
                provider=provider,
                trigger=_normalize_sync_trigger(trigger),
                outcome=outcome,
                duration_seconds=(
                    max(0.0, float(duration_seconds))
                    if duration_seconds is not None
                    else None
                ),
                item_count=(
                    max(0, int(item_count)) if item_count is not None else 0
                ),
            )
        except Exception:
            logger.debug(
                "TelemetryBridge.record_sync_completed suppressed error", exc_info=True
            )

    def on_today_schedule_state(
        self,
        state: Any,
        *,
        manual_requested: bool = False,
    ) -> None:
        """Observe ``TodayScheduleViewState`` transitions for foreground sync metrics."""
        rt = self._runtime
        if rt is None:
            return
        try:
            status_obj = getattr(state, "status", None)
            status_name = str(
                getattr(status_obj, "value", None)
                or getattr(status_obj, "name", "")
                or status_obj
                or ""
            ).strip().casefold()
            if status_name == "loading":
                self._today_schedule_started_mono = self._monotonic()
                if manual_requested:
                    self._today_schedule_manual = True
                return
            if status_name not in {"ready", "auth_required", "error"}:
                return
            if self._today_schedule_started_mono is None and not manual_requested:
                # Hydrated initial cached snapshot before any network transition.
                if status_name != "ready" or self._today_schedule_seen_ready:
                    return
            elapsed = (
                max(0.0, self._monotonic() - self._today_schedule_started_mono)
                if self._today_schedule_started_mono is not None
                else 0.0
            )
            self._today_schedule_started_mono = None
            was_manual = bool(self._today_schedule_manual or manual_requested)
            self._today_schedule_manual = False
            trigger = (
                "manual"
                if was_manual
                else ("startup_fg" if not self._today_schedule_seen_ready else "foreground_poll")
            )
            snapshot = getattr(state, "snapshot", None)
            sessions = getattr(snapshot, "sessions", ()) if snapshot is not None else ()
            item_count = len(sessions or ())
            if status_name == "ready":
                self._today_schedule_seen_ready = True
                is_cached = bool(
                    getattr(state, "from_cache", False)
                    or getattr(state, "is_stale", False)
                )
                outcome = "offline_cached" if is_cached else "succeeded"
            elif status_name == "auth_required":
                outcome = "auth_error"
            else:
                err_code = str(getattr(state, "error_code", "") or "").casefold()
                err_msg = str(getattr(state, "error_message", "") or "").casefold()
                outcome = (
                    "maintenance"
                    if ("maintenance" in err_code or "bảo trì" in err_msg)
                    else "failed"
                )
            self.record_sync_completed(
                provider="today_schedule",
                trigger=trigger,
                outcome=outcome,
                duration_seconds=elapsed,
                item_count=item_count,
            )
        except Exception:
            logger.debug(
                "TelemetryBridge.on_today_schedule_state suppressed error",
                exc_info=True,
            )

    def record_document_opened(
        self,
        *,
        file_name: str,
        viewer_kind: str,
        source_site: str,
        cache_hit: bool,
        load_duration_seconds: float | None = None,
    ) -> None:
        """Emit ``uth_document_opened`` retaining only coarse extension format."""
        rt = self._runtime
        if rt is None:
            return
        try:
            if not self._sync_tracker_consent():
                return
            doc_format = classify_document_format(file_name)
            rt.capture_document_opened(
                doc_format=doc_format,
                viewer_kind=viewer_kind,
                source_site=_normalize_doc_source_site(source_site),
                cache_hit=bool(cache_hit),
                load_duration_seconds=(
                    max(0.0, float(load_duration_seconds))
                    if load_duration_seconds is not None
                    else 0.0
                ),
            )
        except Exception:
            logger.debug(
                "TelemetryBridge.record_document_opened suppressed error", exc_info=True
            )

    def record_course_activity_opened(
        self,
        *,
        activity_kind: str,
        source_site: str,
    ) -> None:
        rt = self._runtime
        if rt is None:
            return
        try:
            if not self._sync_tracker_consent():
                return
            rt.capture_course_activity_opened(
                activity_kind=activity_kind,
                source_site=_normalize_activity_source_site(source_site),
            )
        except Exception:
            logger.debug(
                "TelemetryBridge.record_course_activity_opened suppressed error",
                exc_info=True,
            )

    def record_quiz_action(
        self,
        *,
        action: str,
        source_site: str,
        duration_seconds: float | None = None,
    ) -> None:
        rt = self._runtime
        if rt is None:
            return
        try:
            if not self._sync_tracker_consent():
                return
            resolved_bucket = None
            if duration_seconds is not None:
                clamped = max(0.0, float(duration_seconds))
                if action == "player_closed":
                    resolved_bucket = session_duration_bucket(clamped)
                else:
                    resolved_bucket = duration_bucket(clamped)
            rt.capture_quiz_action(
                action=action,
                source_site=_normalize_doc_source_site(source_site),
                duration_bucket=resolved_bucket,
            )
        except Exception:
            logger.debug(
                "TelemetryBridge.record_quiz_action suppressed error", exc_info=True
            )

    def record_submission_action(
        self,
        *,
        action: str,
        outcome: str,
        file_count: int = 1,
    ) -> None:
        rt = self._runtime
        if rt is None:
            return
        try:
            if not self._sync_tracker_consent():
                return
            rt.capture_submission_action(
                action=action,
                outcome=outcome,
                file_count=max(1, int(file_count)),
            )
        except Exception:
            logger.debug(
                "TelemetryBridge.record_submission_action suppressed error",
                exc_info=True,
            )

    def record_grades_interaction(self, action: str) -> None:
        rt = self._runtime
        if rt is None:
            return
        try:
            if not self._sync_tracker_consent():
                return
            rt.capture_grades_interaction(action)
        except Exception:
            logger.debug(
                "TelemetryBridge.record_grades_interaction suppressed error",
                exc_info=True,
            )

    def record_notification_summary(
        self,
        *,
        kind: str,
        delivered_count: int,
        scheduled_count: int,
        had_channel_failure: bool,
        notifier_manager: Any = None,
        android_bridge: Any = None,
    ) -> None:
        """Emit ``uth_notification_summary`` only when an active foreground session exists."""
        rt = self._runtime
        if rt is None:
            return
        try:
            if (
                not self._sync_tracker_consent()
                or rt.active_session_id is None
                or not rt.tracker.foreground_visible
            ):
                return
            mask = self._compute_channels_mask(
                notifier_manager=notifier_manager,
                android_bridge=android_bridge,
            )
            rt.capture_notification_summary(
                kind=kind,
                delivered_count=max(0, int(delivered_count)),
                scheduled_count=max(0, int(scheduled_count)),
                had_channel_failure=bool(had_channel_failure),
                channels_mask=mask,
            )
        except Exception:
            logger.debug(
                "TelemetryBridge.record_notification_summary suppressed error",
                exc_info=True,
            )

    @staticmethod
    def _compute_channels_mask(
        *,
        notifier_manager: Any = None,
        android_bridge: Any = None,
    ) -> int:
        registered_names = {
            type(ch).__name__
            for ch in (getattr(notifier_manager, "notifiers", ()) or ())
        }
        has_native_mobile = bool(
            android_bridge is not None and getattr(android_bridge, "available", False)
        )
        os_toast = bool(
            has_native_mobile
            or "WindowsNotifier" in registered_names
            or "MobileNotifier" in registered_names
            or getattr(settings, "ENABLE_TRAY_NOTIF", False)
            or getattr(settings, "ENABLE_MOBILE_NOTIF", False)
        )
        discord = bool(
            "DiscordNotifier" in registered_names
            or getattr(settings, "ENABLE_DISCORD", False)
        )
        telegram = bool(
            "TelegramNotifier" in registered_names
            or getattr(settings, "ENABLE_TELEGRAM", False)
        )
        email = bool(
            "EmailNotifier" in registered_names
            or getattr(settings, "ENABLE_GMAIL", False)
        )
        return channels_bitmask(
            os_toast=os_toast,
            discord=discord,
            telegram=telegram,
            email=email,
        )

    # ── Non-Invasive Presentation View Attachments ──

    def attach_calendar_view(self, calendar_view: Any) -> None:
        """Observe mode/filter switches on ``CalendarView`` without modifying its source."""
        if calendar_view is None:
            return
        orig_toggle = getattr(calendar_view, "_toggle_view_mode", None)
        if callable(orig_toggle):

            def _wrapped_toggle(mode: str) -> Any:
                prev_mode = getattr(calendar_view, "_view_mode", None)
                result = orig_toggle(mode)
                if getattr(calendar_view, "_view_mode", None) != prev_mode:
                    self.record_filter_applied("calendar", "calendar_mode")
                return result

            calendar_view._toggle_view_mode = _wrapped_toggle
            # Re-bind mode button handlers if already constructed with old bound method.
            for mode_name, btn_attr in (
                ("month", "_mode_month_btn"),
                ("week", "_mode_week_btn"),
                ("schedule", "_mode_schedule_btn"),
            ):
                btn = getattr(calendar_view, btn_attr, None)
                if btn is not None and hasattr(btn, "on_click"):
                    btn.on_click = lambda _e, m=mode_name: _wrapped_toggle(m)

        orig_detail_filter = getattr(calendar_view, "_set_detail_filter_mode", None)
        if callable(orig_detail_filter):

            def _wrapped_detail_filter(mode: str) -> Any:
                prev_filter = getattr(calendar_view, "_detail_filter_mode", None)
                result = orig_detail_filter(mode)
                if getattr(calendar_view, "_detail_filter_mode", None) != prev_filter:
                    kind = "reset" if mode == "day" else (
                        "activity_type" if mode == "tasks" else "calendar_mode"
                    )
                    self.record_filter_applied("calendar", kind)
                return result

            calendar_view._set_detail_filter_mode = _wrapped_detail_filter
            for filter_mode, chip_attr in (
                ("day", "_filter_day_chip"),
                ("week_classes", "_filter_week_chip"),
                ("tasks", "_filter_tasks_chip"),
            ):
                chip = getattr(calendar_view, chip_attr, None)
                if chip is not None and hasattr(chip, "on_click"):
                    chip.on_click = lambda _e, fm=filter_mode: _wrapped_detail_filter(
                        fm
                    )

    def attach_courses_view(self, courses_view: Any) -> None:
        """Observe filter & catalog sync events on ``CoursesTabView`` non-invasively."""
        if courses_view is None:
            return
        state_holder = {"next_trigger": "startup_fg"}

        orig_filter = getattr(courses_view, "_handle_filter_change", None)
        if callable(orig_filter):

            def _wrapped_filter(semester: str, query: str) -> Any:
                st = getattr(courses_view, "_state", None)
                prev_sem = str(getattr(st, "semester_filter", "all") or "all")
                prev_q = str(getattr(st, "search_query", "") or "").strip()
                res = orig_filter(semester, query)
                clean_q = str(query or "").strip()
                clean_sem = str(semester or "all")
                if clean_sem == "all" and not clean_q and (prev_sem != "all" or prev_q):
                    self.record_filter_applied("courses", "reset")
                elif clean_sem != prev_sem:
                    self.record_filter_applied("courses", "semester_scope")
                elif clean_q and clean_q != prev_q:
                    self.record_filter_applied("courses", "search_used")
                return res

            courses_view._handle_filter_change = _wrapped_filter
            list_pane = getattr(courses_view, "_list_pane", None)
            if list_pane is not None and hasattr(list_pane, "_on_filter_change"):
                list_pane._on_filter_change = _wrapped_filter

        orig_refresh = getattr(courses_view, "refresh", None)
        if callable(orig_refresh):

            def _wrapped_refresh(*args: Any, **kwargs: Any) -> Any:
                state_holder["next_trigger"] = "manual"
                return orig_refresh(*args, **kwargs)

            courses_view.refresh = _wrapped_refresh
            list_pane = getattr(courses_view, "_list_pane", None)
            if list_pane is not None and hasattr(list_pane, "_on_refresh"):
                list_pane._on_refresh = _wrapped_refresh

        orig_refresh_async = getattr(courses_view, "refresh_catalog_async", None)
        if callable(orig_refresh_async):

            async def _wrapped_refresh_async(*args: Any, **kwargs: Any) -> Any:
                trigger = state_holder["next_trigger"]
                state_holder["next_trigger"] = "foreground_poll"
                t0 = self._monotonic()
                res = await orig_refresh_async(*args, **kwargs)
                elapsed = max(0.0, self._monotonic() - t0)
                st = getattr(courses_view, "_state", None)
                courses = list(getattr(st, "courses", ()) or ())
                notice = str(getattr(st, "offline_notice", "") or "").casefold()
                if "bảo trì" in notice or "maintenance" in notice:
                    outcome = "maintenance"
                elif notice and courses:
                    outcome = "offline_cached"
                elif notice and not courses:
                    outcome = "failed"
                else:
                    outcome = "succeeded"
                self.record_sync_completed(
                    provider="courses_catalog",
                    trigger=trigger,
                    outcome=outcome,
                    duration_seconds=elapsed,
                    item_count=len(courses),
                )
                return res

            courses_view.refresh_catalog_async = _wrapped_refresh_async

    def attach_quiz_activity_view(self, quiz_view: Any, source_site: str) -> None:
        """Observe attempt review opens on ``QuizActivityView`` non-invasively."""
        if quiz_view is None:
            return
        orig_open_review = getattr(quiz_view, "_open_attempt_review", None)
        if not callable(orig_open_review):
            return

        def _wrapped_open_review(*args: Any, **kwargs: Any) -> Any:
            self.record_quiz_action(
                action="review_opened",
                source_site=source_site,
            )
            return orig_open_review(*args, **kwargs)

        quiz_view._open_attempt_review = _wrapped_open_review

    def close(self) -> None:
        """Close underlying ``AnalyticsRuntime`` safely (idempotent)."""
        rt = self._runtime
        if rt is None:
            return
        try:
            rt.close()
        except Exception:
            logger.debug("TelemetryBridge.close suppressed error", exc_info=True)
