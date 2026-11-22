from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import flet as ft
from core.academic_radar.models import RadarMetric
from gui.core.theme import C

logger = logging.getLogger(__name__)


class MetricDetailSheet:
    """Displays comprehensive breakdown, contributors, and formula explanation for a single metric."""

    @staticmethod
    def show(
        page: ft.Page,
        metric: RadarMetric,
        is_mobile: bool = False,
        *,
        on_dismiss: Optional[Callable[[], Any]] = None,
        on_closing: Optional[Callable[[], Any]] = None,
    ) -> Optional[Callable[[], None]]:
        # Status capsule
        status_badge = ft.Container(
            content=ft.Text(
                metric.status,
                size=11,
                weight=ft.FontWeight.W_600,
                color=C.ON_ACCENT,
            ),
            bgcolor=metric.color,
            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
            border_radius=8,
        )

        # Header with score and name
        score_str = str(metric.display_score) if metric.display_score is not None else "—"
        header_row = ft.Row(
            controls=[
                ft.Column(
                    controls=[
                        ft.Text(
                            metric.label.upper(),
                            size=16,
                            weight=ft.FontWeight.BOLD,
                            color=C.TEXT_PRIMARY,
                        ),
                        status_badge,
                    ],
                    spacing=4,
                    expand=True,
                ),
                ft.Text(
                    score_str,
                    size=32,
                    weight=ft.FontWeight.BOLD,
                    color=metric.color,
                ),
            ],
            spacing=8,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
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
            if delta_val > 0:
                delta_str = f"+{delta_val:.1f} điểm ↗"
                delta_color = C.SAFE
            elif delta_val < 0:
                delta_str = f"−{abs(delta_val):.1f} điểm ↘"
                delta_color = C.WARNING
            else:
                delta_str = "0.0 điểm →"
                delta_color = C.TEXT_SECONDARY

            delta_controls = [
                ft.Divider(height=1, color=C.BORDER),
                ft.Row(
                    controls=[
                        ft.Text("So với học kỳ trước:", size=12.5, color=C.TEXT_SECONDARY),
                        ft.Text(
                            delta_str,
                            size=12.5,
                            weight=ft.FontWeight.W_600,
                            color=delta_color,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    wrap=True,
                    spacing=8,
                    run_spacing=4,
                ),
            ]

        # Contributing courses
        courses_controls = []
        all_contributors = metric.positive_courses or metric.negative_courses
        if all_contributors:
            if metric.key == "resilience":
                section_title = "Nhóm học phần cần chú ý:"
            elif metric.key == "rhythm":
                section_title = "Ảnh hưởng nhiều nhất:"
            elif metric.key == "breakthrough":
                section_title = "Học phần nổi bật:"
            else:
                section_title = "Nhóm học phần tiêu biểu:"

            course_items = []
            for c in all_contributors[:4]:
                name = c.get("course_name", "")
                if metric.key == "rhythm":
                    drop = c.get("drop", 0.0)
                    if drop > 0:
                        grade_label = f"Giảm {drop:.1f}"
                        val_color = C.WARNING
                    else:
                        grade_label = "Giữ vững"
                        val_color = C.SAFE
                else:
                    grade = c.get("grade") or c.get("process_grade") or 0.0
                    credits = c.get("credits_used") or c.get("credits") or 0
                    grade_label = f"{grade:.1f} · {credits:.0f} TC"
                    val_color = C.TEXT_PRIMARY

                course_items.append(
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Text(
                                    name,
                                    size=13,
                                    weight=ft.FontWeight.W_500,
                                    color=C.TEXT_PRIMARY,
                                    expand=True,
                                    max_lines=2,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                                ft.Text(
                                    grade_label,
                                    size=12,
                                    weight=ft.FontWeight.W_600,
                                    color=val_color,
                                ),
                            ],
                            spacing=8,
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        bgcolor=C.SURFACE,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                        border_radius=8,
                        border=ft.Border.all(1, C.BORDER),
                    )
                )

            courses_controls = [
                ft.Divider(height=1, color=C.BORDER),
                ft.Text(section_title, size=12.5, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY),
                ft.Column(controls=course_items, spacing=6),
            ]

        # Coverage info
        cov_pct = int(round(metric.coverage * 100))
        if metric.confidence == "complete":
            conf_label = "Đầy đủ"
            conf_color = C.SAFE
        elif metric.confidence == "partial":
            conf_label = "Một phần"
            conf_color = C.WARNING
        else:
            conf_label = "Chưa đủ"
            conf_color = C.CRITICAL

        coverage_row = ft.Row(
            controls=[
                ft.Text("Độ đầy đủ dữ liệu:", size=12, color=C.TEXT_SECONDARY),
                ft.Row(
                    controls=[
                        ft.Text(f"{cov_pct}%", size=12, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                        ft.Text("·", size=12, color=C.TEXT_SECONDARY),
                        ft.Text(f"● {conf_label}", size=11.5, weight=ft.FontWeight.W_600, color=conf_color),
                    ],
                    spacing=4,
                    tight=True,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            wrap=True,
            spacing=8,
            run_spacing=4,
        )

        # Expandable formula explanation
        formula_expansion = ft.ExpansionTile(
            title=ft.Text("Cách tính chỉ số", size=12.5, weight=ft.FontWeight.W_600, color=C.ACCENT),
            controls=[
                ft.Container(
                    content=ft.Text(
                        metric.calculation_text,
                        size=12,
                        color=C.TEXT_SECONDARY,
                    ),
                    bgcolor=C.BG,
                    padding=ft.Padding.all(10),
                    border_radius=8,
                    border=ft.Border.all(1, C.BORDER),
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
            scroll=ft.ScrollMode.AUTO,
        )

        try:
            page_w = float(getattr(page, "width", 0) or 0)
        except (TypeError, ValueError):
            page_w = 0.0
        try:
            page_h = float(getattr(page, "height", 0) or 0)
        except (TypeError, ValueError):
            page_h = 0.0
        is_narrow = 0 < page_w < 600
        dialog_w = max(240.0, min(380.0, page_w - 64.0)) if page_w > 0 else 380.0
        max_h = max(240.0, min(520.0, page_h * 0.65)) if page_h > 0 else None

        used_overlay = False
        is_real_page = isinstance(page, ft.Page)

        def _notify_dismiss(_event=None) -> None:
            if callable(on_dismiss):
                try:
                    on_dismiss()
                except Exception:
                    logger.debug("MetricDetailSheet on_dismiss failed", exc_info=True)

        def _close_dialog(p: ft.Page, d: ft.AlertDialog):
            if callable(on_closing):
                try:
                    on_closing()
                except Exception:
                    logger.debug("MetricDetailSheet on_closing failed", exc_info=True)
            if not used_overlay:
                try:
                    p.pop_dialog()
                    if not is_real_page:
                        _notify_dismiss()
                    return
                except Exception:
                    pass
            d.open = False
            try:
                d.update()
            except Exception:
                try:
                    p.update()
                except Exception:
                    pass
            _notify_dismiss()

        def _on_key_down(event) -> None:
            if getattr(event, "key", "") == "Escape":
                _close_dialog(page, dialog)

        # Dialog presentation
        dialog = ft.AlertDialog(
            modal=True,
            semantics_label=f"Chi tiết chỉ số {metric.label}",
            title=ft.Text(
                metric.label,
                size=15,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
                semantics_label=metric.label,
            ),
            content=ft.KeyboardListener(
                autofocus=False,
                on_key_down=_on_key_down,
                content=ft.Container(
                    content=content_column,
                    width=dialog_w,
                    height=max_h,
                ),
            ),
            actions=[
                ft.TextButton(
                    "Đóng",
                    height=44,
                    on_click=lambda _: _close_dialog(page, dialog),
                )
            ],
            on_dismiss=_notify_dismiss,
            inset_padding=ft.Padding.symmetric(horizontal=16 if is_narrow else 40, vertical=24),
            title_padding=ft.Padding.only(left=16 if is_narrow else 24, right=16 if is_narrow else 24, top=16, bottom=4),
            content_padding=ft.Padding.only(left=16 if is_narrow else 24, right=16 if is_narrow else 24, top=12, bottom=8),
            actions_padding=ft.Padding.only(left=16, right=16, top=0, bottom=8),
            bgcolor=C.BG,
            shape=ft.RoundedRectangleBorder(radius=16),
        )

        try:
            page.show_dialog(dialog)
        except Exception:
            used_overlay = True
            overlay = getattr(page, "overlay", None)
            if overlay is not None:
                overlay.append(dialog)
                dialog.open = True
                try:
                    page.update()
                except Exception:
                    pass

        return lambda: _close_dialog(page, dialog)
