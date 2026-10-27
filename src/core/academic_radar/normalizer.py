from __future__ import annotations

from typing import Any
from core.academic_radar.eligibility_resolver import CourseEligibilityResolver
from core.academic_radar.models import CourseGradeItem, SemesterGradeData


def _parse_float(val: Any) -> float | None:
    if val is None or val == "" or val == "-":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


class SemesterNormalizer:
    """Normalizes Portal API response payloads into structured SemesterGradeData."""

    def __init__(self, resolver: CourseEligibilityResolver | None = None) -> None:
        self._resolver = resolver or CourseEligibilityResolver()

    def normalize_course(self, raw: dict[str, Any]) -> CourseGradeItem:
        eligibility = self._resolver.resolve(raw)
        credits = _parse_float(raw.get("soTinChi") or raw.get("credits") or raw.get("stc")) or 0.0

        return CourseGradeItem(
            course_id=str(raw.get("id") or raw.get("maLopHocPhan") or raw.get("maHocPhan") or raw.get("courseid") or ""),
            course_code=str(raw.get("maLopHocPhan") or raw.get("maHocPhan") or raw.get("course_code") or raw.get("shortname") or "").strip(),
            course_name=str(raw.get("tenMonHoc") or raw.get("tenLopHocPhan") or raw.get("course_name") or raw.get("fullname") or raw.get("name") or "Chưa có tên"),
            credits=credits,
            process_grade=_parse_float(raw.get("diemTBThuongKy") or raw.get("diemQT") or raw.get("diemQuaTrinh") or raw.get("process_grade") or raw.get("diem_qt")),
            exam_grade=_parse_float(raw.get("diemThi") or raw.get("diemCuoiKy") or raw.get("diemCK") or raw.get("exam_grade") or raw.get("diem_thi")),
            final_grade=_parse_float(raw.get("diemTongKet") or raw.get("diemTK") or raw.get("final_grade") or raw.get("grade") or raw.get("rawgrade")),
            credit_grade=_parse_float(raw.get("diemTinChi") or raw.get("diemHe4") or raw.get("credit_grade")),
            letter_grade=str(raw.get("diemChu") or raw.get("letter_grade") or raw.get("str_grade") or "").strip(),
            is_passed=bool(raw.get("isDat", True)),
            is_transferred=bool(raw.get("isChuyenDiem", False)),
            eligibility=eligibility,
        )

    def normalize_semester(self, raw_semester: dict[str, Any]) -> SemesterGradeData:
        sem_id = raw_semester.get("id") or raw_semester.get("idDot") or 0
        sem_name = raw_semester.get("tenDot") or raw_semester.get("name") or f"Học kỳ {sem_id}"

        summary = raw_semester.get("tongKetDot") or raw_semester.get("summary") or {}
        # Also carry over top-level summary keys if embedded directly
        normalized_summary = {
            "diemTBHocLuc": _parse_float(summary.get("diemTBHocLuc") or raw_semester.get("diemTBHocLuc")),
            "diemTBTinChi": _parse_float(summary.get("diemTBTinChi") or raw_semester.get("diemTBTinChi")),
            "diemTBHocLucTichLuy": _parse_float(summary.get("diemTBHocLucTichLuy") or raw_semester.get("diemTBHocLucTichLuy")),
            "diemTBTinChiTichLuy": _parse_float(summary.get("diemTBTinChiTichLuy") or raw_semester.get("diemTBTinChiTichLuy")),
            "stcdatHocKy": _parse_float(summary.get("stcdatHocKy") or raw_semester.get("stcdatHocKy") or summary.get("soTCDat")),
            "soTCKhongDat": _parse_float(summary.get("soTCKhongDat") or raw_semester.get("soTCKhongDat")),
            "soTCTichLuy": _parse_float(summary.get("soTCTichLuy") or raw_semester.get("soTCTichLuy")),
            "soDiemRenLuyen": _parse_float(summary.get("soDiemRenLuyen") or raw_semester.get("soDiemRenLuyen")),
            "xepLoaiHocLuc": summary.get("xepLoaiHocLuc") or raw_semester.get("xepLoaiHocLuc") or summary.get("xepLoai"),
        }

        raw_courses = (
            raw_semester.get("diems")
            or raw_semester.get("danhSachMonHoc")
            or raw_semester.get("dsDiem")
            or raw_semester.get("dsMonHoc")
            or raw_semester.get("diemHocKy")
            or raw_semester.get("danhSachDiem")
            or raw_semester.get("courses")
            or []
        )
        courses = [self.normalize_course(c) for c in raw_courses if isinstance(c, dict)]

        return SemesterGradeData(
            id=sem_id,
            name=sem_name,
            summary=normalized_summary,
            courses=courses,
        )

    def normalize_transcript(self, raw_transcript: list[dict[str, Any]]) -> list[SemesterGradeData]:
        """Normalizes full transcript array containing multiple semesters."""
        return [
            self.normalize_semester(s)
            for s in raw_transcript
            if isinstance(s, dict)
        ]
