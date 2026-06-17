import json
import logging
import os
import threading
from datetime import datetime, timedelta
from core.time_utils import parse_datetime
from typing import List, Any, Dict

from .base import BaseNotifier
from notifiers.windows import WindowsNotifier
from notifiers.discord import DiscordNotifier
from notifiers.email import EmailNotifier
from config import settings as config
from models import UrgencyLevel

logger = logging.getLogger(__name__)

class NotificationManager:
    """
    Bộ não quản lý thông báo: Xử lý vụ ngủ (DND), 
    các mốc quan trọng (Milestones) và ẩn mấy môn học không quan tâm.
    """
    def __init__(self, tray_app=None, cache_file="notifications_cache.json"):
        self.notifiers: List[BaseNotifier] = []
        self._cache_path = cache_file
        self._cache_lock = threading.Lock()

        if tray_app:
            try:
                self.register(WindowsNotifier(tray_app=tray_app))
            except Exception as exc:
                logger.warning("Windows notifier disabled during startup: %r", exc)
        
    def register(self, notifier: BaseNotifier):
        self.notifiers.append(notifier)

    def _load_cache(self) -> Dict:
        if not os.path.exists(self._cache_path):
            return {}
        try:
            with self._cache_lock:
                with open(self._cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Cannot load notification cache: {e}")
            return {}

    def _save_cache(self, data: Dict):
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

        for notifier in self.notifiers:
            try:
                notifier.notify(notify_assignments)
            except Exception as e:
                logger.error(f"Failed via channel {notifier.__class__.__name__}: {e}")

        self._mark_assignments_notified(to_notify_items)

    def _filter_assignments(self, assignments: List[Any]) -> List[Dict]:
        """Filter assignments that need notification based on milestones and cache."""
        filtered = []
        cache = self._load_cache()
        now = datetime.now()

        for a in assignments:
            # Support both Assignment objects and dicts for backward compatibility
            course = getattr(a, 'course_name', '') or (a.get('course_name', '') if isinstance(a, dict) else '')
            if course in config.NOTIFY_MUTED_COURSES:
                continue

            status = getattr(a, 'submission_status', '') or (a.get('submission_status', '') if isinstance(a, dict) else '')
            if config.NOTIFY_IGNORE_SUBMITTED and status in ["submitted", "graded"]:
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

            milestones = sorted(config.NOTIFY_MILESTONES)
            matched_milestone = None

            for ms in milestones:
                if time_left_hours <= ms:
                    matched_milestone = ms
                    break

            if not matched_milestone:
                continue

            url = getattr(a, 'url', '') or (a.get('url', '') if isinstance(a, dict) else '')
            task_cache = cache.get(url, [])

            if matched_milestone not in task_cache:
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
                cache[url] = []

            if ms not in cache[url]:
                cache[url].append(ms)
                updated = True

        if updated:
            self._save_cache(cache)
