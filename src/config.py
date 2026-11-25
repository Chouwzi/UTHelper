from pydantic import BaseModel, Field
from collections.abc import Callable, Iterable, Mapping
from typing import Literal, Protocol
import os
from pathlib import Path
import sys
import logging
import platform
import threading
# Secure storage import chain
# Tier 1: keyring (Windows Credential Manager)
# Tier 2: plaintext JSON (last resort, not recommended)
try:
    import keyring
    _HAS_KEYRING = True
except ImportError:
    _HAS_KEYRING = False

# Mò đường dẫn thư mục gốc, chạy script lẻ hay đóng gói Flet đều dùng được
_flet_assets = os.environ.get("FLET_ASSETS_DIR")
if _flet_assets:
    # Nếu được đóng gói bởi Flet, FLET_ASSETS_DIR sẽ trỏ thẳng vào thư mục assets
    BASE_DIR = Path(_flet_assets).parent
elif getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # PyInstaller legacy fallback
    BASE_DIR = Path(sys._MEIPASS)
else:
    # Nếu đang chạy mã nguồn Python thông thường
    BASE_DIR = Path(__file__).resolve().parent.parent

# Fix specifically for Flet build missing standard directories sometimes when using --add-data "assets;assets"


# Platform-aware data directory
def is_android():
    return (
        hasattr(sys, "_ANDROID_")
        or hasattr(sys, "getandroidapilevel")
        or "android" in sys.platform.lower()
        or hasattr(sys, "_IOS_")
        or sys.platform == "ios"
    )


def _is_real_android() -> bool:
    if bool(getattr(sys, "_IOS_", False)) or sys.platform == "ios":
        return False
    return (
        bool(getattr(sys, "_ANDROID_", False))
        or hasattr(sys, "getandroidapilevel")
        or "android" in sys.platform.lower()
    )


def _is_real_ios() -> bool:
    return bool(getattr(sys, "_IOS_", False)) or sys.platform == "ios"


def _is_real_mobile() -> bool:
    return _is_real_android() or _is_real_ios()

def _requires_native_secret_backend() -> bool:
    return _is_real_android() or _is_real_ios()


def is_windows():
    return sys.platform == 'win32'

def _get_windows_data_dir() -> Path:
    return Path(os.getenv('APPDATA', BASE_DIR)) / "UTHelper"

def _get_android_data_dir() -> Path:
    for env_var in ("FLET_APP_STORAGE_DATA", "FLET_APP_DATA"):
        flet_data = os.getenv(env_var, "").strip()
        if flet_data:
            return Path(flet_data) / "UTHelper"
    return Path.home() / ".uthelper"

def _get_linux_data_dir() -> Path:
    xdg_data = os.getenv("XDG_DATA_HOME", "").strip()
    if xdg_data:
        candidate = Path(xdg_data)
        if candidate.is_absolute():
            return candidate / "UTHelper"
    return Path.home() / ".local" / "share" / "UTHelper"

def _get_macos_data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "UTHelper"

def get_data_dir() -> Path:
    """Trả về thư mục lưu trữ dữ liệu tùy theo OS."""
    if is_android():
        return _get_android_data_dir()
    elif is_windows():
        return _get_windows_data_dir()
    elif platform.system() == "Darwin":
        return _get_macos_data_dir()
    return _get_linux_data_dir()


def _ensure_private_data_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


_USER_DATA_DIR = get_data_dir()
_ensure_private_data_dir(_USER_DATA_DIR)
CONFIG_FILE = _USER_DATA_DIR / "settings.json"

KEYRING_SERVICE_NAME = "UTHelper"

# Secret field mapping (attr_name → storage_key)
_SECRET_FIELDS = {
    'UTH_PASSWORD': 'password',
    'MOODLE_SESSION': 'moodle_session',
    'MOODLE_WS_TOKEN': 'ws_token',
    'MOODLE_WS_TOKEN_ORIGIN': 'ws_token_origin',
    'GMAIL_APP_PASSWORD': 'gmail_app_password',
    'DISCORD_WEBHOOK_URL': 'discord_webhook',
    'TELEGRAM_BOT_TOKEN': 'telegram_bot_token',
}



