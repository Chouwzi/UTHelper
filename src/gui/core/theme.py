"""Hệ thống Theme của UTHelper - Các preset màu sắc chọn lọc & cơ chế áp dụng linh hoạt."""


# Định nghĩa các Preset Theme
# Mỗi preset là một dict đầy đủ gồm: các màu UI cơ bản + màu mức độ khẩn cấp + màu badge loại hoạt động.
# Key = tên theme (snake_case), được lưu trong cấu hình settings.THEME.

from dataclasses import fields, is_dataclass, replace
from typing import Iterable

from gui.core.theme_presets import THEME_PRESETS, THEME_ORDER

__all__ = [
    "C",
    "THEME_PRESETS",
    "THEME_ORDER",
    "apply_theme",
    "contrast_ratio",
    "load_theme_from_settings",
    "on_color",
    "refresh_semantic_control_colors",
    "semantic_palette",
    "set_page_theme",
]


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
    BORDER_STRONG  = "#94A3B8"
    SURFACE_ELEVATED = "#1A2340"
    ON_ACCENT      = "#FFFFFF"
    ON_CRITICAL    = "#FFFFFF"
    ON_WARNING     = "#0B0F1A"
    ON_SAFE        = "#0B0F1A"
    UPDATE         = "#F59E0B"
    ON_UPDATE      = "#0B0F1A"
    SCRIM          = "#99000000"
    SHADOW         = "#66000000"


_SEMANTIC_COLOR_NAMES = (
    "BG",
    "SURFACE",
    "SURFACE_HOVER",
    "SURFACE_ELEVATED",
    "ACCENT",
    "CRITICAL",
    "WARNING",
    "SAFE",
    "TEXT_PRIMARY",
    "TEXT_SECONDARY",
    "BORDER",
    "BORDER_STRONG",
    "ON_ACCENT",
    "ON_CRITICAL",
    "ON_WARNING",
    "ON_SAFE",
    "UPDATE",
    "ON_UPDATE",
    "SCRIM",
    "SHADOW",
)

_CONTROL_COLOR_ATTRIBUTES = (
    "color",
    "bgcolor",
    "icon_color",
    "border_color",
    "focused_border_color",
    "selected_color",
    "selected_tile_color",
    "hover_color",
    "splash_color",
    "active_color",
    "active_track_color",
    "check_color",
    "fill_color",
    "focus_color",
    "overlay_color",
    "track_color",
    "track_outline_color",
    "thumb_color",
    "text_color",
    "collapsed_text_color",
    "collapsed_icon_color",
    "divider_color",
    "shadow_color",
    "barrier_color",
    "label_style",
    "hint_style",
    "text_style",
    "style",
    "border",
    "gradient",
)

_CONTROL_CHILD_ATTRIBUTES = (
    "content",
    "controls",
    "items",
    "actions",
    "title",
    "subtitle",
    "leading",
    "trailing",
    "label",
)


def semantic_palette() -> dict[str, str]:
    """Capture the currently active semantic color tokens."""
    return {name: getattr(C, name) for name in _SEMANTIC_COLOR_NAMES}


def _semantic_replacements(
    previous: dict[str, str], current: dict[str, str]
) -> dict[str, str]:
    return {
        previous[name].upper(): current[name]
        for name in _SEMANTIC_COLOR_NAMES
        if name in previous and name in current and previous[name] != current[name]
    }


def _remap_color_string(value: str, replacements: dict[str, str]) -> str:
    direct = replacements.get(value.upper())
    if direct is not None:
        return direct
    # Flet 0.86 serializes Colors.with_opacity() as "#RRGGBB,opacity".
    base, separator, opacity = value.partition(",")
    if separator:
        replacement = replacements.get(base.upper())
        if replacement is not None:
            return f"{replacement},{opacity}"
    # Flet emits translucent colors as #AARRGGBB. Preserve alpha while replacing
    # the semantic RGB token underneath it.
    if len(value) == 9 and value.startswith("#"):
        replacement = replacements.get(f"#{value[3:]}".upper())
        if replacement is not None and replacement.startswith("#"):
            return f"{value[:3]}{replacement[1:]}"
    return value


