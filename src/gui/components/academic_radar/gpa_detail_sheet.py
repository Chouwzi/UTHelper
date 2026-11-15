from __future__ import annotations

import flet as ft

from core.academic_radar.models import RadarProfile
from gui.core.theme import C


def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "—"


def _fmt_credits(value: float | None) -> str:
    if value is None:
        return "—"
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


class GpaDetailSheet:
    """Một nút duy nhất hiển thị GPA trong kỳ, GPA tích lũy (ra trường) và tín chỉ."""

    @staticmethod
    def _row(label: str, value: str, bold: bool = False) -> ft.Row:
        return ft.Row(
            controls=[
                ft.Text(label, size=13, color=C.TEXT_SECONDARY),
                ft.Text(
                    value,
                    size=14,
                    weight=ft.FontWeight.BOLD if bold else ft.FontWeight.W_500,
                    color=C.TEXT_PRIMARY,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

    @staticmethod
    def _section(title: str, rows: list[ft.Control]) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(title, size=12, weight=ft.FontWeight.W_700, color=C.ACCENT),
                    *rows,
                ],
                spacing=6,
            ),
            bgcolor=C.SURFACE,
            padding=ft.Padding.all(12),
            border_radius=10,
            border=ft.Border.all(1, C.BORDER),
        )

    @staticmethod
    def show(page: ft.Page, profile: RadarProfile) -> None:
        row = GpaDetailSheet._row
        semester = GpaDetailSheet._section(
            "TRONG KỲ",
            [
                row("GPA hệ 10", _fmt(profile.gpa_10), bold=True),
                row("GPA hệ 4", _fmt(profile.gpa_4), bold=True),
                row(
                    "Tín chỉ đạt / đăng ký",
                    f"{_fmt_credits(profile.credits_earned)} / {_fmt_credits(profile.credits_registered)}",
                ),
            ],
        )
        cumulative = GpaDetailSheet._section(
            "TÍCH LŨY (RA TRƯỜNG)",
            [
                row("GPA hệ 10", _fmt(profile.cumulative_gpa_10), bold=True),
                row("GPA hệ 4", _fmt(profile.cumulative_gpa_4), bold=True),
                row("Tín chỉ tích lũy", _fmt_credits(profile.credits_cumulative)),
            ],
        )

        def _close(_):
            dialog.open = False
            try:
                dialog.update()
            except Exception:
                try:
                    page.update()
                except Exception:
                    pass

        dialog = ft.AlertDialog(
            modal=False,
            title=ft.Text(profile.semester_name, size=15, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
            content=ft.Container(
                content=ft.Column([semester, cumulative], spacing=10, tight=True),
                width=360,
            ),
            actions=[ft.TextButton("Đóng", on_click=_close)],
            bgcolor=C.BG,
            shape=ft.RoundedRectangleBorder(radius=16),
        )
        try:
            page.show_dialog(dialog)
        except Exception:
            overlay = getattr(page, "overlay", None)
            if overlay is not None:
                overlay.append(dialog)
                dialog.open = True
                try:
                    page.update()
                except Exception:
                    pass
