import sys, os, re

# 1. Update config.py
config_path = r'src/config.py'
with open(config_path, 'r', encoding='utf-8') as f: config_text = f.read()

if "DEBUG_MODE:" not in config_text:
    config_text = config_text.replace(
        "ALWAYS_ON_TOP: bool = Field(default=False, description=\"Luôn hiển thị cửa sổ trên cùng\")",
        "ALWAYS_ON_TOP: bool = Field(default=False, description=\"Luôn hiển thị cửa sổ trên cùng\")\n\n    # Debug\n    DEBUG_MODE: bool = Field(default=False, description=\"Bật chế độ gỡ lỗi (Debug)\")"
    )
    with open(config_path, 'w', encoding='utf-8') as f: f.write(config_text)
    print("Updated config.py")

# 2. Update main.py
main_path = r'src/main.py'
with open(main_path, 'r', encoding='utf-8') as f: main_text = f.read()

new_main = '''"""UTH Elearning Alert - Entry Point."""
import logging
import flet as ft
import os
from config import settings
from gui.compact_desktop import main as app_main

logging.getLogger("flet_core").setLevel(logging.INFO)
logging.getLogger("flet").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG_MODE else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

def main():
    ft.app(target=app_main, assets_dir=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets")))

if __name__ == "__main__":
    main()'''

with open(main_path, 'w', encoding='utf-8') as f: f.write(new_main)
print("Updated main.py")

