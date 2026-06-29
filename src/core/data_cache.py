import json
import logging
import os
import threading
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class DataCache:
    """Cache dữ liệu activities xuống local JSON để hiển thị khi offline."""

    def __init__(self, cache_dir: Optional[Path] = None):
        if cache_dir is None:
            from config import _USER_DATA_DIR
            cache_dir = _USER_DATA_DIR
        self._cache_path = cache_dir / "activities_cache.json"
        self._lock = threading.Lock()

    def save(self, activities: List[Dict[str, Any]]) -> None:
        """Lưu danh sách activities xuống file JSON."""
        try:
            payload = {
                "version": 1,
                "saved_at": datetime.now().isoformat(),
                "count": len(activities),
                "activities": activities,
            }
            from core.safe_file_io import SafeFileIO
            SafeFileIO.write_json_atomic(self._cache_path, payload)
            logger.debug("Đã lưu cache %d activities", len(activities))
        except Exception as e:
            logger.warning("Không thể lưu data cache: %s", e)

    def load(self) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Đọc cache từ file. Trả về (activities, saved_at_iso) hoặc ([], None)."""
        if not self._cache_path.exists():
            return [], None
        try:
            from core.safe_file_io import SafeFileIO
            payload = SafeFileIO.read_json_safe(self._cache_path, dict)
            activities = payload.get("activities", [])
            saved_at = payload.get("saved_at")
            logger.info("Đã tải cache %d activities (lưu lúc %s)", len(activities), saved_at)
            return activities, saved_at
        except Exception as e:
            logger.warning("Không thể đọc data cache: %s", e)
            return [], None

    def clear(self) -> None:
        """Xóa cache file."""
        from core.safe_file_io import SafeFileIO
        lock = SafeFileIO.get_file_lock(self._cache_path)
        try:
            with lock.acquire(timeout=2):
                if self._cache_path.exists():
                    self._cache_path.unlink()
                # Xóa cả file lock nếu có thể
                lock_file = self._cache_path.with_suffix(".lock")
                if lock_file.exists():
                    try:
                        lock_file.unlink()
                    except OSError:
                        pass
        except Exception:
            pass

