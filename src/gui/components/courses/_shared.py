"""Shared Flet UI helper builders for Courses panes."""

from __future__ import annotations

from typing import Any, Callable, Optional

import flet as ft

from gui.core.theme import C


def safe_control_update(control: ft.BaseControl) -> None:
    """Update a Flet control only if it is currently attached to a Page."""
    try:
        if control.page is not None:
            control.update()
    except Exception:
        pass


def build_filter_chip(
    label: str,
    *,
    selected: bool,
    on_select: Callable[[Any], None],
    bold_selected: bool = False,
    bgcolor: Optional[str] = None,
    selected_color: Optional[str] = None,
    text_color: Optional[str] = None,
    selected_text_color: Optional[str] = None,
    border_color: Optional[str] = None,
) -> ft.Chip:
    """Build a themed selection filter chip without mixing click/select handlers."""
    weight = (
        ft.FontWeight.W_600 if (bold_selected and selected) else ft.FontWeight.W_400
    )
    active_accent = selected_color or C.ACCENT
    active_fg = selected_text_color or C.ON_ACCENT
    idle_fg = text_color or C.TEXT_PRIMARY
    idle_bg = bgcolor or C.SURFACE
    idle_border = border_color or C.BORDER
    return ft.Chip(
        label=ft.Text(
            label,
            size=12,
            weight=weight,
            color=active_fg if selected else idle_fg,
        ),
        selected=selected,
        show_checkmark=False,
        selected_color=active_accent,
        bgcolor=idle_bg,
        border_side=ft.BorderSide(1, active_accent if selected else idle_border),
        on_select=on_select,
    )


def build_offline_banner(
    message: str,
    *,
    icon: Optional[ft.IconData] = None,
) -> ft.Container:
    """Build the warning banner displayed when offline or a provider/archive call fails."""
    return ft.Container(
        bgcolor=C.SURFACE,
        border=ft.Border.all(1, C.WARNING),
        border_radius=10,
        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        content=ft.Row(
            spacing=8,
            controls=[
                ft.Icon(icon or ft.Icons.WIFI_OFF_ROUNDED, size=16, color=C.WARNING),
                ft.Text(message, size=12, color=C.TEXT_PRIMARY, expand=True),
            ],
        ),
    )


def build_viewer_header(
    *,
    title: str,
    subtitle: str,
    back_tooltip: str,
    on_back: Callable[[Any], None],
    trailing_actions: list[ft.Control],
) -> tuple[ft.Container, ft.IconButton, ft.Text, ft.Text]:
    """Build a shared top header bar for in-app Courses reader/explorer overlays."""
    back_btn = ft.IconButton(
        icon=ft.Icons.ARROW_BACK_ROUNDED,
        tooltip=back_tooltip,
        icon_color=C.TEXT_PRIMARY,
        on_click=on_back,
    )
    title_text = ft.Text(
        title,
        size=15,
        weight=ft.FontWeight.W_600,
        color=C.TEXT_PRIMARY,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    subtitle_text = ft.Text(
        subtitle,
        size=11,
        color=C.TEXT_SECONDARY,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    container = ft.Container(
        bgcolor=C.SURFACE,
        border=ft.Border.only(bottom=ft.BorderSide(1, C.BORDER)),
        padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        content=ft.Row(
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                back_btn,
                ft.Column(
                    expand=True,
                    spacing=1,
                    controls=[title_text, subtitle_text],
                ),
                *trailing_actions,
            ],
        ),
    )
    return container, back_btn, title_text, subtitle_text


def refresh_viewer_header(
    header_container: ft.Container,
    back_btn: ft.IconButton,
    title_text: ft.Text,
    subtitle_text: ft.Text,
) -> None:
    """Apply active global theme tokens C to a viewer header bar."""
    header_container.bgcolor = C.SURFACE
    header_container.border = ft.Border.only(bottom=ft.BorderSide(1, C.BORDER))
    back_btn.icon_color = C.TEXT_PRIMARY
    title_text.color = C.TEXT_PRIMARY
    subtitle_text.color = C.TEXT_SECONDARY
