from __future__ import annotations

import logging
from typing import Any, Callable, Optional
import flet as ft

from core.academic_radar.models import RadarMetric, RadarProfile
from gui.components.academic_radar.metric_detail_sheet import MetricDetailSheet
from gui.components.academic_radar.radar_chart import AcademicRadarChart
from gui.core.theme import C

logger = logging.getLogger(__name__)


class AcademicRadarCard(ft.Container):
    """Hero container card presenting the semester Academic Radar, KPIs, and Highlights."""

    def __init__(
        self,
        profiles: list[RadarProfile],
        selected_index: int = 0,
        on_semester_change: Optional[Callable[[int], None]] = None,
        page: Optional[ft.Page] = None,
        on_before_dialog: Optional[Callable[[], Any]] = None,
        on_dialog_dismiss: Optional[Callable[[Any], None]] = None,
        on_dialog_opened: Optional[Callable[[Any, Callable[[], None]], None]] = None,
        on_dialog_closing: Optional[Callable[[Any], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.profiles = profiles
        self.selected_index = selected_index
        self.on_semester_change = on_semester_change
        self._page_ref = page
        self._on_before_dialog = on_before_dialog
        self._on_dialog_dismiss = on_dialog_dismiss
        self._on_dialog_opened = on_dialog_opened
        self._on_dialog_closing = on_dialog_closing
        self._is_comparing = False
        self._viewport_width: float = 0.0
        self._chart_width_used: float = 0.0

        self.bgcolor = C.SURFACE
        self.border_radius = 16
        self.border = ft.Border.all(1, C.BORDER)
        self.padding = ft.Padding.all(16)

        self._build_ui()

    def _get_page(self) -> Optional[ft.Page]:
        if self._page_ref is not None:
            return self._page_ref
        try:
            return self.page
        except Exception:
            return None

    def _resolve_page_width(self) -> float:
        if self._viewport_width > 0:
            return self._viewport_width
        page = self._get_page()
        try:
            return float(getattr(page, "width", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def set_viewport_width(self, width: float) -> None:
        try:
            numeric = float(width or 0)
        except (TypeError, ValueError):
            numeric = 0.0
        if numeric <= 0:
            return
        self._viewport_width = numeric
        if self.current_profile is None:
            return
        new_chart_w = max(220.0, min(340.0, numeric - 68.0))
        if abs(new_chart_w - self._chart_width_used) < 2.0:
            return
        self._build_ui()
        try:
            self.update()
        except Exception:
            pass

    @property
    def current_profile(self) -> Optional[RadarProfile]:
        if 0 <= self.selected_index < len(self.profiles):
            return self.profiles[self.selected_index]
        return None

    @property
    def previous_profile(self) -> Optional[RadarProfile]:
        next_idx = self.selected_index + 1
        if 0 <= next_idx < len(self.profiles):
            return self.profiles[next_idx]
        return None

    def set_profiles(self, profiles: list[RadarProfile], selected_index: int = 0) -> None:
        self.profiles = profiles
        self.selected_index = selected_index
        self._build_ui()
        try:
            self.update()
        except Exception:
            pass

    def update_theme(self, *, update: bool = True) -> None:
        """Refresh the card surface and all generated controls for the active theme."""
        self.bgcolor = C.SURFACE
        self.border = ft.Border.all(1, C.BORDER)
        self._build_ui()
        if not update:
            return
        try:
            self.update()
        except Exception:
            pass

    def _toggle_compare(self, e: ft.ControlEvent | None) -> None:
        self._is_comparing = not self._is_comparing
        self._compare_btn.icon = ft.Icons.CHECK_CIRCLE_ROUNDED if self._is_comparing else ft.Icons.RADIO_BUTTON_UNCHECKED
        self._compare_btn.icon_color = C.ACCENT if self._is_comparing else C.TEXT_SECONDARY
        self._compare_btn.content = "Đang so sánh" if self._is_comparing else "So sánh kỳ trước"
        self._radar_chart.update_profile(
            self.current_profile,
            self.previous_profile,
            self._is_comparing,
        )
        try:
            self.update()
        except Exception:
            pass

    def _on_metric_click(self, metric: RadarMetric) -> None:
        p = self._get_page()
        if p is not None:
            token = self._on_before_dialog() if callable(self._on_before_dialog) else None
            closer = MetricDetailSheet.show(
                p,
                metric,
                on_dismiss=(
                    (lambda t=token: self._on_dialog_dismiss(t))
                    if callable(self._on_dialog_dismiss)
                    else None
                ),
                on_closing=(
                    (lambda t=token: self._on_dialog_closing(t))
                    if callable(self._on_dialog_closing)
                    else None
                ),
            )
            if closer is not None and callable(self._on_dialog_opened):
                self._on_dialog_opened(token, closer)

    def _show_radar_info(self, _event=None) -> None:
        page = self._get_page()
        if page is None:
            return
        try:
            page_w = float(getattr(page, "width", 0) or 0)
        except (TypeError, ValueError):
            page_w = 0.0
        is_narrow = 0 < page_w < 600
        dialog_w = max(240.0, min(400.0, page_w - 64.0)) if page_w > 0 else 360.0
        items = [
            ("Tổng Lực", "Điểm trung bình có trọng số tín chỉ trong học kỳ."),
            ("Nền Tảng", "Đánh giá điểm quá trình / chuyên cần."),
            ("Bứt Phá", "Tỷ lệ môn đạt mức giỏi - xuất sắc."),
            ("Giữ Nhịp", "Độ ổn định giữa điểm quá trình và điểm thi."),
            ("Về Đích", "Tỷ lệ hoàn thành và qua môn trong kỳ."),
            ("Trụ Vững", "Điểm của các môn nặng tín chỉ nhất."),
        ]
        token = self._on_before_dialog() if callable(self._on_before_dialog) else None
        used_overlay = False
        is_real_page = isinstance(page, ft.Page)

        def _notify_dismiss(_event=None) -> None:
            if callable(self._on_dialog_dismiss):
                try:
                    self._on_dialog_dismiss(token)
                except Exception:
                    logger.debug("Radar info on_dialog_dismiss failed", exc_info=True)

        def _close_dialog(p: ft.Page, d: ft.AlertDialog) -> None:
            if callable(self._on_dialog_closing):
                try:
                    self._on_dialog_closing(token)
                except Exception:
                    logger.debug("Radar info on_dialog_closing failed", exc_info=True)
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

        dialog = ft.AlertDialog(
            modal=True,
            semantics_label="Giải thích 6 chỉ số Radar học tập",
            title=ft.Text(
                "6 chỉ số Radar học tập",
                size=16,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
                semantics_label="6 chỉ số Radar học tập",
            ),
            content=ft.KeyboardListener(
                autofocus=False,
                on_key_down=_on_key_down,
                content=ft.Container(
                    width=dialog_w,
                    content=ft.Column(
                        controls=[
                            ft.Text(
                                f"• {name}: {desc}",
                                size=12.5,
                                color=C.TEXT_PRIMARY,
                            )
                            for name, desc in items
                        ],
                        spacing=8,
                        tight=True,
                        scroll=ft.ScrollMode.AUTO,
                    ),
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
        if callable(self._on_dialog_opened):
            self._on_dialog_opened(token, lambda: _close_dialog(page, dialog))

    def _build_ui(self) -> None:
        # Data-driven rebuilds can happen after a theme preview while this view
        # is detached. Reapply the root surface as well as child control colors.
        self.bgcolor = C.SURFACE
        self.border = ft.Border.all(1, C.BORDER)
        profile = self.current_profile
        if not profile:
            self.content = ft.Text("Chưa có dữ liệu học kỳ", size=14, color=C.TEXT_SECONDARY)
            return

        page_w = self._resolve_page_width()
        is_narrow = 0 < page_w < 430

        # 1. Header: Title & Semester Selector
        info_btn = ft.Semantics(
            label="Giải thích 6 chỉ số Radar học tập",
            button=True,
            content=ft.IconButton(
                icon=ft.Icons.INFO_OUTLINE_ROUNDED,
                icon_size=18,
                icon_color=C.TEXT_SECONDARY,
                tooltip="Giải thích 6 chỉ số Radar học tập",
                padding=0,
                size_constraints=ft.BoxConstraints(
                    min_width=36, max_width=36, min_height=44, max_height=44
                ),
                on_click=self._show_radar_info,
            ),
        )
        title = ft.Row(
            controls=[
                ft.Text("Hồ sơ học tập", size=15 if is_narrow else 16, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY),
                info_btn,
            ],
            spacing=2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        semester_options = [
            ft.dropdown.Option(key=str(i), text=p.semester_name)
            for i, p in enumerate(self.profiles)
        ]

        def _handle_dropdown(e):
            val = int(e.control.value)
            self.selected_index = val
            if self.on_semester_change:
                self.on_semester_change(val)
            self._build_ui()
            try:
                self.update()
            except Exception:
                pass

        self._sem_dropdown = ft.Dropdown(
            options=semester_options,
            value=str(self.selected_index),
            on_select=_handle_dropdown,
            height=38,
            text_size=11.5 if is_narrow else 12,
            bgcolor=C.BG,
            border_color=C.BORDER,
            border_radius=8,
            dense=True,
            enable_search=False,
            expanded_insets=ft.Padding.zero(),
            content_padding=ft.Padding.only(left=10, right=4, top=0, bottom=0),
            expand=True,
        )

        # Compare button
        has_previous = self.previous_profile is not None
        self._compare_btn = ft.TextButton(
            content="Đang so sánh" if self._is_comparing else "So sánh kỳ trước",
            icon=ft.Icons.CHECK_CIRCLE_ROUNDED if self._is_comparing else ft.Icons.RADIO_BUTTON_UNCHECKED,
            icon_color=C.ACCENT if self._is_comparing else C.TEXT_SECONDARY,
            style=ft.ButtonStyle(
                padding=ft.Padding.symmetric(horizontal=6, vertical=0),
                text_style=ft.TextStyle(size=12 if is_narrow else 13),
            ),
            on_click=self._toggle_compare,
            visible=has_previous,
        )

        header_row = ft.Row(
            controls=[
                title,
                self._compare_btn,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=4,
            wrap=True,
            run_spacing=2,
        )

        # 2. Radar Chart (scale cleanly on 360-390px mobile viewports)
        chart_width = max(220.0, min(340.0, page_w - 68.0)) if page_w > 0 else 340.0
        self._chart_width_used = chart_width
        self._radar_chart = AcademicRadarChart(
            profile=profile,
            previous_profile=self.previous_profile,
            is_comparing=self._is_comparing,
            on_metric_tap=self._on_metric_click,
            width=chart_width,
            alignment=ft.Alignment(0, 0),
        )

        # 3. Strongest & Weakest summary with clean hierarchy
        def _format_metric_spans(metric_list: list[RadarMetric]) -> list[ft.TextSpan]:
            spans: list[ft.TextSpan] = []
            for idx, m in enumerate(metric_list):
                if idx > 0:
                    spans.append(ft.TextSpan("  ·  ", ft.TextStyle(color=C.TEXT_SECONDARY, size=11)))
                spans.append(ft.TextSpan(f"{m.label} ", ft.TextStyle(weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY, size=12)))
                spans.append(ft.TextSpan(f"{m.display_score}", ft.TextStyle(weight=ft.FontWeight.W_500, color=C.TEXT_SECONDARY, size=12)))
            return spans

        summary_cols = []
        if profile.strongest_metrics:
            summary_cols.append(
                ft.Column(
                    controls=[
                        ft.Text("Nổi bật", size=11, weight=ft.FontWeight.W_700, color=C.SAFE),
                        ft.Text(spans=_format_metric_spans(profile.strongest_metrics)),
                    ],
                    spacing=2,
                    expand=True,
                )
            )

        if profile.weakest_metrics:
            summary_cols.append(
                ft.Column(
                    controls=[
                        ft.Text("Cần củng cố", size=11, weight=ft.FontWeight.W_700, color=C.WARNING),
                        ft.Text(spans=_format_metric_spans(profile.weakest_metrics)),
                    ],
                    spacing=2,
                    expand=True,
                )
            )

        summary_box = ft.Container(
            content=ft.Row(
                controls=summary_cols,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.START,
                spacing=12,
            ),
            bgcolor=C.BG,
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            border_radius=8,
            border=ft.Border.all(1, C.BORDER),
        ) if summary_cols else ft.Container()

        # Insight text
        insight_pill = ft.Container(
            content=ft.Text(
                profile.insight_text,
                size=12,
                italic=True,
                color=C.TEXT_SECONDARY,
                text_align=ft.TextAlign.CENTER,
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            alignment=ft.Alignment(0, 0),
            padding=ft.Padding.symmetric(horizontal=8),
        )

        # 4. KPI row (GPA 10, GPA 4, Credits, Training points)
        kpi_items = []
        if profile.gpa_10 is not None:
            kpi_items.append(self._make_kpi_card("GPA (10)", f"{profile.gpa_10:.2f}"))
        if profile.gpa_4 is not None:
            kpi_items.append(self._make_kpi_card("GPA (4)", f"{profile.gpa_4:.2f}"))
        if profile.credits_earned is not None:
            kpi_items.append(self._make_kpi_card("Tín chỉ", f"{int(profile.credits_earned)}"))
        if profile.training_point is not None:
            kpi_items.append(self._make_kpi_card("Rèn luyện", f"{int(profile.training_point)}"))

        kpi_row = ft.Row(
            controls=kpi_items,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            spacing=8,
        )

        self.content = ft.Column(
            controls=[
                header_row,
                ft.Row(controls=[self._sem_dropdown]),
                self._radar_chart,
                summary_box,
                insight_pill,
                ft.Divider(height=1, color=C.BORDER),
                kpi_row,
            ],
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _make_kpi_card(self, label: str, value: str) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(label, size=10, weight=ft.FontWeight.W_500, color=C.TEXT_SECONDARY),
                    ft.Text(value, size=15, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
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
            height=54,
            alignment=ft.Alignment(0, 0),
        )
