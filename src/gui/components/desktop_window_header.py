"""Small frameless desktop title bar shared by startup and the main shell."""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from gui.core.theme import C


def build_desktop_window_header(
    *,
    on_minimize: Callable,
    on_close: Callable,
) -> ft.Container:
    """Build a draggable title-free header with only minimize and close."""
    button_constraints = ft.BoxConstraints(
        min_width=42,
        max_width=42,
        min_height=34,
        max_height=34,
    )
    drag_area = ft.WindowDragArea(
        content=ft.Container(expand=True, bgcolor=C.BG),
        expand=True,
        maximizable=False,
    )
    minimize = ft.IconButton(
        ft.Icons.MINIMIZE_ROUNDED,
        icon_color=C.TEXT_SECONDARY,
        icon_size=17,
        tooltip="Thu nhỏ",
        padding=0,
        size_constraints=button_constraints,
        hover_color=C.SURFACE_HOVER,
        on_click=on_minimize,
    )
    close = ft.IconButton(
        ft.Icons.CLOSE_ROUNDED,
        icon_color=C.TEXT_SECONDARY,
        icon_size=18,
        tooltip="Thoát ứng dụng",
        padding=0,
        size_constraints=button_constraints,
        hover_color=ft.Colors.with_opacity(0.12, C.CRITICAL),
        on_click=on_close,
    )
    return ft.Container(
        content=ft.Row(
            controls=[drag_area, minimize, close],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        height=34,
        bgcolor=C.BG,
    )
