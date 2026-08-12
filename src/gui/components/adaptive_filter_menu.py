from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable

import flet as ft

from gui.core.theme import C


@dataclass(frozen=True, slots=True)
class FilterMenuOption:
    key: str
    label: str
    color: str
    count: int | None = None
    icon: object | None = None
    trigger_label: str | None = None


class AdaptiveFilterMenu:
    """Single-select filter rendered as a desktop popup or mobile sheet."""

    _MOTION_MS = 180
    _SHEET_MAX_HEIGHT = 560
    _SHEET_MAX_VIEWPORT_RATIO = 0.72
    _SHEET_HEADER_HEIGHT = 44
    _SHEET_BOTTOM_PADDING = 16
    _SHEET_OPTION_SPACING = 4

    def __init__(
        self,
        *,
        page,
        title: str,
        options: Iterable[FilterMenuOption],
        selected_key: str,
        on_select: Callable[[str], None],
        mobile: bool,
        trigger_width: int | float | None = None,
        popup_width: int | float = 240,
        option_max_lines: int = 1,
    ) -> None:
        self.page = page
        self.title = title
        self.mobile = mobile
        self.on_select = on_select
        self.trigger_width = trigger_width
        self.popup_width = popup_width
        self.option_max_lines = max(1, min(int(option_max_lines), 2))
        self.option_height = 48 if self.option_max_lines == 1 else 62
        self.options: tuple[FilterMenuOption, ...] = ()
        self.selected_key = selected_key
        self._signature: tuple = ()
        self._expanded = False
        self._sheet: ft.BottomSheet | None = None
        self.mobile_tiles: list[ft.ListTile] = []

        label_width = None
        if trigger_width is not None:
            # PopupMenuButton/Semantics do not always propagate a useful flex
            # constraint to an expanded Text. Reserve the arrow and padding
            # explicitly so short labels do not collapse into a lone ellipsis.
            label_width = max(24, float(trigger_width) - 42)
        self.label = ft.Text(
            "",
            size=12,
            color=C.TEXT_PRIMARY,
            weight=ft.FontWeight.W_600,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            width=label_width,
            expand=trigger_width is None,
        )
        self.arrow = ft.Icon(
            ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED,
            size=16,
            color=C.TEXT_SECONDARY,
            rotate=ft.Rotate(0),
            animate_rotation=ft.Animation(
                self._MOTION_MS, ft.AnimationCurve.EASE_OUT_CUBIC
            ),
        )
        self.trigger = ft.Container(
            content=ft.Row(
                [self.label, self.arrow],
                spacing=4,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            width=trigger_width,
            height=48 if mobile else 40,
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=11, vertical=8),
            ink=True,
            # Flet 0.86.x applies padding in both the inner InkWell container
            # and the outer AnimatedContainer when ink, an event handler and
            # animate are combined. With a fixed 40 px trigger this leaves only
            # 8 px for text and clips Vietnamese glyphs. Keep hover/ripple here;
            # arrow and popup motion remain independently animated.
            animate=None,
            on_hover=self._on_trigger_hover,
            on_click=(lambda _event: self.open()) if mobile else None,
        )

        self.popup: ft.PopupMenuButton | None
        if mobile:
            self.popup = None
            interactive_control = self.trigger
        else:
            self.popup = ft.PopupMenuButton(
                content=self.trigger,
                items=[],
                menu_position=ft.PopupMenuPosition.UNDER,
                shape=ft.RoundedRectangleBorder(
                    radius=12,
                    side=ft.BorderSide(1, C.BORDER),
                ),
                bgcolor=C.BG,
                elevation=12,
                shadow_color=C.SHADOW,
                menu_padding=ft.Padding.symmetric(horizontal=6, vertical=6),
                size_constraints=ft.BoxConstraints(
                    min_width=popup_width,
                    max_width=popup_width,
                    max_height=420,
                ),
                enable_feedback=True,
                popup_animation_style=ft.AnimationStyle(
                    duration=self._MOTION_MS,
                    reverse_duration=140,
                    curve=ft.AnimationCurve.EASE_OUT_CUBIC,
                    reverse_curve=ft.AnimationCurve.EASE_IN_CUBIC,
                ),
                on_open=lambda _event: self._set_expanded(True),
                on_cancel=lambda _event: self._set_expanded(False),
                on_select=lambda _event: self._set_expanded(False),
            )
            interactive_control = self.popup

        self.control = ft.Semantics(
            content=interactive_control,
            button=True,
            focusable=True,
            expanded=False,
            label=title,
            on_tap_hint_text=f"Mở bộ lọc {title}",
        )
        self.set_options(options, notify=False)

    @staticmethod
    def _options_signature(options: tuple[FilterMenuOption, ...]) -> tuple:
        return tuple(
            (
                option.key,
                option.label,
                option.color,
                option.count,
                str(option.icon),
                option.trigger_label,
            )
            for option in options
        )

    def _selected_option(self) -> FilterMenuOption:
        for option in self.options:
            if option.key == self.selected_key:
                return option
        return self.options[0]

    def _sync_trigger(self) -> None:
        option = self._selected_option()
        self.label.value = option.trigger_label or option.label
        self.label.color = option.color if option.key != self.options[0].key else C.TEXT_PRIMARY
        self.trigger.border = ft.Border.all(
            1,
            option.color if option.key != self.options[0].key else C.BORDER,
        )
        self.trigger.bgcolor = (
            ft.Colors.with_opacity(0.07, option.color)
            if option.key != self.options[0].key
            else C.SURFACE
        )
        self.control.label = f"{self.title}: {option.label}"
        self.control.value = option.label

    def _build_popup_items(self) -> list[ft.PopupMenuItem]:
        items = []
        for option in self.options:
            marker = (
                ft.Icon(option.icon, size=17, color=option.color)
                if option.icon is not None
                else ft.Container(
                    width=8,
                    height=8,
                    border_radius=4,
                    bgcolor=option.color,
                )
            )
            count_text = "" if option.count is None else str(max(0, option.count))
            content = ft.Row(
                [
                    marker,
                    ft.Text(
                        option.label,
                        size=12,
                        color=C.TEXT_PRIMARY,
                        weight=(
                            ft.FontWeight.W_600
                            if option.key == self.selected_key
                            else ft.FontWeight.W_500
                        ),
                        max_lines=self.option_max_lines,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        expand=True,
                    ),
                    ft.Text(count_text, size=11, color=C.TEXT_SECONDARY),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            items.append(
                ft.PopupMenuItem(
                    content=content,
                    checked=option.key == self.selected_key,
                    height=self.option_height,
                    padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                    mouse_cursor=ft.MouseCursor.CLICK,
                    data=option.key,
                    tooltip=option.label,
                    on_click=lambda _event, key=option.key: self.select(key),
                )
            )
        return items

    def _build_mobile_tiles(self) -> list[ft.ListTile]:
        tiles = []
        for option in self.options:
            selected = option.key == self.selected_key
            leading = (
                ft.Icon(option.icon, size=20, color=option.color)
                if option.icon is not None
                else ft.Container(
                    width=10,
                    height=10,
                    border_radius=5,
                    bgcolor=option.color,
                )
            )
            count = None
            if option.count is not None:
                count = ft.Text(str(max(0, option.count)), size=12, color=C.TEXT_SECONDARY)
            trailing = ft.Row(
                [
                    *([count] if count is not None else []),
                    ft.Icon(
                        ft.Icons.CHECK_ROUNDED,
                        size=20,
                        color=option.color,
                        visible=selected,
                    ),
                ],
                spacing=12,
                tight=True,
            )
            tiles.append(
                ft.ListTile(
                    title=ft.Text(
                        option.label,
                        size=14,
                        weight=(
                            ft.FontWeight.W_600 if selected else ft.FontWeight.W_500
                        ),
                        max_lines=self.option_max_lines,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    leading=leading,
                    trailing=trailing,
                    selected=selected,
                    selected_color=C.TEXT_PRIMARY,
                    selected_tile_color=C.SURFACE_HOVER,
                    hover_color=C.SURFACE_HOVER,
                    splash_color=ft.Colors.with_opacity(0.12, C.ACCENT),
                    content_padding=ft.Padding.symmetric(horizontal=18, vertical=2),
                    min_height=56 if self.option_max_lines == 1 else 68,
                    shape=ft.RoundedRectangleBorder(radius=10),
                    enable_feedback=True,
                    on_click=lambda _event, key=option.key: self._select_from_sheet(key),
                )
            )
        return tiles

    def _mobile_sheet_height(self) -> float:
        rows_height = sum(float(tile.min_height or 0) for tile in self.mobile_tiles)
        rows_height += self._SHEET_OPTION_SPACING * max(
            0, len(self.mobile_tiles) - 1
        )
        content_height = (
            self._SHEET_HEADER_HEIGHT
            + rows_height
            + self._SHEET_BOTTOM_PADDING
        )
        viewport_height = getattr(self.page, "height", None)
        if isinstance(viewport_height, (int, float)) and viewport_height > 0:
            max_height = min(
                self._SHEET_MAX_HEIGHT,
                viewport_height * self._SHEET_MAX_VIEWPORT_RATIO,
            )
        else:
            max_height = self._SHEET_MAX_HEIGHT
        return min(content_height, max_height)

    def _build_sheet(self) -> ft.BottomSheet:
        self.mobile_tiles = self._build_mobile_tiles()
        body = ft.Column(
            [
                ft.Container(
                    content=ft.Text(
                        self.title,
                        size=16,
                        weight=ft.FontWeight.W_700,
                        color=C.TEXT_PRIMARY,
                    ),
                    padding=ft.Padding.only(left=18, right=18, bottom=8),
                ),
                ft.Column(self.mobile_tiles, spacing=4, tight=True),
            ],
            spacing=4,
            tight=True,
            scroll=ft.ScrollMode.AUTO,
        )
        return ft.BottomSheet(
            content=ft.Container(
                content=body,
                bgcolor=C.BG,
                padding=ft.Padding.only(bottom=16),
                height=self._mobile_sheet_height(),
            ),
            bgcolor=C.BG,
            dismissible=True,
            draggable=True,
            show_drag_handle=True,
            use_safe_area=True,
            scrollable=True,
            maintain_bottom_view_insets_padding=True,
            elevation=12,
            barrier_color=C.SCRIM,
            shape=ft.RoundedRectangleBorder(radius=18),
            animation_style=ft.AnimationStyle(
                duration=220,
                reverse_duration=180,
                curve=ft.AnimationCurve.EASE_OUT_CUBIC,
                reverse_curve=ft.AnimationCurve.EASE_IN_CUBIC,
            ),
            on_dismiss=lambda _event: self._set_expanded(False),
        )

    @staticmethod
    def _safe_update(control) -> None:
        try:
            control.update()
        except (AssertionError, RuntimeError):
            pass

    def _set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.arrow.rotate = ft.Rotate(0.5 if expanded else 0)
        self.control.expanded = expanded
        self._safe_update(self.arrow)
        self._safe_update(self.control)

    def _on_trigger_hover(self, event) -> None:
        hovered = str(getattr(event, "data", "false")).lower() == "true"
        option = self._selected_option()
        if hovered:
            self.trigger.bgcolor = (
                ft.Colors.with_opacity(0.10, option.color)
                if option.key != self.options[0].key
                else C.BORDER
            )
        else:
            self.trigger.bgcolor = (
                ft.Colors.with_opacity(0.07, option.color)
                if option.key != self.options[0].key
                else C.SURFACE
            )
        self._safe_update(self.trigger)

    def set_options(
        self, options: Iterable[FilterMenuOption], *, notify: bool = False
    ) -> bool:
        new_options = tuple(options)
        if not new_options:
            raise ValueError("AdaptiveFilterMenu requires at least one option")
        signature = self._options_signature(new_options)
        if signature == self._signature:
            return False

        self.options = new_options
        self._signature = signature
        if self.selected_key not in {option.key for option in self.options}:
            self.selected_key = self.options[0].key
            if notify:
                self.on_select(self.selected_key)
        self._sync_trigger()
        if self.popup is not None:
            self.popup.items = self._build_popup_items()
        if self._sheet is not None:
            self._sheet = self._build_sheet()
        return True

    def select(self, key: str) -> bool:
        return self.set_selected(key, notify=True)

    def set_selected(self, key: str, *, notify: bool = False) -> bool:
        if key == self.selected_key:
            self._set_expanded(False)
            return False
        if key not in {option.key for option in self.options}:
            return False
        self.selected_key = key
        self._sync_trigger()
        if self.popup is not None:
            for item in self.popup.items:
                item.checked = item.data == key
                label = item.content.controls[1]
                label.weight = (
                    ft.FontWeight.W_600 if item.data == key else ft.FontWeight.W_500
                )
            self._safe_update(self.popup)
        if self._sheet is not None:
            self._sheet = self._build_sheet()
        self._set_expanded(False)
        if notify:
            self.on_select(key)
        return True

    def _select_from_sheet(self, key: str) -> None:
        if key in {option.key for option in self.options}:
            self.select(key)
            self.page.pop_dialog()

    def open(self) -> None:
        if not self.mobile:
            return
        self._sheet = self._build_sheet()
        self._set_expanded(True)
        self.page.show_dialog(self._sheet)

    def with_counts(self, counts: dict[str, int]) -> bool:
        return self.set_options(
            replace(option, count=counts.get(option.key, 0))
            for option in self.options
        )

    def refresh_theme(self, options: Iterable[FilterMenuOption] | None = None) -> None:
        self.arrow.color = C.TEXT_SECONDARY
        self.trigger.bgcolor = C.SURFACE
        if self.popup is not None:
            self.popup.bgcolor = C.BG
            self.popup.shape = ft.RoundedRectangleBorder(
                radius=12,
                side=ft.BorderSide(1, C.BORDER),
            )
        if options is None:
            options = self.options
        self._signature = ()
        self.set_options(options)