class SecretStoreBackend(Protocol):
    def read_secret(self, key: str) -> str: ...
    def write_secret(self, key: str, value: str) -> bool: ...
    def snapshot_secrets(self, keys: Iterable[str]) -> dict[str, str] | None: ...
    def restore_secrets(self, snapshot: Mapping[str, str]) -> bool: ...
    def write_all(self, values: Mapping[str, str]) -> bool: ...


_secret_backend: SecretStoreBackend | None = None
_secret_backend_factory: Callable[[], SecretStoreBackend | None] | None = None
_pending_plaintext_migration: dict[str, str] = {}


def register_secret_backend(backend: SecretStoreBackend | None) -> None:
    """Register or clear a runtime native secret storage backend (e.g. AndroidKeyStore/iOS Keychain)."""
    global _secret_backend, _secret_backend_factory
    with _settings_save_lock:
        _secret_backend = backend
        if backend is None:
            _secret_backend_factory = None


def register_secret_backend_factory(
    factory: Callable[[], SecretStoreBackend | None] | None,
) -> None:
    """Register a lazy factory to recover a native secret backend after transient startup failures."""
    global _secret_backend_factory
    with _settings_save_lock:
        _secret_backend_factory = factory


def _resolve_secret_backend() -> SecretStoreBackend | None:
    """Return active backend, invoking lazy recovery factory outside the in-memory state lock."""
    global _secret_backend
    with _settings_save_lock:
        if _secret_backend is not None:
            return _secret_backend
        factory = _secret_backend_factory
    if factory is None:
        return None
    try:
        candidate = factory()
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Lazy native secret backend initialization failed: %s",
            type(exc).__name__,
        )
        return None
    if candidate is None:
        return None
    with _settings_save_lock:
        if _secret_backend is None:
            _secret_backend = candidate
        return _secret_backend


def hydrate_secrets(
    values: Mapping[str, str],
    *,
    overwrite_non_empty: bool = True,
) -> None:
    """Populate in-memory settings secret attributes from a secure-store mapping."""
    with _settings_save_lock:
        for attr, key_suffix in _SECRET_FIELDS.items():
            if key_suffix in values:
                raw = values[key_suffix]
            elif attr in values:
                raw = values[attr]
            else:
                continue
            new_val = str(raw) if raw else ""
            if not overwrite_non_empty and (getattr(settings, attr, "") or not new_val):
                continue
            setattr(settings, attr, new_val)


def pop_pending_plaintext_migration() -> dict[str, str]:
    """Return and clear legacy JSON secrets queued for native mobile migration."""
    with _settings_save_lock:
        if not _pending_plaintext_migration:
            return {}
        pending = dict(_pending_plaintext_migration)
        _pending_plaintext_migration.clear()
        return pending


def _read_secret(key: str) -> str:
    """Đọc secret từ secure storage (native backend hoặc keyring)."""
    if _secret_backend is not None:
        try:
            val = _secret_backend.read_secret(key)
            return str(val) if val else ""
        except Exception:
            logging.getLogger(__name__).debug("Secret backend read failed", exc_info=True)
            return ""
    if _HAS_KEYRING and not _is_real_mobile():
        try:
            val = keyring.get_password(KEYRING_SERVICE_NAME, key)
            if val:
                return val
        except Exception:
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return ""

def _write_secret(key: str, value: str) -> bool:
    """Persist one secret, deleting its keyring entry when it is cleared.

    Every caller represents an absent secret with the empty string.  Keeping
    the delete at this boundary prevents a later ``load_settings()`` from
    resurrecting credentials that the in-memory settings intentionally
    invalidated.
    """
    _logger = logging.getLogger(__name__)
    if _secret_backend is not None:
        try:
            return bool(_secret_backend.write_secret(key, value))
        except Exception as exc:
            _logger.warning("Native secret backend write failed: %s", type(exc).__name__)
            return False
    if _HAS_KEYRING and not _is_real_mobile():
        try:
            if value:
                keyring.set_password(KEYRING_SERVICE_NAME, key, value)
            else:
                try:
                    keyring.delete_password(KEYRING_SERVICE_NAME, key)
                except keyring.errors.PasswordDeleteError:
                    # Deleting an already-absent secret is the desired state.
                    pass
            return True
        except Exception as e:
            _logger.warning(f"Keyring write failed for {key}: {e}")
            return False
    return False

