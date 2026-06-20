from pydantic import BaseModel, Field
import os
from pathlib import Path
import sys
import json
import logging

# ── Conditional keyring import (Windows-only) ──
try:
    import keyring
    _HAS_KEYRING = True
except ImportError:
    _HAS_KEYRING = False

# Mò đường dẫn thư mục gốc, chạy script lẻ hay đóng gói exe đều dùng được
if getattr(sys, 'frozen', False):
    # Nếu đang chạy từ file exe được build bởi PyInstaller
    BASE_DIR = Path(sys._MEIPASS)
else:
    # Nếu đang chạy mã nguồn Python thông thường
    BASE_DIR = Path(__file__).resolve().parent.parent

# Fix specifically for Flet build missing standard directories sometimes when using --add-data "assets;assets"


# ── Platform-aware data directory ──
def _get_user_data_dir() -> Path:
    """Trả về thư mục lưu trữ dữ liệu phù hợp với nền tảng."""
    if sys.platform == 'win32':
        return Path(os.getenv('APPDATA', BASE_DIR)) / "UTHElearningAlert"
    # Mobile (Android/iOS): Flet sets FLET_APP_STORAGE_DATA env var
    flet_data = os.environ.get('FLET_APP_STORAGE_DATA')
    if flet_data:
        return Path(flet_data) / "UTHElearningAlert"
    # macOS/iOS native fallback (Application Support)
    if sys.platform == 'darwin':
        return Path.home() / "Library" / "Application Support" / "UTHElearningAlert"
    # Fallback for Linux/other
    return Path.home() / ".uthelper"

_USER_DATA_DIR = _get_user_data_dir()
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
    MOODLE_SESSION: str = Field(default="", description="Session cookie dể giữ đăng nhập", exclude=True)
    MOODLE_WS_TOKEN: str = Field(default="", description="Web Services API token (stateless, valid ~30 ngày)", exclude=True)

    # Địa chỉ mấy trang web của trường mình
    MOODLE_BASE_URL: str = "https://courses.ut.edu.vn"
    MOODLE_LOGIN_URL: str = "https://courses.ut.edu.vn/login/index.php"
    PORTAL_API_BASE: str = "https://portal.ut.edu.vn/api/v1"
    
    # Cài đặt chung của ứng dụng
    THEME: str = Field(default="midnight_blue", description="Theme preset: midnight_blue, ocean_teal, sakura_pink, nord_frost, monokai_pro, solarized_dark")
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

    # Android background notifications (AlarmManager)
    BACKGROUND_CHECK_ANDROID: bool = Field(default=True, description="Kiểm tra deadline nền trên Android (AlarmManager)")
    BACKGROUND_CHECK_INTERVAL: int = Field(default=30, description="Tần suất kiểm tra nền (phút, tối thiểu 5)")

    # Mấy kênh thông báo khác (đang phát triển)
    ENABLE_DISCORD: bool = Field(default=False, description="Bật thông báo qua Discord")
    DISCORD_WEBHOOK_URL: str = Field(default="", description="Webhook URL của Discord", exclude=True)
    ENABLE_GMAIL: bool = Field(default=False, description="Bật gửi email nhắc nhở")
    GMAIL_ADDRESS: str = Field(default="", description="Địa chỉ nhận email")
    GMAIL_APP_PASSWORD: str = Field(default="", description="Mật khẩu ứng dụng Gmail (để gửi)", exclude=True)

    # Telegram
    ENABLE_TELEGRAM: bool = Field(default=False, description="Bật thông báo qua Telegram")
    TELEGRAM_BOT_TOKEN: str = Field(default="", description="Token của Telegram Bot", exclude=True)
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

    # Nhóm cài đặt cho tính năng UTHelper
    NOTIFY_MILESTONES: list = Field(default_factory=lambda: [72, 24, 3], description="Nhắc nhở trước X giờ")
    NOTIFY_MUTED_COURSES: list = Field(default_factory=list, description="Danh sách các môn bị tắt thông báo")
    NOTIFY_TYPES: list = Field(default_factory=lambda: ["quiz", "assignment", "attendance"], description="Các loại bài tập sẽ gửi cảnh báo")
    NOTIFY_DND_ENABLE: bool = Field(default=False, description="Bật chế độ không làm phiền")
    NOTIFY_DND_START: int = Field(default=23, description="Giờ bắt đầu im lặng (0-23)")
    NOTIFY_DND_END: int = Field(default=6, description="Giờ kết thúc im lặng (0-23)")
    NOTIFY_IGNORE_SUBMITTED: bool = Field(default=True, description="Im lặng với các bài đã nộp/có điểm")

    # Cài đặt hiệu năng
    PREFETCH_WORKERS: int = Field(default=4, description="Số luồng đồng thời khi prefetch chi tiết (1-10)")
    DETAIL_CACHE_TTL_SECONDS: int = Field(default=1800, description="Thời gian giữ cache chi tiết hoạt động (giây)")
    DETAIL_CACHE_MAX_ENTRIES: int = Field(default=100, description="Số hoạt động chi tiết tối đa giữ trong RAM")

