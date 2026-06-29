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
        from core.safe_file_io import get_memory_lock, SafeFileIO
        # Nhóm toàn bộ thao tác đọc, sửa, ghi dưới lock của tệp tin này
        mem_lock = get_memory_lock(self._path)
        with mem_lock:
            try:
                entries = self._load_raw_unlocked()
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
                SafeFileIO.write_json_atomic(self._path, entries)
            except Exception as e:
                logger.warning("Không thể lưu notification history: %s", e)

    def get_all(self) -> List[Dict[str, Any]]:
        """Lấy toàn bộ lịch sử."""
        from core.safe_file_io import SafeFileIO
        return SafeFileIO.read_json_safe(self._path, list)

    def clear(self) -> None:
        """Xóa lịch sử."""
        from core.safe_file_io import SafeFileIO
        SafeFileIO.write_json_atomic(self._path, [])

    def _load_raw_unlocked(self) -> List[Dict]:
        """Đọc tệp tin mà không yêu cầu lock (phục vụ cho add method đã giữ lock bên ngoài)."""
        if not self._path.exists():
            return []
        try:
            with open(str(self._path), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

