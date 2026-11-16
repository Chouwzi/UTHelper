import flet as ft
from gui.core.theme import C
from config import settings
import platform_utils as _pu

def init_display_controls(view):
    """Khởi tạo các switch tùy chỉnh cách hiển thị bài tập trên danh sách."""
    # Switch thiết lập ghim cửa sổ ứng dụng ở trên cùng (chỉ khả dụng trên Desktop)
    view._sw_always_on_top = ft.Switch(
        value=settings.ALWAYS_ON_TOP, active_color=C.ACCENT,
        label="Luôn ở trên cùng",
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        on_change=lambda e: getattr(view, "_on_form_field_change", lambda: None)(),
    )
    # Switch lọc hiển thị các hoạt động/bài tập đã hoàn tất nộp bài
    view._sw_submitted = ft.Switch(
        value=settings.INCLUDE_SUBMITTED, active_color=C.ACCENT,
        label="Hiển thị bài đã nộp",
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        on_change=lambda e: getattr(view, "_on_form_field_change", lambda: None)(),
    )
    # Switch lọc hiển thị các bài tập đã có điểm chấm từ giảng viên
    view._sw_graded = ft.Switch(
        value=settings.INCLUDE_GRADED, active_color=C.ACCENT,
        label="Hiển thị bài đã chấm",
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        on_change=lambda e: getattr(view, "_on_form_field_change", lambda: None)(),
    )

def build_display_section(view) -> ft.Control:
    """Xây dựng nội dung chi tiết tùy chọn hiển thị danh sách bài tập."""
    controls = [
        view._make_themed_label("Danh sách bài tập"),
        view._build_hint("Tùy chỉnh các bài tập xuất hiện trên bảng điều khiển chính."),
        view._sw_submitted,
        view._sw_graded,
    ]
    if not _pu.UI_IS_MOBILE:
        controls.extend([
            ft.Divider(height=16, color=C.BORDER),
            view._make_themed_label("Cửa sổ ứng dụng"),
            view._sw_always_on_top,
        ])

    return ft.Column(
        controls=controls,
        spacing=10,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
