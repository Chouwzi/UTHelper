import flet as ft
from gui.core.theme import C
from config import settings
from config import save_settings

class SettingsView(ft.Container):
    def __init__(self, page: ft.Page, on_close, on_saved=None, on_test_tray=None):
        super().__init__()
        self._page    = page
        self._on_close_cb = on_close
        self._on_saved = on_saved
        self._on_test_tray = on_test_tray
        self.visible  = False
        self.expand   = True
        self.bgcolor  = C.BG

        self._username_field = ft.TextField(
            value=settings.UTH_USERNAME,
            label="MSSV",
            text_size=14, label_style=ft.TextStyle(size=13, color=C.TEXT_SECONDARY),
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY,
            bgcolor=C.BG, border_radius=10,
        )
        self._password_field = ft.TextField(
            label="Mật khẩu",
            value=settings.UTH_PASSWORD,
            text_size=14, label_style=ft.TextStyle(size=13, color=C.TEXT_SECONDARY),
            password=True, can_reveal_password=True,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, 
            bgcolor=C.BG, border_radius=10,
        )

        self._sw_always_on_top = ft.Switch(
            value=settings.ALWAYS_ON_TOP, active_color=C.ACCENT,
            label="Luôn hiển thị trên cùng",
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
        self._sw_start_minimized = ft.Switch(
            value=settings.START_MINIMIZED, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Khởi động thu nhỏ (Ngầm)"
        )
        self._sw_minimize_to_tray = ft.Switch(
            value=settings.MINIMIZE_TO_TRAY, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Thu nhỏ xuống System Tray"
        )

        self._sw_discord = ft.Switch(
            value=settings.ENABLE_DISCORD, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bật gửi qua Discord (Future)"
        )

        self._sw_telegram = ft.Switch(
            value=settings.ENABLE_TELEGRAM, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bật gửi qua Telegram Bot",
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
        
        self._test_panel = ft.Container(
            content=ft.Column([
                ft.Text("Công cụ Debug", color=C.CRITICAL, weight=ft.FontWeight.BOLD),
                ft.ElevatedButton("Test Thông báo qua System Tray", on_click=lambda e: self._do_test_tray(), bgcolor=C.BG, color=C.TEXT_PRIMARY)
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
            on_change=lambda e: self._update_safe_label()
        )
        self._opening_soon_hours_field = ft.TextField(
            value=str(settings.OPENING_SOON_HOURS),
            label="Sắp mở khi dưới (Giờ)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY,
            bgcolor=C.BG, border_radius=10,
        )
        
        self._safe_hours_label = ft.Text(
            value=f"An toàn khi trên {settings.URGENCY_WARNING_HOURS} giờ",
            color=C.SAFE, size=13, weight=ft.FontWeight.W_500
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
        self._save_status = ft.Text("", size=12, color=C.SAFE)

        back_btn = ft.TextButton(
            content=ft.Row(controls=[
                ft.Icon(ft.Icons.ARROW_BACK, size=14, color=C.TEXT_SECONDARY),
                ft.Text("Quay lại", size=13, color=C.TEXT_SECONDARY),
            ], spacing=4, tight=True),
            on_click=self._handle_back,
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
        
        def _setting_group(title, subtitle, controls, default_open=False):
            return ft.Container(
                content=ft.ExpansionTile(
                    title=ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                    subtitle=ft.Text(subtitle, size=12, color=C.TEXT_SECONDARY),
                    initially_expanded=default_open,
                    controls=[
                        ft.Container(content=ft.Column(controls), padding=10)
                    ],
                    collapsed_text_color=C.TEXT_PRIMARY,
                    text_color=C.ACCENT,
                ),
                bgcolor=C.SURFACE,
                border_radius=10,
                border=ft.border.all(1, C.BORDER),
                padding=0,
                margin=ft.margin.only(bottom=5),
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
                            "1. Tài khoản UTH",
                            "Thông tin đăng nhập hệ thống elearning",
                            [self._username_field, self._password_field],
                            default_open=True
                        ),

                        _setting_group(
                            "2. Hiển thị & Giao diện",
                            "Thiết lập cách hiển thị trên màn hình",
                            [self._sw_submitted, self._sw_graded, self._sw_always_on_top]
                        ),

                        _setting_group(
                            "3. Hệ thống & Khởi động",
                            "Thiết lập tự khởi động và thu nhỏ",
                            [self._sw_start_with_windows, self._sw_start_minimized, self._sw_minimize_to_tray]
                        ),

                        _setting_group(
                            "4. Cảnh báo & Thời gian",
                            "Ngưỡng thời gian màu sắc tuỳ chỉnh",
                            [
                                self._critical_hours_field,
                                self._warning_hours_field,
                                ft.Container(content=self._safe_hours_label, padding=ft.Padding(left=10, top=0, right=0, bottom=0)),
                                _hint("Mức độ An toàn tự động được tính khi thời gian còn lại lớn hơn thời gian cấu hình Sắp tới."),
                                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                                self._opening_soon_hours_field,
                                _hint("Hoạt động sẽ được đánh dấu là Sắp mở nếu còn cấu hình thời gian mở nhỏ hơn (Giờ)"),
                            ]
                        ),

                        _setting_group(
                            "5. Tự động & Thông báo",
                            "Thời gian làm mới và chuông báo",
                            [
                                self._interval_field,
                                _hint("Đặt 0 để tắt tự động cập nhật. Mặc định: 60 phút."),
                                self._notify_min_field,
                            ]
                        ),

                        _setting_group(
                            "6. Tích hợp Bot",
                            "Telegram, Discord",
                            [
                                self._sw_discord,
                                self._sw_telegram,
                                self._tel_token_field,
                                self._tel_chat_field,
                            ]
                        ),

                        _setting_group(
                            "7. Nâng cao & Gỡ lỗi",
                            "Luồng tải, Log hệ thống",
                            [
                                self._workers_field,
                                _hint("Tăng để tải chi tiết hoạt động nhanh hơn. Cao quá có thể bị chặn."),
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

    def _update_safe_label(self):
        val = self._warning_hours_field.value
        if val and val.isdigit():
            self._safe_hours_label.value = f"An toàn khi trên {val} giờ"
            self._safe_hours_label.update()

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
        if self._on_test_tray:
            self._on_test_tray()

    def load_current_settings(self):
        self._username_field.value = settings.UTH_USERNAME
        self._password_field.value = settings.UTH_PASSWORD
        self._sw_always_on_top.value = settings.ALWAYS_ON_TOP
        self._sw_submitted.value = settings.INCLUDE_SUBMITTED
        self._sw_graded.value = settings.INCLUDE_GRADED
        self._sw_start_with_windows.value = settings.START_WITH_WINDOWS
        self._sw_start_minimized.value = settings.START_MINIMIZED
        self._sw_minimize_to_tray.value = settings.MINIMIZE_TO_TRAY
        self._sw_discord.value = settings.ENABLE_DISCORD
        self._sw_telegram.value = settings.ENABLE_TELEGRAM
        self._tel_token_field.value = settings.TELEGRAM_BOT_TOKEN
        self._tel_chat_field.value = settings.TELEGRAM_CHAT_ID
        self._toggle_telegram_ui()
        self._interval_field.value = str(settings.CHECK_INTERVAL_MINUTES)
        self._critical_hours_field.value = str(settings.URGENCY_CRITICAL_HOURS)
        self._warning_hours_field.value = str(settings.URGENCY_WARNING_HOURS)
        self._opening_soon_hours_field.value = str(settings.OPENING_SOON_HOURS)
        self._update_safe_label()
        self._notify_min_field.value = str(settings.NOTIFY_MINUTES_BEFORE)
        self._workers_field.value = str(settings.PREFETCH_WORKERS)
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
        if self._sw_discord.value != settings.ENABLE_DISCORD: return True
        if self._sw_telegram.value != settings.ENABLE_TELEGRAM: return True
        if self._tel_token_field.value != settings.TELEGRAM_BOT_TOKEN: return True
        if self._tel_chat_field.value != settings.TELEGRAM_CHAT_ID: return True
        if self._sw_debug.value != settings.DEBUG_MODE: return True
        if self._interval_field.value != str(settings.CHECK_INTERVAL_MINUTES): return True
        if self._critical_hours_field.value != str(settings.URGENCY_CRITICAL_HOURS): return True
        if self._warning_hours_field.value != str(settings.URGENCY_WARNING_HOURS): return True
        if self._opening_soon_hours_field.value != str(settings.OPENING_SOON_HOURS): return True
        if self._notify_min_field.value != str(settings.NOTIFY_MINUTES_BEFORE): return True
        if self._workers_field.value != str(settings.PREFETCH_WORKERS): return True
        return False

    async def _handle_back(self, e):
        if self.has_changes():
            def close_dlg(e):
                confirm_dlg.open = False
                self._page.update()
            
            def discard_and_close(e):
                confirm_dlg.open = False
                self._page.update()
                self._on_close_cb()

            async def save_and_close(e):
                confirm_dlg.open = False
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
            settings.UTH_USERNAME            = self._username_field.value
            settings.UTH_PASSWORD            = self._password_field.value
            settings.ALWAYS_ON_TOP           = self._sw_always_on_top.value
            settings.INCLUDE_SUBMITTED       = self._sw_submitted.value
            settings.INCLUDE_GRADED          = self._sw_graded.value
            settings.CHECK_INTERVAL_MINUTES  = max(0, int(self._interval_field.value or "60"))
            settings.URGENCY_CRITICAL_HOURS  = max(1, int(self._critical_hours_field.value or "24"))
            settings.URGENCY_WARNING_HOURS   = max(1, int(self._warning_hours_field.value or "72"))
            settings.OPENING_SOON_HOURS      = max(1, int(self._opening_soon_hours_field.value or "72"))
            settings.NOTIFY_MINUTES_BEFORE   = max(0, int(self._notify_min_field.value or "30"))
            workers = int(self._workers_field.value or "4")
            settings.PREFETCH_WORKERS        = max(1, min(workers, 10))
            self._workers_field.value        = str(settings.PREFETCH_WORKERS)
            
            if settings.START_WITH_WINDOWS != self._sw_start_with_windows.value:
                try:
                    import src.core.autostart as autostart
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
            
            settings.ENABLE_DISCORD          = self._sw_discord.value
            settings.ENABLE_TELEGRAM         = self._sw_telegram.value
            settings.TELEGRAM_BOT_TOKEN      = self._tel_token_field.value
            settings.TELEGRAM_CHAT_ID        = self._tel_chat_field.value
            settings.DEBUG_MODE              = self._sw_debug.value

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