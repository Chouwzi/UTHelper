from __future__ import annotations

import math
import os
import sys
from types import SimpleNamespace

import flet as ft

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.components.adaptive_filter_menu import (
    AdaptiveFilterMenu,
    FilterMenuOption,
)
from gui.core.theme import C


class _Page:
    def __init__(self):
        self.dialogs = []
        self.pops = 0
        self.height = 800

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        self.pops += 1


def _options():
    return [
        FilterMenuOption("all", "Tất cả", "#E2E8F0", count=12),
        FilterMenuOption("warning", "Sắp tới", "#F59E0B", count=3),
        FilterMenuOption("safe", "An toàn", "#10B981", count=9),
    ]


def test_desktop_uses_app_coordinated_popup_with_checked_item():
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Mức độ",
        options=_options(),
        selected_key="warning",
        on_select=lambda _key: None,
        mobile=False,
    )

    assert isinstance(menu.control, ft.Semantics)
    assert isinstance(menu.popup_panel, ft.Container)
    assert all(isinstance(item, ft.Container) for item in menu.desktop_items)
    assert [item.height for item in menu.desktop_items] == [48, 48, 48]
    assert [
        item.content.controls[0].content.visible for item in menu.desktop_items
    ] == [False, True, False]


def test_filter_chevron_matches_schedule_disclosure_motion_on_desktop():
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Mức độ",
        options=_options(),
        selected_key="all",
        on_select=lambda _key: None,
        mobile=False,
    )

    assert menu.arrow.icon == ft.Icons.CHEVRON_RIGHT_ROUNDED
    assert menu.arrow.rotate.angle == 0


def test_desktop_hover_never_opens_or_closes_filter_popup():
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Mức độ",
        options=_options(),
        selected_key="all",
        on_select=lambda _key: None,
        mobile=False,
    )
    toggles = []
    menu.on_desktop_toggle = lambda target, event: toggles.append((target, event))

    menu.trigger.on_hover(SimpleNamespace(data="true"))
    menu.trigger.on_hover(SimpleNamespace(data="false"))

    assert toggles == []
    assert menu._expanded is False
    assert menu.arrow.rotate.angle == 0


def test_desktop_click_is_the_only_popup_toggle_event():
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Mức độ",
        options=_options(),
        selected_key="all",
        on_select=lambda _key: None,
        mobile=False,
    )
    event = SimpleNamespace(
        global_position=ft.Offset(80, 160),
        local_position=ft.Offset(20, 20),
    )
    toggles = []
    menu.on_desktop_toggle = lambda target, received: toggles.append(
        (target, received)
    )

    menu.trigger.on_tap_down(event)
    assert toggles == []
    menu.trigger.on_click(SimpleNamespace())

    assert toggles == [(menu, event)]


def test_flexible_trigger_fills_the_width_allocated_by_filter_row():
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Môn học",
        options=[FilterMenuOption("all", "Tất cả môn học", "#FFFFFF")],
        selected_key="all",
        on_select=lambda _key: None,
        mobile=False,
        trigger_width=None,
        popup_width=340,
    )

    assert menu.trigger.expand is True
    assert menu.label.expand is True
    assert menu.trigger.width is None
    assert menu.popup_panel.width == 340
    menu._set_expanded(True)
    assert menu.arrow.rotate.angle == math.pi / 2
    assert menu.semantics.expanded is True
    menu._set_expanded(False)
    assert menu.arrow.rotate.angle == 0
    assert menu.semantics.expanded is False


def test_trigger_avoids_flet_double_padding_text_clip_regression():
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Loại hoạt động",
        options=_options(),
        selected_key="all",
        on_select=lambda _key: None,
        mobile=False,
        trigger_width=104,
    )

    # Flet 0.86.x double-applies Container.padding when ink, an event handler,
    # and animate are all present. Do not restore trigger animation unless its
    # padding is moved into a non-animated child and verified in a real renderer.
    assert menu.trigger.ink is True
    assert menu.trigger.on_hover is not None
    assert menu.trigger.animate is None
    assert menu.arrow.animate_rotation is not None
    assert menu.popup_panel.shadow is not None


def test_trigger_exposes_filter_and_selection_to_accessibility():
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Loại hoạt động",
        options=_options(),
        selected_key="safe",
        on_select=lambda _key: None,
        mobile=False,
    )

    assert menu.semantics.button is True
    assert menu.semantics.label == "Loại hoạt động: An toàn"
    assert menu.semantics.on_tap_hint_text == "Mở bộ lọc Loại hoạt động"


def test_selection_updates_label_checked_state_and_calls_once():
    selected = []
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Mức độ",
        options=_options(),
        selected_key="all",
        on_select=selected.append,
        mobile=False,
    )

    assert menu.select("warning") is True
    assert menu.selected_key == "warning"
    assert menu.label.value == "Sắp tới"
    assert [
        item.content.controls[0].content.visible for item in menu.desktop_items
    ] == [False, True, False]
    assert selected == ["warning"]
    assert menu.select("warning") is False
    assert selected == ["warning"]


def test_option_update_reuses_controls_when_signature_is_unchanged():
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Mức độ",
        options=_options(),
        selected_key="all",
        on_select=lambda _key: None,
        mobile=False,
    )
    item_ids = [id(item) for item in menu.desktop_items]

    assert menu.set_options(_options()) is False
    assert [id(item) for item in menu.desktop_items] == item_ids


