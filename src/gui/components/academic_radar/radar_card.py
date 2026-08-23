from __future__ import annotations

from typing import Callable, Optional
import flet as ft

from core.academic_radar.models import RadarMetric, RadarProfile, SemesterGradeData
from gui.components.academic_radar.metric_detail_sheet import MetricDetailSheet
from gui.components.academic_radar.radar_chart import AcademicRadarChart
from gui.core.theme import C


class AcademicRadarCard(ft.Container):
    """Hero container card presenting the semester Academic Radar, KPIs, and Highlights."""

    def __init__(
        self,
        profiles: list[RadarProfile],
        selected_index: int = 0,
        on_semester_change: Optional[Callable[[int], None]] = None,
        page: Optional[ft.Page] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.profiles = profiles
        self.selected_index = selected_index
        self.on_semester_change = on_semester_change
        self._page_ref = page
        self._is_comparing = False

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
            MetricDetailSheet.show(p, metric)

    def _build_ui(self) -> None:
        profile = self.current_profile
        if not profile:
            self.content = ft.Text("Chưa có dữ liệu học kỳ", size=14, color=C.TEXT_SECONDARY)
            return

        # 1. Header: Title & Semester Selector
        title = ft.Row(
            controls=[
                ft.Text("Hồ sơ học tập", size=16, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY),
                ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=16, color=C.TEXT_SECONDARY),
            ],
            spacing=6,
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
            text_size=12,
            bgcolor=C.BG,
            border_color=C.BORDER,
            border_radius=8,
            dense=True,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            expand=True,
        )

        # Compare button
        has_previous = self.previous_profile is not None
        self._compare_btn = ft.TextButton(
            content="Đang so sánh" if self._is_comparing else "So sánh kỳ trước",
            icon=ft.Icons.CHECK_CIRCLE_ROUNDED if self._is_comparing else ft.Icons.RADIO_BUTTON_UNCHECKED,
            icon_color=C.ACCENT if self._is_comparing else C.TEXT_SECONDARY,
            on_click=self._toggle_compare,
            visible=has_previous,
        )

        header_row = ft.Row(
            controls=[
                title,
                self._compare_btn,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # 2. Radar Chart
        self._radar_chart = AcademicRadarChart(
            profile=profile,
            previous_profile=self.previous_profile,
            is_comparing=self._is_comparing,
            on_metric_tap=self._on_metric_click,
            alignment=ft.Alignment(0, 0),
        )

        # 3. Strongest & Weakest summary
        summary_rows = []
        if profile.strongest_metrics:
            names = " · ".join(f"{m.label} ({m.display_score})" for m in profile.strongest_metrics)
            summary_rows.append(
                ft.Row(
                    controls=[
                        ft.Text("Nổi bật:", size=12, weight=ft.FontWeight.W_600, color=C.SAFE),
                        ft.Text(names, size=12, weight=ft.FontWeight.W_500, color=C.TEXT_PRIMARY),
                    ],
                    spacing=6,
                )
            )

        if profile.weakest_metrics:
            names = " · ".join(f"{m.label} ({m.display_score})" for m in profile.weakest_metrics)
            summary_rows.append(
                ft.Row(
                    controls=[
                        ft.Text("Cần củng cố:", size=12, weight=ft.FontWeight.W_600, color=C.WARNING),
                        ft.Text(names, size=12, weight=ft.FontWeight.W_500, color=C.TEXT_PRIMARY),
                    ],
                    spacing=6,
                )
            )

        summary_box = ft.Container(
            content=ft.Column(controls=summary_rows, spacing=4),
            bgcolor=C.BG,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border_radius=8,
            border=ft.Border.all(1, C.BORDER),
        )

        # Insight text
        insight_pill = ft.Text(
            profile.insight_text,
            size=12,
            italic=True,
            color=C.TEXT_SECONDARY,
            text_align=ft.TextAlign.CENTER,
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
            alignment=ft.MainAxisAlignment.SPACE_EVENLY,
        )

        self.content = ft.Column(
            controls=[
                header_row,
                self._sem_dropdown,
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
                    ft.Text(label, size=11, color=C.TEXT_SECONDARY),
                    ft.Text(value, size=16, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                ],
                spacing=2,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.BG,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border_radius=8,
            border=ft.Border.all(1, C.BORDER),
        )
