"""Small frameless desktop title bar shared by startup and the main shell."""

from __future__ import annotations

from collections.abc import Callable
import math

import flet as ft

from gui.core.theme import C


def build_desktop_window_header(
    *,
    on_minimize: Callable,
    on_close: Callable,
    title_control: ft.Control | None = None,
    version_control: ft.Control | None = None,
) -> ft.Container:
    """Build a draggable header with title, minimize and close controls."""
    button_constraints = ft.BoxConstraints(
        min_width=42,
        max_width=42,
        min_height=34,
        max_height=34,
    )
    title_controls = [
        title_control
        or ft.Text(
            "UTHelper",
            size=13,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY,
        )
    ]
    if version_control is not None:
        title_controls.append(version_control)
    drag_area = ft.WindowDragArea(
        content=ft.Container(
            content=ft.Row(
                controls=title_controls,
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.only(left=12),
            expand=True,
            bgcolor=C.BG,
        ),
        expand=True,
        maximizable=False,
    )
    minimize = ft.IconButton(
        # Material's MINIMIZE glyph sits on the baseline by design. A centered
        # horizontal rule has the same meaning without looking vertically low.
        ft.Icons.HORIZONTAL_RULE_ROUNDED,
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
        rotate=0.0,
        animate_rotation=ft.Animation(
            360, ft.AnimationCurve.EASE_IN_OUT_CUBIC
        ),
    )
    close_rotation = 0.0

    def rotate_close_on_hover(event) -> None:
        nonlocal close_rotation
        value = getattr(event, "data", False)
        hovered = value is True or str(value).lower() == "true"
        if not hovered:
            return
        close_rotation += math.tau
        close.rotate = close_rotation
        close.update()

    close.on_hover = rotate_close_on_hover
    return ft.Container(
        content=ft.Row(
            controls=[drag_area, minimize, close],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        height=34,
        bgcolor=C.BG,
    )
