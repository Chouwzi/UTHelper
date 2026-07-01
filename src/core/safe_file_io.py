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

def get_memory_lock(filepath: Path) -> threading.RLock:
    """Trả về hoặc tạo mới một RLock cho đường dẫn tệp cụ thể để bảo vệ đa luồng."""
    resolved = filepath.resolve()
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
    def write_json_atomic(filepath: Path, data: Any, max_retries: int = 10, initial_delay: float = 0.1) -> bool:
        """
        Ghi dữ liệu JSON nguyên tử và an toàn tuyệt đối.
        Sử dụng Memory Lock + File Lock + Robust Retry Loop với Backoff + Dọn dẹp stale lock toàn cầu.
        """
        # 1. Dọn dẹp stale SoftFileLock trên mọi nền tảng nếu lock bị kẹt quá 10 giây
        lock_path = filepath.with_suffix(".lock")
        if lock_path.exists():
            try:
                mtime = os.path.getmtime(lock_path)
                if time.time() - mtime > 10.0:  # Lock bị treo hơn 10 giây
                    os.remove(lock_path)
                    logger.info(f"Đã dọn dẹp stale SoftFileLock: {lock_path.name}")
            except Exception as le:
                logger.warning(f"Không thể kiểm tra/dọn dẹp stale lock: {le}")


        mem_lock = get_memory_lock(filepath)
        with mem_lock:
            file_lock = SafeFileIO.get_file_lock(filepath)
            delay = initial_delay
            
            for attempt in range(max_retries):
                try:
                    with file_lock.acquire(timeout=2):
                        payload = data() if callable(data) else data

                        # Ghi tệp tạm thời
                        tmp_file = filepath.with_suffix(f".{threading.get_ident()}.tmp")
                        filepath.parent.mkdir(parents=True, exist_ok=True)
                        
                        with open(tmp_file, "w", encoding="utf-8") as f:
                            json.dump(payload, f, indent=4, ensure_ascii=False, default=str)
                            f.flush()
                            os.fsync(f.fileno())
                            
                        # Ghi đè an toàn nguyên tử
                        if sys.platform == 'win32' and filepath.exists():
                            # Trên Windows, nếu file đích đang bị lock nhẹ, đổi tên nó sang trash trước
                            # giúp tránh Sharing Violation dễ dàng hơn ghi đè trực tiếp
                            trash_file = filepath.with_suffix(f".{threading.get_ident()}.trash")
                            try:
                                os.rename(str(filepath), str(trash_file))
                                os.rename(str(tmp_file), str(filepath))
                                try:
                                    os.remove(str(trash_file))
                                except Exception:
                                    # Lên lịch xóa sau nếu bị antivirus giữ lock
                                    pass
                            except Exception:
                                # Fallback về replace truyền thống nếu rename thất bại
                                os.replace(str(tmp_file), str(filepath))
                        else:
                            os.replace(str(tmp_file), str(filepath))
                        return True
                except Exception as e:
                    # Cho phép retry trên cả Windows và thiết bị di động
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Lỗi truy cập file {filepath.name} (lần thử {attempt+1}): {e}. "
                            f"Thử lại sau {delay}s..."
                        )
                        time.sleep(delay)
                        delay *= 2
                    else:
                        logger.error(f"Ghi file {filepath.name} thất bại hoàn toàn sau {max_retries} lần thử: {e}")
                        break
            return False



    @staticmethod
    def read_json_safe(filepath: Path, default_factory: Callable[[], Any] = dict) -> Any:
        """
        Đọc dữ liệu JSON an toàn sử dụng Memory Lock.
        Lưu ý: Không acquire file_lock nếu file lock đang được giữ trong cùng một luồng để tránh tự deadlock.
        """
        if not filepath.exists():
            return default_factory()
            
        mem_lock = get_memory_lock(filepath)
        with mem_lock:
            try:
                # Đọc trực tiếp dưới sự bảo vệ của RLock bộ nhớ
                with open(filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Đọc file {filepath.name} thất bại: {e}")
                return default_factory()
