from __future__ import annotations

import math
from typing import Any
from core.academic_radar.eligibility_resolver import CourseEligibilityResolver
from core.academic_radar.models import ComponentScore, CourseGradeItem, SemesterGradeData


def _parse_float(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, str):
        stripped = val.strip()
        if not stripped or stripped == "-":
            return None
        val = stripped.replace(",", ".")
    try:
        numeric = float(val)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _parse_bool_optional(val: Any) -> bool | None:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        if isinstance(val, float) and not math.isfinite(val):
            return None
        return bool(val)
    if isinstance(val, str):
        lowered = val.strip().lower()
        if lowered in {"true", "1", "yes", "y", "dat", "đạt"}:
            return True
        if lowered in {"false", "0", "no", "n", "khong dat", "không đạt"}:
            return False
    return None


_COMPONENT_LIST_KEYS = (
    "diemThanhPhan", "dsDiemThanhPhan", "thanhPhan", "dsThanhPhan",
    "chiTietDiem", "diemChiTiet", "dsDiemChiTiet", "cotDiem", "dsCotDiem",
    "components", "details",
)
_COMPONENT_NAME_KEYS = (
    "tenThanhPhan", "tenDiem", "tenCotDiem", "tenLoaiDiem", "loaiDiem", "ten", "name",
)
_COMPONENT_SCORE_KEYS = ("diem", "diemSo", "giaTri", "score", "value")
_COMPONENT_WEIGHT_KEYS = ("trongSo", "tyLe", "tiLe", "phanTram", "weight", "percent")
_MAPPED_COURSE_KEYS = frozenset({
    "id", "courseid",
    "maHocPhan", "maLopHocPhan", "course_code", "shortname",
    "tenMonHoc", "course_name", "fullname", "name",
    "soTinChi", "credits", "stc",
    "diemTBThuongKy", "diemQT", "diemQuaTrinh", "process_grade", "diem_qt",
    "diemThi", "diemCuoiKy", "diemCK", "exam_grade", "diem_thi",
    "diemTongKet", "diemTK", "final_grade", "grade", "rawgrade",
    "diemTinChi", "diemHe4", "credit_grade",
    "diemChu", "letter_grade", "str_grade",
    "isDat", "isPassed", "dat",
    "isChuyenDiem", "is_transferred",
    *_COMPONENT_LIST_KEYS,
})


