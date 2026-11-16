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
            letter = (course.letter_grade or "—").strip()
            letter_color = self._letter_grade_color(letter)

            # Left course info
            info_col = ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(
                            course.course_name,
                            size=13.5,
                            weight=ft.FontWeight.W_500,
                            color=C.TEXT_PRIMARY,
                            max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Row(
                            controls=[
                                ft.Text(course.course_code or "HP", size=11, color=C.TEXT_SECONDARY),
                                ft.Text("·", size=11, color=C.TEXT_SECONDARY),
                                ft.Text(f"{course.credits:.0f} TC", size=11, color=C.TEXT_SECONDARY),
                            ],
                            spacing=4,
                        ),
                    ],
                    spacing=3,
                ),
                expand=True,
                padding=ft.Padding.only(right=12),
            )

            # Grade pills with aligned fixed-width columns
            grades_row = ft.Row(
                controls=[
                    self._make_mini_grade("QT", qt_str),
                    self._make_mini_grade("Thi", thi_str),
                    self._make_mini_grade("TK", tk_str, is_bold=True),
                    ft.Container(
                        content=ft.Text(
                            letter,
                            size=12,
                            weight=ft.FontWeight.BOLD,
                            color=letter_color,
                        ),
                        bgcolor=C.BG,
                        width=34,
                        height=28,
                        alignment=ft.Alignment(0, 0),
                        border_radius=6,
                        border=ft.Border.all(1, C.BORDER),
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )

            card = ft.Container(
                content=ft.Row(
                    controls=[info_col, grades_row],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                bgcolor=C.SURFACE,
                padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                border_radius=10,
                border=ft.Border.all(1, C.BORDER),
            )
            cards.append(card)

        self.controls = cards

    def _make_mini_grade(self, label: str, value: str, is_bold: bool = False) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(label, size=10, weight=ft.FontWeight.W_500, color=C.TEXT_SECONDARY),
                    ft.Text(
                        value,
                        size=12.5,
                        weight=ft.FontWeight.BOLD if is_bold else ft.FontWeight.W_500,
                        color=C.TEXT_PRIMARY if is_bold else C.TEXT_SECONDARY,
                    ),
                ],
                spacing=1,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            width=30,
            alignment=ft.Alignment(0, 0),
        )
