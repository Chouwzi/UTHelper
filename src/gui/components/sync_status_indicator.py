from __future__ import annotations

import flet as ft

from gui.core.theme import C


class SyncStatusIndicator:
    """Compact sync state that animates entirely on the Flet client."""

    _SEMANTIC_LABELS = {
        "busy": "Đang cập nhật dữ liệu",
        "success": "Đã cập nhật dữ liệu",
        "error": "Lỗi cập nhật dữ liệu",
    }

    def __init__(self, message: str, *, state: str = "busy") -> None:
        self.text = ft.Text(message, size=11, color=C.TEXT_SECONDARY)
        self._busy_dot = self._make_dot(C.WARNING, glow=True)
        self.busy_shimmer = ft.Shimmer(
            content=self._busy_dot,
            base_color=C.WARNING,
            highlight_color=C.ON_WARNING,
            period=1700,
            loop=0,
            width=8,
            height=8,
            left=3,
            top=3,
        )
        self.static_dot = self._make_dot(C.SAFE, glow=False)
        self.static_dot.left = 3
        self.static_dot.top = 3
        self._dot_slot = ft.Stack(
            controls=[self.busy_shimmer, self.static_dot],
            width=14,
            height=14,
        )
        self.control = ft.Semantics(
            content=ft.Row(
                controls=[self._dot_slot, self.text],
                spacing=6,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            live_region=True,
        )
        self.state = "busy"
        self.set_state(state, message)

    @staticmethod
    def _make_dot(color: str, *, glow: bool) -> ft.Container:
        shadow = None
        if glow:
            shadow = ft.BoxShadow(
                blur_radius=7,
                spread_radius=1,
                color=ft.Colors.with_opacity(0.45, color),
            )
        return ft.Container(
            width=8,
            height=8,
            bgcolor=color,
            border_radius=4,
            shadow=shadow,
        )

    def set_state(self, state: str, message: str) -> None:
        if state not in self._SEMANTIC_LABELS:
            raise ValueError(f"Unsupported sync status: {state}")
        self.state = state
        self.text.value = message
        self.control.label = self._SEMANTIC_LABELS[state]
        self.busy_shimmer.visible = state == "busy"
        self.static_dot.visible = state != "busy"
        if state == "success":
            self.static_dot.bgcolor = C.SAFE
        elif state == "error":
            self.static_dot.bgcolor = C.CRITICAL

    def refresh_theme(self) -> None:
        self.text.color = C.TEXT_SECONDARY
        self._busy_dot.bgcolor = C.WARNING
        self._busy_dot.shadow = ft.BoxShadow(
            blur_radius=7,
            spread_radius=1,
            color=ft.Colors.with_opacity(0.45, C.WARNING),
        )
        self.busy_shimmer.base_color = C.WARNING
        self.busy_shimmer.highlight_color = C.ON_WARNING
        if self.state == "success":
            self.static_dot.bgcolor = C.SAFE
        elif self.state == "error":
            self.static_dot.bgcolor = C.CRITICAL
