import os
import sys
import json
import time
import pytest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.safe_file_io import SafeFileIO, get_memory_lock

def test_safe_file_io_concurrency(tmp_path):
    test_file = tmp_path / "concurrent_settings.json"
    num_threads = 10
    barrier = Barrier(num_threads)
    
    def worker(thread_id):
        barrier.wait()
        
        # Hàm sinh dữ liệu (data generator) chạy trực tiếp bên trong File Lock của SafeFileIO
        def generate_data():
            current_data = SafeFileIO.read_json_safe(test_file, dict)
            current_data[f"thread_{thread_id}"] = f"value_{thread_id}"
            return current_data

        # Ghi đè an toàn với generator
        SafeFileIO.write_json_atomic(test_file, generate_data)


    # Chạy song song 10 luồng ghi đè tranh chấp
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        for f in futures:
            f.result()

    # Xác thực tệp tin cuối cùng phải toàn vẹn và chứa đầy đủ dữ liệu của các thread
    data = SafeFileIO.read_json_safe(test_file, dict)
    assert isinstance(data, dict)
    # Vì mỗi thread thực hiện quy trình Read-Modify-Write, nếu lock hoạt động đúng,
    # các thay đổi phải được giữ lại đầy đủ mà không bị đè mất
    assert len(data) == num_threads
    for i in range(num_threads):
        assert data.get(f"thread_{i}") == f"value_{i}"

def test_safe_file_io_windows_permission_error_retry(tmp_path):
    from unittest.mock import patch
    import errno

    test_file = tmp_path / "retry_settings.json"
    
    # Giả lập lỗi Windows lock (WinError 32)
    win_lock_error = PermissionError(errno.EACCES, "Permission denied")
    win_lock_error.winerror = 32

    # Lần gọi os.replace đầu tiên ném lỗi, lần thứ hai thành công
    with patch("os.replace") as mock_replace:
        mock_replace.side_effect = [win_lock_error, True]
        
        # Gọi ghi file, kỳ vọng cơ chế retry hoạt động và ghi thành công
        res = SafeFileIO.write_json_atomic(test_file, {"test": "ok"}, max_retries=3, initial_delay=0.01)
        assert res is True
        assert mock_replace.call_count == 2


def test_get_memory_lock_is_stable_across_leaf_existence_and_case(tmp_path):
    target = tmp_path / "Sample_Cache.json"
    lock_before = get_memory_lock(target)
    lock_dot_segment = get_memory_lock(tmp_path / "." / "Sample_Cache.json")
    target.write_text("{}", encoding="utf-8")
    lock_existing = get_memory_lock(target)
    target.unlink()
    lock_after_unlink = get_memory_lock(target)

    assert lock_before is lock_dot_segment
    assert lock_before is lock_existing
    assert lock_before is lock_after_unlink

    if os.path.normcase("A") == os.path.normcase("a"):
        lock_alt_case = get_memory_lock(Path(str(target).lower()))
        assert lock_before is lock_alt_case


def test_read_json_safe_waits_for_memory_lock_during_atomic_write(tmp_path):
    target = tmp_path / "rw_race.json"
    SafeFileIO.write_json_atomic(target, {"seeded": 1})

    read_snapshots = []
    errors = []

    def writer():
        try:
            for idx in range(40):
                SafeFileIO.write_json_atomic(target, {"seeded": 1, "seq": idx})
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def reader():
        try:
            for _ in range(80):
                snap = SafeFileIO.read_json_safe(target, dict)
                read_snapshots.append(snap)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(writer), pool.submit(reader), pool.submit(reader)]
        for fut in futures:
            fut.result()

    assert errors == []
    assert read_snapshots
    assert all(isinstance(snap, dict) and snap.get("seeded") == 1 for snap in read_snapshots)


def test_safe_file_io_releases_mem_lock_during_retry_sleep_and_keeps_lock_file(tmp_path):
    target = tmp_path / "contended.json"
    SafeFileIO.write_json_atomic(target, {"initial": True})
    lock_file = target.with_suffix(".lock")
    lock_file.write_text("", encoding="utf-8")
    os.utime(lock_file, (time.time() - 60.0, time.time() - 60.0))

    class FailingLock:
        def acquire(self, timeout=None):
            raise TimeoutError("simulated external process holding file lock")

    from unittest.mock import patch

    reader_latencies: list[float] = []
    writer_started = Barrier(2)

    def slow_failing_writer():
        writer_started.wait()
        return SafeFileIO.write_json_atomic(
            target, {"initial": False}, max_retries=3, initial_delay=0.25
        )

    def concurrent_reader():
        writer_started.wait()
        time.sleep(0.05)  # Land right inside writer's retry backoff sleep
        t0 = time.perf_counter()
        snap = SafeFileIO.read_json_safe(target, dict)
        reader_latencies.append(time.perf_counter() - t0)
        return snap

    with patch.object(SafeFileIO, "get_file_lock", return_value=FailingLock()):
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_write = pool.submit(slow_failing_writer)
            f_read = pool.submit(concurrent_reader)
            assert f_read.result() == {"initial": True}
            assert f_write.result() is False

    # Reader must NOT stall across the 0.25s + 0.50s writer backoff sleeps
    assert reader_latencies and reader_latencies[0] < 0.15
    assert lock_file.exists()


def test_safe_file_io_pid_scoped_tmp_and_no_trash_gap(tmp_path, monkeypatch):
    target = tmp_path / "atomic_replace.json"
    SafeFileIO.write_json_atomic(target, {"v": 1})

    seen_src_names: list[str] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        assert Path(dst).exists(), "Target file must remain present right up to os.replace"
        seen_src_names.append(Path(src).name)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    assert SafeFileIO.write_json_atomic(target, {"v": 2}) is True
    assert seen_src_names
    assert f".{os.getpid()}." in seen_src_names[0]
    assert SafeFileIO.read_json_safe(target, dict) == {"v": 2}
