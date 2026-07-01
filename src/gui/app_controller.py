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

from core.data_orchestrator import DataOrchestrator
from config import settings

from gui.core.theme import C
from core.filter_service import FilterService
from gui.components.activity_card import ActivityCard
from gui.components.detail_view import DetailView
from gui.components.settings_view import SettingsView
from gui.components.calendar_view import CalendarView
from gui.components.grade_overview_view import GradeOverviewView
from gui.view_manager import ViewManager

logger = logging.getLogger(__name__)

try:
    from main import __version__
    APP_VERSION = f"v{__version__}"
except ImportError:
    APP_VERSION = "v2.1.0"

def _save_setting(key: str, value):
    try:
        from config import settings, save_settings
        setattr(settings, key, value)
        save_settings()
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to save setting {key}: {e}")


class AppController:
    def __init__(self, page: ft.Page):
        self.page = page
        self._cards_lock = threading.Lock()
        self._data_lock = threading.Lock()
        self._page_alive = threading.Event()
        self._page_alive.set()
        
        self.orchestrator = DataOrchestrator()
        from core.data_cache import DataCache
        self._data_cache = DataCache()
        
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


        self._init_window()
        self._init_ui()
        
        # Events
        self.page.on_disconnect = self._on_disconnect
        self.page.on_keyboard_event = self._on_keyboard_event
        
        # Android back button - intercept to navigate within app instead of exiting
        if platform_utils.IS_MOBILE:
            self.page.on_view_pop = self._on_back_button
        self.page.run_task(self._pulse_loop_async)
        self.page.run_task(self._countdown_loop_async)
        self.page.run_task(self._auto_refresh_loop_async)
        self._tray_balloon_shown = False  # H-01: only show once
        
        # Check update in background
        from core.update_checker import check_for_update_async
        check_for_update_async(APP_VERSION, self._on_update_check)
        
        # Android: Start background scheduler for deadline checks via AlarmManager
        if platform_utils.IS_MOBILE and settings.BACKGROUND_CHECK_ANDROID:
            self.page.run_task(self._start_background_scheduler)
        
        if not settings.UTH_USERNAME or not settings.UTH_PASSWORD:
            self.page.run_task(self._show_login_dialog)
        else:
            self.page.run_task(self._load_data_async)

    async def _show_login_dialog(self):
        from gui.components.login_dialog import show_login_dialog
        async def _on_login_success():
            # UX-5: Show success snackbar after dialog closes
            self._show_snackbar("Đăng nhập thành công! Đang tải dữ liệu...", ft.Icons.CHECK_CIRCLE_ROUNDED, C.SAFE)
            await self._load_data_async()
        await show_login_dialog(self.page, self.orchestrator, _on_login_success)

    def _init_window(self):
        # Phát hiện nền tảng lúc runtime để xác định chính xác các cờ mobile/desktop
        detect_platform(self.page)
        # Đọc lại các cờ sau khi phát hiện lúc runtime (chúng có thể đã thay đổi)
        import platform_utils
        _is_mobile = platform_utils.IS_MOBILE
        _is_windows = platform_utils.IS_WINDOWS
        
        # Chỉ dành cho Desktop: Cửa sổ kích thước cố định hỗ trợ khay hệ thống
        if not _is_mobile:
            self.page.window.width        = 420
            self.page.window.height       = 720
            self.page.window.max_width    = 420
            self.page.window.min_width    = 420
            self.page.window.always_on_top = settings.ALWAYS_ON_TOP
            self.page.window.resizable    = False
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
        if _is_windows:
            from gui.tray import TrayApp
            self.tray = TrayApp(self.page)
            self.tray.setup()
            self.notifier = NotificationManager(self.tray)
        else:
            self.tray = None
            self.notifier = NotificationManager()
        
        # Chỉ tự ẩn xuống tray nếu app được Win gọi khởi động (có cờ --autostart)
        import sys
        if not _is_mobile and settings.START_MINIMIZED and "--autostart" in sys.argv:
            self.page.window.visible = False
        elif not _is_mobile:
            self.page.window.visible = True
            
        self.page.update()

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
                        pass
            else:
                await self.page.window.destroy()

    async def _on_keyboard_event(self, e):
        """H-04: Escape key to go back from Detail/Settings/Calendar views."""
        if e.key == "Escape":
            await self._navigate_back()

    async def _on_back_button(self, e):
        """Android back button handler - navigate within app instead of exiting."""
        handled = await self._navigate_back()
        if not handled:
            # On dashboard with nothing to go back to - minimize app (don't exit)
            try:
                self.page.window.close()
            except (AttributeError, TypeError):
                pass

    async def _navigate_back(self) -> bool:
        """Shared back-navigation logic. Returns True if a view was closed."""
        if self.settings_view.visible:
            await self._close_settings()
            return True
        elif self.detail_view.visible:
            await self._close_detail()
            return True
        elif self.calendar_view.visible:
            await self._close_calendar()
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
        self.status_text     = ft.Text("Đang khởi động...", size=11, color=C.TEXT_SECONDARY)
        self.footer_critical = ft.Text("", size=11, color=C.CRITICAL, weight=ft.FontWeight.W_600)
        self.footer_warning  = ft.Text("", size=11, color=C.WARNING,  weight=ft.FontWeight.W_600)
        self.footer_safe     = ft.Text("", size=11, color=C.SAFE,     weight=ft.FontWeight.W_600)
        self.footer_overdue  = ft.Text("", size=11, color=C.CRITICAL, weight=ft.FontWeight.W_600)
        self.loading_bar     = ft.ProgressBar(color=C.ACCENT, bgcolor=C.BORDER, visible=False)

        _URGENCY_CFG = [
            ("all",      "Mức độ",   C.TEXT_PRIMARY),
            ("critical", "Cấp bách", C.CRITICAL),
            ("warning",  "Sắp tới",  C.WARNING),
            ("safe",     "An toàn",  C.SAFE),
            ("overdue",  "Quá hạn",  C.CRITICAL),
        ]
        from gui.core.theme import _TYPE_COLORS
        _TYPE_CFG = [
            ("all",        "Loại",         C.TEXT_PRIMARY),
            ("quiz",       "Quiz",         _TYPE_COLORS["quiz"]),
            ("assignment", "Bài tập",      _TYPE_COLORS["assignment"]),
            ("attendance", "Điểm danh",    _TYPE_COLORS["attendance"]),
            ("open",       "Sắp mở",       _TYPE_COLORS["open"]),
            ("other",      "Sự kiện khác", C.TEXT_SECONDARY),
        ]

        self.urgency_popup, self._update_urgency_counts = self._make_filter_popup(
            _URGENCY_CFG, lambda key: self.page.run_task(self._set_urgency, key)
        )
        self.type_popup, self._update_type_counts = self._make_filter_popup(
            _TYPE_CFG, lambda key: self.page.run_task(self._set_type, key)
        )

        self.course_btn_label = ft.Text("Môn học", size=12, color=C.TEXT_PRIMARY, weight=ft.FontWeight.W_500, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.course_popup = ft.PopupMenuButton(
            content=ft.Container(
                content=ft.Row([
                    ft.Container(content=self.course_btn_label, width=50, height=20, alignment=ft.Alignment(-1, 0)),
                    ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=C.TEXT_SECONDARY)
                ], spacing=2, tight=True),
                bgcolor=C.SURFACE, border=ft.Border.all(1, C.BORDER), border_radius=10, padding=ft.Padding.symmetric(horizontal=8, vertical=8),
            ),
            items=[],
            menu_position=ft.PopupMenuPosition.UNDER,
            shape=ft.RoundedRectangleBorder(radius=10),
        )

        self._overdue_cb = ft.Checkbox(
            value=settings.INCLUDE_PAST_DUE,
            label="Quá hạn",
            label_style=ft.TextStyle(size=13, color=C.TEXT_SECONDARY),
            active_color=C.CRITICAL,
            check_color=C.BG,
            scale=1,
            on_change=lambda e: self.page.run_task(self._toggle_overdue, e)
        )

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
            on_click=lambda e: self.page.run_task(self._toggle_calendar),
        )
        self.refresh_btn = ft.IconButton(
            ft.Icons.REFRESH_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=20,
            tooltip="Làm mới",
            on_click=lambda e: self.page.run_task(self._load_data_async),
        )
        self.settings_btn = ft.IconButton(
            ft.Icons.SETTINGS_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=20,
            tooltip="Cài đặt",
            on_click=lambda e: self.page.run_task(self._show_settings),
        )
        self.grades_btn = ft.IconButton(
            ft.Icons.INSIGHTS_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=20,
            tooltip="Bảng điểm",
            on_click=lambda e: self.page.run_task(self._toggle_grades),
        )

        self._notification_badge_text = ft.Text("0", size=8, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD)
        self._notification_badge = ft.Container(
            content=self._notification_badge_text,
            bgcolor=ft.Colors.RED,
            border_radius=8,
            width=16, height=16,
            alignment=ft.Alignment(0, 0),
            visible=False,
        )
        self._notification_icon = ft.Stack(
            controls=[
                ft.IconButton(
                    icon=ft.Icons.NOTIFICATIONS_OUTLINED,
                    icon_color=C.TEXT_SECONDARY,
                    icon_size=20,
                    tooltip="Thông báo",
                    on_click=lambda e: self.page.run_task(self._on_notification_click, e),
                ),
                ft.Container(
                    content=self._notification_badge,
                    alignment=ft.Alignment(1, -1),
                    right=2, top=2,
                ),
            ],
            width=40, height=40,
        )

    def _init_banner_and_states(self):
        self._update_icon = ft.Icon(ft.Icons.SYSTEM_UPDATE_ROUNDED, size=16, color="#FCD34D")
        self._update_text = ft.Text("Có phiên bản mới!", size=12, color="#FCD34D", weight=ft.FontWeight.W_500, expand=True)
        self._update_progress = ft.ProgressBar(value=0, width=0, height=3, color="#FCD34D", bgcolor="#FCD34D20", visible=False)
        self._update_btn = ft.TextButton(
            "Cập nhật",
            icon=ft.Icons.DOWNLOAD_ROUNDED,
            style=ft.ButtonStyle(color="#FCD34D"),
            on_click=self._open_update_url,
        )
        self._update_banner = ft.Container(
            content=ft.Column([
                ft.Row([self._update_icon, self._update_text, self._update_btn], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                self._update_progress,
            ], spacing=2, tight=True),
            bgcolor="#1C1917",
            border=ft.Border.all(1, "#FCD34D30"),
            border_radius=10,
            padding=ft.Padding(left=12, right=8, top=8, bottom=8),
            margin=ft.Margin(left=14, right=14, top=0, bottom=0),
            visible=False,
        )
        self._update_url = ""

        self.cards_column = ft.ListView(spacing=8, expand=True)
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
            on_click=lambda _: self.page.run_task(self._load_data_async),
            bgcolor=C.ACCENT, color=C.TEXT_PRIMARY,
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

    def _init_views_and_transitions(self):
        header_container = ft.Container(
            content=ft.Column(controls=[
                ft.Row(controls=[
                    ft.Row(controls=[
                        ft.Text("UTHelper", size=18, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY),
                        ft.Container(
                            content=ft.Text(APP_VERSION, size=9, color=C.TEXT_SECONDARY),
                            padding=ft.Padding.symmetric(horizontal=5, vertical=1),
                            border=ft.Border.all(1, C.BORDER),
                            border_radius=4,
                        ),
                    ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Row(controls=[self.calendar_btn, self.grades_btn, self._notification_icon, self.refresh_btn, self.settings_btn], spacing=0),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                self.status_text,
            ], spacing=4),
            padding=ft.Padding.only(left=16, right=8, top=25, bottom=8),
            bgcolor=C.BG,
        )

        header = ft.Stack(
            controls=[
                header_container,
                ft.Container(
                    content=self.loading_bar,
                    bottom=0,
                    left=0,
                    right=0,
                )
            ]
        )

        filter_container = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(content=ft.Row([self.search_field]), padding=ft.Padding.only(left=16, right=16, top=6, bottom=0)),
                    ft.Container(
                        content=ft.Row(controls=[self.urgency_popup, self.type_popup, self.course_popup, self._overdue_cb], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, scroll=ft.ScrollMode.AUTO),
                        padding=ft.Padding.only(left=16, right=12),
                    ),
                ], spacing=4,
            ),
            padding=ft.Padding.only(left=0, right=0, top=4, bottom=10),
            bgcolor=C.BG,
        )

        content_area = ft.Container(
            content=ft.Stack(controls=[
                ft.Column(controls=[self.cards_column, self.empty_state, self.error_state], spacing=0, expand=True),
            ], expand=True),
            padding=ft.Padding.only(left=4, right=4, bottom=8),
            expand=True, clip_behavior=ft.ClipBehavior.NONE,
        )

        footer = ft.Container(
            content=ft.Row(controls=[
                self.footer_critical, self.footer_warning, self.footer_safe, self.footer_overdue,
            ], spacing=12, alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=C.SURFACE, padding=ft.Padding.symmetric(vertical=10),
            border=ft.Border.only(top=ft.BorderSide(1, C.BORDER)),
        )

        self.dashboard = ft.Column(
            controls=[header, self._update_banner, filter_container, content_area, footer],
            spacing=0, expand=True,
            animate_opacity=ft.Animation(200, ft.AnimationCurve.EASE_IN_OUT),
        )

        self.detail_view   = DetailView(self.page, on_close=lambda: self.page.run_task(self._close_detail), get_client=lambda: self.orchestrator.client, on_status_changed=self._on_activity_status_changed)
        self.calendar_view = CalendarView(
            self.page,
            on_close=lambda: self.page.run_task(self._close_calendar),
            on_open_detail=lambda data: self.page.run_task(self._show_detail_async, data),
        )
        self.settings_view = SettingsView(
            self.page,
            self.orchestrator,
            on_close=lambda: self.page.run_task(self._close_settings),
            on_saved=self._on_settings_saved,
            on_test_tray=self._on_test_tray,
            on_test_mobile=self._on_test_mobile,
            on_test_tele=self._on_test_tele,
            on_test_discord=self._on_test_discord,
            on_test_mail=self._on_test_mail,
            on_theme_preview=self._rebuild_colors,
        )
        self.grade_overview_view = GradeOverviewView(
            on_close=lambda: self.page.run_task(self._close_grades),
        )

        for _view in (self.detail_view, self.settings_view, self.calendar_view, self.grade_overview_view):
            _view.offset = ft.Offset(1, 0)
            _view.animate_offset = ft.Animation(250, ft.AnimationCurve.EASE_OUT)
            _view.animate_opacity = ft.Animation(200, ft.AnimationCurve.EASE_IN_OUT)

        self.view_manager = ViewManager(
            self.page,
            self.dashboard,
            self.detail_view,
            self.settings_view,
            self.calendar_view,
            self.grade_overview_view,
            self
        )

        main_stack = ft.Stack(controls=[self.dashboard, self.calendar_view, self.grade_overview_view, self.detail_view, self.settings_view], expand=True)
        if platform_utils.IS_MOBILE:
            self.page.add(ft.SafeArea(content=main_stack, expand=True))
        else:
            self.page.add(main_stack)

    def _start_skeleton_shimmer(self):
        self.cards_column.controls = [self._make_skeleton_card() for _ in range(4)]

        async def _pulse_skeletons():
            import asyncio
            while self._skeleton_visible:
                for card in self.cards_column.controls:
                    if hasattr(card, 'opacity'):
                        card.opacity = 0.7 if card.opacity < 0.6 else 0.4
                try:
                    self.page.update()
                except Exception:
                    break
                await asyncio.sleep(0.8)
        self.page.run_task(_pulse_skeletons)

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

    def _make_filter_popup(self, cfg, on_select_cb):
        btn_label = ft.Text(cfg[0][1], size=12, color=cfg[0][2], weight=ft.FontWeight.W_500)
        count_refs = {}
        check_refs = {}

        def _on_item(key: str, label: str, config_color: str):
            btn_label.value = label
            from gui.core.theme import C, _TYPE_COLORS
            if key == "all":
                resolved_color = C.TEXT_PRIMARY
            elif key in ("critical", "overdue"):
                resolved_color = C.CRITICAL
            elif key == "warning":
                resolved_color = C.WARNING
            elif key == "safe":
                resolved_color = C.SAFE
            elif key in _TYPE_COLORS:
                resolved_color = _TYPE_COLORS[key]
            elif key == "other":
                resolved_color = C.TEXT_SECONDARY
            else:
                resolved_color = C.TEXT_PRIMARY
            btn_label.color = resolved_color
            btn_label.update()
            for k, icon in check_refs.items():
                icon.visible = (k == key)
            on_select_cb(key)

        items = []
        for key, label, color in cfg:
            cnt_ref   = ft.Text("", size=10, color=color)
            check_ref = ft.Icon(ft.Icons.CHECK, size=12, color=color, visible=(key == "all"))
            count_refs[key] = cnt_ref
            check_refs[key] = check_ref
            if key == "all":
                item_content = ft.Row([ft.Text(label, size=12, color=C.TEXT_SECONDARY, expand=True), check_ref], spacing=6, tight=True)
            else:
                item_content = ft.Row([ft.Text(label, size=12, color=color, weight=ft.FontWeight.W_600), cnt_ref, ft.Container(expand=True), check_ref], spacing=4, tight=True)
            items.append(ft.PopupMenuItem(content=item_content, on_click=lambda e, k=key, l=label, c=color: _on_item(k, l, c)))

        popup = ft.PopupMenuButton(
            content=ft.Container(
                content=ft.Row([btn_label, ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=C.TEXT_SECONDARY)], spacing=2, tight=True),
                bgcolor=C.SURFACE, border=ft.Border.all(1, C.BORDER), border_radius=10, padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            ),
            items=items,
            menu_position=ft.PopupMenuPosition.UNDER,
            shape=ft.RoundedRectangleBorder(radius=10),
        )

        def update_counts(counts: dict):
            for key, ref in count_refs.items():
                n = counts.get(key, 0)
                ref.value = f"· {n}" if n > 0 else ""

        return popup, update_counts

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
            settings.INCLUDE_PAST_DUE
        )

        n_critical = counts["urgency"].get("critical", 0)
        n_warning = counts["urgency"].get("warning", 0)
        n_safe = counts["urgency"].get("safe", 0)
        n_overdue = counts["urgency"].get("overdue", 0)

        course_counts = counts["course"]
        type_counts = counts["type"]

        def _on_course_select(e, c_name):
            self.page.run_task(self._set_course, c_name)

        c_items = [ft.PopupMenuItem(
            content=ft.Row([ft.Text("Tất cả môn học", size=12, color=C.TEXT_SECONDARY, expand=True), ft.Icon(ft.Icons.CHECK, size=12, color=C.TEXT_PRIMARY, visible=(self.active_course == "all"))], spacing=6, tight=True),
            on_click=lambda e: _on_course_select(e, "all")
        )]
        
        for c in sorted([k for k in course_counts.keys() if k != "all"]):
            is_active = (self.active_course == c)
            cnt = course_counts.get(c, 0)
            c_items.append(ft.PopupMenuItem(
                content=ft.Row([
                    ft.Text(c, size=12, color=C.TEXT_PRIMARY, expand=True),
                    ft.Text(f"· {cnt}", size=11, color=C.TEXT_SECONDARY),
                    ft.Icon(ft.Icons.CHECK, size=12, color=C.TEXT_PRIMARY, visible=is_active)
                ], spacing=6, tight=True),
                on_click=lambda e, name=c: _on_course_select(e, name)
            ))
        self.course_popup.items = c_items
        # UX-3: Natural language order - "N label" instead of "Label · N"
        self.footer_critical.value = f"{n_critical} khẩn cấp" if n_critical else "0 khẩn cấp"
        self.footer_critical.color = C.CRITICAL if n_critical else C.BORDER
        self.footer_warning.value  = f"{n_warning} sắp hạn" if n_warning else "0 sắp hạn"
        self.footer_warning.color  = C.WARNING if n_warning else C.BORDER
        self.footer_safe.value     = f"{n_safe} an toàn" if n_safe else "0 an toàn"
        self.footer_safe.color     = C.SAFE if n_safe else C.BORDER
        self.footer_overdue.value  = f"{n_overdue} quá hạn" if n_overdue else "0 quá hạn"
        self.footer_overdue.color  = C.CRITICAL if n_overdue else C.BORDER

        self._update_urgency_counts(counts["urgency"])
        self._update_type_counts(type_counts)

        # P5: Dynamic overdue checkbox label with count
        if n_overdue > 0:
            self._overdue_cb.label = f"Quá hạn ({n_overdue})"
            self._overdue_cb.label_style = ft.TextStyle(size=13, color=C.CRITICAL)
        else:
            self._overdue_cb.label = "Quá hạn"
            self._overdue_cb.label_style = ft.TextStyle(size=13, color=C.TEXT_SECONDARY)

        # Render cards
        self._clear_skeletons()
        is_empty = (len(filtered_items) == 0 and not self.loading_bar.visible)
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
            
        with self._cards_lock:
            self.active_cards = render_cards
        
        self.cards_column.controls = render_cards
        self.page.update()

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
            settings.INCLUDE_PAST_DUE
        )

        self._clear_skeletons()
        self.empty_state.visible = (len(filtered_items) == 0 and not self.loading_bar.visible)
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

        with self._cards_lock:
            self.active_cards = render_cards

        self.cards_column.controls = render_cards
        self.page.update()

    async def _set_urgency(self, key: str):
        self.active_urgency = key
        # Sync overdue checkbox with urgency popup selection
        if key == "overdue":
            self._overdue_cb.value = True
            settings.INCLUDE_PAST_DUE = True
        else:
            self._overdue_cb.value = False
            settings.INCLUDE_PAST_DUE = False
        self._refresh_ui()

    async def _set_type(self, key: str):
        self.active_type = key
        self._refresh_ui()

    async def _set_course(self, course_name: str):
        self.active_course = course_name
        self.course_btn_label.value = "Môn học" if course_name == "all" else course_name
        self.course_btn_label.update()
        self._refresh_ui()

    def _apply_settings_filter(self, data: list) -> list:
        result = []
        for d in data:
            details     = d.get("details", {})
            status      = details.get("status_data", {})
            # Check both English and Vietnamese keys (WS API uses Vietnamese)
            grading     = status.get("Grading status", "") or status.get("Trạng thái chấm", "") or status.get("Chấm điểm", "")
            submission  = status.get("Submission status", "") or status.get("Trạng thái nộp bài", "") or d.get("submission_status", "")
            is_graded   = (grading in ("Graded", "Đã chấm") or (grading.startswith("Graded") and "Not" not in grading))
            is_submitted = (submission in ("Submitted for grading", "Đã nộp", "Đã nộp, chờ chấm") or ("Submitted" in submission and "Not" not in submission) or ("Đã nộp" in submission and "Chưa" not in submission))

            if is_graded and not settings.INCLUDE_GRADED: continue
            if is_submitted and not is_graded and not settings.INCLUDE_SUBMITTED: continue
            
            # _refresh_ui handles INCLUDE_PAST_DUE now
            result.append(d)
        return result

    async def _toggle_overdue(self, e):
        settings.INCLUDE_PAST_DUE = self._overdue_cb.value
        _save_setting("INCLUDE_PAST_DUE", settings.INCLUDE_PAST_DUE)
        
        # When checkbox is ON: filter to show ONLY overdue items
        # When OFF: reset urgency filter to show all (excluding overdue)
        if self._overdue_cb.value:
            self.active_urgency = "overdue"
        else:
            self.active_urgency = "all"
        
        self._update_footer()

    def _on_search(self, e):
        self.active_search = (e.control.value or "").strip()
        # Debounce: 150ms - fast enough to feel instant, prevents excessive renders
        if hasattr(self, '_search_task') and self._search_task:
            self._search_task.cancel()
        async def _delayed_render():
            await asyncio.sleep(0.15)
            self._render_cards_only()
        self._search_task = self.page.run_task(_delayed_render)

    async def _check_grades_background(self):
        """PERF-OPT: Check grade changes in background (non-blocking)."""
        try:
            grade_changes = await asyncio.to_thread(self.orchestrator.check_grade_changes)
            if grade_changes and hasattr(self, 'notifier') and self.notifier:
                self.notifier.dispatch_grade_alert(grade_changes)
                msg = f"\U0001f4ca {len(grade_changes)} \u0111i\u1ec3m m\u1edbi: "
                msg += ", ".join(f"{c.item_name} ({c.new_grade})" for c in grade_changes[:3])
                if len(grade_changes) > 3:
                    msg += f" +{len(grade_changes) - 3} kh\u00e1c"
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(msg, color=ft.Colors.WHITE),
                    bgcolor="#22C55E",
                    duration=5000,
                )
                self.page.snack_bar.open = True
                self.page.update()
        except Exception as e:
            logger.debug("Grade check background failed (non-critical): %s", e)

    async def _prefetch_details_async(self, activities: list):
        len(activities)
        workers = max(1, min(settings.PREFETCH_WORKERS, 10))
        
        with self._data_lock:
            count = len(self.all_data)
        self.status_text.value = f"Cập nhật lúc {datetime.now().strftime('%H:%M')} · {count} hoạt động"
        self.loading_bar.visible = True
        self.page.update()

        await asyncio.to_thread(
            self.orchestrator.prefetch_all_details,
            activities,
            workers,
            lambda: self._prefetch_cancel_event.is_set(),
            False
        )

        if not self._prefetch_cancel_event.is_set() and not self._is_loading:
            cache = self.orchestrator.get_cached_details_snapshot()
            with self._data_lock:
                data_copy = list(self.all_data)
            for i, item in enumerate(data_copy):
                url = item.get("url")
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
            data_copy.sort(key=lambda x: (
                0 if x.get("urgency") == "critical" else 1 if x.get("urgency") == "warning" else 2,
                x.get("deadline", "")
            ))
            with self._data_lock:
                self.all_data = data_copy
            self._update_footer()
            self.status_text.value = f"Cập nhật lúc {datetime.now().strftime('%H:%M')} · {len(data_copy)} hoạt động ✓"
            self.loading_bar.visible = False
            self.page.update()

    async def _load_data_async(self):
        if self._is_loading: return
        self._is_loading = True
        self._prefetch_cancel_event.set()
        
        # Clear persistent detail cache to force fresh data fetch on manual refresh
        try:
            self.orchestrator.moodle_service.clear_all_caches()
            self.orchestrator.clear_detail_cache()
        except Exception:
            pass

        self.refresh_btn.disabled = True
        self.status_text.value    = "Đang cập nhật dữ liệu..."
        self.loading_bar.visible  = True
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
                self.status_text.value = "Dữ liệu cache · Đang cập nhật..."
                self._update_footer()
                self.page.update()

        try:
            import time
            fetch_start_time = time.time()
            
            # Ưu tiên async WS API (non-blocking), fallback sync in thread
            if hasattr(self.orchestrator, 'get_latest_activities_async'):
                result = await self.orchestrator.get_latest_activities_async()
            else:
                result = await asyncio.to_thread(self.orchestrator.get_latest_activities)
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

            # Lưu cache offline
            self._data_cache.save(data_copy)
            # P3: Tính tiến độ nộp bài - positive reinforcement
            submitted_statuses = ("submitted", "Đã nộp", "graded", "Đã chấm")
            total_count = len(data_copy)
            submitted_count = sum(1 for x in data_copy if x.get("submission_status", "") in submitted_statuses)
            progress_text = ""
            if total_count > 0 and submitted_count > 0:
                if submitted_count == total_count:
                    progress_text = " · Đã nộp hết ✓"
                else:
                    progress_text = f" · {submitted_count}/{total_count} đã nộp ✓"
            self.status_text.value = f"Cập nhật lúc {datetime.now().strftime('%H:%M')} · {total_count} hoạt động{progress_text}"
            if not self._skeleton_visible:
                self._show_snackbar(f"Đã cập nhật {total_count} hoạt động", ft.Icons.SYNC_ROUNDED, C.ACCENT)
            
            # Bắn thông báo thông minh cho người dùng
            if hasattr(self, 'notifier') and self.notifier:
                try:
                    with self._data_lock:
                        dispatch_copy = list(self.all_data)
                    self.notifier.dispatch(dispatch_copy)
                except Exception as e:
                    logger.error(f"[UTHelper] Dispatcher lỗi: {e}")

                # PERF-OPT: Grade check moved to background task to not block main data display
                self.page.run_task(self._check_grades_background)

            self._update_footer()
            
            self._prefetch_cancel_event.clear()
            with self._data_lock:
                prefetch_copy = list(self.all_data)
            self.page.run_task(self._prefetch_details_async, prefetch_copy)


            # Start auto-poll if not already running
            if not hasattr(self, '_auto_poll_task') or self._auto_poll_task is None:
                self._auto_poll_task = self.page.run_task(self._auto_poll_loop)

            # Update notification badge
            self.page.run_task(self._update_notification_badge)
        except Exception as exc:
            logger.exception(f"[Load] Lỗi: {exc}")
            with self._data_lock:
                self.all_data = []
            
            # Thử dùng cache khi offline
            cached_data, cached_at = self._data_cache.load()
            if cached_data:
                with self._data_lock:
                    self.all_data = cached_data
                self.status_text.value = "Offline · Dữ liệu cache"
                self.error_state.visible = False
                self._update_footer()
            else:
                self.error_text.value = "Không thể kết nối tới Moodle. Vui lòng kiểm tra mạng và thử lại."
                self.error_state.visible = True
                self.status_text.value = "Lỗi kết nối server"
        finally:
            self.loading_bar.visible  = False
            self.refresh_btn.disabled = False
            self._is_loading          = False
            self.page.update()

    async def _auto_poll_loop(self):
        """Background loop that periodically refreshes data based on POLL_INTERVAL_MINUTES."""
        logger.info("[AutoPoll] Started with interval=%d min", getattr(settings, 'POLL_INTERVAL_MINUTES', 15))
        while self._page_alive.is_set():
            interval = getattr(settings, 'POLL_INTERVAL_MINUTES', 15) * 60
            await asyncio.sleep(interval)
            if not self._page_alive.is_set():
                break
            if self._is_loading:
                logger.debug("[AutoPoll] Skipping - another load in progress")
                continue
            logger.info("[AutoPoll] Running periodic refresh...")
            try:
                # Smart poll: check if anything changed first
                if getattr(settings, 'SMART_POLL_ENABLED', True):
                    changed = self.orchestrator.get_updates_since(
                        self.orchestrator._last_fetch_ts
                    )
                    if changed is not None and len(changed) == 0:
                        logger.info("[AutoPoll] No changes detected, skipping full fetch")
                        continue
                    if changed:
                        logger.info("[AutoPoll] %d courses changed, doing full fetch", len(changed))
                await self._load_data_async()
            except Exception as e:
                logger.error("[AutoPoll] Error: %s", e)
        logger.info("[AutoPoll] Loop ended")

    async def _close_detail(self):
        from_calendar = getattr(self, '_detail_from_calendar', False)
        self._detail_from_calendar = False
        await self.view_manager.close_detail(from_calendar)

    async def _toggle_calendar(self):
        """Toggle between dashboard list view and calendar view."""
        if self.calendar_view.visible:
            await self._close_calendar()
        else:
            await self._show_calendar()

    async def _show_calendar(self):
        """Show calendar view with current data."""
        with self._data_lock:
            data_snapshot = list(self.all_data)
        self.view_manager.show_calendar(data_snapshot)

    async def _close_calendar(self):
        """Return from calendar to dashboard."""
        await self.view_manager.close_calendar()

    async def _show_settings(self):
        self.view_manager.show_settings()

    async def _toggle_grades(self):
        """Toggle the grade overview panel."""
        if self.grade_overview_view.visible:
            await self._close_grades()
        else:
            await self._show_grades()

    async def _show_grades(self):
        """Show grade overview panel and fetch data."""
        self.view_manager.show_grades_loading()

        # Fetch grades in background thread to avoid freezing UI
        try:
            userid = self.orchestrator._get_userid() if hasattr(self.orchestrator, '_get_userid') else None
            if userid:
                courses_grades, grade_items = await asyncio.to_thread(
                    self.orchestrator.moodle_service.fetch_all_grades, userid
                )
                self.view_manager.show_grades_data(courses_grades or [], grade_items)
            else:
                self.view_manager.show_grades_data([], {})
        except Exception as e:
            logger.error("Grade fetch failed: %s", e)
            self.view_manager.show_grades_data([], {})

    async def _close_grades(self):
        """Close grade overview and return to dashboard."""
        await self.view_manager.close_grades()

    def _rebuild_colors(self):
        """Repaint ALL existing controls with current C values for live theme switching.

        Because controls store hardcoded color strings at init time,
        we must explicitly reassign every color property when the theme changes.
        """
        from gui.core.theme import C as _C

        # Page
        self.page.bgcolor = _C.BG

        # Header
        header_stack = self.dashboard.controls[0]  # ft.Stack
        if len(header_stack.controls) > 0:
            header_container = header_stack.controls[0]
            header_container.bgcolor = _C.BG
            # Walk header children: title text, version badge, status text
            try:
                header_col = header_container.content  # ft.Column
                title_row = header_col.controls[0]  # ft.Row
                left_row = title_row.controls[0]  # ft.Row with title + version
                # "UTHelper" text
                left_row.controls[0].color = _C.TEXT_PRIMARY
                # Version badge container
                ver_badge = left_row.controls[1]
                ver_badge.content.color = _C.TEXT_SECONDARY
                ver_badge.border = ft.Border.all(1, _C.BORDER)
            except (IndexError, AttributeError):
                pass

        # Status text
        self.status_text.color = _C.TEXT_SECONDARY

        # Filter area
        filter_container = self.dashboard.controls[2]
        filter_container.bgcolor = _C.BG
        self.search_field.bgcolor = _C.SURFACE
        self.search_field.border_color = _C.BORDER
        self.search_field.focused_border_color = _C.ACCENT
        self.search_field.color = _C.TEXT_PRIMARY

        # Filter popups and checkbuttons
        try:
            self.urgency_popup.content.bgcolor = _C.SURFACE
            self.urgency_popup.content.border = ft.Border.all(1, _C.BORDER)
            self.urgency_popup.content.content.controls[1].color = _C.TEXT_SECONDARY

            urgency_colors = {
                "all": _C.TEXT_PRIMARY,
                "critical": _C.CRITICAL,
                "warning": _C.WARNING,
                "safe": _C.SAFE,
                "overdue": _C.CRITICAL
            }
            self.urgency_popup.content.content.controls[0].color = urgency_colors.get(self.active_urgency, _C.TEXT_PRIMARY)

            # Urgency Popup Items
            self.urgency_popup.items[0].content.controls[0].color = _C.TEXT_SECONDARY
            self.urgency_popup.items[0].content.controls[1].color = _C.TEXT_SECONDARY

            # critical
            self.urgency_popup.items[1].content.controls[0].color = _C.CRITICAL
            self.urgency_popup.items[1].content.controls[1].color = _C.CRITICAL
            self.urgency_popup.items[1].content.controls[3].color = _C.CRITICAL

            # warning
            self.urgency_popup.items[2].content.controls[0].color = _C.WARNING
            self.urgency_popup.items[2].content.controls[1].color = _C.WARNING
            self.urgency_popup.items[2].content.controls[3].color = _C.WARNING

            # safe
            self.urgency_popup.items[3].content.controls[0].color = _C.SAFE
            self.urgency_popup.items[3].content.controls[1].color = _C.SAFE
            self.urgency_popup.items[3].content.controls[3].color = _C.SAFE

            # overdue
            self.urgency_popup.items[4].content.controls[0].color = _C.CRITICAL
            self.urgency_popup.items[4].content.controls[1].color = _C.CRITICAL
            self.urgency_popup.items[4].content.controls[3].color = _C.CRITICAL
        except Exception:
            pass

        try:
            from gui.core.theme import _TYPE_COLORS
            self.type_popup.content.bgcolor = _C.SURFACE
            self.type_popup.content.border = ft.Border.all(1, _C.BORDER)
            self.type_popup.content.content.controls[1].color = _C.TEXT_SECONDARY

            type_colors = {
                "all": _C.TEXT_PRIMARY,
                "quiz": _TYPE_COLORS["quiz"],
                "assignment": _TYPE_COLORS["assignment"],
                "attendance": _TYPE_COLORS["attendance"],
                "open": _TYPE_COLORS["open"],
                "other": _C.TEXT_SECONDARY
            }
            self.type_popup.content.content.controls[0].color = type_colors.get(self.active_type, _C.TEXT_PRIMARY)

            # Type Popup Items
            self.type_popup.items[0].content.controls[0].color = _C.TEXT_SECONDARY
            self.type_popup.items[0].content.controls[1].color = _C.TEXT_SECONDARY

            self.type_popup.items[1].content.controls[0].color = _TYPE_COLORS["quiz"]
            self.type_popup.items[1].content.controls[1].color = _TYPE_COLORS["quiz"]
            self.type_popup.items[1].content.controls[3].color = _TYPE_COLORS["quiz"]

            self.type_popup.items[2].content.controls[0].color = _TYPE_COLORS["assignment"]
            self.type_popup.items[2].content.controls[1].color = _TYPE_COLORS["assignment"]
            self.type_popup.items[2].content.controls[3].color = _TYPE_COLORS["assignment"]

            self.type_popup.items[3].content.controls[0].color = _TYPE_COLORS["attendance"]
            self.type_popup.items[3].content.controls[1].color = _TYPE_COLORS["attendance"]
            self.type_popup.items[3].content.controls[3].color = _TYPE_COLORS["attendance"]

            self.type_popup.items[4].content.controls[0].color = _TYPE_COLORS["open"]
            self.type_popup.items[4].content.controls[1].color = _TYPE_COLORS["open"]
            self.type_popup.items[4].content.controls[3].color = _TYPE_COLORS["open"]

            self.type_popup.items[5].content.controls[0].color = _C.TEXT_SECONDARY
            self.type_popup.items[5].content.controls[1].color = _C.TEXT_SECONDARY
            self.type_popup.items[5].content.controls[3].color = _C.TEXT_SECONDARY
        except Exception:
            pass

        try:
            self.course_btn_label.color = _C.TEXT_PRIMARY
            self.course_popup.content.bgcolor = _C.SURFACE
            self.course_popup.content.border = ft.Border.all(1, _C.BORDER)
            self.course_popup.content.content.controls[1].color = _C.TEXT_SECONDARY
        except Exception:
            pass

        try:
            self._overdue_cb.label_style.color = _C.TEXT_SECONDARY
            self._overdue_cb.check_color = _C.BG
            self._overdue_cb.active_color = _C.CRITICAL
        except Exception:
            pass

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
            self._error_retry_btn.color = _C.TEXT_PRIMARY

        # Footer
        footer = self.dashboard.controls[-1]
        footer.bgcolor = _C.SURFACE
        footer.border = ft.Border.only(top=ft.BorderSide(1, _C.BORDER))
        # Footer counter colors are set dynamically by _refresh_counts,
        # but update the base colors here too
        self.footer_critical.color = _C.CRITICAL if self.footer_critical.value else _C.BORDER
        self.footer_warning.color = _C.WARNING if self.footer_warning.value else _C.BORDER
        self.footer_safe.color = _C.SAFE if self.footer_safe.value else _C.BORDER
        self.footer_overdue.color = _C.CRITICAL if self.footer_overdue.value else _C.BORDER

        # Cards  Re-color all existing recycled cards
        if hasattr(self, '_reusable_cards'):
            for card in self._reusable_cards:
                card.bgcolor = _C.SURFACE
                if hasattr(card, '_title_text'):
                    card._title_text.color = _C.TEXT_PRIMARY
                if hasattr(card, '_course_text'):
                    card._course_text.color = _C.ACCENT
                if hasattr(card, '_deadline_text'):
                    card._deadline_text.color = _C.TEXT_SECONDARY
                if hasattr(card, '_progress_ctrl'):
                    card._progress_ctrl.bgcolor = _C.BORDER

        # Icon buttons
        notif_btn = None
        if hasattr(self, '_notification_icon') and len(self._notification_icon.controls) > 0:
            notif_btn = self._notification_icon.controls[0]

        buttons_to_update = [self.calendar_btn, self.grades_btn, self.refresh_btn, self.settings_btn]
        if notif_btn:
            buttons_to_update.append(notif_btn)

        for btn in buttons_to_update:
            btn.icon_color = _C.TEXT_SECONDARY

        # Loading bar
        self.loading_bar.color = _C.ACCENT
        self.loading_bar.bgcolor = _C.BORDER

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
        
        self._update_footer()
        
        if getattr(self, '_needs_reload', False):
            self._needs_reload = False
            self.page.run_task(self._load_data_async)
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
        try:
            self.page.show_dialog(sb)
        except (AttributeError, TypeError):
            # Fallback for older Flet
            self.page.overlay.append(sb)
            sb.open = True
            self.page.update()

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
        from notifiers.mobile import MobileNotifier
        MobileNotifier().notify([dummy])

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

    def _on_update_check(self, has_update: bool, version: str, release_url: str, asset_url: str = None):
        """Callback từ background thread khi kiểm tra update xong."""
        if has_update and version:
            async def _update_ui():
                self._update_asset_url = asset_url or ""
                self._update_release_url = release_url or ""
                self._update_url = asset_url or release_url or ""
                self._update_version = version
                self._update_icon.name = ft.Icons.SYSTEM_UPDATE_ROUNDED
                self._update_icon.color = "#FCD34D"
                self._update_text.value = f"Phiên bản mới v{version} đã sẵn sàng!"
                self._update_btn.visible = True
                self._update_btn.text = "Cập nhật"
                self._update_btn.icon = ft.Icons.DOWNLOAD_ROUNDED
                self._update_progress.visible = False
                self._update_banner.visible = True
                self.page.update()
            try:
                self.page.run_task(_update_ui)
            except Exception:
                pass

    async def _open_update_url(self, e):
        """Smart update: auto-download trên Windows/Android, browser trên iOS/other."""
        from core.update_checker import _is_windows, _is_android

        asset_url = getattr(self, "_update_asset_url", "")
        release_url = getattr(self, "_update_release_url", "")

        # Windows: download .zip → batch updater → restart
        if _is_windows() and asset_url and asset_url.endswith(".zip"):
            await self._do_auto_update_windows(asset_url)
            return

        # Android: download .apk → install intent
        if _is_android() and asset_url and asset_url.endswith(".apk"):
            await self._do_auto_update_android(asset_url)
            return

        # Fallback: open browser
        url = asset_url or release_url
        if url:
            try:
                self.page.launch_url(url)
            except Exception:
                import webbrowser; webbrowser.open(url)

    async def _do_auto_update_windows(self, url: str):
        """Download .zip and apply via batch updater (Windows)."""
        import sys
        import asyncio
        from core.update_checker import download_update, apply_update_windows

        # Switch banner to download state
        self._update_icon.name = ft.Icons.DOWNLOADING_ROUNDED
        self._update_icon.color = "#60A5FA"
        self._update_text.value = "Đang tải xuống... 0%"
        self._update_btn.visible = False
        self._update_progress.visible = True
        self._update_progress.value = 0
        self.page.update()

        def _progress(pct):
            async def _upd():
                self._update_text.value = f"Đang tải xuống... {int(pct * 100)}%"
                self._update_progress.value = pct
                self.page.update()
            try: self.page.run_task(_upd)
            except Exception: pass

        zip_path = await asyncio.to_thread(download_update, url, _progress)

        if zip_path:
            self._update_icon.name = ft.Icons.INSTALL_DESKTOP_ROUNDED
            self._update_icon.color = C.SAFE
            self._update_text.value = "Đang cài đặt... Ứng dụng sẽ khởi động lại."
            self._update_progress.value = 1.0
            self.page.update()
            await asyncio.sleep(0.5)

            success = apply_update_windows(zip_path)
            if success:
                sys.exit(0)
            else:
                self._update_icon.name = ft.Icons.ERROR_OUTLINE_ROUNDED
                self._update_icon.color = C.CRITICAL
                self._update_text.value = "Cập nhật thất bại. Vui lòng tải thủ công."
                self._update_btn.visible = True
                self._update_btn.text = "Tải thủ công"
                self._update_btn.icon = ft.Icons.OPEN_IN_BROWSER_ROUNDED
                self._update_progress.visible = False
                self.page.update()
        else:
            self._update_icon.name = ft.Icons.CLOUD_OFF_ROUNDED
            self._update_icon.color = C.CRITICAL
            self._update_text.value = "Tải xuống thất bại."
            self._update_btn.visible = True
            self._update_btn.text = "Thử lại"
            self._update_btn.icon = ft.Icons.REFRESH_ROUNDED
            self._update_progress.visible = False
            self.page.update()

    async def _do_auto_update_android(self, url: str):
        """Download .apk and trigger install intent (Android)."""
        import asyncio
        from core.update_checker import download_update, apply_update_android

        # Switch banner to download state
        self._update_icon.name = ft.Icons.DOWNLOADING_ROUNDED
        self._update_icon.color = "#60A5FA"
        self._update_text.value = "Đang tải APK... 0%"
        self._update_btn.visible = False
        self._update_progress.visible = True
        self._update_progress.value = 0
        self.page.update()

        def _progress(pct):
            async def _upd():
                self._update_text.value = f"Đang tải APK... {int(pct * 100)}%"
                self._update_progress.value = pct
                self.page.update()
            try: self.page.run_task(_upd)
            except Exception: pass

        apk_path = await asyncio.to_thread(download_update, url, _progress)

        if apk_path:
            self._update_icon.name = ft.Icons.INSTALL_MOBILE_ROUNDED
            self._update_icon.color = C.SAFE
            self._update_text.value = "Đang mở trình cài đặt..."
            self._update_progress.value = 1.0
            self.page.update()
            await asyncio.sleep(0.3)

            success = apply_update_android(apk_path)
            if not success:
                self._update_icon.name = ft.Icons.OPEN_IN_BROWSER_ROUNDED
                self._update_icon.color = C.WARNING
                self._update_text.value = "Mở trình duyệt để tải..."
                self._update_progress.visible = False
                self.page.update()
                try: self.page.launch_url(url)
                except Exception: pass
        else:
            self._update_icon.name = ft.Icons.CLOUD_OFF_ROUNDED
            self._update_icon.color = C.CRITICAL
            self._update_text.value = "Tải APK thất bại."
            self._update_btn.visible = True
            self._update_btn.text = "Thử lại"
            self._update_btn.icon = ft.Icons.REFRESH_ROUNDED
            self._update_progress.visible = False
            self.page.update()


    def _on_settings_saved(self):
        from gui.core.theme import load_theme_from_settings, set_page_theme
        
        # Clear all caches to avoid using stale tokens/data from previous credentials
        try:
            self.orchestrator.moodle_service.clear_all_caches()
            self.orchestrator.clear_detail_cache()
            self.orchestrator._userid_cache = None
            self.orchestrator.is_logged_in = False
        except Exception:
            pass

        load_theme_from_settings()
        set_page_theme(self.page)
        self._rebuild_colors()
        self._needs_reload = True
        self._show_snackbar("Đã lưu cài đặt", ft.Icons.SAVE_ROUNDED, C.SAFE)
    def _on_activity_status_changed(self, url: str, new_status: str):
        """Callback từ DetailView khi trạng thái nộp bài được cập nhật hoặc thay đổi."""
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
                pass
            
            # 2. Lưu cache đĩa để không bị mất khi restart app
            try:
                self._data_cache.save(data_copy)
            except Exception:
                pass

            
            # 3. Làm mới UI của dashboard (cập nhật card status badge, đếm số bài đã nộp...)
            self._update_footer()

    def _show_detail(self, data: dict):
        self.page.run_task(self._show_detail_async, data)

    async def _show_detail_async(self, data: dict):
        # Ghi nhận nếu màn hình chi tiết được mở từ Lịch để phục vụ quay lại (back-navigation)
        self._detail_from_calendar = self.calendar_view.visible
        self.view_manager.show_detail_loading(data)
        try:
            full_data = await asyncio.to_thread(self.orchestrator.fetch_full_details, data)
            if full_data and "details" in full_data:
                desc_html = full_data["details"].get("description_html", "")
                if desc_html:
                    from gui.core.utils import pre_cache_description_images
                    full_data["details"]["description_html"] = pre_cache_description_images(desc_html)
            self.view_manager.show_detail_data(full_data)
        except Exception:
            self.view_manager.show_detail_error(data)

    def _pulse_cards_once(self, cards_snapshot: list, pulse_high: bool):
        # Tự động tạo bóng đổ nhấp nháy (pulse shadow) sử dụng mã màu C.CRITICAL của theme hiện tại
        crit_hex = C.CRITICAL.lstrip("#")
        alpha = "40" if pulse_high else "15"
        blur = 16 if pulse_high else 8
        spread = 1.5 if pulse_high else 0.5
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
            self.page.update()

    def _countdown_cards_once(self, cards_snapshot: list):
        if not cards_snapshot:
            return
        changed = False
        for card in cards_snapshot:
            if card.update_countdown():
                changed = True
        if changed:
            self.page.update()

    async def _pulse_loop_async(self):
        pulse_high = True
        while self._page_alive.is_set():
            await asyncio.sleep(1.5)
            if not self._page_alive.is_set(): break
            # Skip pulse when dashboard is not visible (calendar/detail/settings open)
            if not self.dashboard.visible:
                continue
            pulse_high = not pulse_high
            try:
                if not self.active_cards:
                    continue
                with self._cards_lock:
                    cards_snapshot = list(self.active_cards)
                self._pulse_cards_once(cards_snapshot, pulse_high)
            except Exception:
                pass

    async def _countdown_loop_async(self):
        while self._page_alive.is_set():
            # Adaptive interval: faster when critical items exist
            has_critical = any(
                getattr(c, '_is_critical_active', False)
                for c in (self.active_cards or [])
            )
            interval = 15 if has_critical else 60
            slept = 0
            while slept < interval and self._page_alive.is_set():
                await asyncio.sleep(1)
                slept += 1
            if not self._page_alive.is_set(): break
            # Skip when dashboard not visible
            if not self.dashboard.visible:
                continue
            try:
                with self._cards_lock:
                    cards_snapshot = list(self.active_cards)
                self._countdown_cards_once(cards_snapshot)
            except Exception:
                pass

    async def _auto_refresh_loop_async(self):
        while self._page_alive.is_set():
            interval = settings.CHECK_INTERVAL_MINUTES
            target_sleep = 60 if interval <= 0 else interval * 60
            slept = 0
            while slept < target_sleep and self._page_alive.is_set():
                await asyncio.sleep(2)
                slept += 2
                
            if not self._page_alive.is_set() or interval <= 0:
                continue
                
            try:
                # H2: Skip auto-refresh when user is in settings/detail/calendar
                if not self.dashboard.visible:
                    continue
                await self._load_data_async()
            except Exception:
                pass

    async def _start_background_scheduler(self):
        """Initialize Android background scheduler for periodic deadline checks."""
        try:
            from core.background_scheduler import get_scheduler
            scheduler = get_scheduler()
            if not scheduler.is_available:
                logger.debug("Background scheduler not available on this platform")
                return

            await scheduler.request_permissions()
            interval = max(5, settings.BACKGROUND_CHECK_INTERVAL)
            await scheduler.start_periodic_check(interval_minutes=interval)
            logger.info("Android background scheduler started (every %d min)", interval)
        except Exception as e:
            logger.warning("Failed to start background scheduler: %s", e)

    async def _update_notification_badge(self):
        """Fetch unread notification count and update badge."""
        try:
            userid = self.orchestrator._get_userid() if hasattr(self.orchestrator, '_get_userid') else None
            if userid is None:
                userid = self.orchestrator.moodle_service.get_current_user_id()
            if userid:
                count = self.orchestrator.moodle_service.get_unread_notification_count(userid)
                self._notification_badge_text.value = str(count) if count <= 9 else "9+"
                self._notification_badge.visible = count > 0
                self.page.update()
        except Exception as e:
            logger.debug("Badge update failed: %s", e)

    async def _on_notification_click(self, e):
        """Open Moodle notifications in browser."""
        import webbrowser
        webbrowser.open("https://courses.ut.edu.vn/message/index.php")

    def _on_disconnect(self, e):
        self._page_alive.clear()
        self._prefetch_cancel_event.set()
        try:
            self.orchestrator.client.close()
        except Exception:
            pass

def main(page: ft.Page):
    """Stub main function to support Flet Preview on this file directly."""
    # Apply compatibility shims if running directly
    try:
        from gui.flet_compat import patch_flet
        patch_flet()
    except Exception:
        pass
    AppController(page)

if __name__ == "__main__":
    ft.run(main=main, assets_dir=os.path.join(_project_root, "assets"))

