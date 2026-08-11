import flet as ft
from gui.core.theme import C
from config import settings

def init_urgency_controls(view):
    """Khởi tạo các control cấu hình ngưỡng thời gian cảnh báo của hoạt động."""
    # Khung nhập ngưỡng giờ để gắn nhãn "Cấp bách" (màu đỏ)
    view._critical_hours_field = ft.TextField(
        value=str(settings.URGENCY_CRITICAL_HOURS),
        label="Cấp bách khi dưới (Giờ)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
    )
    # Khung nhập ngưỡng giờ để gắn nhãn "Sắp tới" (màu cam)
    view._warning_hours_field = ft.TextField(
        value=str(settings.URGENCY_WARNING_HOURS),
        label="Sắp tới khi dưới (Giờ)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
    )
    # Khung nhập ngưỡng giờ để xác định trạng thái bài tập "Sắp mở"
    view._opening_soon_hours_field = ft.TextField(
        value=str(settings.OPENING_SOON_HOURS),
        label="Sắp mở khi dưới (Giờ)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
    )

def build_urgency_section(view) -> ft.Container:
    """Xây dựng Container cấu hình ngưỡng thời gian mức độ cảnh báo."""
    return view._build_setting_group(
        "Cảnh báo",
        "Mức độ cảnh báo theo thời gian",
        [
            view._make_themed_label("Mức độ"),
            view._critical_hours_field,
            view._warning_hours_field,
            ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
            view._make_themed_label("Trạng thái"),
            view._opening_soon_hours_field,
            view._build_hint("Hoạt động sẽ được đánh dấu 'Sắp mở' khi thời gian mở nhỏ hơn mức này.")
        ],
        icon=ft.Icons.NOTIFICATIONS_ACTIVE_OUTLINED,
    )