def _has_any_secure_backend() -> bool:
    """Kiểm tra có backend nào an toàn hay không."""
    return _secret_backend is not None or (_HAS_KEYRING and not _is_real_mobile())


_settings_save_lock = threading.RLock()
_settings_io_lock = threading.RLock()
_settings_subscriber_lock = threading.RLock()
_settings_saved_subscribers: dict[object, Callable[[], None]] = {}


def subscribe_settings_saved(listener: Callable[[], None]) -> Callable[[], None]:
    """Subscribe to successful durable saves and return an idempotent unsubscribe."""
    if not callable(listener):
        raise TypeError("settings listener must be callable")
    token = object()
    with _settings_subscriber_lock:
        _settings_saved_subscribers[token] = listener

    def unsubscribe() -> None:
        with _settings_subscriber_lock:
            _settings_saved_subscribers.pop(token, None)

    return unsubscribe


def _notify_settings_saved() -> None:
    with _settings_subscriber_lock:
        subscribers = tuple(_settings_saved_subscribers.values())
    for listener in subscribers:
        try:
            listener()
        except Exception:
            logging.getLogger(__name__).warning(
                "A settings-save subscriber failed"
            )


def _snapshot_secure_secrets() -> dict[str, str] | None:
    """Capture all secure-store values before starting a settings transaction."""
    if _secret_backend is not None:
        try:
            snap = _secret_backend.snapshot_secrets(_SECRET_FIELDS.values())
            if snap is None:
                return None
            return {key: str(snap.get(key, "") or "") for key in _SECRET_FIELDS.values()}
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Cannot snapshot native secure settings before save: %s",
                type(exc).__name__,
            )
            return None
    if not _HAS_KEYRING or _is_real_mobile():
        return None
    snapshot: dict[str, str] = {}
    try:
        for key_suffix in _SECRET_FIELDS.values():
            snapshot[key_suffix] = (
                keyring.get_password(KEYRING_SERVICE_NAME, key_suffix) or ""
            )
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Cannot snapshot secure settings before save: %s", exc
        )
        return None
    return snapshot


def _restore_secure_secrets(snapshot: dict[str, str]) -> bool:
    """Compensate secure-store mutations after a failed settings transaction."""
    if _secret_backend is not None:
        try:
            restored = bool(_secret_backend.restore_secrets(snapshot))
        except Exception:
            restored = False
        if not restored:
            logging.getLogger(__name__).critical(
                "Secure settings rollback was incomplete; JSON was not committed"
            )
        return restored
    restored = True
    for key_suffix, value in snapshot.items():
        if not _write_secret(key_suffix, value):
            restored = False
    if not restored:
        logging.getLogger(__name__).critical(
            "Secure settings rollback was incomplete; JSON was not committed"
        )
    return restored