# Đọc đống cài đặt từ file JSON lên để dùng
def load_settings() -> Settings:
    s = Settings()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Extract secrets from JSON data before Pydantic validation
            # (Pydantic's exclude=True prevents them from being set via **data)
            _SECRET_FIELDS = {
                'UTH_PASSWORD', 'MOODLE_SESSION', 'MOODLE_WS_TOKEN',
                'GMAIL_APP_PASSWORD', 'DISCORD_WEBHOOK_URL', 'TELEGRAM_BOT_TOKEN',
            }
            secret_values = {}
            for key in _SECRET_FIELDS:
                val = data.pop(key, None)
                if val and isinstance(val, str):
                    secret_values[key] = val
            s = Settings(**data)
            # On mobile (no keyring), restore secrets from JSON
            if not _HAS_KEYRING:
                for key, val in secret_values.items():
                    setattr(s, key, val)
            # Legacy: also restore UTH_PASSWORD from JSON if present
            elif 'UTH_PASSWORD' in secret_values:
                s.UTH_PASSWORD = secret_values['UTH_PASSWORD']
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to load settings from {CONFIG_FILE}: {e}")
            
    # Khôi phục tất cả secrets từ keyring (chỉ trên platforms có keyring)
    _SECRETS = {
        'UTH_PASSWORD': 'password',
        'MOODLE_SESSION': 'moodle_session',
        'MOODLE_WS_TOKEN': 'ws_token',
        'GMAIL_APP_PASSWORD': 'gmail_app_password',
        'DISCORD_WEBHOOK_URL': 'discord_webhook',
        'TELEGRAM_BOT_TOKEN': 'telegram_bot_token',
    }
    if _HAS_KEYRING:
        for attr, key_suffix in _SECRETS.items():
            try:
                val = keyring.get_password(KEYRING_SERVICE_NAME, key_suffix)
                if val:
                    setattr(s, attr, val)
            except Exception as e:
                logging.getLogger(__name__).warning(f"Keyring read failed for {attr}: {e}")
    
    # Legacy: migrate password from old key format
    if _HAS_KEYRING and s.UTH_USERNAME and not s.UTH_PASSWORD:
        try:
            kp = keyring.get_password(KEYRING_SERVICE_NAME, s.UTH_USERNAME)
            if kp:
                s.UTH_PASSWORD = kp
        except Exception:
            pass
            
    return s

settings = load_settings()

def save_settings():
    """Tiện tay lưu luôn đống setting hiện tại xuống ổ cứng."""
    _logger = logging.getLogger(__name__)

    # --- Step 1: Save settings to JSON ---
    # On mobile (no keyring), include secrets in JSON since
    # Android's app-private storage is already sandboxed.
    json_ok = False
    try:
        if _HAS_KEYRING:
            # Desktop: exclude secrets from JSON (saved to keyring in Step 2)
            data = settings.model_dump()
        else:
            # Mobile: include ALL fields including secrets
            data = settings.model_dump()
            _SECRET_FIELDS = {
                'UTH_PASSWORD', 'MOODLE_SESSION', 'MOODLE_WS_TOKEN',
                'GMAIL_APP_PASSWORD', 'DISCORD_WEBHOOK_URL', 'TELEGRAM_BOT_TOKEN',
            }
            for field_name in _SECRET_FIELDS:
                val = getattr(settings, field_name, '')
                if val:
                    data[field_name] = val
        tmp = CONFIG_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(CONFIG_FILE))
        json_ok = True
    except Exception as e:
        _logger.error(f"Failed to save settings to {CONFIG_FILE}: {e}")

    # --- Step 2: Save all secrets to keyring (independent of JSON success) ---
    _SECRETS = {
        'UTH_PASSWORD': 'password',
        'MOODLE_SESSION': 'moodle_session',
        'MOODLE_WS_TOKEN': 'ws_token',
        'GMAIL_APP_PASSWORD': 'gmail_app_password',
        'DISCORD_WEBHOOK_URL': 'discord_webhook',
        'TELEGRAM_BOT_TOKEN': 'telegram_bot_token',
    }
    if _HAS_KEYRING:
        for attr, key_suffix in _SECRETS.items():
            try:
                val = getattr(settings, attr, '')
                if val:
                    keyring.set_password(KEYRING_SERVICE_NAME, key_suffix, val)
            except Exception as e:
                _logger.warning(f"Failed to write {attr} to keyring: {e}")
    else:
        _logger.debug("Keyring not available, secrets only saved if included in JSON export")

    # --- Step 3: Cleanup legacy password from JSON file ---
    # Only strip secrets from JSON if keyring is available (Windows).
    # On mobile, keep secrets in JSON since Android's app-private storage
    # is already sandboxed and encrypted.
    if json_ok and _HAS_KEYRING:
        _SECRET_KEYS = {'UTH_PASSWORD', 'MOODLE_SESSION', 'MOODLE_WS_TOKEN',
                        'GMAIL_APP_PASSWORD', 'DISCORD_WEBHOOK_URL', 'TELEGRAM_BOT_TOKEN'}
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            stripped = {k: v for k, v in data.items() if k not in _SECRET_KEYS}
            if len(stripped) < len(data):
                tmp = CONFIG_FILE.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(stripped, f, indent=4, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(str(tmp), str(CONFIG_FILE))
                _logger.info("Cleaned legacy secrets from settings JSON file")
        except Exception as e:
            _logger.warning(f"Failed to clean legacy secrets from JSON: {e}")

