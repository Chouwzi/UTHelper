import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

_MAX_HISTORY = 100  # Giữ tối đa 100 entries


class NotificationHistory:
    """Lưu lịch sử thông báo đã gửi."""

    def __init__(self, history_dir: Optional[Path] = None):
        if history_dir is None:
            from config import _USER_DATA_DIR
            history_dir = _USER_DATA_DIR
        self._path = history_dir / "notification_history.json"
        self._lock = threading.Lock()

    def add(self, assignments: List[Any], channels: List[str]) -> None:
        """Ghi nhận một lần gửi thông báo."""
        try:
            entries = self._load_raw()
            for a in assignments:
                title = getattr(a, 'title', '') or (a.get('title', '') if isinstance(a, dict) else '')
                course = getattr(a, 'course_name', '') or (a.get('course', '') if isinstance(a, dict) else '')
                url = getattr(a, 'url', '') or (a.get('url', '') if isinstance(a, dict) else '')
                deadline = getattr(a, 'deadline', None)
                if deadline is None and isinstance(a, dict):
                    deadline = a.get('deadline', '')
                deadline_str = str(deadline) if deadline else ''
                event_type = getattr(a, 'event_type', '') or (a.get('type', '') if isinstance(a, dict) else '')

                entry = {
                    "title": title,
                    "course": course,
                    "url": url,
                    "deadline": deadline_str,
                    "type": event_type,
                    "channels": channels,
                    "sent_at": datetime.now().isoformat(),
                }
                entries.insert(0, entry)  # Mới nhất lên trước

            # Giữ tối đa _MAX_HISTORY
            entries = entries[:_MAX_HISTORY]
            self._save_raw(entries)
        except Exception as e:
            logger.warning("Không thể lưu notification history: %s", e)

    def get_all(self) -> List[Dict[str, Any]]:
        """Lấy toàn bộ lịch sử."""
        return self._load_raw()

    def clear(self) -> None:
        """Xóa lịch sử."""
        self._save_raw([])

    def _load_raw(self) -> List[Dict]:
        if not self._path.exists():
            return []
        try:
            with self._lock:
                with open(str(self._path), "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            return []

    def _save_raw(self, entries: List[Dict]):
        try:
            tmp = f"{self._path}.tmp"
            with self._lock:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(entries, f, ensure_ascii=False, indent=2, default=str)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, str(self._path))
        except Exception as e:
            logger.warning("Không thể ghi notification history: %s", e)
