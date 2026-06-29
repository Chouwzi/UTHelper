import os
import sys
import time
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gui.app_controller import AppController
import flet as ft

class MockPage:
    def __init__(self):
        self.controls = []
        self.views = []
        self.window = MagicMock()
        self.on_disconnect = None
        self.on_keyboard_event = None
        self.on_view_pop = None
    
    def add(self, *args, **kwargs):
        pass

    def update(self):
        pass

    def run_task(self, func, *args, **kwargs):
        if asyncio.iscoroutinefunction(func):
            loop = asyncio.get_event_loop()
            return loop.create_task(func(*args, **kwargs))
        else:
            return func(*args, **kwargs)



@pytest.mark.anyio
async def test_app_controller_submission_race_condition():
    # Khởi tạo Mock Page
    page = MockPage()
    
    # Patch config để tránh load settings từ file thật
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("config.settings.UTH_USERNAME", "test_user")
        mp.setattr("config.settings.UTH_PASSWORD", "test_pass")
        mp.setattr("config.settings.BACKGROUND_CHECK_ANDROID", False)
        
        controller = AppController(page)
        
        # Thiết lập dữ liệu ban đầu
        url = "https://courses.ut.edu.vn/mod/assign/view.php?id=123"
        controller.all_data = [
            {
                "url": url,
                "title": "Bài tập test",
                "submission_status": "Chưa nộp",
                "deadline": "2026-12-31 23:59:59"
            }
        ]
        
        # Giả lập Moodle API trả về trạng thái cũ "Chưa nộp" chậm hơn
        # Mock orchestrator.get_latest_activities_async
        async def mock_get_latest():
            await asyncio.sleep(0.1) # Trễ mạng
            return [
                {
                    "url": url,
                    "title": "Bài tập test",
                    "submission_status": "Chưa nộp",
                    "deadline": "2026-12-31 23:59:59"
                }
            ]
        
        controller.orchestrator.get_latest_activities_async = mock_get_latest
        
        # 1. Kích hoạt cập nhật ngầm (sẽ gọi mock_get_latest)
        refresh_task = asyncio.create_task(controller._load_data_async())
        
        # Chờ 0.02s để luồng ngầm đã bắt đầu thời gian fetch
        await asyncio.sleep(0.02)
        
        # 2. Người dùng nộp bài tập (trạng thái chuyển thành "Đã nộp")
        controller._on_activity_status_changed(url, "Đã nộp")
        assert controller.all_data[0]["submission_status"] == "Đã nộp"
        
        # 3. Chờ tiến trình fetch nền hoàn thành hoàn toàn
        await refresh_task
        
        # 4. Kiểm tra xem Smart Merge có hoạt động chính xác để giữ trạng thái "Đã nộp" hay không
        assert controller.all_data[0]["submission_status"] == "Đã nộp"
