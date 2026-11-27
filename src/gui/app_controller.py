import os
import sys

# Patch path for direct execution / Flet preview compatibility
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import flet as ft
from concurrent.futures import Future
from datetime import datetime
from core.time_utils import parse_datetime
import asyncio
import inspect
import logging
import math
import platform_utils
from platform_utils import detect_platform
import threading
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit, urlunsplit


from core.class_schedule_notifications import REMINDER_LOOKAHEAD_DAYS
from core.data_orchestrator import DataOrchestrator
from core.demo_sandbox import is_demo_account, normalize_demo_username
from core.distribution import DistributionProfile, resolve_distribution_channel
from core.moodle_service import MoodleService
from core.moodle_sites import moodle_site_from_origin
from core.portal_client import PortalClient
from core.today_schedule import (
    ScheduleLoadStatus,
    TodayScheduleCache,
    UpcomingScheduleCache,
    WeekScheduleCache,
    TodayScheduleCoordinator,
    TodayScheduleViewState,
)
from core.use_cases.submission_workflow import SubmissionWorkflow
from core.activity_time_policy import ActivityTimePolicy
from core.activity_status import normalize_submission_status
from core.sync_coordinator import ActivitySyncCoordinator, FetchOutcome, parse_timestamp
from core.update_coordinator import UpdateCoordinator, UpdateEvent, UpdateEventKind
from core.update_models import LaunchResult, ReleasePackage
from core.url_validation import validate_https_url

try:
    from core.webview_auth import (
        UthWebViewAuthService as _CoreUthWebViewAuthService,
        classify_uth_webview_url as _core_classify_uth_webview_url,
        normalize_uth_webview_target_url as _core_normalize_uth_webview_target_url,
    )
except ImportError:
    _CoreUthWebViewAuthService = None
    _core_classify_uth_webview_url = None
    _core_normalize_uth_webview_target_url = None
from config import (
    get_sync_interval_minutes,
    hydrate_secrets,
    pop_pending_plaintext_migration,
    register_secret_backend,
    save_settings,
    settings,
)
from platform_utils.background_sync import AndroidSecretBackend
from platform_utils.update_packages import detect_runtime_target

from gui.core.theme import C, refresh_semantic_control_colors, semantic_palette
from gui.core.utils import DEMO_LMS_WEBVIEW_GUARD_MESSAGE, is_uth_lms_url
from core.filter_service import FilterService
from gui.components.activity_card import ActivityCard
from gui.components.detail_view import DetailView
from gui.components.quiz_player_view import QuizPlayerView
from gui.components.settings_view import SettingsView
from gui.components.calendar_view import CalendarView
from gui.components.grade_overview_view import GradeOverviewView
from gui.components.courses_view import CoursesView
from gui.components.today_schedule_panel import TodaySchedulePanel
from gui.components.sync_status_indicator import SyncStatusIndicator
from gui.components.desktop_window_header import build_desktop_window_header
from gui.components.courses.adaptive_nav import (
    AdaptiveNavShell,
    RAIL_BREAKPOINT,
    RAIL_WIDTH,
)
from gui.components.adaptive_filter_menu import (
    AdaptiveFilterMenu,
    FilterMenuOption,
)
from gui.view_manager import ViewManager
from gui.view_models.courses_state import safe_http_url
from gui.controllers.startup_visibility import (
    is_autostart_launch,
    should_hide_startup_window,
)
from gui.controllers.autostart_settings import apply_initial_autostart_default
from gui.controllers.refresh_coordinator import (
    build_prefetched_dataset,
    build_refresh_dataset,
    card_detail_prefetch_candidates,
    status_hydration_candidates,
)
from gui.controllers.window_activator import WindowActivator

if TYPE_CHECKING:
    from core.courses.models import CourseModuleItem, CourseSummary
    from gui.components.academic_radar.course_components_dialog import (
        MoodleCourseGradesResult,
    )

logger = logging.getLogger(__name__)

_LAZY_MODULE_ATTRS = {
    "NotificationManager": ("notifiers.manager", "NotificationManager"),
    "GitHubReleaseClient": ("core.update_checker", "GitHubReleaseClient"),
}


def __getattr__(name: str):
    target = _LAZY_MODULE_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr_name = target
    import importlib

    value = getattr(importlib.import_module(mod_name), attr_name)
    globals()[name] = value
    return value


def _resolve_lazy_attr(name: str):
    existing = globals().get(name)
    if existing is not None:
        return existing
    return __getattr__(name)


def _new_modal_occlusion_token() -> str:
    import uuid

    return f"dlg:{uuid.uuid4().hex}"


def _call_modal_occlusion(
    reason: str, occluded: bool, *, token: str | None = None
) -> None:
    """Invoke `_set_webview_occlusion` with token support and 2-arg test-monkeypatch fallback."""
    if token is not None:
        try:
            _set_webview_occlusion(reason, occluded, token=token)
            return
        except TypeError:
            pass
    _set_webview_occlusion(reason, occluded)


def _set_webview_occlusion(
    reason: str, occluded: bool, *, token: str | None = None
) -> None:
    """Best-effort bridge to global WebView2 occlusion coordinator."""
    try:
        from gui.components.courses.embedded_webview_host import (
            set_global_webview_occlusion,
        )
    except ImportError:
        return
    try:
        if token is not None:
            try:
                set_global_webview_occlusion(reason, bool(occluded), token=token)
                return
            except TypeError:
                pass
        set_global_webview_occlusion(reason, bool(occluded))
    except Exception:
        logger.debug("WebView occlusion update failed", exc_info=True)


def _build_account_data_cache(username: str | None):
    """Build an isolated DataCache; logged-out and demo states never migrate legacy cache."""
    from core.data_cache import DataCache

    clean_user = str(username or "").strip()
    if clean_user:
        demo_user = is_demo_account(clean_user)
        if demo_user:
            clean_user = normalize_demo_username(clean_user)
        try:
            return DataCache(
                namespace=f"{settings.MOODLE_BASE_URL}|{clean_user}",
                allow_legacy_migration=not demo_user,
            )
        except TypeError:
            return DataCache(namespace=f"{settings.MOODLE_BASE_URL}|{clean_user}")
    try:
        return DataCache(
            namespace="__logged_out__",
            allow_legacy_migration=False,
        )
    except TypeError:
        return DataCache(namespace="__logged_out__")

_SCROLL_TO_TOP_SHOW_OFFSET = 240.0
_SCROLL_TO_TOP_HIDE_OFFSET = 96.0
_SCROLL_TO_TOP_DURATION_MS = 300
_DASHBOARD_SCROLL_EVENT_INTERVAL_MS = 32
# Bố cục PC: từ độ rộng này trở lên chia 2 cột (danh sách | lịch hôm nay / chi tiết).
_WIDE_BREAKPOINT = 980
_SIDE_PANE_WIDTH = 440
_RAIL_WIDTH = 88
_LIST_MAX_WIDTH = 820
_SINGLE_VIEW_MAX_WIDTH = 860

try:
    from core.version import APP_VERSION as _APP_VERSION
    APP_VERSION = f"v{_APP_VERSION}"
except ImportError:
    APP_VERSION = "v0.0.0"

def _save_setting(key: str, value):
    try:
        from config import settings, save_settings
        setattr(settings, key, value)
        save_settings()
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to save setting {key}: {e}")


class _UnavailablePackageLauncher:
    def launch(self, _path: Path, _package: ReleasePackage) -> LaunchResult:
        return LaunchResult(False, "installer is unavailable on this platform")

    def cancel(self) -> None:
        return None


def _update_install_error_message(status: str) -> str:
    normalized = str(status or "").strip()
    messages = {
        "invalid_apk": "Không đọc được gói cập nhật. Hãy tải lại.",
        "package_mismatch": "Gói cập nhật không dành cho ứng dụng này.",
        "release_version_mismatch": "Phiên bản gói cập nhật không hợp lệ.",
        "installed_signer_mismatch": "Chữ ký bản cập nhật không khớp ứng dụng đang cài.",
        "manifest_signer_mismatch": "Chữ ký gói cập nhật không hợp lệ.",
        "apk_signature_invalid": "Không xác minh được chữ ký gói cập nhật.",
        "release_metadata_invalid": "Thông tin bản cập nhật không hợp lệ.",
        "archive_identity_unavailable": "Không đọc được định danh gói cập nhật. Hãy thử lại.",
        "source_unavailable": "Không tìm thấy gói đã tải. Hãy tải lại.",
        "verification_timeout": "Xác minh bản cập nhật quá lâu. Hãy thử lại.",
        "package_staging_failed": "Không chuẩn bị được gói cài đặt. Hãy kiểm tra bộ nhớ rồi thử lại.",
        "update_verification_failed": "Không xác minh được gói cập nhật. Hãy thử lại.",
        "sha256_mismatch": "Gói cập nhật bị hỏng. Hãy tải lại.",
        "size_mismatch": "Gói cập nhật chưa đầy đủ. Hãy tải lại.",
        "already_installed": "Bản cập nhật này đã được cài đặt.",
        "package_installer_unavailable": "Không tìm thấy trình cài đặt trên thiết bị.",
        "file_provider_unavailable": "Không chuyển được gói tới trình cài đặt. Hãy thử lại.",
        "permission_settings_unavailable": "Không mở được quyền cài ứng dụng. Hãy kiểm tra Cài đặt Android.",
        # Windows / cross-platform launcher tokens and reasons
        "installer_unavailable": "Không tìm thấy tệp cài đặt. Hãy tải lại.",
        "Windows installer is unavailable": "Không tìm thấy tệp cài đặt. Hãy tải lại.",
        "platform_unavailable": "Nền tảng hiện tại chưa hỗ trợ cài đặt tự động.",
        "installer is unavailable on this platform": "Nền tảng hiện tại chưa hỗ trợ cài đặt tự động.",
        "strategy_not_allowed": "Phương thức cài đặt không hợp lệ. Hãy tải từ trang phát hành.",
        "installer_strategy_not_allowed": "Phương thức cài đặt không hợp lệ. Hãy tải từ trang phát hành.",
        "installer strategy is not allowed": "Phương thức cài đặt không hợp lệ. Hãy tải từ trang phát hành.",
        "uac_cancelled": "Bạn đã hủy cấp quyền cài đặt cập nhật. Nhấn Thử lại khi sẵn sàng.",
        "uac_denied": "Bạn đã hủy cấp quyền cài đặt cập nhật. Nhấn Thử lại khi sẵn sàng.",
        "installer_cancelled": "Bạn đã hủy cấp quyền cài đặt cập nhật. Nhấn Thử lại khi sẵn sàng.",
        "installer cancelled by user": "Bạn đã hủy cấp quyền cài đặt cập nhật. Nhấn Thử lại khi sẵn sàng.",
        "installer_exit_1223": "Bạn đã hủy cấp quyền cài đặt cập nhật. Nhấn Thử lại khi sẵn sàng.",
        "installer_exit_1602": "Bạn đã hủy cấp quyền cài đặt cập nhật. Nhấn Thử lại khi sẵn sàng.",
        "installer_busy": "Đang có một trình cài đặt Windows khác chạy. Hãy đợi hoàn tất rồi thử lại.",
        "another Windows installation is in progress": "Đang có một trình cài đặt Windows khác chạy. Hãy đợi hoàn tất rồi thử lại.",
        "installer_exit_1618": "Đang có một trình cài đặt Windows khác chạy. Hãy đợi hoàn tất rồi thử lại.",
        "installer_already_running": "Trình cài đặt đang chạy. Vui lòng hoàn tất quá trình hiện tại.",
        "an installer is already running": "Trình cài đặt đang chạy. Vui lòng hoàn tất quá trình hiện tại.",
        "file_in_use": "Tệp ứng dụng đang được sử dụng. Hãy đóng tiến trình khác rồi thử lại.",
        "installer_file_in_use": "Tệp ứng dụng đang được sử dụng. Hãy đóng tiến trình khác rồi thử lại.",
        "installer_exit_1603": "Tệp ứng dụng đang được sử dụng. Hãy đóng tiến trình khác rồi thử lại.",
        "launch_failed": "Không thể khởi chạy trình cài đặt Windows. Hãy thử lại.",
        "installer_launch_failed": "Không thể khởi chạy trình cài đặt Windows. Hãy thử lại.",
        "installer launch failed": "Không thể khởi chạy trình cài đặt Windows. Hãy thử lại.",
        "bridge_failed": "Không kết nối được dịch vụ cài đặt cập nhật. Hãy thử lại.",
        "installer_rejected": "Trình cài đặt Windows đã thoát với mã lỗi. Hãy thử lại.",
        "installer rejected": "Trình cài đặt Windows đã thoát với mã lỗi. Hãy thử lại.",
        "install_timeout": "Trình cài đặt phản hồi quá lâu. Hãy thử lại.",
        "launch_timeout": "Trình cài đặt phản hồi quá lâu. Hãy thử lại.",
        "installer_timeout": "Trình cài đặt phản hồi quá lâu. Hãy thử lại.",
        "relaunch_failed": "Cập nhật hoàn tất nhưng không tự mở lại được ứng dụng.",
    }
    if normalized in messages:
        return messages[normalized]
    lowered = normalized.lower()
    if "1223" in lowered or "1602" in lowered or "uac" in lowered or "cancel" in lowered:
        return messages["uac_cancelled"]
    if "1618" in lowered:
        return messages["installer_busy"]
    if "1603" in lowered:
        return messages["file_in_use"]
    return "Không mở được trình cài đặt. Hãy thử lại."


def _resolve_webview_auth_cls():
    if _CoreUthWebViewAuthService is not None:
        return _CoreUthWebViewAuthService
    wv_mod = sys.modules.get("core.webview_auth")
    return getattr(wv_mod, "UthWebViewAuthService", None)


def _classify_uth_url(url: str) -> str:
    fn = _core_classify_uth_webview_url
    if fn is None:
        wv_mod = sys.modules.get("core.webview_auth")
        fn = getattr(wv_mod, "classify_uth_webview_url", None)
    if callable(fn):
        return str(fn(url))
    validated = validate_https_url(url)
    if validated is None:
        return "external"
    parsed = urlsplit(validated)
    if parsed.port is not None:
        return "external"
    host = (parsed.hostname or "").casefold()
    if host in ("courses.ut.edu.vn", "elearning.ut.edu.vn"):
        return "courses"
    if host == "thnn.ut.edu.vn":
        return "thnn"
    if host == "portal.ut.edu.vn":
        return "portal"
    return "external"


def _normalize_uth_target_url(url: str, *, has_credentials: bool = True) -> str:
    fn = _core_normalize_uth_webview_target_url
    if fn is None:
        wv_mod = sys.modules.get("core.webview_auth")
        fn = getattr(wv_mod, "normalize_uth_webview_target_url", None)
    if callable(fn):
        return str(fn(url, has_credentials=has_credentials))
    if not isinstance(url, str) or not url:
        return ""
    validated = validate_https_url(url)
    if validated is None:
        return url
    parsed = urlsplit(validated)
    if parsed.port is not None:
        return url
    host = (parsed.hostname or "").casefold()
    if host == "elearning.ut.edu.vn":
        return urlunsplit(
            ("https", "courses.ut.edu.vn", parsed.path, parsed.query, parsed.fragment)
        )
    if host == "portal.ut.edu.vn" and parsed.path in ("", "/", "/login") and has_credentials:
        return "https://portal.ut.edu.vn/dashboard"
    return validated


