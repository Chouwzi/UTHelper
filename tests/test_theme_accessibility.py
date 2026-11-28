from types import SimpleNamespace
from pathlib import Path
import re

import pytest

from config import settings
from gui.core.theme import C, apply_theme, contrast_ratio, on_color, set_page_theme
from gui.core.theme_presets import THEME_ORDER, THEME_PRESETS


TEXT_ROLES = (
    "text_primary",
    "text_secondary",
    "accent",
    "critical",
    "warning",
    "safe",
    "quiz",
    "assignment",
    "attendance",
    "open",
    "other",
)

PALETTE_OWNERS = {
    Path("src/gui/core/theme.py"),
    Path("src/gui/core/theme_presets.py"),
    Path("src/gui/components/color_picker.py"),
    Path("src/gui/components/settings/theme_section.py"),
    Path("src/gui/view_models/settings_form.py"),
}


@pytest.fixture(autouse=True)
def restore_selected_theme():
    previous = settings.THEME
    yield
    settings.THEME = previous
    apply_theme(previous)


def test_theme_catalog_is_complete_and_steel_light_is_default_light():
    assert len(THEME_ORDER) == len(set(THEME_ORDER))
    assert set(THEME_ORDER) == set(THEME_PRESETS)
    assert THEME_ORDER[0] == "steel_light"
    assert THEME_PRESETS["steel_light"]["mode"] == "light"
    assert "mặc định" in THEME_PRESETS["steel_light"]["description"].lower()
    assert "mặc định" not in THEME_PRESETS["midnight_blue"]["description"].lower()
    assert THEME_PRESETS["material_graphite"]["mode"] == "dark"


def test_fresh_default_settings_apply_light_theme_without_dark_color_overrides(
    monkeypatch,
):
    import asyncio
    import flet as ft
    import config as config_mod
    from gui.components.settings_view import SettingsView
    from gui.core.theme import load_theme_from_settings
    from gui.view_models.settings_form import DEFAULT_THEME, SettingsFormSnapshot

    assert DEFAULT_THEME == "steel_light"
    fresh = config_mod.Settings()
    assert fresh.THEME == "steel_light"
    light_preset = THEME_PRESETS["steel_light"]
    assert fresh.COLOR_CRITICAL == light_preset["critical"]
    assert fresh.COLOR_WARNING == light_preset["warning"]
    assert fresh.COLOR_SAFE == light_preset["safe"]
    assert fresh.COLOR_QUIZ == light_preset["quiz"]
    assert fresh.COLOR_ASSIGNMENT == light_preset["assignment"]
    assert fresh.COLOR_ATTENDANCE == light_preset["attendance"]
    assert fresh.COLOR_OPEN == light_preset["open"]
    assert fresh.COLOR_OTHER == light_preset["other"]

    monkeypatch.setattr(config_mod, "settings", fresh)
    load_theme_from_settings()
    assert C.BG == light_preset["bg"]
    assert C.WARNING == light_preset["warning"]
    assert C.SAFE == light_preset["safe"]

    page = SimpleNamespace(
        theme_mode=None,
        theme=None,
        dark_theme=None,
        bgcolor=None,
        window=SimpleNamespace(),
        on_route_change=None,
        update=lambda: None,
    )
    set_page_theme(page)
    assert page.theme_mode == ft.ThemeMode.LIGHT
    assert page.dark_theme is None
    assert page.bgcolor == light_preset["bg"]

    # Unknown preset falls back to light default ("steel_light")
    apply_theme("unknown_nonexistent_theme")
    set_page_theme(page, theme_name="unknown_nonexistent_theme")
    assert C.BG == light_preset["bg"]
    assert page.theme_mode == ft.ThemeMode.LIGHT

    # Empty form snapshot and Settings "Khôi phục màu mặc định" both target steel_light
    empty_snapshot = SettingsFormSnapshot.from_form_values({})
    assert empty_snapshot.theme == "steel_light"
    assert empty_snapshot.color_warning == light_preset["warning"]

    view = SettingsView(page=page, orchestrator=None, on_close=lambda: None)
    view.update = lambda: None
    view._on_theme_select("midnight_blue")
    assert view._selected_theme == "midnight_blue"
    asyncio.run(view._handle_reset_defaults(None))
    assert view._selected_theme == "steel_light"
    assert page.theme_mode == ft.ThemeMode.LIGHT


@pytest.mark.parametrize("theme_name", THEME_ORDER)
def test_preset_text_and_semantic_colors_meet_wcag_aa(theme_name):
    preset = THEME_PRESETS[theme_name]

    for role in TEXT_ROLES:
        for surface in ("bg", "surface"):
            assert contrast_ratio(preset[role], preset[surface]) >= 4.5, (
                theme_name,
                role,
                surface,
            )
    for role in ("text_primary", "text_secondary"):
        assert contrast_ratio(preset[role], preset["surface_hover"]) >= 4.5, (
            theme_name,
            role,
            "surface_hover",
        )


