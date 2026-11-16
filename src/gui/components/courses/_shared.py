"""Shared Flet UI helper builders for Courses panes."""

from __future__ import annotations

from typing import Any, Callable

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
) -> ft.Chip:
    """Build a themed selection filter chip without mixing click/select handlers."""
    weight = (
        ft.FontWeight.W_600 if (bold_selected and selected) else ft.FontWeight.W_400
    )
    return ft.Chip(
        label=ft.Text(
            label,
            size=12,
            weight=weight,
            color=C.ON_ACCENT if selected else C.TEXT_PRIMARY,
        ),
        selected=selected,
        show_checkmark=False,
        selected_color=C.ACCENT,
        bgcolor=C.SURFACE,
        border_side=ft.BorderSide(1, C.ACCENT if selected else C.BORDER),
        on_select=on_select,
    )


def build_offline_banner(message: str) -> ft.Container:
    """Build the warning banner displayed when offline or a provider call fails."""
    return ft.Container(
        bgcolor=C.SURFACE,
        border=ft.Border.all(1, C.WARNING),
        border_radius=10,
        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        content=ft.Row(
            spacing=8,
            controls=[
                ft.Icon(ft.Icons.WIFI_OFF_ROUNDED, size=16, color=C.WARNING),
                ft.Text(message, size=12, color=C.TEXT_PRIMARY, expand=True),
            ],
        ),
    )
