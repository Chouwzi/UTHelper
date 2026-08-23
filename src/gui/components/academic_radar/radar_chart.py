from __future__ import annotations

import math
from typing import Callable, Optional
import flet as ft
import flet.canvas as cv

from core.academic_radar.models import RadarMetric, RadarProfile
from gui.core.theme import C

# Angles in radians for 6 vertices (Clockwise starting at 12 o'clock)
ANGLES = [
    -math.pi / 2.0,            # 0: Total Power (Top)
    -math.pi / 6.0,            # 1: Foundation (Top-Right)
    math.pi / 6.0,             # 2: Breakthrough (Bottom-Right)
    math.pi / 2.0,             # 3: Rhythm (Bottom)
    5.0 * math.pi / 6.0,       # 4: Finish (Bottom-Left)
    -5.0 * math.pi / 6.0,      # 5: Resilience (Top-Left)
]


class AcademicRadarChart(ft.Container):
    """6-axis Academic Radar Visualization built with Flet Canvas & interactive node overlays."""

    def __init__(
        self,
        profile: RadarProfile,
        previous_profile: Optional[RadarProfile] = None,
        is_comparing: bool = False,
        on_metric_tap: Optional[Callable[[RadarMetric], None]] = None,
        width: float = 340,
        height: float = 290,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.profile = profile
        self.previous_profile = previous_profile
        self.is_comparing = is_comparing
        self.on_metric_tap = on_metric_tap
        self._chart_width = width
        self._chart_height = height
        self._center_x = width / 2.0
        self._center_y = height / 2.0 - 5.0
        self._radius = 90.0

        self.width = width
        self.height = height
        self._build_chart()

    def update_profile(
        self,
        profile: RadarProfile,
        previous_profile: Optional[RadarProfile] = None,
        is_comparing: bool = False,
    ) -> None:
        self.profile = profile
        self.previous_profile = previous_profile
        self.is_comparing = is_comparing
        self._build_chart()
        try:
            self.update()
        except Exception:
            pass

    def _get_vertex(self, index: int, score_pct: float) -> tuple[float, float]:
        angle = ANGLES[index % len(ANGLES)]
        r = self._radius * (score_pct / 100.0)
        x = self._center_x + r * math.cos(angle)
        y = self._center_y + r * math.sin(angle)
        return x, y

    def _build_chart(self) -> None:
        shapes: list[cv.Shape] = []

        # 1. Reference rings (20, 40, 60, 80, 100)
        ring_paint = ft.Paint(
            color=C.BORDER,
            stroke_width=1,
            style=ft.PaintingStyle.STROKE,
        )
        for pct in (20, 40, 60, 80, 100):
            points: list[cv.PathElement] = []
            for i in range(6):
                x, y = self._get_vertex(i, pct)
                if i == 0:
                    points.append(cv.Path.MoveTo(x, y))
                else:
                    points.append(cv.Path.LineTo(x, y))
            points.append(cv.Path.Close())
            shapes.append(cv.Path(elements=points, paint=ring_paint))

        # 2. Axis spokes
        for i in range(6):
            x, y = self._get_vertex(i, 100)
            shapes.append(
                cv.Line(
                    self._center_x,
                    self._center_y,
                    x,
                    y,
                    paint=ring_paint,
                )
            )

        # 3. Previous Semester Comparison Polygon (if enabled)
        if self.is_comparing and self.previous_profile and self.previous_profile.is_polygon_viable:
            prev_paint = ft.Paint(
                color=C.BORDER_STRONG,
                stroke_width=1.5,
                style=ft.PaintingStyle.STROKE,
            )
            prev_points: list[cv.PathElement] = []
            for i, metric in enumerate(self.previous_profile.ordered_metrics):
                score = metric.score if metric.score is not None else 50.0
                x, y = self._get_vertex(i, score)
                if i == 0:
                    prev_points.append(cv.Path.MoveTo(x, y))
                else:
                    prev_points.append(cv.Path.LineTo(x, y))
            prev_points.append(cv.Path.Close())
            shapes.append(cv.Path(elements=prev_points, paint=prev_paint))

        # 4. Current Semester Polygon
        if self.profile.is_polygon_viable:
            # Fill
            fill_paint = ft.Paint(
                color=C.ACCENT,
                style=ft.PaintingStyle.FILL,
            )
            fill_points: list[cv.PathElement] = []
            for i, metric in enumerate(self.profile.ordered_metrics):
                score = metric.score if metric.score is not None else 50.0
                x, y = self._get_vertex(i, score)
                if i == 0:
                    fill_points.append(cv.Path.MoveTo(x, y))
                else:
                    fill_points.append(cv.Path.LineTo(x, y))
            fill_points.append(cv.Path.Close())
            # Background fill
            shapes.append(cv.Path(elements=fill_points, paint=fill_paint))

            # Stroke outline
            stroke_paint = ft.Paint(
                color=C.ACCENT,
                stroke_width=2.5,
                style=ft.PaintingStyle.STROKE,
            )
            shapes.append(cv.Path(elements=fill_points, paint=stroke_paint))

        # 5. Nodes (core dots & outer glow)
        for i, metric in enumerate(self.profile.ordered_metrics):
            score = metric.score if metric.score is not None else 50.0
            x, y = self._get_vertex(i, score)
            node_color = metric.color

            # Outer glow
            shapes.append(
                cv.Circle(
                    x, y, 7.0,
                    paint=ft.Paint(color=node_color, style=ft.PaintingStyle.FILL),
                )
            )
            # Center dot
            shapes.append(
                cv.Circle(
                    x, y, 3.0,
                    paint=ft.Paint(color=C.ON_ACCENT, style=ft.PaintingStyle.FILL),
                )
            )

        canvas_control = cv.Canvas(
            shapes=shapes,
            width=self._chart_width,
            height=self._chart_height,
        )

        # 6. Interactive Node Label overlays around perimeter
        overlay_controls: list[ft.Control] = [canvas_control]

        for i, metric in enumerate(self.profile.ordered_metrics):
            label_x, label_y = self._get_vertex(i, 130)
            score_text = str(metric.display_score) if metric.display_score is not None else "—"

            # Pill button for label + score
            node_btn = ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(
                            metric.label,
                            size=10,
                            weight=ft.FontWeight.W_700,
                            color=C.TEXT_PRIMARY,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ft.Container(
                            content=ft.Text(
                                score_text,
                                size=11,
                                weight=ft.FontWeight.BOLD,
                                color=C.ON_ACCENT,
                            ),
                            bgcolor=metric.color,
                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                            border_radius=10,
                        ),
                    ],
                    spacing=2,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                left=label_x - 35,
                top=label_y - 20,
                width=70,
                alignment=ft.Alignment(0, 0),
                ink=True,
                on_click=lambda _, m=metric: self._handle_tap(m),
            )
            overlay_controls.append(node_btn)

        self.content = ft.Stack(
            controls=overlay_controls,
            width=self._chart_width,
            height=self._chart_height,
        )

    def _handle_tap(self, metric: RadarMetric) -> None:
        if self.on_metric_tap:
            self.on_metric_tap(metric)
