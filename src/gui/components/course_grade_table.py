"""Bảng "HẠNG MỤC ĐÁNH GIÁ / ĐIỂM / TỶ LỆ" dùng chung (tab Môn học + popup Bảng điểm)."""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

import flet as ft

from core.course_grade_scraper import GradeRow
from gui.core.theme import C

_TOTAL_ROWS = {"course total", "tổng khóa học", "tổng điểm khóa học"}


def tint(color: str, opacity: float = 0.12) -> str:
    return ft.Colors.with_opacity(opacity, color)


def is_total_row(row) -> bool:
    return row.name.strip().lower() in _TOTAL_ROWS


def count_scored(rows) -> tuple[int, int]:
    """(số mục đã có điểm, tổng số mục) — không tính dòng tổng."""
    items = [r for r in rows if not is_total_row(r)]
    return sum(1 for r in items if r.grade), len(items)


def build_grade_table(rows) -> ft.Container:
    col_grade, col_pct = 56, 72

    def head_cell(t: str, w: Optional[int] = None, right: bool = False) -> ft.Text:
        return ft.Text(t, size=10, weight=ft.FontWeight.W_700, color=C.TEXT_SECONDARY,
                       width=w, expand=(w is None),
                       text_align=ft.TextAlign.RIGHT if right else ft.TextAlign.LEFT)

    table_head = ft.Container(
        content=ft.Row([head_cell("HẠNG MỤC ĐÁNH GIÁ"), head_cell("ĐIỂM", col_grade, True),
                        head_cell("TỶ LỆ", col_pct, True)]),
        bgcolor=tint(C.ACCENT, 0.10),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
    )

    body: list[ft.Control] = [table_head]
    for idx, r in enumerate(rows):
        total = is_total_row(r)
        has_grade = bool(r.grade)
        if r.is_attendance:
            name_icon = ft.Icon(ft.Icons.HOW_TO_REG_ROUNDED, size=14, color=C.ACCENT)
        elif total:
            name_icon = ft.Icon(ft.Icons.EMOJI_EVENTS_ROUNDED, size=14, color=C.WARNING)
        else:
            name_icon = ft.Icon(ft.Icons.ASSIGNMENT_ROUNDED, size=14, color=C.TEXT_SECONDARY)
        grade_text = ft.Text(
            r.grade or "—", size=12.5, width=col_grade, text_align=ft.TextAlign.RIGHT,
            weight=ft.FontWeight.W_700 if has_grade else ft.FontWeight.W_400,
            color=(C.TEXT_PRIMARY if has_grade else C.TEXT_SECONDARY))
        pct_text = ft.Text(
            r.percentage or "—", size=12, width=col_pct, text_align=ft.TextAlign.RIGHT,
            color=C.TEXT_SECONDARY)
        if total:
            bg = tint(C.ACCENT, 0.07)
        else:
            bg = C.SURFACE if idx % 2 == 0 else tint(C.TEXT_SECONDARY, 0.05)
        body.append(ft.Container(
            content=ft.Row(
                [ft.Row([name_icon,
                         ft.Text(r.name, size=12, color=C.TEXT_PRIMARY, expand=True, max_lines=2,
                                 overflow=ft.TextOverflow.ELLIPSIS,
                                 weight=ft.FontWeight.W_700 if total else ft.FontWeight.W_400)],
                        spacing=8, expand=True),
                 grade_text, pct_text],
                vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=bg,
            border=ft.Border.only(top=ft.BorderSide(1, C.BORDER)),
            padding=ft.Padding.symmetric(horizontal=12, vertical=9),
        ))
    return ft.Container(
        content=ft.Column(body, spacing=0),
        border=ft.Border.all(1, C.BORDER), border_radius=10, clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )


def build_footer(left: str, right: str = "") -> ft.Row:
    return ft.Row(
        [ft.Text(left, size=10.5, color=C.TEXT_SECONDARY),
         ft.Text(right, size=10.5, color=C.TEXT_SECONDARY)],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )


# --- Ghép môn Portal <-> môn Moodle -------------------------------------------------

def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").replace("đ", "d").replace("Đ", "D"))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def find_moodle_course(item, moodle_courses):
    """Tìm CourseWithGrades khớp với CourseGradeItem của Portal (theo tên / mã môn)."""
    name = _norm(item.course_name)
    code = _norm(item.course_code)
    best = None
    for c in moodle_courses or []:
        cname, cshort = _norm(c.name), _norm(c.shortname)
        if name and cname == name:
            return c
        if code and len(code) >= 3 and (code in cshort or code in cname or cshort == code):
            return c
        if best is None and name and len(name) >= 6 and (name in cname or cname in name):
            best = c
    return best


def portal_rows(item) -> list[GradeRow]:
    """Dựng bảng cùng định dạng từ dữ liệu Portal khi không có báo cáo Moodle."""
    def g(v: Optional[float]) -> str:
        return f"{v:.1f}" if v is not None else ""

    rows: list[GradeRow] = []
    if item.components:
        for comp in item.components:
            pct = ""
            if comp.weight is not None:
                pct = f"{comp.weight:.0f} %" if abs(comp.weight - round(comp.weight)) < 0.05 else f"{comp.weight:.1f} %"
            rows.append(GradeRow(name=comp.name, grade=g(comp.score), percentage=pct,
                                 grade_value=comp.score))
    else:
        rows.append(GradeRow(name="Điểm quá trình", grade=g(item.process_grade), grade_value=item.process_grade))
        rows.append(GradeRow(name="Điểm thi", grade=g(item.exam_grade), grade_value=item.exam_grade))
    rows.append(GradeRow(name="Tổng khóa học", grade=g(item.final_grade), grade_value=item.final_grade))
    return rows
