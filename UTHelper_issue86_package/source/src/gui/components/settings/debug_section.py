import flet as ft
from gui.core.theme import C
from config import settings

def init_debug_controls(view):
    """Khởi tạo các control cấu hình nâng cao và công cụ gỡ lỗi (debug controls)."""
    def _clamp_workers(val: int) -> int:
        return max(1, min(10, val))

    def _on_workers_change(e=None):
        try:
            val = int(view._workers_field.value)
            if val < 1:
                view._workers_field.value = "1"
            elif val > 10:
                view._workers_field.value = "10"
        except (ValueError, TypeError):
            view._workers_field.value = "4"
        if hasattr(view, "_on_form_field_change"):
            view._on_form_field_change()

    def _decrement_workers(e):
        try:
            val = int(view._workers_field.value or "4")
        except (ValueError, TypeError):
            val = 4
        view._workers_field.value = str(_clamp_workers(val - 1))
        _on_workers_change()
        try:
            view._workers_field.update()
        except Exception:
            pass

    def _increment_workers(e):
        try:
            val = int(view._workers_field.value or "4")
        except (ValueError, TypeError):
            val = 4
        view._workers_field.value = str(_clamp_workers(val + 1))
        _on_workers_change()
        try:
            view._workers_field.update()
        except Exception:
            pass

    # Trường cấu hình số lượng worker prefetch song song
    view._workers_field = ft.TextField(
        value=str(settings.PREFETCH_WORKERS),
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.NumbersOnlyInputFilter(),
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
        text_align=ft.TextAlign.CENTER,
        width=70,
        on_change=_on_workers_change,
    )

    view._workers_stepper_row = ft.Row(
        controls=[
            ft.IconButton(
                icon=ft.Icons.REMOVE_ROUNDED,
                icon_color=C.TEXT_PRIMARY,
                on_click=_decrement_workers,
                tooltip="Giảm số luồng",
            ),
            view._workers_field,
            ft.IconButton(
                icon=ft.Icons.ADD_ROUNDED,
                icon_color=C.TEXT_PRIMARY,
                on_click=_increment_workers,
                tooltip="Tăng số luồng",
            ),
            ft.Text("luồng đồng thời (1-10)", size=13, color=C.TEXT_SECONDARY),
        ],
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # Switch bật/tắt ghi log debug chi tiết
    view._sw_debug = ft.Switch(
        value=settings.DEBUG_MODE, active_color=C.CRITICAL,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Chế độ Gỡ lỗi (Debug Log)",
        on_change=lambda e: (view._toggle_debug_ui(), getattr(view, "_on_form_field_change", lambda: None)())
    )
    # Dropdown lựa chọn kiểu dữ liệu giả lập (mock data) phục vụ kiểm thử thông báo
    view._mock_type_drp = ft.Dropdown(
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
    # Khởi tạo panel chứa các nút hành động debug nâng cao
    view._init_debug_panel()

def build_debug_section(view) -> ft.Control:
    """Xây dựng nội dung chi tiết cấu hình nâng cao và công cụ debug."""
    return ft.Column(
        controls=[
            view._build_hint("Các tùy chọn này thường không cần thay đổi."),
            ft.Divider(height=8, color=ft.Colors.TRANSPARENT),
            view._make_themed_label("Hiệu năng tải dữ liệu"),
            view._workers_stepper_row,
            view._build_hint("Tăng để tải chi tiết nhanh hơn. Giảm đi nếu kết nối mạng không ổn định."),
            ft.Divider(height=16, color=C.BORDER),
            view._make_themed_label("Gỡ lỗi"),
            view._sw_debug,
            view._build_hint("Chỉ bật khi cần thu thập thông tin để chẩn đoán lỗi."),
            view._test_panel,
        ],
        spacing=10,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
