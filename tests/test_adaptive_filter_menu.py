from __future__ import annotations

import os
import sys

import flet as ft

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.components.adaptive_filter_menu import (
    AdaptiveFilterMenu,
    FilterMenuOption,
)


class _Page:
    def __init__(self):
        self.dialogs = []
        self.pops = 0

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


def test_desktop_uses_anchored_popup_with_native_animation_and_checked_item():
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Mức độ",
        options=_options(),
        selected_key="warning",
        on_select=lambda _key: None,
        mobile=False,
    )

    assert isinstance(menu.control, ft.Semantics)
    assert isinstance(menu.popup, ft.PopupMenuButton)
    assert menu.popup.menu_position == ft.PopupMenuPosition.UNDER
    assert menu.popup.popup_animation_style is not None
    assert [item.height for item in menu.popup.items] == [48, 48, 48]
    assert [item.checked for item in menu.popup.items] == [False, True, False]


def test_trigger_exposes_filter_and_selection_to_accessibility():
    menu = AdaptiveFilterMenu(
        page=_Page(),
        title="Loại hoạt động",
        options=_options(),
        selected_key="safe",
        on_select=lambda _key: None,
        mobile=False,
    )

    assert menu.control.button is True
    assert menu.control.label == "Loại hoạt động: An toàn"
    assert menu.control.on_tap_hint_text == "Mở bộ lọc Loại hoạt động"


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
    assert [item.checked for item in menu.popup.items] == [False, True, False]
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
    item_ids = [id(item) for item in menu.popup.items]

    assert menu.set_options(_options()) is False
    assert [id(item) for item in menu.popup.items] == item_ids


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
    assert menu.popup.items[1].data == "warning"
    assert "5" in menu.popup.items[1].content.controls[-1].value


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

    assert menu.popup is None
    menu.open()
    sheet = page.dialogs[-1]
    assert isinstance(sheet, ft.BottomSheet)
    assert sheet.scrollable is True
    assert sheet.use_safe_area is True
    assert sheet.draggable is True
    assert sheet.show_drag_handle is True
    assert sheet.animation_style is not None
    assert all(tile.min_height >= 52 for tile in menu.mobile_tiles)


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
