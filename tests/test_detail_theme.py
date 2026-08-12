from __future__ import annotations

from types import SimpleNamespace

import flet as ft

from gui.components.detail_view import DetailView
from gui.core.theme import C, apply_theme


def test_detail_deadline_panel_refreshes_from_dark_to_light_theme():
    page = SimpleNamespace(overlay=[], update=lambda: None)
    try:
        apply_theme("midnight_blue")
        view = DetailView(page, lambda: None)
        assert view._info_box.bgcolor == "#141B2D"

        apply_theme("paper_light")
        view.update_theme()

        assert view.bgcolor == C.BG
        assert view._info_box.bgcolor == C.SURFACE
        assert view._info_box.border == ft.Border.all(1, C.BORDER)
        assert view._details_divider.color == C.BORDER
        assert view._opentime_row.controls[0].color == C.TEXT_SECONDARY
        assert view._deadline_label.color == C.TEXT_SECONDARY
        assert view._deadline_txt.color == C.TEXT_PRIMARY
        assert view._countdown_label.color == C.TEXT_SECONDARY
    finally:
        apply_theme("midnight_blue")


def test_detail_theme_refresh_does_not_depend_on_content_tree_indexes():
    page = SimpleNamespace(overlay=[], update=lambda: None)
    try:
        apply_theme("midnight_blue")
        view = DetailView(page, lambda: None)
        view.content.controls.insert(2, ft.Container())

        apply_theme("paper_light")
        view.update_theme()

        assert view._info_box.bgcolor == C.SURFACE
        assert view._deadline_label.color == C.TEXT_SECONDARY
    finally:
        apply_theme("midnight_blue")
