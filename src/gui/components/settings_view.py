import os
import sys

# Patch path for direct execution / Flet preview compatibility
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import flet as ft
import asyncio
from gui.core.theme import C, THEME_PRESETS, THEME_ORDER, apply_theme
from config import settings
from config import save_settings
import platform_utils as _pu  # use _pu.IS_MOBILE for live platform detection

class SettingsView(ft.Container):
    def __init__(self, page: ft.Page, orchestrator, on_close, on_saved=None, on_test_tray=None, on_test_mobile=None, on_test_tele=None, on_test_discord=None, on_test_mail=None, on_theme_preview=None):
        super().__init__()
        self._page    = page
        self._orchestrator = orchestrator
        self._on_close_cb = on_close
        self._on_saved = on_saved
        self._on_test_tray = on_test_tray
        self._on_test_mobile = on_test_mobile
        self._on_test_tele = on_test_tele
        self._on_test_discord = on_test_discord
        self._on_test_mail = on_test_mail
        self._on_theme_preview = on_theme_preview
        self.visible  = False
        self.expand   = True
        self.bgcolor  = C.BG
        self._selected_theme = getattr(settings, 'THEME', 'midnight_blue')
        self._original_theme = self._selected_theme  # For revert on discard
        self._themed_texts = []  # Track ft.Text instances for live theme refresh (early init)

        self._username_field = ft.TextField(
            value=settings.UTH_USERNAME,
            label="Mã số sinh viên (MSSV)",
            text_size=14, label_style=ft.TextStyle(size=13, color=C.TEXT_SECONDARY),
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY,
            bgcolor=C.BG, border_radius=10,
            on_submit=lambda e: self._password_field.focus()
        )
        self._password_field = ft.TextField(
            label="Mật khẩu",
            value=settings.UTH_PASSWORD,
            text_size=14, label_style=ft.TextStyle(size=13, color=C.TEXT_SECONDARY),
            password=True, can_reveal_password=True,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY,
            bgcolor=C.BG, border_radius=10,
            on_submit=self._handle_test_login
        )

        self._test_login_btn = ft.Button(
            "Kiểm tra kết nối",
            icon=ft.Icons.WIFI_FIND_ROUNDED,
            on_click=self._handle_test_login,
            style=ft.ButtonStyle(
                color=ft.Colors.WHITE,
                bgcolor=C.ACCENT,
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=12,
                animation_duration=300
            ),
            height=44
        )
        self._test_loading_bar = ft.ProgressBar(color=C.ACCENT, bgcolor=C.SURFACE, visible=False)
        self._test_login_status = ft.Text("", size=12, text_align=ft.TextAlign.CENTER)

        self._sw_always_on_top = ft.Switch(

            value=settings.ALWAYS_ON_TOP, active_color=C.ACCENT,
            label="Luôn ở trên cùng",
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        )

        self._sw_submitted = ft.Switch(
            value=settings.INCLUDE_SUBMITTED, active_color=C.ACCENT,
            label="Hiển thị bài đã nộp",
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        )
        self._sw_graded = ft.Switch(
            value=settings.INCLUDE_GRADED, active_color=C.ACCENT,
            label="Hiển thị bài đã chấm",
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        )

        self._sw_start_with_windows = ft.Switch(
            value=settings.START_WITH_WINDOWS, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Khởi động cùng Windows"
        )

        def open_color_picker(e, container_box, label, tb_field):
            def _update_from_sliders(e):
                hex_val = f"#{int(r_sl.value):02X}{int(g_sl.value):02X}{int(b_sl.value):02X}"
                prv.bgcolor = hex_val
                prv.update()
                hex_inp.value = hex_val
                hex_inp.update()
                
            def _update_from_hex(e):
                try:
                    val = hex_inp.value.strip().lstrip('#')
                    if len(val) == 6:
                        r, g, b = int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16)
                        r_sl.value, g_sl.value, b_sl.value = r, g, b
                        r_sl.update(); g_sl.update(); b_sl.update()
                        prv.bgcolor = f"#{val}"
                        prv.update()
                except:
                    pass

            curr = tb_field.value.lstrip('#')
            try: r_v, g_v, b_v = int(curr[0:2], 16), int(curr[2:4], 16), int(curr[4:6], 16)
            except: r_v, g_v, b_v = 0, 0, 0

            r_sl = ft.Slider(min=0, max=255, value=r_v, active_color=ft.Colors.RED_400, on_change=_update_from_sliders, expand=True)
            g_sl = ft.Slider(min=0, max=255, value=g_v, active_color=ft.Colors.GREEN_400, on_change=_update_from_sliders, expand=True)
            b_sl = ft.Slider(min=0, max=255, value=b_v, active_color=ft.Colors.BLUE_400, on_change=_update_from_sliders, expand=True)
            
            hex_inp = ft.TextField(value=tb_field.value, on_change=_update_from_hex, text_align=ft.TextAlign.CENTER, border_radius=8, content_padding=5, text_size=13, width=100)
            prv = ft.Container(width=100, height=40, bgcolor=tb_field.value, border_radius=8, border=ft.Border.all(1, C.BORDER))

            def _apply(e):
                container_box.bgcolor = hex_inp.value
                tb_field.value = hex_inp.value
                container_box.update()
                tb_field.update()
                dlg.open = False
                try:
                    self._page.overlay.remove(dlg)
                except (ValueError, AttributeError):
                    pass
                self._page.update()

            def _cancel(e):
                dlg.open = False
                try:
                    self._page.overlay.remove(dlg)
                except (ValueError, AttributeError):
                    pass
                self._page.update()

            dlg = ft.AlertDialog(
                title=ft.Text(f"Chọn màu: {label}", size=16, weight=ft.FontWeight.BOLD),
                content=ft.Container(
                    width=300,
                    content=ft.Column([
                        ft.Row([prv, hex_inp], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        ft.Container(height=10),
                        ft.Row([ft.Text("R", color=ft.Colors.RED_400, weight=ft.FontWeight.BOLD, width=20), r_sl]),
                        ft.Row([ft.Text("G", color=ft.Colors.GREEN_400, weight=ft.FontWeight.BOLD, width=20), g_sl]),
                        ft.Row([ft.Text("B", color=ft.Colors.BLUE_400, weight=ft.FontWeight.BOLD, width=20), b_sl]),
                    ], tight=True)
                ),
                actions=[
                    ft.TextButton("Hủy", on_click=_cancel),
                    ft.Button("Áp dụng", on_click=_apply, bgcolor=C.ACCENT, color=ft.Colors.WHITE),
                ],
                shape=ft.RoundedRectangleBorder(radius=12)
            )
            self._page.overlay.append(dlg)
            dlg.open = True
            self._page.update()

        def _color_field(label_text, default_color):
            tb = ft.TextField(value=default_color, width=90, text_size=12, height=36, border_color=C.BORDER, focused_border_color=C.ACCENT, color=C.TEXT_PRIMARY, bgcolor=C.BG, content_padding=6)
            box = ft.Container(width=24, height=24, bgcolor=default_color, border_radius=4, border=ft.Border.all(1, "#333333"), ink=True)
            
            # Click event for the box
            def _on_box_click(e):
                open_color_picker(e, box, label_text, tb)
                
            # Allow click wrapping
            box_click = ft.GestureDetector(
                content=box,
                on_tap=_on_box_click,
                mouse_cursor=ft.MouseCursor.CLICK
            )

            def _on_change(e):
                box.bgcolor = tb.value
                box.update()
            tb.on_change = _on_change
            label_txt = ft.Text(label_text, size=13, color=C.TEXT_PRIMARY, expand=True)
            self._themed_texts.append(label_txt)
            return tb, ft.Row([label_txt, box_click, tb], spacing=10, tight=True)

        self._c_tb_critical, row_cri = _color_field("Cấp bách / Quá hạn", getattr(settings, 'COLOR_CRITICAL', '#EF4444'))
        self._c_tb_warning, row_warn = _color_field("Sắp tới", getattr(settings, 'COLOR_WARNING', '#F59E0B'))
        self._c_tb_safe, row_safe = _color_field("An toàn / Thường", getattr(settings, 'COLOR_SAFE', '#10B981'))
        self._c_tb_quiz, row_quiz = _color_field("Tag Quiz", getattr(settings, 'COLOR_QUIZ', '#7C3AED'))
        self._c_tb_ass, row_ass = _color_field("Tag Bài tập", getattr(settings, 'COLOR_ASSIGNMENT', '#2563EB'))
        self._c_tb_att, row_att = _color_field("Tag Điểm danh", getattr(settings, 'COLOR_ATTENDANCE', '#D97706'))
        self._c_tb_open, row_open = _color_field("Tag Sắp mở", getattr(settings, 'COLOR_OPEN', '#0891B2'))
        self._c_tb_other, row_other = _color_field("Tag Sự kiện", getattr(settings, 'COLOR_OTHER', '#6B7280'))
        
        # ── Theme Selector ──
        self._theme_cards_row = self._build_theme_selector()

        self.btn_reset = ft.OutlinedButton("Khôi phục mặc định", width=400, on_click=self._handle_reset_defaults, style=ft.ButtonStyle(color=C.TEXT_SECONDARY))

        self._sw_start_minimized = ft.Switch(
            value=settings.START_MINIMIZED, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Khởi động ở chế độ thu nhỏ"
        )
        self._sw_minimize_to_tray = ft.Switch(
            value=settings.MINIMIZE_TO_TRAY, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Thu nhỏ vào khay hệ thống"
        )

        # Android background check switch
        self._sw_bg_check = ft.Switch(
            value=settings.BACKGROUND_CHECK_ANDROID, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Kiểm tra deadline khi thu nhỏ (Android)",
            on_change=lambda e: self._toggle_bg_check_ui()
        )
        self._bg_interval_field = ft.TextField(
            value=str(settings.BACKGROUND_CHECK_INTERVAL),
            label="Tần suất kiểm tra nền (phút)",
            text_size=13,
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
            width=200,
            visible=settings.BACKGROUND_CHECK_ANDROID,
        )

        self._sw_email = ft.Switch(
            value=settings.ENABLE_GMAIL, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Kích hoạt thông báo qua Gmail",
            on_change=lambda e: self._toggle_integration_ui()
        )
        self._gmail_addr_field = ft.TextField(
            value=getattr(settings, 'GMAIL_ADDRESS', ''),
            label="Địa chỉ Email",
            visible=settings.ENABLE_GMAIL,
            border_color=C.BORDER, focused_border_color=C.ACCENT, color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        )
        self._gmail_pw_field = ft.TextField(
            value=getattr(settings, 'GMAIL_APP_PASSWORD', ''),
            label="Mật khẩu ứng dụng Gmail",
            password=True, can_reveal_password=True,
            visible=settings.ENABLE_GMAIL,
            border_color=C.BORDER, focused_border_color=C.ACCENT, color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        )

        self._sw_discord = ft.Switch(
            value=settings.ENABLE_DISCORD, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Kích hoạt thông báo qua Discord",
            on_change=lambda e: self._toggle_integration_ui()
        )
        self._discord_wh_field = ft.TextField(
            value=getattr(settings, 'DISCORD_WEBHOOK_URL', ''),
            label="Discord Webhook URL",
            visible=settings.ENABLE_DISCORD,
            border_color=C.BORDER, focused_border_color=C.ACCENT, color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        )

        self._sw_telegram = ft.Switch(
            value=settings.ENABLE_TELEGRAM, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Kích hoạt thông báo qua Telegram",
            on_change=lambda e: self._toggle_telegram_ui()
        )
        self._tel_token_field = ft.TextField(
            value=settings.TELEGRAM_BOT_TOKEN,
            label="Bot Token",
            text_size=13,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
            visible=settings.ENABLE_TELEGRAM
        )
        self._tel_chat_field = ft.TextField(
            value=settings.TELEGRAM_CHAT_ID,
            label="Chat ID",
            text_size=13,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
            visible=settings.ENABLE_TELEGRAM
        )
        self._sw_debug = ft.Switch(
            value=settings.DEBUG_MODE, active_color=C.CRITICAL,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Chế độ Gỡ lỗi (Debug Log)",
            on_change=lambda e: self._toggle_debug_ui()
        )
        
        self._mock_type_drp = ft.Dropdown(
            value="critical",
            options=[
                ft.dropdown.Option("critical", "Khẩn cấp (< 24h)"),
                ft.dropdown.Option("warning", "Cảnh báo (2-3 ngày)"),
                ft.dropdown.Option("safe", "An toàn (> 3 ngày)"),
                ft.dropdown.Option("quiz", "Bài Quiz"),
                ft.dropdown.Option("attendance", "Điểm danh"),
            ],
            label="Loại Mock Data",
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=8,
            text_size=13
        )
        # ── Debug panel: Comprehensive test tools ──
        # Section 1: Notification channel tests (platform-aware)
        _notif_section_label = ft.Text("Kiểm thử kênh thông báo", size=12, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY)
        _tray_btn = ft.Button("Windows Tray", icon=ft.Icons.DESKTOP_WINDOWS_ROUNDED, on_click=lambda e: self._do_test_tray(), bgcolor=C.SURFACE, color=C.TEXT_PRIMARY)
        _mobile_btn = ft.Button("Mobile", icon=ft.Icons.PHONE_ANDROID_ROUNDED, on_click=lambda e: self._do_test_mobile(), bgcolor=C.SURFACE, color=C.ACCENT)
        _notif_buttons = ft.Row([
            *([_tray_btn] if not _pu.IS_MOBILE else [_mobile_btn]),
            ft.Button("Telegram", icon=ft.Icons.TELEGRAM_ROUNDED, on_click=lambda e: self._do_test_tele(), bgcolor=C.SURFACE, color="#0088cc"),
            ft.Button("Discord", icon=ft.Icons.DISCORD_ROUNDED, on_click=lambda e: self._do_test_discord(), bgcolor=C.SURFACE, color="#5865F2"),
            ft.Button("Gmail", icon=ft.Icons.EMAIL_ROUNDED, on_click=lambda e: self._do_test_mail(), bgcolor=C.SURFACE, color="#EA4335"),
            ft.Button("Gửi tất cả", icon=ft.Icons.CAMPAIGN_ROUNDED, on_click=lambda e: self._do_test_broadcast(), bgcolor=C.SURFACE, color=C.CRITICAL),
        ], wrap=True, spacing=6)

        # Section 2: System diagnostics
        _sys_section_label = ft.Text("Chẩn đoán hệ thống", size=12, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY)
        self._debug_info_text = ft.Text("", size=11, color=C.TEXT_SECONDARY, selectable=True)
        _sys_buttons = ft.Row([
            ft.Button("Thông tin thiết bị", icon=ft.Icons.INFO_OUTLINE_ROUNDED, on_click=lambda e: self._do_show_device_info(), bgcolor=C.SURFACE, color=C.TEXT_PRIMARY),
            ft.Button("Kết nối Moodle", icon=ft.Icons.CLOUD_SYNC_ROUNDED, on_click=lambda e: self._do_test_moodle_connection(), bgcolor=C.SURFACE, color=C.ACCENT),
            ft.Button("Ping Moodle", icon=ft.Icons.SPEED_ROUNDED, on_click=lambda e: self._do_test_latency(), bgcolor=C.SURFACE, color=C.ACCENT),
            ft.Button("Notifiers", icon=ft.Icons.LIST_ALT_ROUNDED, on_click=lambda e: self._do_show_notifiers(), bgcolor=C.SURFACE, color=C.TEXT_PRIMARY),
            ft.Button("DND Status", icon=ft.Icons.DO_NOT_DISTURB_ROUNDED, on_click=lambda e: self._do_check_dnd(), bgcolor=C.SURFACE, color=C.TEXT_PRIMARY),
        ], wrap=True, spacing=6)

        # Section 3: Cache & data management
        _cache_section_label = ft.Text("Bộ nhớ đệm & dữ liệu", size=12, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY)
        self._debug_cache_stats = ft.Text("", size=10, color=C.TEXT_SECONDARY, selectable=True)
        _cache_buttons = ft.Row([
            ft.Button("Thống kê cache", icon=ft.Icons.ANALYTICS_ROUNDED, on_click=lambda e: self._do_show_cache_stats(), bgcolor=C.SURFACE, color=C.TEXT_PRIMARY),
            ft.Button("Xóa cache thông báo", icon=ft.Icons.NOTIFICATIONS_OFF_ROUNDED, on_click=lambda e: self._do_clear_notif_cache(), bgcolor=C.SURFACE, color=C.WARNING),
            ft.Button("Xóa lịch sử", icon=ft.Icons.DELETE_SWEEP_ROUNDED, on_click=lambda e: self._do_clear_notif_history(), bgcolor=C.SURFACE, color=C.WARNING),
            ft.Button("Xóa cache offline", icon=ft.Icons.CACHED_ROUNDED, on_click=lambda e: self._do_clear_data_cache(), bgcolor=C.SURFACE, color=C.WARNING),
        ], wrap=True, spacing=6)

        # Section 3b: Notification history viewer
        _history_section_label = ft.Text("Lịch sử thông báo đã gửi", size=12, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY)
        self._debug_history_text = ft.Text("", size=10, color=C.TEXT_SECONDARY, selectable=True, max_lines=15)
        _history_buttons = ft.Row([
            ft.Button("Xem gần đây", icon=ft.Icons.HISTORY_ROUNDED, on_click=lambda e: self._do_show_notif_history(), bgcolor=C.SURFACE, color=C.TEXT_PRIMARY),
        ], wrap=True, spacing=6)

        # Section 4: Background scheduler (Android only)
        _bg_section_label = ft.Text("Background Scheduler (Android)", size=12, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY)
        self._debug_scheduler_status = ft.Text("", size=11, color=C.TEXT_SECONDARY, selectable=True)
        _bg_buttons = ft.Row([
            ft.Button("Trạng thái", icon=ft.Icons.QUERY_STATS_ROUNDED, on_click=lambda e: self._do_show_scheduler_status(), bgcolor=C.SURFACE, color=C.TEXT_PRIMARY),
            ft.Button("Start Foreground", icon=ft.Icons.PLAY_CIRCLE_OUTLINE_ROUNDED, on_click=lambda e: self._do_start_foreground(), bgcolor=C.SURFACE, color=C.SAFE),
            ft.Button("Stop Foreground", icon=ft.Icons.STOP_CIRCLE_OUTLINED, on_click=lambda e: self._do_stop_foreground(), bgcolor=C.SURFACE, color=C.CRITICAL),
            ft.Button("Test Immediate", icon=ft.Icons.NOTIFICATION_ADD_ROUNDED, on_click=lambda e: self._do_test_immediate_notif(), bgcolor=C.SURFACE, color=C.ACCENT),
        ], wrap=True, spacing=6)

        # Section 4b: Mobile-specific tests (Android/iOS)
        _mobile_section_label = ft.Text("Kiểm thử Mobile", size=12, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY)
        self._debug_mobile_text = ft.Text("", size=11, color=C.TEXT_SECONDARY, selectable=True)
        _mobile_test_buttons = ft.Row([
            ft.Button("Quyền thông báo", icon=ft.Icons.NOTIFICATIONS_ACTIVE_ROUNDED, on_click=lambda e: self._do_check_notif_permission(), bgcolor=C.SURFACE, color=C.ACCENT),
            ft.Button("Notif Critical", icon=ft.Icons.ERROR_ROUNDED, on_click=lambda e: self._do_mock_mobile_notif("critical"), bgcolor=C.SURFACE, color=C.CRITICAL),
            ft.Button("Notif Warning", icon=ft.Icons.WARNING_ROUNDED, on_click=lambda e: self._do_mock_mobile_notif("warning"), bgcolor=C.SURFACE, color=C.WARNING),
            ft.Button("Notif Safe", icon=ft.Icons.CHECK_CIRCLE_ROUNDED, on_click=lambda e: self._do_mock_mobile_notif("safe"), bgcolor=C.SURFACE, color=C.SAFE),
        ], wrap=True, spacing=6)
        _mobile_test_buttons2 = ft.Row([
            ft.Button("Backend Info", icon=ft.Icons.INFO_ROUNDED, on_click=lambda e: self._do_show_mobile_backend(), bgcolor=C.SURFACE, color=C.TEXT_PRIMARY),
            ft.Button("Rung thiết bị", icon=ft.Icons.VIBRATION_ROUNDED, on_click=lambda e: self._do_test_vibration(), bgcolor=C.SURFACE, color=C.ACCENT),
            ft.Button("Pin & tối ưu", icon=ft.Icons.BATTERY_SAVER_ROUNDED, on_click=lambda e: self._do_check_battery_opt(), bgcolor=C.SURFACE, color=C.WARNING),
            ft.Button("Multi Notif (x3)", icon=ft.Icons.DYNAMIC_FEED_ROUNDED, on_click=lambda e: self._do_mock_multi_notif(), bgcolor=C.SURFACE, color=C.ACCENT),
        ], wrap=True, spacing=6)

        # Section 5: Update checker
        _update_section_label = ft.Text("Kiểm tra cập nhật", size=12, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY)
        self._debug_update_text = ft.Text("", size=11, color=C.TEXT_SECONDARY, selectable=True)
        _update_buttons = ft.Row([
            ft.Button("Force check update", icon=ft.Icons.SYSTEM_UPDATE_ROUNDED, on_click=lambda e: self._do_force_check_update(), bgcolor=C.SURFACE, color=C.ACCENT),
        ], wrap=True, spacing=6)

        # Section 6: Data & actions
        _action_section_label = ft.Text("Hành động nhanh", size=12, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY)
        _action_buttons = ft.Row([
            ft.Button("Force Refresh", icon=ft.Icons.REFRESH_ROUNDED, on_click=lambda e: self._do_force_refresh(), bgcolor=C.SURFACE, color=C.ACCENT),
            ft.Button("Reset Settings", icon=ft.Icons.RESTART_ALT_ROUNDED, on_click=lambda e: self._do_reset_settings(), bgcolor=C.SURFACE, color=C.CRITICAL),
        ], wrap=True, spacing=6)

        # Assemble debug panel with platform-aware sections
        _debug_sections = [
            ft.Text("Công cụ gỡ lỗi nâng cao", color=C.CRITICAL, weight=ft.FontWeight.BOLD, size=14),
            ft.Divider(height=1, color=C.BORDER),
            # Notification tests
            _notif_section_label,
            self._mock_type_drp,
            _notif_buttons,
            ft.Divider(height=1, color=C.BORDER),
            # System diagnostics
            _sys_section_label,
            _sys_buttons,
            self._debug_info_text,
            ft.Divider(height=1, color=C.BORDER),
            # Cache management
            _cache_section_label,
            _cache_buttons,
            self._debug_cache_stats,
            ft.Divider(height=1, color=C.BORDER),
            # Notification history
            _history_section_label,
            _history_buttons,
            self._debug_history_text,
        ]

        # Android-only: background scheduler controls
        if _pu.IS_MOBILE:
            _debug_sections.extend([
                ft.Divider(height=1, color=C.BORDER),
                _bg_section_label,
                _bg_buttons,
                self._debug_scheduler_status,
                ft.Divider(height=1, color=C.BORDER),
                _mobile_section_label,
                _mobile_test_buttons,
                _mobile_test_buttons2,
                self._debug_mobile_text,
            ])

        # Update checker + quick actions (all platforms)
        _debug_sections.extend([
            ft.Divider(height=1, color=C.BORDER),
            _update_section_label,
            _update_buttons,
            self._debug_update_text,
            ft.Divider(height=1, color=C.BORDER),
            _action_section_label,
            _action_buttons,
        ])

        self._test_panel = ft.Container(
            content=ft.Column(_debug_sections, spacing=8, scroll=ft.ScrollMode.AUTO),
            visible=settings.DEBUG_MODE,
            padding=12, border=ft.Border.all(1, C.CRITICAL), border_radius=10, margin=ft.Margin.only(top=10)
        )
        
        self._interval_field = ft.TextField(
            value=str(settings.CHECK_INTERVAL_MINUTES),
            label="Cập nhật mỗi X phút (0 để tắt)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY,
            bgcolor=C.BG, border_radius=10,
        )
        self._dd_poll_interval = ft.Dropdown(
            label="Tần suất kiểm tra tự động",
            value=str(getattr(settings, 'POLL_INTERVAL_MINUTES', 15)),
            options=[
                ft.dropdown.Option("5", "5 phút"),
                ft.dropdown.Option("10", "10 phút"),
                ft.dropdown.Option("15", "15 phút (mặc định)"),
                ft.dropdown.Option("30", "30 phút"),
                ft.dropdown.Option("60", "1 giờ"),
            ],
            text_size=13, color=C.TEXT_PRIMARY,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            bgcolor=C.BG, border_radius=10,
            width=250,
        )
        self._fetch_months_field = ft.TextField(
            value=str(settings.FETCH_MONTHS),
            label="Số tháng lấy sự kiện (1-3)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY,
            bgcolor=C.BG, border_radius=10,
        )
        self._critical_hours_field = ft.TextField( 
            value=str(settings.URGENCY_CRITICAL_HOURS),
            label="Cấp bách khi dưới (Giờ)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, 
            bgcolor=C.BG, border_radius=10,
        )
        self._warning_hours_field = ft.TextField(
            value=str(settings.URGENCY_WARNING_HOURS),
            label="Sắp tới khi dưới (Giờ)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, 
            bgcolor=C.BG, border_radius=10,
        )
        self._opening_soon_hours_field = ft.TextField(
            value=str(settings.OPENING_SOON_HOURS),
            label="Sắp mở khi dưới (Giờ)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY,
            bgcolor=C.BG, border_radius=10,
        )
        self._notify_min_field = ft.TextField( 
            value=str(settings.NOTIFY_MINUTES_BEFORE),
            label="Thông báo trước deadline (Phút)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, 
            bgcolor=C.BG, border_radius=10,
        )
        self._workers_field = ft.TextField( 
            value=str(settings.PREFETCH_WORKERS),
            label="Số luồng tải đồng thời (1-10)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, 
            bgcolor=C.BG, border_radius=10,
        )

        # ── Notification Profile Presets ──
        _PROFILES = {
            "quiet": {"icon": ft.Icons.NOTIFICATIONS_OFF_OUTLINED, "label": "Yên tĩnh", "desc": "Chỉ deadline gấp", "milestones": [24, 1], "dnd": True, "dnd_start": 22, "dnd_end": 8, "min_before": 0},
            "balanced": {"icon": ft.Icons.NOTIFICATIONS_OUTLINED, "label": "Cân bằng", "desc": "Mặc định", "milestones": [72, 24, 3], "dnd": True, "dnd_start": 22, "dnd_end": 7, "min_before": 30},
            "exam_week": {"icon": ft.Icons.LOCAL_FIRE_DEPARTMENT_OUTLINED, "label": "Tuần thi", "desc": "Không bỏ lỡ gì", "milestones": [72, 24, 6, 1], "dnd": False, "dnd_start": 22, "dnd_end": 7, "min_before": 15},
        }
        self._current_profile = getattr(settings, 'NOTIFICATION_PROFILE', 'balanced')
        self._profile_cards = {}
        self._profile_summary = ft.Text("", size=12, color=C.TEXT_SECONDARY, italic=True)

        def _on_profile_select(profile_key):
            def handler(e):
                self._current_profile = profile_key
                prof = _PROFILES[profile_key]
                # Auto-apply profile settings
                self._sw_dnd_enable.value = prof["dnd"]
                self._dnd_start_field.value = str(prof["dnd_start"])
                self._dnd_end_field.value = str(prof["dnd_end"])
                self._notify_min_field.value = str(prof["min_before"])
                # Update milestone chips
                for h, chip in self._milestone_chips.items():
                    chip.selected = h in prof["milestones"]
                self._milestones_field.value = ", ".join(str(h) for h in sorted(prof["milestones"], reverse=True))
                # Update card styles
                for k, card in self._profile_cards.items():
                    is_sel = (k == profile_key)
                    card.border = ft.Border.all(2, C.ACCENT) if is_sel else ft.Border.all(1, C.BORDER)
                    card.bgcolor = C.ACCENT + "15" if is_sel else C.SURFACE
                self._update_profile_summary()
                self._update_dnd_summary()
                self.update()
            return handler

        for pkey, pval in _PROFILES.items():
            is_sel = (pkey == self._current_profile)
            card = ft.Container(
                content=ft.Column([
                    ft.Icon(pval["icon"], size=24, color=C.ACCENT if is_sel else C.TEXT_SECONDARY),
                    ft.Text(pval["label"], size=13, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY, text_align=ft.TextAlign.CENTER),
                    ft.Text(pval["desc"], size=11, color=C.TEXT_SECONDARY, text_align=ft.TextAlign.CENTER),
                ], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                width=110, padding=12, border_radius=12,
                bgcolor=C.ACCENT + "15" if is_sel else C.SURFACE,
                border=ft.Border.all(2, C.ACCENT) if is_sel else ft.Border.all(1, C.BORDER),
                on_click=_on_profile_select(pkey),
                ink=True,
            )
            self._profile_cards[pkey] = card

        self._profile_row = ft.Row(
            controls=list(self._profile_cards.values()),
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=10,
        )

        # ── DND (Không làm phiền) — Visual Time Cards ──
        self._sw_dnd_enable = ft.Switch(
            value=getattr(settings, 'NOTIFY_DND_ENABLE', False), active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Không làm phiền",
            on_change=lambda e: self._update_dnd_summary()
        )
        self._dnd_start_field = ft.TextField(
            value=str(getattr(settings, 'NOTIFY_DND_START', 22)),
            label="Từ (giờ)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
            width=150, text_align=ft.TextAlign.CENTER,
            prefix_icon=ft.Icons.DARK_MODE_OUTLINED,
            on_change=lambda e: self._update_dnd_summary()
        )
        self._dnd_end_field = ft.TextField(
            value=str(getattr(settings, 'NOTIFY_DND_END', 7)),
            label="Đến (giờ)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
            width=150, text_align=ft.TextAlign.CENTER,
            prefix_icon=ft.Icons.LIGHT_MODE_OUTLINED,
            on_change=lambda e: self._update_dnd_summary()
        )
        self._dnd_summary = ft.Text("", size=12, color=C.TEXT_SECONDARY, italic=True)
        self._dnd_time_row = ft.Row(
            controls=[
                self._dnd_start_field,
                ft.Text("—", size=16, color=C.TEXT_SECONDARY, weight=ft.FontWeight.BOLD),
                self._dnd_end_field,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=8,
        )

        # ── Bỏ qua bài đã nộp ──
        self._sw_ignore_sub = ft.Switch(
            value=getattr(settings, 'NOTIFY_IGNORE_SUBMITTED', True), active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bỏ qua bài đã nộp"
        )

        # ── NOTIFY_TYPES: checkboxes cho từng loại hoạt động ──
        _current_types = getattr(settings, 'NOTIFY_TYPES', ["quiz", "assignment", "attendance"])
        self._notify_type_checks = {}
        _type_options = [
            ("quiz",       "Trắc nghiệm"),
            ("assignment", "Bài tập"),
            ("attendance", "Điểm danh"),
            ("forum",      "Thảo luận"),
            ("resource",   "Tài liệu"),
            ("choice",     "Khảo sát"),
        ]
        for key, label in _type_options:
            self._notify_type_checks[key] = ft.Checkbox(
                label=label,
                value=(key in _current_types),
                fill_color={ft.ControlState.SELECTED: C.ACCENT},
                check_color=C.BG,
                label_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            )
        self._notify_types_row = ft.Row(
            controls=list(self._notify_type_checks.values()),
            wrap=True,
            spacing=4,
            run_spacing=0,
        )

        # ── Milestone Chips (thay thế TextField raw) ──
        _current_milestones = getattr(settings, 'NOTIFY_MILESTONES', [72, 24, 3])
        self._milestone_chips = {}
        _milestone_options = [
            (168, "1 tuần"),
            (72, "3 ngày"),
            (24, "1 ngày"),
            (6, "6 giờ"),
            (3, "3 giờ"),
            (1, "1 giờ"),
        ]

        def _on_milestone_toggle(hours):
            def handler(e):
                chip = self._milestone_chips[hours]
                chip.selected = not chip.selected
                # Sync to hidden field
                active = sorted([h for h, c in self._milestone_chips.items() if c.selected], reverse=True)
                self._milestones_field.value = ", ".join(str(h) for h in active)
                self._milestone_summary.value = f"Bạn sẽ nhận {len(active)} lần nhắc cho mỗi deadline" if active else "Không có mốc nhắc nhở nào"
                self.update()
            return handler

        for hours, label in _milestone_options:
            self._milestone_chips[hours] = ft.Chip(
                label=ft.Text(label, size=12),
                selected=(hours in _current_milestones),
                show_checkmark=True,
                selected_color=C.ACCENT,
                bgcolor=C.SURFACE,
                on_select=_on_milestone_toggle(hours),
            )
        self._milestone_chips_row = ft.Row(
            controls=list(self._milestone_chips.values()),
            wrap=True,
            spacing=6,
            run_spacing=4,
        )
        _active_count = sum(1 for h in _current_milestones if h in self._milestone_chips)
        self._milestone_summary = ft.Text(
            f"Bạn sẽ nhận {_active_count} lần nhắc cho mỗi deadline" if _active_count else "Không có mốc nhắc nhở nào",
            size=12, color=C.TEXT_SECONDARY, italic=True
        )
        # Hidden field to store raw milestone values (for save compatibility)
        self._milestones_field = ft.TextField(
            value=", ".join(map(str, _current_milestones)),
            visible=False,
        )
        
        self._muted_courses_list = ft.Column(spacing=2)
        self._muted_courses_drp = ft.ExpansionTile(
            title=ft.Text("Nhấn để mở danh sách chọn môn bỏ qua", size=13, color=C.TEXT_SECONDARY),
            controls=[
                ft.Container(
                    content=self._muted_courses_list,
                    padding=10,
                    bgcolor=C.BG,
                    border_radius=10,
                    border=ft.Border.all(1, C.BORDER)
                )
            ],
            visible=False,
            collapsed_text_color=C.TEXT_PRIMARY,
            text_color=C.ACCENT
        )

        def _update_drp_options():
            if not getattr(self, "_known_courses", None): return
            current = [x.strip() for x in self._muted_courses_field.value.split(",") if x.strip()]
            
            def make_toggle(course):
                def _on_check(e):
                    curr = [x.strip() for x in self._muted_courses_field.value.split(",") if x.strip()]
                    if e.control.value and course not in curr:
                        curr.append(course)
                    elif not e.control.value and course in curr:
                        curr.remove(course)
                    self._muted_courses_field.value = ", ".join(curr)
                    self._muted_courses_field.update()
                return ft.Checkbox(label=course, value=(course in current), on_change=_on_check, fill_color=C.ACCENT)
            
            self._muted_courses_list.controls = [make_toggle(c) for c in sorted(list(self._known_courses))]
            if hasattr(self._muted_courses_list, "page") and self._muted_courses_list.page:
                self._muted_courses_list.update()

        self._muted_courses_field = ft.TextField(
            value=", ".join(getattr(settings, 'NOTIFY_MUTED_COURSES', [])),
            label="Môn học tắt thông báo (cách nhau dấu phẩy)",
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
            read_only=True,
            text_size=13,
            multiline=True,
            max_lines=3,
            visible=False # Ẩn textfield đi cho đẹp, chỉ dựa vào dropdown
        )

        self._save_status = ft.Text("", size=12, color=C.SAFE)

        self._unsaved_dot = ft.Container(
            width=8, height=8, border_radius=4,
            bgcolor=C.CRITICAL, visible=False,
        )
        # Store refs for live theme refresh
        self._back_icon = ft.Icon(ft.Icons.ARROW_BACK_ROUNDED, size=16, color=C.TEXT_SECONDARY)
        self._back_text = ft.Text("Quay lại", size=14, color=C.TEXT_SECONDARY)
        self._back_btn = ft.TextButton(
            content=ft.Row(controls=[
                self._back_icon,
                self._back_text,
                self._unsaved_dot,
            ], spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            on_click=self._handle_back,
            style=ft.ButtonStyle(
                color=C.TEXT_SECONDARY,
                overlay_color=ft.Colors.with_opacity(0.1, C.TEXT_SECONDARY),
                padding=ft.Padding.symmetric(horizontal=8, vertical=10),
            )
        )

        self._save_btn = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SAVE_ROUNDED, size=16, color=ft.Colors.WHITE),
                    ft.Text("Lưu cài đặt", size=14, color=ft.Colors.WHITE,
                            weight=ft.FontWeight.BOLD),
                ],
                spacing=8, alignment=ft.MainAxisAlignment.CENTER,
            ),
            bgcolor=C.ACCENT,
            padding=ft.Padding.symmetric(vertical=14),
            border_radius=10,
            on_click=self._save,
            ink=True,
            alignment=ft.Alignment(0, 0),
        )

        # Track all hint containers for live theme refresh
        self._hint_containers = []

        def _hint(text):
            txt = ft.Text(text, size=11, color=C.TEXT_SECONDARY)
            c = ft.Container(content=txt, padding=ft.Padding.only(left=4))
            self._hint_containers.append((c, txt))
            return c

        self._tiles = []
        self._section_containers = []  # Track for live theme refresh

        def _setting_group(title, subtitle, controls, default_open=False, icon=None):
            leading_icon = ft.Icon(icon, size=20, color=C.ACCENT) if icon else None
            tile = ft.ExpansionTile(
                title=ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                subtitle=ft.Text(subtitle, size=12, color=C.TEXT_SECONDARY) if subtitle else None,
                leading=leading_icon,
                affinity=ft.Theme.color_scheme,
                controls=[
                    ft.Container(content=ft.Column(controls, horizontal_alignment=ft.CrossAxisAlignment.STRETCH), padding=10)
                ],
                collapsed_text_color=C.TEXT_PRIMARY,
                text_color=C.ACCENT,
                shape=ft.RoundedRectangleBorder(radius=10),
                collapsed_shape=ft.RoundedRectangleBorder(radius=10),
                expanded=default_open,
            )
            self._tiles.append(tile)
            container = ft.Container(
                content=tile,
                bgcolor=C.SURFACE,
                border_radius=10,
                border=ft.Border.all(1, C.BORDER),
                padding=0,
                margin=ft.Margin.only(bottom=3),
                clip_behavior=ft.ClipBehavior.HARD_EDGE
            )
            self._section_containers.append(container)
            return container

        # --- Scrollable content (settings groups) ---
        self._title_text = ft.Text("Cài đặt", size=18, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY)
        self._version_text = ft.Text(f"UTHelper v{__import__('main').__version__}", size=11, color=C.TEXT_SECONDARY)
        self._header_divider = ft.Divider(height=16, color=C.BORDER)
        _scroll_content = ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                # Title row
                ft.Row(controls=[
                    ft.Column(controls=[
                        self._title_text,
                        self._version_text,
                    ], spacing=2),
                ]),
                self._header_divider,

                _setting_group(
                    "Tài khoản UTH",
                    "Thông tin đăng nhập hệ thống elearning",
                    [self._username_field, self._password_field, self._test_loading_bar, self._test_login_status, self._test_login_btn],
                    icon=ft.Icons.PERSON_OUTLINE_ROUNDED,
                ),
                _setting_group(
                    "Hiển thị",
                    "Cách hiển thị trên màn hình",
                    [self._sw_submitted, self._sw_graded] + (
                        [self._sw_always_on_top] if not _pu.IS_MOBILE else []
                    ),
                    icon=ft.Icons.VISIBILITY_OUTLINED,
                ),

                # Desktop-only system settings (Windows: autostart, tray, etc.)
                *([_setting_group(
                    "Hệ thống",
                    "Khởi động và tự động cập nhật",
                    [
                        self._sw_start_with_windows, self._sw_start_minimized, self._sw_minimize_to_tray,
                        ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                        self._interval_field,
                          _hint("Đặt 0 để tắt tự động cập nhật. Mặc định: 60 phút."),
                          self._dd_poll_interval,
                          _hint("Tần suất kiểm tra tự động dữ liệu mới."),
                          self._fetch_months_field,
                          _hint("Số tháng cần lấy sự kiện (1-3). (Mặc định 1)")
                    ],
                    icon=ft.Icons.SETTINGS_OUTLINED,
                )] if not _pu.IS_MOBILE else [_setting_group(
                    "Cập nhật",
                    "Tần suất kiểm tra",
                    [
                        self._interval_field,
                          _hint("Đặt 0 để tắt tự động cập nhật. Mặc định: 60 phút."),
                          self._dd_poll_interval,
                          _hint("Tần suất kiểm tra tự động dữ liệu mới."),
                          self._fetch_months_field,
                          _hint("Số tháng cần lấy sự kiện (1-3). (Mặc định 1)"),
                          ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                          self._sw_bg_check,
                          self._bg_interval_field,
                          _hint("Kiểm tra deadline nền qua AlarmManager (tối thiểu 5 phút). Dưới 15 phút có thể bị delay bởi chế độ tiết kiệm pin."),
                    ],
                    icon=ft.Icons.SETTINGS_OUTLINED,
                )]),

                _setting_group(
                    "Cảnh báo",
                    "Ngưỡng thời gian màu sắc",
                    [
                        self._make_themed_label("Mức độ"),
                        self._critical_hours_field,
                        self._warning_hours_field,
                        ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                        self._make_themed_label("Trạng thái"),
                        self._opening_soon_hours_field,
                        _hint("Hoạt động sẽ được đánh dấu 'Sắp mở' khi thời gian mở nhỏ hơn mức này.")
                    ],
                    icon=ft.Icons.NOTIFICATIONS_ACTIVE_OUTLINED,
                ),

                _setting_group(
                    "Giao diện",
                    "Theme và tùy chỉnh màu sắc",
                    [
                        self._make_themed_label("Chọn Theme"),
                        self._theme_cards_row,
                        ft.Divider(height=10, color=C.BORDER),
                        self._make_themed_label("Tùy chỉnh màu"),
                        _hint("Thay đổi màu riêng sẽ ghi đè preset theme."),
                        row_cri, row_warn, row_safe,
                        ft.Divider(height=10, color=C.BORDER),
                        row_quiz, row_ass, row_att, row_open, row_other,
                        ft.Divider(height=10, color=C.BORDER),
                        self.btn_reset,
                    ],
                    icon=ft.Icons.PALETTE_OUTLINED,
                ),
                _setting_group(
                    "Thông báo",
                    "Chế độ và thời gian nhắc nhở",
                    [
                        self._make_themed_label("Chế độ thông báo"),
                        self._profile_row,
                        self._profile_summary,
                        ft.Divider(height=10, color=C.BORDER),
                        self._sw_dnd_enable,
                        self._dnd_time_row,
                        self._dnd_summary,
                        ft.Divider(height=10, color=C.BORDER),
                        self._sw_ignore_sub,
                    ],
                    icon=ft.Icons.NOTIFICATIONS_OUTLINED,
                    default_open=True,
                ),
                _setting_group(
                    "Tùy chỉnh nâng cao",
                    "Mốc nhắc, loại bài, tắt theo môn",
                    [
                        self._make_themed_label("Nhắc trước deadline"),
                        self._milestone_chips_row,
                        self._milestone_summary,
                        self._milestones_field,
                        ft.Divider(height=10, color=C.BORDER),
                        self._make_themed_label("Nhắc phút cuối"),
                        self._notify_min_field,
                        _hint("Gửi thêm 1 lần nhắc khi chỉ còn X phút. Đặt 0 để tắt."),
                        ft.Divider(height=10, color=C.BORDER),
                        self._make_themed_label("Loại hoạt động"),
                        self._notify_types_row,
                        ft.Divider(height=10, color=C.BORDER),
                        self._make_themed_label("Tắt thông báo theo môn"),
                        self._muted_courses_drp,
                        self._muted_courses_field,
                    ],
                    icon=ft.Icons.TUNE_OUTLINED,
                ),

                _setting_group(
                    "Tích hợp",
                    "Nhắn tin qua Bot & Email",
                    [
                        self._sw_email,
                        self._gmail_addr_field,
                        self._gmail_pw_field,
                        ft.Divider(height=10, color=C.BORDER),
                        self._sw_discord,
                        self._discord_wh_field,
                        ft.Divider(height=10, color=C.BORDER),
                        self._sw_telegram,
                        self._tel_token_field,
                        self._tel_chat_field,
                    ],
                    icon=ft.Icons.INTEGRATION_INSTRUCTIONS_OUTLINED,
                ),

                _setting_group(
                    "Nâng cao",
                    "Luồng tải, Log hệ thống",
                    [
                        self._workers_field,
                        _hint("Tăng để tải chi tiết nhanh hơn. Nhỏ đi nếu bị block."),
                        self._sw_debug,
                        self._test_panel,
                    ],
                    icon=ft.Icons.BUILD_OUTLINED,
                ),

                ft.Container(height=16),
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
        )

        # --- Sticky footer (save button) ---
        self._sticky_footer = ft.Container(
            content=ft.Column(controls=[
                self._save_btn,
                self._save_status,
            ], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=C.BG,
            padding=ft.Padding.only(left=20, right=20, top=10, bottom=12),
            border=ft.Border.only(top=ft.BorderSide(1.5, C.TEXT_SECONDARY + "30")),
        )

        self.content = ft.Column(
            controls=[
                # Back button header
                ft.Container(
                    content=ft.Row(controls=[self._back_btn],
                                   alignment=ft.MainAxisAlignment.START),
                    padding=ft.Padding.only(left=8, top=25, bottom=4),
                ),
                # Scrollable content
                ft.Container(
                    content=_scroll_content,
                    padding=ft.Padding.symmetric(horizontal=20),
                    expand=True,
                ),
                # Fixed save button at bottom
                self._sticky_footer,
            ],
            spacing=0,
            expand=True,
        )


    # ── Helper: themed label with tracked reference ─────────────────────

    def _make_themed_label(self, text: str) -> "ft.Text":
        """Create a themed label text and track it for live refresh."""
        t = ft.Text(text, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY, size=13)
        self._themed_texts.append(t)
        return t

    # ── Notification Profile & DND Summary Helpers ────────────────────

    def _update_profile_summary(self):
        """Update the profile summary text based on current profile selection."""
        _labels = {"quiet": "Yên tĩnh", "balanced": "Cân bằng", "exam_week": "Tuần thi"}
        _descs = {
            "quiet": "Chỉ nhắc deadline gấp (1 ngày và 1 giờ trước)",
            "balanced": "Nhắc 3 ngày, 1 ngày và 3 giờ trước deadline",
            "exam_week": "Nhắc liên tục, không bỏ lỡ bất kỳ deadline nào",
        }
        profile = getattr(self, '_current_profile', 'balanced')
        self._profile_summary.value = _descs.get(profile, "")

    def _update_dnd_summary(self):
        """Update DND summary text based on current toggle and time values."""
        try:
            enabled = self._sw_dnd_enable.value
            start = int(self._dnd_start_field.value or "22")
            end = int(self._dnd_end_field.value or "7")
            if not enabled:
                self._dnd_summary.value = "Thông báo hoạt động 24/7"
            else:
                # Calculate quiet hours
                if start > end:
                    hours = (24 - start) + end
                elif start < end:
                    hours = end - start
                else:
                    hours = 24
                self._dnd_summary.value = f"Yên tĩnh {hours} tiếng mỗi đêm (từ {start}:00 đến {end}:00)"
        except (ValueError, AttributeError):
            self._dnd_summary.value = ""

    # ── Theme Selector Builder ────────────────────────────────────────

    def _build_theme_selector(self):
        """Build a scrollable row of theme preview cards."""
        cards = []
        for key in THEME_ORDER:
            preset = THEME_PRESETS[key]
            is_sel = (key == self._selected_theme)
            card = self._make_theme_card(key, preset, is_sel)
            cards.append(card)

        return ft.Container(
            content=ft.Row(
                controls=cards,
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            ),
            padding=ft.Padding.only(top=4, bottom=4),
        )

    def _make_theme_card(self, key: str, preset: dict, selected: bool):
        """Create a single theme preview card."""
        border_col = preset["accent"] if selected else (preset["border"] + "80")
        border_w = 2.5 if selected else 1

        # Color strip: 5 small color dots showing the palette
        dots = ft.Row(
            controls=[
                ft.Container(width=10, height=10, border_radius=5, bgcolor=preset["bg"]),
                ft.Container(width=10, height=10, border_radius=5, bgcolor=preset["surface"]),
                ft.Container(width=10, height=10, border_radius=5, bgcolor=preset["accent"]),
                ft.Container(width=10, height=10, border_radius=5, bgcolor=preset["critical"]),
                ft.Container(width=10, height=10, border_radius=5, bgcolor=preset["safe"]),
            ],
            spacing=4,
            alignment=ft.MainAxisAlignment.CENTER,
        )

        # Selected indicator
        check_icon = ft.Icon(
            ft.Icons.CHECK_CIRCLE_ROUNDED,
            size=16,
            color=preset["accent"],
            visible=selected,
        )

        label = ft.Text(
            preset["label"],
            size=11,
            weight=ft.FontWeight.BOLD if selected else ft.FontWeight.NORMAL,
            color=preset["text_primary"],
            text_align=ft.TextAlign.CENTER,
        )
        sublabel = ft.Text(
            preset["description"],
            size=9,
            color=preset["text_secondary"],
            text_align=ft.TextAlign.CENTER,
        )

        card_content = ft.Column(
            controls=[
                ft.Row([check_icon], alignment=ft.MainAxisAlignment.END),
                # Gradient preview strip
                ft.Container(
                    width=90, height=28,
                    border_radius=6,
                    gradient=ft.LinearGradient(
                        begin=ft.Alignment(-1, 0),
                        end=ft.Alignment(1, 0),
                        colors=[preset["bg"], preset["surface"], preset["accent"]],
                    ),
                ),
                dots,
                label,
                sublabel,
            ],
            spacing=3,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

        def _select(e, theme_key=key):
            self._on_theme_select(theme_key)

        return ft.Container(
            content=card_content,
            width=115,
            padding=ft.Padding.all(8),
            border_radius=12,
            bgcolor=preset["surface"],
            border=ft.Border.all(border_w, border_col),
            on_click=_select,
            ink=True,
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        )

    def _on_theme_select(self, theme_key: str):
        """Handle theme card selection — applies IMMEDIATELY for live preview."""
        import logging
        log = logging.getLogger("UTHelper.settings")
        log.info(f"[THEME] _on_theme_select called: {theme_key}")
        try:
            self._selected_theme = theme_key
            self._apply_theme_colors(theme_key)
            self._rebuild_theme_cards()
            # Apply theme globally RIGHT NOW for instant preview
            apply_theme(theme_key)
            log.info(f"[THEME] After apply_theme: TEXT_PRIMARY={C.TEXT_PRIMARY}, TEXT_SECONDARY={C.TEXT_SECONDARY}")
            # Sync Flet's page.theme ColorScheme
            from gui.core.theme import set_page_theme
            set_page_theme(self._page)
            # Update settings view own colors
            self.bgcolor = C.BG
            # Refresh all section containers with new theme colors
            self._refresh_section_colors()
            log.info(f"[THEME] After _refresh_section_colors: _back_text.color={getattr(self._back_text, 'color', '?')}, _title_text.color={getattr(self._title_text, 'color', '?')}")
            log.info(f"[THEME] themed_texts={len(self._themed_texts)}, hint_containers={len(self._hint_containers)}, tiles={len(self._tiles)}")
            # Notify app_controller to refresh entire UI (dashboard, header, footer, cards)
            if self._on_theme_preview:
                self._on_theme_preview()
            # Show unsaved indicator
            if hasattr(self, '_unsaved_dot'):
                self._unsaved_dot.visible = True
            # Force full page repaint
            self._page.update()
            log.info("[THEME] page.update() completed successfully")
        except Exception as exc:
            log.error(f"[THEME] EXCEPTION in _on_theme_select: {exc}", exc_info=True)

    def _refresh_section_colors(self):
        """Update ALL settings controls with current C values for live theme preview.

        Covers: section containers, expansion tiles, text fields, switches,
        save button, sticky footer, back button, test login, etc.
        """
        # ── Section containers (bg + border) ──
        for container in getattr(self, '_section_containers', []):
            container.bgcolor = C.SURFACE
            container.border = ft.Border.all(1, C.BORDER)
            try:
                container.update()
            except Exception:
                pass

        # ── Expansion tiles (title text + icon colors) ──
        for tile in getattr(self, '_tiles', []):
            tile.collapsed_text_color = C.TEXT_PRIMARY
            tile.text_color = C.ACCENT
            tile.icon_color = C.ACCENT
            tile.collapsed_icon_color = C.TEXT_SECONDARY
            if tile.leading:
                tile.leading.color = C.ACCENT
            try:
                tile.update()
            except Exception:
                pass

        # ── ALL text fields — update border, focus, text, bg colors ──
        _all_fields = [
            '_username_field', '_password_field',
            '_interval_field', '_fetch_months_field',
            '_critical_hours_field', '_warning_hours_field',
            '_opening_soon_hours_field', '_notify_min_field', '_workers_field',
            '_gmail_addr_field', '_gmail_pw_field',
            '_discord_wh_field', '_tel_token_field', '_tel_chat_field',
            '_dnd_start_field', '_dnd_end_field',
            '_milestones_field', '_muted_courses_field',
            '_c_tb_critical', '_c_tb_warning', '_c_tb_safe',
            '_c_tb_quiz', '_c_tb_ass', '_c_tb_att', '_c_tb_open', '_c_tb_other',
        ]
        for fname in _all_fields:
            field = getattr(self, fname, None)
            if field and isinstance(field, ft.TextField):
                field.border_color = C.BORDER
                field.focused_border_color = C.ACCENT
                field.color = C.TEXT_PRIMARY
                field.bgcolor = C.BG
                field.label_style = ft.TextStyle(size=13, color=C.TEXT_SECONDARY)

        # ── Dropdown ──
        if hasattr(self, '_mock_type_drp'):
            self._mock_type_drp.border_color = C.BORDER
            self._mock_type_drp.focused_border_color = C.ACCENT
            self._mock_type_drp.color = C.TEXT_PRIMARY
            self._mock_type_drp.bgcolor = C.BG

        # ── ALL switches — update active_color + label text style ──
        _all_switches = [
            '_sw_always_on_top', '_sw_submitted', '_sw_graded',
            '_sw_start_with_windows', '_sw_start_minimized', '_sw_minimize_to_tray',
            '_sw_email', '_sw_discord', '_sw_telegram',
            '_sw_dnd_enable', '_sw_ignore_sub', '_sw_debug',
        ]
        for sname in _all_switches:
            sw = getattr(self, sname, None)
            if sw and isinstance(sw, ft.Switch):
                # _sw_debug uses C.CRITICAL as active_color
                if sname != '_sw_debug':
                    sw.active_color = C.ACCENT
                sw.label_text_style = ft.TextStyle(color=C.TEXT_PRIMARY, size=13)

        # ── Save button ──
        if hasattr(self, '_save_btn'):
            self._save_btn.bgcolor = C.ACCENT
            try:
                self._save_btn.update()
            except Exception:
                pass

        # ── Sticky footer ──
        if hasattr(self, '_sticky_footer'):
            self._sticky_footer.bgcolor = C.BG
            self._sticky_footer.border = ft.Border.only(
                top=ft.BorderSide(1.5, C.TEXT_SECONDARY + "30")
            )
            try:
                self._sticky_footer.update()
            except Exception:
                pass

        # ── Test login button ──
        if hasattr(self, '_test_login_btn'):
            self._test_login_btn.style = ft.ButtonStyle(
                color=ft.Colors.WHITE,
                bgcolor=C.ACCENT,
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=12,
                animation_duration=300,
            )

        # ── Loading bar ──
        if hasattr(self, '_test_loading_bar'):
            self._test_loading_bar.color = C.ACCENT
            self._test_loading_bar.bgcolor = C.SURFACE

        # ── Reset button ──
        if hasattr(self, 'btn_reset'):
            self.btn_reset.style = ft.ButtonStyle(color=C.TEXT_SECONDARY)

        # ── Back button (icon + text + style) ──
        if hasattr(self, '_back_icon'):
            self._back_icon.color = C.TEXT_SECONDARY
        if hasattr(self, '_back_text'):
            self._back_text.color = C.TEXT_SECONDARY
        if hasattr(self, '_back_btn'):
            self._back_btn.style = ft.ButtonStyle(
                color=C.TEXT_SECONDARY,
                overlay_color=ft.Colors.with_opacity(0.1, C.TEXT_SECONDARY),
                padding=ft.Padding.symmetric(horizontal=8, vertical=10),
            )

        # ── Header texts ──
        if hasattr(self, '_title_text'):
            self._title_text.color = C.TEXT_PRIMARY
        if hasattr(self, '_version_text'):
            self._version_text.color = C.TEXT_SECONDARY
        if hasattr(self, '_header_divider'):
            self._header_divider.color = C.BORDER

        # ── Unsaved dot ──
        if hasattr(self, '_unsaved_dot'):
            self._unsaved_dot.bgcolor = C.CRITICAL

        # ── All themed sub-labels (section titles like "Mức độ", "Chọn Theme") ──
        for txt in getattr(self, '_themed_texts', []):
            txt.color = C.TEXT_PRIMARY

        # ── All hint containers (description texts) ──
        for _container, txt in getattr(self, '_hint_containers', []):
            txt.color = C.TEXT_SECONDARY

        # ── Profile cards (notification presets) ──
        for k, card in getattr(self, '_profile_cards', {}).items():
            is_sel = (k == getattr(self, '_current_profile', 'balanced'))
            card.border = ft.Border.all(2, C.ACCENT) if is_sel else ft.Border.all(1, C.BORDER)
            card.bgcolor = C.ACCENT + "15" if is_sel else C.SURFACE
            col = card.content
            if col and hasattr(col, 'controls') and len(col.controls) >= 3:
                col.controls[0].color = C.ACCENT if is_sel else C.TEXT_SECONDARY  # icon
                col.controls[1].color = C.TEXT_PRIMARY  # label
                col.controls[2].color = C.TEXT_SECONDARY  # desc

        # ── Milestone chips ──
        for h, chip in getattr(self, '_milestone_chips', {}).items():
            chip.selected_color = C.ACCENT
            chip.bgcolor = C.SURFACE

        # ── Summary texts (profile, DND, milestone) ──
        for attr in ('_profile_summary', '_dnd_summary', '_milestone_summary'):
            txt = getattr(self, attr, None)
            if txt:
                txt.color = C.TEXT_SECONDARY

        # ── Expansion tile title + subtitle texts (deep access) ──
        for tile in getattr(self, '_tiles', []):
            if tile.title and hasattr(tile.title, 'color'):
                tile.title.color = C.TEXT_PRIMARY
            if tile.subtitle and hasattr(tile.subtitle, 'color'):
                tile.subtitle.color = C.TEXT_SECONDARY

    def _apply_theme_colors(self, theme_key: str):
        """Fill color text fields from preset values."""
        preset = THEME_PRESETS.get(theme_key, THEME_PRESETS["midnight_blue"])
        self._c_tb_critical.value = preset["critical"]
        self._c_tb_warning.value = preset["warning"]
        self._c_tb_safe.value = preset["safe"]
        self._c_tb_quiz.value = preset["quiz"]
        self._c_tb_ass.value = preset["assignment"]
        self._c_tb_att.value = preset["attendance"]
        self._c_tb_open.value = preset["open"]
        self._c_tb_other.value = preset["other"]

    def _rebuild_theme_cards(self):
        """Rebuild theme cards row to reflect new selection."""
        new_row = self._build_theme_selector()
        self._theme_cards_row.content = new_row.content


    async def _handle_reset_defaults(self, e):
        # Reset theme to default
        self._selected_theme = "midnight_blue"
        self._apply_theme_colors("midnight_blue")
        self._rebuild_theme_cards()
        # Apply theme live preview
        apply_theme("midnight_blue")
        from gui.core.theme import set_page_theme
        set_page_theme(self._page)
        self.bgcolor = C.BG
        self._refresh_section_colors()
        if self._on_theme_preview:
            self._on_theme_preview()
        
        self._critical_hours_field.value = "24"
        self._warning_hours_field.value = "72"
        self._opening_soon_hours_field.value = "72"
        self._interval_field.value = "60"
        self._fetch_months_field.value = "1"
        self._notify_min_field.value = "30"
        
        # Reset notification profile to balanced
        self._current_profile = "balanced"
        for k, card in self._profile_cards.items():
            is_sel = (k == "balanced")
            card.border = ft.Border.all(2, C.ACCENT) if is_sel else ft.Border.all(1, C.BORDER)
            card.bgcolor = C.ACCENT + "15" if is_sel else C.SURFACE
        # Reset milestone chips to defaults [72, 24, 3]
        _default_milestones = [72, 24, 3]
        for h, chip in self._milestone_chips.items():
            chip.selected = h in _default_milestones
        self._milestones_field.value = "72, 24, 3"
        self._milestone_summary.value = "Bạn sẽ nhận 3 lần nhắc cho mỗi deadline"
        # Reset DND
        self._sw_dnd_enable.value = False
        self._dnd_start_field.value = "22"
        self._dnd_end_field.value = "7"
        self._update_profile_summary()
        self._update_dnd_summary()
        
        self.update()

    async def _handle_test_login(self, e):
        user = self._username_field.value.strip()
        pwd = self._password_field.value.strip()
        if not user or not pwd:
            self._test_login_status.value = "Vui lòng nhập đủ MSSV và Mật khẩu!"
            self._test_login_status.color = C.CRITICAL
            self.update()
            return

        self._test_login_btn.disabled = True
        self._username_field.disabled = True
        self._password_field.disabled = True
        self._test_login_btn.text = "Đang kiểm tra..."
        self._test_login_btn.icon = ft.Icons.HOURGLASS_TOP_ROUNDED
        self._test_loading_bar.visible = True
        self._test_login_status.value = ""
        self.update()

        try:
            success = await asyncio.to_thread(self._orchestrator.client.login, username=user, password=pwd, force=True)
            if success:
                self._test_login_status.value = "Kết nối thành công!"
                self._test_login_status.color = C.SAFE
            else:
                self._test_login_status.value = "Đăng nhập thất bại. Kiểm tra lại thông tin!"
                self._test_login_status.color = C.CRITICAL
        except Exception as ex:
            self._test_login_status.value = f"Lỗi: {str(ex)}"
            self._test_login_status.color = C.CRITICAL
        finally:
            self._test_login_btn.disabled = False
            self._username_field.disabled = False
            self._password_field.disabled = False
            self._test_login_btn.text = "Kiểm tra kết nối"
            self._test_login_btn.icon = ft.Icons.WIFI_FIND_ROUNDED
            self._test_loading_bar.visible = False
            self.update()



    def _safe_update(self, *controls):
        """Update controls only if they are already attached to the page."""
        for c in controls:
            try:
                c.update()
            except (RuntimeError, Exception):
                pass  # Control not on page yet — will render on next page.update()

    def _toggle_integration_ui(self):
        self._gmail_addr_field.visible = self._sw_email.value
        self._gmail_pw_field.visible = self._sw_email.value
        self._discord_wh_field.visible = self._sw_discord.value
        self._safe_update(self._gmail_addr_field, self._gmail_pw_field, self._discord_wh_field)

    def _toggle_telegram_ui(self):
        v = self._sw_telegram.value
        self._tel_token_field.visible = v
        self._tel_chat_field.visible = v
        self._safe_update(self._tel_token_field, self._tel_chat_field)

    def _toggle_bg_check_ui(self):
        v = self._sw_bg_check.value
        self._bg_interval_field.visible = v
        self._safe_update(self._bg_interval_field)

    def _toggle_debug_ui(self):
        self._test_panel.visible = self._sw_debug.value
        self._safe_update(self._test_panel)
        
    def _do_test_tray(self):
        t = getattr(self, '_mock_type_drp', ft.Dropdown(value='critical')).value
        if self._on_test_tray: self._on_test_tray(t)

    def _do_test_mobile(self):
        t = getattr(self, '_mock_type_drp', ft.Dropdown(value='critical')).value
        if hasattr(self, '_on_test_mobile') and self._on_test_mobile: self._on_test_mobile(t)

    def _do_test_tele(self):
        t = getattr(self, '_mock_type_drp', ft.Dropdown(value='critical')).value
        if hasattr(self, '_on_test_tele') and self._on_test_tele: self._on_test_tele(t)

    def _do_test_discord(self):
        t = getattr(self, '_mock_type_drp', ft.Dropdown(value='critical')).value
        if hasattr(self, '_on_test_discord') and self._on_test_discord: self._on_test_discord(t)

    def _do_test_mail(self):
        t = getattr(self, '_mock_type_drp', ft.Dropdown(value='critical')).value
        if hasattr(self, '_on_test_mail') and self._on_test_mail: self._on_test_mail(t)

    # ── System diagnostics ──
    def _do_show_device_info(self):
        """Show device/platform info for debugging."""
        import sys
        import platform as pf
        from platform_utils import IS_ANDROID, IS_IOS, IS_MOBILE, IS_WINDOWS
        try:
            import flet as ft_info
            flet_ver = getattr(ft_info, '__version__', 'unknown')
        except Exception:
            flet_ver = 'unknown'

        from config import settings as cfg
        lines = [
            f"Python: {sys.version.split()[0]}",
            f"Platform: {pf.system()} {pf.release()} ({pf.machine()})",
            f"Flet: {flet_ver}",
            f"Flags: Android={IS_ANDROID}, iOS={IS_IOS}, Mobile={IS_MOBILE}, Windows={IS_WINDOWS}",
            f"App: v{getattr(cfg, 'APP_VERSION', '?')}",
            f"Notifiers: {len(getattr(self._orchestrator, 'notifier', object()).notifiers) if hasattr(self._orchestrator, 'notifier') else '?'} registered",
            f"BG Check: {'ON' if cfg.BACKGROUND_CHECK_ANDROID else 'OFF'} (every {cfg.BACKGROUND_CHECK_INTERVAL}m)",
            f"DND: {'ON' if cfg.NOTIFY_DND_ENABLE else 'OFF'} ({cfg.NOTIFY_DND_START}h-{cfg.NOTIFY_DND_END}h)",
        ]
        self._debug_info_text.value = "\n".join(lines)
        self._debug_info_text.update()

    def _do_test_moodle_connection(self):
        """Quick Moodle API connectivity test."""
        import threading
        self._debug_info_text.value = "Đang kiểm tra kết nối Moodle..."
        self._debug_info_text.color = C.TEXT_SECONDARY
        self._debug_info_text.update()

        def _worker():
            try:
                ok = self._orchestrator.client.login(
                    username=settings.UTH_USERNAME,
                    password=settings.UTH_PASSWORD,
                    force=True,
                )
                if ok:
                    token = self._orchestrator.client.token or "?"
                    masked = token[:6] + "..." + token[-4:] if len(token) > 10 else token
                    result = f"Kết nối Moodle OK\nToken: {masked}\nUser ID: {self._orchestrator.client.user_id}"
                    color = C.SAFE
                else:
                    result = "Đăng nhập Moodle thất bại!"
                    color = C.CRITICAL
            except Exception as ex:
                result = f"Lỗi kết nối: {ex}"
                color = C.CRITICAL

            self._debug_info_text.value = result
            self._debug_info_text.color = color
            try:
                self._debug_info_text.update()
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    # ── Cache management ──
    def _do_clear_notif_cache(self):
        """Clear notification milestone cache (forces re-notification)."""
        try:
            from config import _USER_DATA_DIR
            import os
            cache_path = _USER_DATA_DIR / "notifications_cache.json"
            if cache_path.exists():
                os.remove(str(cache_path))
            self._debug_info_text.value = "Cache thông báo đã xóa. Tất cả milestones sẽ được gửi lại."
            self._debug_info_text.color = C.SAFE
        except Exception as ex:
            self._debug_info_text.value = f"Lỗi xóa cache: {ex}"
            self._debug_info_text.color = C.CRITICAL
        self._debug_info_text.update()

    def _do_clear_notif_history(self):
        """Clear notification history log."""
        try:
            from core.notification_history import NotificationHistory
            history = NotificationHistory()
            count = len(history.get_all())
            history.clear()
            self._debug_info_text.value = f"Đã xóa {count} mục lịch sử thông báo."
            self._debug_info_text.color = C.SAFE
        except Exception as ex:
            self._debug_info_text.value = f"Lỗi xóa lịch sử: {ex}"
            self._debug_info_text.color = C.CRITICAL
        self._debug_info_text.update()

    def _do_clear_data_cache(self):
        """Clear offline activities data cache."""
        try:
            from core.data_cache import DataCache
            cache = DataCache()
            cache.clear()
            self._debug_info_text.value = "Cache offline đã xóa. Dữ liệu sẽ được tải lại từ Moodle."
            self._debug_info_text.color = C.SAFE
        except Exception as ex:
            self._debug_info_text.value = f"Lỗi xóa data cache: {ex}"
            self._debug_info_text.color = C.CRITICAL
        self._debug_info_text.update()

    def _do_show_notif_history(self):
        """Show recent notification history entries."""
        try:
            from core.notification_history import NotificationHistory
            history = NotificationHistory()
            entries = history.get_all()
            if not entries:
                self._debug_history_text.value = "Chưa có thông báo nào được gửi."
                self._debug_history_text.color = C.TEXT_SECONDARY
            else:
                lines = []
                for i, e in enumerate(entries[:10]):  # Show last 10
                    sent = e.get("sent_at", "?")[:16].replace("T", " ")
                    title = e.get("title", "?")[:40]
                    channels = ", ".join(e.get("channels", []))
                    lines.append(f"{i+1}. [{sent}] {title}\n   Qua: {channels}")
                self._debug_history_text.value = "\n".join(lines)
                if len(entries) > 10:
                    self._debug_history_text.value += f"\n... và {len(entries) - 10} mục khác"
                self._debug_history_text.color = C.TEXT_PRIMARY
        except Exception as ex:
            self._debug_history_text.value = f"Lỗi đọc lịch sử: {ex}"
            self._debug_history_text.color = C.CRITICAL
        self._debug_history_text.update()

    def _do_show_scheduler_status(self):
        """Show background scheduler status (Android)."""
        try:
            from core.background_scheduler import get_scheduler
            scheduler = get_scheduler()
            lines = [
                f"Available: {'Yes' if scheduler.is_available else 'No'}",
                f"Active: {'Yes' if scheduler._is_active else 'No'}",
                f"Backend: {'flet-android-notifications' if scheduler._android_notif else 'None'}",
                f"Interval: {settings.BACKGROUND_CHECK_INTERVAL} min",
                f"Enabled: {'Yes' if settings.BACKGROUND_CHECK_ANDROID else 'No'}",
            ]
            self._debug_scheduler_status.value = "\n".join(lines)
            self._debug_scheduler_status.color = C.SAFE if scheduler._is_active else C.TEXT_SECONDARY
        except Exception as ex:
            self._debug_scheduler_status.value = f"Lỗi: {ex}"
            self._debug_scheduler_status.color = C.CRITICAL
        self._debug_scheduler_status.update()

    # ── Background scheduler (Android) ──
    def _do_start_foreground(self):
        """Start Android foreground service for persistent background."""
        async def _start():
            try:
                from core.background_scheduler import get_scheduler
                scheduler = get_scheduler()
                if not scheduler.is_available:
                    self._debug_info_text.value = "Background scheduler không khả dụng trên nền tảng này."
                    self._debug_info_text.color = C.WARNING
                else:
                    await scheduler.start_foreground_service()
                    self._debug_info_text.value = "Foreground service đã khởi động. App sẽ chạy ngầm liên tục."
                    self._debug_info_text.color = C.SAFE
            except Exception as ex:
                self._debug_info_text.value = f"Lỗi foreground: {ex}"
                self._debug_info_text.color = C.CRITICAL
            self._debug_info_text.update()
        self._page.run_task(_start)

    def _do_stop_foreground(self):
        """Stop Android foreground service."""
        async def _stop():
            try:
                from core.background_scheduler import get_scheduler
                scheduler = get_scheduler()
                await scheduler.stop_foreground_service()
                self._debug_info_text.value = "Foreground service đã dừng."
                self._debug_info_text.color = C.TEXT_SECONDARY
            except Exception as ex:
                self._debug_info_text.value = f"Lỗi stop foreground: {ex}"
                self._debug_info_text.color = C.CRITICAL
            self._debug_info_text.update()
        self._page.run_task(_stop)

    def _do_test_immediate_notif(self):
        """Send an immediate test notification via background scheduler."""
        async def _send():
            try:
                from core.background_scheduler import get_scheduler
                scheduler = get_scheduler()
                if not scheduler.is_available:
                    self._debug_info_text.value = "Scheduler không khả dụng."
                    self._debug_info_text.color = C.WARNING
                else:
                    await scheduler.send_immediate(
                        title="UTHelper Test",
                        body="Thông báo test từ Background Scheduler"
                    )
                    self._debug_info_text.value = "Đã gửi immediate notification qua scheduler."
                    self._debug_info_text.color = C.SAFE
            except Exception as ex:
                self._debug_info_text.value = f"Lỗi immediate: {ex}"
                self._debug_info_text.color = C.CRITICAL
            self._debug_info_text.update()
        self._page.run_task(_send)

    # ── Mobile-specific test handlers ──
    def _do_check_notif_permission(self):
        """Check notification permission status on mobile."""
        async def _check():
            try:
                import platform_utils as pu
                lines = [f"Platform: {'Android' if pu.IS_ANDROID else 'iOS' if pu.IS_IOS else 'Desktop'}"]

                # Try to get notifier and check permission
                notifier = getattr(self._orchestrator, 'notifier', None)
                if notifier:
                    mgr = notifier
                    mobile_n = None
                    for n in getattr(mgr, 'notifiers', []):
                        if hasattr(n, 'backend_name'):
                            mobile_n = n
                            break
                    if mobile_n:
                        lines.append(f"Backend: {mobile_n.backend_name}")
                        if hasattr(mobile_n, '_android_notif') and mobile_n._android_notif:
                            lines.append("✅ Android notification backend active")
                            if hasattr(mobile_n._android_notif, 'are_notifications_enabled'):
                                enabled = mobile_n._android_notif.are_notifications_enabled()
                                lines.append(f"Notifications enabled: {'✅ YES' if enabled else '❌ NO'}")
                        elif hasattr(mobile_n, '_notifier') and mobile_n._notifier:
                            lines.append("✅ flet_notifications backend active")
                        else:
                            lines.append("⚠️ No notification backend available (log-only mode)")
                    else:
                        lines.append("⚠️ No MobileNotifier found in registered notifiers")
                else:
                    lines.append("❌ NotificationManager not available")

                self._debug_mobile_text.value = "\n".join(lines)
                self._debug_mobile_text.color = C.SAFE if "✅" in lines[-1] else C.WARNING
            except Exception as ex:
                self._debug_mobile_text.value = f"Lỗi kiểm tra quyền: {ex}"
                self._debug_mobile_text.color = C.CRITICAL
            self._debug_mobile_text.update()
        self._page.run_task(_check)

    def _do_mock_mobile_notif(self, urgency="critical"):
        """Send a mock mobile notification with specified urgency."""
        titles = {
            "critical": "⚠️ BÀI TẬP SẮP HẾT HẠN",
            "warning": "📋 Nhắc nhở deadline",
            "safe": "✅ Kiểm tra hoàn tất",
        }
        bodies = {
            "critical": "Lập trình Python — Còn 2 giờ | Test mock notification",
            "warning": "Cơ sở dữ liệu — Còn 48 giờ | Test mock notification",
            "safe": "Tất cả bài tập đều đã nộp đúng hạn | Test mock notification",
        }
        title = titles.get(urgency, titles["critical"])
        body = bodies.get(urgency, bodies["critical"])

        try:
            notifier = getattr(self._orchestrator, 'notifier', None)
            if notifier:
                mobile_n = None
                for n in getattr(notifier, 'notifiers', []):
                    if hasattr(n, 'backend_name'):
                        mobile_n = n
                        break
                if mobile_n:
                    # Create mock assignment data
                    mock = [{"title": title, "course_name": body, "remaining": ""}]
                    result = mobile_n.notify(mock)
                    self._debug_mobile_text.value = f"Mock [{urgency}] → {'✅ Đã gửi' if result else '❌ Thất bại'}\n{title}\n{body}"
                    self._debug_mobile_text.color = C.SAFE if result else C.CRITICAL
                else:
                    self._debug_mobile_text.value = "⚠️ Không tìm thấy MobileNotifier"
                    self._debug_mobile_text.color = C.WARNING
            else:
                self._debug_mobile_text.value = "❌ NotificationManager not available"
                self._debug_mobile_text.color = C.CRITICAL
        except Exception as ex:
            self._debug_mobile_text.value = f"Lỗi gửi mock: {ex}"
            self._debug_mobile_text.color = C.CRITICAL
        self._debug_mobile_text.update()

    def _do_show_mobile_backend(self):
        """Show detailed mobile notification backend info."""
        try:
            import platform_utils as pu
            import sys
            lines = [
                f"Platform: {'Android' if pu.IS_ANDROID else 'iOS' if pu.IS_IOS else 'Other'}",
                f"sys.platform: {sys.platform}",
                f"IS_MOBILE: {pu.IS_MOBILE}",
                f"IS_ANDROID: {pu.IS_ANDROID}",
                f"IS_IOS: {pu.IS_IOS}",
            ]

            # Check available packages
            for pkg in ['flet_android_notifications', 'flet_notifications']:
                try:
                    mod = __import__(pkg)
                    ver = getattr(mod, '__version__', 'installed')
                    lines.append(f"📦 {pkg}: {ver}")
                except ImportError:
                    lines.append(f"❌ {pkg}: not installed")

            # Scheduler info
            try:
                from core.background_scheduler import get_scheduler
                sched = get_scheduler()
                lines.append(f"Scheduler: {'✅ active' if sched.is_available else '❌ unavailable'}")
            except Exception:
                lines.append("Scheduler: not loaded")

            # Notifier info
            notifier = getattr(self._orchestrator, 'notifier', None)
            if notifier:
                for n in getattr(notifier, 'notifiers', []):
                    cls_name = type(n).__name__
                    backend = getattr(n, 'backend_name', 'N/A')
                    lines.append(f"Notifier: {cls_name} (backend={backend})")

            self._debug_mobile_text.value = "\n".join(lines)
            self._debug_mobile_text.color = C.TEXT_PRIMARY
        except Exception as ex:
            self._debug_mobile_text.value = f"Lỗi: {ex}"
            self._debug_mobile_text.color = C.CRITICAL
        self._debug_mobile_text.update()

    def _do_test_vibration(self):
        """Test device vibration (Android only)."""
        try:
            import platform_utils as pu
            if pu.IS_ANDROID:
                try:
                    from jnius import autoclass
                    Context = autoclass('android.content.Context')
                    PythonActivity = autoclass('org.kivy.android.PythonActivity')
                    vibrator = PythonActivity.mActivity.getSystemService(Context.VIBRATOR_SERVICE)
                    if vibrator and vibrator.hasVibrator():
                        vibrator.vibrate(500)  # 500ms
                        self._debug_mobile_text.value = "✅ Đã rung thiết bị (500ms)"
                        self._debug_mobile_text.color = C.SAFE
                    else:
                        self._debug_mobile_text.value = "⚠️ Thiết bị không hỗ trợ rung"
                        self._debug_mobile_text.color = C.WARNING
                except ImportError:
                    # Try Flet's haptic feedback
                    try:
                        from flet import HapticFeedback
                        self._page.run_task(lambda: self._page.haptic_feedback(HapticFeedback.MEDIUM_IMPACT))
                        self._debug_mobile_text.value = "✅ Đã gửi haptic feedback (Flet)"
                        self._debug_mobile_text.color = C.SAFE
                    except Exception:
                        self._debug_mobile_text.value = "⚠️ Không thể rung (thiếu jnius và Flet haptic)"
                        self._debug_mobile_text.color = C.WARNING
            elif pu.IS_IOS:
                try:
                    self._page.haptic_feedback("medium")
                    self._debug_mobile_text.value = "✅ Đã gửi haptic feedback (iOS)"
                    self._debug_mobile_text.color = C.SAFE
                except Exception:
                    self._debug_mobile_text.value = "⚠️ Haptic feedback không khả dụng trên iOS"
                    self._debug_mobile_text.color = C.WARNING
            else:
                self._debug_mobile_text.value = "ℹ️ Vibration chỉ hỗ trợ trên Android/iOS"
                self._debug_mobile_text.color = C.TEXT_SECONDARY
        except Exception as ex:
            self._debug_mobile_text.value = f"Lỗi vibration: {ex}"
            self._debug_mobile_text.color = C.CRITICAL
        self._debug_mobile_text.update()

    def _do_check_battery_opt(self):
        """Check battery optimization / power saving status (Android)."""
        try:
            import platform_utils as pu
            lines = []

            if pu.IS_ANDROID:
                try:
                    from jnius import autoclass
                    Context = autoclass('android.content.Context')
                    PythonActivity = autoclass('org.kivy.android.PythonActivity')
                    pm = PythonActivity.mActivity.getSystemService(Context.POWER_SERVICE)
                    pkg = PythonActivity.mActivity.getPackageName()
                    ignored = pm.isIgnoringBatteryOptimizations(pkg)
                    lines.append(f"Package: {pkg}")
                    lines.append(f"Battery optimization: {'❌ BẬT (app bị giới hạn)' if not ignored else '✅ TẮT (app không bị giới hạn)'}")
                    if not ignored:
                        lines.append("⚠️ Background check có thể bị delay bởi Doze mode")
                        lines.append("Vào: Cài đặt → Pin → Tối ưu pin → UTHelper → Không tối ưu")
                except ImportError:
                    lines.append("⚠️ Không thể kiểm tra (thiếu jnius)")
                    lines.append("Background check dựa vào AlarmManager")
            elif pu.IS_IOS:
                lines.append("iOS: Không có battery optimization API")
                lines.append("Background App Refresh cần được bật trong Settings")
            else:
                lines.append("Desktop: Không áp dụng battery optimization")

            # Show scheduler status
            try:
                from config import settings as cfg
                lines.append(f"\nBackground check: {'BẬT' if cfg.BACKGROUND_CHECK_ANDROID else 'TẮT'}")
                lines.append(f"Interval: {cfg.BACKGROUND_CHECK_INTERVAL} phút")
            except Exception:
                pass

            self._debug_mobile_text.value = "\n".join(lines)
            self._debug_mobile_text.color = C.TEXT_PRIMARY
        except Exception as ex:
            self._debug_mobile_text.value = f"Lỗi kiểm tra pin: {ex}"
            self._debug_mobile_text.color = C.CRITICAL
        self._debug_mobile_text.update()

    def _do_mock_multi_notif(self):
        """Send 3 mock notifications in sequence to test batching."""
        try:
            notifier = getattr(self._orchestrator, 'notifier', None)
            if not notifier:
                self._debug_mobile_text.value = "❌ NotificationManager not available"
                self._debug_mobile_text.color = C.CRITICAL
                self._debug_mobile_text.update()
                return

            mobile_n = None
            for n in getattr(notifier, 'notifiers', []):
                if hasattr(n, 'backend_name'):
                    mobile_n = n
                    break

            if not mobile_n:
                self._debug_mobile_text.value = "⚠️ Không tìm thấy MobileNotifier"
                self._debug_mobile_text.color = C.WARNING
                self._debug_mobile_text.update()
                return

            mock_data = [
                {"title": "📝 Bài tập Lập trình", "course_name": "Lập trình Python", "remaining": "2 giờ"},
                {"title": "📋 Quiz Cơ sở dữ liệu", "course_name": "Cơ sở dữ liệu", "remaining": "1 ngày"},
                {"title": "✋ Điểm danh Toán rời rạc", "course_name": "Toán rời rạc", "remaining": "30 phút"},
            ]
            result = mobile_n.notify(mock_data)
            self._debug_mobile_text.value = f"Multi-notif (x3): {'✅ Đã gửi' if result else '❌ Thất bại'}\n" + "\n".join(
                [f"  • {m['title']} — Còn {m['remaining']}" for m in mock_data]
            )
            self._debug_mobile_text.color = C.SAFE if result else C.CRITICAL
        except Exception as ex:
            self._debug_mobile_text.value = f"Lỗi multi-notif: {ex}"
            self._debug_mobile_text.color = C.CRITICAL
        self._debug_mobile_text.update()

    # ── Update checker ──
    def _do_force_check_update(self):
        """Force check for app updates."""
        import threading
        self._debug_update_text.value = "Đang kiểm tra cập nhật..."
        self._debug_update_text.color = C.TEXT_SECONDARY
        self._debug_update_text.update()

        def _worker():
            try:
                from core.update_checker import check_for_update
                from gui.app_controller import APP_VERSION
                has_update, version, url, asset = check_for_update(APP_VERSION)
                if has_update:
                    result = f"Phiên bản mới: v{version}\nURL: {url}\nAsset: {asset or 'N/A'}"
                    color = C.ACCENT
                else:
                    result = f"Đang dùng phiên bản mới nhất (v{APP_VERSION})."
                    color = C.SAFE
            except Exception as ex:
                result = f"Lỗi kiểm tra: {ex}"
                color = C.CRITICAL

            self._debug_update_text.value = result
            self._debug_update_text.color = color
            try:
                self._debug_update_text.update()
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True, name="debug-update").start()

    # ── Broadcast all channels ──
    def _do_test_broadcast(self):
        """Send mock notification to ALL registered channels simultaneously."""
        t = getattr(self, '_mock_type_drp', ft.Dropdown(value='critical')).value
        sent = []
        if self._on_test_tray and not _pu.IS_MOBILE:
            try:
                self._on_test_tray(t); sent.append("Windows Tray")
            except Exception:
                pass
        if hasattr(self, '_on_test_mobile') and self._on_test_mobile and _pu.IS_MOBILE:
            try:
                self._on_test_mobile(t); sent.append("Mobile")
            except Exception:
                pass
        for name, cb in [("Telegram", "_on_test_tele"), ("Discord", "_on_test_discord"), ("Gmail", "_on_test_mail")]:
            fn = getattr(self, cb, None)
            if fn:
                try:
                    fn(t); sent.append(name)
                except Exception:
                    pass
        self._debug_info_text.value = f"Broadcast [{t}] tới: {', '.join(sent) or 'Không có kênh nào'}"
        self._debug_info_text.color = C.SAFE if sent else C.WARNING
        self._debug_info_text.update()

    # ── Network latency test ──
    def _do_test_latency(self):
        """Ping Moodle server and measure response time."""
        import threading
        self._debug_info_text.value = "Đang ping Moodle..."
        self._debug_info_text.color = C.TEXT_SECONDARY
        self._debug_info_text.update()

        def _ping():
            import time, urllib.request
            url = settings.MOODLE_BASE_URL.rstrip("/")
            results = []
            for _ in range(3):
                try:
                    start = time.perf_counter()
                    req = urllib.request.Request(url, method="HEAD")
                    req.add_header("User-Agent", "UTHelper/ping")
                    urllib.request.urlopen(req, timeout=10)
                    results.append((time.perf_counter() - start) * 1000)
                except Exception:
                    results.append(None)

            valid = [r for r in results if r is not None]
            if valid:
                avg = sum(valid) / len(valid)
                detail = " | ".join([f"{r:.0f}ms" if r else "FAIL" for r in results])
                result = f"Ping {url}\n{detail}\nTB: {avg:.0f}ms ({len(valid)}/3 OK)"
                color = C.SAFE if avg < 1000 else C.WARNING
            else:
                result = f"Không thể kết nối {url}"
                color = C.CRITICAL
            self._debug_info_text.value = result
            self._debug_info_text.color = color
            try: self._debug_info_text.update()
            except Exception: pass

        threading.Thread(target=_ping, daemon=True, name="debug-ping").start()

    # ── Show registered notifiers ──
    def _do_show_notifiers(self):
        """Show all registered notification channels and their status."""
        try:
            mgr = getattr(self._orchestrator, 'notifier', None)
            if not mgr or not hasattr(mgr, 'notifiers'):
                self._debug_info_text.value = "NotificationManager chưa khởi tạo."
                self._debug_info_text.color = C.WARNING
            else:
                ns = mgr.notifiers
                if not ns:
                    lines = ["Không có notifier nào đăng ký."]
                else:
                    lines = [f"{len(ns)} kênh đã đăng ký:"]
                    for i, n in enumerate(ns, 1):
                        cls = n.__class__.__name__
                        extra = f" ({n.backend_name})" if hasattr(n, 'backend_name') else ""
                        lines.append(f"  {i}. {cls}{extra}")
                self._debug_info_text.value = "\n".join(lines)
                self._debug_info_text.color = C.SAFE if ns else C.WARNING
        except Exception as ex:
            self._debug_info_text.value = f"Lỗi: {ex}"
            self._debug_info_text.color = C.CRITICAL
        self._debug_info_text.update()

    # ── DND status check ──
    def _do_check_dnd(self):
        """Check current Do Not Disturb status."""
        from datetime import datetime
        now = datetime.now()
        enabled = settings.NOTIFY_DND_ENABLE
        s, e = settings.NOTIFY_DND_START, settings.NOTIFY_DND_END
        h = now.hour
        if not enabled:
            is_active = False
        elif s == e:
            is_active = True
        elif s > e:
            is_active = h >= s or h < e
        else:
            is_active = s <= h < e

        lines = [
            f"DND: {'BẬT' if enabled else 'TẮT'}",
            f"Khung giờ: {s}:00 – {e}:00",
            f"Hiện tại: {now.strftime('%H:%M')}",
            f"Trạng thái: {'ĐANG IM LẶNG' if (enabled and is_active) else 'Bình thường'}",
        ]
        self._debug_info_text.value = "\n".join(lines)
        self._debug_info_text.color = C.WARNING if (enabled and is_active) else C.SAFE
        self._debug_info_text.update()

    # ── Cache statistics ──
    def _do_show_cache_stats(self):
        """Show cache sizes and statistics."""
        import os
        from config import _USER_DATA_DIR
        from core.safe_file_io import SafeFileIO
        stats = []
        for label, fname in [("Cache thông báo", "notifications_cache.json"),
                              ("Lịch sử thông báo", "notification_history.json"),
                              ("Cache offline", "activities_cache.json")]:
            path = _USER_DATA_DIR / fname
            if path.exists():
                size = os.path.getsize(str(path))
                try:
                    data = SafeFileIO.read_json_safe(path, dict)
                    count = len(data) if isinstance(data, (list, dict)) else "?"
                    stats.append(f"{label}: {count} mục ({size:,} B)")
                except Exception:
                    stats.append(f"{label}: {size:,} B")
            else:
                stats.append(f"{label}: trống")


        detail = self._orchestrator.get_cached_details_snapshot() if hasattr(self._orchestrator, 'get_cached_details_snapshot') else {}
        stats.append(f"Detail cache (RAM): {len(detail)} mục")
        sp = _USER_DATA_DIR / "settings.json"
        if sp.exists():
            stats.append(f"Settings: {os.path.getsize(str(sp)):,} B")
        self._debug_cache_stats.value = "\n".join(stats)
        self._debug_cache_stats.color = C.TEXT_PRIMARY
        self._debug_cache_stats.update()

    # ── Force data refresh ──
    def _do_force_refresh(self):
        """Trigger immediate data reload from Moodle."""
        async def _refresh():
            self._debug_info_text.value = "Đang tải lại dữ liệu..."
            self._debug_info_text.color = C.TEXT_SECONDARY
            self._debug_info_text.update()
            try:
                import time
                start = time.perf_counter()
                acts = await self._orchestrator.get_latest_activities_async()
                elapsed = time.perf_counter() - start
                self._debug_info_text.value = f"Tải xong {len(acts) if acts else 0} hoạt động trong {elapsed:.1f}s"
                self._debug_info_text.color = C.SAFE
            except Exception as ex:
                self._debug_info_text.value = f"Lỗi: {ex}"
                self._debug_info_text.color = C.CRITICAL
            self._debug_info_text.update()
        self._page.run_task(_refresh)

    # ── Reset settings ──
    def _do_reset_settings(self):
        """Reset all settings to factory defaults (with confirmation dialog)."""
        async def _confirm():
            confirmed = [False]
            def _yes(e):
                confirmed[0] = True; dlg.open = False; self._page.update()
            def _no(e):
                dlg.open = False; self._page.update()

            dlg = ft.AlertDialog(
                modal=True,
                title=ft.Text("Xác nhận Reset"),
                content=ft.Text("Đặt lại TẤT CẢ cài đặt về mặc định?\nThao tác không thể hoàn tác."),
                actions=[
                    ft.TextButton("Hủy", on_click=_no),
                    ft.TextButton("Reset", on_click=_yes, style=ft.ButtonStyle(color=C.CRITICAL)),
                ],
            )
            self._page.overlay.append(dlg)
            dlg.open = True
            self._page.update()

            import asyncio
            for _ in range(300):
                await asyncio.sleep(0.1)
                if not dlg.open:
                    break
            if dlg in self._page.overlay:
                self._page.overlay.remove(dlg)

            if confirmed[0]:
                try:
                    from config import _USER_DATA_DIR
                    import os
                    sp = _USER_DATA_DIR / "settings.json"
                    if sp.exists():
                        os.remove(str(sp))
                    self._debug_info_text.value = "Settings đã reset. Khởi động lại app để áp dụng."
                    self._debug_info_text.color = C.SAFE
                except Exception as ex:
                    self._debug_info_text.value = f"Lỗi reset: {ex}"
                    self._debug_info_text.color = C.CRITICAL
                self._debug_info_text.update()
        self._page.run_task(_confirm)

    def load_current_settings(self):
        for tile in getattr(self, '_tiles', []):
            tile.expanded = False

        self._test_login_status.value = ""
        self._test_login_btn.text = "Kiểm tra kết nối"
        self._test_login_btn.icon = ft.Icons.WIFI_FIND_ROUNDED
        self._test_loading_bar.visible = False

        # Reset unsaved indicator
        if hasattr(self, '_unsaved_dot'):
            self._unsaved_dot.visible = False

        # Reload theme selection and store original for revert
        self._selected_theme = getattr(settings, 'THEME', 'midnight_blue')
        self._original_theme = self._selected_theme
        self._rebuild_theme_cards()

        if hasattr(self, '_c_tb_critical'):
            self._c_tb_critical.value = getattr(settings, 'COLOR_CRITICAL', '#EF4444')
            self._c_tb_warning.value = getattr(settings, 'COLOR_WARNING', '#F59E0B')
            self._c_tb_safe.value = getattr(settings, 'COLOR_SAFE', '#10B981')
            self._c_tb_quiz.value = getattr(settings, 'COLOR_QUIZ', '#7C3AED')
            self._c_tb_ass.value = getattr(settings, 'COLOR_ASSIGNMENT', '#2563EB')
            self._c_tb_att.value = getattr(settings, 'COLOR_ATTENDANCE', '#D97706')
            self._c_tb_open.value = getattr(settings, 'COLOR_OPEN', '#0891B2')
            self._c_tb_other.value = getattr(settings, 'COLOR_OTHER', '#6B7280')

        self._username_field.value = settings.UTH_USERNAME
        self._password_field.value = settings.UTH_PASSWORD
        if not _pu.IS_MOBILE:
            self._sw_always_on_top.value = settings.ALWAYS_ON_TOP
        self._sw_submitted.value = settings.INCLUDE_SUBMITTED
        self._sw_graded.value = settings.INCLUDE_GRADED
        if not _pu.IS_MOBILE:
            self._sw_start_with_windows.value = settings.START_WITH_WINDOWS
            self._sw_start_minimized.value = settings.START_MINIMIZED
            self._sw_minimize_to_tray.value = settings.MINIMIZE_TO_TRAY
        self._sw_bg_check.value = settings.BACKGROUND_CHECK_ANDROID
        self._bg_interval_field.value = str(settings.BACKGROUND_CHECK_INTERVAL)
        self._toggle_bg_check_ui()
        self._sw_email.value = settings.ENABLE_GMAIL
        self._sw_discord.value = getattr(settings, 'ENABLE_DISCORD', False)
        self._gmail_addr_field.value = getattr(settings, 'GMAIL_ADDRESS', '')
        self._gmail_pw_field.value = getattr(settings, 'GMAIL_APP_PASSWORD', '')
        self._discord_wh_field.value = getattr(settings, 'DISCORD_WEBHOOK_URL', '')
        self._toggle_integration_ui()
        self._sw_telegram.value = settings.ENABLE_TELEGRAM
        self._tel_token_field.value = settings.TELEGRAM_BOT_TOKEN
        self._tel_chat_field.value = settings.TELEGRAM_CHAT_ID
        self._toggle_telegram_ui()
        self._interval_field.value = str(settings.CHECK_INTERVAL_MINUTES)
        self._dd_poll_interval.value = str(getattr(settings, 'POLL_INTERVAL_MINUTES', 15))
        self._fetch_months_field.value = str(settings.FETCH_MONTHS)
        self._critical_hours_field.value = str(settings.URGENCY_CRITICAL_HOURS)
        self._warning_hours_field.value = str(settings.URGENCY_WARNING_HOURS)
        self._opening_soon_hours_field.value = str(settings.OPENING_SOON_HOURS)
        self._notify_min_field.value = str(settings.NOTIFY_MINUTES_BEFORE)
        self._workers_field.value = str(settings.PREFETCH_WORKERS)
        
        self._sw_dnd_enable.value = getattr(settings, 'NOTIFY_DND_ENABLE', False)
        self._dnd_start_field.value = str(getattr(settings, 'NOTIFY_DND_START', 22))
        self._dnd_end_field.value = str(getattr(settings, 'NOTIFY_DND_END', 7))
        self._sw_ignore_sub.value = getattr(settings, 'NOTIFY_IGNORE_SUBMITTED', True)
        _saved_types = getattr(settings, 'NOTIFY_TYPES', ["quiz", "assignment", "attendance"])
        for key, cb in self._notify_type_checks.items():
            cb.value = (key in _saved_types)
        _saved_milestones = getattr(settings, 'NOTIFY_MILESTONES', [72, 24, 3])
        self._milestones_field.value = ", ".join(map(str, _saved_milestones))
        # Sync milestone chips
        for h, chip in self._milestone_chips.items():
            chip.selected = h in _saved_milestones
        _active_count = sum(1 for h in _saved_milestones if h in self._milestone_chips)
        self._milestone_summary.value = f"Bạn sẽ nhận {_active_count} lần nhắc cho mỗi deadline" if _active_count else "Không có mốc nhắc nhở nào"
        # Sync profile cards
        self._current_profile = getattr(settings, 'NOTIFICATION_PROFILE', 'balanced')
        for k, card in self._profile_cards.items():
            is_sel = (k == self._current_profile)
            card.border = ft.Border.all(2, C.ACCENT) if is_sel else ft.Border.all(1, C.BORDER)
            card.bgcolor = C.ACCENT + "15" if is_sel else C.SURFACE
        self._update_profile_summary()
        self._update_dnd_summary()
        self._muted_courses_field.value = ", ".join(getattr(settings, 'NOTIFY_MUTED_COURSES', []))

        # Cập nhật danh sách môn học cho ExpansionTile
        self._known_courses = set()
        if hasattr(self, '_orchestrator'):
            cache = (
                self._orchestrator.get_cached_details_snapshot()
                if hasattr(self._orchestrator, "get_cached_details_snapshot")
                else getattr(self._orchestrator, "_detail_cache", {})
            )
            for cached in cache.values():
                c = cached.get('course')
                if c:
                    from gui.core.utils import clean_course_name
                    c_name = clean_course_name(c)
                    if c_name: self._known_courses.add(c_name)

        if getattr(self, '_known_courses', None):
            current = [x.strip() for x in self._muted_courses_field.value.split(",") if x.strip()]
            
            def make_toggle(course):
                def _on_check(e):
                    curr = [x.strip() for x in self._muted_courses_field.value.split(",") if x.strip()]
                    if e.control.value and course not in curr:
                        curr.append(course)
                    elif not e.control.value and course in curr:
                        curr.remove(course)
                    self._muted_courses_field.value = ", ".join(curr)
                    self._muted_courses_field.update()
                return ft.Checkbox(label=course, value=(course in current), on_change=_on_check, fill_color=C.ACCENT)
            
            self._muted_courses_list.controls = [make_toggle(c) for c in sorted(list(self._known_courses))]
            if hasattr(self._muted_courses_list, "page") and self._muted_courses_list.page:
                self._muted_courses_list.update()
                
            self._muted_courses_drp.visible = True
        else:
            self._muted_courses_drp.visible = False

        self._save_status.value = ""
        self.update()

    def has_changes(self):
        if self._selected_theme != getattr(settings, 'THEME', 'midnight_blue'): return True
        if self._username_field.value != settings.UTH_USERNAME: return True
        if self._password_field.value != settings.UTH_PASSWORD: return True
        if not _pu.IS_MOBILE:
            if self._sw_always_on_top.value != settings.ALWAYS_ON_TOP: return True
        if self._sw_submitted.value != settings.INCLUDE_SUBMITTED: return True
        if self._sw_graded.value != settings.INCLUDE_GRADED: return True
        if not _pu.IS_MOBILE:
            if self._sw_start_with_windows.value != settings.START_WITH_WINDOWS: return True
            if self._sw_start_minimized.value != settings.START_MINIMIZED: return True
            if self._sw_minimize_to_tray.value != settings.MINIMIZE_TO_TRAY: return True
        if self._sw_bg_check.value != settings.BACKGROUND_CHECK_ANDROID: return True
        if self._bg_interval_field.value != str(settings.BACKGROUND_CHECK_INTERVAL): return True
        if self._sw_email.value != getattr(settings, 'ENABLE_GMAIL', False): return True
        if self._sw_discord.value != getattr(settings, 'ENABLE_DISCORD', False): return True
        if getattr(self, '_gmail_addr_field', None) and self._gmail_addr_field.value != getattr(settings, 'GMAIL_ADDRESS', ''): return True
        if getattr(self, '_gmail_pw_field', None) and self._gmail_pw_field.value != getattr(settings, 'GMAIL_APP_PASSWORD', ''): return True
        if getattr(self, '_discord_wh_field', None) and self._discord_wh_field.value != getattr(settings, 'DISCORD_WEBHOOK_URL', ''): return True
        if self._sw_telegram.value != settings.ENABLE_TELEGRAM: return True
        if self._tel_token_field.value != settings.TELEGRAM_BOT_TOKEN: return True
        if self._tel_chat_field.value != settings.TELEGRAM_CHAT_ID: return True
        if self._sw_debug.value != settings.DEBUG_MODE: return True
        if self._interval_field.value != str(settings.CHECK_INTERVAL_MINUTES): return True
        if self._fetch_months_field.value != str(settings.FETCH_MONTHS): return True
        if self._critical_hours_field.value != str(settings.URGENCY_CRITICAL_HOURS): return True
        if self._warning_hours_field.value != str(settings.URGENCY_WARNING_HOURS): return True
        if self._opening_soon_hours_field.value != str(settings.OPENING_SOON_HOURS): return True
        if self._notify_min_field.value != str(settings.NOTIFY_MINUTES_BEFORE): return True
        if self._workers_field.value != str(settings.PREFETCH_WORKERS): return True
        
        if self._sw_dnd_enable.value != getattr(settings, 'NOTIFY_DND_ENABLE', False): return True
        if self._dnd_start_field.value != str(getattr(settings, 'NOTIFY_DND_START', 22)): return True
        if self._dnd_end_field.value != str(getattr(settings, 'NOTIFY_DND_END', 7)): return True
        if self._sw_ignore_sub.value != getattr(settings, 'NOTIFY_IGNORE_SUBMITTED', True): return True
        if getattr(self, '_current_profile', 'balanced') != getattr(settings, 'NOTIFICATION_PROFILE', 'balanced'): return True
        _saved_types = set(getattr(settings, 'NOTIFY_TYPES', ["quiz", "assignment", "attendance"]))
        _current_types = set(k for k, cb in self._notify_type_checks.items() if cb.value)
        if _saved_types != _current_types: return True
        if self._milestones_field.value != ", ".join(map(str, getattr(settings, 'NOTIFY_MILESTONES', [72, 24, 3]))): return True
        if self._muted_courses_field.value != ", ".join(getattr(settings, 'NOTIFY_MUTED_COURSES', [])): return True
        
        return False

    async def _handle_back(self, e):
        import logging
        _log = logging.getLogger("settings.dialog")
        _log.warning("=== _handle_back called, has_changes=%s ===", self.has_changes())
        if self.has_changes():
            def close_dlg(e):
                _log.warning(">>> CANCEL button clicked!")
                self._page.pop_dialog()
                self._page.update()

            def discard_and_close(e):
                _log.warning(">>> DISCARD button clicked!")
                self._page.pop_dialog()
                # Revert theme to original if it was changed
                if self._selected_theme != self._original_theme:
                    apply_theme(self._original_theme)
                    from gui.core.theme import set_page_theme
                    set_page_theme(self._page)
                    if self._on_theme_preview:
                        self._on_theme_preview()
                self._on_close_cb()
                _log.warning("  discard done")

            def save_and_close(e):
                _log.warning(">>> SAVE button clicked!")
                self._page.pop_dialog()
                # Schedule async save + close via run_task
                async def _do_save_close():
                    _log.warning("  _do_save_close running...")
                    await self._save(e)
                    self._on_close_cb()
                    _log.warning("  save+close done")
                self._page.run_task(_do_save_close)

            confirm_dlg = ft.AlertDialog(
                title=ft.Row(controls=[
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=20, color=C.WARNING),
                    ft.Text("Chưa lưu cài đặt", size=16, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                ], spacing=8),
                content=ft.Text("Bạn có thay đổi chưa lưu. Bạn muốn lưu lại không?",
                                size=13, color=C.TEXT_SECONDARY),
                actions=[
                    ft.TextButton("Hủy", on_click=close_dlg,
                                  style=ft.ButtonStyle(color=C.TEXT_SECONDARY)),
                    ft.TextButton("Bỏ thay đổi", on_click=discard_and_close,
                                  style=ft.ButtonStyle(color=C.CRITICAL)),
                    ft.TextButton("Lưu", on_click=save_and_close,
                                  style=ft.ButtonStyle(color=C.ACCENT)),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                shape=ft.RoundedRectangleBorder(radius=12),
                bgcolor=C.BG,
            )
            self._page.show_dialog(confirm_dlg)
            _log.warning("Dialog opened via page.show_dialog()")
        else:
            self._on_close_cb()

    async def _save(self, e):
        try:
            # Save theme preset
            settings.THEME                   = self._selected_theme
            settings.COLOR_CRITICAL          = getattr(self, '_c_tb_critical', ft.TextField(value='#EF4444')).value
            settings.COLOR_WARNING           = getattr(self, '_c_tb_warning', ft.TextField(value='#F59E0B')).value
            settings.COLOR_SAFE              = getattr(self, '_c_tb_safe', ft.TextField(value='#10B981')).value
            settings.COLOR_QUIZ              = getattr(self, '_c_tb_quiz', ft.TextField(value='#7C3AED')).value
            settings.COLOR_ASSIGNMENT        = getattr(self, '_c_tb_ass', ft.TextField(value='#2563EB')).value
            settings.COLOR_ATTENDANCE        = getattr(self, '_c_tb_att', ft.TextField(value='#D97706')).value
            settings.COLOR_OPEN              = getattr(self, '_c_tb_open', ft.TextField(value='#0891B2')).value
            settings.COLOR_OTHER             = getattr(self, '_c_tb_other', ft.TextField(value='#6B7280')).value
            settings.UTH_USERNAME            = self._username_field.value
            settings.UTH_PASSWORD            = self._password_field.value
            if not _pu.IS_MOBILE:
                settings.ALWAYS_ON_TOP           = self._sw_always_on_top.value
            settings.INCLUDE_SUBMITTED       = self._sw_submitted.value
            settings.INCLUDE_GRADED          = self._sw_graded.value
            settings.CHECK_INTERVAL_MINUTES  = max(0, int(self._interval_field.value or "60"))
            settings.POLL_INTERVAL_MINUTES   = max(5, int(self._dd_poll_interval.value or "15"))
            settings.FETCH_MONTHS            = max(1, min(int(self._fetch_months_field.value or "1"), 3))
            settings.URGENCY_CRITICAL_HOURS  = max(1, int(self._critical_hours_field.value or "24"))
            settings.URGENCY_WARNING_HOURS   = max(1, int(self._warning_hours_field.value or "72"))
            settings.OPENING_SOON_HOURS      = max(1, int(self._opening_soon_hours_field.value or "72"))
            settings.NOTIFY_MINUTES_BEFORE   = max(0, int(self._notify_min_field.value or "30"))
            workers = int(self._workers_field.value or "4")
            settings.PREFETCH_WORKERS        = max(1, min(workers, 10))
            self._workers_field.value        = str(settings.PREFETCH_WORKERS)
            
            # Desktop-only settings (autostart, tray, always on top)
            if not _pu.IS_MOBILE:
                if settings.START_WITH_WINDOWS != self._sw_start_with_windows.value:
                    try:
                        import core.autostart as autostart
                        if self._sw_start_with_windows.value:
                            autostart.add_to_startup()
                        else:
                            autostart.remove_from_startup()
                    except Exception as ex:
                        try:
                            import logging
                            logging.error(f"Failed handling autostart: {ex}")
                        except Exception:
                            pass
                        
                settings.START_WITH_WINDOWS = self._sw_start_with_windows.value
                settings.START_MINIMIZED = self._sw_start_minimized.value
                settings.MINIMIZE_TO_TRAY = self._sw_minimize_to_tray.value

            settings.BACKGROUND_CHECK_ANDROID = self._sw_bg_check.value
            settings.BACKGROUND_CHECK_INTERVAL = max(5, int(self._bg_interval_field.value or "30"))

            settings.ENABLE_GMAIL            = self._sw_email.value
            settings.ENABLE_DISCORD          = self._sw_discord.value
            settings.NOTIFY_DND_ENABLE       = self._sw_dnd_enable.value
            settings.NOTIFY_DND_START        = max(0, min(23, int(self._dnd_start_field.value or "22")))
            settings.NOTIFY_DND_END          = max(0, min(23, int(self._dnd_end_field.value or "7")))
            settings.NOTIFY_IGNORE_SUBMITTED = self._sw_ignore_sub.value
            settings.NOTIFICATION_PROFILE    = getattr(self, '_current_profile', 'balanced')
            settings.NOTIFY_TYPES = [k for k, cb in self._notify_type_checks.items() if cb.value]
            
            try:
                settings.NOTIFY_MILESTONES = [int(x.strip()) for x in self._milestones_field.value.split(",") if x.strip()]
            except ValueError:
                settings.NOTIFY_MILESTONES = [72, 24, 3]
            
            settings.NOTIFY_MUTED_COURSES = [x.strip() for x in self._muted_courses_field.value.split(",") if x.strip()]

            save_settings()

            # Update original theme reference so discard won't revert
            self._original_theme = self._selected_theme

            self._save_status.value   = "Đã lưu cài đặt thành công"
            self._save_status.color   = C.SAFE
            if hasattr(self, '_unsaved_dot'):
                self._unsaved_dot.visible = False
            
            if not _pu.IS_MOBILE:
                self._page.window.always_on_top = settings.ALWAYS_ON_TOP
            self.update()

            if self._on_saved:
                self._on_saved()
        except ValueError:
            self._save_status.value = "Lỗi: Vui lòng nhập số hợp lệ!"
            self._save_status.color = C.CRITICAL
            self.update()
        except Exception as e_err:
            self._save_status.value = f"Lỗi không xác định: {str(e_err)}"
            self._save_status.color = C.CRITICAL
            self.update()

def main(page: ft.Page):
    """Stub main function to support Flet Preview on this file directly."""
    # Apply compatibility shims if running directly
    try:
        from gui.flet_compat import patch_flet
        patch_flet()
    except Exception:
        pass
    from gui.app_controller import AppController
    AppController(page)

if __name__ == "__main__":
    ft.run(main=main, assets_dir=os.path.join(_project_root, "assets"))
