from pydantic import BaseModel, Field
import os
from pathlib import Path

import sys
# Xác định thư mục root một cách an toàn cho cả khi chạy script và sau khi buid bằng PyInstaller
if getattr(sys, 'frozen', False):
    # Nếu đang chạy từ file exe được build bởi PyInstaller
    BASE_DIR = Path(sys._MEIPASS)
else:
    # Nếu đang chạy mã nguồn Python thông thường
    BASE_DIR = Path(__file__).resolve().parent.parent

# Fix specifically for Flet build missing standard directories sometimes when using --add-data "assets;assets"


import json

# Thư mục lưu settings (AppData/Roaming/UTHAgent/settings.json thay vì .env chung chung)
_USER_DATA_DIR = Path(os.getenv('APPDATA', BASE_DIR)) / "UTHElearningAlert"
_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = _USER_DATA_DIR / "settings.json"

class Settings(BaseModel):
    """
    Cấu hình cốt lõi của ứng dụng sử dụng Pydantic Settings.
    """

    # Thông tin đăng nhập UTH
    UTH_USERNAME: str = Field(default="", description="Mã số sinh viên (MSSV)")
    UTH_PASSWORD: str = Field(default="", description="Mật khẩu đăng nhập")
    MOODLE_SESSION: str = Field(default="", description="Session cookie dể giữ đăng nhập")

    # Các đường dẫn URL của Moodle UTH
    MOODLE_BASE_URL: str = "https://courses.ut.edu.vn"
    MOODLE_LOGIN_URL: str = "https://courses.ut.edu.vn/login/index.php"
    
    # Cài đặt ứng dụng
    THEME: str = Field(default="system", description="Giao diện: system (hệ thống), dark (tối), light (sáng)")
    CHECK_INTERVAL_MINUTES: int = Field(default=60, description="Tần suất kiểm tra thông báo tự động (phút)")
    FETCH_MONTHS: int = Field(default=1, description="Số tháng lấy sự kiện từ lịch (1-3 tháng)")

    # Cài đặt bộ lọc bài tập
    INCLUDE_SUBMITTED: bool = Field(default=True, description="Hiển thị cả các bài đã nộp")
    INCLUDE_GRADED: bool = Field(default=True, description="Hiển thị cả các bài đã chấm điểm")
    INCLUDE_PAST_DUE: bool = Field(default=False, description="Hiển thị cả các bài đã quá hạn")

    # Hệ thống & Khởi động
    START_WITH_WINDOWS: bool = Field(default=False, description="Tự động chạy khi mở máy")
    START_MINIMIZED: bool = Field(default=True, description="Chạy ngầm khi khởi động")
    MINIMIZE_TO_TRAY: bool = Field(default=True, description="Thu nhỏ xuống khay hệ thống (System Tray)")

    # Discord / Email (Future)
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

    # Cài đặt ngưỡng cảnh báo
    URGENCY_CRITICAL_HOURS: int = Field(default=24, description="Dưới X giờ → Cấp bách")
    URGENCY_WARNING_HOURS: int  = Field(default=72, description="Dưới X giờ → Sắp tới")
    OPENING_SOON_HOURS: int     = Field(default=72, description="Dưới X giờ (trước khi mở) → Sắp mở")
    NOTIFY_MINUTES_BEFORE: int  = Field(default=30, description="Thông báo trước X phút khi deadline gần")

    # Nhóm cài đặt Smart Alert (Thông báo Thông minh)
    NOTIFY_MILESTONES: list = Field(default_factory=lambda: [72, 24, 3], description="Nhắc nhở trước X giờ")
    NOTIFY_MUTED_COURSES: list = Field(default_factory=list, description="Danh sách các môn bị tắt thông báo")
    NOTIFY_TYPES: list = Field(default_factory=lambda: ["quiz", "assignment", "attendance"], description="Các loại bài tập sẽ gửi cảnh báo")
    NOTIFY_DND_ENABLE: bool = Field(default=False, description="Bật chế độ không làm phiền")
    NOTIFY_DND_START: int = Field(default=23, description="Giờ bắt đầu im lặng (0-23)")
    NOTIFY_DND_END: int = Field(default=6, description="Giờ kết thúc im lặng (0-23)")
    NOTIFY_IGNORE_SUBMITTED: bool = Field(default=True, description="Im lặng với các bài đã nộp/có điểm")

    # Cài đặt hiệu năng
    PREFETCH_WORKERS: int = Field(default=4, description="Số luồng đồng thời khi prefetch chi tiết (1-10)")

# Tải configuration từ file JSON tuỳ chỉnh của ứng dụng
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

