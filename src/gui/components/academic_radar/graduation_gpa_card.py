from __future__ import annotations

import flet as ft

from core.academic_radar.gpa_planner import (
    CreditProgress,
    GraduationGpaResult,
    calculate_graduation_target,
)
from core.academic_radar.models import SemesterGradeData
from gui.core.theme import C


class GraduationGpaCard(ft.Container):
    """GPA ra trường kỳ vọng: cho biết số tín chỉ còn lại cần đạt GPA tối thiểu bao nhiêu."""

    def __init__(
        self,
        target_text: str = "",
        *,
        semesters: list[SemesterGradeData] | None = None,
        progress: CreditProgress | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._semesters: list[SemesterGradeData] = list(semesters or ())
        self._progress: CreditProgress | None = progress
        self._target_text = target_text
        self.bgcolor = C.SURFACE
        self.border_radius = 16
        self.border = ft.Border.all(1, C.BORDER)
        self.padding = ft.Padding.all(16)
        self._init_controls()

    # ---------- public API ----------
    def set_data(
        self,
        semesters: list[SemesterGradeData],
        progress: CreditProgress | None,
    ) -> None:
        self._semesters = semesters
        self._progress = progress
        self._summary_text.value = self._format_summary()
        self._result_col.controls = self._build_result_controls()
        updated_any = False
        for ctrl in (self._summary_text, self._result_col):
            try:
                ctrl.update()
                updated_any = True
            except Exception:
                continue
        if not updated_any:
            try:
                self.update()
            except Exception:
                pass

    def rebuild(self) -> None:
        self.bgcolor = C.SURFACE
        self.border = ft.Border.all(1, C.BORDER)
        self._title_icon.color = C.ACCENT
        self._title_text.color = C.TEXT_PRIMARY
        self._summary_text.color = C.TEXT_SECONDARY
        self._summary_text.value = self._format_summary()
        self._target_field.border_color = C.BORDER
        self._target_field.focused_border_color = C.ACCENT
        self._target_field.bgcolor = C.BG
        self._target_field.color = C.TEXT_PRIMARY
        self._target_field.cursor_color = C.ACCENT
        self._target_field.label_style = ft.TextStyle(size=12, color=C.TEXT_SECONDARY)
        self._result_col.controls = self._build_result_controls()

    # ---------- internals ----------
    def _format_summary(self) -> str:
        p = self._progress
        if p is not None:
            return (
                f"Đã tích lũy {p.earned_credits:.0f}/{p.required_credits:.0f} TC · "
                f"còn {max(p.required_credits - p.earned_credits, 0):.0f} TC"
            )
        return "Chưa có số tín chỉ ra trường từ Portal."

    def _parse_target(self) -> float | None:
        raw = (self._target_text or "").strip().replace(",", ".")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return -1.0

    def _on_target_change(self, e: ft.ControlEvent) -> None:
        data_val = getattr(e, "data", None)
        if isinstance(data_val, str):
            raw_val = data_val
        else:
            ctrl = getattr(e, "control", None)
            ctrl_val = getattr(ctrl, "value", None)
            raw_val = str(ctrl_val) if ctrl_val is not None else ""
        self._target_text = raw_val
        if self._target_field.value != raw_val:
            self._target_field.value = raw_val
        self._result_col.controls = self._build_result_controls()
        for ctrl in (self._result_col, self):
            try:
                ctrl.update()
                return
            except Exception:
                continue

    def _note(self, text: str, color: str | None = None) -> ft.Text:
        return ft.Text(text, size=12.5, color=color or C.TEXT_SECONDARY)

    def _init_controls(self) -> None:
        self._title_icon = ft.Icon(ft.Icons.SCHOOL_ROUNDED, size=18, color=C.ACCENT)
        self._title_text = ft.Text(
            "GPA ra trường kỳ vọng",
            size=16,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY,
            expand=True,
        )
        title = ft.Row(
            controls=[self._title_icon, self._title_text],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._summary_text = self._note(self._format_summary())
        self._target_field = ft.TextField(
            value=self._target_text,
            label="GPA ra trường hệ 4",
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
            controls=[
                title,
                self._summary_text,
                ft.Row(controls=[self._target_field]),
                self._result_col,
            ],
            spacing=10,
        )

    def _build_result_controls(self) -> list[ft.Control]:
        target = self._parse_target()
        if target is None:
            return []
        if target < 0:
            return [self._note("Giá trị không hợp lệ. Hãy nhập số từ 0.0 đến 4.0.", C.WARNING)]

        r: GraduationGpaResult = calculate_graduation_target(self._semesters, self._progress, target)

        if r.status in ("invalid", "no_progress", "no_gpa"):
            return [self._note(r.message, C.WARNING)]
        if r.status == "completed":
            return [self._note(r.message)]

        gpa_txt = f"{r.current_gpa4:.2f}" if r.current_gpa4 is not None else "—"
        src = "Portal" if r.gpa_source == "portal" else "tự tính từ bảng điểm"
        controls: list[ft.Control] = [self._note(
            f"GPA tích lũy hiện tại: {gpa_txt} ({src}) · Còn lại: {r.remaining_credits:.0f} TC"
        )]

        if r.status == "impossible":
            controls.append(ft.Text(r.message, size=13, weight=ft.FontWeight.W_600, color=C.CRITICAL))
            return controls

        if r.status == "achieved":
            controls.append(ft.Text(r.message, size=13, weight=ft.FontWeight.W_600, color=C.SAFE))
        else:
            controls.append(ft.Text(
                f"Cần GPA ≥ {r.required_avg4:.2f}/4.0 ở {r.remaining_credits:.0f} TC còn lại, "
                f"tức trung bình tối thiểu {r.letter} (≥ {r.min_score10:.1f}/10).",
                size=13, weight=ft.FontWeight.W_600, color=C.ACCENT,
            ))
            controls.append(self._note(
                f"GPA ra trường tối đa có thể đạt: {r.max_possible_gpa4:.2f}"
            ))
        controls.append(self._note(
            "Tính theo trọng số tín chỉ: (GPA mục tiêu × TC ra trường − GPA hiện tại × TC đã tích lũy) "
            "÷ TC còn lại. Có thể đổi môn này bù môn kia miễn trung bình đạt yêu cầu."
        ))
        return controls
