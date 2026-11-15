"""Bounded LRU temporary disk cache for previewed course files."""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import threading
import time
from pathlib import Path
from typing import Optional

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_SAFE_NAME_LEN = 120
_STALE_TMP_AGE_SECONDS = 300.0


def _safe_filename(name: str) -> str:
    leaf = (name or "file").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _SAFE_NAME_RE.sub("_", leaf).strip("._")
    if not cleaned:
        return "file"
    if len(cleaned) > _MAX_SAFE_NAME_LEN:
        stem, ext = posixpath.splitext(cleaned)
        if ext and len(ext) < _MAX_SAFE_NAME_LEN:
            cleaned = f"{stem[: _MAX_SAFE_NAME_LEN - len(ext)]}{ext}"
        else:
            cleaned = cleaned[:_MAX_SAFE_NAME_LEN]
    return cleaned


class PreviewFileCache:
    """Thread-safe bounded LRU file cache on disk with age expiry."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        *,
        max_bytes: int = 128 * 1024 * 1024,
        max_age_seconds: int = 14 * 86400,
    ) -> None:
        if cache_dir is None:
            from config import _USER_DATA_DIR

            cache_dir = _USER_DATA_DIR / "preview_cache"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max(1, int(max_bytes))
        self.max_age_seconds = max(60, int(max_age_seconds))
        self._lock = threading.RLock()

    def _entry_path(self, key_url: str, time_modified: int, file_name: str) -> Path:
        digest = hashlib.sha256(
            f"{key_url}|{int(time_modified or 0)}".encode("utf-8")
        ).hexdigest()[:24]
        safe = _safe_filename(file_name)
        return self.cache_dir / f"{digest}_{safe}"

    def get_cached_path(
        self, key_url: str, time_modified: int, file_name: str
    ) -> Optional[Path]:
        """Return a valid non-expired cached file path and refresh its LRU timestamp."""
        with self._lock:
            path = self._entry_path(key_url, time_modified, file_name)
            if not path.is_file():
                return None
            try:
                st = path.stat()
            except OSError:
                return None

            now = time.time()
            if (now - st.st_mtime) > self.max_age_seconds:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                return None

            try:
                os.utime(path, (now, now))
            except OSError:
                if not path.is_file():
                    return None
            return path

    def put_bytes(
        self, key_url: str, time_modified: int, file_name: str, data: bytes
    ) -> Path:
        """Atomically persist preview bytes and enforce LRU/age bounds."""
        with self._lock:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._entry_path(key_url, time_modified, file_name)
            tmp = (
                self.cache_dir
                / f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                with open(tmp, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                os.replace(tmp, path)
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            self.evict_if_needed(keep=path)
            return path

    def evict_if_needed(self, keep: Optional[Path] = None) -> None:
        """Remove expired files, stale temp files, and oldest entries above max_bytes."""
        with self._lock:
            if not self.cache_dir.exists():
                return
            now = time.time()
            keep_resolved = keep.resolve() if keep is not None else None
            entries: list[tuple[int, str, int, Path, bool]] = []
            total_size = 0

            for item in self.cache_dir.iterdir():
                if not item.is_file():
                    continue
                try:
                    st = item.stat()
                except OSError:
                    continue

                if item.name.endswith(".tmp"):
                    if (now - st.st_mtime) > _STALE_TMP_AGE_SECONDS:
                        try:
                            item.unlink(missing_ok=True)
                        except OSError:
                            pass
                    continue

                is_keep = (
                    keep_resolved is not None and item.resolve() == keep_resolved
                )
                if (now - st.st_mtime) > self.max_age_seconds and not is_keep:
                    try:
                        item.unlink(missing_ok=True)
                    except OSError:
                        pass
                    continue

                entries.append((st.st_mtime_ns, item.name, st.st_size, item, is_keep))
                total_size += st.st_size

            entries.sort(key=lambda row: (row[0], row[1]))
            for _, _, size, item, is_keep in entries:
                if total_size <= self.max_bytes:
                    break
                if is_keep:
                    continue
                try:
                    item.unlink(missing_ok=True)
                    total_size -= size
                except OSError:
                    continue

    def clear(self) -> None:
        """Delete all cached files and temporary files in the preview cache directory."""
        with self._lock:
            if not self.cache_dir.exists():
                return
            for item in self.cache_dir.iterdir():
                if item.is_file():
                    try:
                        item.unlink(missing_ok=True)
                    except OSError:
                        continue
