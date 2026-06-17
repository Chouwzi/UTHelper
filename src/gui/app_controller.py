import flet as ft
from datetime import datetime
from core.time_utils import parse_datetime
import asyncio
import logging
from gui.tray import TrayApp
from notifiers.manager import NotificationManager
import threading

from core.data_orchestrator import DataOrchestrator
from config import settings

from gui.core.theme import C, _TYPE_FILTER_MAP
from gui.core.utils import clean_course_name, urgency_str
from gui.components.activity_card import ActivityCard
from gui.components.detail_view import DetailView
from gui.components.settings_view import SettingsView

logger = logging.getLogger(__name__)

def _save_setting(key: str, value):
    try:
        from config import settings, save_settings
        setattr(settings, key, value)
        save_settings()
    except Exception:
        pass


class AppController:
    def __init__(self, page: ft.Page):
        self.page = page
        self._cards_lock = threading.Lock()
        self._page_alive = threading.Event()
        self._page_alive.set()
        
        self.orchestrator = DataOrchestrator()
        
        self.all_data = []
        self.active_cards = []
        self.active_urgency = "all"
        self.active_type = "all"
        self.active_course = "all"
        self.active_search = ""
        self._is_loading = False
        
        self._prefetch_cancel = False

        self._init_window()
        self._init_ui()
        
        # Events
        self.page.on_disconnect = self._on_disconnect
        self.page.run_task(self._pulse_loop_async)
        self.page.run_task(self._countdown_loop_async)
        self.page.run_task(self._auto_refresh_loop_async)
        
        if not settings.UTH_USERNAME or not settings.UTH_PASSWORD:
            self.page.run_task(self._show_login_dialog)
        else:
            self.page.run_task(self._load_data_async)

    async def _show_login_dialog(self):
        from gui.components.login_dialog import show_login_dialog
        await show_login_dialog(self.page, self.orchestrator, self._load_data_async)

    def _init_window(self):
        self.page.window.width        = 420
        self.page.window.height       = 720
        self.page.window.max_width    = 420
        self.page.window.min_width    = 420
        self.page.window.always_on_top = settings.ALWAYS_ON_TOP
        self.page.window.resizable    = False
        self.page.window.icon         = "icon.ico"  # Icon của app, để ở thư mục assets
        self.page.title               = "UTHelper"
        self.page.bgcolor             = C.BG
        self.page.padding             = 0
        self.page.spacing             = 0
        self.page.theme_mode          = ft.ThemeMode.DARK
        self.page.window.prevent_close = True
        self.page.window.on_event = self._on_window_event
        
        self.tray = TrayApp(self.page)
        self.tray.setup()
        self.notifier = NotificationManager(self.tray)
        
        # Chỉ tự ẩn xuống tray nếu app được Win gọi khởi động (có cờ --autostart)
        import sys
        if settings.START_MINIMIZED and "--autostart" in sys.argv:
            self.page.window.visible = False
        else:
            self.page.window.visible = True
            
        self.page.update()

    async def _on_window_event(self, e):
        # Flet bản mới thì sự kiện đóng cửa sổ nằm ở e.type hoặc e.data
        if getattr(e, "type", getattr(e, "data", "")) == ft.WindowEventType.CLOSE or e.data == "close":
            if settings.MINIMIZE_TO_TRAY:
                self.page.window.visible = False
                # Ẩn đi chứ không đóng hẳn, nhờ có prevent_close=True ở trên
                self.page.update()
            else:
                await self.page.window.destroy()

    def _init_ui(self):
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
                    ft.Container(content=self.course_btn_label, width=60, alignment=ft.Alignment(-1, 0)),
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
            height=38,
            expand=True,
            content_padding=ft.Padding.only(right=10, top=8, bottom=8),
            on_change=self._on_search,
        )

        # Header
        self.refresh_btn  = ft.IconButton(
            ft.Icons.REFRESH_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=18, tooltip="Làm mới",
            on_click=lambda e: self.page.run_task(self._load_data_async),
        )
        self.settings_btn = ft.IconButton(
            ft.Icons.SETTINGS_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=18, tooltip="Cài đặt",
            on_click=lambda e: self.page.run_task(self._show_settings),
        )

        header = ft.Container(
            content=ft.Column(controls=[
                ft.Row(controls=[
                    ft.Text("UTHelper", size=18, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY),
                    ft.Row(controls=[self.refresh_btn, self.settings_btn], spacing=0),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                self.status_text,
                self.loading_bar,
            ], spacing=4),
            padding=ft.Padding.only(left=16, right=8, top=20, bottom=8),
            bgcolor=C.BG,
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

        self.cards_column = ft.ListView(spacing=8, expand=True)
        self.empty_state  = ft.Container(
            content=ft.Column(controls=[
                ft.Text("Không có hoạt động nào", size=14, color=C.TEXT_SECONDARY, weight=ft.FontWeight.W_500),
                ft.Text("Không có thông báo mới", size=12, color=C.BORDER),
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
            bgcolor=C.SURFACE, padding=ft.Padding.symmetric(vertical=8),
            border=ft.Border.only(top=ft.BorderSide(1, C.BORDER)),
        )

        self.dashboard = ft.Column(
            controls=[header, filter_container, content_area, footer],
            spacing=0, expand=True,
        )

        self.detail_view   = DetailView(self.page, on_close=lambda: self.page.run_task(self._close_detail), get_client=lambda: self.orchestrator.client)
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

        self.page.add(ft.Stack(controls=[self.dashboard, self.detail_view, self.settings_view], expand=True))

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
                bgcolor=C.SURFACE, border=ft.border.all(1, C.BORDER), border_radius=10, padding=ft.Padding.symmetric(horizontal=10, vertical=8),
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
        # Lọc sơ bộ dữ liệu theo cài đặt chung
        base = self._apply_settings_filter(self.all_data)
        
        from core.filter_service import FilterService
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

        self.footer_critical.value = f"Khẩn cấp · {n_critical}" if n_critical else ""
        self.footer_warning.value  = f"Sắp đến hạn · {n_warning}" if n_warning else ""
        self.footer_safe.value     = f"An toàn · {n_safe}" if n_safe else ""
        self.footer_overdue.value  = f"Quá hạn · {n_overdue}" if n_overdue else ""

        self._update_urgency_counts(counts["urgency"])
        self._update_type_counts(type_counts)

        # Render cards
        self.empty_state.visible = (len(filtered_items) == 0 and not self.loading_bar.visible)
        self.error_state.visible = False

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
        self._refresh_ui()

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
            is_graded   = "Graded" in grading or "Đã chấm" in grading
            is_submitted = "Submitted" in submission or "Đã nộp" in submission

            if is_graded and not settings.INCLUDE_GRADED: continue
            if is_submitted and not is_graded and not settings.INCLUDE_SUBMITTED: continue
            
            # _refresh_ui handles INCLUDE_PAST_DUE now
            result.append(d)
        return result

    async def _toggle_overdue(self, e):
        settings.INCLUDE_PAST_DUE = self._overdue_cb.value
        _save_setting("INCLUDE_PAST_DUE", settings.INCLUDE_PAST_DUE)
        
        self._update_footer()
        self.page.update()

    def _on_search(self, e):
        self.active_search = (e.control.value or "").strip()
        self._render_cards()
        self.page.update()

    async def _prefetch_details_async(self, activities: list):
        total = len(activities)
        workers = max(1, min(settings.PREFETCH_WORKERS, 10))
        
        self.status_text.value = f"Cập nhật lúc {datetime.now().strftime('%H:%M')} • {len(self.all_data)} hoạt động  (đang cập nhật chi tiết...)"
        self.loading_bar.visible = True
        self.page.update()

        done = await asyncio.to_thread(
            self.orchestrator.prefetch_all_details,
            activities,
            workers,
            lambda: self._prefetch_cancel,
            False
        )

        if not self._prefetch_cancel and not self._is_loading:
            cache = self.orchestrator.get_cached_details_snapshot()
            for i, item in enumerate(self.all_data):
                url = item.get("url")
                if url and url in cache:
                    enriched = cache[url]
                    self.all_data[i] = {
                        **item,
                        "type": enriched.get("type", item.get("type", "other")),
                        "course": enriched.get("course", item.get("course", "")),
                        "submission_status": enriched.get("submission_status", "unknown"),
                        "details": enriched.get("details", {}),
                        "deadline": enriched.get("deadline", item.get("deadline")),
                        "is_open": enriched.get("is_open", item.get("is_open")),
                        "urgency": enriched.get("urgency", item.get("urgency")),
                    }
            
            self.all_data.sort(key=lambda x: (
                0 if x.get("urgency") == "critical" else 1 if x.get("urgency") == "warning" else 2,
                x.get("deadline", "")
            ))
            self._update_footer()
            self.status_text.value = f"Cập nhật lúc {datetime.now().strftime('%H:%M')} • {len(self.all_data)} hoạt động  ✓ sẵn sàng"
            self.loading_bar.visible = False
            self.page.update()

    async def _load_data_async(self):
        if self._is_loading: return
        self._is_loading = True
        self._prefetch_cancel = True

        self.refresh_btn.disabled = True
        self.status_text.value    = "Đang kết nối Moodle..."
        self.loading_bar.visible  = True
        self.empty_state.visible  = False
        self.error_state.visible  = False
        self.page.update()

        try:
            result   = await asyncio.to_thread(self.orchestrator.get_latest_activities)
            self.all_data = result or []
            
            cache = self.orchestrator.get_cached_details_snapshot()
            for i, item in enumerate(self.all_data):
                url = item.get("url")
                if url and url in cache:
                    enriched = cache[url]
                    self.all_data[i] = {
                        **item,
                        "type": enriched.get("type", item.get("type", "other")),
                        "course": enriched.get("course", item.get("course", "")),
                        "submission_status": enriched.get("submission_status", "unknown"),
                        "details": enriched.get("details", {}),
                        "deadline": enriched.get("deadline", item.get("deadline")),
                        "is_open": enriched.get("is_open", item.get("is_open")),
                        "urgency": enriched.get("urgency", item.get("urgency")),
                    }
                    
            
            self.status_text.value = f"Cập nhật lúc {datetime.now().strftime('%H:%M')} • {len(self.all_data)} hoạt động"
            self.all_data.sort(key=lambda x: (
                0 if x.get("urgency") == "critical" else 1 if x.get("urgency") == "warning" else 2,
                x.get("deadline", "")
            ))
            
            # Bắn thông báo thông minh cho người dùng
            if hasattr(self, 'notifier') and self.notifier:
                try:
                    self.notifier.dispatch(self.all_data)
                except Exception as e:
                    logger.error(f"[UTHelper] Dispatcher lỗi: {e}")

            self._update_footer()
            
            self._prefetch_cancel = False
            self.page.run_task(self._prefetch_details_async, list(self.all_data))
        except Exception as exc:
            logger.exception(f"[Load] Lỗi: {exc}")
            self.all_data = []
            
            self.error_text.value = f"Lỗi kết nối: {str(exc)[:70]}"
            self.error_state.visible = True
            self.status_text.value = "Lỗi kết nối server"
        finally:
            self.loading_bar.visible  = False
            self.refresh_btn.disabled = False
            self._is_loading          = False
            self.page.update()

    async def _close_detail(self):
        self.detail_view.visible = False
        self.dashboard.visible   = True
        self.page.update()

    async def _show_settings(self):
        self.dashboard.visible = False
        self.settings_view.load_current_settings()
        self.settings_view.visible = True
        self.page.update()

    async def _close_settings(self):
        from gui.core.theme import load_theme_from_settings
        load_theme_from_settings()

        # Lưu lại trạng thái hiện tại để tí phục hồi
        old_data = self.all_data
        old_urgency = getattr(self, "active_urgency", "all")
        old_type = getattr(self, "active_type", "all")
        old_course = getattr(self, "active_course", "all")
        old_search = getattr(self, "active_search", "")
        
        # Clear specific lists and the page
        # Preserve _reusable_cards to prevent flash-bang & memory thrashing
        if hasattr(self, '_reusable_cards'):
            for card in self._reusable_cards:
                card.update_data(card.data, force=True)
                
        self.page.controls.clear()
        
        # Re-build UI completely
        self._init_ui()
        
        # Recalculate urgency dynamically using new settings 
        from datetime import datetime
        for d in old_data:
            dt_str = d.get("deadline")
            if dt_str:
                dt = parse_datetime(dt_str)
                if dt:
                    diff_h = (dt - datetime.now()).total_seconds() / 3600
                    if diff_h < settings.URGENCY_CRITICAL_HOURS:
                        d["urgency"] = "critical"
                    elif diff_h < settings.URGENCY_WARNING_HOURS:
                        d["urgency"] = "warning"
                    else:
                        d["urgency"] = "safe"
        
        # Restore states
        self.all_data = old_data
        self.active_urgency = old_urgency
        self.active_type = old_type
        self.active_course = old_course
        self.active_search = old_search
        
        self.settings_view.visible = False
        self.dashboard.visible = True
        
        self._update_footer()
        
        if getattr(self, '_needs_reload', False):
            self._needs_reload = False
            self.page.run_task(self._load_data_async)
        else:
            self.status_text.value = f"Cập nhật lúc {datetime.now().strftime('%H:%M')} • {len(self.all_data)} hoạt động  ✓ sẵn sàng"
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

    def _on_settings_saved(self):
        self._needs_reload = True
    def _show_detail(self, data: dict):
        self.page.run_task(self._show_detail_async, data)

    async def _show_detail_async(self, data: dict):
        self.dashboard.visible = False
        self.settings_view.visible = False
        self.detail_view.show_loading(data)
        self.page.update()
        try:
            full_data = await asyncio.to_thread(self.orchestrator.fetch_full_details, data)
            self.detail_view.update_detail(full_data)
        except Exception:
            self.detail_view.update_detail(data)
        self.page.update()

    def _pulse_cards_once(self, cards_snapshot: list, pulse_high: bool):
        changed = False
        for card in cards_snapshot:
            if getattr(card, "_is_critical_active", False):
                card.shadow = [ft.BoxShadow(
                    spread_radius=1 if pulse_high else 0,
                    blur_radius=4 if pulse_high else 3,
                    color="#BBEF4444" if pulse_high else "#33EF4444",
                    offset=ft.Offset(0, 0),
                )]
                changed = True
        if changed:
            self.page.update()

    def _countdown_cards_once(self, cards_snapshot: list):
        if not cards_snapshot:
            return
        for card in cards_snapshot:
            card.update_countdown()
        self.page.update()

    async def _pulse_loop_async(self):
        pulse_high = True
        while self._page_alive.is_set():
            await asyncio.sleep(0.8)
            if not self._page_alive.is_set(): break
            pulse_high = not pulse_high
            try:
                # Skip lock acquisition when there are no cards to pulse
                if not self.active_cards:
                    continue
                with self._cards_lock:
                    cards_snapshot = list(self.active_cards)
                self._pulse_cards_once(cards_snapshot, pulse_high)
            except Exception:
                pass

    async def _countdown_loop_async(self):
        while self._page_alive.is_set():
            slept = 0
            while slept < 60 and self._page_alive.is_set():
                await asyncio.sleep(1)
                slept += 1
            if not self._page_alive.is_set(): break
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
                await self._load_data_async()
            except Exception:
                pass

    def _on_disconnect(self, e):
        self._page_alive.clear()

