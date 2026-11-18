"""Tab "Môn học": các môn đang học + điểm và % tham gia tiết học.

Giao diện theo phong cách cổng thông tin sinh viên: thanh tổng quan, thẻ môn có mã
môn / trạng thái, thanh tiến độ chuyên cần và bảng điểm thành phần.
"""
from __future__ import annotations

from typing import Callable, Optional

import flet as ft

from core.course_grades_service import CourseWithGrades
from gui.components.course_grade_table import build_grade_table
from gui.core.theme import C, semantic_palette


def _fmt_pct_color(value: Optional[float]) -> str:
    if value is None:
        return C.TEXT_SECONDARY
    if value >= 80:
        return C.SAFE
    if value >= 50:
        return C.WARNING
    return C.CRITICAL


_PLACEHOLDER_ROWS = {"grade item", "course total", "tổng khóa học", "tổng điểm khóa học"}


_TOTAL_ROWS = {"course total", "tổng khóa học", "tổng điểm khóa học"}


def _is_total_row(row) -> bool:
    return row.name.strip().lower() in _TOTAL_ROWS


def _is_preparing(course: CourseWithGrades) -> bool:
    """Môn chưa có dữ liệu nào (điểm và % đều là "-") -> "Chuẩn bị học"."""
    if course.error or course.report is None:
        return False
    rows = course.report.rows
    if any(r.grade or r.percentage for r in rows):
        return False
    # Có mục điểm thật (ngoài "Grade item" / "Course total") -> môn đã bắt đầu, không gắn nhãn.
    return all(r.name.strip().lower() in _PLACEHOLDER_ROWS for r in rows)


