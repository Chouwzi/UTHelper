import re
import sys

def main():
    try:
        # 1. AppController
        with open('src/gui/app_controller.py', 'r', encoding='utf-8') as f:
            app_content = f.read()

        new_test_base = """
    def _test_notification_base(self, mock_type="critical"):
        import random, datetime
        from models import Assignment
        
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
            title = 'NHẮC NHỞ ĐIỂM DANH (Sắp đóng)'
            event_type = 'attendance'
            course_name = 'Trí tuệ Nhân tạo'
            
        return Assignment(
            id=str(random.randint(10000, 99999)),
            title=title,
            event_type=event_type,
            course_id='0',
            course_name=course_name,
            deadline=datetime.datetime.now() + delta,
            url='https://github.com/microsoft/vscode-copilot',
            submission_status='not_submitted'
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
"""
        app_content = re.sub(r'    def _test_notification_base\(self\):.*?def _test_notification_base\(self\):.*?EmailNotifier\(\)\.notify\(\[dummy\]\)', new_test_base, app_content, flags=re.DOTALL)
        
        # Another approach since my regex for app_controller is risky
        app_content = re.sub(
            r'    def _test_notification_base\(self\):.*?(?=    def _on_settings_saved)',
            new_test_base + '\n',
            app_content,
            flags=re.DOTALL
        )

        with open('src/gui/app_controller.py', 'w', encoding='utf-8') as f:
            f.write(app_content)

        # 2. SettingsView
        with open('src/gui/components/settings_view.py', 'r', encoding='utf-8') as f:
            sv_content = f.read()

        sv_test_panel = """
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
                ft.Text("Công cụ Debug - Mock Test", color=C.CRITICAL, weight=ft.FontWeight.BOLD),
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
        )"""

        sv_content = re.sub(
            r'self\._test_panel = ft\.Container\(\s+content=ft\.Column\(\[\s+ft\.Text\("Công cụ Debug.*?margin=ft\.margin\.only\(top=10\)\s+\)',
            sv_test_panel.strip(),
            sv_content,
            flags=re.DOTALL
        )
        
        sv_callbacks = """
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
"""
        sv_content = re.sub(
            r'    def _do_test_tray\(self\):.*?(?=\n\s+def load_current_settings)',
            sv_callbacks.strip() + '\n',
            sv_content,
            flags=re.DOTALL
        )

        with open('src/gui/components/settings_view.py', 'w', encoding='utf-8') as f:
            f.write(sv_content)

        print("Patch successful.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
