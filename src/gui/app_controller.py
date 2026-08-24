import os
import sys

# Patch path for direct execution / Flet preview compatibility
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import flet as ft
from datetime import datetime
from core.time_utils import parse_datetime
import asyncio
import logging
import platform_utils
from platform_utils import detect_platform
from notifiers.manager import NotificationManager
import threading
from pathlib import Path

from packaging.version import InvalidVersion, Version

from core.data_orchestrator import DataOrchestrator
from core.moodle_service import MoodleService
from core.moodle_sites import moodle_site_from_origin
from core.portal_client import PortalClient
from core.today_schedule import (
    ScheduleLoadStatus,
    TodayScheduleCache,
    TodayScheduleCoordinator,
    TodayScheduleViewState,
)
from core.use_cases.submission_workflow import SubmissionWorkflow
from core.activity_time_policy import ActivityTimePolicy
from core.activity_status import normalize_submission_status
from core.sync_coordinator import ActivitySyncCoordinator, FetchOutcome, parse_timestamp
from core.update_checker import GitHubReleaseClient, VerifiedDownloader
from core.update_coordinator import UpdateCoordinator, UpdateEvent, UpdateEventKind
from core.update_models import LaunchResult, ReleasePackage
from config import get_sync_interval_minutes, save_settings, settings
from platform_utils.update_packages import (
    DownloadedPackageVerifier,
    detect_runtime_target,
)

from gui.core.theme import C, refresh_semantic_control_colors, semantic_palette
from core.filter_service import FilterService
from gui.components.activity_card import ActivityCard
from gui.components.detail_view import DetailView
from gui.components.settings_view import SettingsView
from gui.components.calendar_view import CalendarView
from gui.components.grade_overview_view import GradeOverviewView
from gui.components.today_schedule_panel import TodaySchedulePanel
from gui.components.sync_status_indicator import SyncStatusIndicator
from gui.components.desktop_window_header import build_desktop_window_header
from gui.components.adaptive_filter_menu import (
    AdaptiveFilterMenu,
    FilterMenuOption,
)
from gui.view_manager import ViewManager
from gui.controllers.startup_visibility import (
    is_autostart_launch,
    should_hide_startup_window,
)
from gui.controllers.autostart_settings import apply_initial_autostart_default
from gui.controllers.refresh_coordinator import (
    build_prefetched_dataset,
    card_detail_prefetch_candidates,
    status_hydration_candidates,
)
from gui.controllers.window_activator import WindowActivator

logger = logging.getLogger(__name__)

_SCROLL_TO_TOP_SHOW_OFFSET = 240.0
_SCROLL_TO_TOP_HIDE_OFFSET = 96.0
_SCROLL_TO_TOP_DURATION_MS = 300
_DASHBOARD_SCROLL_EVENT_INTERVAL_MS = 32

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


def _android_release_build_number(version: str) -> int:
    """Derive the Flet Android build number from a strict X.Y.Z version."""
    try:
        parsed = Version(version)
    except InvalidVersion as exc:
        raise ValueError("invalid Android release version") from exc
    if (
        str(parsed) != version
        or len(parsed.release) != 3
        or parsed.pre
        or parsed.post
        or parsed.dev
        or parsed.local
        or any(part < 0 or part > 999 for part in parsed.release)
    ):
        raise ValueError("Android release version must be numeric X.Y.Z")
    major, minor, patch = parsed.release
    return major * 1_000_000 + minor * 1_000 + patch


class _UnavailablePackageLauncher:
    def launch(self, _path: Path, _package: ReleasePackage) -> LaunchResult:
        return LaunchResult(False, "installer is unavailable on this platform")

    def cancel(self) -> None:
        return None


