import os
import sys
import time
import json
import logging
import threading
from pathlib import Path
from typing import Callable, Any, Dict

logger = logging.getLogger(__name__)

# Quản lý locks trong bộ nhớ theo từng tệp (Intra-process)
_file_locks: Dict[Path, threading.RLock] = {}
_file_locks_lock = threading.Lock()
_thread_local = threading.local()

def _canonical_lock_path(filepath: Path) -> Path:
    """Resolve parent directory only so transient leaf renames never alter lock identity."""
    raw = Path(os.path.abspath(str(filepath)))
    parent_resolved = raw.parent.resolve()
    return Path(os.path.normcase(os.path.abspath(str(parent_resolved / raw.name))))


def get_memory_lock(filepath: Path) -> threading.RLock:
    """Trả về hoặc tạo mới một RLock cho đường dẫn tệp cụ thể để bảo vệ đa luồng."""
    resolved = _canonical_lock_path(filepath)
    with _file_locks_lock:
        if resolved not in _file_locks:
            _file_locks[resolved] = threading.RLock()
        return _file_locks[resolved]

class SafeFileIO:
    """Cung cấp các API đọc ghi tệp nguyên tử và khóa an toàn đa luồng, đa tiến trình."""

    @staticmethod
    def get_file_lock(filepath: Path):
        """
        Khởi tạo đối tượng filelock phù hợp với nền tảng.
        Sử dụng SoftFileLock trên Mobile (Android/iOS) để tránh crash do system calls.
        Nếu thiếu package filelock, tự động fallback sang DummyLock để chống crash headless.
        """
        lock_path = filepath.with_suffix(".lock")
        
        # Nhận diện môi trường di động
        is_mobile = (
            hasattr(sys, "_ANDROID_") or 
            "android" in sys.platform.lower() or 
            hasattr(sys, "_IOS_") or
            sys.platform == 'ios'
        )
        
        try:
            from filelock import FileLock, SoftFileLock
        except ImportError:
            # Fallback DummyLock tối giản nếu thiếu thư viện filelock
            class DummyLock:
                def __init__(self, lock_file, timeout=None):
                    self.lock_file = lock_file
                def acquire(self, timeout=None):
                    class Acquirer:
                        def __enter__(self): return self
                        def __exit__(self, exc_type, exc_val, exc_tb): pass
                    return Acquirer()
            FileLock = SoftFileLock = DummyLock

        if is_mobile:
            return SoftFileLock(lock_path)
            
        try:
            return FileLock(lock_path)
        except Exception:
            return SoftFileLock(lock_path)

    @staticmethod
    def _write_unlocked_impl(filepath: Path, data: Any) -> bool:
        """Ghi tệp tạm thời và đổi tên nguyên tử (không có lock, chỉ dùng nội bộ)."""
        payload = data() if callable(data) else data
        tmp_file = filepath.with_name(
            f"{filepath.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        filepath.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4, ensure_ascii=False, default=str)
                f.flush()
                os.fsync(f.fileno())

            os.replace(str(tmp_file), str(filepath))
        except BaseException:
            try:
                tmp_file.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return True

    @staticmethod
    def write_json_atomic(filepath: Path, data: Any, max_retries: int = 10, initial_delay: float = 0.1) -> bool:
        """
        Ghi dữ liệu JSON nguyên tử và an toàn tuyệt đối.
        Sử dụng Memory Lock + File Lock + Retry Loop giải phóng mem_lock trước khi sleep.
        """
        canonical = _canonical_lock_path(filepath)
        is_in_transaction = canonical in getattr(_thread_local, "_safe_io_transactions", set())

        mem_lock = get_memory_lock(canonical)
        if is_in_transaction:
            delay = initial_delay
            for attempt in range(max_retries):
                try:
                    with mem_lock:
                        return SafeFileIO._write_unlocked_impl(filepath, data)
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(min(delay, 0.05))
                        delay = min(delay * 2, 0.05)
                    else:
                        logger.error(
                            f"Ghi file {filepath.name} trong transaction thất bại sau {max_retries} lần thử: {e}"
                        )
                        return False
            return False

        file_lock = SafeFileIO.get_file_lock(filepath)
        delay = initial_delay
        acquire_timeout = min(max(initial_delay * 2.0, 0.05), 0.25)

        for attempt in range(max_retries):
            try:
                with mem_lock:
                    with file_lock.acquire(timeout=acquire_timeout):
                        return SafeFileIO._write_unlocked_impl(filepath, data)
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Lỗi truy cập file {filepath.name} (lần thử {attempt+1}): {e}. "
                        f"Thử lại sau {delay}s..."
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, 1.0)
                else:
                    logger.error(f"Ghi file {filepath.name} thất bại hoàn toàn sau {max_retries} lần thử: {e}")
                    break
        return False

    @staticmethod
    def read_json_safe(filepath: Path, default_factory: Callable[[], Any] = dict) -> Any:
        """
        Đọc dữ liệu JSON an toàn sử dụng Memory Lock.
        Nếu đang trong transaction, file_lock đã được giữ nên việc đọc hoàn toàn an toàn khỏi tiến trình khác.
        """
        mem_lock = get_memory_lock(filepath)
        with mem_lock:
            if not filepath.exists():
                return default_factory()
            try:
                # Đọc trực tiếp dưới sự bảo vệ của RLock bộ nhớ
                with open(filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Đọc file {filepath.name} thất bại: {e}")
                return default_factory()

    @staticmethod
    def transaction(filepath: Path, timeout: float = 5.0):
        """
        Context manager bọc luồng Read-Modify-Write.
        Bảo vệ toàn vẹn dữ liệu cho I/O đa tiến trình.
        """
        import contextlib
        
        @contextlib.contextmanager
        def _transaction():
            canonical = _canonical_lock_path(filepath)
            mem_lock = get_memory_lock(canonical)
            with mem_lock:
                file_lock = SafeFileIO.get_file_lock(filepath)
                with file_lock.acquire(timeout=timeout):
                    if not hasattr(_thread_local, "_safe_io_transactions"):
                        _thread_local._safe_io_transactions = set()
                    _thread_local._safe_io_transactions.add(canonical)
                    try:
                        yield
                    finally:
                        _thread_local._safe_io_transactions.discard(canonical)
        return _transaction()
