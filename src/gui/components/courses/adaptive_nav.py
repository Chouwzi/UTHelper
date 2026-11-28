"""Adaptive six-item desktop rail and five-item mobile bottom bar."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import flet as ft

from gui.core.theme import C

RAIL_BREAKPOINT = 600
RAIL_WIDTH = 88
RAIL_SCROLL_HEIGHT_BREAKPOINT = 420


@dataclass(frozen=True, slots=True)
class NavDestinationSpec:
    """Declarative specification for a top-level workspace destination."""

    key: str
    label: str
    icon: ft.IconData
    selected_icon: ft.IconData


NAV_DESTINATIONS: tuple[NavDestinationSpec, ...] = (
    NavDestinationSpec(
        "home",
        "Tổng quan",
        ft.Icons.DASHBOARD_OUTLINED,
        ft.Icons.DASHBOARD_ROUNDED,
    ),
    NavDestinationSpec(
        "courses",
        "Môn học",
        ft.Icons.MENU_BOOK_OUTLINED,
        ft.Icons.MENU_BOOK_ROUNDED,
    ),
    NavDestinationSpec(
        "calendar",
        "Lịch",
        ft.Icons.CALENDAR_MONTH_OUTLINED,
        ft.Icons.CALENDAR_MONTH_ROUNDED,
    ),
    NavDestinationSpec(
        "grades",
        "Bảng điểm",
        ft.Icons.INSIGHTS_OUTLINED,
        ft.Icons.INSIGHTS_ROUNDED,
    ),
    NavDestinationSpec(
        "profile",
        "Hồ sơ",
        ft.Icons.BADGE_OUTLINED,
        ft.Icons.BADGE_ROUNDED,
    ),
    NavDestinationSpec(
        "settings",
        "Cài đặt",
        ft.Icons.SETTINGS_OUTLINED,
        ft.Icons.SETTINGS_ROUNDED,
    ),
)
_KEY_TO_INDEX: dict[str, int] = {
    spec.key: idx for idx, spec in enumerate(NAV_DESTINATIONS)
}
MOBILE_NAV_DESTINATIONS: tuple[NavDestinationSpec, ...] = tuple(
    spec for spec in NAV_DESTINATIONS if spec.key != "profile"
)
_MOBILE_KEY_TO_INDEX: dict[str, int] = {
    spec.key: idx for idx, spec in enumerate(MOBILE_NAV_DESTINATIONS)
}


class AdaptiveNavShell:
    """Coordinates left NavigationRail (>= 600px) and bottom NavigationBar (< 600px)."""

    def __init__(self, *, on_navigate: Callable[[str], None]) -> None:
        self._on_navigate = on_navigate
        self.rail_visible = False
        self._immersive = False
        self._compact_rail_height = False
        indicator = ft.Colors.with_opacity(0.18, C.ACCENT)
        self.rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=RAIL_WIDTH,
            bgcolor=C.SURFACE,
            indicator_color=indicator,
            selected_label_text_style=ft.TextStyle(
                color=C.ACCENT, size=11, weight=ft.FontWeight.W_600
            ),
            unselected_label_text_style=ft.TextStyle(
                color=C.TEXT_SECONDARY, size=11
            ),
            destinations=[
                ft.NavigationRailDestination(
                    icon=spec.icon,
                    selected_icon=spec.selected_icon,
                    label=spec.label,
                )
                for spec in NAV_DESTINATIONS
            ],
            on_change=self._handle_change,
        )
        self.rail_host = ft.Container(
            content=self.rail,
            width=RAIL_WIDTH,
            bgcolor=C.SURFACE,
            border=ft.Border.only(right=ft.BorderSide(1, C.BORDER)),
            visible=False,
        )
        # Permanently visible slot keeps sibling main_stack at index 1 in Row
        # across <600dp <-> >=600dp (Samsung Pop-up View) so PlatformViews never unmount.
        self.rail_slot = ft.Container(
            content=self.rail_host,
            width=0,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            visible=True,
        )
        self.bar = ft.NavigationBar(
            selected_index=0,
            height=64,
            bgcolor=C.SURFACE,
            indicator_color=indicator,
            border=ft.Border.only(top=ft.BorderSide(1, C.BORDER)),
            label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW,
            label_padding=ft.Padding.symmetric(horizontal=1, vertical=0),
            destinations=[
                ft.NavigationBarDestination(
                    icon=spec.icon,
                    selected_icon=spec.selected_icon,
                    label=spec.label,
                )
                for spec in MOBILE_NAV_DESTINATIONS
            ],
            visible=True,
            on_change=self._handle_bar_change,
        )

    def _handle_change(self, e) -> None:
        control = getattr(e, "control", None)
        raw_idx = getattr(control, "selected_index", None)
        if raw_idx is None:
            raw_idx = getattr(e, "data", None)
        if raw_idx is None or isinstance(raw_idx, bool):
            return
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            return
        if 0 <= idx < len(NAV_DESTINATIONS):
            key = NAV_DESTINATIONS[idx].key
            if key in _KEY_TO_INDEX:
                self._on_navigate(key)

    def _handle_bar_change(self, e) -> None:
        control = getattr(e, "control", None)
        raw_idx = getattr(control, "selected_index", None)
        if raw_idx is None:
            raw_idx = getattr(e, "data", None)
        if raw_idx is None or isinstance(raw_idx, bool):
            return
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            return
        if 0 <= idx < len(MOBILE_NAV_DESTINATIONS):
            self._on_navigate(MOBILE_NAV_DESTINATIONS[idx].key)

    def set_active(self, key: str) -> None:
        """Synchronize the selected destination across both rail and bottom bar."""
        if key not in _KEY_TO_INDEX:
            return
        rail_idx = _KEY_TO_INDEX[key]
        if self.rail.selected_index != rail_idx:
            self.rail.selected_index = rail_idx
        # The mobile bar has five slots; profile remains reachable from
        # Settings → Account and keeps Settings selected as its entry point.
        bar_idx = _MOBILE_KEY_TO_INDEX.get(key, _MOBILE_KEY_TO_INDEX["settings"])
        if self.bar.selected_index != bar_idx:
            self.bar.selected_index = bar_idx

    def apply_width(self, width: float) -> bool:
        """Switch between NavigationRail (>= 600px) and NavigationBar (< 600px)."""
        use_rail = False
        if width is not None and not isinstance(width, bool):
            try:
                numeric = float(width)
                use_rail = math.isfinite(numeric) and numeric >= RAIL_BREAKPOINT
            except (TypeError, ValueError):
                use_rail = False
        self.rail_visible = use_rail
        self.rail_host.visible = use_rail
        self.rail_slot.width = None if use_rail else 0
        self.bar.visible = (not use_rail) and (not self._immersive)
        return use_rail

    def apply_height(self, height: object) -> bool:
        """Enable scrollable NavigationRail when viewport height is below 420dp."""
        scrollable = False
        if height is not None and not isinstance(height, bool):
            try:
                numeric = float(height)
                scrollable = math.isfinite(numeric) and 0 < numeric < RAIL_SCROLL_HEIGHT_BREAKPOINT
            except (TypeError, ValueError):
                scrollable = False
        self._compact_rail_height = scrollable
        self.rail.scrollable = scrollable
        return scrollable

    def set_immersive(self, immersive: bool) -> None:
        """Collapse bottom NavigationBar cleanly during mobile immersive overlays."""
        self._immersive = bool(immersive)
        self.bar.visible = (not self.rail_visible) and (not self._immersive)

    def refresh_theme(self) -> None:
        """Repaint navigation surfaces, borders, indicator, and labels from C tokens."""
        indicator = ft.Colors.with_opacity(0.18, C.ACCENT)
        self.rail.bgcolor = C.SURFACE
        self.rail.indicator_color = indicator
        self.rail.selected_label_text_style = ft.TextStyle(
            color=C.ACCENT, size=11, weight=ft.FontWeight.W_600
        )
        self.rail.unselected_label_text_style = ft.TextStyle(
            color=C.TEXT_SECONDARY, size=11
        )
        self.rail_host.bgcolor = C.SURFACE
        self.rail_host.border = ft.Border.only(right=ft.BorderSide(1, C.BORDER))
        self.bar.bgcolor = C.SURFACE
        self.bar.indicator_color = indicator
        self.bar.border = ft.Border.only(top=ft.BorderSide(1, C.BORDER))
