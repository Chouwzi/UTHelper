import hashlib
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

_MAX_HISTORY = 100  # Giữ tối đa 100 entries


def _history_digest(namespace: str) -> str:
    return hashlib.sha256(namespace.strip().casefold().encode("utf-8")).hexdigest()[:12]


class NotificationHistory:
    """Lưu lịch sử thông báo đã gửi (phân tách theo tài khoản khi có namespace)."""

    def __init__(
        self,
        history_dir: Optional[Path] = None,
        namespace: Optional[str] = None,
    ):
        self._explicit_dir = history_dir is not None
        if history_dir is None:
            from config import _USER_DATA_DIR, settings

            history_dir = Path(_USER_DATA_DIR)
            if namespace is None:
                namespace = str(getattr(settings, "UTH_USERNAME", "") or "").strip() or "__logged_out__"
        self._history_dir = Path(history_dir)
        self._lock = threading.Lock()
        if namespace is not None and str(namespace).strip():
            digest = _history_digest(str(namespace))
            self._path = self._history_dir / f"notification_history_{digest}.json"
        else:
            self._path = self._history_dir / "notification_history.json"

    def set_namespace(self, namespace: Optional[str]) -> None:
        """Switch history file to another account namespace."""
        norm = str(namespace or "").strip() or "__logged_out__"
        digest = _history_digest(norm)
        with self._lock:
            self._path = self._history_dir / f"notification_history_{digest}.json"

    def add(self, assignments: List[Any], channels: List[str]) -> None:
        """Ghi nhận một lần gửi thông báo."""
        from core.safe_file_io import SafeFileIO
        # Nhóm toàn bộ thao tác đọc, sửa, ghi dưới lock của tệp tin này để an toàn đa tiến trình
        with SafeFileIO.transaction(self._path):
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