class Settings(BaseModel):
    """
    Nơi chứa toàn bộ cấu hình của app. 
    Dùng Pydantic để quản lý cho nó chuyên nghiệp.
    """

    # Thông tin đăng nhập UTH
    UTH_USERNAME: str = Field(default="", description="Mã số sinh viên (MSSV)")
    UTH_PASSWORD: str = Field(default="", description="Mật khẩu đăng nhập", exclude=True)
    UTH_CREDENTIALS_ORIGIN: str = Field(default="", description="Trusted Moodle origin verified for stored UTH credentials")
    MOODLE_SESSION: str = Field(default="", description="Session cookie dể giữ đăng nhập", exclude=True)
    MOODLE_WS_TOKEN: str = Field(default="", description="Web Services API token (stateless, valid ~30 ngày)", exclude=True)
    MOODLE_WS_TOKEN_ORIGIN: str = Field(default="", description="Trusted Moodle origin that issued the Web Services token", exclude=True)

    # Địa chỉ mấy trang web của trường mình
    MOODLE_BASE_URL: str = "https://courses.ut.edu.vn"
    MOODLE_LOGIN_URL: str = "https://courses.ut.edu.vn/login/index.php"
    PORTAL_API_BASE: str = "https://portal-publicapp.ut.edu.vn/api/v1"
    PORTAL_WEB_API_BASE: str = "https://portal.ut.edu.vn/api/v1"
    
    # Cài đặt chung của ứng dụng
    THEME: str = Field(
        default="steel_light",
        description="Theme preset, including Material Graphite and light variants",
    )
    SETTINGS_SCHEMA_VERSION: int = Field(default=5, description="Phiên bản schema cài đặt")
    CHECK_INTERVAL_MINUTES: int = Field(default=60, description="Tần suất đồng bộ hoạt động (phút)")
    COURSES_SYNC_INTERVAL_HOURS: int = Field(default=6, description="Chu kỳ làm tươi nền danh mục môn học (giờ)")
    # Deprecated compatibility fields. Runtime scheduling uses only
    # CHECK_INTERVAL_MINUTES; keep these for one migration window so an older
    # settings file can still be read without silently losing its values.
    POLL_INTERVAL_MINUTES: int = Field(default=15, description="[Deprecated] Tần suất poll cũ (phút)")
    SMART_POLL_ENABLED: bool = Field(default=True, description="Bật chế độ poll thông minh (chỉ fetch khi có thay đổi)")
    FETCH_MONTHS: int = Field(default=1, description="Số tháng lấy sự kiện từ lịch (1-3 tháng)")

    # Bộ lọc hiển thị cho danh sách bài tập
    INCLUDE_SUBMITTED: bool = Field(default=True, description="Hiển thị cả các bài đã nộp")
    INCLUDE_GRADED: bool = Field(default=True, description="Hiển thị cả các bài đã chấm điểm")
    INCLUDE_PAST_DUE: bool = Field(default=False, description="Hiển thị cả các bài đã quá hạn")

    # Chuyện khởi động và hệ thống
    START_WITH_WINDOWS: bool = Field(default=True, description="Tự động chạy khi mở máy")
    START_MINIMIZED: bool = Field(default=False, description="Chạy ngầm khi khởi động")
    AUTOSTART_DEFAULT_APPLIED: bool = Field(
        default=False,
        description="Đã áp dụng mặc định autostart cho cài đặt mới",
    )
    MINIMIZE_TO_TRAY: bool = Field(default=True, description="Thu nhỏ xuống khay hệ thống (System Tray)")

    # Android background notifications (AlarmManager)
    BACKGROUND_CHECK_ANDROID: bool = Field(default=True, description="Kiểm tra deadline nền trên Android (AlarmManager)")
    BACKGROUND_CHECK_INTERVAL: int = Field(default=30, description="[Deprecated] Tần suất Android cũ (phút)")
    AUTO_UPDATE_ENABLED: bool = Field(
        default=True,
        description="Tự động kiểm tra cập nhật",
    )
    CRASH_REPORTING_CONSENT: Literal["not_asked", "enabled", "disabled"] = Field(
        default="enabled",
        description="Gửi chẩn đoán sự cố ẩn danh; người dùng có thể tắt",
    )

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
    NOTIFY_MINUTES_BEFORE: int  = Field(default=30, description="[Deprecated] Mốc phút cuối cũ")

    # Nhóm cài đặt cho tính năng UTHelper
    NOTIFICATION_PROFILE: str = Field(default="balanced", description="Chế độ thông báo: quiet, balanced, exam_week")
    NOTIFY_MILESTONES: list = Field(default_factory=lambda: [72, 24, 3], description="[Deprecated] Các mốc giờ cũ")
    NOTIFY_MILESTONES_MINUTES: list[int] = Field(
        default_factory=lambda: [4320, 1440, 180, 60, 30, 5],
        description="Các mốc nhắc trước deadline, tính bằng phút",
    )
    NOTIFY_MUTED_COURSES: list = Field(default_factory=list, description="Danh sách các môn bị tắt thông báo")
    NOTIFY_TYPES: list = Field(default_factory=lambda: ["quiz", "assignment", "attendance"], description="Các loại bài tập sẽ gửi cảnh báo")
    NOTIFY_DND_ENABLE: bool = Field(default=False, description="Bật chế độ không làm phiền")
    NOTIFY_DND_START: int = Field(default=22, description="Giờ bắt đầu im lặng (0-23)")
    NOTIFY_DND_END: int = Field(default=7, description="Giờ kết thúc im lặng (0-23)")
    NOTIFY_IGNORE_SUBMITTED: bool = Field(default=True, description="Im lặng với các bài đã nộp/có điểm")
    NOTIFY_CLASS_SCHEDULE_ENABLED: bool = Field(
        default=False,
        description="Bật thông báo nhắc lịch học từ UTH Portal",
    )
    NOTIFY_CLASS_SCHEDULE_LEAD_HOURS: list[int] = Field(
        # Issue #86: one reminder, one hour before class. Users may opt into
        # the 2/3-hour marks from Settings, but never get them by default.
        default_factory=lambda: [1],
        description="Các mốc nhắc trước giờ học (giờ): 1, 2, 3 (mặc định chỉ 1)",
    )

    # Cài đặt hiệu năng
    PREFETCH_WORKERS: int = Field(default=4, description="Số luồng đồng thời khi prefetch chi tiết (1-10)")
    DETAIL_CACHE_TTL_SECONDS: int = Field(default=1800, description="Thời gian giữ cache chi tiết hoạt động (giây)")
    DETAIL_CACHE_MAX_ENTRIES: int = Field(default=100, description="Số hoạt động chi tiết tối đa giữ trong RAM")

