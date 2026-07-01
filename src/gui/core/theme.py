"""Hệ thống Theme của UTHelper — Các preset màu sắc chọn lọc & cơ chế áp dụng linh hoạt."""


# Định nghĩa các Preset Theme
# Mỗi preset là một dict đầy đủ gồm: các màu UI cơ bản + màu mức độ khẩn cấp + màu badge loại hoạt động.
# Key = tên theme (snake_case), được lưu trong cấu hình settings.THEME.

from gui.core.theme_presets import THEME_PRESETS, THEME_ORDER


# Các Hằng số Theme đang Hoạt động
# Tất cả các file GUI đọc màu sắc C.BG, C.ACCENT … qua các tham chiếu này.
# apply_theme() sẽ cập nhật các giá trị ở mức class-level → có hiệu lực toàn cục ngay lập tức.

class C:
    BG             = "#0B0F1A"
    SURFACE        = "#141B2D"
    SURFACE_HOVER  = "#1A2340"
    ACCENT         = "#3B82F6"
    CRITICAL       = "#EF4444"
    WARNING        = "#F59E0B"
    SAFE           = "#10B981"
    TEXT_PRIMARY   = "#F1F5F9"
    TEXT_SECONDARY = "#94A3B8"
    BORDER         = "#1E293B"


# Nhãn & Ánh xạ loại hoạt động

_TYPE_LABELS = {
    "quiz":       "QUIZ",
    "assignment": "BÀI TẬP",
    "attendance": "ĐIỂM DANH",
    "deadline":   "HẠN NỘP",
    "open":       "SẮP MỞ",
    "other":      "SỰ KIỆN",
}

_DEADLINE_TYPES = {"deadline", "quiz", "assignment"}


_TYPE_COLORS = {
    "quiz":       "#7C3AED",   # tím
    "assignment": "#2563EB",   # xanh dương
    "attendance": "#D97706",   # cam
    "deadline":   "#2563EB",   # giống màu bài tập
    "open":       "#0891B2",   # xanh lơ
    "other":      "#6B7280",   # xám
}


# Cơ chế Áp dụng Theme

def apply_theme(theme_name: str) -> None:
    """Áp dụng một theme preset vào các hằng số giao diện toàn cục C và từ điển _TYPE_COLORS.

    Args:
        theme_name: Khóa (key) trong THEME_PRESETS (ví dụ "midnight_blue").
                    Nếu khóa không hợp lệ, sẽ tự động rollback về "midnight_blue".
    """
    preset = THEME_PRESETS.get(theme_name, THEME_PRESETS["midnight_blue"])

    # Các màu UI cơ sở
    C.BG             = preset["bg"]
    C.SURFACE        = preset["surface"]
    C.SURFACE_HOVER  = preset["surface_hover"]
    C.ACCENT         = preset["accent"]
    C.TEXT_PRIMARY   = preset["text_primary"]
    C.TEXT_SECONDARY = preset["text_secondary"]
    C.BORDER         = preset["border"]

    # Mức độ khẩn cấp
    C.CRITICAL = preset["critical"]
    C.WARNING  = preset["warning"]
    C.SAFE     = preset["safe"]

    # Màu sắc của các badge loại hoạt động
    _TYPE_COLORS["quiz"]       = preset["quiz"]
    _TYPE_COLORS["assignment"] = preset["assignment"]
    _TYPE_COLORS["deadline"]   = preset["assignment"]  # Đồng bộ cùng màu với bài tập
    _TYPE_COLORS["attendance"] = preset["attendance"]
    _TYPE_COLORS["open"]       = preset["open"]
    _TYPE_COLORS["other"]      = preset["other"]


def load_theme_from_settings():
    """Tải cấu hình theme từ settings.json — được gọi tự động khi import và khi người dùng lưu thiết lập."""
    try:
        from config import settings

        # 1. Áp dụng preset trước (thiết lập toàn bộ màu sắc nền tảng)
        theme_name = getattr(settings, 'THEME', 'midnight_blue')
        apply_theme(theme_name)

        # 2. Ghi đè bằng các màu sắc tùy chỉnh riêng lẻ nếu người dùng đã chỉnh sửa
        #    (các giá trị settings.COLOR_* khác biệt so với mặc định của preset)
        preset = THEME_PRESETS.get(theme_name, THEME_PRESETS["midnight_blue"])

        custom_map = {
            'COLOR_CRITICAL':   ('critical',   lambda v: setattr(C, 'CRITICAL', v)),
            'COLOR_WARNING':    ('warning',    lambda v: setattr(C, 'WARNING', v)),
            'COLOR_SAFE':       ('safe',       lambda v: setattr(C, 'SAFE', v)),
            'COLOR_QUIZ':       ('quiz',       lambda v: _TYPE_COLORS.__setitem__('quiz', v)),
            'COLOR_ASSIGNMENT': ('assignment', lambda v: (_TYPE_COLORS.__setitem__('assignment', v),
                                                          _TYPE_COLORS.__setitem__('deadline', v))),
            'COLOR_ATTENDANCE': ('attendance', lambda v: _TYPE_COLORS.__setitem__('attendance', v)),
            'COLOR_OPEN':       ('open',       lambda v: _TYPE_COLORS.__setitem__('open', v)),
            'COLOR_OTHER':      ('other',      lambda v: _TYPE_COLORS.__setitem__('other', v)),
        }

        for setting_key, (preset_key, setter) in custom_map.items():
            user_val = getattr(settings, setting_key, None)
            if user_val and user_val != preset.get(preset_key):
                setter(user_val)

    except Exception:
        import traceback
        traceback.print_exc()


# Lần đầu import gọi luôn để khởi tạo màu
load_theme_from_settings()


def set_page_theme(page) -> None:
    """Đồng bộ các giá trị màu C hiện tại vào page.theme ColorScheme để các màu sắc ngữ nghĩa (semantic colors) của Flet hoạt động chính xác.

    Cần gọi hàm này sau khi thực hiện apply_theme() để cập nhật lại ColorScheme của page.
    Giúp các thuộc tính như ft.Colors.PRIMARY, ft.Colors.SURFACE đồng bộ với theme tùy chỉnh.
    """
    import flet as ft

    page.theme = ft.Theme(
        color_scheme=ft.ColorScheme(
            primary=C.ACCENT,
            on_primary="#FFFFFF",
            secondary=C.TEXT_SECONDARY,
            on_secondary="#FFFFFF",
            surface=C.SURFACE,
            on_surface=C.TEXT_PRIMARY,
            on_surface_variant=C.TEXT_SECONDARY,
            outline=C.BORDER,
            outline_variant=C.BORDER,
            error=C.CRITICAL,
            on_error="#FFFFFF",
        ),
    )
    page.dark_theme = page.theme
    page.bgcolor = C.BG
