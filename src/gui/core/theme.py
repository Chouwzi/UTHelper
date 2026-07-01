"""UTHelper Theme System — Curated theme presets & dynamic application."""


# ── Theme Preset Definitions ─────────────────────────────────────────
# Mỗi preset là dict đầy đủ: base UI colors + urgency + type badge colors.
# Key = tên theme (snake_case), dùng làm giá trị settings.THEME.

from gui.core.theme_presets import THEME_PRESETS, THEME_ORDER


# ── Active Theme Constants ────────────────────────────────────────────
# Tất cả GUI files đọc C.BG, C.ACCENT … qua reference.
# apply_theme() cập nhật giá trị class-level → hiệu lực toàn cục.

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


# ── Labels & mappings ─────────────────────────────────────────────────

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


# ── Theme Application ─────────────────────────────────────────────────

def apply_theme(theme_name: str) -> None:
    """Apply a theme preset to the global C class and _TYPE_COLORS dict.

    Args:
        theme_name: Key trong THEME_PRESETS (ví dụ "midnight_blue").
                    Nếu key không hợp lệ, fallback về midnight_blue.
    """
    preset = THEME_PRESETS.get(theme_name, THEME_PRESETS["midnight_blue"])

    # ── Base UI ──
    C.BG             = preset["bg"]
    C.SURFACE        = preset["surface"]
    C.SURFACE_HOVER  = preset["surface_hover"]
    C.ACCENT         = preset["accent"]
    C.TEXT_PRIMARY   = preset["text_primary"]
    C.TEXT_SECONDARY = preset["text_secondary"]
    C.BORDER         = preset["border"]

    # ── Urgency ──
    C.CRITICAL = preset["critical"]
    C.WARNING  = preset["warning"]
    C.SAFE     = preset["safe"]

    # ── Type badge colors ──
    _TYPE_COLORS["quiz"]       = preset["quiz"]
    _TYPE_COLORS["assignment"] = preset["assignment"]
    _TYPE_COLORS["deadline"]   = preset["assignment"]  # same as assignment
    _TYPE_COLORS["attendance"] = preset["attendance"]
    _TYPE_COLORS["open"]       = preset["open"]
    _TYPE_COLORS["other"]      = preset["other"]


def load_theme_from_settings():
    """Load theme from settings.json — called at import time and on save."""
    try:
        from config import settings

        # 1. Apply preset first (sets ALL colors)
        theme_name = getattr(settings, 'THEME', 'midnight_blue')
        apply_theme(theme_name)

        # 2. Override with custom colors if user has changed them
        #    (settings.COLOR_* khác default preset = user đã custom)
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
    """Sync current C values into page.theme so Flet semantic colors work.

    Call after apply_theme() to update page.theme ColorScheme.
    This makes ft.Colors.PRIMARY, ft.Colors.SURFACE, etc. match our C values.
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
