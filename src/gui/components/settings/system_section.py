import flet as ft
from gui.core.theme import C
from config import settings
import platform_utils as _pu

def init_system_controls(view):
    """Khởi tạo các control cấu hình vòng đời ứng dụng và tần suất đồng bộ trên hệ điều hành."""
    # Thiết lập riêng trên Desktop (Windows)
    view._sw_start_with_windows = ft.Switch(
        value=settings.START_WITH_WINDOWS, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Khởi động cùng Windows",
        on_change=view._on_autostart_toggle,
    )
    view._sw_start_minimized = ft.Switch(
        value=settings.START_MINIMIZED, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Khi khởi động cùng Windows: Ẩn xuống khay hệ thống",
        disabled=not settings.START_WITH_WINDOWS,
    )
    view._autostart_status = ft.Text("", size=11, color=C.TEXT_SECONDARY)
    view._sw_minimize_to_tray = ft.Switch(
        value=settings.MINIMIZE_TO_TRAY, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Thu nhỏ vào khay hệ thống"
    )
    
    # Thiết lập riêng trên Mobile (Android)
    view._sw_bg_check = ft.Switch(
        value=settings.BACKGROUND_CHECK_ANDROID, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Đồng bộ hoạt động nền (Android, best-effort)",
        on_change=lambda e: view._toggle_bg_check_ui()
    )
    
    # Đồng bộ hóa dữ liệu Moodle chung
    interval_options = [
        ft.dropdown.Option("0", "Tắt tự động"),
        ft.dropdown.Option("60", "Mỗi 1 giờ (mặc định)"),
        ft.dropdown.Option("180", "Mỗi 3 giờ"),
        ft.dropdown.Option("360", "Mỗi 6 giờ"),
    ]
    current_interval = str(settings.CHECK_INTERVAL_MINUTES)
    if current_interval not in {"0", "60", "180", "360"}:
        interval_options.append(
            ft.dropdown.Option(current_interval, f"Tùy chỉnh cũ: {current_interval} phút")
        )
    view._interval_field = ft.Dropdown(
        value=str(settings.CHECK_INTERVAL_MINUTES),
        label="Tần suất đồng bộ hoạt động",
        options=interval_options,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
    )
    # Cấu hình số lượng tháng lịch cần quét sự kiện
    view._fetch_months_field = ft.TextField(
        value=str(settings.FETCH_MONTHS),
        label="Số tháng lấy sự kiện (1-3)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
    )

def build_system_section(view) -> ft.Container:
    """Xây dựng Container nhóm cấu hình hệ thống phù hợp với từng nền tảng Desktop/Mobile."""
    if not _pu.IS_MOBILE:
        controls = [
            view._sw_start_with_windows,
            view._sw_start_minimized,
            view._autostart_status,
            view._sw_minimize_to_tray,
            ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
            view._interval_field,
            view._build_hint("Dùng chung cho foreground, Windows tray và Android background."),
            view._fetch_months_field,
            view._build_hint("Số tháng cần lấy sự kiện (1-3). (Mặc định 1)")
        ]
        title = "Hệ thống"
        subtitle = "Khởi động và tự động cập nhật"
    else:
        controls = [
            view._interval_field,
            view._build_hint("Dùng cùng một chu kỳ khi app mở hoặc chạy nền."),
            view._fetch_months_field,
            view._build_hint("Số tháng cần lấy sự kiện (1-3). (Mặc định 1)"),
            ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
            view._sw_bg_check,
            view._build_hint("Android dùng chu kỳ đồng bộ ở trên khi worker native khả dụng."),
        ]
        title = "Cập nhật"
        subtitle = "Tần suất kiểm tra"

    return view._build_setting_group(
        title,
        subtitle,
        controls,
        icon=ft.Icons.SETTINGS_OUTLINED,
    )