class CoursesView(ft.Container):
    def __init__(self, on_close: Optional[Callable] = None,
                 on_refresh: Optional[Callable] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._on_close = on_close
        self._on_refresh = on_refresh
        self._courses: list[CourseWithGrades] = []

        self._back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_ROUNDED, icon_color=C.TEXT_SECONDARY,
            icon_size=20, tooltip="Quay lại", on_click=self._handle_close,
        )
        self._title = ft.Text(
            "Môn học", size=16, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY,
            text_align=ft.TextAlign.CENTER, expand=True,
        )
        self._refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED, icon_color=C.TEXT_SECONDARY,
            icon_size=20, tooltip="Làm mới", on_click=self._handle_refresh,
        )
        header = ft.Row(
            [self._back_btn, self._title, self._refresh_btn],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._loading_row = ft.Row(
            [ft.ProgressRing(width=24, height=24, stroke_width=3, color=C.ACCENT),
             ft.Text("Đang tải môn học...", size=13, color=C.TEXT_SECONDARY)],
            alignment=ft.MainAxisAlignment.CENTER, visible=False,
        )
        self._empty_text = ft.Text(
            "Không có môn học nào đang học.", size=13, color=C.TEXT_SECONDARY,
            text_align=ft.TextAlign.CENTER,
        )
        self._empty_state = ft.Container(
            content=self._empty_text, alignment=ft.Alignment(0, 0), padding=40, visible=False,
        )
        self._list = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)

        self.content = ft.Column(
            [header, ft.Divider(height=1, color=C.BORDER),
             self._loading_row, self._empty_state, self._list],
            spacing=8, expand=True,
        )
        self.bgcolor = C.BG
        self.padding = ft.Padding.only(left=16, right=16, top=20, bottom=16)
        self.expand = True
        self.visible = False
        self.offset = ft.Offset(1, 0)
        self.opacity = 0.0
        self.animate_offset = ft.Animation(250, ft.AnimationCurve.EASE_OUT)
        self.animate_opacity = ft.Animation(200)
        self._rendered_palette = semantic_palette()

    # ------------------------------------------------------------------
    def _safe_update(self) -> None:
        try:
            self.update()
        except Exception:
            pass

    def _handle_close(self, _e=None):
        if self._on_close:
            self._on_close()

    def _handle_refresh(self, _e=None):
        if self._on_refresh:
            self._on_refresh()

    def hide(self) -> None:
        self.opacity = 0.0

    def update_theme(self) -> None:
        self.bgcolor = C.BG
        self._title.color = C.TEXT_PRIMARY
        self._back_btn.icon_color = C.TEXT_SECONDARY
        self._refresh_btn.icon_color = C.TEXT_SECONDARY
        if self._courses:
            self._build(self._courses)

    def show_loading(self) -> None:
        self._loading_row.visible = True
        self._empty_state.visible = False
        self._list.visible = False
        self.visible = True
        self.offset = ft.Offset(0, 0)
        self.opacity = 1.0
        self._safe_update()

    def show_error(self) -> None:
        self._loading_row.visible = False
        self._empty_text.value = "Không tải được danh sách môn học.\nKiểm tra tài khoản/kết nối rồi bấm làm mới."
        self._empty_state.visible = True
        self._list.visible = False
        self._safe_update()

    def update_courses(self, courses: list[CourseWithGrades]) -> None:
        self._courses = courses
        self._loading_row.visible = False
        if not courses:
            self._empty_text.value = "Không có môn học nào đang học."
            self._empty_state.visible = True
            self._list.visible = False
        else:
            self._empty_state.visible = False
            self._list.visible = True
            self._build(courses)
        self._safe_update()

    # ------------------------------------------------------------------
    # Thành phần giao diện (phong cách cổng thông tin sinh viên đại học)
    # ------------------------------------------------------------------
    @staticmethod
    def _tint(color: str, opacity: float = 0.12) -> str:
        return ft.Colors.with_opacity(opacity, color)

    def _summary_tile(self, icon: str, label: str, value: str, color: str) -> ft.Container:
        return ft.Container(
            content=ft.Row(
                [ft.Container(
                    content=ft.Icon(icon, size=18, color=color),
                    width=36, height=36, border_radius=10, bgcolor=self._tint(color),
                    alignment=ft.Alignment(0, 0)),
                 ft.Column(
                    [ft.Text(value, size=18, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY),
                     ft.Text(label, size=10.5, color=C.TEXT_SECONDARY, weight=ft.FontWeight.W_500,
                             max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)],
                    spacing=0, tight=True, expand=True)],
                spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=C.SURFACE, border=ft.Border.all(1, C.BORDER), border_radius=12,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10), expand=True,
        )

    def _summary_bar(self, courses: list[CourseWithGrades]) -> ft.Row:
        graded = 0
        pcts: list[float] = []
        for c in courses:
            if c.report is None:
                continue
            if any(r.grade for r in c.report.rows if not _is_total_row(r)):
                graded += 1
            att = c.report.attendance
            if att is not None and att.percentage_value is not None:
                pcts.append(att.percentage_value)
        avg = f"{sum(pcts) / len(pcts):.0f}%" if pcts else "—"
        avg_color = _fmt_pct_color(sum(pcts) / len(pcts)) if pcts else C.TEXT_SECONDARY
        return ft.Row(
            [self._summary_tile(ft.Icons.MENU_BOOK_ROUNDED, "Môn đang học", str(len(courses)), C.ACCENT),
             self._summary_tile(ft.Icons.FACT_CHECK_ROUNDED, "Môn đã có điểm", str(graded), C.SAFE),
             self._summary_tile(ft.Icons.EVENT_AVAILABLE_ROUNDED, "Tham gia TB", avg, avg_color)],
            spacing=8,
        )

    def _pill(self, text: str, color: str) -> ft.Container:
        return ft.Container(
            content=ft.Text(text, size=10.5, weight=ft.FontWeight.W_600, color=color),
            bgcolor=self._tint(color, 0.14), border_radius=20,
            padding=ft.Padding.symmetric(horizontal=9, vertical=3),
        )

    def _attendance_block(self, att) -> ft.Container:
        value = att.percentage_value if att else None
        text = att.percentage if att and att.percentage else "—"
        color = _fmt_pct_color(value)
        ratio = max(0.0, min(1.0, (value or 0) / 100.0))
        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Row([ft.Icon(ft.Icons.EVENT_AVAILABLE_ROUNDED, size=14, color=C.TEXT_SECONDARY),
                            ft.Text("Tỷ lệ tham gia tiết học", size=11.5, color=C.TEXT_SECONDARY,
                                    weight=ft.FontWeight.W_500)], spacing=6),
                    ft.Text(text, size=15, weight=ft.FontWeight.W_700, color=color)],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.ProgressBar(value=ratio, color=color, bgcolor=self._tint(C.TEXT_SECONDARY, 0.18),
                               bar_height=6),
            ], spacing=7),
            bgcolor=C.BG, border=ft.Border.all(1, C.BORDER), border_radius=10,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
        )

    def _grade_table(self, rows) -> ft.Container:
        return build_grade_table(rows)

    def _course_card(self, course: CourseWithGrades) -> ft.Container:
        report = course.report
        rows = report.rows if report else []
        att = report.attendance if report else None
        preparing = _is_preparing(course)

        if course.error:
            accent = C.CRITICAL
        elif preparing or not rows:
            accent = C.TEXT_SECONDARY
        else:
            accent = _fmt_pct_color(att.percentage_value if att else None)
            if att is None or att.percentage_value is None:
                accent = C.ACCENT

        # --- Tiêu đề: mã môn + tên + trạng thái ---------------------------------
        code = (course.shortname or f"ID {course.course_id}").strip()
        badges: list[ft.Control] = []
        if course.error:
            badges.append(self._pill("Lỗi tải", C.CRITICAL))
        elif preparing:
            badges.append(self._pill("Chuẩn bị học", C.ACCENT))
        else:
            badges.append(self._pill("Đang học", C.SAFE))

        header = ft.Row(
            [ft.Column([
                ft.Text(code.upper(), size=10.5, weight=ft.FontWeight.W_700, color=C.ACCENT,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(course.name, size=14.5, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY,
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)],
                spacing=2, expand=True),
             *badges],
            vertical_alignment=ft.CrossAxisAlignment.START, spacing=8,
        )

        # --- Nội dung -------------------------------------------------------------
        if course.error:
            body: ft.Control = ft.Column([
                ft.Row([ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, size=16, color=C.CRITICAL),
                        ft.Text("Không tải được điểm môn này.", size=12, color=C.CRITICAL)], spacing=6),
                ft.Text(course.error_detail or "", size=10.5, color=C.TEXT_SECONDARY,
                        selectable=True, visible=bool(course.error_detail)),
            ], spacing=2)
        elif not rows:
            body = ft.Text("Chưa có dữ liệu điểm.", size=12, color=C.TEXT_SECONDARY)
        else:
            body = ft.Column([self._attendance_block(att), self._grade_table(rows)], spacing=10)

        # --- Chân thẻ -------------------------------------------------------------
        scored = sum(1 for r in rows if r.grade and not _is_total_row(r))
        item_count = sum(1 for r in rows if not _is_total_row(r))
        foot_left = f"Mã lớp: {course.course_id}"
        foot_right = f"{scored}/{item_count} mục đã có điểm" if item_count else ""
        footer = ft.Row(
            [ft.Text(foot_left, size=10.5, color=C.TEXT_SECONDARY),
             ft.Row([ft.Icon(ft.Icons.HISTORY_ROUNDED, size=12, color=C.TEXT_SECONDARY,
                             visible=course.source == "cache"),
                     ft.Text("Dữ liệu đã lưu" if course.source == "cache" else foot_right,
                             size=10.5, color=C.TEXT_SECONDARY)], spacing=4)],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        return ft.Container(
            content=ft.Column(
                [ft.Container(height=4, bgcolor=accent),
                 ft.Container(
                     content=ft.Column([header, body, footer], spacing=10),
                     padding=ft.Padding.only(left=14, right=14, top=10, bottom=12))],
                spacing=0),
            bgcolor=C.SURFACE, border=ft.Border.all(1, C.BORDER), border_radius=12,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

    def _build(self, courses: list[CourseWithGrades]) -> None:
        section = ft.Row(
            [ft.Row([ft.Container(width=3, height=14, bgcolor=C.ACCENT, border_radius=2),
                     ft.Text("DANH SÁCH MÔN HỌC", size=12, weight=ft.FontWeight.W_700,
                             color=C.TEXT_PRIMARY)], spacing=8),
             ft.Text(f"Học kỳ hiện tại · {len(courses)} môn", size=11.5, color=C.TEXT_SECONDARY)],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
        # Môn "Chuẩn bị học" xuống đáy; sort ổn định nên giữ nguyên thứ tự còn lại.
        ordered = sorted(courses, key=_is_preparing)
        self._list.controls = [self._summary_bar(courses), ft.Container(height=2), section] + \
            [self._course_card(c) for c in ordered]
