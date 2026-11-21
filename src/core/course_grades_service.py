"""Lấy các môn đang học + điểm/chuyên cần.

Ưu tiên cào HTML grade report (/grade/report/user/index.php?id=<id>).
Nếu không đăng nhập session được (thiếu mật khẩu, SSO...), dùng Web Service
``gradereport_user_get_grade_items`` làm phương án dự phòng (cùng dữ liệu).
"""
from __future__ import annotations

import html as _html
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, List, Optional

from core.course_grade_scraper import (
    CourseGradeReport,
    GradeRow,
    _parse_float,
    parse_course_grade_report,
)

logger = logging.getLogger(__name__)

_EMPTY = {"", "-", "\u2013", "\u2014"}


@dataclass
class CourseWithGrades:
    course_id: int
    name: str
    shortname: str = ""
    report: Optional[CourseGradeReport] = None
    error: bool = False
    error_detail: str = ""
    source: str = ""  # "web" | "ws"


def _txt(value: Any) -> str:
    text = re.sub(r"<[^>]+>", "", _html.unescape(str(value or ""))).replace("\xa0", " ").strip()
    return "" if text in _EMPTY else text


def report_from_ws_items(course_id: int, items: list[dict]) -> CourseGradeReport:
    """Chuyển gradeitems của WS thành CourseGradeReport (cùng dữ liệu với trang web)."""
    report = CourseGradeReport(course_id=course_id)
    for it in items or []:
        if not isinstance(it, dict):
            continue
        itype = str(it.get("itemtype") or "")
        module = str(it.get("itemmodule") or "")
        name = _txt(it.get("itemname"))
        if not name:
            name = "Course total" if itype == "course" else ("Grade item" if itype == "category" else "")
        if not name:
            continue
        grade = _txt(it.get("gradeformatted"))
        pct = _txt(it.get("percentageformatted"))
        report.rows.append(GradeRow(
            name=name, grade=grade, percentage=pct,
            grade_value=_parse_float(grade), percentage_value=_parse_float(pct),
            is_attendance=(module == "attendance"),
        ))
    return report


def fetch_current_courses_with_grades(orchestrator: Any, max_workers: int = 4) -> List[CourseWithGrades]:
    """Danh sách môn đang học (Moodle 'inprogress') kèm bảng điểm."""
    service = orchestrator.moodle_service
    raw = service.get_enrolled_courses("inprogress") or []

    courses: list[CourseWithGrades] = []
    for c in raw:
        try:
            cid = int(c.get("id"))
        except (TypeError, ValueError):
            continue
        courses.append(CourseWithGrades(
            course_id=cid,
            name=_html.unescape(str(c.get("fullname") or c.get("shortname") or f"Môn {cid}")),
            shortname=str(c.get("shortname") or ""),
        ))
    if not courses:
        return []

    userid_holder: dict[str, Optional[int]] = {}

    def get_userid() -> Optional[int]:
        if "v" not in userid_holder:
            try:
                userid_holder["v"] = service.get_current_user_id()
            except Exception:
                userid_holder["v"] = None
        return userid_holder["v"]

    def work(item: CourseWithGrades) -> CourseWithGrades:
        html_text = None
        try:
            html_text = service.fetch_grade_report_html(item.course_id)
        except Exception as exc:  # không để một môn làm hỏng cả danh sách
            logger.warning("Grade report web lỗi (môn %s): %s", item.course_id, exc)
        web_reason = getattr(service, "last_grade_report_error", "")
        if html_text is not None:
            item.report = parse_course_grade_report(html_text, item.course_id)
            item.source = "web"
            if item.report.rows:
                return item
            web_reason = "no_rows_in_html"

        # Dự phòng: Web Service
        uid = get_userid()
        if uid:
            try:
                ws_items = service.get_grade_items(item.course_id, uid)
            except Exception as exc:
                ws_items = None
                web_reason = f"{web_reason}; ws:{exc}"
            if ws_items:
                item.report = report_from_ws_items(item.course_id, ws_items)
                item.source = "ws"
                return item
            if ws_items is not None and item.report is None:
                item.report = CourseGradeReport(course_id=item.course_id)
                item.source = "ws"
                return item
        if item.report is None:
            item.error = True
            item.error_detail = web_reason or "unknown"
            logger.warning("Không lấy được điểm môn %s: %s", item.course_id, item.error_detail)
        return item

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(work, courses))
