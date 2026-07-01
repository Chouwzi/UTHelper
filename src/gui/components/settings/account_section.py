import flet as ft
from gui.core.theme import C
from config import settings

def init_account_controls(view):
    view._username_field = ft.TextField(
        value=settings.UTH_USERNAME,
        label="Mã số sinh viên (MSSV)",
        text_size=14, label_style=ft.TextStyle(size=13, color=C.TEXT_SECONDARY),
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
        on_submit=lambda e: view._password_field.focus()
    )
    view._password_field = ft.TextField(
        label="Mật khẩu",
        value=settings.UTH_PASSWORD,
        text_size=14, label_style=ft.TextStyle(size=13, color=C.TEXT_SECONDARY),
        password=True, can_reveal_password=True,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
        on_submit=view._handle_test_login
    )
    view._test_login_btn = ft.Button(
        "Kiểm tra kết nối",
        icon=ft.Icons.WIFI_FIND_ROUNDED,
        on_click=view._handle_test_login,
        style=ft.ButtonStyle(
            color=ft.Colors.WHITE,
            bgcolor=C.ACCENT,
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=12,
            animation_duration=300
        ),
        height=44
    )
    view._test_loading_bar = ft.ProgressBar(color=C.ACCENT, bgcolor=C.SURFACE, visible=False)
    view._test_login_status = ft.Text("", size=12, text_align=ft.TextAlign.CENTER)

def build_account_section(view) -> ft.Container:
    return view._build_setting_group(
        "Tài khoản UTH",
        "Thông tin đăng nhập hệ thống elearning",
        [
            view._username_field,
            view._password_field,
            view._test_loading_bar,
            view._test_login_status,
            view._test_login_btn
        ],
        icon=ft.Icons.PERSON_OUTLINE_ROUNDED,
    )
