from pydantic import BaseModel, Field
import os
from pathlib import Path
import sys
import logging

# Secure storage import chain
# Tier 1: flet-secure-storage (cross-platform, native keystores)
# Tier 2: keyring (Windows Credential Manager legacy fallback)
# Tier 3: plaintext JSON (last resort, not recommended)
try:
    import flet_secure_storage as fss
    _HAS_SECURE_STORAGE = True
except ImportError:
    _HAS_SECURE_STORAGE = False

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


# Platform-aware data directory
def _get_user_data_dir() -> Path:
    """Trả về thư mục lưu trữ dữ liệu phù hợp với nền tảng."""
    if sys.platform == 'win32':
        return Path(os.getenv('APPDATA', BASE_DIR)) / "UTHElearningAlert"
    
    # Mobile (Android/iOS): Flet sets FLET_APP_STORAGE_DATA env var
    flet_data = os.environ.get('FLET_APP_STORAGE_DATA')
    if flet_data:
        return Path(flet_data) / "UTHElearningAlert"
        
    # Fallback an toàn cho Mobile khi chạy ngầm không có biến môi trường
    if hasattr(sys, '_ANDROID_') or 'android' in sys.platform.lower() or hasattr(sys, '_IOS_'):
        import tempfile
        return Path(tempfile.gettempdir()) / "UTHElearningAlert"

    # macOS/iOS native fallback (Application Support)
    if sys.platform == 'darwin':
        return Path.home() / "Library" / "Application Support" / "UTHElearningAlert"
    # Fallback for Linux/other
    return Path.home() / ".uthelper"

_USER_DATA_DIR = _get_user_data_dir()
_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = _USER_DATA_DIR / "settings.json"

KEYRING_SERVICE_NAME = "UTHElearningAlert"

# Secret field mapping (attr_name → storage_key)
_SECRET_FIELDS = {
    'UTH_PASSWORD': 'password',
    'MOODLE_SESSION': 'moodle_session',
    'MOODLE_WS_TOKEN': 'ws_token',
    'GMAIL_APP_PASSWORD': 'gmail_app_password',
    'DISCORD_WEBHOOK_URL': 'discord_webhook',
    'TELEGRAM_BOT_TOKEN': 'telegram_bot_token',
}

# Lazy-init SecureStorage instance
_secure_storage: 'fss.SecureStorage | None' = None

def _get_secure_storage() -> 'fss.SecureStorage | None':
    """Khởi tạo SecureStorage instance (lazy, singleton)."""
    global _secure_storage
    if _secure_storage is not None:
        return _secure_storage
    if not _HAS_SECURE_STORAGE:
        return None
    try:
        _secure_storage = fss.SecureStorage()
        return _secure_storage
    except Exception:
        return None

def _read_secret(key: str) -> str:
    """Đọc secret từ secure storage (tier 1 → tier 2 fallback)."""
    # Tier 1: flet-secure-storage
    ss = _get_secure_storage()
    if ss is not None:
        try:
            val = ss.read(key=key)
            if val:
                return val
        except Exception:
            pass
    # Tier 2: keyring
    if _HAS_KEYRING:
        try:
            val = keyring.get_password(KEYRING_SERVICE_NAME, key)
            if val:
                return val
        except Exception:
            pass
    return ""

def _write_secret(key: str, value: str):
    """Ghi secret vào secure storage (tier 1 → tier 2 fallback)."""
    _logger = logging.getLogger(__name__)
    # Tier 1: flet-secure-storage
    ss = _get_secure_storage()
    if ss is not None:
        try:
            if value:
                ss.write(key=key, value=value)
            else:
                try:
                    ss.delete(key=key)
                except Exception:
                    pass
            return
        except Exception as e:
            _logger.warning(f"SecureStorage write failed for {key}: {e}")
    # Tier 2: keyring
    if _HAS_KEYRING:
        try:
            if value:
                keyring.set_password(KEYRING_SERVICE_NAME, key, value)
            return
        except Exception as e:
            _logger.warning(f"Keyring write failed for {key}: {e}")

