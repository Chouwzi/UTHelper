import flet as ft
from gui.core.theme import C
from config import settings

def init_debug_controls(view):
    """Khởi tạo các control cấu hình nâng cao và công cụ gỡ lỗi (debug controls)."""
    # Trường cấu hình số lượng worker prefetch song song
    view._workers_field = ft.TextField(
        value=str(settings.PREFETCH_WORKERS),
        label="Số luồng tải đồng thời (1-10)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
    )
    # Switch bật/tắt ghi log debug chi tiết
    view._sw_debug = ft.Switch(
        value=settings.DEBUG_MODE, active_color=C.CRITICAL,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Chế độ Gỡ lỗi (Debug Log)",
        on_change=lambda e: view._toggle_debug_ui()
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

def build_debug_section(view) -> ft.Container:
    """Xây dựng Container nhóm các control gỡ lỗi và cấu hình luồng tải."""
    return view._build_setting_group(
        "Nâng cao",
        "Luồng tải, Log hệ thống",
        [
            view._workers_field,
            view._build_hint("Tăng để tải chi tiết nhanh hơn. Nhỏ đi nếu bị block."),
            view._sw_debug,
            view._test_panel,
        ],
        icon=ft.Icons.BUILD_OUTLINED,
    )
