import re

with open("src/gui/components/settings_view.py", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Add "Test Login" UI inside __init__
test_login_ui = """
        self._test_login_btn = ft.ElevatedButton(
            "Kiểm tra đăng nhập", 
            on_click=self._handle_test_login,
            bgcolor=C.ACCENT,
            color=ft.Colors.WHITE,
            height=40,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10))
        )
        self._test_login_status = ft.Text("", size=12)

        self._sw_always_on_top = ft.Switch(
"""
text = text.replace("        self._sw_always_on_top = ft.Switch(", test_login_ui)

# 2. Add email switcher for future
email_ui = """
        self._sw_email = ft.Switch(
            value=False, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bật gửi qua Gmail (Future)"
        )
"""
text = text.replace("        self._sw_discord = ft.Switch(", email_ui + "        self._sw_discord = ft.Switch(")

# 3. Add on_submit for username to focus password
text = text.replace("""            bgcolor=C.BG, border_radius=10,
        )
        self._password_field""", """            bgcolor=C.BG, border_radius=10,
            on_submit=lambda e: self._password_field.focus()
        )
        self._password_field""")

# 4. Add self._tiles = [] and add tile to it inside _setting_group
tile_group_logic = """
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
"""
# Need to capture the original _setting_group function definition to replace it
# We'll use regex
text = re.sub(
    r"        def _setting_group\(title, subtitle, controls, default_open=False\):(.*?)clip_behavior=ft\.ClipBehavior\.HARD_EDGE\n            \)",
    tile_group_logic.strip() + """
                bgcolor=C.SURFACE,
                border_radius=10,
                border=ft.border.all(1, C.BORDER),
                padding=0,
                margin=ft.margin.only(bottom=3),
                clip_behavior=ft.ClipBehavior.HARD_EDGE
            )""",
    text, flags=re.DOTALL
)

# 5. Overhaul the settings groups UI definition
groups_original = """                        _setting_group(
                            "1. Tài khoản UTH",
                            "Thông tin đăng nhập hệ thống elearning",
                            [self._username_field, self._password_field],
                            default_open=True
                        ),"""

groups_new = """
                        _setting_group(
                            "Tài khoản UTH",
                            "Thông tin đăng nhập hệ thống elearning",
                            [self._username_field, self._password_field, self._test_login_btn, self._test_login_status],
                            default_open=True
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
                                _hint("Đặt 0 để tắt tự động cập nhật. Mặc định: 60 phút.")
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
                            "Thông báo",
                            "Thời gian làm mới và chuông báo",
                            [
                                self._notify_min_field,
                            ]
                        ),

                        _setting_group(
                            "Tích hợp",
                            "Nhắn tin qua Bot & Email",
                            [
                                self._sw_email,
                                self._sw_discord,
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
"""
# Replace the big ft.Column controls array content.
text = re.sub(
    r"                        _setting_group\(\n                            \"1\. Tài khoản UTH\".*?self\._test_panel,\n                            \]\n                        \),",
    groups_new.strip(),
    text, flags=re.DOTALL
)

# 6. Add handle_test_login function
func_test_login = """
    async def _handle_test_login(self, e):
        user = self._username_field.value.strip()
        pwd = self._password_field.value.strip()
        if not user or not pwd:
            self._test_login_status.value = "Vui lòng nhập đủ MSSV và Mật khẩu!"
            self._test_login_status.color = C.CRITICAL
            self.update()
            return

        self._test_login_btn.disabled = True
        self._test_login_status.value = "Đang kiểm tra đăng nhập..."
        self._test_login_status.color = C.TEXT_SECONDARY
        self.update()

        try:
            from core.client import MoodleClient
            client = MoodleClient()
            success = client.login(username=user, password=pwd, force=True)
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
            self.update()

    def _update_safe_label(self):
"""
text = text.replace("    def _update_safe_label(self):", func_test_login)

# 7. Collapse tiles when loading
load_settings = """    def load_current_settings(self):
        for tile in getattr(self, '_tiles', []):
            tile.expanded = False

        self._test_login_status.value = ""
"""
text = text.replace("    def load_current_settings(self):", load_settings)

# Remove old safe label occurrences logically
text = re.sub(r"\n\s+self\._safe_hours_label = ft\.Text\(.*?\)", "", text, flags=re.DOTALL)
text = re.sub(r"\n\s+self\._update_safe_label\(\)", "", text, flags=re.DOTALL)
text = re.sub(r"\n\s*on_change=lambda e: self\._update_safe_label\(\)", "", text, flags=re.DOTALL)
text = re.sub(r"    def _update_safe_label\(self\):.*?self\._safe_hours_label\.update\(\)", "", text, flags=re.DOTALL)


with open("src/gui/components/settings_view.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated successfully!")