@pytest.mark.parametrize("theme_name", THEME_ORDER)
def test_solid_semantic_controls_have_accessible_foregrounds(theme_name):
    apply_theme(theme_name)

    for foreground, background in (
        (C.ON_ACCENT, C.ACCENT),
        (C.ON_CRITICAL, C.CRITICAL),
        (C.ON_WARNING, C.WARNING),
        (C.ON_SAFE, C.SAFE),
    ):
        assert contrast_ratio(foreground, background) >= 4.5


@pytest.mark.parametrize("background", ["#FFFFFF", "#000000", "#A8C7FA", "#BE185D"])
def test_on_color_chooses_a_wcag_aa_foreground(background):
    assert contrast_ratio(on_color(background), background) >= 4.5


def test_set_page_theme_maps_material_semantic_roles():
    settings.THEME = "material_graphite"
    apply_theme(settings.THEME)
    page = SimpleNamespace(theme_mode=None, theme=None, dark_theme=None, bgcolor=None)

    set_page_theme(page)

    scheme = page.theme.color_scheme
    assert str(page.theme_mode).lower().endswith("dark")
    assert page.dark_theme is page.theme
    assert page.bgcolor == C.BG
    assert scheme.primary == C.ACCENT
    assert scheme.on_primary == C.ON_ACCENT
    assert scheme.surface == C.SURFACE
    assert scheme.outline == C.BORDER_STRONG
    assert scheme.error == C.CRITICAL
    assert scheme.on_error == C.ON_CRITICAL

    import flet as ft

    nav_theme = page.theme.navigation_bar_theme
    assert isinstance(nav_theme, ft.NavigationBarTheme)
    assert nav_theme.label_behavior == ft.NavigationBarLabelBehavior.ALWAYS_SHOW
    assert nav_theme.label_padding == ft.Padding.symmetric(horizontal=1, vertical=0)
    selected_style = nav_theme.label_text_style[ft.ControlState.SELECTED]
    default_style = nav_theme.label_text_style[ft.ControlState.DEFAULT]
    assert selected_style.size == 10.5
    assert selected_style.overflow == ft.TextOverflow.ELLIPSIS
    assert default_style.size == 10.5
    assert default_style.overflow == ft.TextOverflow.ELLIPSIS


def test_gui_components_do_not_own_literal_palette_colors():
    root = Path(__file__).parents[1]
    forbidden = re.compile(
        r'#[0-9A-Fa-f]{6,8}|ft\.Colors\.(?:WHITE|BLACK|RED|GREEN|BLUE|ORANGE|YELLOW|PURPLE)'
    )
    violations = []

    for path in (root / "src/gui").rglob("*.py"):
        relative = path.relative_to(root)
        if relative in PALETTE_OWNERS:
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if forbidden.search(line):
                violations.append(f"{relative}:{line_number}")

    assert violations == []


def _blend_hex(fg_hex: str, bg_hex: str, alpha: float) -> str:
    fg = [int(fg_hex.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    bg = [int(bg_hex.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    out = [round(alpha * f + (1.0 - alpha) * b) for f, b in zip(fg, bg, strict=True)]
    return "#{:02X}{:02X}{:02X}".format(*out)


@pytest.mark.parametrize("theme_name", THEME_ORDER)
def test_makeup_badge_colors_meet_wcag_aa_across_all_themes(theme_name):
    apply_theme(theme_name)
    chip_bg = _blend_hex(C.MAKEUP_BG, C.SURFACE, 0.25)

    assert contrast_ratio(C.MAKEUP_TEXT, chip_bg) >= 4.5, (
        theme_name,
        C.MAKEUP_TEXT,
        chip_bg,
    )
    assert contrast_ratio(C.MAKEUP_TEXT, C.SURFACE) >= 4.5, (
        theme_name,
        C.MAKEUP_TEXT,
        C.SURFACE,
    )


def test_makeup_semantic_colors_remap_on_live_light_dark_theme_switch():
    import flet as ft
    from gui.core.theme import refresh_semantic_control_colors, semantic_palette

    apply_theme("midnight_blue")
    dark_palette = semantic_palette()
    chip = ft.Container(
        content=ft.Text("Học bù", color=C.MAKEUP_TEXT),
        bgcolor=ft.Colors.with_opacity(0.25, C.MAKEUP_BG),
    )

    apply_theme("steel_light")
    light_palette = semantic_palette()
    assert dark_palette["MAKEUP_TEXT"] != light_palette["MAKEUP_TEXT"]
    refresh_semantic_control_colors([chip], dark_palette, light_palette)

    assert chip.content.color == C.MAKEUP_TEXT
    assert chip.bgcolor == ft.Colors.with_opacity(0.25, C.MAKEUP_BG)

