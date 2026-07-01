import flet as ft
from gui.core.theme import C
from config import settings
import platform_utils as _pu

def init_system_controls(view):
    view._sw_start_with_windows = ft.Switch(
        value=settings.START_WITH_WINDOWS, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Khởi động cùng Windows"
    )
    view._sw_start_minimized = ft.Switch(
        value=settings.START_MINIMIZED, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Khởi động ở chế độ thu nhỏ"
    )
    view._sw_minimize_to_tray = ft.Switch(
        value=settings.MINIMIZE_TO_TRAY, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Thu nhỏ vào khay hệ thống"
    )
    view._sw_bg_check = ft.Switch(
        value=settings.BACKGROUND_CHECK_ANDROID, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Kiểm tra deadline khi thu nhỏ (Android)",
        on_change=lambda e: view._toggle_bg_check_ui()
    )
    view._bg_interval_field = ft.TextField(
        value=str(settings.BACKGROUND_CHECK_INTERVAL),
        label="Tần suất kiểm tra nền (phút)",
        text_size=13,
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        width=200,
        visible=settings.BACKGROUND_CHECK_ANDROID,
    )
    view._interval_field = ft.TextField(
        value=str(settings.CHECK_INTERVAL_MINUTES),
        label="Cập nhật mỗi X phút (0 để tắt)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
    )
    view._dd_poll_interval = ft.Dropdown(
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
    view._fetch_months_field = ft.TextField(
        value=str(settings.FETCH_MONTHS),
        label="Số tháng lấy sự kiện (1-3)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
    )

def build_system_section(view) -> ft.Container:
    if not _pu.IS_MOBILE:
        controls = [
            view._sw_start_with_windows, view._sw_start_minimized, view._sw_minimize_to_tray,
            ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
            view._interval_field,
            view._build_hint("Đặt 0 để tắt tự động cập nhật. Mặc định: 60 phút."),
            view._dd_poll_interval,
            view._build_hint("Tần suất kiểm tra tự động dữ liệu mới."),
            view._fetch_months_field,
            view._build_hint("Số tháng cần lấy sự kiện (1-3). (Mặc định 1)")
        ]
        title = "Hệ thống"
        subtitle = "Khởi động và tự động cập nhật"
    else:
        controls = [
            view._interval_field,
            view._build_hint("Đặt 0 để tắt tự động cập nhật. Mặc định: 60 phút."),
            view._dd_poll_interval,
            view._build_hint("Tần suất kiểm tra tự động dữ liệu mới."),
            view._fetch_months_field,
            view._build_hint("Số tháng cần lấy sự kiện (1-3). (Mặc định 1)"),
            ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
            view._sw_bg_check,
            view._bg_interval_field,
            view._build_hint("Kiểm tra deadline nền qua AlarmManager (tối thiểu 5 phút). Dưới 15 phút có thể bị delay bởi chế độ tiết kiệm pin."),
        ]
        title = "Cập nhật"
        subtitle = "Tần suất kiểm tra"

    return view._build_setting_group(
        title,
        subtitle,
        controls,
        icon=ft.Icons.SETTINGS_OUTLINED,
    )
