"""Lưu snapshot điểm theo môn và phát hiện thay đổi giữa các lần cập nhật.

Chạy chung chu kỳ cập nhật với Tổng quan. Giá trị trống được chuẩn hoá thành "-"
nên các thay đổi "-" -> 7.1, 7.1 -> "-"... đều được tính là thay đổi.
Lần đầu (chưa có snapshot của môn) chỉ lưu nền, không thông báo.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from core.course_grade_scraper import CourseGradeReport, GradeRow, _parse_float
from core.grade_monitor import GradeChange

logger = logging.getLogger(__name__)

DASH = "-"


def _norm(value: str) -> str:
    v = (value or "").strip()
    return v if v else DASH


def _row_keys(rows: List[GradeRow]) -> List[str]:
    """Khoá ổn định theo tên mục (thêm #n nếu trùng tên)."""
    seen: Dict[str, int] = {}
    keys = []
    for r in rows:
        n = seen.get(r.name, 0)
        seen[r.name] = n + 1
        keys.append(r.name if n == 0 else f"{r.name}#{n + 1}")
    return keys


class CourseGradeMonitor:
    def __init__(self, snapshot_path: Optional[str] = None):
        if snapshot_path is None:
            from config import _USER_DATA_DIR
            snapshot_path = str(_USER_DATA_DIR / "course_grades_snapshot.json")
        self._path = Path(snapshot_path)
        self._lock = threading.Lock()
        self._data: Dict[str, dict] = self._load()

    # --- persistence -------------------------------------------------
    def _load(self) -> Dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            from core.safe_file_io import SafeFileIO
            data = SafeFileIO.read_json_safe(self._path, dict)
            courses = data.get("courses") if isinstance(data, dict) else None
            return courses if isinstance(courses, dict) else {}
        except Exception as exc:
            logger.warning("Cannot load course grade snapshot: %s", exc)
            return {}

    def _save(self) -> None:
        try:
            from core.safe_file_io import SafeFileIO
            SafeFileIO.write_json_atomic(
                self._path,
                {"updated": datetime.now().isoformat(), "courses": self._data},
            )
        except Exception as exc:
            logger.error("Cannot save course grade snapshot: %s", exc)

    # --- cache -> model ---------------------------------------------
    @staticmethod
    def _report_from_entry(course_id: int, entry: dict) -> CourseGradeReport:
        report = CourseGradeReport(course_id=course_id)
        for r in entry.get("rows", []):
            g = "" if r.get("grade", DASH) == DASH else r.get("grade", "")
            p = "" if r.get("percentage", DASH) == DASH else r.get("percentage", "")
            report.rows.append(GradeRow(
                name=r.get("name", ""), grade=g, percentage=p,
                grade_value=_parse_float(g), percentage_value=_parse_float(p),
                is_attendance=bool(r.get("is_attendance")),
            ))
        return report

    def load_cached(self):
        """Trả danh sách CourseWithGrades từ snapshot (hiển thị tức thì khi mở tab)."""
        from core.course_grades_service import CourseWithGrades
        out = []
        with self._lock:
            for cid, entry in self._data.items():
                try:
                    cid_int = int(cid)
                except ValueError:
                    continue
                out.append(CourseWithGrades(
                    course_id=cid_int, name=entry.get("name", f"Môn {cid}"),
                    report=self._report_from_entry(cid_int, entry), source="cache",
                ))
        return out

    # --- diff --------------------------------------------------------
    def update(self, courses) -> List[GradeChange]:
        """So sánh với snapshot, cập nhật snapshot, trả về các thay đổi."""
        changes: List[GradeChange] = []
        now = datetime.now().isoformat()
        with self._lock:
            for c in courses:
                cid = str(c.course_id)
                if c.error or c.report is None:
                    # Không tải được lần này: giữ snapshot cũ, hiển thị dữ liệu cũ nếu có.
                    old = self._data.get(cid)
                    if old:
                        c.report = self._report_from_entry(c.course_id, old)
                        c.error = False
                    continue

                rows = c.report.rows
                keys = _row_keys(rows)
                new_rows = {
                    k: {"name": r.name, "grade": _norm(r.grade),
                        "percentage": _norm(r.percentage), "is_attendance": r.is_attendance}
                    for k, r in zip(keys, rows)
                }
                old_entry = self._data.get(cid)
                if old_entry is not None:  # đã có baseline -> mới so sánh
                    old_rows = {
                        k: v for k, v in zip(
                            _row_keys([GradeRow(name=x.get("name", "")) for x in old_entry.get("rows", [])]),
                            old_entry.get("rows", []),
                        )
                    }
                    for k, new in new_rows.items():
                        old = old_rows.get(k, {"grade": DASH, "percentage": DASH})
                        label = new["name"]
                        if new["grade"] != old.get("grade", DASH):
                            changes.append(GradeChange(
                                course_name=c.name, item_name=label,
                                old_grade=old.get("grade", DASH), new_grade=new["grade"],
                                timestamp=now))
                        if new["percentage"] != old.get("percentage", DASH):
                            changes.append(GradeChange(
                                course_name=c.name, item_name=f"{label} (%)",
                                old_grade=old.get("percentage", DASH), new_grade=new["percentage"],
                                timestamp=now))

                self._data[cid] = {
                    "name": c.name,
                    "rows": [new_rows[k] for k in keys],
                }
            self._save()
        if changes:
            logger.info("Detected %d course grade changes", len(changes))
        return changes
