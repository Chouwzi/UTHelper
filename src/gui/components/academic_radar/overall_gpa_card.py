from __future__ import annotations

import flet as ft

from core.academic_radar.overall import OverallGpa
from gui.core.theme import C


def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "—"


class OverallGpaCard(ft.Container):
    """Thẻ GPA trung bình toàn bộ các kỳ."""

    def __init__(self, overall: OverallGpa | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.bgcolor = C.SURFACE
        self.border_radius = 16
        self.border = ft.Border.all(1, C.BORDER)
        self.padding = ft.Padding.all(16)
        self.set_overall(overall, update=False)

    def _stat(self, label: str, value: str) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(label, size=10, weight=ft.FontWeight.W_500, color=C.TEXT_SECONDARY),
                    ft.Text(value, size=18, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                ],
                spacing=2,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            bgcolor=C.BG,
            padding=ft.Padding.symmetric(horizontal=6, vertical=8),
            border_radius=8,
            border=ft.Border.all(1, C.BORDER),
            expand=True,
            height=60,
            alignment=ft.Alignment(0, 0),
        )

    def set_overall(self, overall: OverallGpa | None, update: bool = True) -> None:
        self._overall = overall
        self.content = None
        if overall is None:
            self.content = ft.Text("Chưa đủ dữ liệu để tính GPA toàn khóa", size=13, color=C.TEXT_SECONDARY)
        else:
            credits = overall.total_credits
            credits_text = str(int(credits)) if float(credits).is_integer() else f"{credits:.1f}"
            self.content = ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Text("GPA toàn khóa", size=16, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY),
                            ft.Text(
                                f"{overall.semester_count} kỳ · {overall.course_count} học phần",
                                size=11,
                                color=C.TEXT_SECONDARY,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Row(
                        controls=[
                            self._stat("GPA (10)", _fmt(overall.gpa_10)),
                            self._stat("GPA (4)", _fmt(overall.gpa_4)),
                            self._stat("Tín chỉ", credits_text),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=10,
            )
        if update:
            try:
                self.update()
            except Exception:
                pass
