from pydantic import BaseModel, Field
import os
from pathlib import Path
import keyring

import sys
# Mò đường dẫn thư mục gốc, chạy script lẻ hay đóng gói exe đều dùng được
if getattr(sys, 'frozen', False):
    # Nếu đang chạy từ file exe được build bởi PyInstaller
    BASE_DIR = Path(sys._MEIPASS)
else:
    # Nếu đang chạy mã nguồn Python thông thường
    BASE_DIR = Path(__file__).resolve().parent.parent

# Fix specifically for Flet build missing standard directories sometimes when using --add-data "assets;assets"


import json
import logging

# Chỗ lưu cài đặt, để trong AppData
_USER_DATA_DIR = Path(os.getenv('APPDATA', BASE_DIR)) / "UTHElearningAlert"
_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = _USER_DATA_DIR / "settings.json"

KEYRING_SERVICE_NAME = "UTHElearningAlert"

class Settings(BaseModel):
    """
    Nơi chứa toàn bộ cấu hình của app. 
    Dùng Pydantic để quản lý cho nó chuyên nghiệp.
    """

    # Thông tin đăng nhập UTH
    UTH_USERNAME: str = Field(default="", description="Mã số sinh viên (MSSV)")
    UTH_PASSWORD: str = Field(default="", description="Mật khẩu đăng nhập", exclude=True)
    MOODLE_SESSION: str = Field(default="", description="Session cookie dể giữ đăng nhập")

    # Địa chỉ mấy trang web của trường mình
    MOODLE_BASE_URL: str = "https://courses.ut.edu.vn"
    MOODLE_LOGIN_URL: str = "https://courses.ut.edu.vn/login/index.php"
    PORTAL_API_BASE: str = "https://portal.ut.edu.vn/api/v1"
    
    # Cài đặt chung của ứng dụng
    THEME: str = Field(default="system", description="Giao diện: system (hệ thống), dark (tối), light (sáng)")
    CHECK_INTERVAL_MINUTES: int = Field(default=60, description="Tần suất kiểm tra thông báo tự động (phút)")
    FETCH_MONTHS: int = Field(default=1, description="Số tháng lấy sự kiện từ lịch (1-3 tháng)")

    # Bộ lọc hiển thị cho danh sách bài tập
    INCLUDE_SUBMITTED: bool = Field(default=True, description="Hiển thị cả các bài đã nộp")
    INCLUDE_GRADED: bool = Field(default=True, description="Hiển thị cả các bài đã chấm điểm")
    INCLUDE_PAST_DUE: bool = Field(default=False, description="Hiển thị cả các bài đã quá hạn")

    # Chuyện khởi động và hệ thống
    START_WITH_WINDOWS: bool = Field(default=False, description="Tự động chạy khi mở máy")
    START_MINIMIZED: bool = Field(default=True, description="Chạy ngầm khi khởi động")
    MINIMIZE_TO_TRAY: bool = Field(default=True, description="Thu nhỏ xuống khay hệ thống (System Tray)")

    # Mấy kênh thông báo khác (đang phát triển)
    ENABLE_DISCORD: bool = Field(default=False, description="Bật thông báo qua Discord")
    DISCORD_WEBHOOK_URL: str = Field(default="", description="Webhook URL của Discord")
    ENABLE_GMAIL: bool = Field(default=False, description="Bật gửi email nhắc nhở")
    GMAIL_ADDRESS: str = Field(default="", description="Địa chỉ nhận email")
    GMAIL_APP_PASSWORD: str = Field(default="", description="Mật khẩu ứng dụng Gmail (để gửi)")

    # Telegram
    ENABLE_TELEGRAM: bool = Field(default=False, description="Bật thông báo qua Telegram")
    TELEGRAM_BOT_TOKEN: str = Field(default="", description="Token của Telegram Bot")
    TELEGRAM_CHAT_ID: str = Field(default="", description="Chat ID nhận thông báo")

    # Hiển thị
    ALWAYS_ON_TOP: bool = Field(default=False, description="Luôn hiển thị cửa sổ trên cùng")

    # Tùy chỉnh màu sắc
    COLOR_CRITICAL: str = Field(default="#EF4444", description="Màu cảnh báo quá hạn/cấp bách")
    COLOR_WARNING: str = Field(default="#F59E0B", description="Màu cảnh báo sắp tới")
    COLOR_SAFE: str = Field(default="#10B981", description="Màu an toàn")
    COLOR_QUIZ: str = Field(default="#7C3AED", description="Màu tag Quiz")
    COLOR_ASSIGNMENT: str = Field(default="#2563EB", description="Màu tag Bài tập")
    COLOR_ATTENDANCE: str = Field(default="#D97706", description="Màu tag Điểm danh")
    COLOR_OPEN: str = Field(default="#0891B2", description="Màu tag Sắp mở")
    COLOR_OTHER: str = Field(default="#6B7280", description="Màu tag Sự kiện khác")


    # Debug
    DEBUG_MODE: bool = Field(default=False, description="Bật chế độ gỡ lỗi (Debug)")

    # Cài đặt các mốc thời gian báo động
    URGENCY_CRITICAL_HOURS: int = Field(default=24, description="Dưới X giờ → Cấp bách")
    URGENCY_WARNING_HOURS: int  = Field(default=72, description="Dưới X giờ → Sắp tới")
    OPENING_SOON_HOURS: int     = Field(default=72, description="Dưới X giờ (trước khi mở) → Sắp mở")
    NOTIFY_MINUTES_BEFORE: int  = Field(default=30, description="Thông báo trước X phút khi deadline gần")

    # Nhóm cài đặt cho tính năng Smart Alert
    NOTIFY_MILESTONES: list = Field(default_factory=lambda: [72, 24, 3], description="Nhắc nhở trước X giờ")
    NOTIFY_MUTED_COURSES: list = Field(default_factory=list, description="Danh sách các môn bị tắt thông báo")
    NOTIFY_TYPES: list = Field(default_factory=lambda: ["quiz", "assignment", "attendance"], description="Các loại bài tập sẽ gửi cảnh báo")
    NOTIFY_DND_ENABLE: bool = Field(default=False, description="Bật chế độ không làm phiền")
    NOTIFY_DND_START: int = Field(default=23, description="Giờ bắt đầu im lặng (0-23)")
    NOTIFY_DND_END: int = Field(default=6, description="Giờ kết thúc im lặng (0-23)")
    NOTIFY_IGNORE_SUBMITTED: bool = Field(default=True, description="Im lặng với các bài đã nộp/có điểm")

    # Cài đặt hiệu năng
    PREFETCH_WORKERS: int = Field(default=4, description="Số luồng đồng thời khi prefetch chi tiết (1-10)")

