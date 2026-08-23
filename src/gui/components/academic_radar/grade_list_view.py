from __future__ import annotations

import flet as ft
from core.academic_radar.models import CourseGradeItem
from gui.core.theme import C


class AcademicGradeListView(ft.Column):
    """List of course grade cards for a semester."""

    def __init__(self, courses: list[CourseGradeItem], **kwargs) -> None:
        super().__init__(**kwargs)
        self.courses = courses
        self.spacing = 8
        self._build_list()

    def update_courses(self, courses: list[CourseGradeItem]) -> None:
        self.courses = courses
        self._build_list()
        try:
            self.update()
        except Exception:
            pass

    def _letter_grade_color(self, letter: str) -> str:
        letter = (letter or "").strip().upper()
        if letter.startswith("A"):
            return C.SAFE
        if letter.startswith("B"):
            return C.ACCENT
        if letter.startswith("C"):
            return C.WARNING
        if letter.startswith("D"):
            return C.WARNING
        if letter.startswith("F"):
            return C.CRITICAL
        return C.TEXT_PRIMARY

    def _build_list(self) -> None:
        if not self.courses:
            self.controls = [
                ft.Container(
                    content=ft.Text("Chưa có danh sách học phần cho kỳ này.", size=13, color=C.TEXT_SECONDARY),
                    alignment=ft.Alignment(0, 0),
                    padding=20,
                )
            ]
            return

        header = ft.Row(
            controls=[
                ft.Text("DANH SÁCH HỌC PHẦN", size=13, weight=ft.FontWeight.BOLD, color=C.TEXT_SECONDARY),
                ft.Text(f"{len(self.courses)} môn", size=12, color=C.TEXT_SECONDARY),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        cards: list[ft.Control] = [header]

        for course in self.courses:
            qt_str = f"{course.process_grade:.1f}" if course.process_grade is not None else "—"
            thi_str = f"{course.exam_grade:.1f}" if course.exam_grade is not None else "—"
            tk_str = f"{course.final_grade:.1f}" if course.final_grade is not None else "—"
            letter = course.letter_grade or "—"
            letter_color = self._letter_grade_color(letter)

            # Left course info
            info_col = ft.Column(
                controls=[
                    ft.Text(
                        course.course_name,
                        size=14,
                        weight=ft.FontWeight.W_600,
                        color=C.TEXT_PRIMARY,
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.Row(
                        controls=[
                            ft.Text(course.course_code or "HP", size=11, color=C.TEXT_SECONDARY),
                            ft.Text(f"· {course.credits:.0f} TC", size=11, color=C.TEXT_SECONDARY),
                        ],
                        spacing=4,
                    ),
                ],
                spacing=2,
                expand=True,
            )

            # Grade pills
            grades_row = ft.Row(
                controls=[
                    self._make_mini_grade("QT", qt_str),
                    self._make_mini_grade("Thi", thi_str),
                    self._make_mini_grade("TK", tk_str, is_bold=True),
                    ft.Container(
                        content=ft.Text(
                            letter,
                            size=13,
                            weight=ft.FontWeight.BOLD,
                            color=letter_color,
                        ),
                        bgcolor=C.BG,
                        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                        border_radius=6,
                        border=ft.Border.all(1, C.BORDER),
                    ),
                ],
                spacing=6,
            )

            card = ft.Container(
                content=ft.Row(
                    controls=[info_col, grades_row],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                bgcolor=C.SURFACE,
                padding=ft.Padding.all(12),
                border_radius=12,
                border=ft.Border.all(1, C.BORDER),
            )
            cards.append(card)

        self.controls = cards

    def _make_mini_grade(self, label: str, value: str, is_bold: bool = False) -> ft.Column:
        return ft.Column(
            controls=[
                ft.Text(label, size=10, color=C.TEXT_SECONDARY),
                ft.Text(
                    value,
                    size=12,
                    weight=ft.FontWeight.BOLD if is_bold else ft.FontWeight.W_500,
                    color=C.TEXT_PRIMARY,
                ),
            ],
            spacing=1,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
