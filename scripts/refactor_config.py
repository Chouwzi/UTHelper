import sys, os, re

# Update src/config.py
path = r'src/config.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_config = '''class Settings(BaseSettings):
    """
    Cấu hình cốt lõi của ứng dụng sử dụng Pydantic Settings.
    Tự động tải từ file .env hoặc biến môi trường.
    """
    model_config = SettingsConfigDict(
        env_file=os.path.join(BASE_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )'''

new_config = '''import json

# Thư mục lưu settings (AppData/Roaming/UTHAgent/settings.json thay vì .env chung chung)
_USER_DATA_DIR = Path(os.getenv('APPDATA', BASE_DIR)) / "UTHElearningAlert"
_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = _USER_DATA_DIR / "settings.json"

class Settings(BaseSettings):
    """
    Cấu hình cốt lõi của ứng dụng sử dụng Pydantic Settings.
    """
    model_config = SettingsConfigDict(
        extra="ignore"
    )'''

if "import json" not in text:
    text = text.replace(old_config, new_config)

    # Thêm hàm load tự động
    bottom_old = '''# Khởi tạo đối tượng settings dùng chung cho toàn dự án
settings = Settings()'''
    
    bottom_new = '''# Tải configuration từ file JSON tuỳ chỉnh của ứng dụng
def load_settings() -> Settings:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Settings(**data)
        except Exception:
            pass
    return Settings()

settings = load_settings()

def save_settings():
    """Hàm này sẽ ghi trực tiếp model hiện tại xuống đĩa."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        # Pydantic v2
        json.dump(settings.model_dump(), f, indent=4, ensure_ascii=False)
'''
    text = text.replace(bottom_old, bottom_new)
    with open(path, 'w', encoding='utf-8') as f: f.write(text)
    print("Refactored config.py")

