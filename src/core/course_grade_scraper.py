"""Cào bảng điểm người dùng của một môn học trên Moodle.

Nguồn: ``/grade/report/user/index.php?id=<course_id>``.
Mỗi dòng ``<tr>`` là một mục điểm; ta lấy:
  - ``td`` có class ``column-grade``      (headers chứa ``grade<itemid>``)      -> điểm
  - ``td`` có class ``column-percentage`` (headers chứa ``percentage<itemid>``) -> %
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

_EMPTY = {"", "-", "\u2013", "\u2014"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _parse_float(text: str) -> Optional[float]:
    m = re.search(r"-?\d+(?:[.,]\d+)?", text or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


@dataclass
class GradeRow:
    name: str
    grade: str = ""            # text gốc, ví dụ "7.1"
    percentage: str = ""       # text gốc, ví dụ "71.4 %"
    grade_value: Optional[float] = None
    percentage_value: Optional[float] = None
    is_attendance: bool = False
    url: str = ""


@dataclass
class CourseGradeReport:
    course_id: int
    rows: List[GradeRow] = field(default_factory=list)

    @property
    def attendance(self) -> Optional[GradeRow]:
        return next((r for r in self.rows if r.is_attendance), None)


def _cell(tr, kind: str):
    """Tìm td theo class ``column-<kind>`` hoặc headers chứa ``<kind><số>``."""
    td = tr.find("td", class_=lambda c: c and f"column-{kind}" in c.split())
    if td is not None:
        return td
    return tr.find("td", attrs={"headers": re.compile(rf"\b{kind}\d+\b")})


def parse_course_grade_report(html: str, course_id: int = 0) -> CourseGradeReport:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    report = CourseGradeReport(course_id=course_id)

    for tr in soup.find_all("tr"):
        th = tr.find("th", class_=lambda c: c and "column-itemname" in c.split())
        if th is None:
            continue

        link = th.find("a", class_="gradeitemheader") or th.find("a")
        title = th.find(class_="rowtitle")
        name = _clean((link or title or th).get_text(" "))
        if not name:
            continue

        grade_td = _cell(tr, "grade")
        pct_td = _cell(tr, "percentage")
        grade = _clean(grade_td.get_text(" ")) if grade_td is not None else ""
        pct = _clean(pct_td.get_text(" ")) if pct_td is not None else ""
        if grade in _EMPTY:
            grade = ""
        if pct in _EMPTY:
            pct = ""

        href = (link.get("href") if link is not None else "") or ""
        img = th.find("img", class_="itemicon")
        icon_alt = (img.get("alt") or "") if img is not None else ""
        is_att = "/mod/attendance/" in href or "điểm danh" in icon_alt.lower() or "attendance" in icon_alt.lower()

        report.rows.append(GradeRow(
            name=name,
            grade=grade,
            percentage=pct,
            grade_value=_parse_float(grade),
            percentage_value=_parse_float(pct),
            is_attendance=is_att,
            url=href,
        ))
    return report