def migrate_settings_data(raw: dict) -> dict:
    """Migrate legacy refresh settings to one canonical interval.

    Explicit ``CHECK_INTERVAL_MINUTES`` always wins. Older installations that
    predate it fall back to the poll interval and finally the Android interval.
    Legacy keys are intentionally preserved during this compatibility window.
    """
    source = dict(raw or {})
    data = dict(source)
    # Existing installations already contain an explicit preference. Mark the
    # one-time default as handled so an upgrade never re-enables autostart after
    # a user deliberately disabled it.
    data.setdefault(
        "AUTOSTART_DEFAULT_APPLIED",
        "START_WITH_WINDOWS" in source,
    )
    if data.get("CRASH_REPORTING_CONSENT") in {None, "not_asked"}:
        data["CRASH_REPORTING_CONSENT"] = "enabled"
    data.setdefault("AUTO_UPDATE_ENABLED", True)
    if "CHECK_INTERVAL_MINUTES" not in data:
        legacy_value = data.get("POLL_INTERVAL_MINUTES")
        if legacy_value is None:
            legacy_value = data.get("BACKGROUND_CHECK_INTERVAL", 60)
        try:
            data["CHECK_INTERVAL_MINUTES"] = max(0, int(legacy_value))
        except (TypeError, ValueError):
            data["CHECK_INTERVAL_MINUTES"] = 60
    if "NOTIFY_MILESTONES_MINUTES" not in data:
        legacy_hours = data.get("NOTIFY_MILESTONES")
        legacy_minute = data.get("NOTIFY_MINUTES_BEFORE")
        if legacy_hours is not None or legacy_minute is not None:
            converted: set[int] = set()
            for value in legacy_hours or []:
                try:
                    minutes = int(value) * 60
                    if minutes > 0:
                        converted.add(minutes)
                except (TypeError, ValueError):
                    continue
            try:
                minute = int(legacy_minute or 0)
                if minute > 0:
                    converted.add(minute)
            except (TypeError, ValueError):
                pass
            data["NOTIFY_MILESTONES_MINUTES"] = sorted(converted, reverse=True)
    data.setdefault("NOTIFY_CLASS_SCHEDULE_ENABLED", False)
    data.setdefault("NOTIFY_CLASS_SCHEDULE_LEAD_HOURS", [1])
    data["SETTINGS_SCHEMA_VERSION"] = 5
    return data


