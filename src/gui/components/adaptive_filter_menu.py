from __future__ import annotations

import math
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
    """Single-select filter rendered as a coordinated popup or mobile sheet."""

    _ARROW_MOTION_MS = 260
    _EXPANDED_ARROW_ANGLE = math.pi / 2
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
        self.desktop_items: list[ft.Container] = []
        self._desktop_item_parts: dict[str, tuple[ft.Icon, ft.Text]] = {}
        self._last_desktop_tap_event = None
        self.on_desktop_toggle: (
            Callable[["AdaptiveFilterMenu", object], None] | None
        ) = None
        self.on_desktop_dismiss: Callable[["AdaptiveFilterMenu"], None] | None = None

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
            ft.Icons.CHEVRON_RIGHT_ROUNDED,
            size=18,
            color=C.TEXT_SECONDARY,
            rotate=ft.Rotate(angle=0, alignment=ft.Alignment.CENTER),
            animate_rotation=ft.Animation(
                self._ARROW_MOTION_MS, ft.AnimationCurve.EASE_OUT_CUBIC
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
            expand=trigger_width is None,
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
            on_click=(lambda _event: self.open()) if mobile else self._on_desktop_click,
            on_tap_down=None if mobile else self._on_desktop_tap_down,
        )

        self.semantics = ft.Semantics(
            content=self.trigger,
            button=True,
            focusable=True,
            expanded=False,
            label=title,
            on_tap_hint_text=f"Mở bộ lọc {title}",
        )
        self.control = self.semantics
        self.popup_panel: ft.Container | None = None
        if not mobile:
            self.popup_panel = self._build_desktop_panel()
        self.set_options(options, notify=False)

    def _build_desktop_panel(self) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                controls=[],
                spacing=0,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
            width=self.popup_width,
            bgcolor=C.BG,
            padding=ft.Padding.symmetric(horizontal=6, vertical=6),
            border=ft.Border.all(1, C.BORDER),
            border_radius=12,
            shadow=ft.BoxShadow(
                blur_radius=20,
                spread_radius=0,
                color=C.SHADOW,
                offset=ft.Offset(0, 8),
            ),
        )

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
        self.semantics.label = f"{self.title}: {option.label}"
        self.semantics.value = option.label

    def _build_desktop_items(self) -> list[ft.Container]:
        items = []
        self._desktop_item_parts = {}
        for option in self.options:
            selected = option.key == self.selected_key
            check = ft.Icon(
                ft.Icons.CHECK_ROUNDED,
                size=18,
                color=option.color,
                visible=selected,
            )
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
            label = ft.Text(
                option.label,
                size=12,
                color=C.TEXT_PRIMARY,
                weight=(ft.FontWeight.W_600 if selected else ft.FontWeight.W_500),
                max_lines=self.option_max_lines,
                overflow=ft.TextOverflow.ELLIPSIS,
                expand=True,
            )
            content = ft.Row(
                [
                    ft.Container(
                        content=check,
                        width=20,
                        alignment=ft.Alignment.CENTER,
                    ),
                    marker,
                    label,
                    ft.Text(count_text, size=11, color=C.TEXT_SECONDARY),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            item = ft.Container(
                content=content,
                height=self.option_height,
                padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                border_radius=8,
                ink=True,
                data=option.key,
                tooltip=option.label,
                on_hover=self._on_desktop_item_hover,
                on_click=lambda _event, key=option.key: self.select(key),
            )
            items.append(item)
            self._desktop_item_parts[option.key] = (check, label)
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
        self.arrow.rotate = ft.Rotate(
            angle=self._EXPANDED_ARROW_ANGLE if expanded else 0,
            alignment=ft.Alignment.CENTER,
        )
        self.semantics.expanded = expanded
        self._safe_update(self.arrow)
        self._safe_update(self.semantics)

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

    def _on_desktop_tap_down(self, event) -> None:
        self._last_desktop_tap_event = event

    def _on_desktop_click(self, event) -> None:
        """Toggle only on click; hover merely updates the trigger color."""
        if self.on_desktop_toggle is not None:
            anchor_event = self._last_desktop_tap_event or event
            self._last_desktop_tap_event = None
            self.on_desktop_toggle(self, anchor_event)

    def _on_desktop_item_hover(self, event) -> None:
        hovered = str(getattr(event, "data", "false")).lower() == "true"
        item = getattr(event, "control", None)
        if item is None:
            return
        item.bgcolor = C.SURFACE_HOVER if hovered else ft.Colors.TRANSPARENT
        self._safe_update(item)

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
        if self.popup_panel is not None:
            self.desktop_items = self._build_desktop_items()
            self.popup_panel.content.controls = self.desktop_items
            self.popup_panel.height = min(
                420,
                12 + len(self.desktop_items) * self.option_height,
            )
        if self._sheet is not None:
            self._sheet = self._build_sheet()
        return True

    def select(self, key: str) -> bool:
        return self.set_selected(key, notify=True)

    def set_selected(self, key: str, *, notify: bool = False) -> bool:
        if key == self.selected_key:
            self._dismiss_desktop_or_collapse()
            return False
        if key not in {option.key for option in self.options}:
            return False
        self.selected_key = key
        self._sync_trigger()
        if self.popup_panel is not None:
            for item_key, (check, label) in self._desktop_item_parts.items():
                selected = item_key == key
                check.visible = selected
                label.weight = (
                    ft.FontWeight.W_600 if selected else ft.FontWeight.W_500
                )
            self._safe_update(self.popup_panel)
        if self._sheet is not None:
            self._sheet = self._build_sheet()
        self._dismiss_desktop_or_collapse()
        if notify:
            self.on_select(key)
        return True

    def _dismiss_desktop_or_collapse(self) -> None:
        if (
            not self.mobile
            and self._expanded
            and self.on_desktop_dismiss is not None
        ):
            self.on_desktop_dismiss(self)
            return
        self._set_expanded(False)

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
        if self.popup_panel is not None:
            self.popup_panel.bgcolor = C.BG
            self.popup_panel.border = ft.Border.all(1, C.BORDER)
            self.popup_panel.shadow = ft.BoxShadow(
                blur_radius=20,
                spread_radius=0,
                color=C.SHADOW,
                offset=ft.Offset(0, 8),
            )
        if options is None:
            options = self.options
        self._signature = ()
        self.set_options(options)
