import json
import logging
import os
import threading
from datetime import datetime, timedelta
from core.time_utils import parse_datetime
from typing import List, Any, Dict

from .base import BaseNotifier
from notifiers.discord import DiscordNotifier
from notifiers.email import EmailNotifier
from config import settings as config
from models import UrgencyLevel

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

    def _load_cache(self) -> Dict:
        if not os.path.exists(self._cache_path):
            return {}
        try:
            with self._cache_lock:
                with open(self._cache_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
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
            tmp = f"{self._cache_path}.tmp"
            with self._cache_lock:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self._cache_path)
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
        if not config.NOTIFY_DND_ENABLE:
            return False
            
        now = datetime.now()
        start = config.NOTIFY_DND_START
        end = config.NOTIFY_DND_END
        
        current_hour = now.hour
        
        if start > end: # Trường hợp DND xuyên màn đêm (qua 12h sáng)
            if current_hour >= start or current_hour < end:
                return True
        else:
            if start <= current_hour < end:
                return True
                
        return False

    def dispatch(self, assignments: List[Any]):
        """Dispatch notifications for assignments that hit milestone thresholds."""
        # Đang trong giờ nghỉ thì thôi, đừng làm phiền người ta
        if self._is_in_dnd():
            logger.info("Do Not Disturb is on. Skipping notifications.")
            return

        # Lọc lại xem cái nào thực sự cần bắn thông báo
        to_notify_items = self._filter_assignments(assignments)

        if not to_notify_items:
            return

        # Pass real Assignment objects directly — no more DummyAssign wrapper
        notify_assignments = [item["assignment"] for item in to_notify_items]

        any_success = False
        for notifier in self.notifiers:
            try:
                result = notifier.notify(notify_assignments)
                # notify() trả về True khi gửi thành công
                if result is not False:
                    any_success = True
            except Exception as e:
                logger.error(f"Failed via channel {notifier.__class__.__name__}: {e}")

        # CHỈ đánh dấu đã gửi khi ít nhất 1 channel thành công
        if any_success:
            self._mark_assignments_notified(to_notify_items)
            # Ghi lịch sử thông báo
            success_channels = [n.__class__.__name__ for n in self.notifiers]
            self._history.add(notify_assignments, success_channels)
        else:
            logger.warning("Tất cả notification channels đều thất bại! Sẽ thử lại lần sau.")

    def _filter_assignments(self, assignments: List[Any]) -> List[Dict]:
        """Filter assignments that need notification based on milestones and cache."""
        filtered = []
        cache = self._load_cache()
        now = datetime.now()
        notify_minutes = getattr(config, 'NOTIFY_MINUTES_BEFORE', 0)

        for a in assignments:
            # Support both Assignment objects and dicts for backward compatibility
            course = getattr(a, 'course_name', '') or (a.get('course_name', '') if isinstance(a, dict) else '')
            if course in config.NOTIFY_MUTED_COURSES:
                continue

            status = getattr(a, 'submission_status', '') or (a.get('submission_status', '') if isinstance(a, dict) else '')
            if config.NOTIFY_IGNORE_SUBMITTED and status in ["submitted", "graded"]:
                continue

            # Lọc theo loại hoạt động (assignment/quiz/attendance/...)
            event_type = getattr(a, 'event_type', '') or (a.get('type', '') if isinstance(a, dict) else '')
            notify_types = getattr(config, 'NOTIFY_TYPES', None)
            if notify_types and event_type and event_type not in notify_types:
                continue

            deadline = getattr(a, 'deadline', None)
            if deadline is None and isinstance(a, dict):
                deadline_str = a.get("deadline")
                deadline = parse_datetime(deadline_str) if deadline_str else None
            if not deadline:
                continue

            time_left = deadline - now
            time_left_hours = time_left.total_seconds() / 3600.0

            if time_left_hours < 0:
                continue

            url = getattr(a, 'url', '') or (a.get('url', '') if isinstance(a, dict) else '')
            cache_entry = cache.get(url, {})
            # Backward compat: old format was a list
            if isinstance(cache_entry, list):
                task_milestones = cache_entry
            else:
                task_milestones = cache_entry.get("milestones", [])

            # --- Milestone matching ---
            milestones = sorted(config.NOTIFY_MILESTONES)
            matched_milestone = None

            for ms in milestones:
                if time_left_hours <= ms:
                    matched_milestone = ms
                    break

            # --- NOTIFY_MINUTES_BEFORE: trigger when close to deadline ---
            minutes_before_triggered = False
            if notify_minutes > 0:
                time_left_minutes = time_left.total_seconds() / 60.0
                if time_left_minutes <= notify_minutes:
                    # Use a special sentinel milestone to track "minutes_before" notifications
                    sentinel = f"_min_{notify_minutes}"
                    if sentinel not in task_milestones:
                        minutes_before_triggered = True
                        matched_milestone = sentinel

            if not matched_milestone:
                continue

            if matched_milestone not in task_milestones:
                filtered.append({
                    "assignment": a,
                    "url": url,
                    "milestone": matched_milestone
                })

        return filtered

    def _mark_assignments_notified(self, items: List[Dict]):
        """Mark assignments as notified in cache."""
        cache = self._load_cache()
        updated = False
        for item in items:
            url = item["url"]
            ms = item["milestone"]

            if url not in cache:
                cache[url] = {"milestones": [], "updated_at": datetime.now().isoformat()}

            entry = cache[url]
            # Backward compat: migrate list → dict in-place
            if isinstance(entry, list):
                entry = {"milestones": entry, "updated_at": datetime.now().isoformat()}
                cache[url] = entry

            if ms not in entry["milestones"]:
                entry["milestones"].append(ms)
                entry["updated_at"] = datetime.now().isoformat()
                updated = True

        if updated:
            self._save_cache(cache)

    @property
    def history(self):
        """Truy cập lịch sử thông báo."""
        return self._history
