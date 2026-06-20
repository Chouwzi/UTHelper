"""UTHelper Theme System — Curated theme presets & dynamic application."""


# ── Theme Preset Definitions ─────────────────────────────────────────
# Mỗi preset là dict đầy đủ: base UI colors + urgency + type badge colors.
# Key = tên theme (snake_case), dùng làm giá trị settings.THEME.

THEME_PRESETS = {
    "midnight_blue": {
        "label": "Midnight Blue",
        "description": "Deep navy — mặc định",
        # ── Base UI ──
        "bg":             "#0B0F1A",
        "surface":        "#141B2D",
        "surface_hover":  "#1A2340",
        "accent":         "#3B82F6",
        "text_primary":   "#F1F5F9",
        "text_secondary": "#94A3B8",
        "border":         "#1E293B",
        # ── Urgency ──
        "critical": "#EF4444",
        "warning":  "#F59E0B",
        "safe":     "#10B981",
        # ── Type badges ──
        "quiz":       "#7C3AED",
        "assignment": "#2563EB",
        "attendance": "#D97706",
        "open":       "#0891B2",
        "other":      "#6B7280",
    },
    "ocean_teal": {
        "label": "Ocean Teal",
        "description": "Calm cyan — education",
        "bg":             "#0A1628",
        "surface":        "#122035",
        "surface_hover":  "#1A2D45",
        "accent":         "#0891B2",
        "text_primary":   "#E0F2FE",
        "text_secondary": "#7DD3FC",
        "border":         "#1B3A4B",
        "critical": "#F87171",
        "warning":  "#FBBF24",
        "safe":     "#34D399",
        "quiz":       "#8B5CF6",
        "assignment": "#06B6D4",
        "attendance": "#F59E0B",
        "open":       "#22D3EE",
        "other":      "#64748B",
    },
    "sakura_pink": {
        "label": "Sakura Pink",
        "description": "Vibrant — youthful",
        "bg":             "#1A0F1C",
        "surface":        "#251828",
        "surface_hover":  "#312035",
        "accent":         "#EC4899",
        "text_primary":   "#FDF2F8",
        "text_secondary": "#F9A8D4",
        "border":         "#3B1D3F",
        "critical": "#FB7185",
        "warning":  "#FCD34D",
        "safe":     "#6EE7B7",
        "quiz":       "#A78BFA",
        "assignment": "#F472B6",
        "attendance": "#FBBF24",
        "open":       "#67E8F9",
        "other":      "#9CA3AF",
    },
    "nord_frost": {
        "label": "Nord Frost",
        "description": "Arctic — minimalist",
        "bg":             "#2E3440",
        "surface":        "#3B4252",
        "surface_hover":  "#434C5E",
        "accent":         "#88C0D0",
        "text_primary":   "#ECEFF4",
        "text_secondary": "#D8DEE9",
        "border":         "#4C566A",
        "critical": "#BF616A",
        "warning":  "#EBCB8B",
        "safe":     "#A3BE8C",
        "quiz":       "#B48EAD",
        "assignment": "#81A1C1",
        "attendance": "#D08770",
        "open":       "#8FBCBB",
        "other":      "#7B88A1",
    },
    "monokai_pro": {
        "label": "Monokai Pro",
        "description": "Warm dark — developer",
        "bg":             "#2D2A2E",
        "surface":        "#403E41",
        "surface_hover":  "#4A474B",
        "accent":         "#FFD866",
        "text_primary":   "#FCFCFA",
        "text_secondary": "#C1C0C0",
        "border":         "#5B595C",
        "critical": "#FF6188",
        "warning":  "#FFD866",
        "safe":     "#A9DC76",
        "quiz":       "#AB9DF2",
        "assignment": "#78DCE8",
        "attendance": "#FC9867",
        "open":       "#78DCE8",
        "other":      "#727072",
    },
    "solarized_dark": {
        "label": "Solarized Dark",
        "description": "Proven — readability",
        "bg":             "#002B36",
        "surface":        "#073642",
        "surface_hover":  "#0D4150",
        "accent":         "#268BD2",
        "text_primary":   "#FDF6E3",
        "text_secondary": "#93A1A1",
        "border":         "#586E75",
        "critical": "#DC322F",
        "warning":  "#B58900",
        "safe":     "#859900",
        "quiz":       "#6C71C4",
        "assignment": "#268BD2",
        "attendance": "#CB4B16",
        "open":       "#2AA198",
        "other":      "#657B83",
    },
}

# Thứ tự hiển thị trong UI
THEME_ORDER = [
    "midnight_blue", "ocean_teal", "sakura_pink",
    "nord_frost", "monokai_pro", "solarized_dark",
]


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