def _first(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        v = raw.get(k)
        if v is not None and v != "":
            return v
    return None


def _extract_components(raw: dict[str, Any]) -> tuple[ComponentScore, ...]:
    """Trích danh sách điểm thành phần từ payload Portal (chấp nhận nhiều tên khóa)."""
    items: Any = None
    for key in _COMPONENT_LIST_KEYS:
        if isinstance(raw.get(key), list) and raw[key]:
            items = raw[key]
            break
    if not items:
        return ()
    entries: list[tuple[str, float | None, float | None, float | None]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(_first(it, _COMPONENT_NAME_KEYS) or "").strip()
        if not name:
            continue
        weight = _parse_float(_first(it, _COMPONENT_WEIGHT_KEYS))
        if weight is not None and 0 < weight <= 1:
            weight *= 100  # 0.3 -> 30%
        factor = _parse_float(it.get("heSo"))
        entries.append((name, _parse_float(_first(it, _COMPONENT_SCORE_KEYS)), weight, factor))

    if not entries:
        return ()

    # Chỉ quy đổi hệ số (heSo) sang % khi tất cả các mục đều không có trọng số %
    # và đều có hệ số dương; tránh nhầm heSo=1 thành 100% hoặc heSo=2 thành 2%.
    use_factors = (
        all(w is None and f is not None and f > 0 for _, _, w, f in entries)
    )
    total_factor = sum(f for _, _, _, f in entries if f is not None) if use_factors else 0.0

    result: list[ComponentScore] = []
    for name, score, weight, factor in entries:
        resolved_weight = weight
        if resolved_weight is None and use_factors and total_factor > 0 and factor is not None:
            resolved_weight = (factor / total_factor) * 100.0
        result.append(ComponentScore(
            name=name,
            score=score,
            weight=resolved_weight,
        ))
    return tuple(result)


class SemesterNormalizer:
    """Normalizes Portal API response payloads into structured SemesterGradeData."""

    def __init__(self, resolver: CourseEligibilityResolver | None = None) -> None:
        self._resolver = resolver or CourseEligibilityResolver()

    def normalize_course(self, raw: dict[str, Any]) -> CourseGradeItem:
        eligibility = self._resolver.resolve(raw)
        parsed_credits = _parse_float(_first(raw, ("soTinChi", "credits", "stc")))
        credits = parsed_credits if parsed_credits is not None else 0.0

        subject_code = str(_first(raw, ("maHocPhan",)) or "").strip()
        class_code = str(_first(raw, ("maLopHocPhan",)) or "").strip()
        course_id = str(
            _first(raw, ("id", "maLopHocPhan", "maHocPhan", "courseid")) or ""
        )
        course_code = str(
            _first(raw, ("maLopHocPhan", "maHocPhan", "course_code", "shortname")) or ""
        ).strip()
        course_name = str(
            _first(raw, ("tenMonHoc", "tenLopHocPhan", "course_name", "fullname", "name"))
            or "Chưa có tên"
        )

        pass_status = _parse_bool_optional(_first(raw, ("isDat", "isPassed", "dat")))
        is_passed = True if pass_status is None else pass_status
        transferred_val = _parse_bool_optional(
            _first(raw, ("isChuyenDiem", "is_transferred"))
        )
        is_transferred = bool(transferred_val) if transferred_val is not None else False

        extra_portal_fields: dict[str, Any] = {}
        for key, value in raw.items():
            if key in _MAPPED_COURSE_KEYS:
                continue
            if key == "tenLopHocPhan" and str(value or "").strip() == course_name.strip():
                continue
            if value is None or value == "" or value == [] or value == {}:
                continue
            extra_portal_fields[key] = value

        return CourseGradeItem(
            course_id=course_id,
            course_code=course_code,
            course_name=course_name,
            credits=credits,
            process_grade=_parse_float(
                _first(
                    raw,
                    ("diemTBThuongKy", "diemQT", "diemQuaTrinh", "process_grade", "diem_qt"),
                )
            ),
            exam_grade=_parse_float(
                _first(raw, ("diemThi", "diemCuoiKy", "diemCK", "exam_grade", "diem_thi"))
            ),
            final_grade=_parse_float(
                _first(raw, ("diemTongKet", "diemTK", "final_grade", "grade", "rawgrade"))
            ),
            credit_grade=_parse_float(
                _first(raw, ("diemTinChi", "diemHe4", "credit_grade"))
            ),
            letter_grade=str(
                _first(raw, ("diemChu", "letter_grade", "str_grade")) or ""
            ).strip(),
            is_passed=is_passed,
            is_transferred=is_transferred,
            eligibility=eligibility,
            components=_extract_components(raw),
            subject_code=subject_code,
            class_code=class_code,
            pass_status=pass_status,
            extra_portal_fields=extra_portal_fields,
        )

    def normalize_semester(self, raw_semester: dict[str, Any]) -> SemesterGradeData:
        sem_id = _first(raw_semester, ("id", "idDot"))
        if sem_id is None:
            sem_id = 0
        sem_name = str(
            _first(raw_semester, ("tenDot", "name")) or f"Học kỳ {sem_id}"
        )

        summary_raw = raw_semester.get("tongKetDot")
        if not isinstance(summary_raw, dict):
            summary_raw = raw_semester.get("summary")
        summary = summary_raw if isinstance(summary_raw, dict) else {}

        def _summary_first(*keys: str) -> Any:
            val = _first(summary, keys)
            if val is not None:
                return val
            return _first(raw_semester, keys)

        # Also carry over top-level summary keys if embedded directly
        normalized_summary = {
            "diemTBHocLuc": _parse_float(_summary_first("diemTBHocLuc")),
            "diemTBTinChi": _parse_float(_summary_first("diemTBTinChi")),
            "diemTBHocLucTichLuy": _parse_float(_summary_first("diemTBHocLucTichLuy")),
            "diemTBTinChiTichLuy": _parse_float(_summary_first("diemTBTinChiTichLuy")),
            "stcdatHocKy": _parse_float(_summary_first("stcdatHocKy", "soTCDat")),
            "soTCKhongDat": _parse_float(_summary_first("soTCKhongDat")),
            "soTCTichLuy": _parse_float(_summary_first("soTCTichLuy")),
            "soDiemRenLuyen": _parse_float(_summary_first("soDiemRenLuyen")),
            "xepLoaiHocLuc": _summary_first("xepLoaiHocLuc", "xepLoai"),
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