class AppController:
    def __init__(
        self,
        page: ft.Page,
        *,
        activation_broker=None,
        force_visible: bool = False,
        error_reporter=None,
        diagnostic_recorder=None,
    ):
        self.page = page
        self.activation_broker = activation_broker
        self.force_visible = force_visible
        self._unhandled_error_reporter = error_reporter
        self._diagnostic_recorder = diagnostic_recorder
        self._cards_lock = threading.Lock()
        self._data_lock = threading.Lock()
        self._exit_cleanup_lock = threading.Lock()
        self._exit_cleanup_done = False
        self._page_alive = threading.Event()
        self._page_alive.set()
        
        self.orchestrator = DataOrchestrator()
        self._data_cache = _build_account_data_cache(settings.UTH_USERNAME)
        schedule_namespace = (
            f"{settings.PORTAL_API_BASE}|{settings.UTH_USERNAME}"
            if settings.UTH_USERNAME
            else f"{settings.PORTAL_API_BASE}|anonymous"
        )
        self._account_epoch = 0
        self._portal_client = PortalClient(settings.PORTAL_API_BASE)
        self._webview_auth_service = self._build_webview_auth_service(
            courses_client=self.orchestrator.client,
            thnn_client=self.orchestrator.thnn_client,
            portal_client=self._portal_client,
        )
        # The Portal web app (lichTuan) is served from its own host.
        self._portal_week_client = PortalClient(settings.PORTAL_WEB_API_BASE)
        self._portal_week_lock = threading.Lock()
        self._week_schedule_cache = WeekScheduleCache(namespace=schedule_namespace)
        self._today_schedule_coordinator = TodayScheduleCoordinator(
            fetch_day=self._portal_client.fetch_day,
            cache=TodayScheduleCache(namespace=schedule_namespace),
            credentials_provider=lambda: (
                settings.UTH_USERNAME,
                settings.UTH_PASSWORD,
            ),
            state_sink=self._on_today_schedule_state,
        )
        self._today_schedule_state = self._today_schedule_coordinator.cached_state()
        self._settings_identity = (settings.UTH_USERNAME, settings.UTH_PASSWORD)
        self._notification_milestones_snapshot = tuple(
            settings.NOTIFY_MILESTONES_MINUTES or []
        )
        self._notification_class_schedule_snapshot = (
            bool(settings.NOTIFY_CLASS_SCHEDULE_ENABLED),
            tuple(settings.NOTIFY_CLASS_SCHEDULE_LEAD_HOURS),
            bool(settings.NOTIFY_DND_ENABLE),
            int(settings.NOTIFY_DND_START),
            int(settings.NOTIFY_DND_END),
        )
        self._last_successful_sync_at = None
        self._time_policy = ActivityTimePolicy(
            settings.URGENCY_CRITICAL_HOURS,
            settings.URGENCY_WARNING_HOURS,
        )
        
        self.all_data = []
        self.active_cards = []
        self.active_urgency = "all"
        self.active_type = "all"
        self.active_course = "all"
        self.active_search = ""
        self._is_loading = False
        
        # Lưu vết các thay đổi thủ công từ người dùng để tránh Race Condition với prefetch ngầm
        # Cấu trúc: {url: (timestamp_mili, new_status)}
        self._pending_updates = {}
        
        self._account_epoch = 0
        self._sync_generation = 0
        self._active_sync_epoch: int | None = None
        self._active_sync_generation: int | None = None
        self._grades_request_epoch = 0
        self._course_grades_request_epoch = 0
        self._prefetch_cancel_event = threading.Event()
        self._prefetch_epoch = 0
        self._card_detail_warm_cancel_event = threading.Event()
        self._detail_request_epoch = 0
        self._android_background = None
        self._dashboard_scroll_offset = 0.0
        self._scroll_to_top_visible = False
        self._scroll_to_top_in_progress = False

        self._init_window()
        self._distribution_profile = resolve_distribution_channel()
        self._update_candidate = None
        self._update_ready = False
        self._update_open_release_page = False
        self._update_manual_download = False
        self._manual_update_check_requested = False
        self._update_coordinator = self._create_update_coordinator()
        self._init_ui()
        self._diag_screen("home")
        self._diag_breadcrumb("app_start", "succeeded", "app", screen="home")
        has_cached_snapshot = self._load_cached_snapshot()
        self._sync_coordinator = ActivitySyncCoordinator(
            self._perform_data_sync,
            interval_provider=get_sync_interval_minutes,
            last_success_provider=lambda: self._last_successful_sync_at,
            can_sync_provider=self._has_sync_credentials,
        )
        
        # Events
        self.page.on_disconnect = self._on_disconnect
        self.page.on_keyboard_event = self._on_keyboard_event
        self.page.on_resize = self._on_page_resize
        
        # Intercept the native root-view pop on Android/iOS. This app swaps
        # controls inside one root View, so on_view_pop alone cannot prevent the
        # platform from finishing the activity.
        if platform_utils.IS_MOBILE:
            self._configure_mobile_back_navigation()
        if platform_utils.IS_WINDOWS:
            self._safe_run_task(self._apply_initial_windows_autostart_default)
        needs_initial_sync = self._needs_initial_data_sync(has_cached_snapshot)
        self._safe_run_task(self._countdown_loop_async)
        self._safe_run_task(self._pulse_critical_cards_loop_async)
        self._safe_run_task(self._sync_coordinator.run)
        self._tray_balloon_shown = False  # H-01: only show once

        if self._update_coordinator is not None:
            self._update_coordinator.start()

        if (
            (platform_utils.IS_ANDROID or platform_utils.IS_IOS)
            and self._android_background
            and self._android_background.available
        ):
            self._safe_run_task(
                self._startup_android_sequence,
                has_cached_snapshot,
                needs_initial_sync,
            )
        else:
            if not needs_initial_sync:
                hydration_started = self._schedule_cached_detail_hydration(
                    has_cached_snapshot
                )
                if not hydration_started:
                    self._schedule_card_detail_warmup()
            self._safe_run_task(self._today_schedule_coordinator.run)
            if self._android_background and self._android_background.available:
                self._safe_run_task(self._initialize_android_background)
            if not settings.UTH_USERNAME or not settings.UTH_PASSWORD:
                self._safe_run_task(self._show_login_dialog)
            else:
                self._safe_run_task(self._load_data_async, "startup")

    def _diag_screen(self, screen: str) -> None:
        recorder = getattr(self, "_diagnostic_recorder", None)
        if recorder is None:
            return
        try:
            recorder.set_diagnostic_screen(screen)
        except Exception:
            logger.debug("Diagnostic state update failed", exc_info=True)

    def _diag_breadcrumb(
        self,
        action: str,
        outcome: str,
        source: str,
        *,
        screen: str | None = None,
        duration: str | None = None,
    ) -> None:
        recorder = getattr(self, "_diagnostic_recorder", None)
        if recorder is None:
            return
        try:
            recorder.record_breadcrumb(
                action,
                outcome,
                source,
                screen=screen,
                duration=duration,
            )
        except Exception:
            logger.debug("Diagnostic breadcrumb failed", exc_info=True)

    def _attach_modal_occlusion_release(
        self, *, occlusion_token: str | None = None
    ) -> bool:
        """Hook the active modal dialog's on_dismiss to release WebView occlusion."""
        dlg = None
        dialogs_ctrl = getattr(self.page, "_dialogs", None)
        controls = getattr(dialogs_ctrl, "controls", None)
        if controls:
            for candidate in reversed(controls):
                if getattr(candidate, "open", False):
                    dlg = candidate
                    break
        if dlg is None:
            overlay = getattr(self.page, "overlay", None)
            if overlay:
                for candidate in reversed(overlay):
                    if isinstance(candidate, ft.AlertDialog) and getattr(
                        candidate, "open", False
                    ):
                        dlg = candidate
                        break
                    if getattr(candidate, "open", False) and hasattr(
                        candidate, "on_dismiss"
                    ):
                        dlg = candidate
                        break
        if dlg is None:
            candidate = getattr(self.page, "dialog", None)
            if candidate is not None and getattr(candidate, "open", False):
                dlg = candidate
        if dlg is None:
            return False
        prev_dismiss = getattr(dlg, "on_dismiss", None)

        async def _on_dismiss(event=None):
            try:
                if callable(prev_dismiss):
                    res = prev_dismiss(event)
                    if inspect.isawaitable(res):
                        await res
            finally:
                _call_modal_occlusion(
                    "modal_dialog", False, token=occlusion_token
                )

        try:
            dlg.on_dismiss = _on_dismiss
            return True
        except Exception:
            return False

    async def _show_login_dialog(self):
        from gui.components.login_dialog import show_login_dialog

        self._invalidate_active_webviews_before_credential_mutation()
        self._diag_screen("login")
        self._diag_breadcrumb("login", "started", "user", screen="login")
        modal_token = _new_modal_occlusion_token()
        _call_modal_occlusion("modal_dialog", True, token=modal_token)

        async def _on_login_success():
            _call_modal_occlusion("modal_dialog", False, token=modal_token)
            self._diag_breadcrumb("login", "succeeded", "moodle", screen="home")
            self._diag_screen("home")
            # UX-5: Show success snackbar after dialog closes
            self._show_snackbar("Đăng nhập thành công! Đang tải dữ liệu...", ft.Icons.CHECK_CIRCLE_ROUNDED, C.SAFE)
            await self._complete_login_success()

        try:
            await show_login_dialog(self.page, self.orchestrator, _on_login_success)
        except Exception:
            _call_modal_occlusion("modal_dialog", False, token=modal_token)
            raise
        if not self._attach_modal_occlusion_release(occlusion_token=modal_token):
            _call_modal_occlusion("modal_dialog", False, token=modal_token)

    async def _show_quiz_auth_dialog(self, retry_callback):
        """Reuse the app credential dialog, then retry Moodle session setup."""
        from gui.components.login_dialog import show_login_dialog

        self._invalidate_active_webviews_before_credential_mutation()
        self._diag_screen("login")
        self._diag_breadcrumb("login", "started", "quiz_player", screen="login")
        modal_token = _new_modal_occlusion_token()
        _call_modal_occlusion("modal_dialog", True, token=modal_token)

        async def _on_login_success():
            _call_modal_occlusion("modal_dialog", False, token=modal_token)
            self._diag_breadcrumb("login", "succeeded", "moodle", screen="home")
            self._diag_screen("home")
            await self._complete_login_success()
            if callable(retry_callback):
                result = retry_callback()
                if inspect.isawaitable(result):
                    await result

        try:
            await show_login_dialog(
                self.page,
                self.orchestrator,
                _on_login_success,
                allow_cancel=True,
            )
        except Exception:
            _call_modal_occlusion("modal_dialog", False, token=modal_token)
            raise
        if not self._attach_modal_occlusion_release(occlusion_token=modal_token):
            _call_modal_occlusion("modal_dialog", False, token=modal_token)

    def _restore_refresh_button(self) -> None:
        refresh_btn = getattr(self, "refresh_btn", None)
        if refresh_btn is None or not getattr(refresh_btn, "disabled", False):
            return
        refresh_btn.disabled = False
        upd = getattr(refresh_btn, "update", None)
        if callable(upd):
            try:
                upd()
                return
            except Exception:
                pass
        page = getattr(self, "page", None)
        page_upd = getattr(page, "update", None)
        if callable(page_upd):
            try:
                page_upd()
            except Exception:
                pass

    def _teardown_detail_view_for_account_switch(self, *, clear_ui: bool) -> None:
        """Reset DetailView state and close any open detail overlay on account switch."""
        detail_view = getattr(self, "detail_view", None)
        view_manager = getattr(self, "view_manager", None)
        detail_was_open = bool(
            getattr(detail_view, "visible", False)
            or (
                view_manager is not None
                and getattr(view_manager, "detail_switcher", None) is not None
                and getattr(view_manager.detail_switcher, "content", None) is detail_view
            )
        )
        reset_detail = getattr(detail_view, "reset_for_account_switch", None)
        if callable(reset_detail):
            try:
                reset_detail(account_epoch=getattr(self, "_account_epoch", 0))
            except TypeError:
                reset_detail()
            except Exception:
                logger.debug("DetailView account reset failed", exc_info=True)
        if not clear_ui:
            return
        from_calendar = bool(getattr(self, "_detail_from_calendar", False))
        self._detail_from_calendar = False
        if view_manager is not None and detail_was_open:
            origin = getattr(view_manager, "_detail_origin", None)
            if origin is None:
                origin = (
                    getattr(self, "calendar_view", None)
                    if from_calendar
                    else getattr(self, "dashboard", None)
                )
            clear_layer = getattr(view_manager, "_clear_detail_layer", None)
            if callable(clear_layer):
                try:
                    clear_layer()
                    if origin is not None and not getattr(
                        getattr(self, "settings_view", None), "visible", False
                    ):
                        origin.visible = True
                        origin.opacity = 1.0
                        if getattr(view_manager, "detail_switcher", None) is None:
                            show_only = getattr(view_manager, "_show_only", None)
                            if callable(show_only):
                                show_only(origin)
                except Exception:
                    logger.debug("Detail overlay clear failed", exc_info=True)
            else:
                close_detail = getattr(view_manager, "close_detail", None)
                safe_run = getattr(self, "_safe_run_task", None)
                if callable(close_detail) and callable(safe_run):
                    safe_run(close_detail, from_calendar)
            page = getattr(self, "page", None)
            update_fn = getattr(page, "update", None)
            if callable(update_fn):
                try:
                    update_fn()
                except Exception:
                    pass
        elif detail_view is not None:
            try:
                detail_view.visible = False
            except Exception:
                pass

    def _teardown_account_session(
        self,
        new_identity: tuple[str, str],
        *,
        reset_moodle_and_caches: bool,
        account_switched_or_logged_out: bool,
        schedule_refresh_trigger: str | None,
        trigger_android_logout: bool = True,
        clear_ui: bool = True,
        mark_logged_out: bool = False,
        preserve_login_state: bool = False,
    ) -> int:
        """Unified account-switch & logout teardown.

        Flips ``_settings_identity`` and bumps ``_account_epoch`` FIRST so no
        in-flight sync/prefetch/grade/schedule task from the previous account
        can coalesce with or overwrite the new account's caches, UI, or alerts.
        """
        new_username = str(new_identity[0] or "").strip()
        # 1. Flip identity & advance epochs BEFORE scheduling or mutating anything.
        self._settings_identity = new_identity
        should_advance_account_epoch = bool(
            reset_moodle_and_caches
            or account_switched_or_logged_out
            or mark_logged_out
        )
        if should_advance_account_epoch:
            self._account_epoch = getattr(self, "_account_epoch", 0) + 1
            self._detail_request_epoch = (
                getattr(self, "_detail_request_epoch", 0) + 1
            )
            self._grades_request_epoch = (
                getattr(self, "_grades_request_epoch", 0) + 1
            )
            self._course_grades_request_epoch = (
                getattr(self, "_course_grades_request_epoch", 0) + 1
            )
        else:
            self._account_epoch = getattr(self, "_account_epoch", 0)
        self._sync_generation = getattr(self, "_sync_generation", 0) + 1
        self._prefetch_epoch = getattr(self, "_prefetch_epoch", 0) + 1
        self._is_loading = False
        self._active_sync_epoch = None
        self._active_sync_generation = None
        self._restore_refresh_button()

        # 2. Invalidate in-flight sync coalescing & cancel background warmers.
        sync_coord = getattr(self, "_sync_coordinator", None)
        if sync_coord is not None:
            invalidate = getattr(sync_coord, "invalidate", None)
            if callable(invalidate):
                invalidate(account_key=new_identity)
            else:
                wake = getattr(sync_coord, "wake", None)
                if callable(wake):
                    wake()

        old_prefetch_cancel = getattr(self, "_prefetch_cancel_event", None)
        if old_prefetch_cancel is not None:
            old_prefetch_cancel.set()
        self._prefetch_cancel_event = threading.Event()

        old_warm_cancel = getattr(self, "_card_detail_warm_cancel_event", None)
        if old_warm_cancel is not None:
            old_warm_cancel.set()
        self._card_detail_warm_cancel_event = threading.Event()

        # 3. Clear in-memory activity data & pending local submission overrides on account switch/logout.
        if reset_moodle_and_caches:
            data_lock = getattr(self, "_data_lock", None)
            if data_lock is not None:
                with data_lock:
                    self.all_data = []
                    self._pending_updates = {}
            else:
                if hasattr(self, "all_data"):
                    self.all_data = []
                if hasattr(self, "_pending_updates"):
                    self._pending_updates = {}
            cards_lock = getattr(self, "_cards_lock", None)
            if cards_lock is not None:
                with cards_lock:
                    self.active_cards = []
            elif hasattr(self, "active_cards"):
                self.active_cards = []
            self._last_successful_sync_at = None
            self._cache_requires_server_refresh = False

        # Always drop transient Moodle HTML session state on credential change.
        if reset_moodle_and_caches or account_switched_or_logged_out:
            try:
                settings.MOODLE_SESSION = ""
                if account_switched_or_logged_out:
                    raw_owner = getattr(settings, "MOODLE_WS_TOKEN_USERNAME", "")
                    token_owner = raw_owner.strip() if isinstance(raw_owner, str) else ""
                    clean_new = str(new_username or "").strip()
                    if (
                        not clean_new
                        or is_demo_account(clean_new)
                        or not token_owner
                        or token_owner.casefold() != clean_new.casefold()
                    ):
                        settings.MOODLE_WS_TOKEN = ""
                        settings.MOODLE_WS_TOKEN_ORIGIN = ""
                        settings.MOODLE_WS_TOKEN_USERNAME = ""
            except Exception:
                pass

        # 4. Clear Portal tokens.
        for portal_attr in ("_portal_client", "_portal_week_client"):
            portal_obj = getattr(self, portal_attr, None)
            clear_token = getattr(portal_obj, "clear_token", None)
            if callable(clear_token):
                try:
                    clear_token()
                except Exception:
                    logger.debug(
                        "Portal token clear failed (%s)", portal_attr, exc_info=True
                    )

        moodle_ns = (
            f"{settings.MOODLE_BASE_URL}|{new_username}"
            if new_username
            else f"{settings.MOODLE_BASE_URL}|__logged_out__"
        )
        courses_ns = f"{settings.MOODLE_BASE_URL}|{new_username or 'anon'}"
        schedule_namespace = (
            f"{settings.PORTAL_API_BASE}|{new_username}"
            if new_username
            else f"{settings.PORTAL_API_BASE}|anonymous"
        )

        # 5. Clear Moodle caches, sessions, grade monitors, notification state & WebViews.
        if reset_moodle_and_caches:
            orchestrator = getattr(self, "orchestrator", None)
            if orchestrator is not None:
                try:
                    preserved_logged_in = (
                        bool(getattr(orchestrator, "is_logged_in", False))
                        if preserve_login_state and not mark_logged_out
                        else False
                    )
                    clear_caches = getattr(orchestrator, "clear_caches", None)
                    if callable(clear_caches):
                        clear_caches()
                    moodle_svc = getattr(orchestrator, "moodle_service", None)
                    clear_all = getattr(moodle_svc, "clear_all_caches", None)
                    if callable(clear_all):
                        clear_all()
                    clear_detail = getattr(orchestrator, "clear_detail_cache", None)
                    if callable(clear_detail):
                        clear_detail()
                    orchestrator._userid_cache = None
                    if hasattr(orchestrator, "_thnn_activities_cache"):
                        orchestrator._thnn_activities_cache = []
                    if hasattr(orchestrator, "_last_thnn_fetch_ts"):
                        orchestrator._last_thnn_fetch_ts = 0.0
                    if mark_logged_out or hasattr(orchestrator, "is_logged_in"):
                        orchestrator.is_logged_in = preserved_logged_in
                    for client_attr in ("client", "thnn_client"):
                        client_obj = getattr(orchestrator, client_attr, None)
                        clear_session = getattr(client_obj, "clear_session", None)
                        if callable(clear_session):
                            clear_session()
                    for monitor_attr in ("course_grade_monitor", "grade_monitor"):
                        monitor = getattr(orchestrator, monitor_attr, None)
                        if monitor is None:
                            continue
                        reset_fn = getattr(monitor, "reset", None)
                        if callable(reset_fn):
                            reset_fn()
                        set_grade_ns = getattr(monitor, "set_namespace", None)
                        if callable(set_grade_ns):
                            set_grade_ns(
                                f"{settings.MOODLE_BASE_URL}|{new_username}"
                                if new_username
                                else moodle_ns
                            )
                    if hasattr(orchestrator, "latest_course_grades"):
                        orchestrator.latest_course_grades = []
                except Exception:
                    logger.debug(
                        "Moodle cache clear failed during account teardown",
                        exc_info=True,
                    )

            # Clear & re-namespace NotificationManager (seam with core branch).
            seen_notifiers: set[int] = set()
            for notif_obj in (
                getattr(self, "notifier", None),
                getattr(getattr(self, "orchestrator", None), "notifier", None),
            ):
                if notif_obj is None or id(notif_obj) in seen_notifiers:
                    continue
                seen_notifiers.add(id(notif_obj))
                try:
                    clear_acct = getattr(notif_obj, "clear_account_state", None)
                    if callable(clear_acct):
                        clear_acct()
                    set_notif_ns = getattr(notif_obj, "set_namespace", None)
                    if callable(set_notif_ns):
                        set_notif_ns(moodle_ns)
                except Exception:
                    logger.debug(
                        "Notification account reset failed", exc_info=True
                    )

            # Clear AcademicGradeService session / cached Portal client.
            grade_service = getattr(self, "_academic_grade_service", None)
            if grade_service is not None:
                try:
                    clear_grade_sess = getattr(grade_service, "clear_session", None)
                    if callable(clear_grade_sess):
                        clear_grade_sess()
                    else:
                        gs_portal = getattr(grade_service, "_portal_client", None)
                        gs_clear = getattr(gs_portal, "clear_token", None)
                        if callable(gs_clear):
                            gs_clear()
                except Exception:
                    logger.debug(
                        "AcademicGradeService session clear failed", exc_info=True
                    )

            self._data_cache = _build_account_data_cache(new_username)

            # Close active WebViews BEFORE purging profiles/cookies so locks release.
            close_wv = getattr(self, "_close_app_webview", None)
            if callable(close_wv):
                try:
                    close_wv(update_page=False, clear_browser_storage=True)
                except TypeError:
                    close_wv(update_page=False)

            courses_coord = getattr(self, "_courses_coordinator", None)
            reset_courses = getattr(courses_coord, "reset_for_account_switch", None)
            if callable(reset_courses):
                reset_courses(courses_ns)

            if account_switched_or_logged_out and sys.platform == "win32":
                try:
                    from gui.components.courses.embedded_webview_host import (
                        webview2_account_profile_name,
                    )

                    keep_profile = (
                        webview2_account_profile_name(courses_ns)
                        if new_username
                        else None
                    )
                    self._schedule_webview2_profile_purge(keep_profile)
                except Exception:
                    logger.debug("WebView2 profile purge skipped", exc_info=True)

        # 6. Clear schedule caches & views.
        week_cache = getattr(self, "_week_schedule_cache", None)
        set_week_ns = getattr(week_cache, "set_namespace", None)
        if callable(set_week_ns):
            set_week_ns(schedule_namespace)
        calendar_view = getattr(self, "calendar_view", None)
        reset_schedule = getattr(calendar_view, "reset_schedule", None)
        if callable(reset_schedule):
            reset_schedule()
        today_coord = getattr(self, "_today_schedule_coordinator", None)
        replace_cache = getattr(today_coord, "replace_cache", None)
        if callable(replace_cache):
            replace_cache(TodayScheduleCache(namespace=schedule_namespace))
        self._today_schedule_state = TodayScheduleViewState(ScheduleLoadStatus.IDLE)
        component = getattr(self, "_today_schedule_component", None)
        set_state = getattr(component, "set_state", None)
        if callable(set_state):
            set_state(self._today_schedule_state)

        # 7. Clear UI surfaces when visible so stale account data disappears immediately.
        if reset_moodle_and_caches or clear_ui:
            self._teardown_detail_view_for_account_switch(clear_ui=clear_ui)
        else:
            detail_view = getattr(self, "detail_view", None)
            if detail_view is not None and hasattr(
                detail_view, "_owner_account_epoch"
            ):
                detail_view._owner_account_epoch = getattr(
                    self, "_account_epoch", 0
                )
        if clear_ui:
            quiz_player_view = getattr(self, "quiz_player_view", None)
            if quiz_player_view is not None and getattr(
                quiz_player_view, "visible", False
            ):
                for close_attr in ("force_close", "close"):
                    close_fn = getattr(quiz_player_view, close_attr, None)
                    if callable(close_fn):
                        try:
                            close_fn()
                        except Exception:
                            logger.debug("Quiz player close failed", exc_info=True)
                        break
                try:
                    quiz_player_view.visible = False
                except Exception:
                    pass
                self._sync_quiz_immersive(False)

            if hasattr(self, "cards_list") and getattr(
                self.cards_list, "controls", None
            ) is not None:
                self.cards_list.controls = []
            for view_attr in ("grade_overview_view", "course_grades_view"):
                view_obj = getattr(self, view_attr, None)
                if view_obj is None:
                    continue
                clear_fn = getattr(view_obj, "clear", None) or getattr(
                    view_obj, "reset", None
                )
                if callable(clear_fn):
                    try:
                        clear_fn()
                    except Exception:
                        pass

        # 8. Queue async teardowns & refreshes in deterministic order:
        #    (a) clear old class-schedule reminders FIRST,
        #    (b) refresh today schedule for the new identity if requested,
        #    (c) clear persisted Quiz WebView sessions EVEN IF quiz_player_view was visible,
        #    (d) clear Android native background vault/Room snapshot.
        self._safe_run_task(
            self._reconcile_class_schedule_notifications, (), clear_only=True
        )
        if schedule_refresh_trigger and today_coord is not None:
            self._safe_run_task(today_coord.refresh, schedule_refresh_trigger)

        if reset_moodle_and_caches:
            quiz_player_view = getattr(self, "quiz_player_view", None)
            clear_quiz_sessions = getattr(
                quiz_player_view, "clear_persisted_sessions", None
            )
            if callable(clear_quiz_sessions):
                self._safe_run_task(clear_quiz_sessions)
            if self._is_macos_native_runtime():
                self._safe_run_task(self._clear_macos_persisted_webview_sessions)

            android_bg = getattr(self, "_android_background", None)
            if (
                trigger_android_logout
                and android_bg is not None
                and getattr(android_bg, "available", False)
            ):
                if is_demo_account(new_username):
                    async def _reset_demo_android_bg(bg=android_bg) -> None:
                        await bg.logout()
                        await bg.configure("", token_origin="")

                    self._safe_run_task(_reset_demo_android_bg)
                else:
                    self._safe_run_task(android_bg.logout)

        return self._account_epoch

    async def _complete_login_success(self) -> None:
        """Switch account-owned caches and schedules after either login flow."""
        old_username = (
            str(self._settings_identity[0] or "").strip()
            if getattr(self, "_settings_identity", None)
            else ""
        )
        new_identity = (settings.UTH_USERNAME, settings.UTH_PASSWORD)
        identity_changed = new_identity != getattr(self, "_settings_identity", None)
        new_username = str(settings.UTH_USERNAME or "").strip()
        account_switched_or_logged_out = (
            old_username.casefold() != new_username.casefold()
        ) or not new_username
        do_android_logout = identity_changed and account_switched_or_logged_out

        self._teardown_account_session(
            new_identity,
            reset_moodle_and_caches=identity_changed,
            account_switched_or_logged_out=account_switched_or_logged_out,
            schedule_refresh_trigger="login",
            trigger_android_logout=False,
            clear_ui=identity_changed,
            preserve_login_state=True,
        )
        if do_android_logout or is_demo_account(settings.UTH_USERNAME):
            mobile_bridge = getattr(self, "_android_background", None)
            if mobile_bridge is not None and getattr(mobile_bridge, "available", False):
                try:
                    await mobile_bridge.logout()
                    if is_demo_account(settings.UTH_USERNAME):
                        await mobile_bridge.configure("", token_origin="")
                except Exception:
                    logger.debug(
                        "Android background logout after login skipped",
                        exc_info=True,
                    )
        await self._load_data_async()

    def _is_macos_native_runtime(self) -> bool:
        page = getattr(self, "page", None)
        if page is not None and bool(getattr(page, "web", False)):
            return False
        plat_str = str(getattr(page, "platform", "") or "").lower()
        return sys.platform == "darwin" or "macos" in plat_str

    async def _clear_macos_persisted_webview_sessions(self) -> bool:
        """Invoke UthQuizSessionCleaner on macOS so WKWebsiteDataStore localStorage & cookies wipe on logout."""
        cleaner = getattr(self, "_macos_quiz_session_cleaner", None)
        if cleaner is None:
            try:
                from flet_uth_quiz import UthQuizSessionCleaner
            except ImportError:
                try:
                    from platform_utils.quiz_client_bootstrap import (
                        add_source_quiz_package_to_path,
                    )

                    repo_root = Path(__file__).resolve().parents[2]
                    add_source_quiz_package_to_path(repo_root)
                    from flet_uth_quiz import UthQuizSessionCleaner
                except Exception:
                    return False
            except Exception:
                return False
            try:
                cleaner = UthQuizSessionCleaner()
            except Exception:
                return False
            page = getattr(self, "page", None)
            services = getattr(page, "services", None)
            if isinstance(services, list) and cleaner not in services:
                services.append(cleaner)
                update_fn = getattr(page, "update", None)
                if callable(update_fn):
                    try:
                        update_fn()
                    except Exception:
                        pass
            self._macos_quiz_session_cleaner = cleaner
        clear_fn = getattr(cleaner, "clear_sessions", None)
        if not callable(clear_fn):
            return False
        try:
            return bool(await clear_fn())
        except Exception:
            return False

    def _committed_webview_credentials(self) -> tuple[str, str]:
        username = str(getattr(settings, "UTH_USERNAME", "") or "")
        password = str(getattr(settings, "UTH_PASSWORD", "") or "")
        committed = getattr(self, "_settings_identity", None)
        if (
            isinstance(committed, tuple)
            and len(committed) == 2
            and committed != (username, password)
        ):
            return ("", "")
        return (username, password)

    def _invalidate_active_webviews_before_credential_mutation(self) -> None:
        """Bump account epoch and close active WebViews BEFORE mutating settings credentials (SEC-134-01)."""
        self._account_epoch = getattr(self, "_account_epoch", 0) + 1
        close_wv = getattr(self, "_close_app_webview", None)
        if callable(close_wv):
            try:
                close_wv(update_page=False, clear_browser_storage=True)
            except TypeError:
                close_wv(update_page=False)
            except Exception:
                logger.debug("Pre-mutation app WebView close skipped", exc_info=True)
        courses_coord = getattr(self, "_courses_coordinator", None)
        if courses_coord is not None:
            courses_view = getattr(courses_coord, "courses_view", None)
            overlays = getattr(courses_view, "_overlays", None)
            if isinstance(overlays, list):
                for ov in list(overlays):
                    disp = getattr(ov, "dispose", None)
                    if callable(disp):
                        try:
                            disp(clear_browser_storage=True)
                        except TypeError:
                            disp()
                        except Exception:
                            pass
            clear_overlays = getattr(courses_view, "clear_overlays", None)
            if callable(clear_overlays):
                try:
                    clear_overlays(update=False)
                except TypeError:
                    clear_overlays()
                except Exception:
                    logger.debug("Pre-mutation overlay clear skipped", exc_info=True)
        orchestrator_pre = getattr(self, "orchestrator", None)
        if orchestrator_pre is not None:
            for client_attr in ("client", "thnn_client"):
                client_obj = getattr(orchestrator_pre, client_attr, None)
                clear_session = getattr(client_obj, "clear_session", None)
                if callable(clear_session):
                    try:
                        clear_session()
                    except Exception:
                        logger.debug("Pre-mutation Moodle session clear skipped", exc_info=True)
        for portal_attr in ("_portal_client", "_portal_week_client"):
            portal_obj = getattr(self, portal_attr, None)
            clear_token = getattr(portal_obj, "clear_token", None)
            if callable(clear_token):
                try:
                    clear_token()
                except Exception:
                    logger.debug("Pre-mutation portal clear skipped", exc_info=True)

    async def _handle_settings_account_login_async(
        self, username: str, password: str
    ) -> None:
        """Unified Settings -> Tài khoản login handler routed through full isolation."""
        self._invalidate_active_webviews_before_credential_mutation()
        raw_user = str(username or "")
        clean_user = (
            normalize_demo_username(raw_user)
            if is_demo_account(raw_user)
            else raw_user.strip()
        )
        clean_pass = str(password or "")
        settings.UTH_USERNAME = clean_user
        settings.UTH_PASSWORD = clean_pass
        settings.UTH_CREDENTIALS_ORIGIN = ""
        settings.MOODLE_WS_TOKEN = ""
        settings.MOODLE_WS_TOKEN_ORIGIN = ""
        settings.MOODLE_WS_TOKEN_USERNAME = ""
        settings.MOODLE_SESSION = ""
        orchestrator = getattr(self, "orchestrator", None)
        if orchestrator is not None and hasattr(orchestrator, "is_logged_in"):
            orchestrator.is_logged_in = False
        await asyncio.to_thread(save_settings)
        await self._complete_login_success()

    async def _handle_logout_async(self) -> None:
        """Sign out cleanly: erase persisted secrets, tear down all caches, prompt login."""
        self._invalidate_active_webviews_before_credential_mutation()
        settings.UTH_USERNAME = ""
        settings.UTH_PASSWORD = ""
        settings.UTH_CREDENTIALS_ORIGIN = ""
        settings.MOODLE_WS_TOKEN = ""
        settings.MOODLE_WS_TOKEN_ORIGIN = ""
        settings.MOODLE_WS_TOKEN_USERNAME = ""
        settings.MOODLE_SESSION = ""
        await asyncio.to_thread(save_settings)

        self._teardown_account_session(
            ("", ""),
            reset_moodle_and_caches=True,
            account_switched_or_logged_out=True,
            schedule_refresh_trigger="logout",
            trigger_android_logout=True,
            clear_ui=True,
            mark_logged_out=True,
        )
        if getattr(getattr(self, "settings_view", None), "visible", False):
            close_settings = getattr(self, "_close_settings", None)
            if callable(close_settings):
                await close_settings()
        update_footer = getattr(self, "_update_footer", None)
        if callable(update_footer) and hasattr(self, "cards_list"):
            try:
                update_footer()
            except Exception:
                logger.debug("Footer refresh after logout failed", exc_info=True)
        show_snack = getattr(self, "_show_snackbar", None)
        if callable(show_snack):
            show_snack("Đã đăng xuất và xóa thông tin đăng nhập", ft.Icons.LOGOUT_ROUNDED, C.SAFE)
        if getattr(self, "page", None) is not None and callable(
            getattr(self, "_show_login_dialog", None)
        ):
            self._safe_run_task(self._show_login_dialog)

    async def _apply_initial_windows_autostart_default(self) -> None:
        """Register the new-install Windows default exactly once."""
        from platform_utils.autostart import create_autostart_service

        try:
            result = await apply_initial_autostart_default(
                create_autostart_service(),
                settings,
                save_settings,
            )
        except Exception:
            logger.warning("Initial Windows autostart default failed", exc_info=True)
            return
        if result is not None and not result.success:
            logger.warning("Windows did not accept the autostart default: %s", result.message)

    def _safe_run_task(self, handler, *args, **kwargs):
        if not hasattr(self.page, "session") or self.page.session is None:
            return None
        if not hasattr(self.page.session, "connection") or self.page.session.connection is None:
            return None

        async def guarded_task():
            try:
                return await handler(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reference = None
                reporter = getattr(self, "_unhandled_error_reporter", None)
                if callable(reporter):
                    try:
                        reference = reporter(exc)
                    except Exception:
                        logger.debug(
                            "Unhandled-error reporter failed", exc_info=True
                        )
                logger.exception(
                    "Background UI task failed; reference=%s",
                    reference or "none",
                )
                return None

        return self.page.run_task(guarded_task)

    def _run_page_task(self, handler, *args, **kwargs) -> Future | None:
        if not hasattr(self.page, "session") or self.page.session is None:
            return None
        if not hasattr(self.page.session, "connection") or self.page.session.connection is None:
            return None

        result_future: Future = Future()

        async def _runner():
            try:
                value = await handler(*args, **kwargs)
            except asyncio.CancelledError:
                if not result_future.done():
                    result_future.cancel()
                raise
            except BaseException as exc:
                if not result_future.done():
                    result_future.set_exception(exc)
                return None
            if not result_future.done():
                result_future.set_result(value)
            return value

        task_future = self.page.run_task(_runner)
        if task_future is None:
            return None
        cancel_task = getattr(task_future, "cancel", None)
        add_done_callback = getattr(task_future, "add_done_callback", None)
        if callable(cancel_task):
            def _propagate_cancel(done_future):
                if done_future.cancelled():
                    try:
                        cancel_task()
                    except Exception:
                        pass

            result_future.add_done_callback(_propagate_cancel)
        if callable(add_done_callback):
            def _sync_task_done(done_task):
                if result_future.done():
                    return
                try:
                    if done_task.cancelled():
                        result_future.cancel()
                        return
                    exc = done_task.exception()
                    if exc is not None:
                        result_future.set_exception(exc)
                    else:
                        result_future.set_result(done_task.result())
                except BaseException:
                    pass

            add_done_callback(_sync_task_done)
        return result_future

    def _current_update_release_version(self) -> str | None:
        coordinator = getattr(self, "_update_coordinator", None)
        candidate = None
        if coordinator is not None and callable(
            getattr(coordinator, "current_candidate", None)
        ):
            candidate = coordinator.current_candidate()
        if candidate is None:
            candidate = getattr(self, "_update_candidate", None)
        manifest = getattr(candidate, "manifest", None)
        version = getattr(manifest, "release_version", None)
        return str(version) if version else None

    def _get_distribution_profile(self) -> DistributionProfile:
        profile = getattr(self, "_distribution_profile", None)
        if profile is not None:
            return profile
        return resolve_distribution_channel()

    def _create_update_coordinator(self) -> UpdateCoordinator | None:
        if not self._get_distribution_profile().supports_github_self_update:
            return None
        from core.update_checker import VerifiedDownloader
        from platform_utils.update_packages import DownloadedPackageVerifier

        release_client_cls = _resolve_lazy_attr("GitHubReleaseClient")
        target = detect_runtime_target()
        downloader = VerifiedDownloader()
        if target.platform == "windows":
            from platform_utils.windows_update import (
                WindowsPackageLauncher,
                WindowsPackageVerifier,
            )

            verifier = WindowsPackageVerifier()
            launcher = WindowsPackageLauncher()
        elif target.platform == "android":
            from platform_utils.android_updates import (
                AndroidPackageDownloader,
                AndroidPackageLauncher,
                AndroidPackageVerifier,
            )

            verifier = AndroidPackageVerifier()
            launcher = AndroidPackageLauncher(
                self._android_background,
                self._run_page_task,
                self._current_update_release_version,
            )
            downloader = AndroidPackageDownloader(
                self._android_background,
                self._run_page_task,
                self._current_update_release_version,
            )
        else:
            verifier = DownloadedPackageVerifier()
            launcher = _UnavailablePackageLauncher()
        return UpdateCoordinator(
            release_client_cls(),
            downloader,
            verifier,
            launcher,
            target,
            _APP_VERSION,
            self._on_update_event,
            automatic_enabled=settings.AUTO_UPDATE_ENABLED,
            check_interval_seconds=86400,
            external_opener=self._open_external_update_url,
        )

    def _open_external_update_url(self, url: str) -> bool:
        if (
            not isinstance(url, str)
            or "\\" in url
            or any(ord(ch) < 32 or 0x7F <= ord(ch) <= 0x9F for ch in url)
        ):
            return False
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return False
        decoded_path = unquote(parsed.path)
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").lower() != "github.com"
            or port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "\\" in decoded_path
            or not parsed.path.startswith("/Chouwzi/UTHelper/releases/")
            or not decoded_path.startswith("/Chouwzi/UTHelper/releases/")
            or ".." in decoded_path.replace("\\", "/").split("/")
        ):
            return False

        async def _open():
            await self.page.launch_url(url)

        return self._safe_run_task(_open) is not None

    def _open_store_url(
        self,
        url: str,
        fallback_url: str | None = None,
    ) -> bool:
        allowed_schemes = {"market", "ms-windows-store", "https"}
        if urlsplit(url).scheme.lower() not in allowed_schemes:
            return False
        safe_fallback = (
            fallback_url
            if fallback_url and urlsplit(fallback_url).scheme.lower() == "https"
            else None
        )

        async def _launch_single(target: str) -> None:
            try:
                await ft.UrlLauncher().launch_url(
                    target,
                    mode=ft.LaunchMode.EXTERNAL_APPLICATION,
                )
                return
            except RuntimeError as exc:
                if "must be added to the page first" not in str(exc):
                    raise
                launch_fn = getattr(self.page, "launch_url", None)
                if (
                    not callable(launch_fn)
                    or getattr(launch_fn, "__func__", None) is ft.Page.launch_url
                ):
                    raise
                await launch_fn(target)

        async def _open() -> None:
            try:
                await _launch_single(url)
            except Exception:
                if safe_fallback:
                    try:
                        await _launch_single(safe_fallback)
                        return
                    except Exception:
                        logger.warning(
                            "Failed to open store fallback URL",
                            exc_info=True,
                        )
                        return
                logger.warning("Failed to open store URL", exc_info=True)

        return self._safe_run_task(_open) is not None

    def _cancel_update_download(self) -> None:
        coordinator = getattr(self, "_update_coordinator", None)
        if coordinator is not None:
            coordinator.cancel_download()

    def _check_updates_now(self) -> None:
        if not self._get_distribution_profile().supports_github_self_update:
            return
        coordinator = getattr(self, "_update_coordinator", None)
        if coordinator is None:
            return
        self._diag_breadcrumb("update_check", "started", "github")
        self._manual_update_check_requested = True
        coordinator.check_now()

    def _submission_workflow_factory(self, client):
        """Build a fresh workflow only for the orchestrator's current client."""
        if client is not getattr(self.orchestrator, "client", None):
            return None
        configured_site = moodle_site_from_origin(settings.MOODLE_BASE_URL)
        site = moodle_site_from_origin(getattr(client, "moodle_site_origin", None))
        call_ws_api = getattr(client, "call_ws_api", None)
        if (
            configured_site is None
            or configured_site != site
            or not callable(call_ws_api)
        ):
            return None
        return SubmissionWorkflow(
            client,
            MoodleService(call_ws_api),
            site_origin=site,
        )

    def _native_background_credentials(self) -> tuple[str, str]:
        """Return only a token proven to belong to the active Moodle client."""
        if is_demo_account(settings.UTH_USERNAME):
            return "", ""
        raw_owner = getattr(settings, "MOODLE_WS_TOKEN_USERNAME", "")
        token_owner = raw_owner.strip() if isinstance(raw_owner, str) else ""
        active_user = str(getattr(settings, "UTH_USERNAME", "") or "").strip()
        if token_owner and token_owner.casefold() != active_user.casefold():
            return "", ""
        configured_site = moodle_site_from_origin(settings.MOODLE_BASE_URL)
        client_site = moodle_site_from_origin(
            getattr(getattr(self.orchestrator, "client", None), "moodle_site_origin", None)
        )
        token_site = moodle_site_from_origin(settings.MOODLE_WS_TOKEN_ORIGIN)
        token = settings.MOODLE_WS_TOKEN
        if (
            token
            and configured_site is not None
            and configured_site == client_site == token_site
        ):
            return token, token_site.origin
        return "", ""

    def _init_window(self):
        self.window_activator = WindowActivator(self.page)
        # Resolve the presentation profile without mutating native capabilities.
        detect_platform(self.page)
        # Read the immutable runtime capabilities after resolving the UI profile.
        import platform_utils
        _is_mobile = platform_utils.IS_MOBILE
        _is_windows = platform_utils.IS_WINDOWS

        if platform_utils.IS_ANDROID or platform_utils.IS_IOS:
            from platform_utils.background_sync import create_mobile_background_bridge

            self._android_background = create_mobile_background_bridge(self.page)
        
        # Chỉ dành cho Desktop: Cửa sổ kích thước linh hoạt hỗ trợ khay hệ thống
        if not _is_mobile:
            # PC: cửa sổ co giãn được. Rộng >= _WIDE_BREAKPOINT sẽ chuyển sang
            # bố cục 2 cột; hẹp hơn vẫn dùng bố cục gọn như bản cũ.
            self.page.window.width         = 1120
            self.page.window.height        = 760
            self.page.window.min_width     = 420
            self.page.window.min_height    = 560
            self.page.window.max_width     = None
            self.page.window.max_height    = None
            self.page.window.always_on_top = settings.ALWAYS_ON_TOP
            self.page.window.resizable     = True
            self.page.window.maximizable   = True
            self.page.window.minimizable   = True
            self.page.window.title_bar_hidden = True
            self.page.window.title_bar_buttons_hidden = True
            self.page.window.icon          = "icon.ico"  # Icon của app, để ở thư mục assets
            self.page.window.prevent_close = True
            self.page.window.on_event = self._on_window_event
        
        self.page.title               = "UTHelper"
        self.page.bgcolor             = C.BG
        self.page.padding             = 0
        self.page.spacing             = 0
        self.page.theme_mode          = ft.ThemeMode.LIGHT

        # Đồng bộ ColorScheme theme của Flet page với các giá trị C của chúng ta
        from gui.core.theme import set_page_theme
        set_page_theme(self.page)
        
        # Khay hệ thống & Thông báo (tự động nhận biết nền tảng)
        tray_ready = False
        notification_manager_cls = _resolve_lazy_attr("NotificationManager")
        if _is_windows:
            from gui.tray import TrayApp
            self.tray = TrayApp(
                self.page, on_show=self.window_activator.request_show
            )
            tray_ready = self.tray.setup()
            self.notifier = notification_manager_cls(self.tray)
        else:
            self.tray = None
            self.notifier = notification_manager_cls()
            if platform_utils.IS_IOS and self._android_background:
                self.notifier.bind_native_mobile_bridge(self._android_background)
        self.orchestrator.notifier = self.notifier

        # Runtime permission requests and native service initialization must run
        # on Flet's event loop, after accurate platform detection.
        self._safe_run_task(self.notifier.initialize, self.page)
        
        if not _is_mobile:
            if self.force_visible:
                self.page.window.visible = True
            else:
                autostart_launch = is_autostart_launch()
                hide_window = should_hide_startup_window(
                    autostart_launch=autostart_launch,
                    start_minimized=settings.START_MINIMIZED,
                    tray_ready=tray_ready,
                    is_mobile=_is_mobile,
                )
                if (
                    autostart_launch
                    and settings.START_MINIMIZED
                    and not tray_ready
                ):
                    logger.warning(
                        "Autostart requested a hidden window, but the tray was unavailable; "
                        "keeping the main window visible"
                    )
                self.page.window.visible = not hide_window

        # Publish the initial window state only after the activation receiver is
        # ready. Otherwise an autostart window can become observably hidden in
        # the small gap before a manual second launch is able to hand off SHOW.
        if self.activation_broker is not None:
            self.activation_broker.bind_show_handler(
                self.window_activator.request_show
            )
        self.page.update()
        if self.force_visible and not _is_mobile:
            self.window_activator.request_show()

    async def _on_window_event(self, e):
        # Desktop-only: Flet bản mới thì sự kiện đóng cửa sổ nằm ở e.type hoặc e.data
        if platform_utils.IS_MOBILE:
            return
        if getattr(e, "type", getattr(e, "data", "")) == ft.WindowEventType.CLOSE or e.data == "close":
            if settings.MINIMIZE_TO_TRAY:
                self.page.window.visible = False
                self.page.update()
                # H-01: Show tray balloon the first time so user knows app is still running
                if not self._tray_balloon_shown:
                    self._tray_balloon_shown = True
                    try:
                        if self.tray:
                            self.tray._icon.notify(
                                "UTHelper đang chạy ở khay hệ thống.\nNhấp đúp để mở lại.",
                                "UTHelper",
                            )
                    except Exception:
                        import logging as _fb_log
                        _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)
            else:
                await self._request_window_exit()

    def _toggle_maximize_window(self, _event=None) -> None:
        """Phóng to / khôi phục cửa sổ từ thanh tiêu đề tuỳ chỉnh."""
        self.page.window.maximized = not self.page.window.maximized
        self.page.update()

    # ------------------------------------------------------------------
    # Responsive desktop layout
    # ------------------------------------------------------------------
    def _with_inline_schedule(self, cards: list) -> list:
        """Compact: lịch hôm nay nằm đầu danh sách. Wide: nằm ở cột phải."""
        if getattr(self, "_wide_layout", False):
            return list(cards)
        return [self.today_schedule_panel] + list(cards)

    def _current_section(self) -> str:
        if getattr(getattr(self, "settings_view", None), "visible", False):
            return "settings"
        if getattr(getattr(self, "calendar_view", None), "visible", False):
            return "calendar"
        if getattr(getattr(self, "grade_overview_view", None), "visible", False) or getattr(
            getattr(self, "course_grades_view", None), "visible", False
        ):
            return "grades"
        if getattr(getattr(self, "courses_view", None), "visible", False):
            return "courses"
        return "home"

    def _sync_rail_active(self, force_home: bool = False) -> None:
        current = "home" if force_home else self._current_section()
        nav_shell = getattr(self, "_nav_shell", None)
        if nav_shell is not None and hasattr(nav_shell, "set_active"):
            nav_shell.set_active(current)
        if not getattr(self, "_rail_items", None):
            return
        for key, (box, ic, tx) in self._rail_items.items():
            active = key == current
            color = C.ACCENT if active else C.TEXT_SECONDARY
            ic.color = color
            tx.color = color
            box.bgcolor = ft.Colors.with_opacity(0.14, C.ACCENT) if active else None
        rail = getattr(self, "_rail", None)
        if rail is not None:
            try:
                rail.update()
            except Exception:
                pass

    async def _rail_navigate(self, target: str) -> None:
        """Chuyển mục từ thanh điều hướng: đóng view đang mở rồi mở view đích."""
        self._dismiss_desktop_filter_menu()
        if getattr(getattr(self, "detail_view", None), "visible", False):
            await self._close_detail()
        if getattr(getattr(self, "course_grades_view", None), "visible", False):
            await self._close_course_grades()
        current = self._current_section()
        if target == current:
            # Bấm lại đúng mục đang mở: giữ nguyên, không quay về Tổng quan.
            self._sync_rail_active()
            return
        if current == "settings":
            if self.settings_view.has_changes():
                self._sync_rail_active()
                request_close = getattr(self.settings_view, "request_close", None)
                if callable(request_close):
                    await request_close(
                        on_closed=lambda dest=target: self._safe_run_task(
                            self._leave_settings_and_navigate, dest
                        )
                    )
                else:
                    await self.settings_view._handle_back(None)
                return
            await self._close_settings()
        elif current == "calendar":
            await self._close_calendar()
        elif current == "grades":
            await self._close_grades()
        elif current == "courses":
            await self._close_courses()
        if target == "calendar":
            await self._show_calendar()
        elif target == "grades":
            await self._show_grades()
        elif target == "courses":
            await self._show_courses()
        elif target == "settings":
            await self._show_settings()
        self._sync_rail_active()

    def _toggle_maximize_window(self, _event=None) -> None:
        """Toggle desktop maximized window state from the custom header."""
        window = getattr(self.page, "window", None)
        if window is None:
            return
        window.maximized = not bool(getattr(window, "maximized", False))
        self.page.update()

    def _minimize_window(self, _event=None) -> None:
        """Minimize from the custom desktop header without hiding to tray."""
        self.page.window.minimized = True
        self.page.update()

    async def _destroy_window(self) -> None:
        prepare = getattr(self, "_prepare_async_shutdown", None)
        if callable(prepare):
            prepare()
        window = getattr(getattr(self, "page", None), "window", None)
        destroy_fn = getattr(window, "destroy", None)
        if not callable(destroy_fn):
            return
        try:
            await destroy_fn()
        except RuntimeError as exc:
            if "Session closed" not in str(exc):
                raise
            logger.debug("Window session closed before custom exit completed")

    async def _request_window_exit(self) -> None:
        """Confirm a window exit while a Moodle quiz page is mounted."""
        player_view = getattr(self, "quiz_player_view", None)
        if player_view is not None and player_view.visible:
            player_view.request_close(
                after_close=self._destroy_window,
                title="Thoát ứng dụng?",
                action_label="Thoát",
            )
            return
        await self._destroy_window()

    async def _exit_from_window_header(self, _event=None) -> None:
        """The custom close button follows the same safe exit path."""
        await self._request_window_exit()

    async def _on_keyboard_event(self, e):
        """Handle Escape back navigation and Ctrl+K / search focus shortcuts."""
        key = str(getattr(e, "key", "") or "")
        courses_view = getattr(self, "courses_view", None)
        detail_open = bool(getattr(getattr(self, "detail_view", None), "visible", False))
        if (
            courses_view is not None
            and getattr(courses_view, "visible", False)
            and not detail_open
        ):
            overlays = getattr(courses_view, "_overlays", None)
            if overlays:
                key_handler = getattr(overlays[-1], "handle_key_event", None)
                if callable(key_handler) and key_handler(e):
                    return
        if key == "Escape":
            await self._navigate_back()
            return
        if (
            courses_view is not None
            and getattr(courses_view, "visible", False)
            and not detail_open
        ):
            ctrl = bool(getattr(e, "ctrl", False) or getattr(e, "meta", False))
            if (ctrl and key in ("\\", "Backslash")) or (key == "F11" and not getattr(e, "alt", False)):
                toggle_expand = getattr(courses_view, "toggle_pane3_expanded", None)
                if callable(toggle_expand):
                    toggle_expand()
                    return
            if (ctrl and key.lower() == "k") or (
                not ctrl
                and not getattr(e, "alt", False)
                and key in ("/", "Slash")
            ):
                focus_search = getattr(courses_view, "focus_search", None)
                if callable(focus_search):
                    res = focus_search()
                    if inspect.isawaitable(res):
                        await res

    async def _on_back_button(self, e):
        """Fallback for explicit AppBar/View pop events."""
        await self._navigate_back()

    def _configure_mobile_back_navigation(self) -> bool:
        """Install root pop confirmation so system Back reaches app navigation."""
        views = getattr(self.page, "views", None)
        if not views:
            logger.warning("Mobile root View is unavailable; Back interception skipped")
            return False
        root_view = views[0]
        root_view.can_pop = False
        root_view.on_confirm_pop = self._on_root_view_confirm_pop
        # Retain the standard callback for future stacked Views/AppBars.
        self.page.on_view_pop = self._on_back_button
        return True

    async def _on_root_view_confirm_pop(self, event) -> None:
        """Cancel native pop after internal Back; allow it only on Dashboard."""
        handled = await self._navigate_back()
        view = getattr(event, "control", None)
        if view is None:
            views = getattr(self.page, "views", ())
            view = views[0] if views else None
        confirm_pop = getattr(view, "confirm_pop", None)
        if callable(confirm_pop):
            await confirm_pop(not handled)

    async def _navigate_back(self) -> bool:
        """Shared back-navigation logic. Returns True if a view was closed."""
        quiz_player_view = getattr(self, "quiz_player_view", None)
        if quiz_player_view is not None and quiz_player_view.handle_back():
            return True
        app_wv_host = getattr(self, "_app_webview_host", None)
        app_wv_pane = getattr(self, "_app_webview_pane", None)
        if app_wv_host is not None and getattr(app_wv_host, "visible", False):
            if app_wv_pane is not None and app_wv_pane.handle_back():
                return True
            self._close_app_webview()
            return True
        schedule_component = getattr(self, "_today_schedule_component", None)
        if schedule_component is not None and schedule_component.handle_back():
            return True
        courses_view = getattr(self, "courses_view", None)
        detail_over_courses = (
            bool(getattr(getattr(self, "detail_view", None), "visible", False))
            and getattr(getattr(self, "view_manager", None), "_detail_origin", None)
            is courses_view
        )
        courses_handle_back = getattr(courses_view, "handle_back", None)
        if (
            courses_view is not None
            and getattr(courses_view, "visible", False)
            and not detail_over_courses
            and callable(courses_handle_back)
            and courses_handle_back()
        ):
            return True
        handled = False
        course_grades_view = getattr(self, "course_grades_view", None)
        if self.settings_view.visible:
            await self.settings_view._handle_back(None)
            handled = True
        elif self.detail_view.visible:
            await self._close_detail()
            handled = True
        elif course_grades_view is not None and course_grades_view.visible:
            await self._close_course_grades()
            handled = True
        elif self.calendar_view.visible:
            await self._close_calendar()
            handled = True
        elif self.grade_overview_view.visible:
            grade_handle_back = getattr(self.grade_overview_view, "handle_back", None)
            if callable(grade_handle_back) and grade_handle_back():
                return True
            await self._close_grades()
            handled = True
        elif courses_view is not None and getattr(courses_view, "visible", False):
            await self._close_courses()
            handled = True
        self._sync_rail_active()
        return handled

    def _init_ui(self):
        self._skeleton_visible = True
        self._init_footer_and_filters()
        self._init_header_controls()
        self._init_banner_and_states()
        self._init_views_and_transitions()
        self._start_skeleton_shimmer()

    def _init_footer_and_filters(self):
        self._sync_status = SyncStatusIndicator("Đang khởi động...")
        self.status_text = self._sync_status.text
        self.footer_critical = ft.Text("", size=11, color=C.CRITICAL, weight=ft.FontWeight.W_600)
        self.footer_warning  = ft.Text("", size=11, color=C.WARNING,  weight=ft.FontWeight.W_600)
        self.footer_safe     = ft.Text("", size=11, color=C.SAFE,     weight=ft.FontWeight.W_600)
        self.footer_overdue  = ft.Text("", size=11, color=C.CRITICAL, weight=ft.FontWeight.W_600)

        self.urgency_filter_menu = AdaptiveFilterMenu(
            page=self.page,
            title="Mức độ",
            options=self._urgency_filter_options(),
            selected_key=self.active_urgency,
            on_select=lambda key: self._safe_run_task(self._set_urgency, key),
            mobile=platform_utils.UI_IS_MOBILE,
            trigger_width=None,
            popup_width=224,
        )
        self.type_filter_menu = AdaptiveFilterMenu(
            page=self.page,
            title="Loại hoạt động",
            options=self._type_filter_options(),
            selected_key=self.active_type,
            on_select=lambda key: self._safe_run_task(self._set_type, key),
            mobile=platform_utils.UI_IS_MOBILE,
            trigger_width=None,
            popup_width=232,
        )
        self.course_filter_menu = AdaptiveFilterMenu(
            page=self.page,
            title="Môn học",
            options=self._course_filter_options({"all": 0}),
            selected_key=self.active_course,
            on_select=lambda key: self._safe_run_task(self._set_course, key),
            mobile=platform_utils.UI_IS_MOBILE,
            trigger_width=None,
            popup_width=340,
            option_max_lines=2,
        )
        if not platform_utils.UI_IS_MOBILE:
            for menu in (
                self.urgency_filter_menu,
                self.type_filter_menu,
                self.course_filter_menu,
            ):
                menu.on_desktop_toggle = self._toggle_desktop_filter_menu
                menu.on_desktop_dismiss = self._dismiss_desktop_filter_menu

        self._search_clear_btn = ft.IconButton(
            ft.Icons.CLOSE_ROUNDED,
            icon_size=16,
            icon_color=C.TEXT_SECONDARY,
            tooltip="Xóa từ khóa tìm kiếm",
            padding=0,
            visible=False,
            size_constraints=ft.BoxConstraints(
                min_width=32, max_width=32, min_height=32, max_height=32
            ),
            on_click=self._clear_search,
        )
        self.search_field = ft.TextField(
            hint_text="Tìm hoạt động hoặc môn học",
            hint_style=ft.TextStyle(size=12, color=C.TEXT_SECONDARY),
            prefix_icon=ft.Icons.SEARCH_ROUNDED,
            suffix=self._search_clear_btn,
            border_radius=10,
            border_color=C.BORDER,
            focused_border_color=C.ACCENT,
            bgcolor=C.SURFACE,
            text_size=12.5,
            height=46 if platform_utils.UI_IS_MOBILE else 40,
            expand=True,
            content_padding=ft.Padding.only(left=12, right=8, top=8, bottom=8),
            on_change=self._on_search,
        )
        self._reset_filters_btn = ft.TextButton(
            "Xóa lọc",
            icon=ft.Icons.FILTER_ALT_OFF_ROUNDED,
            visible=False,
            height=40 if platform_utils.UI_IS_MOBILE else 36,
            style=ft.ButtonStyle(
                color=C.ACCENT,
                padding=ft.Padding.symmetric(horizontal=10, vertical=0),
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
            on_click=lambda _event: self._run_dashboard_header_action(
                self._reset_all_filters
            ),
        )
        self.courses_btn = ft.IconButton(
            ft.Icons.MENU_BOOK_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Môn học",
            on_click=lambda _event: self._run_dashboard_header_action(
                lambda: self._navigate_destination("courses")
            ),
        )
        self._nav_shell = AdaptiveNavShell(
            on_navigate=lambda key: self._safe_run_task(
                self._navigate_destination, key
            )
        )

    def _init_header_controls(self):
        btn_constraints = ft.BoxConstraints(
            min_width=44, max_width=44, min_height=44, max_height=44
        )
        self.calendar_btn = ft.IconButton(
            ft.Icons.CALENDAR_MONTH_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=20,
            tooltip="Lịch",
            padding=0,
            size_constraints=btn_constraints,
            on_click=lambda _event: self._run_dashboard_header_action(
                self._toggle_calendar
            ),
        )
        self.refresh_btn = ft.IconButton(
            ft.Icons.REFRESH_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=20,
            tooltip="Làm mới",
            padding=0,
            size_constraints=btn_constraints,
            on_click=lambda _event: self._run_dashboard_header_action(
                self._load_data_async
            ),
        )
        self.settings_btn = ft.IconButton(
            ft.Icons.SETTINGS_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=20,
            tooltip="Cài đặt",
            padding=0,
            size_constraints=btn_constraints,
            on_click=lambda _event: self._run_dashboard_header_action(
                self._show_settings
            ),
        )
        self.grades_btn = ft.IconButton(
            ft.Icons.INSIGHTS_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=20,
            tooltip="Bảng điểm",
            padding=0,
            size_constraints=btn_constraints,
            on_click=lambda _event: self._run_dashboard_header_action(
                self._toggle_grades
            ),
        )

    def _run_dashboard_header_action(self, handler) -> None:
        self._dismiss_desktop_filter_menu()
        self._safe_run_task(handler)

    def _init_banner_and_states(self):
        self._update_text = ft.Text(
            "Có phiên bản mới!",
            size=12,
            color=C.TEXT_PRIMARY,
            weight=ft.FontWeight.W_500,
            expand=True,
        )
        self._update_progress = ft.ProgressBar(
            value=0,
            height=3,
            color=C.UPDATE,
            bgcolor=C.BORDER,
            visible=False,
        )
        self._update_btn = ft.TextButton(
            "Cập nhật",
            style=ft.ButtonStyle(
                color=C.TEXT_PRIMARY, shape=ft.RoundedRectangleBorder(radius=8),
            ),
            on_click=self._request_update_download,
        )
        self._update_cancel_btn = ft.TextButton(
            "Hủy tải", visible=False,
            style=ft.ButtonStyle(
                color=C.TEXT_SECONDARY, shape=ft.RoundedRectangleBorder(radius=8),
            ),
            on_click=lambda _event: self._cancel_update_download(),
        )
        self._update_banner = ft.Container(
            content=ft.Column([
                ft.Row([self._update_text, self._update_btn, self._update_cancel_btn], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                self._update_progress,
            ], spacing=2, tight=True),
            bgcolor=C.SURFACE_ELEVATED,
            border=ft.Border.all(1, C.UPDATE),
            border_radius=10,
            padding=ft.Padding(left=12, right=8, top=8, bottom=8),
            margin=ft.Margin(left=14, right=14, top=0, bottom=0),
            visible=False,
        )
        self._update_url = ""

        self.cards_column = ft.ListView(
            spacing=8,
            expand=True,
            # Flet's scroll_to() API does not work for dynamically-built lists.
            # The dashboard is intentionally bounded to the filtered activity
            # controls already held by this controller, so eager Flutter layout
            # is the smallest reliable trade-off for exact restore + smooth top.
            build_controls_on_demand=False,
            auto_scroll=False,
            # Zero disables throttling and can flood the Python event queue
            # during a fling, delaying a subsequent button click by hundreds
            # of milliseconds. Two display frames stay visually responsive
            # while bounding bridge work on low-end desktop/mobile devices.
            scroll_interval=_DASHBOARD_SCROLL_EVENT_INTERVAL_MS,
            on_scroll=self._on_dashboard_scroll,
        )
        self._scroll_to_top_button = ft.Container(
            content=ft.Icon(
                ft.Icons.KEYBOARD_ARROW_UP_ROUNDED,
                size=23,
                color=C.TEXT_PRIMARY,
            ),
            width=44,
            height=44,
            right=12,
            bottom=12,
            alignment=ft.Alignment.CENTER,
            bgcolor=C.SURFACE_ELEVATED,
            border_radius=999,
            shadow=ft.BoxShadow(
                blur_radius=12,
                color=C.SHADOW,
                offset=ft.Offset(0, 3),
            ),
            tooltip="Lên đầu danh sách",
            ink=True,
            on_click=self._scroll_dashboard_to_top,
            opacity=0.0,
            offset=ft.Offset(0, 0.3),
            scale=0.84,
            animate_opacity=ft.Animation(110, ft.AnimationCurve.EASE_OUT),
            animate_offset=ft.Animation(150, ft.AnimationCurve.EASE_OUT_CUBIC),
            animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT_CUBIC),
            # An opacity-zero overlay must not consume taps intended for cards.
            ignore_interactions=True,
        )
        self._empty_icon = ft.Icon(ft.Icons.INBOX_ROUNDED, size=48, color=C.BORDER)
        self._empty_title = ft.Text("Không có hoạt động nào", size=14, color=C.TEXT_SECONDARY, weight=ft.FontWeight.W_500)
        self._empty_subtitle = ft.Text("Thử thay đổi bộ lọc hoặc làm mới dữ liệu", size=12, color=C.BORDER)
        self._empty_clear_filter_btn = ft.OutlinedButton(
            "Đặt lại bộ lọc",
            icon=ft.Icons.RESTART_ALT_ROUNDED,
            visible=False,
            style=ft.ButtonStyle(
                color=C.ACCENT,
                side=ft.BorderSide(1, C.BORDER),
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
            on_click=lambda _event: self._run_dashboard_header_action(
                self._reset_all_filters
            ),
        )
        self.empty_state  = ft.Container(
            content=ft.Column(controls=[
                self._empty_icon,
                ft.Container(height=4),
                self._empty_title,
                self._empty_subtitle,
                ft.Container(height=6),
                self._empty_clear_filter_btn,
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),
            alignment=ft.Alignment(0, 0), expand=True, visible=False,
        )
        self._error_icon = ft.Icon(ft.Icons.CLOUD_OFF_ROUNDED, size=48, color=C.WARNING)
        self._error_title = ft.Text("Không thể tải dữ liệu", size=14, color=C.TEXT_SECONDARY, weight=ft.FontWeight.W_600)
        self.error_text = ft.Text("Kiểm tra kết nối mạng và thử lại", size=12, color=C.BORDER)
        self._error_retry_btn = ft.Button(
            "Thử lại",
            icon=ft.Icons.REFRESH_ROUNDED,
            on_click=lambda _: self._safe_run_task(self._load_data_async),
            bgcolor=C.ACCENT, color=C.ON_ACCENT,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
        )
        self.error_state = ft.Container(
            content=ft.Column(controls=[
                self._error_icon,
                ft.Container(height=4),
                self._error_title,
                self.error_text,
                ft.Container(height=8),
                self._error_retry_btn,
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),
            alignment=ft.Alignment(0, 0), expand=True, visible=False,
        )

        self._today_schedule_component = TodaySchedulePanel(
            on_need_data=self._request_today_schedule,
            page=self.page,
            mobile=platform_utils.UI_IS_MOBILE,
        )
        self.today_schedule_panel = self._today_schedule_component.control
        self._today_schedule_component.set_state(self._today_schedule_state)

    def _init_views_and_transitions(self):
        self._app_title = ft.Text(
            "UTHelper", size=18, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY
        )
        self._version_text = ft.Text(
            APP_VERSION, size=9, color=C.TEXT_SECONDARY
        )
        self._version_badge = ft.Container(
            content=self._version_text,
            padding=ft.Padding.symmetric(horizontal=5, vertical=1),
            border=ft.Border.all(1, C.BORDER),
            border_radius=4,
        )
        self._header_container = self._build_dashboard_header()

        header = self._header_container


        self._filter_container = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row([self.search_field]),
                    self._build_dashboard_filter_row(),
                ],
                spacing=8,
                tight=True,
            ),
            padding=ft.Padding.only(left=16, right=16, top=2, bottom=10),
            bgcolor=C.BG,
        )

        self._content_area = ft.Container(
            content=ft.Column(controls=[
                ft.Stack(controls=[
                    ft.Column(controls=[self.cards_column, self.empty_state, self.error_state], spacing=0, expand=True),
                    self._scroll_to_top_button,
                ], expand=True),
            ], spacing=8, expand=True),
            padding=ft.Padding.only(left=6, right=6, bottom=10),
            expand=True, clip_behavior=ft.ClipBehavior.NONE,
        )

        self._footer = ft.Container(
            content=ft.Row(controls=[
                self.footer_critical, self.footer_warning, self.footer_safe, self.footer_overdue,
            ], spacing=12, alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=C.SURFACE, padding=ft.Padding.symmetric(vertical=10),
            border=ft.Border.only(top=ft.BorderSide(1, C.BORDER)),
            visible=False,
        )

        self._dashboard_main = ft.Column(
            controls=[
                header,
                self._update_banner,
                self._filter_container,
                self._content_area,
            ],
            spacing=0, expand=True,
        )
        # Cột phải (chỉ hiện ở chế độ rộng): lịch học hôm nay.
        self._sidebar_title = ft.Container(
            content=ft.Text(
                "LỊCH HỌC HÔM NAY", size=11, weight=ft.FontWeight.W_700,
                color=C.TEXT_SECONDARY,
            ),
            padding=ft.Padding.only(left=10, right=10, top=4, bottom=2),
        )
        self._sidebar_column = ft.Column(
            controls=[], spacing=8, scroll=ft.ScrollMode.AUTO, expand=True,
        )
        self._sidebar = ft.Container(
            content=self._sidebar_column,
            width=_SIDE_PANE_WIDTH,
            padding=ft.Padding(left=6, right=6, top=16, bottom=12),
            bgcolor=C.BG,
            border=ft.Border.only(left=ft.BorderSide(1, C.BORDER)),
            visible=False,
        )
        self._wide_layout = False
        self.dashboard = ft.Row(
            controls=[self._dashboard_main, self._sidebar],
            spacing=0, expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        self.quiz_player_view = QuizPlayerView(
            self.page,
            on_closed=self._on_quiz_player_closed,
            account_id_provider=lambda: settings.UTH_USERNAME,
            client_provider=self._quiz_client_for_site,
            on_auth_required=self._show_quiz_auth_dialog,
        )
        self._app_webview_pane = None
        self._app_webview_host = ft.Container(
            expand=True,
            visible=False,
            bgcolor=C.BG,
            left=0,
            right=0,
            top=0,
            bottom=0,
        )
        self.detail_view   = DetailView(
            self.page,
            on_close=lambda: self._safe_run_task(self._close_detail),
            get_client=lambda: self.orchestrator.client,
            on_status_changed=self._on_activity_status_changed,
            submission_workflow_factory=self._submission_workflow_factory,
            quiz_player_view=self.quiz_player_view,
            get_quiz_account_id=lambda: settings.UTH_USERNAME,
            on_open_url=self._open_url_in_app,
            on_open_external=self._open_url_external,
            get_account_epoch=lambda: getattr(self, "_account_epoch", 0),
        )
        self.calendar_view = CalendarView(
            self.page,
            on_close=lambda: self._safe_run_task(self._close_calendar),
            on_open_detail=lambda data: self._safe_run_task(self._show_detail_async, data),
            fetch_schedule_week=self._fetch_schedule_week,
            schedule_cache=self._week_schedule_cache,
            on_open_url=self._open_url_in_app,
            on_open_external=self._open_url_external,
        )
        supports_self_update = self._get_distribution_profile().supports_github_self_update
        self.settings_view = SettingsView(
            self.page,
            self.orchestrator,
            on_close=lambda: self._safe_run_task(self._close_settings),
            on_saved=self._on_settings_saved,
            on_test_tray=self._on_test_tray,
            on_test_mobile=self._on_test_mobile,
            on_test_tele=self._on_test_tele,
            on_test_discord=self._on_test_discord,
            on_test_mail=self._on_test_mail,
            on_theme_preview=self._rebuild_colors,
            on_check_update=self._check_updates_now if supports_self_update else None,
            on_update_action=(
                self._request_update_download if supports_self_update else None
            ),
            on_cancel_update=(
                self._cancel_update_download if supports_self_update else None
            ),
            distribution_profile=self._get_distribution_profile(),
            on_open_store_url=self._open_store_url,
        )
        # Seam contracts with SettingsView (`ui-webview-docs`): explicit logout
        # and Settings -> Tài khoản login route through unified AppController isolation.
        self.settings_view.on_logout = self._handle_logout_async
        self.settings_view.on_account_login = self._handle_settings_account_login_async

        self.grade_overview_view = GradeOverviewView(
            on_close=lambda: self._safe_run_task(self._close_grades),
            on_refresh=lambda: self._safe_run_task(lambda: self._show_grades(force_refresh=True)),
            page=self.page,
            moodle_courses_provider=self._get_moodle_course_grades,
            on_open_courses=lambda: self._safe_run_task(self._show_course_grades),
            course_grades_lookup=self._lookup_moodle_course_grades_cached,
            course_grades_async_lookup=self._fetch_moodle_course_grades_async,
        )
        self.course_grades_view = CoursesView(
            on_close=lambda: self._safe_run_task(self._close_course_grades),
            on_refresh=lambda: self._safe_run_task(lambda: self._show_course_grades(force_refresh=True)),
        )

        from core.courses.activity_service import CourseActivityService
        from core.courses.archive_service import ArchiveService
        from core.courses.cache_store import CourseCacheStore
        from core.courses.catalog_service import CourseCatalogService
        from core.courses.local_preview_server import LocalPreviewServer
        from core.courses.preview_cache import PreviewFileCache
        from core.courses.providers import CoursesWsProvider, ThnnCourseProvider
        from gui.controllers.courses_coordinator import CoursesCoordinator

        courses_namespace = (
            f"{settings.MOODLE_BASE_URL}|{settings.UTH_USERNAME or 'anon'}"
        )
        self._course_cache_store = CourseCacheStore(namespace=courses_namespace)
        self._preview_cache = PreviewFileCache()
        self._courses_ws_provider = CoursesWsProvider(
            service=self.orchestrator.moodle_service,
            client=self.orchestrator.client,
            origin=settings.MOODLE_BASE_URL,
            userid_resolver=getattr(self.orchestrator, "_get_userid", None),
        )
        self._thnn_course_provider = ThnnCourseProvider(
            client=self.orchestrator.thnn_client,
        )
        def _fetch_portal_current_courses() -> tuple[str, list[dict[str, object]]]:
            u_name = settings.UTH_USERNAME
            u_pass = settings.UTH_PASSWORD
            if not u_name or not u_pass:
                return ("", [])
            return self._portal_client.fetch_current_semester_courses(u_name, u_pass)

        self._course_catalog_service = CourseCatalogService(
            cache_store=self._course_cache_store,
            preview_cache=self._preview_cache,
            courses_provider=self._courses_ws_provider,
            thnn_provider=self._thnn_course_provider,
            portal_courses_fetcher=_fetch_portal_current_courses,
        )
        self._archive_service = ArchiveService()
        self._preview_server = LocalPreviewServer()
        self._course_activity_service = CourseActivityService(
            moodle_service=self.orchestrator.moodle_service,
            courses_client=self.orchestrator.client,
            thnn_client=self.orchestrator.thnn_client,
        )
        self._courses_coordinator = CoursesCoordinator(
            page=self.page,
            catalog_service=self._course_catalog_service,
            preview_cache=self._preview_cache,
            archive_service=self._archive_service,
            preview_server=self._preview_server,
            on_open_assignment_fallback=self._open_course_assignment,
            on_launch_url=self._launch_safe_url,
            activity_service=self._course_activity_service,
            courses_client=self.orchestrator.client,
            thnn_client=self.orchestrator.thnn_client,
            assignment_detail_fetcher=self.orchestrator.fetch_full_details,
            on_assignment_status_changed=self._on_activity_status_changed,
            submission_workflow_factory=self._submission_workflow_factory,
            all_assignments_provider=self._snapshot_all_data,
            on_start_quiz=self._start_course_quiz,
            account_namespace=courses_namespace,
            webview_auth_service=self._webview_auth_service,
            account_epoch_getter=lambda: getattr(self, "_account_epoch", 0),
        )
        if sys.platform == "win32" and not bool(getattr(self.page, "web", False)):
            from gui.components.courses.embedded_webview_host import (
                clear_persisted_webview2_profiles,
                webview2_account_profile_name,
            )

            keep_startup_profile = (
                webview2_account_profile_name(courses_namespace)
                if str(getattr(settings, "UTH_USERNAME", "") or "").strip()
                else None
            )
            threading.Thread(
                target=clear_persisted_webview2_profiles,
                kwargs={"keep_profile_name": keep_startup_profile},
                name="WebView2ProfileSweep",
                daemon=True,
            ).start()
        self.courses_view = self._courses_coordinator.courses_view
        self.courses_view.visible = False
        if hasattr(self.courses_view, "set_on_immersive_changed"):
            self.courses_view.set_on_immersive_changed(self._on_courses_immersive_changed)

        self.dashboard.animate_opacity = None
        self.dashboard.animate_offset = None
        for _view in (
            self.detail_view,
            self.settings_view,
            self.calendar_view,
            self.grade_overview_view,
            self.courses_view,
            self.course_grades_view,
        ):
            _view.offset = ft.Offset(0, 0)
            _view.opacity = 0.0
            _view.animate_opacity = None
            _view.animate_offset = None

        switcher_ms = 90 if platform_utils.UI_IS_MOBILE else 120
        self.tab_switcher = ft.AnimatedSwitcher(
            content=self.dashboard,
            transition=ft.AnimatedSwitcherTransition.FADE,
            duration=switcher_ms,
            reverse_duration=switcher_ms,
            switch_in_curve=ft.AnimationCurve.EASE_OUT,
            switch_out_curve=ft.AnimationCurve.EASE_IN,
            expand=True,
        )
        self._empty_detail = ft.Container(expand=True, ignore_interactions=True)
        self.detail_switcher = ft.AnimatedSwitcher(
            content=self._empty_detail,
            transition=ft.AnimatedSwitcherTransition.FADE,
            duration=160,
            reverse_duration=160,
            switch_in_curve=ft.AnimationCurve.EASE_OUT,
            switch_out_curve=ft.AnimationCurve.EASE_IN,
            expand=True,
        )

        # Ở chế độ rộng, chi tiết bài tập là panel bên phải (list vẫn thấy được).
        self._detail_host = ft.Container(
            content=self.detail_switcher,
            left=0, right=0, top=0, bottom=0,
        )
        # Chưa mở chi tiết thì lớp này không được chặn thao tác của màn bên dưới.
        self._detail_host.ignore_interactions = True
        main_stack_controls = [self.tab_switcher, self._detail_host]
        overlays = []
        if not platform_utils.UI_IS_MOBILE:
            self._active_desktop_filter_menu = None
            self._desktop_filter_top_dismiss_layer = ft.Container(
                visible=False,
                left=0,
                right=180,
                top=0,
                height=0,
                bgcolor=ft.Colors.with_opacity(0.001, C.BG),
                on_click=lambda _event: self._dismiss_desktop_filter_menu(),
            )
            self._desktop_filter_upper_body_dismiss_layer = ft.Container(
                visible=False,
                left=0,
                right=0,
                top=58,
                height=0,
                bgcolor=ft.Colors.with_opacity(0.001, C.BG),
                on_click=lambda _event: self._dismiss_desktop_filter_menu(),
            )
            self._desktop_filter_dismiss_layer = ft.Container(
                visible=False,
                left=0,
                right=0,
                top=0,
                bottom=0,
                bgcolor=ft.Colors.with_opacity(0.001, C.BG),
                on_click=lambda _event: self._dismiss_desktop_filter_menu(),
            )
            self._desktop_filter_popup_host = ft.Container(
                visible=True,
                opacity=0,
                scale=0.96,
                offset=ft.Offset(0, -0.025),
                animate_opacity=ft.Animation(
                    180, ft.AnimationCurve.EASE_OUT_CUBIC
                ),
                animate_scale=ft.Animation(
                    180, ft.AnimationCurve.EASE_OUT_CUBIC
                ),
                animate_offset=ft.Animation(
                    180, ft.AnimationCurve.EASE_OUT_CUBIC
                ),
                ignore_interactions=True,
                left=0,
                top=0,
            )
            overlays = [
                self._desktop_filter_top_dismiss_layer,
                self._desktop_filter_upper_body_dismiss_layer,
                self._desktop_filter_dismiss_layer,
                self._desktop_filter_popup_host,
            ]
            main_stack_controls.extend(overlays)
        overlays.append(self._app_webview_host)
        main_stack_controls.append(self._app_webview_host)
        overlays.append(self.quiz_player_view)
        main_stack_controls.append(self.quiz_player_view)
        main_stack = ft.Stack(
            controls=main_stack_controls,
            expand=True,
            clip_behavior=ft.ClipBehavior.NONE,
        )
        self.view_manager = ViewManager(
            self.page,
            self.dashboard,
            self.detail_view,
            self.settings_view,
            self.calendar_view,
            self.grade_overview_view,
            self,
            courses_view=self.courses_view,
            course_grades_view=self.course_grades_view,
            view_host=main_stack,
            overlays=overlays,
            tab_switcher=self.tab_switcher,
            detail_switcher=self.detail_switcher,
            empty_detail=self._empty_detail,
            on_destination_change=self._nav_shell.set_active,
        )
        body_with_rail = ft.Row(
            controls=[self._nav_shell.rail_slot, main_stack],
            spacing=0,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        shell_body = ft.Column(
            controls=[body_with_rail, self._nav_shell.bar],
            spacing=0,
            expand=True,
        )
        self._apply_responsive_layout(force=True)
        self._rendered_palette = semantic_palette()
        # Replace the startup splash atomically after the real UI is complete.
        self.page.controls.clear()
        if platform_utils.UI_IS_MOBILE:
            self._app_shell = ft.SafeArea(content=shell_body, expand=True)
        else:
            self._window_header = self._build_desktop_window_header()
            self._app_shell = ft.Column(
                controls=[self._window_header, shell_body],
                spacing=0,
                expand=True,
            )
        self.page.add(self._app_shell)
        self.page.on_resize = self._on_page_resize
        self._apply_responsive_layout(force=True)

    def _on_courses_immersive_changed(self, immersive: bool) -> None:
        nav_shell = getattr(self, "_nav_shell", None)
        if nav_shell is None or not hasattr(nav_shell, "set_immersive"):
            return
        nav_shell.set_immersive(bool(immersive))
        bar = getattr(nav_shell, "bar", None)
        if bar is not None and getattr(bar, "page", None) is not None:
            try:
                bar.update()
                return
            except Exception:
                pass
        page = getattr(self, "page", None)
        if page is not None and callable(getattr(page, "update", None)):
            try:
                page.update()
            except Exception:
                pass

    def _current_page_width(self, width: float | None = None) -> float:
        candidates = (
            width,
            getattr(self.page, "width", None),
            getattr(getattr(self.page, "window", None), "width", None),
            420.0 if platform_utils.UI_IS_MOBILE else 1120.0,
        )
        for candidate in candidates:
            if candidate is None or isinstance(candidate, bool):
                continue
            try:
                numeric = float(candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(numeric) and numeric > 0:
                return numeric
        return 420.0 if platform_utils.UI_IS_MOBILE else 1120.0

    def _current_page_height(self, height: float | None = None) -> float:
        candidates = (
            height,
            getattr(self.page, "height", None),
            getattr(getattr(self.page, "window", None), "height", None),
            760.0,
        )
        for candidate in candidates:
            if candidate is None or isinstance(candidate, bool):
                continue
            try:
                numeric = float(candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(numeric) and numeric > 0:
                return numeric
        return 760.0

    def _apply_responsive_layout(
        self,
        width: float | None = None,
        height: float | None = None,
        force: bool = False,
    ) -> None:
        resolved_width = self._current_page_width(width)
        resolved_height = self._current_page_height(height)
        nav_shell = getattr(self, "_nav_shell", None)
        use_rail = False
        if nav_shell is not None:
            use_rail = nav_shell.apply_width(resolved_width)
            if hasattr(nav_shell, "apply_height"):
                nav_shell.apply_height(resolved_height)
        content_width = max(0.0, resolved_width - (RAIL_WIDTH if use_rail else 0.0))
        courses_view = getattr(self, "courses_view", None)
        if courses_view is not None:
            if callable(getattr(courses_view, "set_viewport_size", None)):
                courses_view.set_viewport_size(content_width, resolved_height)
            else:
                if hasattr(courses_view, "set_width"):
                    courses_view.set_width(content_width)
                if hasattr(courses_view, "set_viewport_height"):
                    courses_view.set_viewport_height(resolved_height)
        grade_view = getattr(self, "grade_overview_view", None)
        if grade_view is not None and callable(getattr(grade_view, "set_width", None)):
            grade_view.set_width(content_width)
        self._sync_app_webview_bounds(resolved_width, resolved_height)

        content_area = getattr(self, "_content_area", None)
        if content_area is None or not hasattr(self, "_wide_layout"):
            return

        wide = (not platform_utils.UI_IS_MOBILE) and content_width >= _WIDE_BREAKPOINT
        mode_changed = wide != self._wide_layout
        if not (
            force
            or mode_changed
            or getattr(self, "_last_layout_width", None) is None
            or abs(content_width - self._last_layout_width) >= 1
        ):
            return
        self._last_layout_width = content_width
        self._wide_layout = wide

        list_width = content_width - (_SIDE_PANE_WIDTH if wide else 0)
        gutter = max(0.0, (list_width - _LIST_MAX_WIDTH) / 2)
        base_pad = 12.0 if platform_utils.UI_IS_MOBILE else 16.0
        card_container_pad = max(0.0, base_pad - 10.0) + gutter
        content_area.padding = ft.Padding.only(
            left=card_container_pad, right=card_container_pad, bottom=10
        )
        self._filter_container.padding = ft.Padding.only(
            left=base_pad + gutter, right=base_pad + gutter, top=2, bottom=10
        )
        update_banner = getattr(self, "_update_banner", None)
        if update_banner is not None:
            update_banner.margin = ft.Margin(
                left=base_pad + gutter, right=base_pad + gutter, top=0, bottom=8
            )

        single_gutter = max(0.0, (content_width - _SINGLE_VIEW_MAX_WIDTH) / 2)
        self.settings_view.margin = ft.Margin(left=single_gutter, right=single_gutter, top=0, bottom=0)
        zero_margin = ft.Margin(left=0, right=0, top=0, bottom=0)
        for view in (
            getattr(self, "grade_overview_view", None),
            getattr(self, "calendar_view", None),
            courses_view,
            getattr(self, "course_grades_view", None),
        ):
            if view is not None and getattr(view, "margin", None) != zero_margin:
                view.margin = zero_margin

        host = getattr(self, "_detail_host", None)
        if host is not None:
            if wide:
                host.left = None
                host.width = _SIDE_PANE_WIDTH
                self.detail_view.border = ft.Border.only(left=ft.BorderSide(1, C.BORDER))
            else:
                host.left = 0
                host.width = None
                self.detail_view.border = None

        header_top = 16 if wide else (14 if platform_utils.UI_IS_MOBILE else 12)
        self._header_container.padding = ft.Padding(
            left=base_pad + gutter, right=base_pad + gutter, top=header_top, bottom=8
        )
        dashboard_title = getattr(self, "_dashboard_title", None)
        if dashboard_title is not None:
            dashboard_title.size = 22 if wide else 20

        if mode_changed or force:
            self._sidebar.visible = wide
            self._footer.visible = False
            if wide:
                self._sidebar_column.controls = [self._sidebar_title, self.today_schedule_panel]
            else:
                self._sidebar_column.controls = []
            cards = [c for c in self.cards_column.controls if c is not self.today_schedule_panel]
            self.cards_column.controls = self._with_inline_schedule(cards)
            with self._cards_lock:
                self.active_cards = list(self.cards_column.controls)

    def _on_page_resize(self, e=None) -> None:
        width = getattr(e, "width", None) if e is not None else None
        height = getattr(e, "height", None) if e is not None else None
        try:
            self._apply_responsive_layout(width, height=height)
            self.page.update()
        except Exception:
            logger.debug("Responsive layout update skipped", exc_info=True)

    def _show_demo_lms_guard_snackbar(self) -> None:
        self._show_snackbar(
            DEMO_LMS_WEBVIEW_GUARD_MESSAGE,
            ft.Icons.INFO_OUTLINE_ROUNDED,
            C.WARNING,
        )

    def _launch_safe_url(self, url: str) -> None:
        safe = safe_http_url(url)
        if not safe:
            return
        if is_demo_account(settings.UTH_USERNAME) and is_uth_lms_url(safe):
            self._show_demo_lms_guard_snackbar()
            return

        async def _open() -> None:
            try:
                await ft.UrlLauncher().launch_url(safe)
            except Exception:
                launcher = getattr(self.page, "launch_url", None)
                if callable(launcher):
                    res = launcher(safe)
                    if inspect.isawaitable(res):
                        await res

        self._safe_run_task(_open)

    def _snapshot_all_data(self) -> list[dict]:
        """Thread-safe shallow copy of all_data dicts for Pane-3 assignment lookup."""
        with self._data_lock:
            return [dict(item) for item in (self.all_data or [])]

    def _open_course_assignment(self, mod: "CourseModuleItem") -> None:
        courses_view = getattr(self, "courses_view", None)
        selected_course = (
            courses_view._state.selected_course()
            if courses_view is not None and hasattr(courses_view, "_state")
            else None
        )
        url = safe_http_url(mod.web_url) or safe_http_url(mod.url)
        if not url:
            return
        if _classify_uth_url(url) in {"courses", "thnn"}:
            url = _normalize_uth_target_url(url, has_credentials=False)
        activity_data = None
        with self._data_lock:
            for item in self.all_data:
                if item.get("url") == url or (
                    mod.cmid
                    and item.get("cmid") == mod.cmid
                    and (
                        selected_course is None
                        or item.get("course_id") == selected_course.id
                    )
                ):
                    activity_data = dict(item)
                    break
        if activity_data is None:
            activity_data = {
                "title": mod.name or "Bài tập",
                "course": (
                    (selected_course.display_name or selected_course.fullname)
                    if selected_course is not None
                    else ""
                ),
                "course_id": selected_course.id if selected_course is not None else None,
                "cmid": mod.cmid,
                "type": "assignment" if mod.modname == "assign" else mod.modname,
                "url": url,
                "site_identity": (
                    selected_course.source if selected_course is not None else "courses"
                ),
                "submission_status": "unknown",
                "details": {"description_html": mod.description_html or ""},
            }
        self._show_detail(activity_data)

    async def _navigate_destination(self, key: str) -> None:
        player_view = getattr(self, "quiz_player_view", None)
        if player_view is not None and getattr(player_view, "visible", False):
            self._sync_rail_active()
            try:
                self.page.update()
            except Exception:
                pass
            player_view.request_close(
                after_close=lambda k=key: self._safe_run_task(
                    self._navigate_destination, k
                )
            )
            return
        self._close_app_webview(update_page=False)
        settings_view = getattr(self, "settings_view", None)
        if settings_view is not None and getattr(settings_view, "visible", False):
            if key == "settings":
                self._sync_rail_active()
                try:
                    self.page.update()
                except Exception:
                    pass
                return
            has_changes_fn = getattr(settings_view, "has_changes", None)
            has_unsaved = bool(has_changes_fn()) if callable(has_changes_fn) else False
            if has_unsaved:
                self._sync_rail_active()
                try:
                    self.page.update()
                except Exception:
                    pass
                request_close = getattr(settings_view, "request_close", None)
                if callable(request_close):
                    await request_close(
                        on_closed=lambda target=key: self._safe_run_task(
                            self._leave_settings_and_navigate, target
                        )
                    )
                else:
                    await settings_view._handle_back(None)
                return
            await self._leave_settings_and_navigate(key)
            return
        await self._perform_destination_navigation(key)

    async def _perform_destination_navigation(self, key: str) -> None:
        if key == "home":
            self.view_manager.show_dashboard()
            if hasattr(self, "_diag_screen"):
                self._diag_screen("home")
                self._diag_breadcrumb("navigation", "succeeded", "user", screen="home")
        elif key == "courses":
            self.view_manager.show_courses()
            if hasattr(self, "_diag_screen"):
                self._diag_screen("courses")
                self._diag_breadcrumb("navigation", "succeeded", "user", screen="courses")
            coord = getattr(self, "_courses_coordinator", None)
            if coord is not None:
                await coord.ensure_loaded_async(
                    background_sync_hours=getattr(
                        settings, "COURSES_SYNC_INTERVAL_HOURS", 6
                    )
                )
        elif key == "calendar":
            await self._show_calendar()
        elif key == "grades":
            await self._show_grades()
        elif key == "settings":
            await self._show_settings()

    def _build_desktop_window_header(self) -> ft.Container:
        self._app_title.size = 13
        self._app_title.weight = ft.FontWeight.W_700
        return build_desktop_window_header(
            on_minimize=self._minimize_window,
            on_maximize=self._toggle_maximize_window,
            on_close=lambda _: self._safe_run_task(
                self._exit_from_window_header
            ),
            title_control=self._app_title,
            version_control=self._version_badge,
        )

    def _compute_app_webview_bounds(
        self,
        width: float | None = None,
        height: float | None = None,
    ) -> tuple[float, float, float, float, float]:
        raw_page_w = self._current_page_width(width)
        page_h = self._current_page_height(height)
        is_web = bool(getattr(self.page, "web", False))
        nav_shell = getattr(self, "_nav_shell", None)
        has_rail = (
            bool(getattr(nav_shell, "rail_visible", False))
            if nav_shell is not None
            else raw_page_w >= float(RAIL_BREAKPOINT)
        )
        rail_w = float(RAIL_WIDTH + 1) if has_rail else 0.0
        left = rail_w
        top = 0.0 if (is_web or platform_utils.UI_IS_MOBILE) else 36.0
        bottom_inset = 0.0 if has_rail else 64.0
        pane_w = max(240.0, raw_page_w - rail_w)
        pane_h = max(200.0, page_h - top - bottom_inset)
        return (left, top, pane_w, pane_h, bottom_inset)

    def _sync_app_webview_bounds(
        self,
        width: float | None = None,
        height: float | None = None,
    ) -> None:
        pane = getattr(self, "_app_webview_pane", None)
        host = getattr(self, "_app_webview_host", None)
        if pane is None or host is None or not getattr(host, "visible", False):
            return
        sync_fn = getattr(pane, "sync_bounds", None)
        if callable(sync_fn):
            left, top, pane_w, pane_h, bottom_inset = self._compute_app_webview_bounds(
                width, height
            )
            sync_fn(left, top, pane_w, pane_h, nav_bottom_inset=bottom_inset)

    def _schedule_webview2_profile_purge(
        self, keep_profile_name: str | None = None
    ) -> None:
        from gui.components.courses.embedded_webview_host import (
            clear_persisted_webview2_profiles,
        )

        threading.Thread(
            target=clear_persisted_webview2_profiles,
            kwargs={"keep_profile_name": keep_profile_name},
            name="WebView2ProfilePurge",
            daemon=True,
        ).start()

    def _close_app_webview(
        self, *, update_page: bool = True, clear_browser_storage: bool = False
    ) -> bool:
        host = getattr(self, "_app_webview_host", None)
        pane = getattr(self, "_app_webview_pane", None)
        was_visible = bool(host is not None and getattr(host, "visible", False))
        self._app_webview_pane = None
        if pane is not None:
            try:
                if clear_browser_storage:
                    try:
                        pane.dispose(clear_browser_storage=True)
                    except TypeError:
                        pane.dispose()
                else:
                    pane.dispose()
            except Exception:
                logger.debug("App WebView dispose skipped", exc_info=True)
        if host is not None:
            host.visible = False
            host.content = None
        if (was_visible or pane is not None) and update_page:
            try:
                self.page.update()
            except Exception:
                pass
        return was_visible or pane is not None

    def _build_webview_auth_service(
        self,
        *,
        courses_client=None,
        thnn_client=None,
        portal_client=None,
    ):
        svc_cls = _resolve_webview_auth_cls()
        if svc_cls is None:
            return None
        kwargs = {
            "courses_client": courses_client,
            "thnn_client": thnn_client,
            "portal_client": portal_client,
            "credentials_provider": self._committed_webview_credentials,
        }
        try:
            params = inspect.signature(svc_cls).parameters
            if "account_epoch_getter" in params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            ):
                kwargs["account_epoch_getter"] = lambda: getattr(
                    self, "_account_epoch", 0
                )
        except (TypeError, ValueError):
            pass
        return svc_cls(**kwargs)

    def _get_webview_auth_service(self):
        svc = getattr(self, "_webview_auth_service", None)
        if svc is not None:
            return svc
        coord = getattr(self, "_courses_coordinator", None)
        orch = getattr(self, "orchestrator", None)
        courses_client = getattr(orch, "client", None)
        thnn_client = getattr(orch, "thnn_client", None)
        if coord is not None:
            client_for = getattr(coord, "_client_for", None)
            if callable(client_for):
                if courses_client is None:
                    courses_client = client_for("courses")
                if thnn_client is None:
                    thnn_client = client_for("thnn")
        portal_client = getattr(self, "_portal_client", None)
        svc = self._build_webview_auth_service(
            courses_client=courses_client,
            thnn_client=thnn_client,
            portal_client=portal_client,
        )
        self._webview_auth_service = svc
        return svc

    def _open_url_in_app(self, url: str, title: str = "") -> None:
        from core.url_validation import validate_public_https_url
        from gui.components.courses.embedded_webview_host import (
            _is_flet_webview_platform,
            _is_inappwebview_mobile_platform,
            _is_windows_webview2_platform,
            _webview2_runtime_available,
        )
        from gui.components.courses.webview_pane import CoursesWebViewPane
        from gui.components.in_app_webview import InAppWebViewDialog

        validated = validate_public_https_url(url, resolve_dns=False)
        if validated is None:
            logger.warning("Blocked invalid or non-HTTPS URL")
            return
        if is_demo_account(settings.UTH_USERNAME) and is_uth_lms_url(validated):
            self._show_demo_lms_guard_snackbar()
            return

        site_kind = _classify_uth_url(validated)
        committed_user, committed_pass = self._committed_webview_credentials()
        has_creds = bool(
            not is_demo_account(committed_user)
            and committed_user.strip()
            and committed_pass.strip()
        )
        if site_kind in {"courses", "thnn", "portal"}:
            validated = _normalize_uth_target_url(
                validated, has_credentials=has_creds
            )

        host = getattr(self, "_app_webview_host", None)
        supports_embedded = host is not None and (
            _is_inappwebview_mobile_platform(self.page)
            or _is_flet_webview_platform(self.page)
            or (
                _is_windows_webview2_platform(self.page)
                and _webview2_runtime_available()
            )
        )
        if supports_embedded:
            self._close_app_webview(update_page=False)
            pop_dialog = getattr(self.page, "pop_dialog", None)
            if callable(pop_dialog):
                try:
                    pop_dialog()
                except Exception:
                    pass

            source = "thnn" if site_kind == "thnn" else "courses"
            preview_server = None
            session_client = None
            coord = getattr(self, "_courses_coordinator", None)
            if coord is not None:
                ensure_srv = getattr(coord, "ensure_preview_server", None)
                if callable(ensure_srv):
                    preview_server = ensure_srv()
                else:
                    preview_server = getattr(coord, "preview_server", None)
            if preview_server is None:
                preview_server = getattr(self, "_preview_server", None)
            if site_kind in {"courses", "thnn"}:
                if coord is not None:
                    client_for = getattr(coord, "_client_for", None)
                    if callable(client_for):
                        session_client = client_for(source)
                if session_client is None:
                    orch = getattr(self, "orchestrator", None)
                    if site_kind == "thnn":
                        session_client = getattr(orch, "thnn_client", None)
                    else:
                        session_client = getattr(orch, "client", None)

            webview_auth_service = self._get_webview_auth_service()
            left, top, pane_w, pane_h, bottom_inset = self._compute_app_webview_bounds()
            account_ns = (
                f"{settings.MOODLE_BASE_URL}|{settings.UTH_USERNAME or 'anon'}"
            )
            pane_kwargs = {
                "page": self.page,
                "source": source,
                "title": (title or "").strip() or "Trình duyệt trong ứng dụng",
                "url": validated,
                "preview_server": preview_server,
                "session_client": session_client,
                "webview_auth_service": webview_auth_service,
                "on_back": self._close_app_webview,
                "on_open_external": self._open_url_external,
                "show_expand_toggle": False,
                "pane_width": pane_w,
                "account_namespace": account_ns,
            }
            try:
                pane_params = inspect.signature(CoursesWebViewPane).parameters
                if "account_epoch_getter" in pane_params or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in pane_params.values()
                ):
                    pane_kwargs["account_epoch_getter"] = lambda: getattr(
                        self, "_account_epoch", 0
                    )
            except (TypeError, ValueError):
                pass
            pane = CoursesWebViewPane(**pane_kwargs)
            self._app_webview_pane = pane
            host.content = pane
            host.visible = True
            pane.sync_bounds(
                left, top, pane_w, pane_h, nav_bottom_inset=bottom_inset
            )
            try:
                self.page.update()
            except Exception:
                logger.debug("App WebView page update skipped", exc_info=True)
            return

        InAppWebViewDialog(
            self.page,
            url=validated,
            title=title,
            on_open_external=self._open_url_external,
        ).show()

    def _open_url_external(self, url: str) -> None:
        from core.url_validation import validate_https_url

        validated = validate_https_url(url)
        if validated is None:
            logger.warning("Blocked invalid or non-HTTPS external URL")
            return
        if is_demo_account(settings.UTH_USERNAME) and is_uth_lms_url(validated):
            self._show_demo_lms_guard_snackbar()
            return

        async def _launch() -> None:
            try:
                await ft.UrlLauncher().launch_url(validated)
            except Exception:
                logger.debug("External URL launch failed", exc_info=True)

        self._safe_run_task(_launch)

    def _build_dashboard_header(self) -> ft.Container:
        def _wrap_header_btn(btn: ft.Control, fallback_label: str) -> ft.Control:
            label = getattr(btn, "tooltip", None) or fallback_label
            return ft.Semantics(label=label, button=True, content=btn)

        self._sync_status.control.expand = True
        self.status_text.no_wrap = True
        self.status_text.overflow = ft.TextOverflow.ELLIPSIS

        self._dashboard_title = ft.Text(
            "Tổng quan",
            size=20 if platform_utils.UI_IS_MOBILE else 22,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        action_controls: list[ft.Control] = []
        reset_btn = getattr(self, "_reset_filters_btn", None)
        if reset_btn is not None:
            action_controls.append(reset_btn)

        box_size = 44 if platform_utils.UI_IS_MOBILE else 38
        self.refresh_btn.size_constraints = ft.BoxConstraints(
            min_width=box_size,
            max_width=box_size,
            min_height=box_size,
            max_height=box_size,
        )
        self._refresh_action_box = ft.Container(
            content=_wrap_header_btn(self.refresh_btn, "Làm mới"),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
        )
        action_controls.append(self._refresh_action_box)

        content = ft.Row(
            controls=[
                ft.Column(
                    controls=[
                        self._dashboard_title,
                        ft.Row(
                            controls=[self._sync_status.control],
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ],
                    spacing=2,
                    tight=True,
                    expand=True,
                ),
                ft.Row(
                    controls=action_controls,
                    spacing=6,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=12,
        )
        base_pad = 12 if platform_utils.UI_IS_MOBILE else 16
        padding = ft.Padding.only(
            left=base_pad,
            right=base_pad,
            top=14 if platform_utils.UI_IS_MOBILE else 12,
            bottom=8,
        )
        self._header_compact_content = content
        self._header_compact_padding = padding
        return ft.Container(content=content, padding=padding, bgcolor=C.BG)

    def _build_dashboard_filter_row(self) -> ft.Row:
        controls = [
            self.urgency_filter_menu.control,
            self.type_filter_menu.control,
            self.course_filter_menu.control,
        ]
        for control, flex in zip(controls, (4, 4, 5), strict=True):
            control.expand = flex
        return ft.Row(
            controls=controls,
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _toggle_desktop_filter_menu(self, menu, event) -> None:
        """Atomically replace the active popup; pointer hover never calls this."""
        active = getattr(self, "_active_desktop_filter_menu", None)
        if active is menu:
            self._dismiss_desktop_filter_menu(menu)
            return

        opening_from_closed = active is None
        if active is not None:
            active._set_expanded(False)

        nav_shell = getattr(self, "_nav_shell", None)
        rail_offset = (
            float(RAIL_WIDTH)
            if nav_shell is not None and getattr(nav_shell, "rail_visible", False)
            else 0.0
        )
        global_position = getattr(event, "global_position", None)
        local_position = getattr(event, "local_position", None)
        if global_position is not None and local_position is not None:
            anchor_left = float(global_position.x - local_position.x) - rail_offset
            anchor_bottom = float(
                global_position.y - local_position.y + menu.trigger.height
            )
        else:
            # Event coordinates are always present for real TapEvent payloads.
            # This fallback keeps keyboard/test invocation deterministic.
            anchor_left = 8.0
            anchor_bottom = 160.0

        header_height = float(
            getattr(getattr(self, "_window_header", None), "height", 34) or 34
        )
        viewport_width = max(
            0.0, float(getattr(self.page, "width", 420) or 420) - rail_offset
        )
        popup_width = float(menu.popup_width)
        popup_left = min(
            max(8.0, anchor_left),
            max(8.0, viewport_width - popup_width - 8.0),
        )
        popup_top = max(0.0, anchor_bottom - header_height)

        self._active_desktop_filter_menu = menu
        menu._set_expanded(True)
        self._desktop_filter_popup_host.content = menu.popup_panel
        self._desktop_filter_popup_host.left = popup_left
        self._desktop_filter_popup_host.top = popup_top
        self._desktop_filter_popup_host.ignore_interactions = False
        if not opening_from_closed:
            self._desktop_filter_popup_host.opacity = 1
            self._desktop_filter_popup_host.scale = 1
            self._desktop_filter_popup_host.offset = ft.Offset(0, 0)
        # Keep the three triggers uncovered so another explicit press reaches
        # them. Two dismiss regions protect everything above and below the row.
        filter_top = max(0.0, popup_top - float(menu.trigger.height or 40))
        header_region_height = min(filter_top, 58.0)
        self._desktop_filter_top_dismiss_layer.height = header_region_height
        self._desktop_filter_top_dismiss_layer.visible = header_region_height > 0
        upper_body_height = max(0.0, filter_top - 58.0)
        self._desktop_filter_upper_body_dismiss_layer.height = upper_body_height
        self._desktop_filter_upper_body_dismiss_layer.visible = (
            upper_body_height > 0
        )
        self._desktop_filter_dismiss_layer.top = popup_top
        self._desktop_filter_dismiss_layer.visible = True
        self.page.update()
        if opening_from_closed:
            self._safe_run_task(self._animate_desktop_filter_open, menu)

    async def _animate_desktop_filter_open(self, menu) -> None:
        # The old PopupMenuButton painted a closed frame before its 180 ms
        # transition. Preserve that two-frame contract for the custom overlay.
        await asyncio.sleep(0.016)
        if getattr(self, "_active_desktop_filter_menu", None) is not menu:
            return
        host = self._desktop_filter_popup_host
        host.animate_opacity = ft.Animation(180, ft.AnimationCurve.EASE_OUT_CUBIC)
        host.animate_scale = ft.Animation(180, ft.AnimationCurve.EASE_OUT_CUBIC)
        host.animate_offset = ft.Animation(180, ft.AnimationCurve.EASE_OUT_CUBIC)
        host.opacity = 1
        host.scale = 1
        host.offset = ft.Offset(0, 0)
        self.page.update()

    def _dismiss_desktop_filter_menu(self, menu=None) -> None:
        active = getattr(self, "_active_desktop_filter_menu", None)
        if menu is not None and active is not menu:
            return
        if active is not None:
            active._set_expanded(False)
        self._active_desktop_filter_menu = None
        popup_host = getattr(self, "_desktop_filter_popup_host", None)
        top_dismiss_layer = getattr(
            self, "_desktop_filter_top_dismiss_layer", None
        )
        upper_body_dismiss_layer = getattr(
            self, "_desktop_filter_upper_body_dismiss_layer", None
        )
        dismiss_layer = getattr(self, "_desktop_filter_dismiss_layer", None)
        if popup_host is not None:
            popup_host.animate_opacity = ft.Animation(
                140, ft.AnimationCurve.EASE_IN_CUBIC
            )
            popup_host.animate_scale = ft.Animation(
                140, ft.AnimationCurve.EASE_IN_CUBIC
            )
            popup_host.animate_offset = ft.Animation(
                140, ft.AnimationCurve.EASE_IN_CUBIC
            )
            popup_host.opacity = 0
            popup_host.scale = 0.96
            popup_host.offset = ft.Offset(0, -0.025)
            popup_host.ignore_interactions = True
        if top_dismiss_layer is not None:
            top_dismiss_layer.visible = False
        if upper_body_dismiss_layer is not None:
            upper_body_dismiss_layer.visible = False
        if dismiss_layer is not None:
            dismiss_layer.visible = False
        if (
            popup_host is not None
            or top_dismiss_layer is not None
            or upper_body_dismiss_layer is not None
            or dismiss_layer is not None
        ):
            self.page.update()

    def _start_skeleton_shimmer(self):
        self.cards_column.controls = [self._make_skeleton_card() for _ in range(4)]

    def _make_skeleton_card(self):
        """Create a skeleton placeholder card that mimics ActivityCard layout."""
        _shimmer = C.BORDER
        return ft.Container(
            content=ft.Row([
                ft.Container(width=3, bgcolor=_shimmer,
                             border_radius=ft.BorderRadius.only(top_left=10, bottom_left=10)),
                ft.Container(
                    content=ft.Column(controls=[
                        ft.Row(controls=[
                            ft.Container(width=50, height=18, bgcolor=_shimmer, border_radius=4),
                            ft.Container(width=55, height=18, bgcolor=_shimmer, border_radius=4),
                        ], spacing=6),
                        ft.Container(width=120, height=12, bgcolor=_shimmer, border_radius=3),
                        ft.Container(width=260, height=14, bgcolor=_shimmer, border_radius=3),
                        ft.Container(width=180, height=11, bgcolor=_shimmer, border_radius=3),
                        ft.Container(height=4, bgcolor=_shimmer, border_radius=2),
                    ], spacing=8),
                    padding=ft.Padding.only(left=14, right=14, top=12, bottom=12),
                    expand=True,
                ),
            ], spacing=0),
            bgcolor=C.SURFACE,
            border_radius=10,
            border=ft.Border.all(1, C.BORDER),
            margin=ft.Margin(left=10, right=10, top=0, bottom=0),
            # UX-4: Shimmer pulse animation
            opacity=0.4,
            animate_opacity=ft.Animation(800, ft.AnimationCurve.EASE_IN_OUT),
        )

    def _clear_skeletons(self):
        """Remove skeleton cards when real data arrives."""
        if self._skeleton_visible:
            self._skeleton_visible = False
            self.cards_column.controls.clear()

    @staticmethod
    def _urgency_filter_options(counts: dict | None = None):
        counts = counts or {}
        return [
            FilterMenuOption(
                "all", "Tất cả mức độ", C.TEXT_SECONDARY,
                count=counts.get("all", 0), icon=ft.Icons.TUNE_ROUNDED,
                trigger_label="Mức độ",
            ),
            FilterMenuOption(
                "critical", "Cấp bách", C.CRITICAL,
                count=counts.get("critical", 0), icon=ft.Icons.PRIORITY_HIGH_ROUNDED,
            ),
            FilterMenuOption(
                "warning", "Sắp tới", C.WARNING,
                count=counts.get("warning", 0), icon=ft.Icons.SCHEDULE_ROUNDED,
            ),
            FilterMenuOption(
                "safe", "An toàn", C.SAFE,
                count=counts.get("safe", 0), icon=ft.Icons.SHIELD_OUTLINED,
            ),
            FilterMenuOption(
                "overdue", "Quá hạn", C.CRITICAL,
                count=counts.get("overdue", 0), icon=ft.Icons.HISTORY_ROUNDED,
            ),
        ]

    @staticmethod
    def _type_filter_options(counts: dict | None = None):
        from gui.core.theme import _TYPE_COLORS

        counts = counts or {}
        return [
            FilterMenuOption(
                "all", "Tất cả hoạt động", C.TEXT_SECONDARY,
                count=counts.get("all", 0), icon=ft.Icons.CATEGORY_OUTLINED,
                trigger_label="Loại bài",
            ),
            FilterMenuOption(
                "quiz", "Quiz", _TYPE_COLORS["quiz"],
                count=counts.get("quiz", 0), icon=ft.Icons.QUIZ_OUTLINED,
            ),
            FilterMenuOption(
                "assignment", "Bài tập", _TYPE_COLORS["assignment"],
                count=counts.get("assignment", 0), icon=ft.Icons.ASSIGNMENT_OUTLINED,
            ),
            FilterMenuOption(
                "attendance", "Điểm danh", _TYPE_COLORS["attendance"],
                count=counts.get("attendance", 0), icon=ft.Icons.FACT_CHECK_OUTLINED,
            ),
            FilterMenuOption(
                "open", "Sắp mở", _TYPE_COLORS["open"],
                count=counts.get("open", 0), icon=ft.Icons.LOCK_CLOCK_OUTLINED,
            ),
            FilterMenuOption(
                "other", "Sự kiện khác", C.TEXT_SECONDARY,
                count=counts.get("other", 0), icon=ft.Icons.MORE_HORIZ_ROUNDED,
            ),
        ]

    @staticmethod
    def _course_filter_options(counts: dict):
        options = [
            FilterMenuOption(
                "all", "Tất cả môn học", C.TEXT_SECONDARY,
                count=counts.get("all", 0), icon=ft.Icons.SCHOOL_OUTLINED,
                trigger_label="Môn học",
            )
        ]
        options.extend(
            FilterMenuOption(
                course,
                course,
                C.ACCENT,
                count=counts.get(course, 0),
                icon=ft.Icons.BOOK_OUTLINED,
            )
            for course in sorted(key for key in counts if key != "all")
        )
        return options

    @staticmethod
    def _activity_selection_key(data: dict | None) -> tuple | None:
        if not isinstance(data, dict) or not data:
            return None
        url = str(data.get("url") or "").strip()
        if url:
            return ("url", url)
        item_id = data.get("id")
        if item_id not in (None, ""):
            return ("id", str(item_id))
        cmid = data.get("cmid")
        course_id = data.get("course_id")
        if cmid not in (None, ""):
            return ("cmid", str(course_id or ""), str(cmid))
        title = str(data.get("title") or "").strip()
        deadline = str(data.get("deadline") or "").strip()
        if title or deadline:
            return ("fallback", title, deadline)
        return None

    def _is_activity_selected(self, data: dict | None) -> bool:
        active_key = getattr(self, "_selected_activity_key", None)
        if active_key is None:
            return False
        return self._activity_selection_key(data) == active_key

    def _sync_reusable_card_selection(self) -> None:
        for card in getattr(self, "_reusable_cards", ()) or ():
            set_selected = getattr(card, "set_selected", None)
            if callable(set_selected):
                set_selected(self._is_activity_selected(getattr(card, "data", None)))

    def _set_selected_activity(self, data: dict | None) -> None:
        self._selected_activity_key = self._activity_selection_key(data)
        self._sync_reusable_card_selection()

    def _clear_selected_activity(self) -> None:
        if getattr(self, "_selected_activity_key", None) is None:
            return
        self._selected_activity_key = None
        self._sync_reusable_card_selection()

    def _refresh_ui(self, *, update_page: bool = True):
        # Snapshot all_data under lock for thread safety
        with self._data_lock:
            data_snapshot = list(self.all_data)
        # Lọc sơ bộ dữ liệu theo cài đặt chung
        base = self._apply_settings_filter(data_snapshot)
        
        filtered_items, counts = FilterService.filter_and_count(
            base,
            self.active_urgency,
            self.active_type,
            self.active_course,
            self.active_search,
            self.active_urgency == "overdue",
        )
        if (
            self.active_course != "all"
            and self.active_course not in counts["course"]
        ):
            self.active_course = "all"
            self.course_filter_menu.set_selected("all")
            filtered_items, counts = FilterService.filter_and_count(
                base,
                self.active_urgency,
                self.active_type,
                self.active_course,
                self.active_search,
                self.active_urgency == "overdue",
            )

        n_critical = counts["urgency"].get("critical", 0)
        n_warning = counts["urgency"].get("warning", 0)
        n_safe = counts["urgency"].get("safe", 0)
        n_overdue = counts["urgency"].get("overdue", 0)

        course_counts = counts["course"]
        type_counts = counts["type"]

        self.course_filter_menu.set_options(
            self._course_filter_options(course_counts)
        )
        # UX-3: Natural language order - "N label" instead of "Label · N"
        self.footer_critical.value = f"{n_critical} khẩn cấp" if n_critical else "0 khẩn cấp"
        self.footer_critical.color = C.CRITICAL if n_critical else C.BORDER
        self.footer_warning.value  = f"{n_warning} sắp hạn" if n_warning else "0 sắp hạn"
        self.footer_warning.color  = C.WARNING if n_warning else C.BORDER
        self.footer_safe.value     = f"{n_safe} an toàn" if n_safe else "0 an toàn"
        self.footer_safe.color     = C.SAFE if n_safe else C.BORDER
        self.footer_overdue.value  = f"{n_overdue} quá hạn" if n_overdue else "0 quá hạn"
        self.footer_overdue.color  = C.CRITICAL if n_overdue else C.BORDER
        self._sync_filter_controls_state()

        self.urgency_filter_menu.set_options(
            self._urgency_filter_options(counts["urgency"])
        )
        self.type_filter_menu.set_options(
            self._type_filter_options(type_counts)
        )

        # Render cards
        self._clear_skeletons()
        is_empty = len(filtered_items) == 0 and not self._is_loading
        self.empty_state.visible = is_empty
        self.error_state.visible = False
        has_filter = self._has_active_dashboard_filter()
        empty_clear_btn = getattr(self, "_empty_clear_filter_btn", None)
        if empty_clear_btn is not None:
            empty_clear_btn.visible = bool(is_empty and has_filter)

        # P2/P6: Contextual empty state messaging
        if is_empty:
            # Check if all activities are submitted (victory state)
            all_total = counts["urgency"].get("all", 0)
            submitted_statuses = ("submitted", "Đã nộp", "graded", "Đã chấm")
            all_submitted = all_total > 0 and all(
                a.get("submission_status", "") in submitted_statuses for a in base
            ) if not has_filter else False

            if all_submitted:
                # P6: Victory state - positive reinforcement
                self._empty_icon.name = ft.Icons.EMOJI_EVENTS_ROUNDED
                self._empty_icon.color = C.SAFE
                self._empty_title.value = "Tuyệt vời! Đã nộp tất cả"
                self._empty_subtitle.value = "Bạn đã hoàn thành mọi bài tập. Nghỉ ngơi thôi!"
            elif has_filter:
                # Filter active - guide user to adjust
                self._empty_icon.name = ft.Icons.FILTER_ALT_OFF_ROUNDED
                self._empty_icon.color = C.BORDER
                self._empty_title.value = "Không tìm thấy kết quả"
                self._empty_subtitle.value = "Thử bỏ bớt bộ lọc hoặc đổi từ khóa tìm kiếm"
            else:
                # Default
                self._empty_icon.name = ft.Icons.INBOX_ROUNDED
                self._empty_icon.color = C.BORDER
                self._empty_title.value = "Không có hoạt động nào"
                self._empty_subtitle.value = "Nhấn nút làm mới để cập nhật dữ liệu"

        if not hasattr(self, '_reusable_cards'):
            self._reusable_cards = []

        current_cards = self._reusable_cards
        
        # Tận dụng lại mấy cái thẻ cũ (Recycling) để app không bị lag
        for i, item in enumerate(filtered_items):
            if i < len(current_cards):
                # Tái sử dụng thẻ
                current_cards[i].set_selected(self._is_activity_selected(item))
                current_cards[i].update_data(item, on_tap=self._show_detail)
                current_cards[i].visible = True
            else:
                # Tạo thẻ mới
                new_card = ActivityCard(item, on_tap=self._show_detail, animate=False)
                new_card.set_selected(self._is_activity_selected(item))
                new_card.visible = True
                current_cards.append(new_card)
        
        # Ẩn các thẻ thừa
        for i in range(len(filtered_items), len(current_cards)):
            current_cards[i].set_selected(False)
            current_cards[i].visible = False
            
        render_cards = current_cards[:len(filtered_items)]
        self._reusable_cards = current_cards
        render_cards = self._with_inline_schedule(render_cards)
            
        with self._cards_lock:
            self.active_cards = render_cards
        
        self.cards_column.controls = render_cards
        if update_page:
            self.page.update()

    def _fetch_schedule_week(self, week_anchor):
        """Blocking Portal ``lichTuan`` fetch; runs in a worker thread."""
        with self._portal_week_lock:
            return self._portal_week_client.fetch_week(
                week_anchor, settings.UTH_USERNAME, settings.UTH_PASSWORD
            )

    def _request_today_schedule(self) -> None:
        coordinator = self._today_schedule_coordinator
        if coordinator.state.status is ScheduleLoadStatus.ERROR:
            self._safe_run_task(coordinator.refresh, "retry")
        else:
            self._safe_run_task(coordinator.ensure_today)

    def _get_upcoming_schedule_cache(self, username: str) -> UpcomingScheduleCache:
        namespace = f"{settings.PORTAL_API_BASE}|{username}"
        return UpcomingScheduleCache(namespace=namespace)

    async def _fetch_upcoming_sessions(self, credentials=None):
        """Async callable: Portal classes inside the reminder look-ahead window.

        The reconcile task passes the ``(username, password)`` pair captured
        when it was created, so an in-flight task of the previous account keeps
        fetching with its own credentials instead of reading the global
        settings after a switch.

        Days ``1..REMINDER_LOOKAHEAD_DAYS`` after today are fetched one by one
        with the shared Portal token (today's sessions already arrive with the
        dashboard snapshot). The window is all-or-nothing: any failure — a
        dead network or a rejected session — propagates, because the manager
        keeps the previously armed alarms on a fetch error instead of
        replacing them with a partial window (which would cancel the alarms of
        the days that failed to fetch) and clears them on
        ``ScheduleAuthenticationError``.
        """
        from datetime import timedelta

        if credentials is None:
            credentials = (settings.UTH_USERNAME, settings.UTH_PASSWORD)
        username, password = credentials
        if not username or not password or is_demo_account(username):
            return ()
        expected_identity = (username, password)
        has_identity_snapshot = hasattr(self, "_settings_identity")
        identity_snapshot = tuple(
            getattr(self, "_settings_identity", expected_identity)
        )
        if identity_snapshot != expected_identity:
            return ()
        has_account_epoch = hasattr(self, "_account_epoch")
        account_epoch = getattr(self, "_account_epoch", None)

        def account_is_current() -> bool:
            return (
                (not has_identity_snapshot or tuple(
                    getattr(self, "_settings_identity", ())
                ) == identity_snapshot)
                and (
                    not has_account_epoch
                    or getattr(self, "_account_epoch", None) == account_epoch
                )
            )

        if not account_is_current():
            return ()

        cache = self._get_upcoming_schedule_cache(str(username))
        cached = cache.load_fresh()
        if cached is not None and account_is_current():
            return cached.sessions

        today = datetime.now().date()
        upcoming = []
        for offset in range(1, REMINDER_LOOKAHEAD_DAYS + 1):
            if not account_is_current():
                return ()
            day_sessions = await asyncio.to_thread(
                self._portal_client.fetch_day,
                today + timedelta(days=offset),
                username,
                password,
            )
            if not account_is_current():
                return ()
            upcoming.extend(day_sessions)
        sessions = tuple(upcoming)
        if account_is_current():
            cache.save(sessions)
        return sessions

    async def _reconcile_class_schedule_notifications(
        self, sessions, *, clear_only: bool = False
    ) -> None:
        """Delegate the class-schedule reconcile use case to NotificationManager.

        ``clear_only=True`` drops reminders owned by a previous account without
        any network call: used on logout/credentials change where fetching a
        day's timetable would use stale credentials and race the new account's
        schedule. The manager serializes concurrent runs and re-checks the
        account identity after every await.
        """
        manager = getattr(self, "notifier", None)
        reconcile = getattr(
            manager, "reconcile_class_schedule_notifications", None
        )
        if not callable(reconcile):
            return
        effective_clear_only = bool(
            clear_only or is_demo_account(settings.UTH_USERNAME)
        )
        effective_sessions = () if effective_clear_only else sessions
        await reconcile(
            effective_sessions,
            clear_only=effective_clear_only,
            fetch_day=self._fetch_upcoming_sessions,
            identity_getter=lambda: (
                getattr(self, "_settings_identity", None),
                getattr(self, "_account_epoch", None),
            ),
            android_bridge=getattr(self, "_android_background", None),
        )

    def _on_today_schedule_state(self, state: TodayScheduleViewState) -> None:
        self._today_schedule_state = state
        if state.status is ScheduleLoadStatus.READY and state.snapshot is not None:
            # Reconcile from the Portal snapshot already fetched for the dashboard.
            # The platform receives only the scheduled reminder payload, never credentials.
            self._safe_run_task(
                self._reconcile_class_schedule_notifications, state.snapshot.sessions
            )
        elif state.status is ScheduleLoadStatus.AUTH_REQUIRED:
            # Logged out / session expired: drop reminders owned by the previous
            # account so Windows never fires them again. No network call here.
            self._safe_run_task(
                self._reconcile_class_schedule_notifications, (), clear_only=True
            )

        if state.status is ScheduleLoadStatus.LOADING:
            self._diag_breadcrumb("schedule_load", "started", "portal")
        elif state.status is ScheduleLoadStatus.READY:
            self._diag_breadcrumb("schedule_load", "succeeded", "portal")
        elif state.status is ScheduleLoadStatus.AUTH_REQUIRED:
            self._diag_breadcrumb(
                "schedule_load", "permission_denied", "portal"
            )
        elif state.status is ScheduleLoadStatus.ERROR:
            self._diag_breadcrumb("schedule_load", "failed", "portal")

        component = getattr(self, "_today_schedule_component", None)
        if component is None:
            return
        component.set_state(state)
        try:
            self.page.update()
        except Exception:
            logger.debug("Today schedule UI update skipped", exc_info=True)

    def _update_footer(self, *, update_page: bool = True):
        self._refresh_ui(update_page=update_page)

    def _render_cards(self):
        """Full UI refresh - rebuilds filters + cards."""
        self._refresh_ui()

    def _render_cards_only(self):
        """Lightweight render - only re-filter and update card list, skip popup rebuild."""
        with self._data_lock:
            data_snapshot = list(self.all_data)
        base = self._apply_settings_filter(data_snapshot)

        filtered_items, counts = FilterService.filter_and_count(
            base, self.active_urgency, self.active_type,
            self.active_course, self.active_search,
            self.active_urgency == "overdue",
        )

        self._clear_skeletons()
        is_empty = len(filtered_items) == 0 and not self._is_loading
        self.empty_state.visible = is_empty
        self.error_state.visible = False
        has_filter = self._has_active_dashboard_filter()
        self._sync_filter_controls_state()
        empty_clear_btn = getattr(self, "_empty_clear_filter_btn", None)
        if empty_clear_btn is not None:
            empty_clear_btn.visible = bool(is_empty and has_filter)
        if is_empty and has_filter:
            self._empty_icon.name = ft.Icons.FILTER_ALT_OFF_ROUNDED
            self._empty_icon.color = C.BORDER
            self._empty_title.value = "Không tìm thấy kết quả"
            self._empty_subtitle.value = "Thử bỏ bớt bộ lọc hoặc đổi từ khóa tìm kiếm"

        if not hasattr(self, '_reusable_cards'):
            self._reusable_cards = []

        current_cards = self._reusable_cards
        for i, item in enumerate(filtered_items):
            if i < len(current_cards):
                current_cards[i].set_selected(self._is_activity_selected(item))
                current_cards[i].update_data(item, on_tap=self._show_detail)
                current_cards[i].visible = True
            else:
                new_card = ActivityCard(item, on_tap=self._show_detail, animate=False)
                new_card.set_selected(self._is_activity_selected(item))
                new_card.visible = True
                current_cards.append(new_card)

        for i in range(len(filtered_items), len(current_cards)):
            current_cards[i].set_selected(False)
            current_cards[i].visible = False

        render_cards = current_cards[:len(filtered_items)]
        self._reusable_cards = current_cards
        render_cards = self._with_inline_schedule(render_cards)

        with self._cards_lock:
            self.active_cards = render_cards

        self.cards_column.controls = render_cards
        self.page.update()

    async def _set_urgency(self, key: str):
        self.active_urgency = key
        self.urgency_filter_menu.set_selected(key)
        self._refresh_ui()

    async def _set_type(self, key: str):
        self.active_type = key
        self.type_filter_menu.set_selected(key)
        self._refresh_ui()

    async def _set_course(self, course_name: str):
        self.active_course = course_name
        self.course_filter_menu.set_selected(course_name)
        self._refresh_ui()

    def _apply_settings_filter(self, data: list) -> list:
        result = []
        for d in data:
            details     = d.get("details", {})
            status      = details.get("status_data", {})
            # Check both English and Vietnamese keys (WS API uses Vietnamese)
            grading     = status.get("Grading status", "") or status.get("Trạng thái chấm", "") or status.get("Chấm điểm", "")
            submission  = status.get("Submission status", "") or status.get("Trạng thái nộp bài", "") or d.get("submission_status", "")
            submission_state = normalize_submission_status(submission)
            grading_state = normalize_submission_status(grading)
            is_graded = (
                submission_state == "graded" or grading_state == "graded"
            )
            is_submitted = submission_state in ("submitted", "graded")

            if is_graded and not settings.INCLUDE_GRADED: continue
            if is_submitted and not is_graded and not settings.INCLUDE_SUBMITTED: continue
            
            # _refresh_ui exposes overdue activities through the urgency menu.
            result.append(d)
        return result

    def _has_active_dashboard_filter(self) -> bool:
        return bool(
            getattr(self, "active_urgency", "all") != "all"
            or getattr(self, "active_type", "all") != "all"
            or getattr(self, "active_course", "all") != "all"
            or getattr(self, "active_search", "")
        )

    def _sync_filter_controls_state(self) -> None:
        reset_btn = getattr(self, "_reset_filters_btn", None)
        if reset_btn is not None:
            reset_btn.visible = self._has_active_dashboard_filter()
        clear_btn = getattr(self, "_search_clear_btn", None)
        if clear_btn is not None:
            clear_btn.visible = bool(getattr(self, "active_search", ""))

    def _clear_search(self, _e=None) -> None:
        if hasattr(self, "_search_task") and self._search_task:
            self._search_task.cancel()
        self.active_search = ""
        if hasattr(self, "search_field") and self.search_field is not None:
            self.search_field.value = ""
        self._sync_filter_controls_state()
        self._refresh_ui()

    async def _reset_all_filters(self) -> None:
        if hasattr(self, "_search_task") and self._search_task:
            self._search_task.cancel()
        self.active_urgency = "all"
        self.active_type = "all"
        self.active_course = "all"
        self.active_search = ""
        if hasattr(self, "search_field") and self.search_field is not None:
            self.search_field.value = ""
        self.urgency_filter_menu.set_selected("all")
        self.type_filter_menu.set_selected("all")
        self.course_filter_menu.set_selected("all")
        self._sync_filter_controls_state()
        self._refresh_ui()

    def _on_search(self, e):
        self.active_search = (e.control.value or "").strip()
        self._sync_filter_controls_state()
        # Debounce: 150ms - fast enough to feel instant, prevents excessive renders
        if hasattr(self, '_search_task') and self._search_task:
            self._search_task.cancel()
        async def _delayed_render():
            await asyncio.sleep(0.15)
            self._render_cards_only()
        self._search_task = self._safe_run_task(_delayed_render)

    def _get_moodle_course_grades(self) -> list:
        """Danh sách môn Moodle kèm báo cáo điểm (đã lưu) để popup Bảng điểm dùng."""
        try:
            return list(
                getattr(self.orchestrator, "latest_course_grades", None)
                or self.orchestrator.course_grade_monitor.load_cached()
                or []
            )
        except Exception:
            return []

    def _lookup_legacy_monitor_grades(self, portal_course) -> "MoodleCourseGradesResult":
        from gui.components.academic_radar.course_components_dialog import (
            MoodleCourseGradesResult,
        )
        from gui.components.course_grade_table import (
            find_moodle_course,
            grade_rows_to_moodle_items,
        )

        try:
            moodle_course = find_moodle_course(portal_course, self._get_moodle_course_grades())
        except Exception:
            moodle_course = None
        report = getattr(moodle_course, "report", None)
        rows = getattr(report, "rows", None) or []
        if not rows:
            return MoodleCourseGradesResult()
        return MoodleCourseGradesResult(
            grades=grade_rows_to_moodle_items(rows),
            course_id=getattr(moodle_course, "course_id", None),
            course_title=str(getattr(moodle_course, "name", "") or ""),
            source="monitor",
            needs_async_fetch=False,
        )

    def _lookup_moodle_course_grades_cached(
        self, portal_course, semester_name: str = ""
    ) -> "MoodleCourseGradesResult":
        """Instant cached lookup from CourseCacheStore, falling back to CourseGradeMonitor."""
        from gui.components.academic_radar.course_components_dialog import (
            MoodleCourseGradesResult,
        )
        from gui.components.course_grade_table import match_portal_course_to_summary

        store = getattr(self, "_course_cache_store", None)
        catalog_service = getattr(self, "_course_catalog_service", None)
        matched = None
        catalog_courses: list = []
        if store is not None:
            try:
                catalog = store.load_catalog()
                catalog_courses = list(getattr(catalog, "courses", None) or [])
                matched = match_portal_course_to_summary(
                    portal_course, catalog_courses, semester_name=semester_name
                )
                if matched is not None:
                    detail = store.load_course_detail(matched.source, matched.id)
                    grades = list(getattr(detail, "grades", None) or [])
                    if grades:
                        return MoodleCourseGradesResult(
                            grades=grades,
                            course_id=matched.id,
                            course_title=matched.display_name or matched.fullname,
                            source=matched.source,
                            last_synced_at=int(getattr(detail, "last_synced_at", 0) or 0),
                            needs_async_fetch=False,
                        )
                    if catalog_service is not None:
                        return MoodleCourseGradesResult(
                            course_id=matched.id,
                            course_title=matched.display_name or matched.fullname,
                            source=matched.source,
                            needs_async_fetch=True,
                        )
            except Exception:
                logger.debug("CourseCacheStore grade lookup failed", exc_info=True)

        legacy_res = self._lookup_legacy_monitor_grades(portal_course)
        if legacy_res.grades:
            return legacy_res

        can_fetch = catalog_service is not None and (
            matched is not None or not catalog_courses
        )
        return MoodleCourseGradesResult(
            course_id=matched.id if matched is not None else None,
            course_title=(
                (matched.display_name or matched.fullname) if matched is not None else ""
            ),
            source=matched.source if matched is not None else "",
            needs_async_fetch=bool(can_fetch),
        )

    async def _fetch_moodle_course_grades_async(
        self, portal_course, semester_name: str = ""
    ) -> "MoodleCourseGradesResult":
        """Background on-demand Moodle grade lookup via CourseCatalogService."""
        from gui.components.academic_radar.course_components_dialog import (
            MoodleCourseGradesResult,
        )
        from gui.components.course_grade_table import match_portal_course_to_summary

        account_epoch = getattr(self, "_account_epoch", 0)

        def _epoch_is_current() -> bool:
            return account_epoch == getattr(self, "_account_epoch", 0)

        def _work() -> MoodleCourseGradesResult:
            if not _epoch_is_current():
                return MoodleCourseGradesResult()
            store = getattr(self, "_course_cache_store", None)
            catalog_service = getattr(self, "_course_catalog_service", None)
            if store is None or catalog_service is None:
                return (
                    self._lookup_legacy_monitor_grades(portal_course)
                    if _epoch_is_current()
                    else MoodleCourseGradesResult()
                )

            courses: list = []
            try:
                courses = list(getattr(store.load_catalog(), "courses", None) or [])
            except Exception:
                courses = []
            if not _epoch_is_current():
                return MoodleCourseGradesResult()
            if not courses:
                try:
                    courses = list(catalog_service.refresh_catalog(include_thnn=True) or [])
                except Exception:
                    logger.debug("On-demand catalog refresh failed", exc_info=True)
                    courses = []
                if not _epoch_is_current():
                    return MoodleCourseGradesResult()

            matched = match_portal_course_to_summary(
                portal_course, courses, semester_name=semester_name
            )
            if not _epoch_is_current():
                return MoodleCourseGradesResult()
            if matched is None:
                return (
                    self._lookup_legacy_monitor_grades(portal_course)
                    if _epoch_is_current()
                    else MoodleCourseGradesResult()
                )

            try:
                grades = list(
                    catalog_service.load_course_grades(matched.source, matched.id, False)
                    or []
                )
            except Exception:
                logger.debug("On-demand load_course_grades failed", exc_info=True)
                grades = []

            if not _epoch_is_current():
                return MoodleCourseGradesResult()

            if grades:
                last_synced = 0
                try:
                    detail = store.load_course_detail(matched.source, matched.id)
                    last_synced = int(getattr(detail, "last_synced_at", 0) or 0)
                except Exception:
                    pass
                if not _epoch_is_current():
                    return MoodleCourseGradesResult()
                return MoodleCourseGradesResult(
                    grades=grades,
                    course_id=matched.id,
                    course_title=matched.display_name or matched.fullname,
                    source=matched.source,
                    last_synced_at=last_synced,
                    needs_async_fetch=False,
                )

            legacy_res = self._lookup_legacy_monitor_grades(portal_course)
            if not _epoch_is_current():
                return MoodleCourseGradesResult()
            if legacy_res.grades:
                return legacy_res
            return MoodleCourseGradesResult(
                course_id=matched.id,
                course_title=matched.display_name or matched.fullname,
                source=matched.source,
                needs_async_fetch=False,
            )

        result = await asyncio.to_thread(_work)
        if not _epoch_is_current():
            return MoodleCourseGradesResult()
        return result

    async def _check_grades_background(self):
        """Chạy cùng chu kỳ cập nhật Tổng quan qua GradeMonitor (WS nhẹ).

        Tránh cào HTML N+1 cho toàn bộ môn học trên mỗi chu kỳ đồng bộ nền
        và tránh phát trùng thông báo khi màn hình điểm môn không đang mở.
        """
        account_epoch = getattr(self, "_account_epoch", 0)
        grade_changes = []
        try:
            grade_changes = list(await asyncio.to_thread(self.orchestrator.check_grade_changes) or [])
        except Exception as e:
            logger.debug("Grade check background failed (non-critical): %s", e)

        if account_epoch != getattr(self, "_account_epoch", 0):
            return

        course_grades_view = getattr(self, "course_grades_view", None)
        if course_grades_view is not None and getattr(course_grades_view, "visible", False):
            try:
                courses, _ = await asyncio.to_thread(self.orchestrator.refresh_course_grades)
                if account_epoch != getattr(self, "_account_epoch", 0):
                    return
                if courses:
                    show_fn = getattr(
                        self.view_manager, "show_course_grades_data", None
                    ) or getattr(self.view_manager, "show_courses_data", None)
                    if callable(show_fn):
                        show_fn(courses)
            except Exception as e:
                logger.debug("Course grades background refresh failed (non-critical): %s", e)

        if account_epoch != getattr(self, "_account_epoch", 0):
            return
        if grade_changes and not is_demo_account(settings.UTH_USERNAME):
            await self._notify_grade_changes(grade_changes)

    async def _notify_grade_changes(self, grade_changes: list) -> None:
        if not grade_changes:
            return
        try:
            if hasattr(self, 'notifier') and self.notifier:
                await self.notifier.dispatch_grade_alert(grade_changes)
            msg = f"\U0001f4ca {len(grade_changes)} thay \u0111\u1ed5i \u0111i\u1ec3m: "
            msg += ", ".join(f"{c.item_name} ({c.old_grade or '-'} \u2192 {c.new_grade})" for c in grade_changes[:3])
            if len(grade_changes) > 3:
                msg += f" +{len(grade_changes) - 3} kh\u00e1c"
            self._show_snackbar(msg, ft.Icons.GRADE_ROUNDED, C.SAFE, duration=5000)
        except Exception as e:
            logger.debug("Grade notification failed (non-critical): %s", e)

    def _set_sync_status(self, state: str, message: str) -> None:
        component = getattr(self, "_sync_status", None)
        if component is not None:
            component.set_state(state, message)
        elif hasattr(self, "status_text"):
            # Keep isolated controller tests and legacy embedders compatible.
            self.status_text.value = message

    async def _prefetch_details_async(
        self, activities: list, prefetch_epoch: int | None = None
    ):
        import time as _time

        if prefetch_epoch is None:
            prefetch_epoch = getattr(self, "_prefetch_epoch", 0)
        account_epoch = getattr(self, "_account_epoch", 0)
        cancel_event = getattr(self, "_prefetch_cancel_event", None)
        data_cache = getattr(self, "_data_cache", None)
        prefetch_start_time = _time.time()
        worker_cap = 2 if platform_utils.IS_MOBILE else 4
        workers = max(1, min(settings.PREFETCH_WORKERS, worker_cap))
        is_cancelled = (
            cancel_event.is_set if cancel_event is not None else (lambda: False)
        )

        if hasattr(self.orchestrator, "prefetch_activity_statuses"):
            cache = await asyncio.to_thread(
                self.orchestrator.prefetch_activity_statuses,
                activities,
                workers,
                is_cancelled,
            )
        else:
            await asyncio.to_thread(
                self.orchestrator.prefetch_all_details,
                activities,
                workers,
                is_cancelled,
                False,
            )
            cache = self.orchestrator.get_cached_details_snapshot()

        if (
            is_cancelled()
            or getattr(self, "_is_loading", False)
            or prefetch_epoch != getattr(self, "_prefetch_epoch", 0)
            or account_epoch != getattr(self, "_account_epoch", 0)
        ):
            return
        with self._data_lock:
            if (
                prefetch_epoch != getattr(self, "_prefetch_epoch", 0)
                or account_epoch != getattr(self, "_account_epoch", 0)
            ):
                return
            pending_updates = getattr(self, "_pending_updates", None)
            data_copy = build_prefetched_dataset(
                list(self.all_data),
                cache,
                pending_updates,
                prefetch_start_time,
            )
            self.all_data = data_copy
        self._update_footer()
        if data_cache is not None and account_epoch == getattr(self, "_account_epoch", 0):
            persisted = await asyncio.to_thread(
                data_cache.save_local_activities, data_copy
            )
            if not persisted:
                logger.warning("Không thể lưu snapshot trạng thái activity đã enrich")
        if account_epoch == getattr(self, "_account_epoch", 0):
            self._schedule_card_detail_warmup()

    async def _warm_card_details_async(
        self,
        activities: list,
        cancel_event: threading.Event,
        *,
        now: datetime | None = None,
    ) -> int:
        """Warm card detail cache near-to-far with a single low-impact worker."""
        candidates = card_detail_prefetch_candidates(activities, now=now)
        if not candidates or cancel_event.is_set():
            return 0
        return await asyncio.to_thread(
            self.orchestrator.prefetch_all_details,
            candidates,
            1,
            cancel_event.is_set,
            False,
        )

    def _has_detail_prefetch_credentials(self) -> bool:
        if settings.MOODLE_WS_TOKEN:
            return True
        if not is_demo_account(settings.UTH_USERNAME):
            return False
        client = getattr(getattr(self, "orchestrator", None), "client", None)
        return bool(getattr(client, "has_site_credentials", False))

    def _schedule_card_detail_warmup(self) -> bool:
        """Schedule one ordered card-cache pass without affecting sync UI."""
        if not self._has_detail_prefetch_credentials():
            return False
        with self._data_lock:
            activities = list(self.all_data)
        if not card_detail_prefetch_candidates(activities):
            return False

        previous_event = getattr(self, "_card_detail_warm_cancel_event", None)
        if previous_event is not None:
            previous_event.set()
        cancel_event = threading.Event()
        self._card_detail_warm_cancel_event = cancel_event
        return self._safe_run_task(
            self._warm_card_details_async,
            activities,
            cancel_event,
        ) is not None

    def _schedule_cached_detail_hydration(self, has_cached_snapshot: bool) -> bool:
        """Hydrate incomplete cached cards immediately without blocking startup."""
        if not has_cached_snapshot or not self._has_detail_prefetch_credentials():
            return False
        with self._data_lock:
            cached_items = list(self.all_data)
        items_to_hydrate = status_hydration_candidates(cached_items)
        if not items_to_hydrate:
            return False
        self._prefetch_cancel_event.clear()
        self._prefetch_epoch = getattr(self, "_prefetch_epoch", 0) + 1
        self._safe_run_task(
            self._prefetch_details_async, items_to_hydrate, self._prefetch_epoch
        )
        return True

    def _refresh_activity_time_state(self) -> bool:
        """Recompute volatile urgency from absolute deadlines and current time."""
        self._time_policy = ActivityTimePolicy(
            settings.URGENCY_CRITICAL_HOURS,
            settings.URGENCY_WARNING_HOURS,
        )
        changed = False
        with self._data_lock:
            refreshed = []
            for item in self.all_data:
                updated = self._time_policy.refresh(item)
                changed = changed or updated.get("urgency") != item.get("urgency")
                refreshed.append(updated)
            if changed:
                refreshed.sort(
                    key=lambda item: (
                        0
                        if item.get("urgency") == "critical"
                        else 1
                        if item.get("urgency") == "warning"
                        else 2,
                        item.get("deadline", ""),
                    )
                )
            self.all_data = refreshed
        return changed

    def _load_cached_snapshot(self) -> bool:
        """Render local activities immediately without starting a Moodle call."""
        snapshot = self._data_cache.load_snapshot()
        self._cache_requires_server_refresh = bool(
            getattr(snapshot, "requires_server_refresh", False)
        )
        self._last_successful_sync_at = parse_timestamp(
            snapshot.last_successful_sync_at
        )
        if not snapshot.activities:
            return False
        with self._data_lock:
            self.all_data = list(snapshot.activities)
        self._refresh_activity_time_state()
        for item in self.all_data:
            deadline = item.get("deadline", "")
            if deadline:
                item["_deadline_dt"] = parse_datetime(deadline)
            item["_title_lower"] = str(item.get("title", "")).lower()
            item["_course_lower"] = str(item.get("course", "")).lower()
        self._set_sync_status(
            "success", f"Dữ liệu đã lưu · {len(self.all_data)} hoạt động"
        )
        self._update_footer()
        self.page.update()
        return True

    def _needs_initial_data_sync(self, has_cached_snapshot: bool) -> bool:
        """Refresh immediately when cached activities predate the current catalog."""
        return not has_cached_snapshot or bool(
            getattr(self, "_cache_requires_server_refresh", False)
        )

    async def _startup_android_sequence(
        self,
        has_cached_snapshot: bool = False,
        needs_initial_sync: bool = True,
    ) -> None:
        """Hydrate mobile secret vault (AndroidKeyStore/iOS Keychain), migrate legacy JSON, and start sync."""
        bridge = getattr(self, "_android_background", None)
        if bridge and bridge.available:
            vault_secrets: dict[str, str] = {}
            vault_read_ok = False
            retry_delays = (0.05, 0.1, 0.2, 0.4, 0.6, 0.65)
            for attempt, delay in enumerate((0.0, *retry_delays)):
                if delay > 0:
                    await asyncio.sleep(delay)
                page_alive = getattr(self, "_page_alive", None)
                if page_alive is not None and not page_alive.is_set():
                    return
                try:
                    vault_secrets = await bridge.read_app_secrets()
                    vault_read_ok = True
                    break
                except Exception as exc:
                    if attempt == len(retry_delays):
                        logger.warning(
                            "Failed to read mobile secret vault: %s",
                            type(exc).__name__,
                        )

            if vault_read_ok:
                backend = AndroidSecretBackend(
                    asyncio.get_running_loop(),
                    bridge,
                    vault_secrets,
                    loop_thread_id=threading.get_ident(),
                )
                register_secret_backend(backend)
                hydrate_secrets(vault_secrets)

                pending = pop_pending_plaintext_migration()
                if pending:
                    merged = {**pending, **vault_secrets}
                    migration_ok = True
                    if merged != vault_secrets:
                        try:
                            migration_ok = bool(await bridge.write_app_secrets(merged))
                        except Exception:
                            migration_ok = False
                    if migration_ok:
                        backend.replace_cached_secrets(merged)
                        hydrate_secrets(merged)
                        await asyncio.to_thread(save_settings)
                    else:
                        logger.warning(
                            "Mobile secret migration did not confirm; preserving legacy JSON"
                        )

            self._settings_identity = (settings.UTH_USERNAME, settings.UTH_PASSWORD)
            if not platform_utils.IS_IOS:
                await self._initialize_android_background()

        if not needs_initial_sync:
            hydration_started = self._schedule_cached_detail_hydration(
                has_cached_snapshot
            )
            if not hydration_started:
                self._schedule_card_detail_warmup()
        self._safe_run_task(self._today_schedule_coordinator.run)
        if not settings.UTH_USERNAME or not settings.UTH_PASSWORD:
            self._safe_run_task(self._show_login_dialog)
        else:
            self._safe_run_task(self._load_data_async, "startup")

    async def _initialize_android_background(self):
        """Attach native worker settings and prefer its cache after app restart."""
        bridge = self._android_background
        if not bridge or not bridge.available:
            return
        try:
            if is_demo_account(settings.UTH_USERNAME):
                await bridge.logout()
                await bridge.configure("", token_origin="")
                return True
            token, token_origin = self._native_background_credentials()
            await bridge.configure(token, token_origin=token_origin)
            native_data = await bridge.cached_activities()
            if native_data:
                diagnostics = await bridge.diagnostics()
                self.orchestrator.android_background_diagnostics = diagnostics
                native_sync = float(diagnostics.get("last_success_at", 0) or 0)
                if not self.all_data or native_sync >= (self._last_successful_sync_at or 0):
                    with self._data_lock:
                        self.all_data = native_data
                    self._refresh_activity_time_state()
                    self._set_sync_status(
                        "success",
                        f"Dữ liệu nền đã lưu · {len(native_data)} hoạt động",
                    )
                    self._update_footer()
                    self.page.update()
        except Exception as exc:
            logger.warning("Cannot initialize Android background sync: %s", exc)
        return True

    async def _load_data_async(self, trigger: str = "manual"):
        """Refresh entry point; concurrent callers share one fetch."""
        coordinator = getattr(self, "_sync_coordinator", None)
        if coordinator is None:
            return await self._perform_data_sync(trigger)
        account_key = getattr(self, "_settings_identity", None)
        try:
            return await coordinator.request(
                trigger,
                force=trigger in ("manual", "startup"),
                account_key=account_key,
            )
        except TypeError:
            return await coordinator.request(
                trigger, force=trigger in ("manual", "startup")
            )

    @staticmethod
    def _has_sync_credentials() -> bool:
        username = str(getattr(settings, "UTH_USERNAME", "") or "").strip()
        if not username:
            return False
        password = str(getattr(settings, "UTH_PASSWORD", "") or "")
        if password:
            return True
        if is_demo_account(username):
            return False
        ws_token = str(getattr(settings, "MOODLE_WS_TOKEN", "") or "").strip()
        if not ws_token:
            return False
        raw_owner = getattr(settings, "MOODLE_WS_TOKEN_USERNAME", "")
        token_owner = raw_owner.strip() if isinstance(raw_owner, str) else ""
        if token_owner and token_owner.casefold() != username.casefold():
            return False
        return True

    async def _perform_data_sync(self, trigger: str = "timer"):
        if self._is_loading:
            return
        if not self._has_sync_credentials():
            self._restore_refresh_button()
            return
        account_epoch = getattr(self, "_account_epoch", 0)
        sync_generation = getattr(self, "_sync_generation", 0)
        data_cache = self._data_cache

        def _epoch_is_current() -> bool:
            return (
                account_epoch == getattr(self, "_account_epoch", 0)
                and sync_generation == getattr(self, "_sync_generation", 0)
            )

        sync_source = "user" if trigger == "manual" else "scheduler"
        self._diag_breadcrumb("refresh", "started", sync_source)
        self._is_loading = True
        self._active_sync_epoch = account_epoch
        self._active_sync_generation = sync_generation
        self._prefetch_epoch = getattr(self, "_prefetch_epoch", 0) + 1
        old_prefetch = getattr(self, "_prefetch_cancel_event", None)
        if old_prefetch is not None:
            old_prefetch.set()
        self._prefetch_cancel_event = threading.Event()
        detail_warm_cancel = getattr(self, "_card_detail_warm_cancel_event", None)
        if detail_warm_cancel is not None:
            detail_warm_cancel.set()

        # A user-forced refresh bypasses caches; timer/startup refreshes reuse
        # valid metadata and avoid restarting the expensive detail pipeline.
        if trigger == "manual":
            try:
                self.orchestrator.moodle_service.clear_all_caches()
                self.orchestrator.clear_detail_cache()
            except Exception:
                import logging as _fb_log
                _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

        self.refresh_btn.disabled = True
        status_text = (
            "Dữ liệu cache · Đang cập nhật..."
            if self.all_data
            else "Đang cập nhật dữ liệu..."
        )
        self._set_sync_status("busy", status_text)
        self.empty_state.visible  = False
        self.error_state.visible  = False
        self.page.update()

        # Hiển thị cache cũ ngay lập tức (nếu có) trong khi chờ server
        if not self.all_data:  # Chỉ load cache khi chưa có data
            cached_data, cached_at = await asyncio.to_thread(data_cache.load)
            if not _epoch_is_current():
                return
            if cached_data:
                with self._data_lock:
                    if not _epoch_is_current():
                        return
                    self.all_data = cached_data
                from core.time_utils import parse_datetime as _parse_dt
                for item in cached_data:
                    dl = item.get("deadline", "")
                    if dl and "_deadline_dt" not in item:
                        item["_deadline_dt"] = _parse_dt(dl)
                    if "_title_lower" not in item:
                        item["_title_lower"] = str(item.get("title", "")).lower()
                    if "_course_lower" not in item:
                        item["_course_lower"] = str(item.get("course", "")).lower()
                self._set_sync_status("busy", "Dữ liệu cache · Đang cập nhật...")
                self._update_footer()
                self.page.update()

        try:
            import time
            fetch_start_time = time.time()

            # A failed or partial result must never replace the last good cache.
            if hasattr(self.orchestrator, "fetch_activity_outcome_async"):
                outcome = await self.orchestrator.fetch_activity_outcome_async()
            elif hasattr(self.orchestrator, "get_latest_activities_async"):
                result = await self.orchestrator.get_latest_activities_async()
                outcome = FetchOutcome.complete(result or [])
            else:
                result = await asyncio.to_thread(self.orchestrator.get_latest_activities)
                outcome = FetchOutcome.complete(result or [])
            if not _epoch_is_current():
                logger.info(
                    "Discarding stale activity sync (%s): account changed mid-flight",
                    trigger,
                )
                return
            if not outcome.can_commit:
                raise RuntimeError(outcome.error or "Incomplete Moodle activity snapshot")
            orch_ref = getattr(self, "orchestrator", None)
            if orch_ref is not None and hasattr(orch_ref, "is_logged_in"):
                orch_ref.is_logged_in = True
            result = list(outcome.activities or [])

            cache = self.orchestrator.get_cached_details_snapshot()
            with self._data_lock:
                if not _epoch_is_current():
                    return
                dataset = build_refresh_dataset(
                    result,
                    cache,
                    self._pending_updates,
                    fetch_start_time,
                )
                self._pending_updates = dataset.pending_updates
                data_copy = dataset.items
                self.all_data = data_copy

            # Publish server truth before slower flash/native/notification work.
            # This is especially important on older Android storage.
            total_count = len(data_copy)
            success_message = (
                f"Cập nhật lúc {datetime.now().strftime('%H:%M')} · "
                f"{total_count} hoạt động"
            )
            should_show_snackbar = not self._skeleton_visible
            self._update_footer()

            # Persist after the first useful frame and keep slow flash I/O off
            # the Flet event loop.
            if not _epoch_is_current():
                return
            snapshot_persisted = await asyncio.to_thread(
                data_cache.save_server_snapshot, data_copy
            )
            if not _epoch_is_current():
                return
            if snapshot_persisted:
                saved_snapshot = await asyncio.to_thread(data_cache.load_snapshot)
                if not _epoch_is_current():
                    return
                self._last_successful_sync_at = (
                    parse_timestamp(saved_snapshot.last_successful_sync_at)
                    or time.time()
                )
                self._set_sync_status("success", success_message)
                if should_show_snackbar:
                    self._show_snackbar(
                        f"Đã cập nhật {total_count} hoạt động",
                        ft.Icons.SYNC_ROUNDED,
                        C.ACCENT,
                    )
            else:
                logger.error("Không thể commit snapshot activity mới")
                self._set_sync_status(
                    "error", "Lỗi lưu cập nhật · dữ liệu mới chưa được lưu"
                )
            # Commit state is user-visible before optional native/notification work.
            self.page.update()
            if not _epoch_is_current():
                return
            demo_mode = is_demo_account(settings.UTH_USERNAME)
            native_schedule_result = None
            if self._android_background and self._android_background.available:
                try:
                    if demo_mode:
                        await self._android_background.logout()
                        if not _epoch_is_current():
                            return
                        await self._android_background.configure(
                            "", token_origin=""
                        )
                    else:
                        token, token_origin = self._native_background_credentials()
                        await self._android_background.configure(
                            token, token_origin=token_origin
                        )
                        if not _epoch_is_current():
                            return
                        native_schedule_result = await self._android_background.import_activities(
                            data_copy,
                            authoritative=True,
                        )
                    if not _epoch_is_current():
                        return
                    self.orchestrator.android_background_diagnostics = (
                        await self._android_background.diagnostics()
                    )
                except Exception as exc:
                    logger.warning("Android native snapshot import failed: %s", exc)
            if not _epoch_is_current():
                return
            # Bắn thông báo thông minh cho người dùng
            if hasattr(self, 'notifier') and self.notifier:
                if demo_mode:
                    try:
                        await self.notifier.reconcile_schedules([])
                    except Exception as e:
                        logger.debug("Demo schedule clear skipped: %s", e)
                else:
                    try:
                        with self._data_lock:
                            dispatch_copy = list(self.all_data)
                        if native_schedule_result is None:
                            dispatch_result = await self.notifier.dispatch(dispatch_copy)
                            if not _epoch_is_current():
                                return
                            schedule_result = await self.notifier.reconcile_schedules(
                                dispatch_copy
                            )
                        else:
                            from core.notification_types import ScheduleResult

                            dispatch_result = await self.notifier.dispatch_with_native_local(
                                dispatch_copy,
                                native_schedule_result,
                            )
                            schedule_result = ScheduleResult(
                                desired=int(native_schedule_result.get("scheduled", 0) or 0),
                                scheduled=int(native_schedule_result.get("scheduled", 0) or 0),
                                cancelled=int(native_schedule_result.get("cancelled", 0) or 0),
                            )
                        logger.info(
                            "[Notifications] delivered=%d filtered=%d scheduled=%d "
                            "cancelled=%d failed=%d",
                            dispatch_result.delivered,
                            dispatch_result.filtered,
                            schedule_result.scheduled,
                            schedule_result.cancelled,
                            schedule_result.failed,
                        )
                    except Exception as e:
                        logger.error(f"[UTHelper] Dispatcher lỗi: {e}")

                    if not _epoch_is_current():
                        return
                    # PERF-OPT: Grade check moved to background task to not block main data display
                    self._safe_run_task(self._check_grades_background)

            if not _epoch_is_current():
                return
            self._prefetch_cancel_event.clear()
            with self._data_lock:
                prefetch_copy = status_hydration_candidates(list(self.all_data))
            if prefetch_copy:
                self._safe_run_task(
                    self._prefetch_details_async,
                    prefetch_copy,
                    self._prefetch_epoch,
                )
            else:
                self._schedule_card_detail_warmup()
            elapsed = time.time() - fetch_start_time
            duration = (
                "lt_250ms" if elapsed < 0.25
                else "250ms_1s" if elapsed < 1
                else "1s_5s" if elapsed < 5
                else "gte_5s"
            )
            self._diag_breadcrumb(
                "refresh", "succeeded", "moodle", duration=duration
            )
            # Moodle's unread-notification feed is intentionally not part of
            # the activity reminder pipeline. Activity data above is the source.
        except Exception as exc:
            if not _epoch_is_current():
                return
            orch_ref = getattr(self, "orchestrator", None)
            if (
                not is_demo_account(settings.UTH_USERNAME)
                and not str(getattr(settings, "MOODLE_WS_TOKEN", "") or "").strip()
                and orch_ref is not None
                and hasattr(orch_ref, "is_logged_in")
            ):
                orch_ref.is_logged_in = False
            self._diag_breadcrumb("refresh", "failed", "moodle")
            logger.exception(f"[Load] Lỗi: {exc}")
            try:
                await asyncio.to_thread(data_cache.record_failed_attempt, str(exc))
            except Exception:
                import logging as _fb_log
                _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)
            if not _epoch_is_current():
                return
            with self._data_lock:
                self.all_data = []

            # Thử dùng cache khi offline
            cached_data, cached_at = await asyncio.to_thread(data_cache.load)
            if not _epoch_is_current():
                return
            if cached_data:
                with self._data_lock:
                    self.all_data = cached_data
                self._set_sync_status(
                    "error", "Lỗi cập nhật · đang dùng dữ liệu đã lưu"
                )
                self.error_state.visible = False
                self._update_footer()
            else:
                self.error_text.value = "Không thể kết nối tới Moodle. Vui lòng kiểm tra mạng và thử lại."
                self.error_state.visible = True
                self._set_sync_status("error", "Lỗi kết nối server")
        finally:
            if (
                getattr(self, "_active_sync_epoch", None) == account_epoch
                and getattr(self, "_active_sync_generation", sync_generation)
                == sync_generation
            ):
                self.refresh_btn.disabled = False
                self._is_loading = False
                self._active_sync_epoch = None
                self._active_sync_generation = None
                self.page.update()

    async def _close_detail(self):
        # asyncio.to_thread cannot cancel blocking HTTP. Invalidate its UI
        # continuation so a late response cannot reopen/overwrite this view.
        self._detail_request_epoch = getattr(self, "_detail_request_epoch", 0) + 1
        from_calendar = getattr(self, '_detail_from_calendar', False)
        self._detail_from_calendar = False
        await self.view_manager.close_detail(from_calendar)
        if hasattr(self, "_diag_screen"):
            if from_calendar:
                self._diag_screen("calendar")
            elif getattr(getattr(self, "courses_view", None), "visible", False):
                self._diag_screen("courses")
            else:
                self._diag_screen("home")

    def _active_course_quiz_overlay(self, activity: dict | None = None):
        courses_view = getattr(self, "courses_view", None)
        overlays = getattr(courses_view, "_overlays", None)
        if not overlays:
            return None
        top = overlays[-1]
        if not hasattr(top, "set_runtime_notice") or not hasattr(top, "reload"):
            return None
        if isinstance(activity, dict):
            mod = getattr(top, "_module", None)
            cmid = activity.get("cmid")
            mod_cmid = getattr(mod, "cmid", None)
            if cmid and mod_cmid and int(cmid) != int(mod_cmid):
                return None
        return top

    def _quiz_client_for_site(self, site_identity: str):
        orchestrator = getattr(self, "orchestrator", None)
        if orchestrator is None:
            return None
        if str(site_identity or "").strip().casefold() == "thnn":
            return getattr(orchestrator, "thnn_client", None)
        return getattr(orchestrator, "client", None)

    def _sync_quiz_immersive(self, active: bool) -> None:
        _set_webview_occlusion("quiz_player", bool(active))
        nav_shell = getattr(self, "_nav_shell", None)
        if nav_shell is None or not hasattr(nav_shell, "set_immersive"):
            return
        if active:
            nav_shell.set_immersive(True)
        else:
            courses_view = getattr(self, "courses_view", None)
            keep_immersive = False
            if courses_view is not None and getattr(courses_view, "visible", False):
                comp = getattr(courses_view, "_compute_immersive", None)
                if callable(comp):
                    keep_immersive = bool(comp())
            nav_shell.set_immersive(keep_immersive)
        bar = getattr(nav_shell, "bar", None)
        if bar is not None:
            try:
                if getattr(bar, "page", None) is not None:
                    bar.update()
            except Exception:
                pass

    def _build_course_quiz_activity(
        self, course: "CourseSummary", mod: "CourseModuleItem"
    ) -> dict:
        origin = (
            "https://thnn.ut.edu.vn"
            if course.source == "thnn"
            else "https://courses.ut.edu.vn"
        )
        url = (
            safe_http_url(mod.web_url)
            or safe_http_url(mod.url)
            or (f"{origin}/mod/quiz/view.php?id={mod.cmid}" if mod.cmid else "")
        )
        if url and _classify_uth_url(url) in {"courses", "thnn"}:
            url = _normalize_uth_target_url(url, has_credentials=False)
        activity: dict = {}
        for item in self._snapshot_all_data():
            item_url = str(item.get("url") or "")
            if item_url and _classify_uth_url(item_url) in {"courses", "thnn"}:
                item_url = _normalize_uth_target_url(item_url, has_credentials=False)
            if (url and item_url == url) or (
                mod.cmid
                and item.get("cmid") == mod.cmid
                and item.get("course_id") == course.id
            ):
                activity = dict(item)
                break
        activity.setdefault("title", mod.name or "Bài kiểm tra")
        activity.setdefault("course", course.display_name or course.fullname)
        activity.setdefault("course_id", course.id)
        activity.setdefault("cmid", mod.cmid)
        activity["type"] = "quiz"
        activity["site_identity"] = course.source
        resolved_quiz_url = str(activity.get("url") or url or "")
        if resolved_quiz_url and _classify_uth_url(resolved_quiz_url) in {
            "courses",
            "thnn",
        }:
            resolved_quiz_url = _normalize_uth_target_url(
                resolved_quiz_url, has_credentials=False
            )
        if resolved_quiz_url:
            activity["url"] = resolved_quiz_url
        return activity

    def _start_course_quiz(self, course: "CourseSummary", mod: "CourseModuleItem") -> None:
        if is_demo_account(settings.UTH_USERNAME):
            self._show_demo_lms_guard_snackbar()
            return
        player_view = getattr(self, "quiz_player_view", None)
        activity = self._build_course_quiz_activity(course, mod)
        url = str(activity.get("url") or "")
        if player_view is None:
            if url and getattr(self, "_courses_coordinator", None) is not None:
                self._courses_coordinator.open_in_app_webview(
                    course.source, mod.name or "Bài kiểm tra", url, tag=f"webview:quiz:{mod.cmid}"
                )
            return
        if getattr(player_view, "visible", False):
            return

        account_id = getattr(settings, "UTH_USERNAME", "") or ""
        target, state = player_view.prepare_activity(activity, account_id)
        if state == "missing_account":
            request_auth = getattr(player_view, "request_app_auth", None)
            if callable(request_auth):
                async def _retry_course_quiz(c=course, m=mod) -> None:
                    self._start_course_quiz(c, m)

                self._safe_run_task(
                    request_auth,
                    _retry_course_quiz,
                )
            return
        if state == "requires_safe_exam_browser":
            overlay = self._active_course_quiz_overlay(activity)
            if overlay is not None:
                overlay.set_runtime_notice(
                    "Bài kiểm tra yêu cầu Safe Exam Browser. Hãy mở bằng trình duyệt ngoài."
                )
            if url:
                self._open_url_external(url)
            return
        if target is None or state in {"player_unavailable", "unsupported_runtime"}:
            if url and getattr(self, "_courses_coordinator", None) is not None:
                self._courses_coordinator.open_in_app_webview(
                    course.source, mod.name or "Bài kiểm tra", url, tag=f"webview:quiz:{mod.cmid}"
                )
            return

        self._sync_quiz_immersive(True)
        opened = player_view.open_activity_for_account(
            target,
            activity,
            account_id,
            on_entry_unavailable=self._on_course_quiz_entry_unavailable,
        )
        if not opened and not getattr(player_view, "visible", False):
            self._sync_quiz_immersive(False)
            if url and getattr(self, "_courses_coordinator", None) is not None:
                self._courses_coordinator.open_in_app_webview(
                    course.source, mod.name or "Bài kiểm tra", url, tag=f"webview:quiz:{mod.cmid}"
                )

    async def _on_course_quiz_entry_unavailable(
        self, activity: dict | None, reason: str
    ) -> None:
        self._sync_quiz_immersive(False)
        overlay = self._active_course_quiz_overlay(activity if isinstance(activity, dict) else None)
        messages = {
            "quiz_closed": "Bài kiểm tra hiện đã đóng.",
            "quiz_unavailable": "Bài kiểm tra hiện không thể mở.",
            "unsupported_action": "Bài kiểm tra hiện không thể mở trong ứng dụng.",
            "requires_safe_exam_browser": "Bài kiểm tra yêu cầu Safe Exam Browser. Hãy mở bằng trình duyệt.",
            "entry_in_progress": "Bài kiểm tra đang được thực hiện.",
            "entry_not_opened": "Không thể mở lượt làm bài.",
            "attempts_exhausted": "Bạn đã sử dụng hết số lượt làm bài.",
        }
        msg = messages.get(
            reason,
            "Không thể mở bài kiểm tra trực tiếp. Hãy tải lại hoặc mở trang web.",
        )
        if overlay is not None:
            if reason == "attempts_exhausted":
                overlay.reload()
            overlay.set_runtime_notice(msg)

    async def _on_quiz_player_closed(self, activity: dict | None) -> None:
        """Refresh quiz state from Moodle after the native player is released."""
        self._sync_quiz_immersive(False)
        if not isinstance(activity, dict):
            return
        url = activity.get("url")
        if not isinstance(url, str) or not url:
            return
        try:
            # The following detail load must ask Moodle for the latest attempt
            # state; a player close does not prove that an attempt was submitted.
            self.orchestrator.invalidate_detail_cache(url)
            if (
                getattr(self, "detail_view", None) is not None
                and self.detail_view.visible
                and self.detail_view._current_url == url
            ):
                await self._show_detail_async(dict(activity))
            if getattr(getattr(self, "courses_view", None), "visible", False):
                overlay = self._active_course_quiz_overlay(activity)
                if overlay is not None:
                    overlay.reload()
        except Exception:
            logger.exception("Quiz detail refresh failed after player close")
        finally:
            detail_view = getattr(self, "detail_view", None)
            focus_opener = getattr(detail_view, "focus_quiz_opener", None)
            if callable(focus_opener):
                await focus_opener()

    def _set_scroll_to_top_visible(self, visible: bool) -> bool:
        """Update the floating affordance without rebuilding the card list."""
        if visible == self._scroll_to_top_visible:
            return False
        self._scroll_to_top_visible = visible
        self._scroll_to_top_button.opacity = 1.0 if visible else 0.0
        self._scroll_to_top_button.offset = (
            ft.Offset(0, 0) if visible else ft.Offset(0, 0.3)
        )
        self._scroll_to_top_button.scale = 1.0 if visible else 0.84
        self._scroll_to_top_button.ignore_interactions = not visible
        return True

    def _on_dashboard_scroll(self, event) -> None:
        """Track the exact dashboard position and toggle the overlay sparingly."""
        try:
            pixels = float(event.pixels)
            minimum = float(getattr(event, "min_scroll_extent", 0.0))
            out_of_range = bool(getattr(event, "out_of_range", False))
        except (AttributeError, TypeError, ValueError):
            return

        offset = max(0.0, pixels - minimum)
        if offset <= 5.0 or (out_of_range and pixels <= minimum):
            offset = 0.0
        self._dashboard_scroll_offset = offset
        if getattr(self, "_scroll_to_top_in_progress", False):
            # Native animateTo() also emits scroll notifications. The click
            # path already hid the button; do not re-show/repaint it while the
            # animation is moving through offsets above the show threshold.
            return
        if self._scroll_to_top_visible:
            should_show = offset > _SCROLL_TO_TOP_HIDE_OFFSET
        else:
            should_show = offset > _SCROLL_TO_TOP_SHOW_OFFSET

        # Hysteresis plus update-on-transition avoids a page update for every
        # scroll event, which matters on low-end desktop and mobile devices.
        if self._set_scroll_to_top_visible(should_show):
            try:
                self._scroll_to_top_button.update()
            except RuntimeError:
                logger.debug("Scroll button unmounted during visibility update")

    async def _scroll_dashboard_to_top(self, _event=None) -> None:
        """Fade locally first, then issue native scrolling in the same tick."""
        if getattr(self, "_scroll_to_top_in_progress", False):
            return
        self._scroll_to_top_in_progress = True
        previous_offset = self._dashboard_scroll_offset
        try:
            # Flet method invocations can occupy the client command queue until
            # their animation completes. Publish the tiny control-only patch
            # first so the fade cannot sit behind the 300 ms scroll method.
            self._dashboard_scroll_offset = 0.0
            self._set_scroll_to_top_visible(False)
            self._scroll_to_top_button.update()
            await self.cards_column.scroll_to(
                offset=0,
                duration=_SCROLL_TO_TOP_DURATION_MS,
                curve=ft.AnimationCurve.EASE_OUT_CUBIC,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._dashboard_scroll_offset = previous_offset
            self._set_scroll_to_top_visible(
                previous_offset > _SCROLL_TO_TOP_HIDE_OFFSET
            )
            try:
                self._scroll_to_top_button.update()
            except RuntimeError:
                logger.debug("Scroll button unmounted during recovery")
            logger.debug("Scroll-to-top command failed", exc_info=True)
        finally:
            self._scroll_to_top_in_progress = False

    async def _toggle_calendar(self):
        """Toggle between dashboard list view and calendar view."""
        if self.calendar_view.visible:
            await self._close_calendar()
        else:
            await self._show_calendar()

    async def _show_calendar(self):
        """Show calendar view with current data."""
        self._diag_screen("calendar")
        self._diag_breadcrumb("navigation", "succeeded", "user", screen="calendar")
        with self._data_lock:
            data_snapshot = list(self.all_data)
        self.view_manager.show_calendar(data_snapshot)
        self._sync_rail_active()

    async def _close_calendar(self):
        """Return from calendar to dashboard."""
        await self.view_manager.close_calendar()
        self._diag_screen("home")
        self._sync_rail_active()

    async def _show_settings(self):
        if hasattr(self, "_diag_screen"):
            self._diag_screen("settings")
            self._diag_breadcrumb(
                "navigation", "succeeded", "user", screen="settings"
            )
        await self.view_manager.show_settings()
        self._sync_rail_active()

    async def _toggle_grades(self):
        """Toggle the grade overview panel."""
        if self.grade_overview_view.visible:
            await self._close_grades()
        else:
            await self._show_grades()

    async def _show_grades(self, force_refresh: bool = False):
        """Show grade overview panel and load academic transcript/radar data."""
        account_epoch = getattr(self, "_account_epoch", 0)
        request_epoch = getattr(self, "_grades_request_epoch", 0) + 1
        self._grades_request_epoch = request_epoch

        def _is_current() -> bool:
            return (
                account_epoch == getattr(self, "_account_epoch", 0)
                and request_epoch == getattr(self, "_grades_request_epoch", 0)
            )

        self._diag_screen("grades")
        self._diag_breadcrumb("navigation", "succeeded", "user", screen="grades")
        self.view_manager.show_grades_loading()
        self._sync_rail_active()

        # Load academic radar data from daily cache / Portal in background thread
        try:
            from core.academic_radar import AcademicGradeService
            service = getattr(self, "_academic_grade_service", None)
            if service is None:
                service = AcademicGradeService()
                self._academic_grade_service = service

            username = settings.UTH_USERNAME
            password = settings.UTH_PASSWORD

            semesters = await asyncio.to_thread(
                service.load_academic_data,
                username=username,
                password=password,
                force_refresh=force_refresh,
            )
            if not _is_current():
                return
            if semesters:
                profiles = service.get_radar_profiles(semesters)
                # Hiện bảng điểm ngay, không chờ API tiến độ tín chỉ
                self.view_manager.show_academic_radar_data(semesters, profiles, None)
                try:
                    progress = await asyncio.to_thread(
                        service.load_credit_progress,
                        username=username,
                        password=password,
                        force_refresh=force_refresh,
                    )
                    if not _is_current():
                        return
                    if progress is not None:
                        self.view_manager.show_credit_progress(progress)
                except Exception as exc:
                    logger.warning("Credit progress load failed: %s", exc)
            else:
                # Fallback to Moodle grades if Portal returned no semesters
                userid = await asyncio.to_thread(
                    getattr(self.orchestrator, "_get_userid", lambda: None)
                )
                if not _is_current():
                    return
                if userid:
                    courses_grades, grade_items = await asyncio.to_thread(
                        self.orchestrator.moodle_service.fetch_all_grades, userid
                    )
                    if not _is_current():
                        return
                    moodle_semesters = service.create_semester_from_moodle_grades(
                        courses_grades or [], grade_items or {}
                    )
                    if moodle_semesters:
                        profiles = service.get_radar_profiles(moodle_semesters)
                        self.view_manager.show_academic_radar_data(moodle_semesters, profiles)
                    else:
                        self.view_manager.show_grades_data(courses_grades or [], grade_items or {})
                else:
                    self.view_manager.show_academic_radar_data([], [])
        except Exception as e:
            if not _is_current():
                return
            logger.error("Academic grades fetch failed: %s", e)
            self.view_manager.show_grades_error(
                "Không tải được bảng điểm từ Portal.\nKiểm tra kết nối/tài khoản rồi bấm thử lại."
            )

    async def _show_course_grades(self, force_refresh: bool = False):
        """Mở danh sách môn & điểm/chuyên cần từ màn hình Bảng điểm."""
        account_epoch = getattr(self, "_account_epoch", 0)
        request_epoch = getattr(self, "_course_grades_request_epoch", 0) + 1
        self._course_grades_request_epoch = request_epoch

        def _is_current() -> bool:
            return (
                account_epoch == getattr(self, "_account_epoch", 0)
                and request_epoch == getattr(self, "_course_grades_request_epoch", 0)
            )

        self._diag_screen("course_grades")
        self._diag_breadcrumb("navigation", "succeeded", "user", screen="course_grades")
        self.view_manager.show_course_grades_loading()
        cached = getattr(self.orchestrator, "latest_course_grades", None)
        if not cached:
            monitor = getattr(self.orchestrator, "course_grade_monitor", None)
            load_cached = getattr(monitor, "load_cached", None)
            if callable(load_cached):
                cached = await asyncio.to_thread(load_cached)
                if not _is_current():
                    return
        if cached and not force_refresh:
            self.view_manager.show_course_grades_data(cached)
        try:
            courses, changes = await asyncio.to_thread(
                self.orchestrator.refresh_course_grades
            )
            if not _is_current():
                return
            self.view_manager.show_course_grades_data(courses)
            if changes and not is_demo_account(settings.UTH_USERNAME):
                await self._notify_grade_changes(changes)
        except Exception as e:
            if not _is_current():
                return
            logger.error("Course grades fetch failed: %s", e, exc_info=True)
            if cached:
                self.view_manager.show_course_grades_stale_notice(
                    "Không thể làm mới danh sách môn · đang hiển thị dữ liệu đã lưu."
                )
            else:
                self.view_manager.show_course_grades_error()

    async def _close_course_grades(self):
        """Đóng danh sách điểm môn và quay về màn hình Bảng điểm."""
        await self.view_manager.close_course_grades()
        self._diag_screen("grades")
        self._sync_rail_active()

    async def _toggle_courses(self):
        if self.courses_view.visible:
            await self._close_courses()
        else:
            await self._show_courses()

    async def _show_courses(self, force_refresh: bool = False):
        """Mở tab Môn học hợp nhất (Courses & THNN)."""
        del force_refresh
        await self._navigate_destination("courses")
        self._sync_rail_active()

    async def _close_courses(self):
        self.view_manager.show_dashboard()
        self._diag_screen("home")
        self._sync_rail_active()

    async def _close_grades(self):
        """Close grade overview and return to dashboard."""
        await self.view_manager.close_grades()
        self._diag_screen("home")
        self._sync_rail_active()

    def _rebuild_colors(self, *, sync_nav: bool = True, update_page: bool = True):
        """Repaint ALL existing controls with current C values for live theme switching.

        Because controls store hardcoded color strings at init time,
        we must explicitly reassign every color property when the theme changes.
        """
        from gui.core.theme import C as _C
        current_palette = semantic_palette()
        dashboard = getattr(self, "dashboard", None)
        theme_roots = [dashboard] if dashboard is not None else []
        window_header = getattr(self, "_window_header", None)
        if window_header is not None:
            theme_roots.append(window_header)
        previous_palette = getattr(self, "_rendered_palette", None)
        if theme_roots and isinstance(previous_palette, dict):
            refresh_semantic_control_colors(
                theme_roots, previous_palette, current_palette
            )
        self._rendered_palette = current_palette

        # Page
        page = getattr(self, "page", None)
        if page is not None:
            page.bgcolor = _C.BG
        if getattr(self, "_sidebar", None) is not None:
            self._sidebar.border = ft.Border.only(left=ft.BorderSide(1, _C.BORDER))
        if sync_nav:
            self._sync_rail_active()

        header_container = getattr(self, "_header_container", None)
        if header_container is not None:
            header_container.bgcolor = _C.BG
        app_title = getattr(self, "_app_title", None)
        if app_title is not None:
            app_title.color = _C.TEXT_PRIMARY
        version_text = getattr(self, "_version_text", None)
        if version_text is not None:
            version_text.color = _C.TEXT_SECONDARY
        version_badge = getattr(self, "_version_badge", None)
        if version_badge is not None:
            version_badge.border = ft.Border.all(1, _C.BORDER)
        dashboard_title = getattr(self, "_dashboard_title", None)
        if dashboard_title is not None:
            dashboard_title.color = _C.TEXT_PRIMARY
        refresh_box = getattr(self, "_refresh_action_box", None)
        if refresh_box is not None:
            refresh_box.bgcolor = _C.SURFACE
            refresh_box.border = ft.Border.all(1, _C.BORDER)
        reset_filters_btn = getattr(self, "_reset_filters_btn", None)
        if reset_filters_btn is not None:
            reset_filters_btn.style = ft.ButtonStyle(
                color=_C.ACCENT,
                padding=ft.Padding.symmetric(horizontal=10, vertical=0),
                shape=ft.RoundedRectangleBorder(radius=8),
            )
        search_clear_btn = getattr(self, "_search_clear_btn", None)
        if search_clear_btn is not None:
            search_clear_btn.icon_color = _C.TEXT_SECONDARY
        empty_clear_btn = getattr(self, "_empty_clear_filter_btn", None)
        if empty_clear_btn is not None:
            empty_clear_btn.style = ft.ButtonStyle(
                color=_C.ACCENT,
                side=ft.BorderSide(1, _C.BORDER),
                shape=ft.RoundedRectangleBorder(radius=8),
            )

        # Status text
        status_text = getattr(self, "status_text", None)
        if status_text is not None:
            status_text.color = _C.TEXT_SECONDARY

        # Filter area
        filter_container = getattr(self, "_filter_container", None)
        if filter_container is not None:
            filter_container.bgcolor = _C.BG
        search_field = getattr(self, "search_field", None)
        if search_field is not None:
            search_field.bgcolor = _C.SURFACE
            search_field.border_color = _C.BORDER
            search_field.focused_border_color = _C.ACCENT
            search_field.color = _C.TEXT_PRIMARY

        # Adaptive filters rebuild from their option models instead of relying
        # on brittle control-tree indexes.
        urgency_filter_menu = getattr(self, "urgency_filter_menu", None)
        if urgency_filter_menu is not None:
            urgency_filter_menu.refresh_theme(
                self._urgency_filter_options(
                    {option.key: option.count or 0 for option in urgency_filter_menu.options}
                )
            )
        type_filter_menu = getattr(self, "type_filter_menu", None)
        if type_filter_menu is not None:
            type_filter_menu.refresh_theme(
                self._type_filter_options(
                    {option.key: option.count or 0 for option in type_filter_menu.options}
                )
            )
        course_filter_menu = getattr(self, "course_filter_menu", None)
        if course_filter_menu is not None:
            course_filter_menu.refresh_theme(
                self._course_filter_options(
                    {option.key: option.count or 0 for option in course_filter_menu.options}
                )
            )

        # Empty state
        if hasattr(self, '_empty_icon'):
            self._empty_icon.color = _C.BORDER
        if hasattr(self, '_empty_title'):
            self._empty_title.color = _C.TEXT_SECONDARY
        if hasattr(self, '_empty_subtitle'):
            self._empty_subtitle.color = _C.BORDER

        # Error state
        if hasattr(self, '_error_icon'):
            self._error_icon.color = _C.WARNING
        if hasattr(self, '_error_title'):
            self._error_title.color = _C.TEXT_SECONDARY
        if hasattr(self, 'error_text'):
            self.error_text.color = _C.BORDER
        if hasattr(self, '_error_retry_btn'):
            self._error_retry_btn.bgcolor = _C.ACCENT
            self._error_retry_btn.color = _C.ON_ACCENT

        # Footer
        footer = getattr(self, "_footer", None)
        if footer is not None:
            footer.bgcolor = _C.SURFACE
            footer.border = ft.Border.only(top=ft.BorderSide(1, _C.BORDER))
        # Footer counter colors are set dynamically by _refresh_counts,
        # but update the base colors here too
        for footer_attr, active_color in (
            ("footer_critical", _C.CRITICAL),
            ("footer_warning", _C.WARNING),
            ("footer_safe", _C.SAFE),
            ("footer_overdue", _C.CRITICAL),
        ):
            footer_label = getattr(self, footer_attr, None)
            if footer_label is not None:
                footer_label.color = active_color if footer_label.value else _C.BORDER

        content_area = getattr(self, "_content_area", None)
        if content_area is not None:
            content_area.bgcolor = _C.BG

        # Cards  Re-color all existing recycled cards
        if hasattr(self, '_reusable_cards'):
            for card in self._reusable_cards:
                card.bgcolor = _C.SURFACE
                try:
                    card.update_data(card.data, force=True)
                except Exception:
                    import logging as _fb_log
                    _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

        component = getattr(self, "_today_schedule_component", None)
        if component is not None:
            component.refresh_theme()
        app_wv_host = getattr(self, "_app_webview_host", None)
        if app_wv_host is not None:
            app_wv_host.bgcolor = _C.BG
        app_wv_pane = getattr(self, "_app_webview_pane", None)
        if app_wv_pane is not None:
            app_wv_pane.update_theme()

        # Icon buttons
        buttons_to_update = [
            btn
            for btn in (
                getattr(self, "calendar_btn", None),
                getattr(self, "grades_btn", None),
                getattr(self, "courses_btn", None),
                getattr(self, "refresh_btn", None),
                getattr(self, "settings_btn", None),
            )
            if btn is not None
        ]

        for btn in buttons_to_update:
            btn.icon_color = _C.TEXT_SECONDARY

        nav_shell = getattr(self, "_nav_shell", None)
        if nav_shell is not None and hasattr(nav_shell, "refresh_theme"):
            nav_shell.refresh_theme()

        sync_status = getattr(self, "_sync_status", None)
        if sync_status is not None:
            sync_status.refresh_theme()

        # Update banner is a semantic elevated surface, not a fixed brown card.
        update_banner = getattr(self, "_update_banner", None)
        if update_banner is not None:
            update_banner.bgcolor = _C.SURFACE_ELEVATED
            update_banner.border = ft.Border.all(1, _C.UPDATE)
        update_text = getattr(self, "_update_text", None)
        if update_text is not None:
            update_text.color = _C.TEXT_PRIMARY
        update_progress = getattr(self, "_update_progress", None)
        if update_progress is not None:
            update_progress.color = _C.UPDATE
            update_progress.bgcolor = _C.BORDER
        update_btn = getattr(self, "_update_btn", None)
        if update_btn is not None:
            update_btn.style = ft.ButtonStyle(
                color=_C.TEXT_PRIMARY, shape=ft.RoundedRectangleBorder(radius=8),
            )
        update_cancel_btn = getattr(self, "_update_cancel_btn", None)
        if update_cancel_btn is not None:
            update_cancel_btn.style = ft.ButtonStyle(
                color=_C.TEXT_SECONDARY, shape=ft.RoundedRectangleBorder(radius=8),
            )

        # Settings view bgcolor
        settings_view = getattr(self, "settings_view", None)
        if settings_view is not None:
            settings_view.bgcolor = _C.BG

        # Sub-views theme update propagation
        for view_attr in (
            "calendar_view",
            "detail_view",
            "grade_overview_view",
            "courses_view",
            "course_grades_view",
            "quiz_player_view",
        ):
            view_obj = getattr(self, view_attr, None)
            if view_obj and hasattr(view_obj, "update_theme"):
                try:
                    view_obj.update_theme()
                except Exception as e:
                    import logging
                    logging.getLogger("UTHelper").error(f"Failed to update theme for {view_attr}: {e}", exc_info=True)

        courses_coord = getattr(self, "_courses_coordinator", None)
        if courses_coord is not None and hasattr(courses_coord, "update_theme"):
            try:
                courses_coord.update_theme()
            except Exception as e:
                logger.error("Failed to update theme for _courses_coordinator: %s", e, exc_info=True)

        if update_page and page is not None:
            page.update()


    def _reconcile_settings_after_exit(
        self, *, sync_nav: bool = True, update_page: bool = True
    ) -> None:
        """Restore persisted theme/colors and recalculate urgencies when leaving Settings."""
        settings_view = getattr(self, "settings_view", None)
        cancel_load = getattr(settings_view, "cancel_pending_load", None)
        if callable(cancel_load):
            cancel_load()
        from gui.core.theme import C as _C, load_theme_from_settings, set_page_theme

        load_theme_from_settings()
        if hasattr(self, "page") and self.page is not None:
            set_page_theme(self.page)
        current_palette = semantic_palette()
        needs_rebuild = (
            getattr(self, "_rendered_palette", None) != current_palette
            or getattr(getattr(self, "page", None), "bgcolor", None) != _C.BG
        )
        if needs_rebuild and hasattr(self, "_rebuild_colors"):
            rebuild = self._rebuild_colors
            try:
                params = inspect.signature(rebuild).parameters
            except (TypeError, ValueError):
                params = {}
            if "sync_nav" in params and "update_page" in params:
                rebuild(sync_nav=sync_nav, update_page=update_page)
            else:
                rebuild()

        from datetime import datetime

        data_lock = getattr(self, "_data_lock", None)
        all_data = getattr(self, "all_data", None)
        if data_lock is not None and all_data is not None:
            with data_lock:
                data_copy = list(all_data)
            for d in data_copy:
                dt_str = d.get("deadline")
                if dt_str:
                    dt = parse_datetime(dt_str)
                    if dt:
                        diff_h = (dt - datetime.now()).total_seconds() / 3600
                        if diff_h < 0:
                            d["urgency"] = "overdue"
                        elif diff_h < settings.URGENCY_CRITICAL_HOURS:
                            d["urgency"] = "critical"
                        elif diff_h < settings.URGENCY_WARNING_HOURS:
                            d["urgency"] = "warning"
                        else:
                            d["urgency"] = "safe"
            with data_lock:
                self.all_data = data_copy

    async def _leave_settings_and_navigate(self, target: str) -> None:
        """Exit Settings cleanly (after clean check, save, or discard) and open ``target``.

        Important Flet invariant: never set ``settings_view.visible = False`` or call
        ``page.update()`` while ``tab_switcher.content`` still references
        ``settings_view``, otherwise Flet raises
        ``ValueError('AnimatedSwitcher.content must be provided and visible')``.
        """
        if target == "home":
            await self._close_settings()
            return
        reconcile = self._reconcile_settings_after_exit
        try:
            reconcile_params = inspect.signature(reconcile).parameters
        except (TypeError, ValueError):
            reconcile_params = {}
        if "sync_nav" in reconcile_params and "update_page" in reconcile_params:
            reconcile(sync_nav=False, update_page=False)
        else:
            reconcile()
        update_footer = getattr(self, "_update_footer", None)
        if callable(update_footer):
            try:
                sig = inspect.signature(update_footer)
                if "update_page" in sig.parameters:
                    update_footer(update_page=False)
                else:
                    update_footer()
            except (TypeError, ValueError):
                update_footer()
        if getattr(self, "_needs_reload", False):
            self._needs_reload = False
            self._safe_run_task(self._load_data_async)
        await self._perform_destination_navigation(target)
        settings_view = getattr(self, "settings_view", None)
        tab_switcher = getattr(self, "tab_switcher", None)
        if settings_view is not None and (
            tab_switcher is None or tab_switcher.content is not settings_view
        ):
            settings_view.visible = False
        self._sync_rail_active()

    async def _close_settings(self):
        self._sync_rail_active(force_home=True)
        reconcile = self._reconcile_settings_after_exit
        try:
            reconcile_params = inspect.signature(reconcile).parameters
        except (TypeError, ValueError):
            reconcile_params = {}
        if "sync_nav" in reconcile_params and "update_page" in reconcile_params:
            reconcile(sync_nav=False, update_page=False)
        else:
            reconcile()

        # Toggle visibility - no full rebuild needed (fixes white flash)
        await self.view_manager.close_settings()
        self._diag_screen("home")
        
        self._update_footer()
        
        if getattr(self, '_needs_reload', False):
            self._needs_reload = False
            self._safe_run_task(self._load_data_async)
        # _update_footer() already calls _refresh_ui() which calls page.update()
        # No extra page.update() needed

    def _show_snackbar(
        self,
        msg: str,
        icon=ft.Icons.CHECK_CIRCLE_ROUNDED,
        color=C.SAFE,
        *,
        duration: int = 2500,
    ):
        """UX: Show a brief toast-like notification without leaking overlay SnackBars."""
        sb = ft.SnackBar(
            content=ft.Row([
                ft.Icon(icon, color=color, size=16),
                ft.Text(msg, size=13, color=C.TEXT_PRIMARY, expand=True),
            ], spacing=8),
            bgcolor=C.SURFACE,
            duration=duration,
        )
        overlay = getattr(self.page, "overlay", None)
        if overlay is not None:
            # Prune prior SnackBars from overlay so repeated syncs/alerts never leak.
            overlay[:] = [
                ctrl
                for ctrl in overlay
                if not isinstance(ctrl, ft.SnackBar)
            ]
            overlay.append(sb)
            sb.open = True
            try:
                self.page.update()
            except Exception:
                pass
        else:
            try:
                self.page.show_dialog(sb)
            except (AttributeError, TypeError):
                pass

    def _test_notification_base(self, mock_type="critical"):
        import random
        import datetime
        from models import Assignment, ActivityDetail

        # Mặc định
        delta = datetime.timedelta(hours=10)
        title = 'BÀI KIỂM THỬ KHẨN CẤP (< 24h)'
        event_type = 'deadline'
        course_name = 'Công nghệ Phần mềm'

        if mock_type == "warning":
            delta = datetime.timedelta(hours=48)
            title = 'BÀI KIỂM THỬ SẮP TỚI HẠN (2-3 ngày)'
        elif mock_type == "safe":
            delta = datetime.timedelta(days=5)
            title = 'BÀI KIỂM THỬ AN TOÀN (> 3 ngày)'
        elif mock_type == "quiz":
            delta = datetime.timedelta(hours=10)
            title = 'BÀI TRẮC NGHIỆM ĐANG MỞ (Quiz)'
            event_type = 'quiz'
            course_name = 'Mạng Máy Tính'
        elif mock_type == "attendance":
            delta = datetime.timedelta(hours=2)
            title = 'BÀI ĐIỂM DANH (Sắp đóng)'
            event_type = 'attendance'
            course_name = 'Triết học Mác - Lênin'
            
        now = datetime.datetime.now()
        dummy_details = ActivityDetail(
            open_time=now - datetime.timedelta(days=2),
            description_html="<p>Đây là bài tập kiểm thử được khởi tạo bởi Debug Mode.</p>",
        )

        return Assignment(
            id=str(random.randint(1000, 9999)),
            course_id="0",
            course_name=course_name,
            title=title,
            event_type=event_type,
            deadline=now + delta,
            url="https://lms.hcmut.edu.vn",
            details=dummy_details
        )
    def _dispatch_test_notifier_off_loop(self, send_fn) -> None:
        async def _run():
            await asyncio.to_thread(send_fn)

        if self._safe_run_task(_run) is None:
            send_fn()

    def _on_test_tray(self, mock_type="critical"):
        dummy = self._test_notification_base(mock_type)

        def _send():
            from notifiers.windows import WindowsNotifier

            notifier = WindowsNotifier()
            try:
                notifier.notify([dummy])
            finally:
                close_fn = getattr(notifier, "close", None)
                if callable(close_fn):
                    close_fn()

        self._dispatch_test_notifier_off_loop(_send)

    def _on_test_mobile(self, mock_type="critical"):
        dummy = self._test_notification_base(mock_type)
        async def _send():
            from notifiers.mobile import MobileNotifier
            notifier = MobileNotifier()
            await notifier.setup(self.page)
            await notifier.notify([dummy])

        self._safe_run_task(_send)

    def _on_test_tele(self, mock_type="critical"):
        dummy = self._test_notification_base(mock_type)

        def _send():
            from notifiers.telegram import TelegramNotifier

            TelegramNotifier().notify([dummy])

        self._dispatch_test_notifier_off_loop(_send)

    def _on_test_discord(self, mock_type="critical"):
        dummy = self._test_notification_base(mock_type)

        def _send():
            from notifiers.discord import DiscordNotifier

            DiscordNotifier().notify([dummy])

        self._dispatch_test_notifier_off_loop(_send)

    def _on_test_mail(self, mock_type="critical"):
        dummy = self._test_notification_base(mock_type)

        def _send():
            from notifiers.email import EmailNotifier

            EmailNotifier().notify([dummy])

        self._dispatch_test_notifier_off_loop(_send)

    def _on_update_event(self, event: UpdateEvent) -> None:
        if not self._get_distribution_profile().supports_github_self_update:
            return
        if event.candidate is not None:
            if event.candidate != self._update_candidate:
                self._update_ready = False
                self._update_manual_download = False
            self._update_candidate = event.candidate
        self._safe_run_task(self._apply_update_event, event)

    def _request_update_download(self, _event=None) -> None:
        if not self._get_distribution_profile().supports_github_self_update:
            return
        candidate = getattr(self, "_update_candidate", None)
        if candidate is not None:
            if getattr(self, "_update_open_release_page", False):
                self._open_external_update_url(candidate.manifest.release_notes_url)
            elif getattr(self, "_update_ready", False) or getattr(
                self, "_update_manual_download", False
            ):
                self._show_update_confirmation()
            else:
                coordinator = getattr(self, "_update_coordinator", None)
                if coordinator is not None:
                    coordinator.request_download(candidate)

    async def _apply_update_event(self, event: UpdateEvent) -> None:
        if not self._get_distribution_profile().supports_github_self_update:
            banner = getattr(self, "_update_banner", None)
            if banner is not None:
                banner.visible = False
            return
        candidate = event.candidate
        if candidate is not None:
            self._update_candidate = candidate
            if event.kind is not UpdateEventKind.INSTALL_FAILED:
                self._update_open_release_page = False
        check_button = getattr(self.settings_view, "_check_update_btn", None)
        cancel_button = getattr(self, "_update_cancel_btn", None)
        self._update_btn.visible = event.kind not in {
            UpdateEventKind.DOWNLOADING, UpdateEventKind.DOWNLOAD_PROGRESS,
            UpdateEventKind.VERIFYING, UpdateEventKind.INSTALLING,
        }
        if cancel_button is not None:
            cancel_button.visible = event.kind in {
                UpdateEventKind.DOWNLOADING, UpdateEventKind.DOWNLOAD_PROGRESS,
            }
        if event.kind is UpdateEventKind.CHECKING:
            self._diag_breadcrumb("update_check", "started", "github")
            if check_button is not None:
                check_button.disabled = True
                check_button.content = "Đang kiểm tra..."
        elif event.kind is UpdateEventKind.UP_TO_DATE:
            self._update_ready = False
            self._update_manual_download = False
            self._update_candidate = None
            self._update_banner.visible = False
            self._diag_breadcrumb("update_check", "succeeded", "github")
            if check_button is not None:
                check_button.disabled = False
                check_button.content = "Kiểm tra ngay"
            if self._manual_update_check_requested:
                self._show_snackbar(
                    "Bạn đang dùng phiên bản mới nhất",
                    ft.Icons.CHECK_CIRCLE_ROUNDED,
                    C.SAFE,
                )
            self._manual_update_check_requested = False
        elif event.kind is UpdateEventKind.UPDATE_AVAILABLE and candidate is not None:
            self._diag_breadcrumb("update_check", "succeeded", "github")
            self._manual_update_check_requested = False
            if check_button is not None:
                check_button.disabled = False
                check_button.content = "Kiểm tra ngay"
            self._update_text.value = (
                f"Có bản cập nhật v{candidate.manifest.release_version}"
            )
            self._update_btn.visible = True
            self._update_btn.disabled = False
            self._update_btn.content = "Cập nhật"
            self._update_btn.icon = None
            self._update_progress.visible = False
            self._update_banner.visible = True
            if getattr(self, "_update_manual_download", False):
                self._update_text.value = "Có bản cập nhật"
                self._update_btn.content = "Mở trang tải"
            elif getattr(self, "_update_ready", False):
                self._update_text.value = "Đã tải xong bản cập nhật"
                self._update_btn.content = "Cài đặt"
        elif event.kind in {UpdateEventKind.DOWNLOADING, UpdateEventKind.DOWNLOAD_PROGRESS}:
            if event.kind is UpdateEventKind.DOWNLOADING:
                self._diag_breadcrumb("update_download", "started", "github")
            if candidate is not None:
                self._update_text.value = (
                    f"Đang tải v{candidate.manifest.release_version} · {int((event.progress or 0) * 100)}%"
                )
            self._update_btn.disabled = True
            self._update_ready = False
            self._update_manual_download = False
            self._update_btn.content = "Đang tải"
            self._update_banner.visible = True
            self._update_progress.visible = True
            self._update_progress.value = event.progress or 0.0
        elif event.kind is UpdateEventKind.READY_TO_INSTALL:
            self._diag_breadcrumb("update_download", "succeeded", "github")
            self._update_btn.disabled = False
            self._update_ready = True
            self._update_manual_download = False
            self._update_btn.content = "Cài đặt"
            self._update_text.value = "Đã tải xong bản cập nhật"
            self._update_progress.visible = False
            self._update_banner.visible = True
        elif event.kind is UpdateEventKind.MANUAL_DOWNLOAD_REQUIRED:
            self._diag_breadcrumb("update_download", "succeeded", "github")
            self._update_btn.disabled = False
            self._update_ready = False
            self._update_manual_download = True
            self._update_btn.content = "Mở trang tải"
            self._update_text.value = "Có bản cập nhật"
            self._update_progress.visible = False
            self._update_banner.visible = True
        elif event.kind is UpdateEventKind.VERIFYING:
            self._update_text.value = "Đang xác minh bản cập nhật…"
            self._update_btn.content = "Đang xác minh"
            self._update_btn.disabled = True
            self._update_progress.visible = True
            self._update_progress.value = None
        elif event.kind is UpdateEventKind.INSTALLING:
            self._diag_breadcrumb("update_install", "started", "os")
            self._update_text.value = "Đang mở trình cài đặt…"
            self._update_btn.content = "Đang mở"
            self._update_btn.disabled = True
        elif event.kind is UpdateEventKind.INSTALL_PERMISSION_REQUIRED:
            self._update_ready = True
            self._update_manual_download = False
            self._update_banner.visible = True
            self._update_text.value = "Cho phép cài ứng dụng, rồi quay lại và nhấn Cài đặt."
            self._update_btn.content = "Cài đặt"
            self._update_btn.disabled = False
            self._update_progress.visible = False
        elif event.kind is UpdateEventKind.INSTALL_FAILED:
            self._diag_breadcrumb("update_install", "failed", "os")
            self._update_manual_download = False
            self._update_banner.visible = True
            self._update_text.value = _update_install_error_message(event.message)
            self._update_btn.content = "Thử lại"
            self._update_open_release_page = event.message in {
                "installed_signer_mismatch", "manifest_signer_mismatch",
                "apk_signature_invalid", "release_metadata_invalid",
                "package_mismatch", "release_version_mismatch", "invalid_apk",
                "installer strategy is not allowed",
                "strategy_not_allowed",
                "installer_strategy_not_allowed",
                "installer is unavailable on this platform",
                "platform_unavailable",
            }
            self._update_ready = (
                not self._update_open_release_page
                and event.message not in {
                    "source_unavailable",
                    "Windows installer is unavailable",
                    "installer_unavailable",
                    "sha256_mismatch",
                    "size_mismatch",
                }
            )
            if self._update_open_release_page:
                self._update_btn.content = "Bản phát hành"
            self._update_btn.disabled = False
            self._update_progress.visible = False
        elif event.kind is UpdateEventKind.FAILED:
            if candidate is None:
                self._diag_breadcrumb("update_check", "failed", "github")
                should_notify = self._manual_update_check_requested
                self._manual_update_check_requested = False
                if check_button is not None:
                    check_button.disabled = False
                    check_button.content = "Kiểm tra ngay"
                if should_notify:
                    self._show_snackbar(
                        "Không thể kiểm tra cập nhật. "
                        "Hãy kiểm tra mạng và thử lại.",
                        ft.Icons.ERROR_OUTLINE_ROUNDED,
                        C.CRITICAL,
                    )
            else:
                self._update_ready = False
                self._update_manual_download = False
                self._diag_breadcrumb("update_download", "failed", "github")
                should_notify = self._update_banner.visible
                self._manual_update_check_requested = False
                if check_button is not None:
                    check_button.disabled = False
                    check_button.content = "Kiểm tra ngay"
                self._update_btn.disabled = False
                self._update_btn.content = "Thử lại"
                self._update_progress.visible = False
                self._update_text.value = (
                    "Không thể tải hoặc xác minh "
                    f"v{candidate.manifest.release_version}. Hãy thử lại."
                )
                if "insufficient_space" in event.message:
                    self._update_text.value = "Không đủ dung lượng. Hãy giải phóng bộ nhớ rồi thử lại."
                elif "storage_unavailable" in event.message:
                    self._update_text.value = "Không truy cập được bộ nhớ tải về. Hãy thử lại."
                elif (
                    "timed out" in event.message.lower()
                    or "verification_timeout" in event.message.lower()
                ):
                    self._update_text.value = "Xác minh bản cập nhật bị quá thời gian. Hãy thử lại."
                if should_notify:
                    self._show_snackbar(
                        "Không thể tải hoặc xác minh bản cập nhật",
                        ft.Icons.ERROR_OUTLINE_ROUNDED,
                        C.CRITICAL,
                    )
        elif event.kind is UpdateEventKind.CANCELLED:
            self._update_ready = False
            self._update_manual_download = False
            self._update_text.value = "Đã hủy tải bản cập nhật"
            self._diag_breadcrumb("update_download", "cancelled", "user")
            self._update_btn.disabled = False
            self._update_btn.content = "Cập nhật"
            self._update_progress.visible = False
        elif event.kind is UpdateEventKind.INSTALL_LAUNCHED:
            self._diag_breadcrumb("update_install", "succeeded", "os")
            self._update_text.value = "Đã mở trình cài đặt"
            self._update_btn.content = "Mở lại"
            self._update_btn.disabled = False
            self._update_banner.visible = platform_utils.IS_ANDROID
        status_setter = getattr(self.settings_view, "set_update_status", None)
        if callable(status_setter) and candidate is not None:
            status_setter(
                self._update_text.value,
                progress=self._update_progress.value,
                show_progress=self._update_progress.visible,
                action_label=self._update_btn.content if not self._update_btn.disabled else None,
                can_cancel=bool(cancel_button and cancel_button.visible),
            )
        elif callable(status_setter):
            if event.kind is UpdateEventKind.CHECKING:
                status_setter("Đang kiểm tra cập nhật...")
            elif event.kind is UpdateEventKind.UP_TO_DATE:
                status_setter("Bạn đang dùng phiên bản mới nhất")
            elif event.kind is UpdateEventKind.FAILED:
                status_setter("Không thể kiểm tra cập nhật. Hãy kiểm tra mạng và thử lại.")
        self.page.update()
        if event.kind is UpdateEventKind.INSTALL_LAUNCHED and platform_utils.IS_WINDOWS:
            # Close asyncio coordinators and Flet UI hosts on the event loop
            # BEFORE offloading blocking thread/socket teardown via to_thread.
            self._prepare_async_shutdown()
            await asyncio.to_thread(self._shutdown_for_exit)
            try:
                await self.page.window.destroy()
            except RuntimeError as exc:
                if "Session closed" not in str(exc):
                    raise

    def _show_update_confirmation(self) -> None:
        if not self._get_distribution_profile().supports_github_self_update:
            return
        if platform_utils.IS_WINDOWS:
            affirmative = "Cài đặt và thoát"
            content = "Ứng dụng sẽ đóng để cài bản cập nhật."
        elif platform_utils.IS_ANDROID and getattr(
            self, "_update_manual_download", False
        ):
            affirmative = "Mở trang tải APK"
            content = (
                "Bản cập nhật này đổi định danh gói ứng dụng nên cần tải và cài thủ công "
                "một lần từ GitHub Releases. UTHelper chỉ mở trang phát hành sau khi bạn xác nhận."
            )
        elif platform_utils.IS_ANDROID:
            affirmative = "Mở trình cài đặt"
            content = "Mở trình cài đặt Android cho bản cập nhật đã tải?"
        else:
            affirmative = "Mở trang tải IPA"
            content = (
                "Tải IPA trên máy tính, sau đó ký lại bằng cùng Apple ID và bundle ID "
                "qua Sideloadly/AltStore. Chứng thực cá nhân có thể cần được làm mới "
                "định kỳ. UTHelper chỉ mở trang phát hành sau khi bạn xác nhận."
            )

        modal_token = _new_modal_occlusion_token()

        def cancel(_event):
            _call_modal_occlusion("modal_dialog", False, token=modal_token)
            self.page.pop_dialog()

        def confirm(_event):
            _call_modal_occlusion("modal_dialog", False, token=modal_token)
            self.page.pop_dialog()
            coordinator = getattr(self, "_update_coordinator", None)
            if coordinator is not None:
                coordinator.confirm_install()

        dialog = ft.AlertDialog(
            modal=False,
            title=ft.Text("Xác nhận cập nhật"),
            content=ft.Text(content),
            actions=[
                ft.TextButton(
                    "Hủy", on_click=cancel, autofocus=True,
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
                ),
                ft.TextButton(
                    affirmative, on_click=confirm,
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            bgcolor=C.BG,
            on_dismiss=lambda _e: _call_modal_occlusion(
                "modal_dialog", False, token=modal_token
            ),
        )
        _call_modal_occlusion("modal_dialog", True, token=modal_token)
        self.page.show_dialog(dialog)


    def _on_settings_saved(self):
        from gui.core.theme import load_theme_from_settings, set_page_theme

        old_username = str(self._settings_identity[0] or "").strip() if getattr(self, "_settings_identity", None) else ""
        new_identity = (settings.UTH_USERNAME, settings.UTH_PASSWORD)
        credentials_changed = new_identity != self._settings_identity
        new_username = str(settings.UTH_USERNAME or "").strip()
        account_switched_or_logged_out = (
            old_username.casefold() != new_username.casefold()
        ) or not new_username
        new_milestones = tuple(settings.NOTIFY_MILESTONES_MINUTES or [])
        milestones_changed = new_milestones != self._notification_milestones_snapshot
        self._notification_milestones_snapshot = new_milestones
        new_class_schedule_snapshot = (
            bool(settings.NOTIFY_CLASS_SCHEDULE_ENABLED),
            tuple(settings.NOTIFY_CLASS_SCHEDULE_LEAD_HOURS),
            bool(settings.NOTIFY_DND_ENABLE),
            int(settings.NOTIFY_DND_START),
            int(settings.NOTIFY_DND_END),
        )
        class_schedule_settings_changed = (
            new_class_schedule_snapshot != self._notification_class_schedule_snapshot
        )
        self._notification_class_schedule_snapshot = new_class_schedule_snapshot
        if credentials_changed:
            # Credentials are the only settings change that requires immediate
            # network reload and an account-specific cache namespace.
            # Delegate to the unified teardown helper so every cache, token,
            # epoch, WebView, and native background state stays consistent.
            self._teardown_account_session(
                new_identity,
                reset_moodle_and_caches=True,
                account_switched_or_logged_out=account_switched_or_logged_out,
                schedule_refresh_trigger="credentials_changed",
                trigger_android_logout=True,
                clear_ui=True,
                mark_logged_out=True,
            )

        load_theme_from_settings()
        set_page_theme(self.page)
        self._rebuild_colors()
        self._needs_reload = credentials_changed
        coordinator = getattr(self, "_sync_coordinator", None)
        if coordinator is not None and not credentials_changed:
            coordinator.wake()
        update_coordinator = getattr(self, "_update_coordinator", None)
        if update_coordinator is not None:
            update_coordinator.set_automatic_enabled(settings.AUTO_UPDATE_ENABLED)
        if (
            self._android_background
            and self._android_background.available
            and not credentials_changed
        ):
            async def _apply_android_settings():
                token, token_origin = self._native_background_credentials()
                await self._android_background.configure(
                    token, token_origin=token_origin
                )
                needs_exact_alarm = (
                    not is_demo_account(settings.UTH_USERNAME)
                    and (
                        (
                            milestones_changed
                            and any(
                                int(value) <= 60
                                for value in (settings.NOTIFY_MILESTONES_MINUTES or [])
                            )
                        )
                        or (
                            class_schedule_settings_changed
                            and bool(settings.NOTIFY_CLASS_SCHEDULE_ENABLED)
                        )
                    )
                )
                if needs_exact_alarm:
                    diagnostics = await self._android_background.diagnostics()
                    self.orchestrator.android_background_diagnostics = diagnostics
                    if not bool(diagnostics.get("exact_alarm_allowed", False)):
                        await self._android_background.request_exact_alarm_access()

            self._safe_run_task(_apply_android_settings)
        # A credentials change already scheduled a clear_only reconcile above
        # and refresh("credentials_changed") will rebuild the new account's
        # reminders from its own snapshot; a non-clear run here would fetch
        # tomorrow with the old generation and could overwrite the fresh
        # account's schedule.
        if class_schedule_settings_changed and not credentials_changed:
            schedule_state = getattr(self, "_today_schedule_state", None)
            schedule_snapshot = (
                schedule_state.snapshot.sessions
                if schedule_state is not None and schedule_state.snapshot is not None
                else ()
            )
            self._safe_run_task(
                self._reconcile_class_schedule_notifications, schedule_snapshot
            )
        self._show_snackbar("Đã lưu cài đặt", ft.Icons.SAVE_ROUNDED, C.SAFE)
    def _on_activity_status_changed(
        self,
        url: str,
        new_status: str,
        expected_account_epoch: int | None = None,
    ):
        """Callback từ DetailView khi trạng thái nộp bài được cập nhật hoặc thay đổi."""
        current_epoch = getattr(self, "_account_epoch", 0)
        if (
            expected_account_epoch is not None
            and expected_account_epoch != current_epoch
        ):
            logger.info(
                "Discarding stale activity status update for %s (epoch %s != %s)",
                url,
                expected_account_epoch,
                current_epoch,
            )
            return
        new_status = normalize_submission_status(new_status)
        updated = False
        import time
        with self._data_lock:
            # Ghi nhận thay đổi thủ công của người dùng kèm timestamp
            self._pending_updates[url] = (time.time(), new_status)
            
            new_data = []
            for item in self.all_data:
                if item.get("url") == url:
                    if item.get("submission_status") != new_status:
                        new_item = dict(item)
                        new_item["submission_status"] = new_status
                        
                        details = dict(new_item.get("details", {}))
                        status_data = dict(details.get("status_data", {}))
                        status_data["Trạng thái nộp bài"] = new_status
                        details["status_data"] = status_data
                        new_item["details"] = details
                        
                        new_data.append(new_item)
                        updated = True
                    else:
                        new_data.append(item)
                else:
                    new_data.append(item)
            
            if updated:
                self.all_data = new_data
            data_copy = list(self.all_data)

        if updated:
            # 1. Invalidate cache chi tiết trong orchestrator để bắt buộc tải lại đầy đủ khi mở lại
            try:
                self.orchestrator.invalidate_detail_cache(url)
            except Exception:
                import logging as _fb_log
                _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)
            
            # 2. Lưu cache đĩa để không bị mất khi restart app (offload khỏi event loop)
            data_cache = self._data_cache
            if data_cache is not None:
                async def _persist_local(cache=data_cache, payload=data_copy):
                    try:
                        await asyncio.to_thread(cache.save_local_activities, payload)
                    except Exception:
                        logger.debug("Ignored async local activity persist error", exc_info=True)

                if self._safe_run_task(_persist_local) is None:
                    try:
                        data_cache.save_local_activities(data_copy)
                    except Exception:
                        logger.debug("Ignored sync local activity persist error", exc_info=True)
            self._update_footer()

            # A successful/manual submission must cancel every remaining local
            # deadline reminder immediately instead of waiting for the next sync.
            if not is_demo_account(settings.UTH_USERNAME):
                mobile_bridge = getattr(self, "_android_background", None)
                if mobile_bridge is not None and mobile_bridge.available:
                    self._safe_run_task(
                        mobile_bridge.import_activities,
                        data_copy,
                        authoritative=True,
                    )
                elif getattr(self, "notifier", None) is not None:
                    self._safe_run_task(
                        self.notifier.reconcile_schedules, data_copy
                    )

    def _show_detail(self, data: dict):
        self._dismiss_desktop_filter_menu()
        self._safe_run_task(self._show_detail_async, data)

    async def _show_detail_async(self, data: dict):
        request_epoch = getattr(self, "_detail_request_epoch", 0) + 1
        self._detail_request_epoch = request_epoch

        def request_is_current() -> bool:
            return request_epoch == getattr(self, "_detail_request_epoch", 0)

        # Ghi nhận nếu màn hình chi tiết được mở từ Lịch để phục vụ quay lại (back-navigation)
        self._detail_from_calendar = self.calendar_view.visible
        if not self._detail_from_calendar and not getattr(
            getattr(self, "courses_view", None), "visible", False
        ):
            self._set_selected_activity(data)
        self._diag_screen("detail")
        self._diag_breadcrumb("navigation", "succeeded", "user", screen="detail")
        self.view_manager.show_detail_loading(data)
        try:
            full_data = await asyncio.to_thread(self.orchestrator.fetch_full_details, data)
            if not request_is_current():
                return
            details_map = full_data.get("details") if isinstance(full_data, dict) else None
            if isinstance(details_map, dict):
                desc_html = details_map.get("description_html", "")
                if desc_html:
                    from gui.core.utils import pre_cache_description_images
                    cached_html = await asyncio.to_thread(
                        pre_cache_description_images,
                        desc_html,
                    )
                    if not request_is_current():
                        return
                    # Cached detail dictionaries are shared with dashboard
                    # hydration. Prepare the view without mutating that cache.
                    full_data = dict(full_data)
                    full_data["details"] = dict(details_map)
                    full_data["details"]["description_html"] = cached_html
            if not request_is_current():
                return
            self.view_manager.show_detail_data(full_data)
        except Exception:
            logger.exception("Không thể tải chi tiết activity")
            if request_is_current():
                self.view_manager.show_detail_error(data)

    def _pulse_cards_once(self, cards_snapshot: list, pulse_high: bool):
        # Tự động tạo bóng đổ nhấp nháy mờ nhẹ (pulse shadow) sử dụng mã màu C.CRITICAL của theme hiện tại
        alpha = 0.27 if pulse_high else 0.08
        blur = 16 if pulse_high else 6
        spread = 1.2 if pulse_high else 0.2
        shadow = [ft.BoxShadow(
            spread_radius=spread,
            blur_radius=blur,
            color=ft.Colors.with_opacity(alpha, C.CRITICAL),
            offset=ft.Offset(0, 0)
        )]
        changed = False
        for card in cards_snapshot:
            if getattr(card, "_is_critical_active", False) and not getattr(
                card, "_selected", False
            ):
                card.shadow = shadow
                changed = True
        if changed:
            try:
                self.page.update()
            except Exception:
                pass

    def _is_window_hidden_or_minimized(self) -> bool:
        """Return True when the desktop window is hidden in tray or minimized."""
        window = getattr(getattr(self, "page", None), "window", None)
        if window is None:
            return False
        if getattr(window, "visible", True) is False:
            return True
        if getattr(window, "minimized", False) is True:
            return True
        return False

    async def _pulse_critical_cards_loop_async(self):
        """Smoothly pulses red glow shadow for critical deadline cards."""
        pulse_state = False
        while self._page_alive.is_set():
            await asyncio.sleep(1.2)
            if not self._page_alive.is_set():
                break
            if self._is_window_hidden_or_minimized():
                continue
            if not getattr(self, "dashboard", None) or not self.dashboard.visible:
                continue
            with self._cards_lock:
                cards_snapshot = list(self.active_cards or [])
            has_critical = any(getattr(c, "_is_critical_active", False) for c in cards_snapshot)
            if not has_critical:
                continue
            pulse_state = not pulse_state
            self._pulse_cards_once(cards_snapshot, pulse_state)

    def _countdown_cards_once(self, cards_snapshot: list):
        if not cards_snapshot:
            return
        changed = False
        for card in cards_snapshot:
            update_countdown = getattr(card, "update_countdown", None)
            if callable(update_countdown) and update_countdown():
                changed = True
        if changed:
            self.page.update()

    def _refresh_visible_countdown_views(self, changed: bool = False):
        if self.detail_view.visible:
            if self.detail_view.refresh_countdown():
                self.page.update()
            with self._cards_lock:
                cards_snapshot = list(self.active_cards)
            for card in cards_snapshot:
                update_countdown = getattr(card, "update_countdown", None)
                if callable(update_countdown):
                    update_countdown()
        elif self.calendar_view.visible:
            with self._data_lock:
                calendar_snapshot = list(self.all_data)
            self.calendar_view.update_data(calendar_snapshot)
            self.page.update()
        elif self.dashboard.visible:
            schedule_changed = self._today_schedule_component.refresh_time_state()
            if changed:
                self._render_cards_only()
                return
            with self._cards_lock:
                cards_snapshot = list(self.active_cards)
            self._countdown_cards_once(cards_snapshot)
            if schedule_changed:
                self.page.update()

    async def _countdown_loop_async(self):
        while self._page_alive.is_set():
            # Adaptive interval: faster when critical items exist
            has_critical = any(
                getattr(c, '_is_critical_active', False)
                for c in (self.active_cards or [])
            )
            interval = 15 if has_critical else 60
            await asyncio.sleep(interval)
            if not self._page_alive.is_set():
                break
            if self._is_window_hidden_or_minimized():
                continue
            try:
                changed = self._refresh_activity_time_state()
                self._refresh_visible_countdown_views(changed)
            except Exception:
                logger.warning("Countdown refresh failed", exc_info=True)

    @staticmethod
    def _run_shutdown_step(label: str, step) -> None:
        try:
            step()
        except Exception:
            logger.debug("Ignored shutdown step error (%s)", label, exc_info=True)

    def _prepare_async_shutdown(self) -> None:
        """Close asyncio coordinators and Flet UI hosts on the event loop thread."""
        page_alive = getattr(self, "_page_alive", None)
        if page_alive is not None:
            self._run_shutdown_step("page_alive.clear", page_alive.clear)
        self._prefetch_epoch = getattr(self, "_prefetch_epoch", 0) + 1
        prefetch_cancel = getattr(self, "_prefetch_cancel_event", None)
        if prefetch_cancel is not None:
            self._run_shutdown_step("prefetch_cancel.set", prefetch_cancel.set)
        detail_warm_cancel = getattr(self, "_card_detail_warm_cancel_event", None)
        if detail_warm_cancel is not None:
            self._run_shutdown_step("detail_warm_cancel.set", detail_warm_cancel.set)
        coordinator = getattr(self, "_sync_coordinator", None)
        if coordinator is not None:
            self._run_shutdown_step("sync_coordinator.close", coordinator.close)
        schedule_coordinator = getattr(self, "_today_schedule_coordinator", None)
        if schedule_coordinator is not None:
            self._run_shutdown_step(
                "today_schedule_coordinator.close", schedule_coordinator.close
            )
        close_wv = getattr(self, "_close_app_webview", None)
        if callable(close_wv):
            self._run_shutdown_step(
                "close_app_webview", lambda: close_wv(update_page=False)
            )

    def _shutdown_for_exit(self) -> None:
        lock = getattr(self, "_exit_cleanup_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._exit_cleanup_lock = lock
        with lock:
            if getattr(self, "_exit_cleanup_done", False):
                return
            self._exit_cleanup_done = True

        view_manager = getattr(self, "view_manager", None)
        if view_manager is not None:
            cancel_nav = getattr(
                view_manager, "cancel_pending_settings_navigation", None
            )
            if callable(cancel_nav):
                self._run_shutdown_step(
                    "view_manager.cancel_pending_settings_navigation", cancel_nav
                )
        else:
            settings_view = getattr(self, "settings_view", None)
            cancel_load = getattr(settings_view, "cancel_pending_load", None)
            if callable(cancel_load):
                self._run_shutdown_step(
                    "settings_view.cancel_pending_load", cancel_load
                )

        activation_broker = getattr(self, "activation_broker", None)
        if activation_broker is not None:
            close_broker = getattr(activation_broker, "close", None)
            if callable(close_broker):
                self._run_shutdown_step(
                    "activation_broker.close",
                    lambda: close_broker(timeout_seconds=1.0),
                )

        tray = getattr(self, "tray", None)
        if tray is not None:
            close_tray = getattr(tray, "close", None)
            if callable(close_tray):
                self._run_shutdown_step(
                    "tray.close", lambda: close_tray(timeout_seconds=1.0)
                )

        page_alive = getattr(self, "_page_alive", None)
        if page_alive is not None:
            self._run_shutdown_step("page_alive.clear", page_alive.clear)
        self._prefetch_epoch = getattr(self, "_prefetch_epoch", 0) + 1
        prefetch_cancel = getattr(self, "_prefetch_cancel_event", None)
        if prefetch_cancel is not None:
            self._run_shutdown_step("prefetch_cancel.set", prefetch_cancel.set)
        detail_warm_cancel = getattr(self, "_card_detail_warm_cancel_event", None)
        if detail_warm_cancel is not None:
            self._run_shutdown_step("detail_warm_cancel.set", detail_warm_cancel.set)

        coordinator = getattr(self, "_sync_coordinator", None)
        if coordinator is not None:
            close_sync = getattr(coordinator, "close", None)
            if callable(close_sync):
                self._run_shutdown_step("sync_coordinator.close", close_sync)

        schedule_coordinator = getattr(self, "_today_schedule_coordinator", None)
        if schedule_coordinator is not None:
            close_sched = getattr(schedule_coordinator, "close", None)
            if callable(close_sched):
                self._run_shutdown_step(
                    "today_schedule_coordinator.close", close_sched
                )

        update_coordinator = getattr(self, "_update_coordinator", None)
        if update_coordinator is not None:
            shutdown_upd = getattr(update_coordinator, "shutdown", None)
            if callable(shutdown_upd):
                self._run_shutdown_step(
                    "update_coordinator.shutdown",
                    lambda: shutdown_upd(timeout_seconds=1.0),
                )

        close_wv = getattr(self, "_close_app_webview", None)
        if callable(close_wv):
            self._run_shutdown_step(
                "close_app_webview", lambda: close_wv(update_page=False)
            )

        courses_coord = getattr(self, "_courses_coordinator", None)
        if courses_coord is not None:
            close_courses = getattr(courses_coord, "close", None)
            if callable(close_courses):
                self._run_shutdown_step("courses_coordinator.close", close_courses)

        # Close NotificationManager & WindowsNotifier scheduler threads cleanly.
        seen_notifiers: set[int] = set()
        for notif_mgr in (
            getattr(self, "notifier", None),
            getattr(getattr(self, "orchestrator", None), "notifier", None),
        ):
            if notif_mgr is None or id(notif_mgr) in seen_notifiers:
                continue
            seen_notifiers.add(id(notif_mgr))
            close_mgr = getattr(notif_mgr, "close", None)
            if callable(close_mgr):
                self._run_shutdown_step("notifier.close", close_mgr)
            for channel in getattr(notif_mgr, "notifiers", ()) or ():
                close_ch = getattr(channel, "close", None)
                if callable(close_ch):
                    self._run_shutdown_step("notifier_channel.close", close_ch)

        orchestrator = getattr(self, "orchestrator", None)
        for client_attr in ("client", "thnn_client"):
            client = getattr(orchestrator, client_attr, None)
            close_client = getattr(client, "close", None)
            if callable(close_client):
                self._run_shutdown_step(f"orchestrator.{client_attr}.close", close_client)

    def _on_disconnect(self, e):
        self._shutdown_for_exit()

def main(page: ft.Page):
    """Stub main function to support Flet Preview on this file directly."""
    # Apply compatibility shims if running directly
    try:
        from gui.flet_compat import patch_flet
        patch_flet()
    except Exception:
        import logging as _fb_log
        _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)
    AppController(page)

if __name__ == "__main__":
    ft.run(main=main, assets_dir=os.path.join(_project_root, "assets"))

