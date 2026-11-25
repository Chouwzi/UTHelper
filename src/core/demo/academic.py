"""Date-relative schedules, multi-semester transcript, and credit progress for Demo Mode."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from core.today_schedule import ClassSession


@dataclass(frozen=True)
class DailyScheduleSnapshot:
    """Single-day class schedule snapshot for Reviewer Demo Mode."""

    schedule_date: date
    sessions: tuple[ClassSession, ...]


@dataclass(frozen=True)
class WeekScheduleSnapshot:
    """Monday-anchored weekly timetable snapshot for Reviewer Demo Mode."""

    monday: date
    days: dict[date, tuple[ClassSession, ...]]
    sessions: tuple[ClassSession, ...]


_SYLLABUS_NETWORK = (
    "## Mục tiêu học phần\n"
    "- Nắm vững lập trình Socket TCP/UDP, framing bản tin và xử lý đa luồng.\n"
    "- Thiết kế và kiểm thử dịch vụ mạng chịu tải cho bài toán giao thông thông minh.\n\n"
    "## Đánh giá học phần\n"
    "- Quá trình & Thực hành Lab: **50%**\n"
    "- Thi kết thúc học phần: **50%**"
)

_SYLLABUS_DDB = (
    "## Mục tiêu học phần\n"
    "- Thiết kế lược đồ phân mảnh ngang/dọc và nhân bản dữ liệu trên cụm phân tán.\n"
    "- Triển khai giao thức cam kết 2 pha (2PC) và tối ưu hóa truy vấn liên trạm.\n\n"
    "## Đánh giá học phần\n"
    "- Chuyên cần & Bài tập thiết kế: **40%**\n"
    "- Thi kết thúc học phần: **60%**"
)

_SYLLABUS_SEC = (
    "## Mục tiêu học phần\n"
    "- Phân tích bề mặt tấn công ứng dụng Web, xác thực phiên và mã hóa dữ liệu.\n"
    "- Thực hành cấu hình phòng thủ theo tiêu chuẩn OWASP ASVS.\n\n"
    "## Đánh giá học phần\n"
    "- Báo cáo nhóm & Lab phòng thủ: **50%**\n"
    "- Thi kết thúc học phần: **50%**"
)

_SYLLABUS_ENG = (
    "## Mục tiêu học phần\n"
    "- Đọc hiểu tài liệu kiến trúc đám mây, RFC kỹ thuật và viết báo cáo tiếng Anh CNTT.\n"
    "- Thuyết trình giải pháp kỹ thuật theo nhóm.\n\n"
    "## Đánh giá học phần\n"
    "- Quá trình & Bài luận kỹ thuật: **50%**\n"
    "- Kiểm tra tổng kết trên hệ thống THNN: **50%**"
)


def _slot(
    slot_key: str,
    subject: str,
    subject_code: str,
    start: tuple[int, int],
    end: tuple[int, int],
    periods: tuple[int, int],
    room: str,
    campus: str,
    lecturer: str,
    note: str,
    outline: str,
) -> dict[str, Any]:
    return {
        "slot": slot_key,
        "subject": subject,
        "course_code": f"{subject_code}01",
        "subject_code": subject_code,
        "start": start,
        "end": end,
        "p_start": periods[0],
        "p_end": periods[1],
        "room": room,
        "campus": campus,
        "lecturer": lecturer,
        "note": note,
        "outline": outline,
    }


# Providing 2 sessions every day (Mon..Sun) guarantees reviewers and automated bots
# see an active morning + afternoon timetable regardless of which day the review runs.
_WEEKDAY_TEMPLATES: dict[int, tuple[dict[str, Any], ...]] = {
    0: (
        _slot("m", "Lập trình mạng", "0101123001", (7, 0), (9, 30), (1, 3), "B.304", "Cơ sở Quận 12", "TS. Trần Quốc Bảo", "Lý thuyết Chương Socket TCP & Framing", _SYLLABUS_NETWORK),
        _slot("a", "Thực hành Lập trình mạng", "0101123001", (13, 0), (15, 30), (7, 9), "Lab Mạng A.202", "Cơ sở Quận 12", "TS. Trần Quốc Bảo", "Thực hành xây dựng TCP Echo Server đa luồng", _SYLLABUS_NETWORK),
    ),
    1: (
        _slot("m", "Hệ cơ sở dữ liệu phân tán", "0101123002", (7, 0), (9, 30), (1, 3), "C.401", "Cơ sở Quận 12", "PGS.TS. Lê Hoàng Nam", "Phân mảnh ngang dẫn xuất và định vị dữ liệu", _SYLLABUS_DDB),
        _slot("a", "Tiếng Anh chuyên ngành CNTT", "0101005004", (13, 0), (15, 30), (7, 9), "A.305", "Cơ sở Bình Thạnh", "ThS. Đặng Minh Châu", "Unit 3: Cloud Computing & Technical Writing", _SYLLABUS_ENG),
    ),
    2: (
        _slot("m", "An toàn thông tin", "0101123003", (7, 0), (9, 30), (1, 3), "B.208", "Cơ sở Quận 12", "ThS. Phạm Thu Hà", "Kiểm soát truy cập, xác thực đa nhân tố & OWASP Top 10", _SYLLABUS_SEC),
        _slot("a", "Thực hành Hệ CSDL phân tán", "0101123002", (13, 0), (15, 30), (7, 9), "Lab CSDL A.205", "Cơ sở Quận 12", "PGS.TS. Lê Hoàng Nam", "Cấu hình nhân bản dữ liệu và kiểm thử giao dịch phân tán", _SYLLABUS_DDB),
    ),
    3: (
        _slot("m", "Lập trình mạng (Thảo luận thiết kế)", "0101123001", (9, 40), (12, 10), (4, 6), "B.304", "Cơ sở Quận 12", "TS. Trần Quốc Bảo", "Review kiến trúc giao thức nhóm", _SYLLABUS_NETWORK),
        _slot("a", "Thực hành An toàn thông tin", "0101123003", (13, 0), (15, 30), (7, 9), "Lab ATTT A.210", "Cơ sở Quận 12", "ThS. Phạm Thu Hà", "Phân tích HTTP Security Headers & CSP", _SYLLABUS_SEC),
    ),
    4: (
        _slot("m", "Hệ cơ sở dữ liệu phân tán", "0101123002", (7, 0), (9, 30), (1, 3), "C.401", "Cơ sở Quận 12", "PGS.TS. Lê Hoàng Nam", "Tối ưu hóa truy vấn liên cụm và đồng bộ khóa", _SYLLABUS_DDB),
        _slot("a", "Tiếng Anh chuyên ngành CNTT", "0101005004", (13, 0), (15, 30), (7, 9), "A.305", "Cơ sở Bình Thạnh", "ThS. Đặng Minh Châu", "Thực hành đọc hiểu tài liệu kỹ thuật & thuyết trình", _SYLLABUS_ENG),
    ),
    5: (
        _slot("m", "Chuyên đề An toàn thông tin", "0101123003", (7, 30), (10, 0), (1, 3), "Hội trường B.101", "Cơ sở Bình Thạnh", "ThS. Phạm Thu Hà", "Báo cáo giữa kỳ nhóm 01–06", _SYLLABUS_SEC),
        _slot("a", "Hướng dẫn đồ án Lập trình mạng", "0101123001", (13, 30), (16, 0), (7, 9), "B.204", "Cơ sở Quận 12", "TS. Trần Quốc Bảo", "Kiểm tra tiến độ mã nguồn nhóm", _SYLLABUS_NETWORK),
    ),
    6: (
        _slot("m", "Ôn tập Tiếng Anh chuyên ngành CNTT", "0101005004", (8, 0), (10, 30), (2, 4), "A.305", "Cơ sở Bình Thạnh", "ThS. Đặng Minh Châu", "Luyện viết Technical Summary & từ vựng học thuật", _SYLLABUS_ENG),
        _slot("a", "Tự học có hướng dẫn: Lab Lập trình mạng", "0101123001", (13, 30), (16, 0), (7, 9), "Lab Mạng A.202", "Cơ sở Quận 12", "TS. Trần Quốc Bảo", "Kiểm thử hiệu năng TCP Multi-Client", _SYLLABUS_NETWORK),
    ),
}


def academic_term_for(ref_date: date | None = None) -> tuple[str, str, int, int]:
    """Return `(short_term_code, portal_ten_dot, start_year, end_year)` for `ref_date`."""
    d = ref_date or date.today()
    if d.month >= 8:
        y1, y2 = d.year, d.year + 1
        return ("HKI", f"Học kỳ 1 năm học {y1}-{y2}", y1, y2)
    y1, y2 = d.year - 1, d.year
    return ("HKII", f"Học kỳ 2 năm học {y1}-{y2}", y1, y2)


def _previous_terms(term_code: str, y1: int, y2: int) -> tuple[str, str]:
    if term_code == "HKI":
        return (
            f"Học kỳ 2 năm học {y1 - 1}-{y1}",
            f"Học kỳ 1 năm học {y1 - 1}-{y1}",
        )
    return (
        f"Học kỳ 1 năm học {y1}-{y2}",
        f"Học kỳ 2 năm học {y1 - 1}-{y1}",
    )


def _sessions_for_date(target_date: date) -> tuple[ClassSession, ...]:
    templates = _WEEKDAY_TEMPLATES.get(target_date.weekday(), ())
    built: list[ClassSession] = []
    for item in templates:
        sh, sm = item["start"]
        eh, em = item["end"]
        built.append(
            ClassSession(
                session_id=f"demo-{target_date.isoformat()}-{item['slot']}-{item['subject_code']}",
                subject=item["subject"],
                course_code=item["course_code"],
                start_at=datetime.combine(target_date, time(sh, sm)),
                end_at=datetime.combine(target_date, time(eh, em)),
                room=item["room"],
                campus=item["campus"],
                period_start=item["p_start"],
                period_end=item["p_end"],
                cancelled=False,
                note=item["note"],
                subject_code=item["subject_code"],
                lecturer=item["lecturer"],
                has_outline=True,
                outline=item["outline"],
            )
        )
    return tuple(built)


def build_demo_day_schedule(target_date: date) -> DailyScheduleSnapshot:
    """Build deterministic daily schedule snapshot for `target_date`."""
    return DailyScheduleSnapshot(
        schedule_date=target_date,
        sessions=_sessions_for_date(target_date),
    )


def build_demo_week_schedule(week_anchor: date) -> WeekScheduleSnapshot:
    """Build Monday-anchored weekly schedule snapshot containing `week_anchor`."""
    monday = week_anchor - timedelta(days=week_anchor.weekday())
    days: dict[date, tuple[ClassSession, ...]] = {}
    flat: list[ClassSession] = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        day_sessions = _sessions_for_date(day)
        days[day] = day_sessions
        flat.extend(day_sessions)
    return WeekScheduleSnapshot(monday=monday, days=days, sessions=tuple(flat))


def build_demo_credit_progress() -> dict[str, Any]:
    """Return Portal `hoctap/tiendo` body (`78 / 132` credits)."""
    return {"canDat": 132, "hienTai": 78}


def build_demo_current_semester_courses(
    ref_date: date | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Return `(ten_dot, courses)` matching `PortalClient.fetch_current_semester_courses`."""
    _code, ten_dot, _y1, _y2 = academic_term_for(ref_date)
    return (
        ten_dot,
        [
            {"maHocPhan": "0101123001", "tenMonHoc": "Lập trình mạng", "soTinChi": 3},
            {"maHocPhan": "0101123002", "tenMonHoc": "Hệ cơ sở dữ liệu phân tán", "soTinChi": 3},
            {"maHocPhan": "0101123003", "tenMonHoc": "An toàn thông tin", "soTinChi": 3},
            {"maHocPhan": "0101005004", "tenMonHoc": "Tiếng Anh chuyên ngành CNTT", "soTinChi": 3},
        ],
    )


