import flet as ft
from gui.core.theme import C
from config import settings

def init_urgency_controls(view):
    """Khởi tạo các control cấu hình ngưỡng thời gian cảnh báo của hoạt động."""
    def _on_urgency_change(e=None):
        validate_urgency_thresholds(view)
        if hasattr(view, "_on_form_field_change"):
            view._on_form_field_change()
        try:
            view._urgency_error_text.update()
        except Exception:
            pass

    # Khung nhập ngưỡng giờ để gắn nhãn "Cấp bách" (màu đỏ)
    view._critical_hours_field = ft.TextField(
        value=str(settings.URGENCY_CRITICAL_HOURS),
        label="Cấp bách khi còn dưới",
        suffix=ft.Text("giờ", size=12, color=C.TEXT_SECONDARY),
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.NumbersOnlyInputFilter(),
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
        on_change=_on_urgency_change,
    )
    # Khung nhập ngưỡng giờ để gắn nhãn "Sắp tới" (màu cam)
    view._warning_hours_field = ft.TextField(
        value=str(settings.URGENCY_WARNING_HOURS),
        label="Sắp tới khi còn dưới",
        suffix=ft.Text("giờ", size=12, color=C.TEXT_SECONDARY),
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.NumbersOnlyInputFilter(),
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
        on_change=_on_urgency_change,
    )
    # Inline validation error text
    view._urgency_error_text = ft.Text(
        "Ngưỡng \"Sắp tới\" phải lớn hơn ngưỡng \"Cấp bách\".",
        size=12,
        color=C.CRITICAL,
        visible=False,
    )
    # Khung nhập ngưỡng giờ để xác định trạng thái bài tập "Sắp mở"
    view._opening_soon_hours_field = ft.TextField(
        value=str(settings.OPENING_SOON_HOURS),
        label="Sắp mở khi còn dưới",
        suffix=ft.Text("giờ", size=12, color=C.TEXT_SECONDARY),
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.NumbersOnlyInputFilter(),
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
        on_change=lambda e: getattr(view, "_on_form_field_change", lambda: None)(),
    )

def validate_urgency_thresholds(view) -> bool:
    """Kiểm tra tính hợp lệ giữa ngưỡng cấp bách và sắp tới."""
    try:
        crit_val = getattr(getattr(view, "_critical_hours_field", None), "value", None) or "24"
        warn_val = getattr(getattr(view, "_warning_hours_field", None), "value", None) or "72"
        crit = int(crit_val)
        warn = int(warn_val)
        if crit >= warn:
            if hasattr(view, "_urgency_error_text"):
                view._urgency_error_text.visible = True
            return False
        else:
            if hasattr(view, "_urgency_error_text"):
                view._urgency_error_text.visible = False
            return True
    except (ValueError, TypeError):
        if hasattr(view, "_urgency_error_text"):
            view._urgency_error_text.visible = True
        return False

def build_urgency_section(view) -> ft.Control:
    """Xây dựng nội dung chi tiết cấu hình ngưỡng thời gian cảnh báo."""
    return ft.Column(
        controls=[
            view._make_themed_label("Mức độ khẩn cấp"),
            view._build_hint("Đặt ngưỡng thời gian để phân loại mức độ khẩn cấp của bài tập."),
            view._critical_hours_field,
            view._warning_hours_field,
            view._urgency_error_text,
            ft.Divider(height=16, color=C.BORDER),
            view._make_themed_label("Bài tập sắp mở"),
            view._opening_soon_hours_field,
            view._build_hint("Hoạt động sẽ được gắn nhãn 'Sắp mở' khi thời gian mở nhỏ hơn mức này."),
        ],
        spacing=10,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