def test_option_update_changes_counts_without_replacing_selection():
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Mức độ",
        options=_options(),
        selected_key="warning",
        on_select=lambda _key: None,
        mobile=False,
    )
    updated = [
        FilterMenuOption("all", "Tất cả", "#E2E8F0", count=20),
        FilterMenuOption("warning", "Sắp tới", "#F59E0B", count=5),
        FilterMenuOption("safe", "An toàn", "#10B981", count=15),
    ]

    assert menu.set_options(updated) is True
    assert menu.selected_key == "warning"
    assert menu.desktop_items[1].data == "warning"
    assert "5" in menu.desktop_items[1].content.controls[-1].value


def test_missing_dynamic_selection_falls_back_to_first_option_without_callback():
    selected = []
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Môn học",
        options=[
            FilterMenuOption("all", "Tất cả môn học", "#E2E8F0"),
            FilterMenuOption("course-a", "Course A", "#E2E8F0"),
        ],
        selected_key="course-a",
        on_select=selected.append,
        mobile=False,
    )

    menu.set_options(
        [FilterMenuOption("all", "Tất cả môn học", "#E2E8F0")]
    )

    assert menu.selected_key == "all"
    assert menu.label.value == "Tất cả môn học"
    assert selected == []


def test_long_trigger_label_is_single_line_ellipsis():
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Môn học",
        options=[
            FilterMenuOption(
                "course",
                "Tên môn học rất dài cần được cắt gọn trên nút",
                "#E2E8F0",
            )
        ],
        selected_key="course",
        on_select=lambda _key: None,
        mobile=False,
        trigger_width=112,
    )

    assert menu.label.max_lines == 1
    assert menu.label.overflow == ft.TextOverflow.ELLIPSIS
    assert menu.trigger.width == 112
    assert menu.label.expand is False
    assert menu.label.width == 70
    assert menu.trigger.content.tight is True


def test_course_variant_wraps_options_and_exposes_full_tooltip():
    label = "Hệ quản trị cơ sở dữ liệu - CNS_CS1"
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Môn học",
        options=[FilterMenuOption("course", label, "#E2E8F0", count=20)],
        selected_key="course",
        on_select=lambda _key: None,
        mobile=False,
        popup_width=340,
        option_max_lines=2,
    )

    item = menu.desktop_items[0]
    assert menu.popup_panel.bgcolor != menu.trigger.bgcolor
    assert item.height == 62
    assert item.tooltip == label
    assert item.content.controls[2].max_lines == 2


def test_mobile_uses_scrollable_safe_bottom_sheet_with_large_rows():
    page = _Page()
    menu = AdaptiveFilterMenu(
        page=page,
        title="Môn học",
        options=_options(),
        selected_key="all",
        on_select=lambda _key: None,
        mobile=True,
    )

    assert menu.popup_panel is None
    menu.open()
    sheet = page.dialogs[-1]
    assert isinstance(sheet, ft.BottomSheet)
    assert sheet.scrollable is True
    assert sheet.use_safe_area is True
    assert sheet.draggable is True
    assert sheet.show_drag_handle is True
    assert sheet.animation_style is not None
    assert all(tile.min_height >= 52 for tile in menu.mobile_tiles)
    assert sheet.content.height == 236
    assert sheet.content.height < page.height
    assert all(tile.hover_color == C.SURFACE_HOVER for tile in menu.mobile_tiles)
    assert all(
        tile.selected_tile_color == C.SURFACE_HOVER
        for tile in menu.mobile_tiles
    )
    assert menu.arrow.rotate.angle == math.pi / 2
    assert menu.semantics.expanded is True
    sheet.on_dismiss(None)
    assert menu.arrow.rotate.angle == 0
    assert menu.semantics.expanded is False


def test_mobile_sheet_caps_long_option_lists_to_viewport_and_keeps_scroll():
    page = _Page()
    options = [
        FilterMenuOption(str(index), f"Môn học {index}", "#3B82F6")
        for index in range(20)
    ]
    menu = AdaptiveFilterMenu(
        page=page,
        title="Môn học",
        options=options,
        selected_key="0",
        on_select=lambda _key: None,
        mobile=True,
        option_max_lines=2,
    )

    menu.open()
    sheet = page.dialogs[-1]

    assert sheet.content.height == 560
    assert sheet.scrollable is True
    assert sheet.content.content.scroll == ft.ScrollMode.AUTO


def test_mobile_selection_closes_sheet_after_applying():
    page = _Page()
    selected = []
    menu = AdaptiveFilterMenu(
        page=page,
        title="Mức độ",
        options=_options(),
        selected_key="all",
        on_select=selected.append,
        mobile=True,
    )
    menu.open()

    menu.mobile_tiles[1].on_click(None)

    assert menu.selected_key == "warning"
    assert selected == ["warning"]
    assert page.pops == 1


def test_tapping_current_mobile_selection_closes_without_duplicate_callback():
    page = _Page()
    selected = []
    menu = AdaptiveFilterMenu(
        page=page,
        title="Mức độ",
        options=_options(),
        selected_key="all",
        on_select=selected.append,
        mobile=True,
    )
    menu.open()

    menu.mobile_tiles[0].on_click(None)

    assert selected == []
    assert page.pops == 1