def _has_any_secure_backend() -> bool:
    """Kiểm tra có backend nào an toàn hay không."""
    return _get_secure_storage() is not None or _HAS_KEYRING

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
    POLL_INTERVAL_MINUTES: int = Field(default=15, description="Tần suất kiểm tra tự động (phút)")
    SMART_POLL_ENABLED: bool = Field(default=True, description="Bật chế độ poll thông minh (chỉ fetch khi có thay đổi)")
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
    BACKGROUND_CHECK_INTERVAL: int = Field(default=30, description="Tần suất đồng bộ activity nền (phút, tối thiểu 15)")

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
    NOTIFICATION_PROFILE: str = Field(default="balanced", description="Chế độ thông báo: quiet, balanced, exam_week")
    NOTIFY_MILESTONES: list = Field(default_factory=lambda: [72, 24, 3], description="Nhắc nhở trước X giờ")
    NOTIFY_MUTED_COURSES: list = Field(default_factory=list, description="Danh sách các môn bị tắt thông báo")
    NOTIFY_TYPES: list = Field(default_factory=lambda: ["quiz", "assignment", "attendance"], description="Các loại bài tập sẽ gửi cảnh báo")
    NOTIFY_DND_ENABLE: bool = Field(default=False, description="Bật chế độ không làm phiền")
    NOTIFY_DND_START: int = Field(default=22, description="Giờ bắt đầu im lặng (0-23)")
    NOTIFY_DND_END: int = Field(default=7, description="Giờ kết thúc im lặng (0-23)")
    NOTIFY_IGNORE_SUBMITTED: bool = Field(default=True, description="Im lặng với các bài đã nộp/có điểm")

    # Cài đặt hiệu năng
    PREFETCH_WORKERS: int = Field(default=4, description="Số luồng đồng thời khi prefetch chi tiết (1-10)")
    DETAIL_CACHE_TTL_SECONDS: int = Field(default=1800, description="Thời gian giữ cache chi tiết hoạt động (giây)")
    DETAIL_CACHE_MAX_ENTRIES: int = Field(default=100, description="Số hoạt động chi tiết tối đa giữ trong RAM")

# Đọc đống cài đặt từ file JSON lên để dùng
def load_settings() -> Settings:
    _logger = logging.getLogger(__name__)
    s = Settings()
    json_secrets: dict[str, str] = {}

    from core.safe_file_io import SafeFileIO
    data = SafeFileIO.read_json_safe(CONFIG_FILE, dict)
    
    if data:
        try:
            # Trích xuất secrets trước khi xác thực Pydantic
            for key in _SECRET_FIELDS:
                val = data.pop(key, None)
                if val and isinstance(val, str):
                    json_secrets[key] = val
            s = Settings(**data)
        except Exception as e:
            _logger.warning(f"Failed to parse settings values: {e}")

    # Khôi phục secrets từ secure storage (tier 1 → tier 2 → tier 3 JSON)
    for attr, key_suffix in _SECRET_FIELDS.items():
        val = _read_secret(key_suffix)
        if val:
            setattr(s, attr, val)

    # Tier 3 fallback: restore secrets từ JSON
    if not _has_any_secure_backend():
        for attr, val in json_secrets.items():
            if not getattr(s, attr, ''):
                setattr(s, attr, val)

    # Một lần migration
    if _get_secure_storage() is not None and json_secrets:
        migrated = False
        for attr, key_suffix in _SECRET_FIELDS.items():
            if attr in json_secrets:
                _write_secret(key_suffix, json_secrets[attr])
                migrated = True
        if migrated:
            _logger.info("Migrated secrets from JSON → SecureStorage")

    # Legacy migration
    if _HAS_KEYRING and s.UTH_USERNAME and not s.UTH_PASSWORD:
        try:
            kp = keyring.get_password(KEYRING_SERVICE_NAME, s.UTH_USERNAME)
            if kp:
                s.UTH_PASSWORD = kp
                _write_secret('password', kp)
        except Exception:
            pass

    return s

settings = load_settings()

def save_settings():
    """Tiện tay lưu luôn đống setting hiện tại xuống ổ cứng an toàn."""
    _logger = logging.getLogger(__name__)
    has_secure = _has_any_secure_backend()

    # --- Step 1: Save non-secret settings to JSON ---
    json_ok = False
    try:
        def get_data_to_write():
            data = settings.model_dump()
            if not has_secure:
                for attr in _SECRET_FIELDS:
                    val = getattr(settings, attr, '')
                    if val:
                        data[attr] = val
            return data
            
        from core.safe_file_io import SafeFileIO
        json_ok = SafeFileIO.write_json_atomic(CONFIG_FILE, get_data_to_write())
    except Exception as e:
        _logger.error(f"Failed to save settings: {e}")

    # --- Step 2: Save all secrets to secure storage ---
    if has_secure:
        for attr, key_suffix in _SECRET_FIELDS.items():
            try:
                val = getattr(settings, attr, '')
                _write_secret(key_suffix, val)
            except Exception as e:
                _logger.warning(f"Failed to write {attr} to secure storage: {e}")
    else:
        _logger.warning(
            "⚠️ CẢNH BÁO BẢO MẬT: Không tìm thấy secure storage."
        )

    # --- Step 3: Cleanup legacy secrets from JSON file ---
    if json_ok and has_secure:
        try:
            from core.safe_file_io import SafeFileIO
            data = SafeFileIO.read_json_safe(CONFIG_FILE, dict)
            stripped = {k: v for k, v in data.items() if k not in _SECRET_FIELDS}
            if len(stripped) < len(data):
                SafeFileIO.write_json_atomic(CONFIG_FILE, stripped)
                _logger.info("Cleaned legacy secrets from settings JSON file")
        except Exception as e:
            _logger.warning(f"Failed to clean legacy secrets from JSON: {e}")


