from __future__ import annotations

import math
from typing import Callable, Optional
import flet as ft
import flet.canvas as cv

from core.academic_radar.color_scale import (
    POLYGON_RADIAL_GRADIENT_COLORS,
    POLYGON_RADIAL_GRADIENT_STOPS,
    hex_with_alpha,
)
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
    """6-axis Academic Radar Visualization with polygon-bound RPG character-stat radial gradient."""

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
        self._center_y = height / 2.0 - 2.0
        self._radius = 86.0

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

        # 1. Reference rings (20, 40, 60, 80, 100) on neutral background
        ring_paint = ft.Paint(
            color=C.BORDER,
            stroke_width=0.8,
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
            prev_points: list[cv.PathElement] = []
            for i, metric in enumerate(self.previous_profile.ordered_metrics):
                score = metric.score if metric.score is not None else 50.0
                x, y = self._get_vertex(i, score)
                if i == 0:
                    prev_points.append(cv.Path.MoveTo(x, y))
                else:
                    prev_points.append(cv.Path.LineTo(x, y))
            prev_points.append(cv.Path.Close())

            prev_fill = ft.Paint(
                color=hex_with_alpha(C.BORDER_STRONG, 0.08),
                style=ft.PaintingStyle.FILL,
            )
            shapes.append(cv.Path(elements=prev_points, paint=prev_fill))

            prev_stroke = ft.Paint(
                color=C.BORDER_STRONG,
                stroke_width=1.5,
                stroke_dash_pattern=[4, 4],
                style=ft.PaintingStyle.STROKE,
            )
            shapes.append(cv.Path(elements=prev_points, paint=prev_stroke))

        # 4. Current Semester Polygon (with radial gradient from center red/orange -> mid yellow -> tips green)
        if self.profile.is_polygon_viable:
            fill_points: list[cv.PathElement] = []
            for i, metric in enumerate(self.profile.ordered_metrics):
                score = metric.score if metric.score is not None else 50.0
                x, y = self._get_vertex(i, score)
                if i == 0:
                    fill_points.append(cv.Path.MoveTo(x, y))
                else:
                    fill_points.append(cv.Path.LineTo(x, y))
            fill_points.append(cv.Path.Close())

            # Radial gradient fill inside the polygon
            poly_gradient = ft.PaintRadialGradient(
                center=ft.Offset(self._center_x, self._center_y),
                radius=self._radius,
                colors=POLYGON_RADIAL_GRADIENT_COLORS,
                color_stops=POLYGON_RADIAL_GRADIENT_STOPS,
            )
            poly_fill_paint = ft.Paint(
                gradient=poly_gradient,
                style=ft.PaintingStyle.FILL,
            )
            shapes.append(cv.Path(elements=fill_points, paint=poly_fill_paint))

            # Slender, soft translucent stroke outline
            dominant_color = (
                self.profile.metrics["total_power"].color
                if self.profile.metrics and "total_power" in self.profile.metrics
                else C.SAFE
            )

            # Subtle soft glow stroke
            stroke_glow_paint = ft.Paint(
                color=hex_with_alpha(dominant_color, 0.18),
                stroke_width=2.4,
                stroke_join=ft.StrokeJoin.ROUND,
                style=ft.PaintingStyle.STROKE,
            )
            shapes.append(cv.Path(elements=fill_points, paint=stroke_glow_paint))

            # Inner slender crisp stroke (1.3px, 65% opacity)
            stroke_paint = ft.Paint(
                color=hex_with_alpha(dominant_color, 0.65),
                stroke_width=1.3,
                stroke_join=ft.StrokeJoin.ROUND,
                style=ft.PaintingStyle.STROKE,
            )
            shapes.append(cv.Path(elements=fill_points, paint=stroke_paint))

        # 5. Character-stat vertex nodes (outer glow + inner node + center spark)
        for i, metric in enumerate(self.profile.ordered_metrics):
            score = metric.score if metric.score is not None else 50.0
            x, y = self._get_vertex(i, score)
            node_color = metric.color

            # Outer glow halo (slender)
            shapes.append(
                cv.Circle(
                    x, y, 6.0,
                    paint=ft.Paint(color=hex_with_alpha(node_color, 0.20), style=ft.PaintingStyle.FILL),
                )
            )
            # Inner core dot
            shapes.append(
                cv.Circle(
                    x, y, 3.8,
                    paint=ft.Paint(color=node_color, style=ft.PaintingStyle.FILL),
                )
            )
            # Center highlight spark
            shapes.append(
                cv.Circle(
                    x, y, 1.4,
                    paint=ft.Paint(color=C.ON_ACCENT, style=ft.PaintingStyle.FILL),
                )
            )

        canvas_control = cv.Canvas(
            shapes=shapes,
            width=self._chart_width,
            height=self._chart_height,
        )

        # 6. Interactive Node Label overlays positioned closely and compactly around nodes
        overlay_controls: list[ft.Control] = [canvas_control]

        for i, metric in enumerate(self.profile.ordered_metrics):
            # Compact radial distance bringing labels close to nodes without clipping
            label_r = 112.0 if i in (0, 3) else 114.0
            angle = ANGLES[i % len(ANGLES)]
            label_x = self._center_x + (self._radius * label_r / 100.0) * math.cos(angle)
            label_y = self._center_y + (self._radius * label_r / 100.0) * math.sin(angle)
            score_text = str(metric.display_score) if metric.display_score is not None else "—"

            if i == 0:  # Top (12h) - spaced comfortably above top node
                box_left = label_x - 35
                box_top = label_y - 34
            elif i == 3:  # Bottom (6h) - spaced comfortably below bottom node
                box_left = label_x - 35
                box_top = label_y - 2
            elif i in (1, 2):  # Right side (2h, 4h)
                box_left = label_x - 14
                box_top = label_y - 18
            else:  # Left side (8h, 10h)
                box_left = label_x - 56
                box_top = label_y - 18

            # Pill button for label + score with smooth circular hover shape
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
                left=box_left,
                top=box_top,
                width=66,
                padding=ft.Padding.symmetric(horizontal=4, vertical=4),
                border_radius=16,
                alignment=ft.Alignment(0, 0),
                ink=True,
                tooltip=f"{metric.label}: {score_text} ({metric.status})",
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
