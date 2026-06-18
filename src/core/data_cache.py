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
            tmp = f"{self._cache_path}.tmp"
            with self._lock:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, default=str)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, str(self._cache_path))
            logger.debug("Đã lưu cache %d activities", len(activities))
        except Exception as e:
            logger.warning("Không thể lưu data cache: %s", e)

    def load(self) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Đọc cache từ file. Trả về (activities, saved_at_iso) hoặc ([], None)."""
        if not self._cache_path.exists():
            return [], None
        try:
            with self._lock:
                with open(str(self._cache_path), "r", encoding="utf-8") as f:
                    payload = json.load(f)
            activities = payload.get("activities", [])
            saved_at = payload.get("saved_at")
            logger.info("Đã tải cache %d activities (lưu lúc %s)", len(activities), saved_at)
            return activities, saved_at
        except Exception as e:
            logger.warning("Không thể đọc data cache: %s", e)
            return [], None

    def clear(self) -> None:
        """Xóa cache file."""
        try:
            if self._cache_path.exists():
                self._cache_path.unlink()
        except Exception:
            pass
