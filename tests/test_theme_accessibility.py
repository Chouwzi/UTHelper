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


def test_theme_catalog_is_complete_and_material_graphite_is_dark():
    assert len(THEME_ORDER) == len(set(THEME_ORDER))
    assert set(THEME_ORDER) == set(THEME_PRESETS)
    assert THEME_ORDER[0] == "material_graphite"
    assert THEME_PRESETS["material_graphite"]["mode"] == "dark"


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
