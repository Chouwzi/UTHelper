import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import unittest
import threading
import time
import shutil
from concurrent.futures import ThreadPoolExecutor

import config
from core.data_orchestrator import DataOrchestrator
from core.moodle_service import MoodleService

TEST_DIR = Path("test_uthelper_concurrency")

class MockConfig:
    def __init__(self):
        self.UTH_USERNAME = "test"
        self.UTH_PASSWORD = "test"

    def get(self, key, default=""):
        return getattr(self, key, default)

class TestConcurrency(unittest.TestCase):
    def setUp(self):
        if TEST_DIR.exists():
            shutil.rmtree(TEST_DIR)
        TEST_DIR.mkdir(parents=True)
        config._USER_DATA_DIR = TEST_DIR
        config.CONFIG_FILE = TEST_DIR / "settings.json"
        
        # Write dummy config
        config.settings = config.Settings()
        config.save_settings()
        
        self.orchestrator = DataOrchestrator()
        # Mock fetch to return dummy data immediately
        self.orchestrator._fetch_via_ws_api = lambda: [{"id": 1, "name": "Test"}]
        self.orchestrator._fetch_detail_via_ws = lambda a: {"id": a.get("id"), "details": "loaded"}

    def tearDown(self):
        if TEST_DIR.exists():
            shutil.rmtree(TEST_DIR)

    def test_concurrent_ui_and_background_sync(self):
        errors = []
        
        def background_sync_task():
            try:
                for _ in range(10):
                    # Simulate background sync writing cache
                    self.orchestrator.get_latest_activities()
                    time.sleep(0.01)
            except Exception as e:
                errors.append(f"Bg Sync Error: {e}")
                
        def ui_read_task():
            try:
                for _ in range(10):
                    # Simulate UI reading data
                    data = self.orchestrator.get_cached_details_snapshot()
                    s = config.load_settings()
                    time.sleep(0.01)
            except Exception as e:
                errors.append(f"UI Read Error: {e}")
                
        def ui_write_task():
            try:
                for _ in range(10):
                    # Simulate UI updating settings
                    config.settings.CHECK_INTERVAL_MINUTES += 1
                    config.save_settings()
                    time.sleep(0.05)
            except Exception as e:
                errors.append(f"UI Write Error: {e}")

        threads = []
        # 2 bg syncs, 5 readers, 2 writers
        for _ in range(2):
            threads.append(threading.Thread(target=background_sync_task))
        for _ in range(5):
            threads.append(threading.Thread(target=ui_read_task))
        for _ in range(2):
            threads.append(threading.Thread(target=ui_write_task))
            
        for t in threads:
            t.start()
            
        for t in threads:
            t.join()
            
        self.assertEqual(len(errors), 0, f"Concurrency errors occurred: {errors}")

if __name__ == "__main__":
    unittest.main()
