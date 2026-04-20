import json
import logging
import os
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

        if tray_app:
            self.register(WindowsNotifier(tray_app=tray_app))
        
    def register(self, notifier: BaseNotifier):
        self.notifiers.append(notifier)

    def _load_cache(self) -> Dict:
        if not os.path.exists(self._cache_path):
            return {}
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cache(self, data: Dict):
        try:
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
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

    def _filter_tasks(self, tasks: List[Dict]) -> List[Dict]:
        filtered = []
        cache = self._load_cache()
        now = datetime.now()

        for task in tasks:
            course = task.get("course", "")
            if course in config.NOTIFY_MUTED_COURSES:
                continue

            status = task.get("submission_status", "")
            if config.NOTIFY_IGNORE_SUBMITTED and status in ["submitted", "graded"]:
                continue

            deadline_str = task.get("deadline")
            if not deadline_str:
                continue
                
            # Parse deadline as timezone-aware when possible; skip if unparsable
            deadline = parse_datetime(deadline_str)
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

            url = task.get("url", "")
            task_cache = cache.get(url, [])
            
            if matched_milestone not in task_cache:
                filtered.append({
                    "task": task,
                    "milestone": matched_milestone
                })

        return filtered

    def _mark_as_notified(self, items: List[Dict]):
        cache = self._load_cache()
        for item in items:
            task = item["task"]
            ms = item["milestone"]
            url = task.get("url", "")
            
            if url not in cache:
                cache[url] = []
                
            if ms not in cache[url]:
                cache[url].append(ms)
                
        self._save_cache(cache)

    def dispatch(self, assignments: List[Any]):
        # Đang trong giờ nghỉ thì thôi, đừng làm phiền người ta
        if self._is_in_dnd():
            logger.info("Do Not Disturb is on. Skipping notifications.")
            return

        # Chuẩn hóa dữ liệu: chấp nhận cả dict lẫn Object cho linh hoạt
        tasks = []
        for a in assignments:
            if isinstance(a, dict):
                tasks.append(a)
            elif hasattr(a, '__dict__'):
                tasks.append(a.__dict__)

        # Lọc lại xem cái nào thực sự cần bắn thông báo
        to_notify_items = self._filter_tasks(tasks)

        if not to_notify_items:
            return

        class DummyAssign:
            def __init__(self, data):
                self.id = data.get("id", data.get("url"))
                self.title = data.get("title", "Không tên")
                self.urgency_str = data.get("urgency", "safe")
                if self.urgency_str == "critical":
                    self.urgency = UrgencyLevel.CRITICAL
                elif self.urgency_str == "warning":
                    self.urgency = UrgencyLevel.WARNING
                else:
                    self.urgency = UrgencyLevel.SAFE

        extracted_tasks = [DummyAssign(item["task"]) for item in to_notify_items]

        for notifier in self.notifiers:
            try:
                notifier.notify(extracted_tasks)
            except Exception as e:
                logger.error(f"Failed via channel {notifier.__class__.__name__}: {e}")

        self._mark_as_notified(to_notify_items)
