import asyncio
import inspect
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import List, Any, Dict

from core.notification_policy import (
    ActivityNotificationPolicy,
    NotificationPolicyConfig,
)
from core.notification_types import (
    ActivityNotification,
    DispatchResult,
    NotificationDiagnostics,
    ScheduleResult,
)
from .base import BaseNotifier
from notifiers.discord import DiscordNotifier
from notifiers.email import EmailNotifier
from config import settings as config

logger = logging.getLogger(__name__)

# Stale cache entries older than this are evicted
_CACHE_TTL_DAYS = 90

class NotificationManager:
    """
    Bộ não quản lý thông báo: Xử lý vụ ngủ (DND), 
    các mốc quan trọng (Milestones) và ẩn mấy môn học không quan tâm.
    """
    def __init__(self, tray_app=None, cache_file="notifications_cache.json"):
        self.notifiers: List[BaseNotifier] = []
        # Store cache in AppData alongside settings.json
        from config import _USER_DATA_DIR
        self._cache_path = str(_USER_DATA_DIR / cache_file)
        self._cache_lock = threading.Lock()

        from core.notification_history import NotificationHistory
        self._history = NotificationHistory()
        self._diagnostics = NotificationDiagnostics()

        # Platform-aware notifier registration
        from platform_utils import IS_WINDOWS
        if IS_WINDOWS and tray_app:
            try:
                from notifiers.windows import WindowsNotifier
                self.register(WindowsNotifier(tray_app=tray_app))
            except Exception as exc:
                logger.warning("Windows notifier disabled during startup: %r", exc)
        elif not IS_WINDOWS:
            try:
                from platform_utils.notifications import get_platform_notifier
                mobile_notifier = get_platform_notifier()
                self.register(mobile_notifier)
            except Exception as exc:
                logger.warning("Mobile notifier disabled: %r", exc)

        # Auto-register integration channels when credentials are configured
        if getattr(config, 'DISCORD_WEBHOOK_URL', ''):
            try:
                self.register(DiscordNotifier())
                logger.info("Discord notifier registered")
            except Exception as exc:
                logger.warning("Discord notifier failed: %r", exc)

        if getattr(config, 'TELEGRAM_BOT_TOKEN', '') and getattr(config, 'TELEGRAM_CHAT_ID', ''):
            try:
                from notifiers.telegram import TelegramNotifier
                self.register(TelegramNotifier())
                logger.info("Telegram notifier registered")
            except Exception as exc:
                logger.warning("Telegram notifier failed: %r", exc)

        if getattr(config, 'GMAIL_ADDRESS', '') and getattr(config, 'GMAIL_APP_PASSWORD', ''):
            try:
                self.register(EmailNotifier())
                logger.info("Email notifier registered")
            except Exception as exc:
                logger.warning("Email notifier failed: %r", exc)
        
    def register(self, notifier: BaseNotifier):
        self.notifiers.append(notifier)

    def _policy(self) -> ActivityNotificationPolicy:
        return ActivityNotificationPolicy(
            NotificationPolicyConfig(
                notify_types=tuple(getattr(config, "NOTIFY_TYPES", ()) or ()),
                muted_courses=tuple(
                    getattr(config, "NOTIFY_MUTED_COURSES", ()) or ()
                ),
                ignore_submitted=bool(
                    getattr(config, "NOTIFY_IGNORE_SUBMITTED", True)
                ),
                milestone_minutes=tuple(
                    int(value)
                    for value in (
                        getattr(config, "NOTIFY_MILESTONES_MINUTES", ()) or ()
                    )
                ),
                milestones=tuple(
                    int(value)
                    for value in (getattr(config, "NOTIFY_MILESTONES", ()) or ())
                ),
                minutes_before=max(
                    0, int(getattr(config, "NOTIFY_MINUTES_BEFORE", 0) or 0)
                ),
                dnd_enabled=bool(getattr(config, "NOTIFY_DND_ENABLE", False)),
                dnd_start=int(getattr(config, "NOTIFY_DND_START", 22)),
                dnd_end=int(getattr(config, "NOTIFY_DND_END", 7)),
            )
        )

    def _diagnostics_state(self) -> NotificationDiagnostics:
        if not hasattr(self, "_diagnostics"):
            self._diagnostics = NotificationDiagnostics()
        return self._diagnostics

    async def initialize(self, page=None) -> None:
        """Initialize platform channels and request their runtime permissions."""
        diagnostics = self._diagnostics_state()
        diagnostics.backend_names = []
        for notifier in self.notifiers:
            backend = getattr(notifier, "backend_name", notifier.__class__.__name__)
            diagnostics.backend_names.append(str(backend))
            setup = getattr(notifier, "setup", None)
            if not setup:
                continue
            try:
                result = setup(page)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                diagnostics.last_error = f"{notifier.__class__.__name__}: {exc}"
                logger.warning("Notifier initialization failed: %s", exc)

    def _load_cache(self) -> Dict:
        if not os.path.exists(self._cache_path):
            return {}
        try:
            from core.safe_file_io import SafeFileIO
            from pathlib import Path
            raw = SafeFileIO.read_json_safe(Path(self._cache_path), dict)
        except Exception as e:
            logger.warning(f"Cannot load notification cache: {e}")
            return {}

        # Migrate old format: {url: [milestones]} → {url: {"milestones": [...], "updated_at": "..."}}
        migrated = False
        for url, value in list(raw.items()):
            if isinstance(value, list):
                raw[url] = {
                    "milestones": value,
                    "updated_at": datetime.now().isoformat(),
                }
                migrated = True

        if migrated:
            self._save_cache(raw)

        return raw

    def _save_cache(self, data: Dict):
        # Evict stale entries before writing
        self._evict_stale_entries(data)
        try:
            from core.safe_file_io import SafeFileIO
            from pathlib import Path
            SafeFileIO.write_json_atomic(Path(self._cache_path), data)
        except Exception as e:
            logger.error(f"Cannot save notification cache: {e}")


    def _evict_stale_entries(self, data: Dict):
        """Remove cache entries older than _CACHE_TTL_DAYS."""
        cutoff = datetime.now() - timedelta(days=_CACHE_TTL_DAYS)
        stale_keys = []
        for url, entry in data.items():
            if not isinstance(entry, dict):
                stale_keys.append(url)
                continue
            updated_at_str = entry.get("updated_at", "")
            if not updated_at_str:
                stale_keys.append(url)
                continue
            try:
                updated_at = datetime.fromisoformat(updated_at_str)
                if updated_at < cutoff:
                    stale_keys.append(url)
            except (ValueError, TypeError):
                stale_keys.append(url)
        for key in stale_keys:
            del data[key]
        if stale_keys:
            logger.debug("Evicted %d stale notification cache entries", len(stale_keys))

    def _is_in_dnd(self) -> bool:
        return self._policy().is_dnd(datetime.now())

    async def dispatch(self, assignments: List[Any]) -> DispatchResult:
        """Dispatch due activity reminders without blocking the Flet event loop."""
        result = DispatchResult()
        diagnostics = self._diagnostics_state()
        diagnostics.last_fetch_at = datetime.now().isoformat()
        diagnostics.activities_seen = len(assignments)

        if self._is_in_dnd():
            logger.info("Do Not Disturb is on. Skipping notifications.")
            result.dnd_active = True
            result.filtered = len(assignments)
            diagnostics.skipped += len(assignments)
            return result

        # BUG-09 fix: Load cache once for the entire dispatch cycle
        cache = self._load_cache()
        await self._merge_native_receipts(cache)

        # A delivery receipt belongs to one channel. A successful desktop
        # toast must not suppress retrying a failed Telegram/email delivery.
        channel_items: list[tuple[Any, str, str, list[Dict]]] = []
        matched_keys: set[tuple[str, int | str]] = set()
        for notifier in self.notifiers:
            channel = notifier.__class__.__name__
            channel_key = self._channel_cache_key(notifier)
            items = self._filter_assignments(assignments, cache, channel=channel_key)
            channel_items.append((notifier, channel, channel_key, items))
            matched_keys.update((item["url"], item["milestone"]) for item in items)

        result.filtered = len(assignments) - len({key for key, _ in matched_keys})
        diagnostics.activities_matched = len(matched_keys)
        diagnostics.skipped += result.filtered

        if not matched_keys:
            return result

        result.attempted = len(matched_keys)
        delivered_keys: set[tuple[str, int | str]] = set()
        delivered_milestones: set[int | str] = set()
        for notifier, channel, channel_key, to_notify_items in channel_items:
            if not to_notify_items:
                continue
            notify_assignments = [item["assignment"] for item in to_notify_items]
            try:
                if inspect.iscoroutinefunction(notifier.notify):
                    channel_result = await notifier.notify(notify_assignments)
                else:
                    channel_result = await asyncio.to_thread(
                        notifier.notify, notify_assignments
                    )
                    if inspect.isawaitable(channel_result):
                        channel_result = await channel_result
                if channel_result is True:
                    result.successful_channels.append(channel)
                    self._mark_assignments_notified(
                        to_notify_items, cache, channel=channel_key
                    )
                    self._history.add(notify_assignments, [channel])
                    delivered_keys.update(
                        (item["url"], item["milestone"]) for item in to_notify_items
                    )
                    delivered_milestones.update(
                        item["milestone"] for item in to_notify_items
                    )
                else:
                    result.failed_channels[channel] = "backend did not confirm delivery"
            except Exception as exc:
                result.failed_channels[channel] = str(exc)
                diagnostics.last_error = f"{channel}: {exc}"
                logger.error("Failed via channel %s: %s", channel, exc)

        if delivered_keys:
            result.delivered = len(delivered_keys)
            result.milestones = list(delivered_milestones)
            diagnostics.delivered += result.delivered
        else:
            logger.warning("Tất cả notification channels đều thất bại! Sẽ thử lại lần sau.")
        return result

    async def _merge_native_receipts(self, cache: Dict) -> None:
        """Merge receipts emitted while the foreground process was sleeping."""
        changed = False
        for notifier in self.notifiers:
            consume = getattr(notifier, "consume_delivery_receipts", None)
            if not consume:
                continue
            try:
                receipts = consume()
                if inspect.isawaitable(receipts):
                    receipts = await receipts
            except Exception as exc:
                self._diagnostics_state().last_error = str(exc)
                logger.warning("Cannot consume native notification receipts: %s", exc)
                continue
            if not isinstance(receipts, (list, tuple)):
                continue
            for receipt in receipts:
                activity_key = str(receipt.get("activity_key", ""))
                milestone = receipt.get("milestone")
                if not activity_key or milestone is None:
                    continue
                entry = cache.setdefault(
                    activity_key,
                    {"milestones": [], "updated_at": datetime.now().isoformat()},
                )
                revision = str(receipt.get("deadline_revision", ""))
                if entry.get("deadline_revision") not in (None, "", revision):
                    entry["milestones"] = []
                    entry["channels"] = {}
                entry["deadline_revision"] = revision
                channel = str(receipt.get("channel", "") or "native").lower()
                delivered = entry.setdefault("channels", {}).setdefault(channel, [])
                if milestone not in delivered:
                    delivered.append(milestone)
                entry["updated_at"] = datetime.now().isoformat()
                changed = True
        if changed:
            self._save_cache(cache)

    async def reconcile_schedules(self, assignments: List[Any]) -> ScheduleResult:
        """Reconcile platform-native reminders after a successful activity fetch."""
        activities = [ActivityNotification.from_value(value) for value in assignments]
        reminders = self._policy().desired_schedules(activities, datetime.now())
        aggregate = ScheduleResult(desired=len(reminders))
        diagnostics = self._diagnostics_state()
        for notifier in self.notifiers:
            reconcile = getattr(notifier, "reconcile_schedules", None)
            if not reconcile:
                continue
            try:
                channel_result = reconcile(reminders)
                if inspect.isawaitable(channel_result):
                    channel_result = await channel_result
                if isinstance(channel_result, ScheduleResult):
                    aggregate.scheduled += channel_result.scheduled
                    aggregate.cancelled += channel_result.cancelled
                    aggregate.failed += channel_result.failed
                    aggregate.errors.extend(channel_result.errors)
            except Exception as exc:
                aggregate.failed += 1
                aggregate.errors.append(str(exc))
                diagnostics.last_error = str(exc)
        diagnostics.scheduled += aggregate.scheduled
        diagnostics.cancelled += aggregate.cancelled
        return aggregate

    async def cancel_activity(self, activity_id: str) -> int:
        """Cancel all native reminders associated with a Moodle activity ID."""
        cancelled = 0
        for notifier in self.notifiers:
            cancel = getattr(notifier, "cancel_activity", None)
            if not cancel:
                continue
            value = cancel(activity_id)
            if inspect.isawaitable(value):
                value = await value
            cancelled += int(value or 0)
        self._diagnostics_state().cancelled += cancelled
        return cancelled

    def get_diagnostics(self) -> NotificationDiagnostics:
        diagnostics = self._diagnostics_state()
        snapshot = NotificationDiagnostics(**vars(diagnostics))
        for notifier in self.notifiers:
            adapter_diagnostics = getattr(notifier, "get_diagnostics", None)
            if not adapter_diagnostics:
                continue
            try:
                values = adapter_diagnostics()
                if not isinstance(values, dict):
                    continue
                snapshot.pending_schedules += int(
                    values.get("pending_schedules", 0) or 0
                )
                snapshot.scheduled_delivered += int(
                    values.get("scheduled_delivered", 0) or 0
                )
                delivered_at = str(
                    values.get("last_scheduled_delivery_at", "") or ""
                )
                if delivered_at > snapshot.last_scheduled_delivery_at:
                    snapshot.last_scheduled_delivery_at = delivered_at
                platform_error = str(
                    values.get("last_schedule_error", "")
                    or values.get("last_toast_error", "")
                    or ""
                )
                if platform_error:
                    snapshot.last_error = platform_error
            except Exception as exc:
                logger.debug("Cannot read notifier diagnostics: %s", exc)
        return snapshot

    @staticmethod
    def _channel_cache_key(notifier: Any) -> str:
        explicit = str(getattr(notifier, "channel_name", "") or "").strip().lower()
        if explicit:
            return explicit
        name = notifier.__class__.__name__.lower()
        return name.removesuffix("notifier") or "unknown"

    def _filter_assignments(
        self,
        assignments: List[Any],
        cache: Dict = None,
        *,
        channel: str | None = None,
    ) -> List[Dict]:
        """Filter assignments that need notification based on milestones and cache."""
        filtered = []
        if cache is None:
            cache = self._load_cache()
        now = datetime.now()
        policy = self._policy()

        for a in assignments:
            activity = ActivityNotification.from_value(a)
            cache_key = activity.key
            cache_entry = cache.get(cache_key)
            # Migrate URL-keyed cache entries used by releases before 2.2.
            if cache_entry is None and activity.url in cache:
                cache_entry = cache.pop(activity.url)
                cache[cache_key] = cache_entry
            cache_entry = cache_entry or {}
            current_revision = activity.deadline_revision
            cached_revision = (
                cache_entry.get("deadline_revision", "")
                if isinstance(cache_entry, dict)
                else ""
            )
            # Backward compat: old format was a list
            if isinstance(cache_entry, list):
                task_milestones = cache_entry
            else:
                channels = cache_entry.get("channels", {})
                if channel and isinstance(channels, dict) and channel in channels:
                    task_milestones = channels.get(channel, [])
                else:
                    # Legacy global receipts apply to every channel. New
                    # entries store channel-specific receipts in ``channels``.
                    task_milestones = cache_entry.get("milestones", [])
            if cached_revision and cached_revision != current_revision:
                task_milestones = []
            # Convert delivery receipts written by the legacy hour-based
            # policy to canonical minutes once the new setting is active.
            if getattr(config, "NOTIFY_MILESTONES_MINUTES", None):
                legacy_hours = set(getattr(config, "NOTIFY_MILESTONES", ()) or ())
                task_milestones = [
                    int(value) * 60 if isinstance(value, int) and value in legacy_hours else value
                    for value in task_milestones
                ]
            candidate = policy.due_candidate(activity, task_milestones, now)
            if candidate:
                filtered.append(
                    {
                        "assignment": a,
                        "url": cache_key,
                        "milestone": candidate.milestone,
                        "deadline_revision": current_revision,
                    }
                )

        return filtered

    def _mark_assignments_notified(
        self,
        items: List[Dict],
        cache: Dict = None,
        *,
        channel: str | None = None,
    ):
        """Mark assignments as notified in cache."""
        if cache is None:
            cache = self._load_cache()
        updated = False
        for item in items:
            url = item["url"]
            ms = item["milestone"]
            deadline_revision = item.get("deadline_revision", "")

            if url not in cache:
                cache[url] = {
                    "milestones": [],
                    "deadline_revision": deadline_revision,
                    "updated_at": datetime.now().isoformat(),
                }

            entry = cache[url]
            # Backward compat: migrate list → dict in-place
            if isinstance(entry, list):
                entry = {"milestones": entry, "updated_at": datetime.now().isoformat()}
                cache[url] = entry

            if entry.get("deadline_revision") not in (None, "", deadline_revision):
                entry["milestones"] = []
                entry["channels"] = {}
            entry["deadline_revision"] = deadline_revision

            delivered = (
                entry.setdefault("channels", {}).setdefault(channel, [])
                if channel
                else entry.setdefault("milestones", [])
            )
            if ms not in delivered:
                delivered.append(ms)
                entry["updated_at"] = datetime.now().isoformat()
                updated = True

        if updated:
            self._save_cache(cache)

    @property
    def history(self):
        """Truy cập lịch sử thông báo."""
        return self._history

    async def dispatch_grade_alert(self, grade_changes: list) -> DispatchResult:
        """Send notifications for grade changes.

        Args:
            grade_changes: List of GradeChange objects from GradeMonitor.
        """
        if not grade_changes:
            return DispatchResult()

        # Check DND
        if self._is_in_dnd():
            logger.info("DND active, skipping grade alerts.")
            return DispatchResult(
                filtered=len(grade_changes), dnd_active=True
            )

        # BUG-14 fix: define helper class once, outside the loop
        class _GradeNotif:
            def __init__(self, t, b, cn):
                self.title = t
                self.body = b
                self.course_name = cn
                self.url = ""
                self.deadline_str = ""

        notifications = []
        for change in grade_changes:
            title = f"📊 Điểm mới: {change.item_name}"
            body_parts = [f"Môn: {change.course_name}"]
            if change.old_grade:
                body_parts.append(f"Điểm cũ: {change.old_grade}")
            body_parts.append(f"Điểm mới: {change.new_grade}")
            body = "\n".join(body_parts)

            notifications.append(_GradeNotif(title, body, change.course_name))

        result = DispatchResult(attempted=len(notifications))
        for notifier in self.notifiers:
            channel = notifier.__class__.__name__
            try:
                if inspect.iscoroutinefunction(notifier.notify):
                    delivered = await notifier.notify(notifications)
                else:
                    delivered = await asyncio.to_thread(notifier.notify, notifications)
                    if inspect.isawaitable(delivered):
                        delivered = await delivered
                if delivered is True:
                    result.successful_channels.append(channel)
                else:
                    result.failed_channels[channel] = "backend did not confirm delivery"
            except Exception as exc:
                result.failed_channels[channel] = str(exc)
                logger.error("Grade alert via %s failed: %s", channel, exc)
        if result.successful_channels:
            result.delivered = len(notifications)
            if hasattr(self, "_history"):
                self._history.add(notifications, result.successful_channels)
        logger.info("Dispatched %d grade alerts", result.delivered)
        return result
