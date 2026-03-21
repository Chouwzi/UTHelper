import sys, os, re

path = r'src/config.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_base_dir = '''# Thư mục gốc của ứng dụng (nơi chứa file .env)
BASE_DIR = Path(__file__).resolve().parent.parent'''

new_base_dir = '''import sys
# Xác định thư mục root một cách an toàn cho cả khi chạy script và sau khi buid bằng PyInstaller
if getattr(sys, 'frozen', False):
    # Nếu đang chạy từ file exe được build bởi PyInstaller
    BASE_DIR = Path(sys._MEIPASS)
else:
    # Nếu đang chạy mã nguồn Python thông thường
    BASE_DIR = Path(__file__).resolve().parent.parent

# Fix specifically for Flet build missing standard directories sometimes when using --add-data "assets;assets"
'''

text = text.replace(old_base_dir, new_base_dir)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("fixed base_dir")
