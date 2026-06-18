import flet as ft
from datetime import datetime
from core.time_utils import parse_datetime
import asyncio
import logging
from platform_utils import IS_WINDOWS, IS_MOBILE, detect_platform
from notifiers.manager import NotificationManager
import threading

from core.data_orchestrator import DataOrchestrator
from config import settings

from gui.core.theme import C, _TYPE_FILTER_MAP
from gui.core.utils import clean_course_name, urgency_str
from core.filter_service import FilterService
from gui.components.activity_card import ActivityCard
from gui.components.detail_view import DetailView
from gui.components.settings_view import SettingsView
from gui.components.calendar_view import CalendarView

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
        
        self._prefetch_cancel_event = threading.Event()

        self._init_window()
        self._init_ui()
        
        # Events
        self.page.on_disconnect = self._on_disconnect
        self.page.on_keyboard_event = self._on_keyboard_event
        
        # Android back button — intercept to navigate within app instead of exiting
        if IS_MOBILE:
            self.page.on_view_pop = self._on_back_button
        self.page.run_task(self._pulse_loop_async)
        self.page.run_task(self._countdown_loop_async)
        self.page.run_task(self._auto_refresh_loop_async)
        self._tray_balloon_shown = False  # H-01: only show once
        
        # Check update in background
        from core.update_checker import check_for_update_async
        check_for_update_async(APP_VERSION, self._on_update_check)
        
        if not settings.UTH_USERNAME or not settings.UTH_PASSWORD:
            self.page.run_task(self._show_login_dialog)
        else:
            self.page.run_task(self._load_data_async)

    async def _show_login_dialog(self):
        from gui.components.login_dialog import show_login_dialog
        await show_login_dialog(self.page, self.orchestrator, self._load_data_async)

    def _init_window(self):
        # Runtime platform detection for accurate mobile/desktop flags
        detect_platform(self.page)
        # Re-read flags after runtime detection (they may have changed)
        import platform_utils
        _is_mobile = platform_utils.IS_MOBILE
        _is_windows = platform_utils.IS_WINDOWS
        
        # ── Desktop-only: Fixed-size window with tray support ──
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
        
        # ── Tray & Notifications (platform-aware) ──
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
        if IS_MOBILE:
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
        """Android back button handler — navigate within app instead of exiting."""
        handled = await self._navigate_back()
        if not handled:
            # On dashboard with nothing to go back to — minimize app (don't exit)
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
        # Skeleton loading cards
        self._skeleton_visible = True

        # Footer components
        self.status_text     = ft.Text("Đang khởi động...", size=11, color=C.TEXT_SECONDARY)
        self.footer_critical = ft.Text("", size=11, color=C.CRITICAL, weight=ft.FontWeight.W_600)
        self.footer_warning  = ft.Text("", size=11, color=C.WARNING,  weight=ft.FontWeight.W_600)
        self.footer_safe     = ft.Text("", size=11, color=C.SAFE,     weight=ft.FontWeight.W_600)
        self.footer_overdue  = ft.Text("", size=11, color=C.CRITICAL, weight=ft.FontWeight.W_600)
        self.loading_bar     = ft.ProgressBar(color=C.ACCENT, bgcolor=C.BORDER, visible=False)

        # Filters
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
                    ft.Container(content=self.course_btn_label, width=80, alignment=ft.Alignment(-1, 0)),
                    ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=C.TEXT_SECONDARY)
                ], spacing=2, tight=True),
                bgcolor=C.SURFACE, border=ft.border.all(1, C.BORDER), border_radius=10, padding=ft.Padding.symmetric(horizontal=8, vertical=8),
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

        # Header
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

        header = ft.Container(
            content=ft.Column(controls=[
                ft.Row(controls=[
                    ft.Row(controls=[
                        ft.Text("UTHelper", size=18, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY),
                        ft.Container(
                            content=ft.Text(APP_VERSION, size=9, color=C.TEXT_SECONDARY),
                            padding=ft.Padding.symmetric(horizontal=5, vertical=1),
                            border=ft.border.all(1, C.BORDER),
                            border_radius=4,
                        ),
                    ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Row(controls=[self.calendar_btn, self.refresh_btn, self.settings_btn], spacing=0),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                self.status_text,
                self.loading_bar,
            ], spacing=4),
            padding=ft.Padding.only(left=16, right=8, top=20, bottom=4),
            bgcolor=C.BG,
        )

        # Update banner (ẩn mặc định)
        self._update_banner = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.SYSTEM_UPDATE, size=14, color="#FCD34D"),
                ft.Text("Có phiên bản mới!", size=12, color="#FCD34D", expand=True),
                ft.TextButton("Tải về", style=ft.ButtonStyle(color="#FCD34D"), on_click=self._open_update_url),
            ], spacing=6),
            bgcolor="#1C1917",
            border=ft.border.all(1, "#FCD34D40"),
            border_radius=8,
            padding=ft.Padding(left=10, right=6, top=4, bottom=4),
            margin=ft.Margin(left=14, right=14, top=0, bottom=0),
            visible=False,
        )
        self._update_url = ""

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

        self.cards_column = ft.ListView(spacing=8, expand=True)
        # P2/P6: Dynamic empty state — changes based on context
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
        self.error_text = ft.Text("", size=13, color=C.CRITICAL)
        self.error_state = ft.Container(
            content=ft.Column(controls=[
                ft.Text("Không thể kết nối", size=14, color=C.TEXT_SECONDARY, weight=ft.FontWeight.W_500),
                self.error_text,
                ft.TextButton("Thử lại", on_click=lambda _: self.page.run_task(self._load_data_async)),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            alignment=ft.Alignment(0, 0), expand=True, visible=False,
        )

        content_area = ft.Container(
            content=ft.Stack(controls=[
                ft.Column(controls=[self.cards_column, self.empty_state, self.error_state], spacing=0, expand=True),
            ], expand=True),
            padding=ft.Padding.only(left=14, right=14, bottom=8),
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

        self.detail_view   = DetailView(self.page, on_close=lambda: self.page.run_task(self._close_detail), get_client=lambda: self.orchestrator.client)
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
            on_test_tele=self._on_test_tele,
            on_test_discord=self._on_test_discord,
            on_test_mail=self._on_test_mail
        )

        # UX: Slide-in transitions for views
        for _view in (self.detail_view, self.settings_view, self.calendar_view):
            _view.offset = ft.Offset(1, 0)  # start off-screen right
            _view.animate_offset = ft.Animation(250, ft.AnimationCurve.EASE_OUT)
            _view.animate_opacity = ft.Animation(200, ft.AnimationCurve.EASE_IN_OUT)

        self.page.add(ft.Stack(controls=[self.dashboard, self.calendar_view, self.detail_view, self.settings_view], expand=True))

        # Show skeleton cards immediately while data loads
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
            border=ft.border.all(1, C.BORDER),
            opacity=0.5,
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

        def _on_item(key: str, label: str, color: str):
            btn_label.value = label
            btn_label.color = color
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
                bgcolor=C.SURFACE, border=ft.border.all(1, C.BORDER), border_radius=10, padding=ft.Padding.symmetric(horizontal=12, vertical=10),
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

        self.footer_critical.value = f"Khẩn cấp · {n_critical}"
        self.footer_critical.color = C.CRITICAL if n_critical else C.BORDER
        self.footer_warning.value  = f"Sắp hạn · {n_warning}"
        self.footer_warning.color  = C.WARNING if n_warning else C.BORDER
        self.footer_safe.value     = f"An toàn · {n_safe}"
        self.footer_safe.color     = C.SAFE if n_safe else C.BORDER
        self.footer_overdue.value  = f"Quá hạn · {n_overdue}"
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
                # P6: Victory state — positive reinforcement
                self._empty_icon.name = ft.Icons.EMOJI_EVENTS_ROUNDED
                self._empty_icon.color = C.SAFE
                self._empty_title.value = "Tuyệt vời! Đã nộp tất cả 🎉"
                self._empty_subtitle.value = "Bạn đã hoàn thành mọi bài tập. Nghỉ ngơi thôi!"
            elif has_filter:
                # Filter active — guide user to adjust
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
        """Full UI refresh — rebuilds filters + cards."""
        self._refresh_ui()

    def _render_cards_only(self):
        """Lightweight render — only re-filter and update card list, skip popup rebuild."""
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
            grading     = status.get("Grading status", "")
            submission  = status.get("Submission status", "")
            is_graded   = (grading in ("Graded", "Đã chấm") or (grading.startswith("Graded") and "Not" not in grading))
            is_submitted = (submission in ("Submitted for grading", "Đã nộp") or ("Submitted" in submission and "Not" not in submission) or ("Đã nộp" in submission and "Chưa" not in submission))

            if is_graded and not settings.INCLUDE_GRADED: continue
            if is_submitted and not is_graded and not settings.INCLUDE_SUBMITTED: continue
            
            # _refresh_ui handles INCLUDE_PAST_DUE now
            result.append(d)
        return result

    async def _toggle_overdue(self, e):
        settings.INCLUDE_PAST_DUE = self._overdue_cb.value
        _save_setting("INCLUDE_PAST_DUE", settings.INCLUDE_PAST_DUE)
        
        self._update_footer()

    def _on_search(self, e):
        self.active_search = (e.control.value or "").strip()
        # Debounce: 150ms — fast enough to feel instant, prevents excessive renders
        if hasattr(self, '_search_task') and self._search_task:
            self._search_task.cancel()
        async def _delayed_render():
            await asyncio.sleep(0.15)
            self._render_cards_only()
        self._search_task = self.page.run_task(_delayed_render)

    async def _prefetch_details_async(self, activities: list):
        total = len(activities)
        workers = max(1, min(settings.PREFETCH_WORKERS, 10))
        
        with self._data_lock:
            count = len(self.all_data)
        self.status_text.value = f"Cập nhật lúc {datetime.now().strftime('%H:%M')} · {count} hoạt động"
        self.loading_bar.visible = True
        self.page.update()

        done = await asyncio.to_thread(
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

        self.refresh_btn.disabled = True
        self.status_text.value    = "Đang kết nối Moodle..."
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
                self.status_text.value = f"📦 Dữ liệu cache · Đang cập nhật..."
                self._update_footer()
                self.page.update()

        try:
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
                    
            
            # Determine data source for status display
            ws_count = sum(1 for x in data_copy if x.get('source') == 'ws_api')
            source_tag = "⚡ API" if ws_count > 0 else "🌐 Web"
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
            # P3: Tính tiến độ nộp bài — positive reinforcement
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

            self._update_footer()
            
            self._prefetch_cancel_event.clear()
            with self._data_lock:
                prefetch_copy = list(self.all_data)
            self.page.run_task(self._prefetch_details_async, prefetch_copy)
        except Exception as exc:
            logger.exception(f"[Load] Lỗi: {exc}")
            with self._data_lock:
                self.all_data = []
            
            # Thử dùng cache khi offline
            cached_data, cached_at = self._data_cache.load()
            if cached_data:
                with self._data_lock:
                    self.all_data = cached_data
                self.status_text.value = f"⚡ Offline · Dữ liệu cache"
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

    async def _close_detail(self):
        self.detail_view.offset = ft.Offset(1, 0)  # slide out
        self.detail_view.opacity = 0.0
        self.page.update()
        await asyncio.sleep(0.25)  # wait for animation
        self.detail_view.visible = False
        # Return to calendar if it was the source, otherwise dashboard
        if getattr(self, '_detail_from_calendar', False):
            self._detail_from_calendar = False
            self.calendar_view.visible = True
        else:
            self.dashboard.opacity = 1.0
            self.dashboard.visible = True
        self.page.update()

    async def _toggle_calendar(self):
        """Toggle between dashboard list view and calendar view."""
        if self.calendar_view.visible:
            await self._close_calendar()
        else:
            await self._show_calendar()

    async def _show_calendar(self):
        """Show calendar view with current data."""
        self.dashboard.visible = False
        self.detail_view.visible = False
        self.settings_view.visible = False
        with self._data_lock:
            data_snapshot = list(self.all_data)
        self.calendar_view.update_data(data_snapshot)
        self.calendar_view.show()
        self.calendar_view.offset = ft.Offset(0, 0)
        self.calendar_view.opacity = 1.0
        self.calendar_btn.icon_color = C.ACCENT
        self.page.update()

    async def _close_calendar(self):
        """Return from calendar to dashboard."""
        self.calendar_view.offset = ft.Offset(1, 0)
        self.calendar_view.opacity = 0.0
        self.page.update()
        await asyncio.sleep(0.25)
        self.calendar_view.hide()
        self.dashboard.opacity = 1.0
        self.dashboard.visible = True
        self.calendar_btn.icon_color = C.TEXT_SECONDARY
        self.page.update()

    async def _show_settings(self):
        self.dashboard.visible = False
        self.settings_view.load_current_settings()
        self.settings_view.visible = True
        self.settings_view.offset = ft.Offset(0, 0)
        self.settings_view.opacity = 1.0
        self.page.update()

    async def _close_settings(self):
        from gui.core.theme import load_theme_from_settings
        load_theme_from_settings()

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
        self.settings_view.offset = ft.Offset(1, 0)
        self.settings_view.opacity = 0.0
        self.page.update()
        await asyncio.sleep(0.25)
        self.settings_view.visible = False
        self.dashboard.opacity = 1.0
        self.dashboard.visible = True
        
        self._update_footer()
        
        if getattr(self, '_needs_reload', False):
            self._needs_reload = False
            self.page.run_task(self._load_data_async)
        # _update_footer() already calls _refresh_ui() which calls page.update()
        # No extra page.update() needed

    def _show_snackbar(self, msg: str, icon=ft.Icons.CHECK_CIRCLE_ROUNDED, color=C.SAFE):
        """UX: Show a brief toast-like notification."""
        self.page.snack_bar = ft.SnackBar(
            content=ft.Row([
                ft.Icon(icon, color=color, size=16),
                ft.Text(msg, size=13, color=C.TEXT_PRIMARY),
            ], spacing=8),
            bgcolor=C.SURFACE,
            duration=2500,
        )
        self.page.snack_bar.open = True
        self.page.update()

    def _test_notification_base(self, mock_type="critical"):
        import random, datetime
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

    def _on_update_check(self, has_update: bool, version: str, url: str):
        """Callback từ background thread khi kiểm tra update xong."""
        if has_update and version:
            self._update_url = url or ""
            self._update_banner.visible = True
            self._update_banner.content.controls[1].value = f"Phiên bản mới v{version} đã sẵn sàng!"
            try:
                self.page.update()
            except Exception:
                pass

    async def _open_update_url(self, e):
        """Mở trang tải bản cập nhật trên trình duyệt."""
        if self._update_url:
            self.page.launch_url(self._update_url)

    def _on_settings_saved(self):
        self._needs_reload = True
        self._show_snackbar("Đã lưu cài đặt", ft.Icons.SAVE_ROUNDED, C.SAFE)
    def _show_detail(self, data: dict):
        self.page.run_task(self._show_detail_async, data)

    async def _show_detail_async(self, data: dict):
        # Track if detail was opened from calendar for back-navigation
        self._detail_from_calendar = self.calendar_view.visible
        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.settings_view.visible = False
        self.detail_view.offset = ft.Offset(0, 0)  # slide in
        self.detail_view.opacity = 1.0
        self.detail_view.show_loading(data)
        self.page.update()
        try:
            full_data = await asyncio.to_thread(self.orchestrator.fetch_full_details, data)
            self.detail_view.update_detail(full_data)
        except Exception:
            self.detail_view.update_detail(data)
            self.detail_view.show_error_banner()
        self.page.update()

    def _pulse_cards_once(self, cards_snapshot: list, pulse_high: bool):
        changed = False
        shadow = ActivityCard._PULSE_SHADOW_HIGH if pulse_high else ActivityCard._PULSE_SHADOW_LOW
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

    def _on_disconnect(self, e):
        self._page_alive.clear()
        self._prefetch_cancel_event.set()
        try:
            self.orchestrator.client.close()
        except Exception:
            pass
        try:
            from core.data_orchestrator import shutdown_parser_pool
            shutdown_parser_pool()
        except Exception:
            pass

