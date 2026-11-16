from __future__ import annotations

import re
from datetime import date
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


def test_theme_preview_palettes_are_restored_after_external_repaint():
    original_theme = settings.THEME
    page = _page()
    orchestrator = SimpleNamespace(
        client=SimpleNamespace(),
        get_cached_details_snapshot=lambda: {},
    )
    view = None
    external_previous_palette = None

    def _repaint_everything_including_previews():
        refresh_semantic_control_colors(
            [view], external_previous_palette, semantic_palette()
        )

    try:
        apply_theme("midnight_blue")
        external_previous_palette = semantic_palette()
        view = SettingsView(
            page,
            orchestrator,
            lambda: None,
            on_theme_preview=_repaint_everything_including_previews,
            autostart_coordinator=SimpleNamespace(),
        )

        view._on_theme_select("sakura_pink")

        for key, card in zip(
            THEME_ORDER, view._theme_cards_row.content.controls, strict=True
        ):
            preset = THEME_PRESETS[key]
            content = card.content.controls
            assert card.bgcolor == preset["surface"]
            assert content[1].gradient.colors == [
                preset["bg"], preset["surface"], preset["accent"]
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
        assert len(calendar._period_pages) == 3
        for period in calendar._period_pages:
            for row in period.day_cells:
                for cell in row:
                    assert cell.data["today_dot"].bgcolor == C.ACCENT
    finally:
        apply_theme(original_theme)


def test_calendar_mounted_period_states_keep_semantic_roles_across_all_themes():
    original_theme = settings.THEME
    try:
        apply_theme(THEME_ORDER[0])
        calendar = CalendarView(_page(), on_close=lambda: None)
        today = date.today()
        calendar._year, calendar._month = today.year, today.month
        calendar._selected_day = today.day
        calendar._selected_date = today

        critical_date = next(
            date(today.year, today.month, day)
            for day in range(1, 29)
            if day != today.day
            and date(today.year, today.month, day).weekday() != 6
        )
        calendar._deadline_map = {
            critical_date.isoformat(): [{"urgency": "critical"}]
        }
        calendar._reset_period_pager()

        for theme_name in [*THEME_ORDER, "material_graphite"]:
            apply_theme(theme_name)
            calendar.update_theme()
            center = calendar._period_pages[1]
            dated_cells = {
                cell.data["date"]: cell
                for row in center.day_cells
                for cell in row
                if cell.data["date"] is not None
            }
            selected = dated_cells[today]
            critical = dated_cells[critical_date]

            assert selected.bgcolor == C.SURFACE_HOVER, theme_name
            assert selected.data["day_text"].color == C.ACCENT, theme_name
            assert selected.border.top.color == C.BORDER_STRONG, theme_name
            assert critical.bgcolor == ft.Colors.with_opacity(
                0.06, C.CRITICAL
            ), theme_name
            assert critical.border.top.color == ft.Colors.with_opacity(
                0.19, C.CRITICAL
            ), theme_name
            assert calendar._mode_month_btn.bgcolor == ft.Colors.with_opacity(
                0.19, C.ACCENT
            ), theme_name
            assert all(
                label.color == (
                    C.CRITICAL if label.value == "CN" else C.TEXT_SECONDARY
                )
                for label in center.weekday_labels
            ), theme_name
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
