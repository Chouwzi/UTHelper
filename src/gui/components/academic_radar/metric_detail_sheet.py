from __future__ import annotations

import flet as ft
from typing import Optional
from core.academic_radar.models import RadarMetric
from gui.core.theme import C


class MetricDetailSheet:
    """Displays comprehensive breakdown, contributors, and formula explanation for a single metric."""

    @staticmethod
    def show(page: ft.Page, metric: RadarMetric, is_mobile: bool = False) -> None:
        # Status capsule
        status_badge = ft.Container(
            content=ft.Text(
                metric.status,
                size=12,
                weight=ft.FontWeight.W_600,
                color=C.ON_ACCENT,
            ),
            bgcolor=metric.color,
            padding=ft.Padding.symmetric(horizontal=10, vertical=4),
            border_radius=12,
        )

        # Header with score and name
        score_str = str(metric.display_score) if metric.display_score is not None else "—"
        header_row = ft.Row(
            controls=[
                ft.Column(
                    controls=[
                        ft.Text(
                            metric.label.upper(),
                            size=18,
                            weight=ft.FontWeight.BOLD,
                            color=C.TEXT_PRIMARY,
                        ),
                        status_badge,
                    ],
                    spacing=6,
                ),
                ft.Text(
                    score_str,
                    size=36,
                    weight=ft.FontWeight.BOLD,
                    color=metric.color,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # Description
        desc_text = ft.Text(
            metric.description,
            size=13,
            color=C.TEXT_SECONDARY,
        )

        # Delta vs previous semester
        delta_controls = []
        if metric.delta_vs_previous is not None:
            delta_val = metric.delta_vs_previous
            delta_sign = "+" if delta_val > 0 else ""
            delta_arrow = "↗" if delta_val > 0 else ("↘" if delta_val < 0 else "→")
            delta_color = C.SAFE if delta_val > 0 else (C.WARNING if delta_val < 0 else C.TEXT_SECONDARY)
            delta_controls = [
                ft.Divider(height=1, color=C.BORDER),
                ft.Row(
                    controls=[
                        ft.Text("So với học kỳ trước:", size=13, color=C.TEXT_SECONDARY),
                        ft.Text(
                            f"{delta_sign}{delta_val} điểm {delta_arrow}",
                            size=13,
                            weight=ft.FontWeight.W_600,
                            color=delta_color,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ]

        # Contributing courses
        courses_controls = []
        all_contributors = metric.positive_courses or metric.negative_courses
        if all_contributors:
            section_title = "Nhóm học phần ảnh hưởng nhiều nhất:" if metric.positive_courses else "Nhóm học phần cần chú ý:"
            course_items = []
            for c in all_contributors[:4]:
                name = c.get("course_name", "")
                grade = c.get("grade") or c.get("drop") or c.get("process_grade")
                credits = c.get("credits_used") or c.get("credits") or 0
                grade_label = f"Hụt -{grade:.1f}" if "drop" in c else f"Điểm {grade:.1f}"
                if credits:
                    grade_label += f" ({credits:.1f} TC)"

                course_items.append(
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Text(name, size=13, weight=ft.FontWeight.W_500, color=C.TEXT_PRIMARY, expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(grade_label, size=12, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY),
                            ],
                        ),
                        bgcolor=C.SURFACE,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                        border_radius=8,
                        border=ft.Border.all(1, C.BORDER),
                    )
                )

            courses_controls = [
                ft.Divider(height=1, color=C.BORDER),
                ft.Text(section_title, size=13, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY),
                ft.Column(controls=course_items, spacing=6),
            ]

        # Coverage info
        cov_pct = int(round(metric.coverage * 100))
        conf_label = "Đầy đủ" if metric.confidence == "complete" else ("Một phần" if metric.confidence == "partial" else "Chưa đủ")
        coverage_row = ft.Row(
            controls=[
                ft.Text("Mức độ đầy đủ dữ liệu:", size=12, color=C.TEXT_SECONDARY),
                ft.Text(f"{cov_pct}% ({conf_label})", size=12, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # Expandable formula explanation
        formula_expansion = ft.ExpansionTile(
            title=ft.Text("Cách tính chỉ số", size=13, weight=ft.FontWeight.W_600, color=C.ACCENT),
            controls=[
                ft.Container(
                    content=ft.Text(
                        metric.calculation_text,
                        size=12,
                        color=C.TEXT_SECONDARY,
                    ),
                    padding=ft.Padding.only(left=8, right=8, bottom=8),
                )
            ],
            tile_padding=ft.Padding.all(0),
        )

        content_column = ft.Column(
            controls=[
                header_row,
                desc_text,
                *delta_controls,
                *courses_controls,
                ft.Divider(height=1, color=C.BORDER),
                coverage_row,
                formula_expansion,
            ],
            spacing=10,
            tight=True,
        )

        # Dialog presentation
        dialog = ft.AlertDialog(
            modal=False,
            content=ft.Container(
                content=content_column,
                width=380,
                padding=16,
            ),
            actions=[
                ft.TextButton(
                    "Đóng",
                    on_click=lambda _: _close_dialog(page, dialog),
                )
            ],
            bgcolor=C.BG,
            shape=ft.RoundedRectangleBorder(radius=16),
        )

        def _close_dialog(p: ft.Page, d: ft.AlertDialog):
            d.open = False
            try:
                d.update()
            except Exception:
                try:
                    p.update()
                except Exception:
                    pass

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
