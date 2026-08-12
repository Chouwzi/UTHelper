from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import flet as ft

from config import settings
from gui.components.calendar_view import CalendarView
from gui.components.grade_overview_view import GradeOverviewView
from gui.components.settings_view import SettingsView
from gui.core.theme import (
    C,
    apply_theme,
    refresh_semantic_control_colors,
    semantic_palette,
)
from gui.core.theme_presets import THEME_ORDER, THEME_PRESETS


def _page():
    return SimpleNamespace(
        overlay=[],
        update=lambda: None,
        run_task=lambda *_args: None,
        window=SimpleNamespace(),
    )


def test_semantic_refresh_updates_nested_styles_and_preserves_opacity():
    original_theme = settings.THEME
    try:
        apply_theme("midnight_blue")
        previous = semantic_palette()
        text = ft.Text("Nhãn", color=C.TEXT_SECONDARY)
        panel = ft.Container(
            content=text,
            bgcolor=ft.Colors.with_opacity(0.19, C.ACCENT),
            border=ft.Border.all(1, C.BORDER),
        )

        apply_theme("steel_light")
        refresh_semantic_control_colors([panel], previous)

        assert text.color == C.TEXT_SECONDARY
        assert panel.bgcolor == ft.Colors.with_opacity(0.19, C.ACCENT)
        assert panel.border == ft.Border.all(1, C.BORDER)
    finally:
        apply_theme(original_theme)


def test_settings_refresh_includes_detached_and_collapsed_controls():
    original_theme = settings.THEME
    page = _page()
    orchestrator = SimpleNamespace(
        client=SimpleNamespace(),
        get_cached_details_snapshot=lambda: {},
    )
    try:
        apply_theme("midnight_blue")
        view = SettingsView(
            page,
            orchestrator,
            lambda: None,
            autostart_coordinator=SimpleNamespace(),
        )

        apply_theme("steel_light")
        view._refresh_section_colors()

        assert view.bgcolor == C.BG
        assert view._debug_scheduler_status.color == C.TEXT_SECONDARY
        assert view._debug_mobile_text.color == C.TEXT_SECONDARY
        assert view._dd_crash_reporting_consent.bgcolor == C.BG
        assert view._sticky_footer.border.top.color == ft.Colors.with_opacity(
            0.19, C.TEXT_SECONDARY
        )
    finally:
        apply_theme(original_theme)


def test_theme_preview_cards_keep_their_own_preset_palettes():
    original_theme = settings.THEME
    page = _page()
    orchestrator = SimpleNamespace(
        client=SimpleNamespace(),
        get_cached_details_snapshot=lambda: {},
    )
    try:
        apply_theme("midnight_blue")
        view = SettingsView(
            page,
            orchestrator,
            lambda: None,
            autostart_coordinator=SimpleNamespace(),
        )

        for selected_theme in ("steel_light", "ocean_teal", "rose_light"):
            view._on_theme_select(selected_theme)
            cards = view._theme_cards_row.content.controls

            for key, card in zip(THEME_ORDER, cards, strict=True):
                preset = THEME_PRESETS[key]
                content = card.content.controls
                assert card.bgcolor == preset["surface"]
                assert content[0].controls[0].color == preset["accent"]
                assert content[1].gradient.colors == [
                    preset["bg"],
                    preset["surface"],
                    preset["accent"],
                ]
                assert [dot.bgcolor for dot in content[2].controls] == [
                    preset["bg"],
                    preset["surface"],
                    preset["accent"],
                    preset["critical"],
                    preset["safe"],
                ]
                assert content[3].color == preset["text_primary"]
                assert content[4].color == preset["text_secondary"]
    finally:
        apply_theme(original_theme)


def test_calendar_and_grade_hidden_controls_receive_live_theme():
    original_theme = settings.THEME
    try:
        apply_theme("midnight_blue")
        calendar = CalendarView(_page(), on_close=lambda: None)
        grades = GradeOverviewView(on_close=lambda: None)

        apply_theme("steel_light")
        calendar.update_theme()
        grades.update_theme()

        assert all(
            cell.data["today_dot"].bgcolor == C.ACCENT
            for row in calendar._day_cells
            for cell in row
        )
        assert all(
            cell.data["count_text"].color == C.TEXT_SECONDARY
            for cell in calendar._week_cells
        )
        assert calendar._detail_divider.color == C.BORDER
        assert grades._header_divider.color == C.BORDER
        assert grades._loading_text.color == C.TEXT_SECONDARY
    finally:
        apply_theme(original_theme)


def test_gui_does_not_build_transparency_with_rrggbbaa_concatenation():
    source_root = Path(__file__).parents[1] / "src" / "gui"
    pattern = re.compile(r"\+\s*['\"][0-9A-Fa-f]{2}['\"]")
    offenders = []
    for path in source_root.rglob("*.py"):
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(source_root)}:{line_number}")

    assert offenders == []