def _graded_row(
    code: str, name: str, credits: int, qt: float, thi: float, tk: float, he4: float, letter: str
) -> dict[str, Any]:
    return {
        "maHocPhan": code,
        "maLopHocPhan": f"{code}01",
        "tenMonHoc": name,
        "soTinChi": credits,
        "diemQT": qt,
        "diemThi": thi,
        "diemTongKet": tk,
        "diemTinChi": he4,
        "diemChu": letter,
        "isDat": True,
        "diemThanhPhan": [
            {"tenThanhPhan": "Điểm quá trình", "diem": qt, "trongSo": 40},
            {"tenThanhPhan": "Điểm thi kết thúc", "diem": thi, "trongSo": 60},
        ],
    }


def build_demo_full_transcript(ref_date: date | None = None) -> list[dict[str, Any]]:
    """Build 3-semester fictional CNTT transcript (newest first) for Academic Radar."""
    term_code, current_label, y1, y2 = academic_term_for(ref_date)
    prev_label_1, prev_label_2 = _previous_terms(term_code, y1, y2)

    sem_current = {
        "id": 202603,
        "tenDot": current_label,
        "tongKetDot": {
            "diemTBHocLuc": None,
            "diemTBTinChi": None,
            "diemTBHocLucTichLuy": 8.24,
            "diemTBTinChiTichLuy": 3.42,
            "stcdatHocKy": 9,
            "soTCKhongDat": 0,
            "soTCTichLuy": 78,
            "soDiemRenLuyen": 88,
            "xepLoaiHocLuc": "Giỏi",
        },
        "danhSachMonHoc": [
            _graded_row("0101123001", "Lập trình mạng", 3, 8.8, 8.5, 8.6, 4.0, "A"),
            _graded_row("0101123002", "Hệ cơ sở dữ liệu phân tán", 3, 8.2, 7.9, 8.0, 3.5, "B+"),
            _graded_row("0101005004", "Tiếng Anh chuyên ngành CNTT", 3, 8.5, 8.1, 8.3, 3.5, "B+"),
            {
                "maHocPhan": "0101123003",
                "maLopHocPhan": "010112300301",
                "tenMonHoc": "An toàn thông tin",
                "soTinChi": 3,
                "diemQT": 8.6,
                "diemThi": None,
                "diemTongKet": None,
                "diemTinChi": None,
                "diemChu": "",
                "isDat": None,
                "diemThanhPhan": [
                    {"tenThanhPhan": "Chuyên cần", "diem": 9.0, "trongSo": 20},
                    {"tenThanhPhan": "Báo cáo nhóm 01", "diem": 8.5, "trongSo": 30},
                ],
            },
        ],
    }

    sem_prev_1 = {
        "id": 202602,
        "tenDot": prev_label_1,
        "tongKetDot": {
            "diemTBHocLuc": 8.18,
            "diemTBTinChi": 3.40,
            "diemTBHocLucTichLuy": 8.15,
            "diemTBTinChiTichLuy": 3.38,
            "stcdatHocKy": 15,
            "soTCKhongDat": 0,
            "soTCTichLuy": 69,
            "soDiemRenLuyen": 86,
            "xepLoaiHocLuc": "Giỏi",
        },
        "danhSachMonHoc": [
            _graded_row("0101122011", "Mạng máy tính", 3, 8.6, 8.4, 8.5, 4.0, "A"),
            _graded_row("0101122012", "Hệ điều hành", 3, 8.2, 8.0, 8.1, 3.5, "B+"),
            _graded_row("0101122013", "Công nghệ phần mềm", 3, 8.5, 8.1, 8.3, 3.5, "B+"),
            _graded_row("0101122014", "Phân tích và thiết kế hệ thống", 3, 8.0, 7.8, 7.9, 3.5, "B+"),
            _graded_row("0101122015", "Xác suất thống kê ứng dụng", 3, 8.0, 7.6, 7.8, 3.5, "B+"),
        ],
    }

    sem_prev_2 = {
        "id": 202601,
        "tenDot": prev_label_2,
        "tongKetDot": {
            "diemTBHocLuc": 7.92,
            "diemTBTinChi": 3.20,
            "diemTBHocLucTichLuy": 7.92,
            "diemTBTinChiTichLuy": 3.20,
            "stcdatHocKy": 15,
            "soTCKhongDat": 0,
            "soTCTichLuy": 54,
            "soDiemRenLuyen": 84,
            "xepLoaiHocLuc": "Khá",
        },
        "danhSachMonHoc": [
            _graded_row("0101121001", "Cấu trúc dữ liệu và giải thuật", 3, 8.4, 8.2, 8.3, 3.5, "B+"),
            _graded_row("0101121002", "Cơ sở dữ liệu", 3, 8.3, 8.0, 8.1, 3.5, "B+"),
            _graded_row("0101121003", "Lập trình hướng đối tượng", 3, 8.2, 7.9, 8.0, 3.5, "B+"),
            _graded_row("0101121004", "Kiến trúc máy tính", 3, 7.8, 7.5, 7.6, 3.0, "B"),
            _graded_row("0101121005", "Toán rời rạc", 3, 7.7, 7.5, 7.6, 3.0, "B"),
        ],
    }

    return [sem_current, sem_prev_1, sem_prev_2]
