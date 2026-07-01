import flet as ft
from gui.core.theme import C
from config import settings

def init_theme_controls(view):
    """Khởi tạo các bộ chọn màu sắc (color fields) tùy chỉnh cho thẻ và nhãn."""
    # Khởi tạo màu cho các trạng thái khẩn cấp của thẻ hạn nộp
    view._c_tb_critical, view._row_cri = view._build_color_field("Cấp bách / Quá hạn", getattr(settings, 'COLOR_CRITICAL', '#EF4444'))
    view._c_tb_warning, view._row_warn = view._build_color_field("Sắp tới", getattr(settings, 'COLOR_WARNING', '#F59E0B'))
    view._c_tb_safe, view._row_safe = view._build_color_field("An toàn / Thường", getattr(settings, 'COLOR_SAFE', '#10B981'))
    
    # Khởi tạo màu cho các thẻ tag nhãn loại hoạt động
    view._c_tb_quiz, view._row_quiz = view._build_color_field("Tag Quiz", getattr(settings, 'COLOR_QUIZ', '#7C3AED'))
    view._c_tb_ass, view._row_ass = view._build_color_field("Tag Bài tập", getattr(settings, 'COLOR_ASSIGNMENT', '#2563EB'))
    view._c_tb_att, view._row_att = view._build_color_field("Tag Điểm danh", getattr(settings, 'COLOR_ATTENDANCE', '#D97706'))
    view._c_tb_open, view._row_open = view._build_color_field("Tag Sắp mở", getattr(settings, 'COLOR_OPEN', '#0891B2'))
    view._c_tb_other, view._row_other = view._build_color_field("Tag Sự kiện", getattr(settings, 'COLOR_OTHER', '#6B7280'))
    
    # Dựng bộ chọn theme card (Theme Selector)
    view._theme_cards_row = view._build_theme_selector()

    # Nút bấm khôi phục màu sắc và cấu hình giao diện mặc định
    view.btn_reset = ft.OutlinedButton(
        "Khôi phục mặc định", 
        width=400, 
        on_click=view._handle_reset_defaults, 
        style=ft.ButtonStyle(color=C.TEXT_SECONDARY)
    )

def build_theme_section(view) -> ft.Container:
    """Xây dựng Container chứa cấu hình lựa chọn theme và tùy chỉnh màu sắc thẻ."""
    return view._build_setting_group(
        "Giao diện",
        "Theme và tùy chỉnh màu sắc",
        [
            view._make_themed_label("Chọn Theme"),
            view._theme_cards_row,
            ft.Divider(height=10, color=C.BORDER),
            view._make_themed_label("Tùy chỉnh màu"),
            view._build_hint("Thay đổi màu riêng sẽ ghi đè preset theme."),
            view._row_cri, view._row_warn, view._row_safe,
            ft.Divider(height=10, color=C.BORDER),
            view._row_quiz, view._row_ass, view._row_att, view._row_open, view._row_other,
            ft.Divider(height=10, color=C.BORDER),
            view.btn_reset,
        ],
        icon=ft.Icons.PALETTE_OUTLINED,
    )