def _remap_style_value(value, replacements: dict[str, str]):
    import flet as ft

    if isinstance(value, str):
        return _remap_color_string(value, replacements)
    if isinstance(value, ft.BaseControl):
        return value
    if isinstance(value, dict):
        return {
            key: _remap_style_value(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_remap_style_value(item, replacements) for item in value]
    if isinstance(value, tuple):
        return tuple(_remap_style_value(item, replacements) for item in value)
    if is_dataclass(value):
        changes = {}
        for field in fields(value):
            if not field.init:
                continue
            old = getattr(value, field.name)
            new = _remap_style_value(old, replacements)
            if new != old:
                changes[field.name] = new
        return replace(value, **changes) if changes else value
    return value


def refresh_semantic_control_colors(
    roots: Iterable[object],
    previous: dict[str, str],
    current: dict[str, str] | None = None,
    *,
    excluded_roots: Iterable[object] = (),
) -> None:
    """Remap stored semantic colors across mounted or detached Flet controls.

    Flet controls retain concrete color strings from construction time. This
    bounded traversal updates public color/style properties after a live theme
    switch and intentionally avoids private Flet serialization internals.
    """
    import flet as ft

    replacements = _semantic_replacements(previous, current or semantic_palette())
    if not replacements:
        return
    seen: set[int] = set()
    excluded_ids = {id(control) for control in excluded_roots}

    def visit(control) -> None:
        if (
            not isinstance(control, ft.BaseControl)
            or id(control) in seen
            or id(control) in excluded_ids
        ):
            return
        seen.add(id(control))
        for attribute in _CONTROL_COLOR_ATTRIBUTES:
            if not hasattr(control, attribute):
                continue
            try:
                old = getattr(control, attribute)
                new = _remap_style_value(old, replacements)
                if new != old:
                    setattr(control, attribute, new)
            except (AttributeError, TypeError, ValueError):
                continue
        for attribute in _CONTROL_CHILD_ATTRIBUTES:
            try:
                children = getattr(control, attribute, None)
            except (AttributeError, RuntimeError):
                continue
            if isinstance(children, ft.BaseControl):
                visit(children)
            elif isinstance(children, (list, tuple)):
                for child in children:
                    visit(child)

    for root in roots:
        visit(root)


def _relative_luminance(color: str) -> float:
    value = color.lstrip("#")[:6]
    channels = [int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(first: str, second: str) -> float:
    """Return the WCAG contrast ratio for two opaque RGB colors."""
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def on_color(background: str) -> str:
    """Choose a WCAG-friendly foreground for a solid semantic color."""
    return max(
        ("#000000", "#FFFFFF"),
        key=lambda candidate: contrast_ratio(candidate, background),
    )


def _best_on_color(background: str, preset: dict, explicit_key: str) -> str:
    explicit = preset.get(explicit_key)
    if explicit:
        return explicit
    candidates = (preset["bg"], preset["text_primary"], "#000000", "#FFFFFF")
    return max(candidates, key=lambda candidate: contrast_ratio(candidate, background))


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
    C.BORDER_STRONG  = preset.get("border_strong", preset["text_secondary"])
    C.SURFACE_ELEVATED = preset.get(
        "surface_elevated", preset["surface_hover"]
    )
    C.ON_ACCENT = _best_on_color(C.ACCENT, preset, "on_accent")

    # Mức độ khẩn cấp
    C.CRITICAL = preset["critical"]
    C.WARNING  = preset["warning"]
    C.SAFE     = preset["safe"]
    C.ON_CRITICAL = _best_on_color(C.CRITICAL, preset, "on_critical")
    C.ON_WARNING = _best_on_color(C.WARNING, preset, "on_warning")
    C.ON_SAFE = _best_on_color(C.SAFE, preset, "on_safe")
    C.UPDATE = C.WARNING
    C.ON_UPDATE = C.ON_WARNING

    # Màu sắc của các badge loại hoạt động
    _TYPE_COLORS["quiz"]       = preset["quiz"]
    _TYPE_COLORS["assignment"] = preset["assignment"]
    _TYPE_COLORS["deadline"]   = preset["assignment"]  # Đồng bộ cùng màu với bài tập
    _TYPE_COLORS["attendance"] = preset["attendance"]
    _TYPE_COLORS["open"]       = preset["open"]
    _TYPE_COLORS["other"]      = preset["other"]


def load_theme_from_settings():
    """Tải cấu hình theme từ settings.json - được gọi tự động khi import và khi người dùng lưu thiết lập."""
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

        # User-defined semantic colors can be lighter than the preset. Recompute
        # their foregrounds so solid buttons never retain an unreadable color.
        C.ON_CRITICAL = on_color(C.CRITICAL)
        C.ON_WARNING = on_color(C.WARNING)
        C.ON_SAFE = on_color(C.SAFE)
        C.UPDATE = C.WARNING
        C.ON_UPDATE = C.ON_WARNING

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
    from config import settings
    from gui.core.theme_presets import THEME_PRESETS
    
    theme_name = getattr(settings, 'THEME', 'midnight_blue')
    preset = THEME_PRESETS.get(theme_name, THEME_PRESETS["midnight_blue"])
    is_light = preset.get("mode", "dark") == "light"

    theme_obj = ft.Theme(
        color_scheme=ft.ColorScheme(
            primary=C.ACCENT,
            on_primary=C.ON_ACCENT,
            primary_container=C.SURFACE_HOVER,
            on_primary_container=C.TEXT_PRIMARY,
            secondary=C.SAFE,
            on_secondary=C.ON_SAFE,
            secondary_container=C.SURFACE_HOVER,
            on_secondary_container=C.TEXT_PRIMARY,
            tertiary=C.WARNING,
            on_tertiary=C.ON_WARNING,
            surface=C.SURFACE,
            surface_dim=C.BG,
            surface_bright=C.SURFACE_ELEVATED,
            surface_container_lowest=C.BG,
            surface_container_low=C.SURFACE,
            surface_container=C.SURFACE,
            surface_container_high=C.SURFACE_HOVER,
            surface_container_highest=C.SURFACE_ELEVATED,
            on_surface=C.TEXT_PRIMARY,
            on_surface_variant=C.TEXT_SECONDARY,
            outline=C.BORDER_STRONG,
            outline_variant=C.BORDER,
            error=C.CRITICAL,
            on_error=C.ON_CRITICAL,
            shadow=C.SHADOW,
            scrim=C.SCRIM,
        ),
    )
    
    if is_light:
        page.theme_mode = ft.ThemeMode.LIGHT
        page.theme = theme_obj
        page.dark_theme = None
    else:
        page.theme_mode = ft.ThemeMode.DARK
        page.theme = theme_obj
        page.dark_theme = theme_obj
        
    page.bgcolor = C.BG
