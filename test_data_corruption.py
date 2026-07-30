import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import unittest
import os
import shutil

# Mock _USER_DATA_DIR before importing config
import config
TEST_DIR = Path("test_uthelper_data_corruption")
config._USER_DATA_DIR = TEST_DIR
config.CONFIG_FILE = TEST_DIR / "settings.json"

class TestDataCorruption(unittest.TestCase):
    def setUp(self):
        if TEST_DIR.exists():
            shutil.rmtree(TEST_DIR)
        TEST_DIR.mkdir(parents=True)
        
    def tearDown(self):
        if TEST_DIR.exists():
            shutil.rmtree(TEST_DIR)
            
    def test_corrupted_json(self):
        # Write corrupted JSON
        with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write("{ \"username\": \"student\", \"password\": \"test\"") # missing closing brace
            
        print("Corrupted settings.json created.")
        
        # Load config
        try:
            data = config.load_settings()
            print("Config loaded without crashing.")
            
            # The config should fall back to default values if decoding fails
            self.assertEqual(data.UTH_USERNAME, "")
            
        except Exception as e:
            self.fail(f"Config module crashed on corrupted JSON: {e}")
            
    def test_empty_json(self):
        # Write empty file
        with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write("")
            
        # Load config
        try:
            data = config.load_settings()
            self.assertEqual(data.UTH_USERNAME, "")
            print("Config loaded from empty file without crashing.")
        except Exception as e:
            self.fail(f"Config module crashed on empty JSON: {e}")
            
if __name__ == "__main__":
    unittest.main()
