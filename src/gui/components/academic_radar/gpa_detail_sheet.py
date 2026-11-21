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

        try:
            page_w = float(getattr(page, "width", 0) or 0)
        except (TypeError, ValueError):
            page_w = 0.0
        try:
            page_h = float(getattr(page, "height", 0) or 0)
        except (TypeError, ValueError):
            page_h = 0.0
        dialog_w = max(260.0, min(360.0, page_w - 64.0)) if page_w > 0 else 360.0
        max_h = max(220.0, min(480.0, page_h * 0.65)) if page_h > 0 else None

        dialog = ft.AlertDialog(
            modal=True,
            semantics_label=f"Chi tiết GPA {profile.semester_name}",
            title=ft.Text(
                profile.semester_name,
                size=15,
                weight=ft.FontWeight.BOLD,
                color=C.TEXT_PRIMARY,
                semantics_label=profile.semester_name,
            ),
            content=ft.Container(
                content=ft.Column(
                    [semester, cumulative],
                    spacing=10,
                    tight=True,
                    scroll=ft.ScrollMode.AUTO,
                ),
                width=dialog_w,
                height=max_h,
            ),
            actions=[ft.TextButton("Đóng", height=44, on_click=_close)],
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
