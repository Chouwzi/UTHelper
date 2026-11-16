import os
import sys
import pytest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.safe_file_io import SafeFileIO

def test_safe_file_io_headless_fallback_dummy_lock(tmp_path, monkeypatch):
    # Giả lập môi trường thiếu thư viện filelock (ModuleNotFoundError)
    # Bằng cách ép import sys.modules['filelock'] raise ImportError
    import sys
    monkeypatch.setitem(sys.modules, "filelock", None)
    
    test_file = tmp_path / "headless_settings.json"
    
    # Kiểm tra xem get_file_lock có trả về DummyLock và ghi file thành công hay không
    res = SafeFileIO.write_json_atomic(test_file, {"status": "headless_ok"})
    assert res is True
    
    # Đọc lại và xác minh dữ liệu toàn vẹn
    data = SafeFileIO.read_json_safe(test_file, dict)
    assert data.get("status") == "headless_ok"
