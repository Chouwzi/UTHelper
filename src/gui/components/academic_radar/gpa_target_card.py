from __future__ import annotations

import flet as ft

from core.academic_radar.gpa_planner import GpaTargetResult, calculate_gpa_target
from core.academic_radar.models import SemesterGradeData
from gui.core.theme import C


class GpaTargetCard(ft.Container):
    """Nhập GPA hệ 4 mong muốn của học kỳ -> hiển thị điểm cần đạt ở các môn chưa có điểm."""

    def __init__(self, target_text: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._semester: SemesterGradeData | None = None
        self._target_text = target_text
        self.bgcolor = C.SURFACE
        self.border_radius = 16
        self.border = ft.Border.all(1, C.BORDER)
        self.padding = ft.Padding.all(16)
        self._build_ui()

    # ---------- public API ----------
    def set_semester(self, semester: SemesterGradeData | None) -> None:
        self._semester = semester
        self._build_ui()
        try:
            self.update()
        except Exception:
            pass

    def rebuild(self) -> None:
        """Dựng lại UI (dùng khi đổi theme)."""
        self.bgcolor = C.SURFACE
        self.border = ft.Border.all(1, C.BORDER)
        self._build_ui()

    # ---------- internals ----------
    def _parse_target(self) -> float | None:
        raw = (self._target_text or "").strip().replace(",", ".")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return -1.0  # đánh dấu không hợp lệ

    def _on_target_change(self, e: ft.ControlEvent) -> None:
        self._target_text = e.control.value or ""
        self._refresh_result()

    def _refresh_result(self) -> None:
        self._result_col.controls = self._build_result_controls()
        try:
            self._result_col.update()
        except Exception:
            pass

    def _build_ui(self) -> None:
        title = ft.Row(
            controls=[
                ft.Icon(ft.Icons.TRACK_CHANGES_ROUNDED, size=18, color=C.ACCENT),
                ft.Text(
                    "GPA mong muốn trong kỳ",
                    size=16,
                    weight=ft.FontWeight.W_700,
                    color=C.TEXT_PRIMARY,
                    expand=True,
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        hint = ft.Text(
            "Nhập GPA hệ 4 muốn đạt (VD: 4.0) để xem các môn chưa có điểm cần đạt bao nhiêu.",
            size=12, color=C.TEXT_SECONDARY,
        )
        self._target_field = ft.TextField(
            value=self._target_text,
            label="GPA hệ 4 mục tiêu",
            hint_text="0.0 – 4.0",
            keyboard_type=ft.KeyboardType.NUMBER,
            expand=True,
            height=48,
            text_size=14,
            content_padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            border_radius=10,
            border_color=C.BORDER,
            focused_border_color=C.ACCENT,
            bgcolor=C.BG,
            color=C.TEXT_PRIMARY,
            cursor_color=C.ACCENT,
            label_style=ft.TextStyle(size=12, color=C.TEXT_SECONDARY),
            on_change=self._on_target_change,
            on_submit=self._on_target_change,
        )
        self._result_col = ft.Column(controls=self._build_result_controls(), spacing=8)
        self.content = ft.Column(
            controls=[title, hint, ft.Row(controls=[self._target_field]), self._result_col],
            spacing=10,
        )

    def _note(self, text: str, color: str | None = None) -> ft.Text:
        return ft.Text(text, size=12.5, color=color or C.TEXT_SECONDARY)

    def _build_result_controls(self) -> list[ft.Control]:
        target = self._parse_target()
        if target is None:
            return []
        if target < 0:
            return [self._note("Giá trị không hợp lệ. Hãy nhập số từ 0.0 đến 4.0.", C.WARNING)]

        r: GpaTargetResult = calculate_gpa_target(self._semester, target)

        if r.status in ("invalid", "no_courses", "no_pending"):
            return [self._note(r.message, C.WARNING if r.status == "invalid" else C.TEXT_SECONDARY)]

        controls: list[ft.Control] = []
        cur = f"{r.current_gpa4:.2f}" if r.current_gpa4 is not None else "—"
        controls.append(self._note(
            f"Đã có điểm: {r.graded_credits:.0f} TC (GPA hiện tại {cur}) · "
            f"Chưa có điểm: {r.pending_credits:.0f} TC"
        ))

        if r.status == "impossible":
            controls.append(ft.Text(r.message, size=13, weight=ft.FontWeight.W_600, color=C.CRITICAL))
            return controls

        if r.status == "achieved":
            controls.append(ft.Text(r.message, size=13, weight=ft.FontWeight.W_600, color=C.SAFE))
        else:
            controls.append(ft.Text(
                f"Cần trung bình ≥ {r.required_avg4:.2f}/4.0 ở các môn còn lại, "
                f"tức mỗi môn tối thiểu {r.uniform_letter} (≥ {r.uniform_min_score10:.1f}/10).",
                size=13, weight=ft.FontWeight.W_600, color=C.ACCENT,
            ))

        for need in r.pending:
            c = need.course
            controls.append(ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                ft.Text(c.course_name, size=13, color=C.TEXT_PRIMARY,
                                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(f"{c.credits:.0f} TC", size=11, color=C.TEXT_SECONDARY),
                            ],
                            spacing=2, expand=True,
                        ),
                        ft.Column(
                            controls=[
                                ft.Text(f"≥ {need.letter}", size=13, weight=ft.FontWeight.BOLD, color=C.ACCENT),
                                ft.Text(f"≥ {need.min_score10:.1f}/10", size=11, color=C.TEXT_SECONDARY),
                            ],
                            spacing=2, horizontal_alignment=ft.CrossAxisAlignment.END,
                        ),
                    ],
                    spacing=8,
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                bgcolor=C.BG,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                border_radius=10,
                border=ft.Border.all(1, C.BORDER),
            ))

        controls.append(self._note(
            "Đây là mức tối thiểu nếu mọi môn còn lại đạt điểm như nhau; có thể đổi môn này bù môn kia "
            "miễn trung bình có trọng số tín chỉ đạt yêu cầu. Quy đổi theo thang điểm chuẩn "
            "(A ≥ 8.5, B+ ≥ 7.8, B ≥ 7.0, C+ ≥ 6.3, C ≥ 5.5, D+ ≥ 4.8, D ≥ 4.0)."
        ))
        return controls