def get_sync_interval_minutes(value=None) -> int:
    """Return the single effective interval used by every Python scheduler."""
    candidate = settings.CHECK_INTERVAL_MINUTES if value is None else value
    try:
        return max(0, int(candidate))
    except (TypeError, ValueError):
        return 60


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
            s = Settings(**migrate_settings_data(data))
        except Exception as e:
            _logger.warning(f"Failed to parse settings values: {e}")

    # Khôi phục secrets từ secure storage (keyring → tier 2 JSON fallback)
    for attr, key_suffix in _SECRET_FIELDS.items():
        val = _read_secret(key_suffix)
        if val:
            setattr(s, attr, val)

    # Tier 2 fallback: restore secrets từ JSON (trên Android/iOS chỉ giữ trong RAM để migrate)
    if not _has_any_secure_backend():
        for attr, val in json_secrets.items():
            if not getattr(s, attr, ''):
                setattr(s, attr, val)
        if _requires_native_secret_backend():
            with _settings_save_lock:
                _pending_plaintext_migration.clear()
                for attr, val in json_secrets.items():
                    key_suffix = _SECRET_FIELDS.get(attr)
                    if key_suffix and val:
                        _pending_plaintext_migration[key_suffix] = val

    # Legacy migration
    if _HAS_KEYRING and not _is_real_mobile() and s.UTH_USERNAME and not s.UTH_PASSWORD:
        try:
            kp = keyring.get_password(KEYRING_SERVICE_NAME, s.UTH_USERNAME)
            if kp:
                s.UTH_PASSWORD = kp
                _write_secret('password', kp)
        except Exception:
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

    return s

settings = load_settings()

def save_settings() -> bool:
    """Persist settings transactionally across secure storage and JSON storage.

    Fast in-memory state reads hold ``_settings_save_lock`` briefly, while all
    blocking secure-store and filesystem I/O runs under ``_settings_io_lock``
    only — preventing AB-BA lock inversion with event-loop callbacks such as
    ``hydrate_secrets()`` or ``register_secret_backend()``.
    """
    _logger = logging.getLogger(__name__)
    with _settings_io_lock:
        backend = _resolve_secret_backend()
        with _settings_save_lock:
            has_secure = _has_any_secure_backend()
            if backend is None:
                backend = _secret_backend
            data = settings.model_dump()
            desired_secrets = {
                key_suffix: getattr(settings, attr, "")
                for attr, key_suffix in _SECRET_FIELDS.items()
            }
            raw_secret_attrs = {
                attr: getattr(settings, attr, "") for attr in _SECRET_FIELDS
            }

        if not has_secure:
            if _requires_native_secret_backend():
                has_non_empty_secret = any(bool(v) for v in raw_secret_attrs.values())
                if has_non_empty_secret:
                    _logger.warning(
                        "Refusing to persist plaintext secrets on mobile without a secure backend"
                    )
                    return False
            else:
                for attr, value in raw_secret_attrs.items():
                    if value:
                        data[attr] = value
                _logger.warning(
                    "⚠️ CẢNH BÁO BẢO MẬT: Không tìm thấy secure storage."
                )

        previous_secrets: dict[str, str] | None = None
        if has_secure:
            previous_secrets = _snapshot_secure_secrets()
            if previous_secrets is None:
                return False
            if backend is not None:
                if desired_secrets != previous_secrets:
                    try:
                        wrote = bool(backend.write_all(desired_secrets))
                    except Exception as exc:
                        _logger.warning(
                            "Native secret backend batch write failed: %s",
                            type(exc).__name__,
                        )
                        wrote = False
                    if not wrote:
                        if not getattr(backend, "atomic_batch_write", False):
                            _restore_secure_secrets(previous_secrets)
                        return False
            else:
                for key_suffix, value in desired_secrets.items():
                    if previous_secrets.get(key_suffix, "") == value:
                        continue
                    if not _write_secret(key_suffix, value):
                        _restore_secure_secrets(previous_secrets)
                        return False

        try:
            from core.safe_file_io import SafeFileIO

            _ensure_private_data_dir(CONFIG_FILE.parent)
            json_ok = bool(SafeFileIO.write_json_atomic(CONFIG_FILE, data))
        except Exception as exc:
            json_ok = False
            _logger.error("Failed to save settings: %s", exc)

        if not json_ok and previous_secrets is not None:
            _restore_secure_secrets(previous_secrets)
    if json_ok:
        _notify_settings_saved()
    return json_ok