class _AndroidPackageLauncher:
    """Thin adapter from the synchronous coordinator to the native bridge."""

    PACKAGE_ID = "com.uthelper.uthelper"

    def __init__(self, bridge, task_runner, release_version_provider, result_sink=None):
        self._bridge = bridge
        self._task_runner = task_runner
        self._release_version_provider = release_version_provider
        self._result_sink = result_sink

    def launch(self, path: Path, package: ReleasePackage) -> LaunchResult:
        if (
            package.platform != "android"
            or package.package_type != "apk"
            or self._bridge is None
            or not self._bridge.available
        ):
            return LaunchResult(False, "Android installer bridge is unavailable")
        try:
            version_code = _android_release_build_number(
                self._release_version_provider()
            )
            task = self._task_runner(self._install, Path(path), package, version_code)
        except (RuntimeError, TypeError, ValueError):
            logger.warning("Android installer scheduling failed", exc_info=True)
            return LaunchResult(False, "Android installer scheduling failed")
        return LaunchResult(task is not None, "Android installer scheduled")

    async def _install(
        self,
        path: Path,
        package: ReleasePackage,
        version_code: int,
    ) -> None:
        try:
            result = await self._bridge.install_update(
                str(path),
                package.sha256,
                package.size,
                self.PACKAGE_ID,
                version_code,
                package.certificate_fingerprint,
            )
        except Exception:
            logger.warning("Android installer bridge failed", exc_info=True)
            result = {"status": "bridge_failed"}
        if self._result_sink is not None:
            self._result_sink(result)
        if result.get("status") not in {"installer_opened", "permission_required"}:
            logger.warning("Android installer rejected update: %s", result.get("status"))

    def cancel(self) -> None:
        if self._bridge is None or not self._bridge.available:
            return
        try:
            self._task_runner(self._bridge.cancel_update)
        except (RuntimeError, TypeError):
            logger.debug("Android installer cancellation scheduling failed", exc_info=True)


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
        self._page_alive = threading.Event()
        self._page_alive.set()
        
        self.orchestrator = DataOrchestrator()
        from core.data_cache import DataCache
        cache_namespace = (
            f"{settings.MOODLE_BASE_URL}|{settings.UTH_USERNAME}"
            if settings.UTH_USERNAME
            else None
        )
        self._data_cache = DataCache(namespace=cache_namespace)
        schedule_namespace = (
            f"{settings.PORTAL_API_BASE}|{settings.UTH_USERNAME}"
            if settings.UTH_USERNAME
            else f"{settings.PORTAL_API_BASE}|anonymous"
        )
        self._portal_client = PortalClient(settings.PORTAL_API_BASE)
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
        
        self._prefetch_cancel_event = threading.Event()
        self._prefetch_epoch = 0
        self._card_detail_warm_cancel_event = threading.Event()
        self._detail_request_epoch = 0
        self._android_background = None
        self._dashboard_scroll_offset = 0.0
        self._scroll_to_top_visible = False
        self._scroll_to_top_in_progress = False

        self._init_window()
        self._update_candidate = None
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
        )
        
        # Events
        self.page.on_disconnect = self._on_disconnect
        self.page.on_keyboard_event = self._on_keyboard_event
        
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
        if not needs_initial_sync:
            hydration_started = self._schedule_cached_detail_hydration(
                has_cached_snapshot
            )
            if not hydration_started:
                self._schedule_card_detail_warmup()
        self._safe_run_task(self._today_schedule_coordinator.run)
        if self._android_background and self._android_background.available:
            self._safe_run_task(self._initialize_android_background)
        self._tray_balloon_shown = False  # H-01: only show once

        self._update_coordinator.start()

        if not settings.UTH_USERNAME or not settings.UTH_PASSWORD:
            self._safe_run_task(self._show_login_dialog)
        elif needs_initial_sync:
            self._safe_run_task(self._load_data_async, "startup")
        # With credentials, the coordinator waits until cache age + the
        # one-minute startup grace are due. Manual refresh remains immediate.

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

    async def _show_login_dialog(self):
        from gui.components.login_dialog import show_login_dialog
        self._diag_screen("login")
        self._diag_breadcrumb("login", "started", "user", screen="login")
        async def _on_login_success():
            self._diag_breadcrumb("login", "succeeded", "moodle", screen="home")
            self._diag_screen("home")
            # UX-5: Show success snackbar after dialog closes
            self._show_snackbar("Đăng nhập thành công! Đang tải dữ liệu...", ft.Icons.CHECK_CIRCLE_ROUNDED, C.SAFE)
            self._portal_client.clear_token()
            self._safe_run_task(self._today_schedule_coordinator.refresh, "login")
            await self._load_data_async()
        await show_login_dialog(self.page, self.orchestrator, _on_login_success)

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

    def _create_update_coordinator(self) -> UpdateCoordinator:
        target = detect_runtime_target()
        if target.platform == "windows":
            from platform_utils.windows_update import (
                WindowsPackageLauncher,
                WindowsPackageVerifier,
            )

            verifier = WindowsPackageVerifier()
            launcher = WindowsPackageLauncher()
        elif target.platform == "android":
            verifier = DownloadedPackageVerifier()
            launcher = _AndroidPackageLauncher(
                self._android_background,
                self._safe_run_task,
                lambda: self._update_candidate.manifest.release_version,
                self._on_android_update_result,
            )
        else:
            verifier = DownloadedPackageVerifier()
            launcher = _UnavailablePackageLauncher()
        return UpdateCoordinator(
            GitHubReleaseClient(),
            VerifiedDownloader(),
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
        async def _open():
            await self.page.launch_url(url)

        return self._safe_run_task(_open) is not None

    def _check_updates_now(self) -> None:
        self._diag_breadcrumb("update_check", "started", "github")
        self._manual_update_check_requested = True
        self._update_coordinator.check_now()

    def _on_android_update_result(self, result: dict) -> None:
        status = result.get("status")
        if status == "installer_opened":
            self._diag_breadcrumb("update_install", "succeeded", "android")
            self._update_banner.visible = False
            self.page.update()
        elif status == "permission_required":
            self._update_banner.visible = True
            self._update_btn.disabled = False
            self._update_btn.content = "Mở trình cài đặt"
            self._show_snackbar(
                "Hãy cho phép cài ứng dụng rồi nhấn Cập nhật lại",
                ft.Icons.SECURITY_ROUNDED,
                C.WARNING,
            )
            self.page.update()
        else:
            self._update_banner.visible = True
            self._update_btn.disabled = False
            self._update_btn.content = "Thử lại"
            self._show_snackbar(
                "Android đã từ chối gói cập nhật",
                ft.Icons.ERROR_OUTLINE_ROUNDED,
                C.CRITICAL,
            )
            self.page.update()

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
        
        # Chỉ dành cho Desktop: Cửa sổ kích thước cố định hỗ trợ khay hệ thống
        if not _is_mobile:
            self.page.window.width        = 420
            self.page.window.height       = 720
            self.page.window.max_width    = 420
            self.page.window.min_width    = 420
            self.page.window.always_on_top = settings.ALWAYS_ON_TOP
            self.page.window.resizable    = False
            self.page.window.maximizable  = False
            self.page.window.minimizable  = True
            self.page.window.title_bar_hidden = True
            self.page.window.title_bar_buttons_hidden = True
            self.page.window.icon         = "icon.ico"  # Icon của app, để ở thư mục assets
            self.page.window.prevent_close = True
            self.page.window.on_event = self._on_window_event
        
        self.page.title               = "UTHelper"
        self.page.bgcolor             = C.BG
        self.page.padding             = 0
        self.page.spacing             = 0
        self.page.theme_mode          = ft.ThemeMode.DARK

        # Đồng bộ ColorScheme theme của Flet page với các giá trị C của chúng ta
        from gui.core.theme import set_page_theme
        set_page_theme(self.page)
        
        # Khay hệ thống & Thông báo (tự động nhận biết nền tảng)
        tray_ready = False
        if _is_windows:
            from gui.tray import TrayApp
            self.tray = TrayApp(
                self.page, on_show=self.window_activator.request_show
            )
            tray_ready = self.tray.setup()
            self.notifier = NotificationManager(self.tray)
        else:
            self.tray = None
            self.notifier = NotificationManager()
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
                try:
                    await self.page.window.destroy()
                except RuntimeError as exc:
                    # The host can close its Flet session between the close
                    # event and the native destroy RPC. That is a completed
                    # shutdown, not an application failure.
                    if str(exc) != "Session closed":
                        raise
                    logger.debug("Window session closed before destroy completed")

    def _minimize_window(self, _event=None) -> None:
        """Minimize from the custom desktop header without hiding to tray."""
        self.page.window.minimized = True
        self.page.update()

    async def _exit_from_window_header(self, _event=None) -> None:
        """The custom close button is an explicit application-exit action."""
        try:
            await self.page.window.destroy()
        except RuntimeError as exc:
            if str(exc) != "Session closed":
                raise
            logger.debug("Window session closed before custom exit completed")

    async def _on_keyboard_event(self, e):
        """H-04: Escape key to go back from Detail/Settings/Calendar views."""
        if e.key == "Escape":
            await self._navigate_back()

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
        schedule_component = getattr(self, "_today_schedule_component", None)
        if schedule_component is not None and schedule_component.handle_back():
            return True
        if self.settings_view.visible:
            await self.settings_view._handle_back(None)
            return True
        elif self.detail_view.visible:
            await self._close_detail()
            return True
        elif self.calendar_view.visible:
            await self._close_calendar()
            return True
        elif self.grade_overview_view.visible:
            await self._close_grades()
            return True
        return False

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

        self.search_field = ft.TextField(
            hint_text="Tìm hoạt động hoặc môn học",
            hint_style=ft.TextStyle(size=12, color=C.TEXT_SECONDARY),
            prefix_icon=ft.Icons.SEARCH,
            border_radius=10,
            border_color=C.BORDER,
            focused_border_color=C.ACCENT,
            bgcolor=C.SURFACE,
            text_size=12,
            height=44,
            expand=True,
            content_padding=ft.Padding.only(left=12, right=10, top=10, bottom=10),
            on_change=self._on_search,
        )

    def _init_header_controls(self):
        self.calendar_btn = ft.IconButton(
            ft.Icons.CALENDAR_MONTH_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=20,
            tooltip="Lịch",
            on_click=lambda _event: self._run_dashboard_header_action(
                self._toggle_calendar
            ),
        )
        self.refresh_btn = ft.IconButton(
            ft.Icons.REFRESH_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=20,
            tooltip="Làm mới",
            on_click=lambda _event: self._run_dashboard_header_action(
                self._load_data_async
            ),
        )
        self.settings_btn = ft.IconButton(
            ft.Icons.SETTINGS_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=20,
            tooltip="Cài đặt",
            on_click=lambda _event: self._run_dashboard_header_action(
                self._show_settings
            ),
        )
        self.grades_btn = ft.IconButton(
            ft.Icons.INSIGHTS_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=20,
            tooltip="Bảng điểm",
            on_click=lambda _event: self._run_dashboard_header_action(
                self._toggle_grades
            ),
        )

    def _run_dashboard_header_action(self, handler) -> None:
        self._dismiss_desktop_filter_menu()
        self._safe_run_task(handler)

    def _init_banner_and_states(self):
        self._update_icon = ft.Icon(
            ft.Icons.SYSTEM_UPDATE_ROUNDED, size=16, color=C.UPDATE
        )
        self._update_text = ft.Text(
            "Có phiên bản mới!",
            size=12,
            color=C.UPDATE,
            weight=ft.FontWeight.W_500,
            expand=True,
        )
        self._update_progress = ft.ProgressBar(
            value=0,
            width=0,
            height=3,
            color=C.UPDATE,
            bgcolor=C.BORDER,
            visible=False,
        )
        self._update_btn = ft.TextButton(
            "Cập nhật",
            icon=ft.Icons.DOWNLOAD_ROUNDED,
            style=ft.ButtonStyle(color=C.UPDATE),
            on_click=self._request_update_download,
        )
        self._update_banner = ft.Container(
            content=ft.Column([
                ft.Row([self._update_icon, self._update_text, self._update_btn], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
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
        self.empty_state  = ft.Container(
            content=ft.Column(controls=[
                self._empty_icon,
                ft.Container(height=4),
                self._empty_title,
                self._empty_subtitle,
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
                    ft.Container(content=ft.Row([self.search_field]), padding=ft.Padding.only(left=16, right=16, top=6, bottom=0)),
                    ft.Container(
                        content=self._build_dashboard_filter_row(),
                        padding=ft.Padding.only(left=16, right=12),
                    ),
                ], spacing=4,
            ),
            padding=ft.Padding.only(left=0, right=0, top=4, bottom=10),
            bgcolor=C.BG,
        )

        self._content_area = ft.Container(
            content=ft.Column(controls=[
                ft.Stack(controls=[
                    ft.Column(controls=[self.cards_column, self.empty_state, self.error_state], spacing=0, expand=True),
                    self._scroll_to_top_button,
                ], expand=True),
            ], spacing=8, expand=True),
            padding=ft.Padding.only(left=4, right=4, bottom=8),
            expand=True, clip_behavior=ft.ClipBehavior.NONE,
        )

        self._footer = ft.Container(
            content=ft.Row(controls=[
                self.footer_critical, self.footer_warning, self.footer_safe, self.footer_overdue,
            ], spacing=12, alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=C.SURFACE, padding=ft.Padding.symmetric(vertical=10),
            border=ft.Border.only(top=ft.BorderSide(1, C.BORDER)),
        )

        self.dashboard = ft.Column(
            controls=[
                header,
                self._update_banner,
                self._filter_container,
                self._content_area,
                self._footer,
            ],
            spacing=0, expand=True,
            animate_opacity=ft.Animation(200, ft.AnimationCurve.EASE_IN_OUT),
        )

        self.detail_view   = DetailView(self.page, on_close=lambda: self._safe_run_task(self._close_detail), get_client=lambda: self.orchestrator.client, on_status_changed=self._on_activity_status_changed, submission_workflow_factory=self._submission_workflow_factory)
        self.calendar_view = CalendarView(
            self.page,
            on_close=lambda: self._safe_run_task(self._close_calendar),
            on_open_detail=lambda data: self._safe_run_task(self._show_detail_async, data),
        )
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
            on_check_update=self._check_updates_now,
        )
        self.grade_overview_view = GradeOverviewView(
            on_close=lambda: self._safe_run_task(self._close_grades),
            on_refresh=lambda: self._safe_run_task(lambda: self._show_grades(force_refresh=True)),
            page=self.page,
        )

        for _view in (self.detail_view, self.settings_view, self.calendar_view, self.grade_overview_view):
            _view.offset = ft.Offset(0, 0)
            _view.opacity = 0.0
            _view.animate_opacity = ft.Animation(180, ft.AnimationCurve.EASE_OUT)

        self.tab_switcher = ft.AnimatedSwitcher(
            content=self.dashboard,
            transition=ft.AnimatedSwitcherTransition.FADE,
            duration=160,
            reverse_duration=160,
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

        main_stack_controls = [self.tab_switcher, self.detail_switcher]
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
            view_host=main_stack,
            overlays=overlays,
            tab_switcher=self.tab_switcher,
            detail_switcher=self.detail_switcher,
            empty_detail=self._empty_detail,
        )
        self._rendered_palette = semantic_palette()
        # Replace the startup splash atomically after the real UI is complete.
        self.page.controls.clear()
        if platform_utils.UI_IS_MOBILE:
            self._app_shell = ft.SafeArea(content=main_stack, expand=True)
        else:
            self._window_header = self._build_desktop_window_header()
            self._app_shell = ft.Column(
                controls=[self._window_header, main_stack],
                spacing=0,
                expand=True,
            )
        self.page.add(self._app_shell)

    def _build_desktop_window_header(self) -> ft.Container:
        self._app_title.size = 13
        self._app_title.weight = ft.FontWeight.W_700
        return build_desktop_window_header(
            on_minimize=self._minimize_window,
            on_close=lambda _: self._safe_run_task(
                self._exit_from_window_header
            ),
            title_control=self._app_title,
            version_control=self._version_badge,
        )

    def _build_dashboard_header(self) -> ft.Container:
        action_buttons = ft.Row(
            controls=[
                self.calendar_btn,
                self.grades_btn,
                self.refresh_btn,
                self.settings_btn,
            ],
            spacing=0,
        )
        if platform_utils.UI_IS_MOBILE:
            title_group = ft.Row(
                controls=[self._app_title, self._version_badge],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            content = ft.Column(
                controls=[
                    ft.Row(
                        controls=[title_group, action_buttons],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    self._sync_status.control,
                ],
                spacing=4,
            )
            padding = ft.Padding.only(left=16, right=8, top=25, bottom=8)
        else:
            self._sync_status.control.expand = True
            self.status_text.no_wrap = True
            self.status_text.overflow = ft.TextOverflow.ELLIPSIS
            content = ft.Row(
                controls=[self._sync_status.control, action_buttons],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            padding = ft.Padding.only(left=16, right=8, top=4, bottom=8)
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

        global_position = getattr(event, "global_position", None)
        local_position = getattr(event, "local_position", None)
        if global_position is not None and local_position is not None:
            anchor_left = float(global_position.x - local_position.x)
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
        viewport_width = float(getattr(self.page, "width", 420) or 420)
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
                trigger_label="Tất cả",
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

    def _refresh_ui(self):
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

        # P2/P6: Contextual empty state messaging
        if is_empty:
            has_filter = (self.active_urgency != "all" or self.active_type != "all"
                          or self.active_course != "all" or self.active_search)
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
                current_cards[i].update_data(item, on_tap=self._show_detail)
                current_cards[i].visible = True
            else:
                # Tạo thẻ mới
                new_card = ActivityCard(item, on_tap=self._show_detail, animate=False)
                new_card.visible = True
                current_cards.append(new_card)
        
        # Ẩn các thẻ thừa
        for i in range(len(filtered_items), len(current_cards)):
            current_cards[i].visible = False
            
        render_cards = current_cards[:len(filtered_items)]
        self._reusable_cards = current_cards
        render_cards = [self.today_schedule_panel] + render_cards
            
        with self._cards_lock:
            self.active_cards = render_cards
        
        self.cards_column.controls = render_cards
        self.page.update()

    def _request_today_schedule(self) -> None:
        coordinator = self._today_schedule_coordinator
        if coordinator.state.status is ScheduleLoadStatus.ERROR:
            self._safe_run_task(coordinator.refresh, "retry")
        else:
            self._safe_run_task(coordinator.ensure_today)

    def _on_today_schedule_state(self, state: TodayScheduleViewState) -> None:
        self._today_schedule_state = state
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

    def _update_footer(self):
        self._refresh_ui()

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
        self.empty_state.visible = len(filtered_items) == 0 and not self._is_loading
        self.error_state.visible = False

        if not hasattr(self, '_reusable_cards'):
            self._reusable_cards = []

        current_cards = self._reusable_cards
        for i, item in enumerate(filtered_items):
            if i < len(current_cards):
                current_cards[i].update_data(item, on_tap=self._show_detail)
                current_cards[i].visible = True
            else:
                new_card = ActivityCard(item, on_tap=self._show_detail, animate=False)
                new_card.visible = True
                current_cards.append(new_card)

        for i in range(len(filtered_items), len(current_cards)):
            current_cards[i].visible = False

        render_cards = current_cards[:len(filtered_items)]
        self._reusable_cards = current_cards
        render_cards = [self.today_schedule_panel] + render_cards

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

    def _on_search(self, e):
        self.active_search = (e.control.value or "").strip()
        # Debounce: 150ms - fast enough to feel instant, prevents excessive renders
        if hasattr(self, '_search_task') and self._search_task:
            self._search_task.cancel()
        async def _delayed_render():
            await asyncio.sleep(0.15)
            self._render_cards_only()
        self._search_task = self._safe_run_task(_delayed_render)

    async def _check_grades_background(self):
        """PERF-OPT: Check grade changes in background (non-blocking)."""
        try:
            grade_changes = await asyncio.to_thread(self.orchestrator.check_grade_changes)
            if grade_changes and hasattr(self, 'notifier') and self.notifier:
                await self.notifier.dispatch_grade_alert(grade_changes)
                msg = f"\U0001f4ca {len(grade_changes)} \u0111i\u1ec3m m\u1edbi: "
                msg += ", ".join(f"{c.item_name} ({c.new_grade})" for c in grade_changes[:3])
                if len(grade_changes) > 3:
                    msg += f" +{len(grade_changes) - 3} kh\u00e1c"
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(msg, color=C.ON_SAFE),
                    bgcolor=C.SAFE,
                    duration=5000,
                )
                self.page.snack_bar.open = True
                self.page.update()
        except Exception as e:
            logger.debug("Grade check background failed (non-critical): %s", e)

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
        if prefetch_epoch is None:
            prefetch_epoch = getattr(self, "_prefetch_epoch", 0)
        worker_cap = 2 if platform_utils.IS_MOBILE else 4
        workers = max(1, min(settings.PREFETCH_WORKERS, worker_cap))

        if hasattr(self.orchestrator, "prefetch_activity_statuses"):
            cache = await asyncio.to_thread(
                self.orchestrator.prefetch_activity_statuses,
                activities,
                workers,
                lambda: self._prefetch_cancel_event.is_set(),
            )
        else:
            await asyncio.to_thread(
                self.orchestrator.prefetch_all_details,
                activities,
                workers,
                lambda: self._prefetch_cancel_event.is_set(),
                False,
            )
            cache = self.orchestrator.get_cached_details_snapshot()

        if (
            self._prefetch_cancel_event.is_set()
            or self._is_loading
            or prefetch_epoch != getattr(self, "_prefetch_epoch", 0)
        ):
            return
        with self._data_lock:
            data_copy = list(self.all_data)
        data_copy = build_prefetched_dataset(data_copy, cache)
        with self._data_lock:
            self.all_data = data_copy
        self._update_footer()
        persisted = await asyncio.to_thread(
            self._data_cache.save_local_activities, data_copy
        )
        if not persisted:
            logger.warning("Không thể lưu snapshot trạng thái activity đã enrich")
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

    def _schedule_card_detail_warmup(self) -> bool:
        """Schedule one ordered card-cache pass without affecting sync UI."""
        if not settings.MOODLE_WS_TOKEN:
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
        if not has_cached_snapshot or not settings.MOODLE_WS_TOKEN:
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

    async def _initialize_android_background(self):
        """Attach native worker settings and prefer its cache after app restart."""
        bridge = self._android_background
        if not bridge or not bridge.available:
            return
        try:
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
        return await coordinator.request(trigger, force=trigger == "manual")

    async def _perform_data_sync(self, trigger: str = "timer"):
        if self._is_loading: return
        sync_source = "user" if trigger == "manual" else "scheduler"
        self._diag_breadcrumb("refresh", "started", sync_source)
        self._is_loading = True
        self._prefetch_epoch = getattr(self, "_prefetch_epoch", 0) + 1
        self._prefetch_cancel_event.set()
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
        self._set_sync_status("busy", "Đang cập nhật dữ liệu...")
        self.empty_state.visible  = False
        self.error_state.visible  = False
        self.page.update()

        # Hiển thị cache cũ ngay lập tức (nếu có) trong khi chờ server
        if not self.all_data:  # Chỉ load cache khi chưa có data
            cached_data, cached_at = self._data_cache.load()
            if cached_data:
                with self._data_lock:
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
            if not outcome.can_commit:
                raise RuntimeError(outcome.error or "Incomplete Moodle activity snapshot")
            result = outcome.activities
            with self._data_lock:
                self.all_data = result or []
            
            cache = self.orchestrator.get_cached_details_snapshot()
            with self._data_lock:
                data_copy = list(self.all_data)
            
            # Áp dụng Local Overrides Smart Merge
            now = time.time()
            # Dọn dẹp pending updates cũ (> 5 phút) để tránh rò rỉ bộ nhớ
            self._pending_updates = {
                k: v for k, v in self._pending_updates.items() if now - v[0] < 300
            }
            
            for i, item in enumerate(data_copy):
                url = item.get("url")
                
                # Trích xuất thông tin cache chi tiết trước
                if url and url in cache:
                    enriched = cache[url]
                    data_copy[i] = {
                        **item,
                        "type": enriched.get("type", item.get("type", "other")),
                        "course": enriched.get("course", item.get("course", "")),
                        "submission_status": enriched.get("submission_status", "unknown"),
                        "details": enriched.get("details", {}),
                        "deadline": enriched.get("deadline", item.get("deadline")),
                        "is_open": enriched.get("is_open", item.get("is_open")),
                        "urgency": enriched.get("urgency", item.get("urgency")),
                    }
                
                # Thực hiện Smart Merge với các thay đổi thủ công đang chờ của người dùng
                if url and url in self._pending_updates:
                    action_time, new_status = self._pending_updates[url]
                    if action_time > fetch_start_time:
                        # Thao tác nộp bài diễn ra SAU khi bắt đầu fetch, ưu tiên bảo toàn trạng thái UI mới
                        data_copy[i]["submission_status"] = new_status
                        
                        details = dict(data_copy[i].get("details", {}))
                        status_data = dict(details.get("status_data", {}))
                        status_data["Trạng thái nộp bài"] = new_status
                        details["status_data"] = status_data
                        data_copy[i]["details"] = details
                        logger.info(f"[SmartMerge] Đã bảo toàn trạng thái '{new_status}' của {url}")
            
            # Determine data source for status display
            sum(1 for x in data_copy if x.get('source') == 'ws_api')
            data_copy.sort(key=lambda x: (
                0 if x.get("urgency") == "critical" else 1 if x.get("urgency") == "warning" else 2,
                x.get("deadline", "")
            ))
            # C4: Pre-parse deadlines + pre-compute lowercase for hot-path filter
            for item in data_copy:
                dl = item.get("deadline", "")
                if dl and "_deadline_dt" not in item:
                    item["_deadline_dt"] = parse_datetime(dl)
                if "_title_lower" not in item:
                    item["_title_lower"] = str(item.get("title", "")).lower()
                if "_course_lower" not in item:
                    item["_course_lower"] = str(item.get("course", "")).lower()
            with self._data_lock:
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
            snapshot_persisted = await asyncio.to_thread(
                self._data_cache.save_server_snapshot, data_copy
            )
            if snapshot_persisted:
                saved_snapshot = await asyncio.to_thread(
                    self._data_cache.load_snapshot
                )
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
            native_schedule_result = None
            if self._android_background and self._android_background.available:
                try:
                    token, token_origin = self._native_background_credentials()
                    await self._android_background.configure(
                        token, token_origin=token_origin
                    )
                    native_schedule_result = await self._android_background.import_activities(
                        data_copy,
                        authoritative=True,
                    )
                    self.orchestrator.android_background_diagnostics = (
                        await self._android_background.diagnostics()
                    )
                except Exception as exc:
                    logger.warning("Android native snapshot import failed: %s", exc)
            # Bắn thông báo thông minh cho người dùng
            if hasattr(self, 'notifier') and self.notifier:
                try:
                    with self._data_lock:
                        dispatch_copy = list(self.all_data)
                    if native_schedule_result is None:
                        dispatch_result = await self.notifier.dispatch(dispatch_copy)
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

                # PERF-OPT: Grade check moved to background task to not block main data display
                self._safe_run_task(self._check_grades_background)

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
            self._diag_breadcrumb("refresh", "failed", "moodle")
            logger.exception(f"[Load] Lỗi: {exc}")
            try:
                self._data_cache.record_failed_attempt(str(exc))
            except Exception:
                import logging as _fb_log
                _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)
            with self._data_lock:
                self.all_data = []
            
            # Thử dùng cache khi offline
            cached_data, cached_at = self._data_cache.load()
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
            self.refresh_btn.disabled = False
            self._is_loading          = False
            self.page.update()

    async def _close_detail(self):
        # asyncio.to_thread cannot cancel blocking HTTP. Invalidate its UI
        # continuation so a late response cannot reopen/overwrite this view.
        self._detail_request_epoch = getattr(self, "_detail_request_epoch", 0) + 1
        from_calendar = getattr(self, '_detail_from_calendar', False)
        self._detail_from_calendar = False
        await self.view_manager.close_detail(from_calendar)

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

    async def _close_calendar(self):
        """Return from calendar to dashboard."""
        await self.view_manager.close_calendar()
        self._diag_screen("home")

    async def _show_settings(self):
        if hasattr(self, "_diag_screen"):
            self._diag_screen("settings")
            self._diag_breadcrumb(
                "navigation", "succeeded", "user", screen="settings"
            )
        await self.view_manager.show_settings()

    async def _toggle_grades(self):
        """Toggle the grade overview panel."""
        if self.grade_overview_view.visible:
            await self._close_grades()
        else:
            await self._show_grades()

    async def _show_grades(self, force_refresh: bool = False):
        """Show grade overview panel and load academic transcript/radar data."""
        self._diag_screen("grades")
        self._diag_breadcrumb("navigation", "succeeded", "user", screen="grades")
        self.view_manager.show_grades_loading()

        # Load academic radar data from daily cache / Portal in background thread
        try:
            from core.academic_radar import AcademicGradeService
            service = getattr(self, "_academic_grade_service", None)
            if service is None:
                service = AcademicGradeService()
                self._academic_grade_service = service

            username = settings.UTH_USERNAME or settings.MOODLE_USERNAME
            password = settings.UTH_PASSWORD or settings.MOODLE_PASSWORD

            semesters = await asyncio.to_thread(
                service.load_academic_data,
                username=username,
                password=password,
                force_refresh=force_refresh,
            )
            if semesters:
                profiles = service.get_radar_profiles(semesters)
                self.view_manager.show_academic_radar_data(semesters, profiles)
            else:
                # Fallback to Moodle grades if Portal returned no semesters
                userid = await asyncio.to_thread(
                    getattr(self.orchestrator, "_get_userid", lambda: None)
                )
                if userid:
                    courses_grades, grade_items = await asyncio.to_thread(
                        self.orchestrator.moodle_service.fetch_all_grades, userid
                    )
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
            logger.error("Academic grades fetch failed: %s", e)
            self.view_manager.show_academic_radar_data([], [])

    async def _close_grades(self):
        """Close grade overview and return to dashboard."""
        await self.view_manager.close_grades()
        self._diag_screen("home")

    def _rebuild_colors(self):
        """Repaint ALL existing controls with current C values for live theme switching.

        Because controls store hardcoded color strings at init time,
        we must explicitly reassign every color property when the theme changes.
        """
        from gui.core.theme import C as _C
        current_palette = semantic_palette()
        theme_roots = [self.dashboard]
        window_header = getattr(self, "_window_header", None)
        if window_header is not None:
            theme_roots.append(window_header)
        refresh_semantic_control_colors(
            theme_roots, self._rendered_palette, current_palette
        )
        self._rendered_palette = current_palette

        # Page
        self.page.bgcolor = _C.BG

        self._header_container.bgcolor = _C.BG
        self._app_title.color = _C.TEXT_PRIMARY
        self._version_text.color = _C.TEXT_SECONDARY
        self._version_badge.border = ft.Border.all(1, _C.BORDER)

        # Status text
        self.status_text.color = _C.TEXT_SECONDARY

        # Filter area
        self._filter_container.bgcolor = _C.BG
        self.search_field.bgcolor = _C.SURFACE
        self.search_field.border_color = _C.BORDER
        self.search_field.focused_border_color = _C.ACCENT
        self.search_field.color = _C.TEXT_PRIMARY

        # Adaptive filters rebuild from their option models instead of relying
        # on brittle control-tree indexes.
        self.urgency_filter_menu.refresh_theme(
            self._urgency_filter_options(
                {option.key: option.count or 0 for option in self.urgency_filter_menu.options}
            )
        )
        self.type_filter_menu.refresh_theme(
            self._type_filter_options(
                {option.key: option.count or 0 for option in self.type_filter_menu.options}
            )
        )
        self.course_filter_menu.refresh_theme(
            self._course_filter_options(
                {option.key: option.count or 0 for option in self.course_filter_menu.options}
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
        self._footer.bgcolor = _C.SURFACE
        self._footer.border = ft.Border.only(top=ft.BorderSide(1, _C.BORDER))
        # Footer counter colors are set dynamically by _refresh_counts,
        # but update the base colors here too
        self.footer_critical.color = _C.CRITICAL if self.footer_critical.value else _C.BORDER
        self.footer_warning.color = _C.WARNING if self.footer_warning.value else _C.BORDER
        self.footer_safe.color = _C.SAFE if self.footer_safe.value else _C.BORDER
        self.footer_overdue.color = _C.CRITICAL if self.footer_overdue.value else _C.BORDER

        self._content_area.bgcolor = _C.BG

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

        # Icon buttons
        buttons_to_update = [self.calendar_btn, self.grades_btn, self.refresh_btn, self.settings_btn]

        for btn in buttons_to_update:
            btn.icon_color = _C.TEXT_SECONDARY

        self._sync_status.refresh_theme()

        # Update banner is a semantic elevated surface, not a fixed brown card.
        self._update_banner.bgcolor = _C.SURFACE_ELEVATED
        self._update_banner.border = ft.Border.all(1, _C.UPDATE)
        self._update_icon.color = _C.UPDATE
        self._update_text.color = _C.UPDATE
        self._update_progress.color = _C.UPDATE
        self._update_progress.bgcolor = _C.BORDER
        self._update_btn.style = ft.ButtonStyle(color=_C.UPDATE)

        # Settings view bgcolor
        self.settings_view.bgcolor = _C.BG

        # Sub-views theme update propagation
        for view_attr in ('calendar_view', 'detail_view', 'grade_overview_view'):
            view_obj = getattr(self, view_attr, None)
            if view_obj and hasattr(view_obj, 'update_theme'):
                try:
                    view_obj.update_theme()
                except Exception as e:
                    import logging
                    logging.getLogger("UTHelper").error(f"Failed to update theme for {view_attr}: {e}", exc_info=True)

        self.page.update()


    async def _close_settings(self):
        self.settings_view.cancel_pending_load()
        from gui.core.theme import load_theme_from_settings, set_page_theme
        load_theme_from_settings()
        set_page_theme(self.page)

        # Rebuild all colors from the saved theme
        self._rebuild_colors()

        # Recalculate urgency dynamically using new settings thresholds
        from datetime import datetime
        with self._data_lock:
            data_copy = list(self.all_data)
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
        with self._data_lock:
            self.all_data = data_copy
        
        # Toggle visibility - no full rebuild needed (fixes white flash)
        await self.view_manager.close_settings()
        self._diag_screen("home")
        
        self._update_footer()
        
        if getattr(self, '_needs_reload', False):
            self._needs_reload = False
            self._safe_run_task(self._load_data_async)
        # _update_footer() already calls _refresh_ui() which calls page.update()
        # No extra page.update() needed

    def _show_snackbar(self, msg: str, icon=ft.Icons.CHECK_CIRCLE_ROUNDED, color=C.SAFE):
        """UX: Show a brief toast-like notification."""
        sb = ft.SnackBar(
            content=ft.Row([
                ft.Icon(icon, color=color, size=16),
                ft.Text(msg, size=13, color=C.TEXT_PRIMARY),
            ], spacing=8),
            bgcolor=C.SURFACE,
            duration=2500,
        )
        overlay = getattr(self.page, "overlay", None)
        if overlay is not None:
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
    def _on_test_tray(self, mock_type="critical"):
        dummy = self._test_notification_base(mock_type)
        from notifiers.windows import WindowsNotifier
        WindowsNotifier().notify([dummy])

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
        from notifiers.telegram import TelegramNotifier
        TelegramNotifier().notify([dummy])

    def _on_test_discord(self, mock_type="critical"):
        dummy = self._test_notification_base(mock_type)
        from notifiers.discord import DiscordNotifier
        DiscordNotifier().notify([dummy])

    def _on_test_mail(self, mock_type="critical"):
        dummy = self._test_notification_base(mock_type)
        from notifiers.email import EmailNotifier
        EmailNotifier().notify([dummy])

    def _on_update_event(self, event: UpdateEvent) -> None:
        self._safe_run_task(self._apply_update_event, event)

    def _request_update_download(self, _event=None) -> None:
        candidate = getattr(self, "_update_candidate", None)
        if candidate is not None:
            self._update_coordinator.request_download(candidate)

    async def _apply_update_event(self, event: UpdateEvent) -> None:
        candidate = event.candidate
        if candidate is not None:
            self._update_candidate = candidate
        check_button = getattr(self.settings_view, "_check_update_btn", None)
        if event.kind is UpdateEventKind.CHECKING:
            self._diag_breadcrumb("update_check", "started", "github")
            if check_button is not None:
                check_button.disabled = True
                check_button.content = "Đang kiểm tra..."
        elif event.kind is UpdateEventKind.UP_TO_DATE:
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
            self._update_icon.name = ft.Icons.SYSTEM_UPDATE_ROUNDED
            self._update_icon.color = C.UPDATE
            self._update_text.value = (
                f"Phiên bản mới v{candidate.manifest.release_version} đã sẵn sàng!"
            )
            self._update_btn.visible = True
            self._update_btn.disabled = False
            self._update_btn.content = "Cập nhật"
            self._update_btn.icon = ft.Icons.DOWNLOAD_ROUNDED
            self._update_progress.visible = False
            self._update_banner.visible = True
        elif event.kind in {UpdateEventKind.DOWNLOADING, UpdateEventKind.DOWNLOAD_PROGRESS}:
            if event.kind is UpdateEventKind.DOWNLOADING:
                self._diag_breadcrumb("update_download", "started", "github")
            if candidate is not None:
                self._update_text.value = (
                    f"Đang tải và xác minh v{candidate.manifest.release_version}..."
                )
            self._update_btn.disabled = True
            self._update_btn.content = "Đang tải và xác minh..."
            self._update_progress.visible = True
            self._update_progress.value = event.progress or 0.0
        elif event.kind in {
            UpdateEventKind.READY_TO_INSTALL,
            UpdateEventKind.MANUAL_DOWNLOAD_REQUIRED,
        }:
            self._diag_breadcrumb("update_download", "succeeded", "github")
            self._update_btn.disabled = False
            self._update_btn.content = "Cập nhật"
            self._update_progress.visible = False
            self._show_update_confirmation()
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
                if should_notify:
                    self._show_snackbar(
                        "Không thể tải hoặc xác minh bản cập nhật",
                        ft.Icons.ERROR_OUTLINE_ROUNDED,
                        C.CRITICAL,
                    )
        elif event.kind is UpdateEventKind.CANCELLED:
            self._diag_breadcrumb("update_download", "cancelled", "user")
            self._update_btn.disabled = False
            self._update_btn.content = "Cập nhật"
            self._update_progress.visible = False
        elif event.kind is UpdateEventKind.INSTALL_LAUNCHED:
            if platform_utils.IS_ANDROID:
                self._diag_breadcrumb("update_install", "started", "android")
                self._update_text.value = "Đang mở trình cài đặt Android..."
                self._update_btn.content = "Đang mở..."
                self._update_btn.disabled = True
                self._update_banner.visible = True
            else:
                self._diag_breadcrumb("update_install", "succeeded", "os")
                self._update_banner.visible = False
        self.page.update()
        if event.kind is UpdateEventKind.INSTALL_LAUNCHED and platform_utils.IS_WINDOWS:
            await self.page.window.destroy()

    def _show_update_confirmation(self) -> None:
        if platform_utils.IS_WINDOWS:
            affirmative = "Cài đặt và thoát"
        elif platform_utils.IS_ANDROID:
            affirmative = "Mở trình cài đặt"
        else:
            affirmative = "Mở trang tải IPA"

        def cancel(_event):
            self.page.pop_dialog()

        def confirm(_event):
            self.page.pop_dialog()
            self._update_coordinator.confirm_install()

        content = (
            "Tải IPA trên máy tính, sau đó ký lại bằng cùng Apple ID và bundle ID "
            "qua Sideloadly/AltStore. Chứng thực cá nhân có thể cần được làm mới "
            "định kỳ. UTHelper chỉ mở trang phát hành sau khi bạn xác nhận."
            if not platform_utils.IS_WINDOWS and not platform_utils.IS_ANDROID
            else "UTHelper sẽ chỉ mở trình cài đặt sau khi bạn xác nhận."
        )
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Xác nhận cập nhật"),
            content=ft.Text(content),
            actions=[
                ft.TextButton("Hủy", on_click=cancel),
                ft.TextButton(affirmative, on_click=confirm),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            bgcolor=C.BG,
        )
        self.page.show_dialog(dialog)


    def _on_settings_saved(self):
        from gui.core.theme import load_theme_from_settings, set_page_theme

        new_identity = (settings.UTH_USERNAME, settings.UTH_PASSWORD)
        credentials_changed = new_identity != self._settings_identity
        new_milestones = tuple(settings.NOTIFY_MILESTONES_MINUTES or [])
        milestones_changed = new_milestones != self._notification_milestones_snapshot
        self._notification_milestones_snapshot = new_milestones
        if credentials_changed:
            # Credentials are the only settings change that requires immediate
            # network reload and an account-specific cache namespace.
            try:
                self.orchestrator.moodle_service.clear_all_caches()
                self.orchestrator.clear_detail_cache()
                self.orchestrator._userid_cache = None
                self.orchestrator.is_logged_in = False
            except Exception:
                import logging as _fb_log
                _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)
            from core.data_cache import DataCache

            cache_namespace = (
                f"{settings.MOODLE_BASE_URL}|{settings.UTH_USERNAME}"
                if settings.UTH_USERNAME
                else None
            )
            self._data_cache = DataCache(namespace=cache_namespace)
            schedule_namespace = (
                f"{settings.PORTAL_API_BASE}|{settings.UTH_USERNAME}"
                if settings.UTH_USERNAME
                else f"{settings.PORTAL_API_BASE}|anonymous"
            )
            self._portal_client.clear_token()
            self._today_schedule_coordinator.replace_cache(
                TodayScheduleCache(namespace=schedule_namespace)
            )
            self._today_schedule_state = TodayScheduleViewState(
                ScheduleLoadStatus.IDLE
            )
            self._today_schedule_component.set_state(self._today_schedule_state)
            self._safe_run_task(self._today_schedule_coordinator.ensure_today)
            self._settings_identity = new_identity

        load_theme_from_settings()
        set_page_theme(self.page)
        self._rebuild_colors()
        self._needs_reload = credentials_changed
        coordinator = getattr(self, "_sync_coordinator", None)
        if coordinator is not None:
            coordinator.wake()
        update_coordinator = getattr(self, "_update_coordinator", None)
        if update_coordinator is not None:
            update_coordinator.set_automatic_enabled(settings.AUTO_UPDATE_ENABLED)
        if self._android_background and self._android_background.available:
            if credentials_changed:
                # Prevent the previous account token/Room snapshot from being
                # used while the new credentials have not logged in yet.
                self._safe_run_task(self._android_background.logout)
            else:
                async def _apply_android_settings():
                    token, token_origin = self._native_background_credentials()
                    await self._android_background.configure(
                        token, token_origin=token_origin
                    )
                    if milestones_changed and any(
                        int(value) <= 60
                        for value in (settings.NOTIFY_MILESTONES_MINUTES or [])
                    ):
                        diagnostics = await self._android_background.diagnostics()
                        self.orchestrator.android_background_diagnostics = diagnostics
                        if not bool(diagnostics.get("exact_alarm_allowed", False)):
                            await self._android_background.request_exact_alarm_access()

                self._safe_run_task(_apply_android_settings)
        self._show_snackbar("Đã lưu cài đặt", ft.Icons.SAVE_ROUNDED, C.SAFE)
    def _on_activity_status_changed(self, url: str, new_status: str):
        """Callback từ DetailView khi trạng thái nộp bài được cập nhật hoặc thay đổi."""
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
            
            # 2. Lưu cache đĩa để không bị mất khi restart app
            try:
                self._data_cache.save_local_activities(data_copy)
            except Exception:
                import logging as _fb_log
                _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)
            self._update_footer()

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
        self._diag_screen("detail")
        self._diag_breadcrumb("navigation", "succeeded", "user", screen="detail")
        self.view_manager.show_detail_loading(data)
        try:
            full_data = await asyncio.to_thread(self.orchestrator.fetch_full_details, data)
            if not request_is_current():
                return
            if full_data and "details" in full_data:
                desc_html = full_data["details"].get("description_html", "")
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
                    full_data["details"] = dict(full_data["details"])
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
        crit_hex = C.CRITICAL.lstrip("#")
        alpha = "45" if pulse_high else "12"
        blur = 16 if pulse_high else 6
        spread = 1.2 if pulse_high else 0.2
        shadow = [ft.BoxShadow(
            spread_radius=spread,
            blur_radius=blur,
            color=f"#{alpha}{crit_hex}",
            offset=ft.Offset(0, 0)
        )]
        changed = False
        for card in cards_snapshot:
            if getattr(card, "_is_critical_active", False):
                card.shadow = shadow
                changed = True
        if changed:
            try:
                self.page.update()
            except Exception:
                pass

    async def _pulse_critical_cards_loop_async(self):
        """Smoothly pulses red glow shadow for critical deadline cards."""
        pulse_state = False
        while self._page_alive.is_set():
            await asyncio.sleep(1.2)
            if not self._page_alive.is_set():
                break
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
            if card.update_countdown():
                changed = True
        if changed:
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
            if not self._page_alive.is_set(): break
            try:
                changed = self._refresh_activity_time_state()
                if self.dashboard.visible:
                    schedule_changed = self._today_schedule_component.refresh_time_state()
                    if changed:
                        self._render_cards_only()
                        continue
                    with self._cards_lock:
                        cards_snapshot = list(self.active_cards)
                    self._countdown_cards_once(cards_snapshot)
                    if schedule_changed:
                        self.page.update()
                elif self.calendar_view.visible:
                    with self._data_lock:
                        calendar_snapshot = list(self.all_data)
                    self.calendar_view.update_data(calendar_snapshot)
                    self.page.update()
                elif self.detail_view.visible and self.detail_view.refresh_countdown():
                    self.page.update()
            except Exception:
                import logging as _fb_log
                _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _on_disconnect(self, e):
        view_manager = getattr(self, "view_manager", None)
        if view_manager is not None:
            view_manager.cancel_pending_settings_navigation()
        else:
            settings_view = getattr(self, "settings_view", None)
            if settings_view is not None:
                settings_view.cancel_pending_load()
        if self.activation_broker is not None:
            self.activation_broker.close(timeout_seconds=1.0)
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.close(timeout_seconds=1.0)
        self._page_alive.clear()
        self._prefetch_epoch = getattr(self, "_prefetch_epoch", 0) + 1
        self._prefetch_cancel_event.set()
        detail_warm_cancel = getattr(self, "_card_detail_warm_cancel_event", None)
        if detail_warm_cancel is not None:
            detail_warm_cancel.set()
        coordinator = getattr(self, "_sync_coordinator", None)
        if coordinator is not None:
            coordinator.close()
        schedule_coordinator = getattr(self, "_today_schedule_coordinator", None)
        if schedule_coordinator is not None:
            schedule_coordinator.close()
        update_coordinator = getattr(self, "_update_coordinator", None)
        if update_coordinator is not None:
            update_coordinator.shutdown(timeout_seconds=5.0)
        try:
            self.orchestrator.client.close()
        except Exception:
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

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

