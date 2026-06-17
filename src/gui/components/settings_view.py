import flet as ft
import asyncio
from gui.core.theme import C
from config import settings
from config import save_settings

class SettingsView(ft.Container):
    def __init__(self, page: ft.Page, orchestrator, on_close, on_saved=None, on_test_tray=None, on_test_tele=None, on_test_discord=None, on_test_mail=None):
        super().__init__()
        self._page    = page
        self._orchestrator = orchestrator
        self._on_close_cb = on_close
        self._on_saved = on_saved
        self._on_test_tray = on_test_tray
        self._on_test_tele = on_test_tele
        self._on_test_discord = on_test_discord
        self._on_test_mail = on_test_mail
        self.visible  = False
        self.expand   = True
        self.bgcolor  = C.BG

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

        self._test_login_btn = ft.ElevatedButton(
            "Kiểm tra kết nối",
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

            r_sl = ft.Slider(min=0, max=255, value=r_v, active_color=ft.colors.RED_400, on_change=_update_from_sliders, expand=True)
            g_sl = ft.Slider(min=0, max=255, value=g_v, active_color=ft.colors.GREEN_400, on_change=_update_from_sliders, expand=True)
            b_sl = ft.Slider(min=0, max=255, value=b_v, active_color=ft.colors.BLUE_400, on_change=_update_from_sliders, expand=True)
            
            hex_inp = ft.TextField(value=tb_field.value, on_change=_update_from_hex, text_align=ft.TextAlign.CENTER, border_radius=8, content_padding=5, text_size=13, width=100)
            prv = ft.Container(width=100, height=40, bgcolor=tb_field.value, border_radius=8, border=ft.border.all(1, C.BORDER))

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
                        ft.Row([ft.Text("R", color=ft.colors.RED_400, weight=ft.FontWeight.BOLD, width=20), r_sl]),
                        ft.Row([ft.Text("G", color=ft.colors.GREEN_400, weight=ft.FontWeight.BOLD, width=20), g_sl]),
                        ft.Row([ft.Text("B", color=ft.colors.BLUE_400, weight=ft.FontWeight.BOLD, width=20), b_sl]),
                    ], tight=True)
                ),
                actions=[
                    ft.TextButton("Hủy", on_click=_cancel),
                    ft.ElevatedButton("Áp dụng", on_click=_apply, bgcolor=C.ACCENT, color=ft.colors.WHITE),
                ],
                shape=ft.RoundedRectangleBorder(radius=12)
            )
            self._page.overlay.append(dlg)
            dlg.open = True
            self._page.update()

        def _color_field(label_text, default_color):
            tb = ft.TextField(value=default_color, width=90, text_size=12, height=36, border_color=C.BORDER, focused_border_color=C.ACCENT, color=C.TEXT_PRIMARY, bgcolor=C.BG, content_padding=6)
            box = ft.Container(width=24, height=24, bgcolor=default_color, border_radius=4, border=ft.border.all(1, "#333333"), ink=True)
            
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
            return tb, ft.Row([ft.Text(label_text, size=13, color=C.TEXT_PRIMARY, expand=True), box_click, tb], spacing=10, tight=True)

        self._c_tb_critical, row_cri = _color_field("Cấp bách / Quá hạn", getattr(settings, 'COLOR_CRITICAL', '#EF4444'))
        self._c_tb_warning, row_warn = _color_field("Sắp tới", getattr(settings, 'COLOR_WARNING', '#F59E0B'))
        self._c_tb_safe, row_safe = _color_field("An toàn / Thường", getattr(settings, 'COLOR_SAFE', '#10B981'))
        self._c_tb_quiz, row_quiz = _color_field("Tag Quiz", getattr(settings, 'COLOR_QUIZ', '#7C3AED'))
        self._c_tb_ass, row_ass = _color_field("Tag Bài tập", getattr(settings, 'COLOR_ASSIGNMENT', '#2563EB'))
        self._c_tb_att, row_att = _color_field("Tag Điểm danh", getattr(settings, 'COLOR_ATTENDANCE', '#D97706'))
        self._c_tb_open, row_open = _color_field("Tag Sắp mở", getattr(settings, 'COLOR_OPEN', '#0891B2'))
        self._c_tb_other, row_other = _color_field("Tag Sự kiện", getattr(settings, 'COLOR_OTHER', '#6B7280'))
        
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
        
        self._test_panel = ft.Container(
            content=ft.Column([
                ft.Text("Công cụ kiểm thử (Debug / Mock)", color=C.CRITICAL, weight=ft.FontWeight.BOLD),
                self._mock_type_drp,
                ft.Row([
                    ft.ElevatedButton("Windows Tray", on_click=lambda e: self._do_test_tray(), bgcolor=C.SURFACE, color=C.TEXT_PRIMARY),
                    ft.ElevatedButton("Telegram", on_click=lambda e: self._do_test_tele(), bgcolor=C.SURFACE, color="#0088cc"),
                ], wrap=True),
                ft.Row([
                    ft.ElevatedButton("Discord", on_click=lambda e: self._do_test_discord(), bgcolor=C.SURFACE, color="#5865F2"),
                    ft.ElevatedButton("Gmail", on_click=lambda e: self._do_test_mail(), bgcolor=C.SURFACE, color="#EA4335"),
                ], wrap=True),
            ]),
            visible=settings.DEBUG_MODE,
            padding=10, border=ft.border.all(1, C.CRITICAL), border_radius=8, margin=ft.margin.only(top=10)
        )
        
        self._interval_field = ft.TextField(
            value=str(settings.CHECK_INTERVAL_MINUTES),
            label="Cập nhật mỗi X phút (0 để tắt)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY,
            bgcolor=C.BG, border_radius=10,
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

        # Thêm nhóm tính năng báo thức (UTHelper Phase 3)
        self._sw_dnd_enable = ft.Switch(
            value=getattr(settings, 'NOTIFY_DND_ENABLE', False), active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Kích hoạt chế độ Không làm phiền (DND)"
        )
        self._dnd_start_field = ft.TextField(
            value=str(getattr(settings, 'NOTIFY_DND_START', 23)),
            label="Bắt đầu im lặng (giờ)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        )
        self._dnd_end_field = ft.TextField(
            value=str(getattr(settings, 'NOTIFY_DND_END', 6)),
            label="Kết thúc im lặng (giờ)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        )
        self._sw_ignore_sub = ft.Switch(
            value=getattr(settings, 'NOTIFY_IGNORE_SUBMITTED', True), active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bỏ qua bài tập đã nộp/chấm điểm"
        )
        self._milestones_field = ft.TextField(
            value=", ".join(map(str, getattr(settings, 'NOTIFY_MILESTONES', [72, 24, 3]))),
            label="Số giờ nhắc nhở (VD: 72, 24, 3)",
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
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
                    border=ft.border.all(1, C.BORDER)
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

        back_btn = ft.TextButton(
            content=ft.Row(controls=[
                ft.Icon(ft.Icons.ARROW_BACK, size=14, color=C.TEXT_SECONDARY),
                ft.Text("Quay lại", size=13, color=C.TEXT_SECONDARY),
            ], spacing=4, tight=True),
            on_click=self._handle_back,
            style=ft.ButtonStyle(
                color=C.TEXT_SECONDARY,
                overlay_color=ft.Colors.with_opacity(0.1, C.TEXT_SECONDARY)
            )
        )

        save_btn = ft.Container(
            content=ft.Text("Lưu cài đặt", size=13, color=ft.Colors.WHITE,
                            weight=ft.FontWeight.BOLD,
                            text_align=ft.TextAlign.CENTER),
            bgcolor=C.ACCENT,
            padding=ft.Padding.symmetric(vertical=14),
            border_radius=10,
            on_click=self._save,
            ink=True,
            alignment=ft.Alignment(0, 0),
        )

        def _hint(text): return ft.Container(
            content=ft.Text(text, size=11, color=C.TEXT_SECONDARY),
            padding=ft.Padding.only(left=4),
        )
        
        self._tiles = []

        def _setting_group(title, subtitle, controls, default_open=False):
            tile = ft.ExpansionTile(
                title=ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                subtitle=ft.Text(subtitle, size=12, color=C.TEXT_SECONDARY) if subtitle else None,
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
            return ft.Container(
                content=tile,
                bgcolor=C.SURFACE,
                border_radius=10,
                border=ft.border.all(1, C.BORDER),
                padding=0,
                margin=ft.margin.only(bottom=3),
                clip_behavior=ft.ClipBehavior.HARD_EDGE
            )

        self.content = ft.Column(
            controls=[
                ft.Container(
                    content=ft.Row(controls=[back_btn],
                                   alignment=ft.MainAxisAlignment.START),
                    padding=ft.Padding.only(left=8, top=16, bottom=8),
                ),
                ft.Container(
                    content=ft.Column(horizontal_alignment=ft.CrossAxisAlignment.STRETCH, controls=[
                        ft.Text("Cài đặt", size=18, weight=ft.FontWeight.W_700,
                                color=C.TEXT_PRIMARY),
                        ft.Divider(height=20, color=C.BORDER),

                        _setting_group(
                            "Tài khoản UTH",
                            "Thông tin đăng nhập hệ thống elearning",
                            [self._username_field, self._password_field, self._test_loading_bar, self._test_login_status, self._test_login_btn],
                        ),
                        _setting_group(
                            "Hiển thị",
                            "Cách hiển thị trên màn hình",
                            [self._sw_submitted, self._sw_graded, self._sw_always_on_top]
                        ),

                        _setting_group(
                            "Hệ thống",
                            "Khởi động và tự động cập nhật",
                            [
                                self._sw_start_with_windows, self._sw_start_minimized, self._sw_minimize_to_tray,
                                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                                self._interval_field,
                                  _hint("Đặt 0 để tắt tự động cập nhật. Mặc định: 60 phút."),
                                  self._fetch_months_field,
                                  _hint("Số tháng cần lấy sự kiện (1-3). (Mặc định 1)")
                            ]
                        ),

                        _setting_group(
                            "Cảnh báo",
                            "Ngưỡng thời gian màu sắc",
                            [
                                ft.Text("Mức độ", weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                                self._critical_hours_field,
                                self._warning_hours_field,
                                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                                ft.Text("Trạng thái", weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                                self._opening_soon_hours_field,
                                _hint("Hoạt động sẽ được đánh dấu 'Sắp mở' khi thời gian mở nhỏ hơn mức này.")
                            ]
                        ),

                        _setting_group(
                            "Giao diện",
                            "Tùy chỉnh màu thông báo và thẻ",
                            [row_cri, row_warn, row_safe, ft.Divider(height=10, color=C.BORDER), row_quiz, row_ass, row_att, row_open, row_other, ft.Divider(height=10, color=C.BORDER), self.btn_reset]
                        ),
                        _setting_group(
                            "Cảnh báo thông minh (UTHelper)",
                            "Tùy chỉnh đối tượng và thời gian",
                            [
                                self._sw_ignore_sub,
                                ft.Divider(height=10, color=C.BORDER),
                                ft.Text("Các mốc nhắc nhở (giờ)", weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                                self._milestones_field,
                                ft.Divider(height=10, color=C.BORDER),
                                ft.Text("Không làm phiền (Im lặng)", weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                                self._sw_dnd_enable,
                                self._dnd_start_field,
                                self._dnd_end_field,
                                ft.Divider(height=10, color=C.BORDER),
                                ft.Text("Bỏ qua môn học", weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                                self._muted_courses_drp,
                                self._muted_courses_field,
                                ft.Divider(height=10, color=C.BORDER),
                                ft.Text("Thông báo nhắc nhở cơ bản", weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                                self._notify_min_field,
                            ]
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
                            ]
                        ),

                        _setting_group(
                            "Nâng cao",
                            "Luồng tải, Log hệ thống",
                            [
                                self._workers_field,
                                _hint("Tăng để tải chi tiết nhanh hơn. Nhỏ đi nếu bị block."),
                                self._sw_debug,
                                self._test_panel,
                            ]
                        ),

                        ft.Container(height=4),
                        save_btn,
                        self._save_status,
                        ft.Container(height=20)
                    ], spacing=10, scroll=ft.ScrollMode.AUTO),
                    padding=ft.Padding.symmetric(horizontal=20),
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
        )


    async def _handle_reset_defaults(self, e):
        self._c_tb_critical.value = "#EF4444"
        self._c_tb_warning.value = "#F59E0B"
        self._c_tb_safe.value = "#10B981"
        self._c_tb_quiz.value = "#7C3AED"
        self._c_tb_ass.value = "#2563EB"
        self._c_tb_att.value = "#D97706"
        self._c_tb_open.value = "#0891B2"
        self._c_tb_other.value = "#6B7280"
        
        self._critical_hours_field.value = "24"
        self._warning_hours_field.value = "72"
        self._opening_soon_hours_field.value = "72"
        self._interval_field.value = "60"
        self._fetch_months_field.value = "1"
        self._notify_min_field.value = "30"
        
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
        self._test_login_btn.text = "Đang kiểm tra đăng nhập..."
        self._test_loading_bar.visible = True
        self._test_login_status.value = ""
        self.update()

        try:
            success = await asyncio.to_thread(self._orchestrator.client.login, username=user, password=pwd, force=True)
            if success:
                self._test_login_status.value = "Đăng nhập thành công!"
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
            self._test_login_btn.text = "Kiểm tra đăng nhập"
            self._test_loading_bar.visible = False
            self.update()



    def _toggle_integration_ui(self):
        self._gmail_addr_field.visible = self._sw_email.value
        self._gmail_pw_field.visible = self._sw_email.value
        self._discord_wh_field.visible = self._sw_discord.value
        self._gmail_addr_field.update()
        self._gmail_pw_field.update()
        self._discord_wh_field.update()

    def _toggle_telegram_ui(self):
        v = self._sw_telegram.value
        self._tel_token_field.visible = v
        self._tel_chat_field.visible = v
        self._tel_token_field.update()
        self._tel_chat_field.update()

    def _toggle_debug_ui(self):
        self._test_panel.visible = self._sw_debug.value
        self._test_panel.update()
        
    def _do_test_tray(self):
        t = getattr(self, '_mock_type_drp', ft.Dropdown(value='critical')).value
        if self._on_test_tray: self._on_test_tray(t)

    def _do_test_tele(self):
        t = getattr(self, '_mock_type_drp', ft.Dropdown(value='critical')).value
        if hasattr(self, '_on_test_tele') and self._on_test_tele: self._on_test_tele(t)

    def _do_test_discord(self):
        t = getattr(self, '_mock_type_drp', ft.Dropdown(value='critical')).value
        if hasattr(self, '_on_test_discord') and self._on_test_discord: self._on_test_discord(t)

    def _do_test_mail(self):
        t = getattr(self, '_mock_type_drp', ft.Dropdown(value='critical')).value
        if hasattr(self, '_on_test_mail') and self._on_test_mail: self._on_test_mail(t)


    def load_current_settings(self):
        for tile in getattr(self, '_tiles', []):
            tile.expanded = False

        self._test_login_status.value = ""
        self._test_login_btn.text = "Kiểm tra đăng nhập"
        self._test_loading_bar.visible = False

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
        self._sw_always_on_top.value = settings.ALWAYS_ON_TOP
        self._sw_submitted.value = settings.INCLUDE_SUBMITTED
        self._sw_graded.value = settings.INCLUDE_GRADED
        self._sw_start_with_windows.value = settings.START_WITH_WINDOWS
        self._sw_start_minimized.value = settings.START_MINIMIZED
        self._sw_minimize_to_tray.value = settings.MINIMIZE_TO_TRAY
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
        self._fetch_months_field.value = str(settings.FETCH_MONTHS)
        self._critical_hours_field.value = str(settings.URGENCY_CRITICAL_HOURS)
        self._warning_hours_field.value = str(settings.URGENCY_WARNING_HOURS)
        self._opening_soon_hours_field.value = str(settings.OPENING_SOON_HOURS)
        self._notify_min_field.value = str(settings.NOTIFY_MINUTES_BEFORE)
        self._workers_field.value = str(settings.PREFETCH_WORKERS)
        
        self._sw_dnd_enable.value = getattr(settings, 'NOTIFY_DND_ENABLE', False)
        self._dnd_start_field.value = str(getattr(settings, 'NOTIFY_DND_START', 23))
        self._dnd_end_field.value = str(getattr(settings, 'NOTIFY_DND_END', 6))
        self._sw_ignore_sub.value = getattr(settings, 'NOTIFY_IGNORE_SUBMITTED', True)
        self._milestones_field.value = ", ".join(map(str, getattr(settings, 'NOTIFY_MILESTONES', [72, 24, 3])))
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
        if self._username_field.value != settings.UTH_USERNAME: return True
        if self._password_field.value != settings.UTH_PASSWORD: return True
        if self._sw_always_on_top.value != settings.ALWAYS_ON_TOP: return True
        if self._sw_submitted.value != settings.INCLUDE_SUBMITTED: return True
        if self._sw_graded.value != settings.INCLUDE_GRADED: return True
        if self._sw_start_with_windows.value != settings.START_WITH_WINDOWS: return True
        if self._sw_start_minimized.value != settings.START_MINIMIZED: return True
        if self._sw_minimize_to_tray.value != settings.MINIMIZE_TO_TRAY: return True
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
        if self._dnd_start_field.value != str(getattr(settings, 'NOTIFY_DND_START', 23)): return True
        if self._dnd_end_field.value != str(getattr(settings, 'NOTIFY_DND_END', 6)): return True
        if self._sw_ignore_sub.value != getattr(settings, 'NOTIFY_IGNORE_SUBMITTED', True): return True
        if self._milestones_field.value != ", ".join(map(str, getattr(settings, 'NOTIFY_MILESTONES', [72, 24, 3]))): return True
        if self._muted_courses_field.value != ", ".join(getattr(settings, 'NOTIFY_MUTED_COURSES', [])): return True
        
        return False

    async def _handle_back(self, e):
        if self.has_changes():
            def close_dlg(e):
                confirm_dlg.open = False
                try:
                    self._page.overlay.remove(confirm_dlg)
                except (ValueError, AttributeError):
                    pass
                self._page.update()
            
            def discard_and_close(e):
                confirm_dlg.open = False
                try:
                    self._page.overlay.remove(confirm_dlg)
                except (ValueError, AttributeError):
                    pass
                self._page.update()
                self._on_close_cb()

            async def save_and_close(e):
                confirm_dlg.open = False
                try:
                    self._page.overlay.remove(confirm_dlg)
                except (ValueError, AttributeError):
                    pass
                self._page.update()
                await self._save(e)
                self._on_close_cb()

            confirm_dlg = ft.AlertDialog(
                title=ft.Text("Chưa lưu cài đặt", size=16, weight=ft.FontWeight.BOLD),
                content=ft.Text("Bạn có thay đổi chưa lưu. Bạn muốn lưu lại không?", size=13),
                actions=[
                    ft.TextButton("Hủy", on_click=close_dlg),
                    ft.TextButton("Bỏ qua", on_click=discard_and_close),
                    ft.TextButton("Lưu", on_click=save_and_close),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            self._page.overlay.append(confirm_dlg)
            confirm_dlg.open = True
            self._page.update()
        else:
            self._on_close_cb()

    async def _save(self, e):
        try:
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
            settings.ALWAYS_ON_TOP           = self._sw_always_on_top.value
            settings.INCLUDE_SUBMITTED       = self._sw_submitted.value
            settings.INCLUDE_GRADED          = self._sw_graded.value
            settings.CHECK_INTERVAL_MINUTES  = max(0, int(self._interval_field.value or "60"))
            settings.FETCH_MONTHS            = max(1, min(int(self._fetch_months_field.value or "1"), 3))
            settings.URGENCY_CRITICAL_HOURS  = max(1, int(self._critical_hours_field.value or "24"))
            settings.URGENCY_WARNING_HOURS   = max(1, int(self._warning_hours_field.value or "72"))
            settings.OPENING_SOON_HOURS      = max(1, int(self._opening_soon_hours_field.value or "72"))
            settings.NOTIFY_MINUTES_BEFORE   = max(0, int(self._notify_min_field.value or "30"))
            workers = int(self._workers_field.value or "4")
            settings.PREFETCH_WORKERS        = max(1, min(workers, 10))
            self._workers_field.value        = str(settings.PREFETCH_WORKERS)
            
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

            settings.ENABLE_GMAIL            = self._sw_email.value
            settings.ENABLE_DISCORD          = self._sw_discord.value
            settings.NOTIFY_DND_ENABLE       = self._sw_dnd_enable.value
            settings.NOTIFY_DND_START        = max(0, min(23, int(self._dnd_start_field.value or "23")))
            settings.NOTIFY_DND_END          = max(0, min(23, int(self._dnd_end_field.value or "6")))
            settings.NOTIFY_IGNORE_SUBMITTED = self._sw_ignore_sub.value
            
            try:
                settings.NOTIFY_MILESTONES = [int(x.strip()) for x in self._milestones_field.value.split(",") if x.strip()]
            except ValueError:
                settings.NOTIFY_MILESTONES = [72, 24, 3]
            
            settings.NOTIFY_MUTED_COURSES = [x.strip() for x in self._muted_courses_field.value.split(",") if x.strip()]

            save_settings()

            self._save_status.value   = "Đã lưu cài đặt. Cần khởi động lại ứng dụng nếu đổi tài khoản!"
            self._save_status.color   = C.SAFE
            
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