# Đọc đống cài đặt từ file JSON lên để dùng
def load_settings() -> Settings:
    s = Settings()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            legacy_pass = data.pop("UTH_PASSWORD", None)
            s = Settings(**data)
            if legacy_pass and isinstance(legacy_pass, str):
                s.UTH_PASSWORD = legacy_pass
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to load settings from {CONFIG_FILE}: {e}")
            
    if s.UTH_USERNAME:
        try:
            kp = keyring.get_password(KEYRING_SERVICE_NAME, s.UTH_USERNAME)
            if kp: s.UTH_PASSWORD = kp
        except Exception as e:
            logging.getLogger(__name__).warning(f"Keyring access failed: {e}")
            
    return s

settings = load_settings()

def save_settings():
    """Tiện tay lưu luôn đống setting hiện tại xuống ổ cứng."""
    try:
        tmp = CONFIG_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            # Pydantic v2
            json.dump(settings.model_dump(), f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(CONFIG_FILE))
    except Exception as e:
        logging.getLogger(__name__).error(f"Failed to save settings to {CONFIG_FILE}: {e}")

    try:
        if settings.UTH_USERNAME and settings.UTH_PASSWORD:
            keyring.set_password(KEYRING_SERVICE_NAME, settings.UTH_USERNAME, settings.UTH_PASSWORD)
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to write password to keyring: {e}")
