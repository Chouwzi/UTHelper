from __future__ import annotations

import logging
from typing import Any, Optional

import flet as ft
from core.academic_radar.models import CourseGradeItem
from gui.components.course_grade_table import (
    build_footer,
    build_grade_table,
    count_scored,
    portal_rows,
)
from gui.core.theme import C

logger = logging.getLogger(__name__)


def build_course_components_body(
    course: CourseGradeItem, moodle_course: Optional[Any], width: float,
    max_height: float = 0.0,
) -> ft.Control:
    """Bảng "Hạng mục đánh giá" của môn; ưu tiên báo cáo điểm Moodle, dự phòng dữ liệu Portal."""
    report = getattr(moodle_course, "report", None)
    if report is not None and report.rows:
        rows = report.rows
        left = f"Mã lớp: {moodle_course.course_id}"
        scored, total = count_scored(rows)
        right = f"{scored}/{total} mục đã có điểm" if total else ""
    else:
        rows = portal_rows(course)
        left = f"{course.course_code or 'Học phần'} · {course.credits:.0f} TC"
        right = "Nguồn: Portal"

    # Ước lượng chiều cao nội dung: header ~34, mỗi dòng ~40, footer ~24.
    # Chỉ bật cuộn (và cố định chiều cao) khi bảng dài hơn vùng cho phép, để
    # popup ôm sát nội dung, không chừa khoảng trống phía dưới.
    est_height = 34 + 40 * len(rows) + 10 + 24
    needs_scroll = max_height > 0 and est_height > max_height

    column = ft.Column(
        [build_grade_table(rows), build_footer(left, right)],
        spacing=10,
        tight=True,
        scroll=ft.ScrollMode.AUTO if needs_scroll else None,
    )
    return ft.Container(
        content=column,
        width=width,
        height=max_height if needs_scroll else None,
    )


def show_course_components_dialog(
    page: ft.Page | None, course: CourseGradeItem, moodle_course: Optional[Any] = None
) -> None:
    """Mở popup bảng điểm thành phần của một học phần."""
    if page is None:
        logger.error("Course components dialog: no page available")
        return
    try:
        page_w = float(getattr(page, "width", None) or 0)
    except (TypeError, ValueError):
        page_w = 0.0
    body_w = max(300.0, min(520.0, page_w - 60)) if page_w else 480.0
    try:
        page_h = float(getattr(page, "height", None) or 0)
    except (TypeError, ValueError):
        page_h = 0.0
    max_h = max(240.0, page_h * 0.6) if page_h else 0.0

    dialog = ft.AlertDialog(
        modal=False,
        title=ft.Text(course.course_name, size=15, weight=ft.FontWeight.W_700,
                      color=C.TEXT_PRIMARY, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
        content=build_course_components_body(course, moodle_course, body_w, max_h),
        content_padding=ft.Padding.only(left=24, right=24, top=12, bottom=4),
        actions_padding=ft.Padding.only(left=16, right=16, top=0, bottom=8),
        actions=[ft.TextButton("Đóng", on_click=lambda _: _close(page, dialog))],
        bgcolor=C.BG,
        shape=ft.RoundedRectangleBorder(radius=16),
    )
    try:
        page.show_dialog(dialog)
    except Exception:
        logger.warning("page.show_dialog failed; falling back to overlay", exc_info=True)
        page.overlay.append(dialog)
        dialog.open = True
        page.update()


def _close(page: ft.Page, dialog: ft.AlertDialog) -> None:
    dialog.open = False
    try:
        dialog.update()
    except Exception:
        try:
            page.update()
        except Exception:
            pass
