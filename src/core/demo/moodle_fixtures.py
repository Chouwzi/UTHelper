"""Structured course, assignment, quiz, section, and grade fixtures for Demo Mode."""

from __future__ import annotations

from datetime import datetime, timedelta
import html
from typing import Any

from core.demo.academic import academic_term_for
from core.demo.files import (
    LECTURE_PDF_PATH,
    MARKDOWN_SUMMARY_PATH,
    SAMPLE_LAB_ZIP_PATH,
    SUBMITTED_REPORT_PDF_PATH,
    build_lecture_pdf_bytes,
    build_markdown_summary_bytes,
    build_sample_lab_zip_bytes,
    build_submitted_report_pdf_bytes,
)

DEMO_STUDENT_FULLNAME = "Nguyễn Minh Khôi"
DEMO_STUDENT_DISPLAY_MSSV = "00002026"


def bounded_month_due_ts(now: datetime) -> int:
    """Return a timestamp guaranteed inside `[month_start, next_month_start]`."""
    month_start = datetime(now.year, now.month, 1)
    next_month = (
        datetime(now.year + 1, 1, 1)
        if now.month == 12
        else datetime(now.year, now.month + 1, 1)
    )
    min_ts = int(month_start.timestamp()) + 1800
    max_ts = int(next_month.timestamp()) - 1800
    preferred = int((now + timedelta(hours=30)).timestamp())
    return max(min_ts, min(preferred, max_ts))


def courses_catalog(origin: str, now: datetime) -> list[dict[str, Any]]:
    term_code, _ten_dot, y1, y2 = academic_term_for(now.date())
    start_ts = int((now - timedelta(days=45)).timestamp())
    end_ts = int((now + timedelta(days=75)).timestamp())
    specs = (
        (
            101,
            "010112300101",
            "Lập trình mạng",
            68.0,
            "Học phần Lập trình mạng — Khoa Công nghệ thông tin UTH.",
            "TS. Trần Quốc Bảo",
            "bao.tq.demo@ut.edu.vn",
            "0901234567",
        ),
        (
            102,
            "010112300201",
            "Hệ cơ sở dữ liệu phân tán",
            55.0,
            "Thiết kế phân mảnh, nhân bản và đồng bộ giao dịch phân tán.",
            "PGS.TS. Lê Hoàng Nam",
            "nam.lh.demo@ut.edu.vn",
            "0902345678",
        ),
        (
            103,
            "010112300301",
            "An toàn thông tin",
            75.0,
            "Mật mã ứng dụng, xác thực phiên và phòng thủ ứng dụng Web.",
            "ThS. Phạm Thu Hà",
            "ha.pt.demo@ut.edu.vn",
            "0903456789",
        ),
    )
    return [
        {
            "id": cid,
            "shortname": f"[CNTT]_{term_code}_{y1}-{y2}_{name}_[{code}]",
            "fullname": f"[{term_code} {y1}-{y2}] {name} [{code}]",
            "displayname": name,
            "summary": (
                f"<p>{desc}</p>"
                f"<p>Giảng viên: {teacher} - Email: {email} - SĐT: {phone}</p>"
            ),
            "summaryformat": 1,
            "startdate": start_ts,
            "enddate": end_ts,
            "progress": prog,
            "completed": False,
            "visible": 1,
            "viewurl": f"{origin}/course/view.php?id={cid}",
        }
        for cid, code, name, prog, desc, teacher, email, phone in specs
    ]


def thnn_catalog(origin: str, now: datetime) -> list[dict[str, Any]]:
    term_code, _ten_dot, y1, y2 = academic_term_for(now.date())
    return [
        {
            "id": 201,
            "shortname": f"[THNN]_{term_code}_{y1}-{y2}_Tiếng Anh chuyên ngành CNTT_[010100500401]",
            "fullname": f"[{term_code} {y1}-{y2}] Tiếng Anh chuyên ngành CNTT [010100500401]",
            "displayname": "Tiếng Anh chuyên ngành CNTT",
            "viewurl": f"{origin}/course/view.php?id=201",
            "progress": 60,
            "hasprogress": True,
        }
    ]


def _assign_entry(
    aid: int, cmid: int, cid: int, name: str, open_ts: int, due_ts: int, cutoff_ts: int, intro: str
) -> dict[str, Any]:
    return {
        "id": aid,
        "cmid": cmid,
        "course": cid,
        "name": name,
        "nosubmissions": 0,
        "submissiondrafts": 0,
        "allowsubmissionsfromdate": open_ts,
        "duedate": due_ts,
        "cutoffdate": cutoff_ts,
        "teamsubmission": 0,
        "requiresubmissionstatement": 0,
        "intro": intro,
        "configs": [
            {"plugin": "file", "subtype": "assignsubmission", "name": "enabled", "value": "1"},
            {"plugin": "file", "subtype": "assignsubmission", "name": "maxfilesubmissions", "value": "5"},
            {"plugin": "file", "subtype": "assignsubmission", "name": "maxsubmissionsizebytes", "value": str(20 * 1024 * 1024)},
            {"plugin": "file", "subtype": "assignsubmission", "name": "filetypeslist", "value": ".pdf,.zip,.docx,.txt,.md,.py"},
        ],
    }


def assignments_by_course(now: datetime) -> dict[int, list[dict[str, Any]]]:
    now_ts = int(now.timestamp())
    open_ts = now_ts - 7 * 86400
    sub_due = bounded_month_due_ts(now)
    return {
        101: [
            _assign_entry(
                501, 101, 101,
                "Bài tập thực hành tuần 6: Xây dựng TCP Echo Server đa luồng",
                open_ts, now_ts + 8 * 3600, now_ts + 32 * 3600,
                "<p>Triển khai TCP Echo Server hỗ trợ đồng thời tối thiểu 10 client bằng <code>ThreadPoolExecutor</code>, kèm framing độ dài 4 byte và báo cáo kiểm thử PDF.</p>",
            )
        ],
        102: [
            _assign_entry(
                502, 102, 102,
                "Thiết kế lược đồ phân mảnh ngang và nhân bản dữ liệu",
                open_ts, now_ts + 48 * 3600, now_ts + 72 * 3600,
                "<p>Phân tích vị từ phân mảnh ngang cho cơ sở dữ liệu điều độ xe buýt TP.HCM giữa Cơ sở Quận 12 và Cơ sở Bình Thạnh, kèm kịch bản SQL kiểm chứng.</p>",
            )
        ],
        103: [
            _assign_entry(
                503, 103, 103,
                "Lab 04: Cấu hình Content-Security-Policy & HttpOnly Session",
                open_ts, now_ts + 6 * 86400, now_ts + 8 * 86400,
                "<p>Thiết lập header bảo mật HTTP chống XSS/CSRF trên ứng dụng mẫu và chụp kết quả kiểm tra tự động nộp dưới dạng tệp PDF.</p>",
            ),
            _assign_entry(
                504, 104, 103,
                "Báo cáo nhóm 01: Phân tích bề mặt tấn công ứng dụng Web",
                open_ts, sub_due, sub_due + 48 * 3600,
                "<p>Tổng hợp kết quả rà soát bề mặt tấn công OWASP Top 10 và đề xuất phương án khắc phục cho cổng thông tin nội bộ mẫu.</p>",
            ),
        ],
    }


def submission_status_payload(assign_id: int, origin: str, now: datetime) -> dict[str, Any]:
    now_ts = int(now.timestamp())
    files: list[dict[str, Any]] = []
    status = "new"
    mod_ts = now_ts - 86400
    sub_id = 9000 + max(1, assign_id)
    if assign_id == 504:
        report_bytes = build_submitted_report_pdf_bytes()
        mod_ts = now_ts - 5 * 3600
        status = "submitted"
        sub_id = 9504
        files = [
            {
                "filename": "Bao_cao_nhom_01.pdf",
                "filepath": "/",
                "filesize": len(report_bytes),
                "fileurl": f"{origin}{SUBMITTED_REPORT_PDF_PATH}",
                "timemodified": mod_ts,
                "mimetype": "application/pdf",
            }
        ]
    return {
        "lastattempt": {
            "submissionsenabled": True,
            "canedit": True,
            "cansubmit": True,
            "locked": False,
            "graded": False,
            "gradingstatus": "notgraded",
            "attemptnumber": 0,
            "submission": {
                "id": sub_id,
                "status": status,
                "timecreated": mod_ts - 600,
                "timemodified": mod_ts,
                "attemptnumber": 0,
                "plugins": [
                    {
                        "type": "file",
                        "name": "File submissions",
                        "fileareas": [{"area": "submission_files", "files": files}],
                    }
                ],
            },
        },
        "feedback": {},
        "warnings": [],
    }


def quiz_review_questions_spec() -> list[dict[str, Any]]:
    return [
        {
            "slot": 1,
            "number": "1",
            "state": "gradedright",
            "status": "Đúng",
            "mark": "2.50",
            "maxmark": "2.50",
            "stem": "::SOCKET_Q01::[html] Thứ tự gọi hàm chuẩn trên máy chủ TCP lắng nghe kết nối là gì?",
            "options": [
                ("a.", "socket() → bind() → listen() → accept()", True, "correct"),
                ("b.", "socket() → connect() → listen() → recv()", False, "neutral"),
                ("c.", "bind() → socket() → accept() → listen()", False, "neutral"),
                ("d.", "listen() → bind() → socket() → accept()", False, "neutral"),
            ],
            "right": "socket() → bind() → listen() → accept()",
            "feedback": "Máy chủ TCP phải khởi tạo socket, gắn địa chỉ (bind), mở hàng đợi (listen) trước khi chấp nhận kết nối (accept).",
        },
        {
            "slot": 2,
            "number": "2",
            "state": "gradedright",
            "status": "Đúng",
            "mark": "2.50",
            "maxmark": "2.50",
            "stem": "::SOCKET_Q02::[html] Đặc điểm nào sau đây phân biệt giao thức UDP với TCP?",
            "options": [
                ("a.", "Đảm bảo thứ tự gói tin và truyền lại tự động", False, "neutral"),
                ("b.", "Phi kết nối (connectionless) và bảo toàn ranh giới datagram", True, "correct"),
                ("c.", "Bắt buộc bắt tay ba bước trước khi truyền dữ liệu", False, "neutral"),
                ("d.", "Không sử dụng số hiệu cổng (port) ở tầng giao vận", False, "neutral"),
            ],
            "right": "Phi kết nối (connectionless) và bảo toàn ranh giới datagram",
            "feedback": "UDP gửi từng gói datagram độc lập mà không thiết lập kết nối trước, giảm độ trễ cho ứng dụng thời gian thực.",
        },
        {
            "slot": 3,
            "number": "3",
            "state": "gradedwrong",
            "status": "Sai",
            "mark": "0.00",
            "maxmark": "2.50",
            "stem": "::SOCKET_Q03::[html] Khi hàm recv(1024) trên TCP socket trả về chuỗi byte rỗng b'', điều đó có ý nghĩa gì?",
            "options": [
                ("a.", "Mạng tạm thời chưa có dữ liệu mới, cần gọi lại ngay", True, "incorrect"),
                ("b.", "Phía đối tác đã đóng kết nối chiều gửi (EOF / FIN)", False, "correct"),
                ("c.", "Bộ đệm nhận của hệ điều hành bị tràn", False, "neutral"),
                ("d.", "Cổng mạng đã chuyển sang chế độ UDP", False, "neutral"),
            ],
            "right": "Phía đối tác đã đóng kết nối chiều gửi (EOF / FIN)",
            "feedback": "Trên socket chặn (blocking), recv() trả về b'' báo hiệu đầu bên kia đã gửi cờ FIN đóng kết nối.",
        },
        {
            "slot": 4,
            "number": "4",
            "state": "gradedright",
            "status": "Đúng",
            "mark": "2.50",
            "maxmark": "2.50",
            "stem": "::SOCKET_Q04::[html] Giải pháp nào xử lý triệt để hiện tượng dính gói (Sticky Packet) trên luồng TCP?",
            "options": [
                ("a.", "Tăng kích thước tham số recv() lên 65535 byte", False, "neutral"),
                ("b.", "Thêm sleep(0.1) giữa hai lần gọi send()", False, "neutral"),
                ("c.", "Đóng gói độ dài bản tin cố định ở đầu mỗi khung (Length-Prefixed Framing)", True, "correct"),
                ("d.", "Tắt kiểm tra tổng kiểm (checksum) của tầng IP", False, "neutral"),
            ],
            "right": "Đóng gói độ dài bản tin cố định ở đầu mỗi khung (Length-Prefixed Framing)",
            "feedback": "TCP là giao thức luồng byte liên tục; header độ dài cố định giúp bên nhận tách chính xác từng thông điệp.",
        },
    ]


def render_question_dom_html(q: dict[str, Any]) -> str:
    cls_state = "correct" if q["state"] == "gradedright" else "incorrect"
    opt_rows: list[str] = []
    for idx, (num, txt, checked, opt_st) in enumerate(q["options"]):
        r_cls = f"r{idx % 2}" + (f" {opt_st}" if opt_st in {"correct", "incorrect"} else "")
        chk_attr = ' checked="checked"' if checked else ""
        opt_rows.append(
            f'<div class="{r_cls}">'
            f'<input type="radio" name="q{q["slot"]}" value="{idx}" disabled="disabled"{chk_attr} />'
            f'<span class="answernumber">{html.escape(num)} </span>'
            f"<span>{html.escape(txt)}</span></div>"
        )
    return (
        f'<div class="que multichoice {cls_state}"><div class="info">'
        f'<span class="qno">{html.escape(q["number"])}</span>'
        f'<div class="state">{html.escape(q["status"])}</div>'
        f'<div class="grade">Điểm {q["mark"]} trên {q["maxmark"]}</div></div>'
        '<div class="content">'
        f'<div class="formulation"><div class="qtext"><p>{html.escape(q["stem"])}</p></div>'
        f'<div class="ablock"><div class="answer">{"".join(opt_rows)}</div></div></div>'
        '<div class="outcome">'
        f'<div class="generalfeedback"><p>{html.escape(q["feedback"])}</p></div>'
        f'<div class="rightanswer">Câu trả lời đúng là: {html.escape(q["right"])}</div>'
        "</div></div></div>"
    )


def _mod(
    cmid: int,
    instance: int,
    modname: str,
    name: str,
    url: str,
    *,
    desc: str = "",
    completion: int = 0,
    state: int = 0,
    contents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": cmid,
        "instance": instance,
        "name": name,
        "modname": modname,
        "url": url,
        "description": desc,
        "completion": completion,
        "contents": contents or [],
    }
    if completion > 0:
        item["completiondata"] = {"state": state}
    return item


def _file_blob(name: str, size: int, url: str, ts: int, mime: str) -> dict[str, Any]:
    return {
        "type": "file",
        "filename": name,
        "filepath": "/",
        "filesize": size,
        "fileurl": url,
        "timemodified": ts,
        "mimetype": mime,
    }


def course_contents(course_id: int, origin: str, now: datetime) -> list[dict[str, Any]]:
    mod_ts = int((now - timedelta(days=3)).timestamp())
    if course_id == 101:
        pdf_bytes = build_lecture_pdf_bytes()
        zip_bytes = build_sample_lab_zip_bytes()
        return [
            {
                "id": 1101,
                "name": "Thông tin chung & Đề cương học phần",
                "visible": 1,
                "summary": "<p>Tài liệu hướng dẫn học tập và lịch thực hành phòng máy Quận 12.</p>",
                "section": 0,
                "modules": [
                    _mod(111, 301, "forum", "Thông báo từ giảng viên", f"{origin}/mod/forum/view.php?id=111", desc="<p>Kênh thông báo lịch thực hành và giải đáp bài tập môn Lập trình mạng.</p>"),
                    _mod(112, 401, "label", "Lịch học & Quy định phòng Lab", f"{origin}/course/view.php?id=101#module-112", desc="<p><strong>Lưu ý thực hành:</strong> Sinh viên mang theo tài liệu Chương 1–2 và kiểm tra môi trường Python 3.12+ trước buổi Lab tại phòng A.202 (Quận 12).</p>"),
                    _mod(113, 451, "page", "Đề cương & Hướng dẫn chấm đồ án môn học", f"{origin}/mod/page/view.php?id=113", desc="<p>Quy chuẩn đóng gói mã nguồn và tiêu chí đánh giá.</p>", completion=1, state=1),
                    _mod(106, 801, "attendance", "Điểm danh chuyên cần Lập trình mạng", f"{origin}/mod/attendance/view.php?id=106", desc="<p>Bảng theo dõi chuyên cần lý thuyết và thực hành.</p>"),
                ],
            },
            {
                "id": 1102,
                "name": "Chương 1–2: Lập trình Socket TCP/UDP & Framing",
                "visible": 1,
                "summary": "<p>Slide bài giảng PDF, mã nguồn mẫu `.zip`, bài kiểm tra và bài tập tuần 6.</p>",
                "section": 1,
                "modules": [
                    _mod(114, 701, "resource", "Bài giảng Chương 1–2: Socket TCP & UDP (PDF)", f"{origin}/mod/resource/view.php?id=114", completion=1, state=1, contents=[_file_blob("Bai_giang_Chuong_01_Socket_TCP_UDP.pdf", len(pdf_bytes), f"{origin}{LECTURE_PDF_PATH}", mod_ts, "application/pdf")]),
                    _mod(115, 702, "resource", "Mã nguồn mẫu Lab 02: TCP Multi-Client (.zip)", f"{origin}/mod/resource/view.php?id=115", completion=1, state=0, contents=[_file_blob("Lab02_TCP_MultiClient_Sample.zip", len(zip_bytes), f"{origin}{SAMPLE_LAB_ZIP_PATH}", mod_ts, "application/zip")]),
                    _mod(105, 601, "quiz", "Kiểm tra trắc nghiệm Chương 1–2: Lập trình Socket", f"{origin}/mod/quiz/view.php?id=105", desc="<p>Thời gian làm bài: 30 phút (tối đa 2 lượt).</p>", completion=2, state=1),
                    _mod(101, 501, "assign", "Bài tập thực hành tuần 6: Xây dựng TCP Echo Server đa luồng", f"{origin}/mod/assign/view.php?id=101", desc="<p>Nộp mã nguồn và báo cáo kiểm thử định dạng PDF/ZIP.</p>", completion=2, state=0),
                ],
            },
        ]
    if course_id == 102:
        md_bytes = build_markdown_summary_bytes()
        return [
            {
                "id": 1201,
                "name": "Chương 1: Thiết kế phân mảnh & Định vị dữ liệu",
                "visible": 1,
                "summary": "<p>Tài liệu tóm tắt Markdown và bài tập thiết kế phân mảnh ngang.</p>",
                "section": 1,
                "modules": [
                    _mod(121, 452, "page", "Hướng dẫn cài đặt cụm PostgreSQL thực hành", f"{origin}/mod/page/view.php?id=121", desc="<p>Cấu hình Docker Compose 3 node cho bài thực hành phân mảnh.</p>", completion=1, state=1),
                    _mod(122, 703, "resource", "Tóm tắt ôn tập CSDL phân tán (.md)", f"{origin}/mod/resource/view.php?id=122", completion=1, state=1, contents=[_file_blob("Tom_tat_on_tap_CSDL_phan_tan.md", len(md_bytes), f"{origin}{MARKDOWN_SUMMARY_PATH}", mod_ts, "text/markdown")]),
                    _mod(102, 502, "assign", "Thiết kế lược đồ phân mảnh ngang và nhân bản dữ liệu", f"{origin}/mod/assign/view.php?id=102", desc="<p>Bài tập cá nhân tuần này.</p>", completion=2, state=0),
                ],
            }
        ]
    if course_id == 103:
        return [
            {
                "id": 1301,
                "name": "Chương 1–2: Phân tích bề mặt tấn công & Phòng thủ Web",
                "visible": 1,
                "summary": "<p>Báo cáo nhóm đã nộp và bài Lab cấu hình HTTP Security Headers.</p>",
                "section": 1,
                "modules": [
                    _mod(131, 303, "forum", "Diễn đàn thảo luận An toàn thông tin", f"{origin}/mod/forum/view.php?id=131", desc="<p>Trao đổi chủ đề bảo mật ứng dụng Web.</p>"),
                    _mod(104, 504, "assign", "Báo cáo nhóm 01: Phân tích bề mặt tấn công ứng dụng Web", f"{origin}/mod/assign/view.php?id=104", desc="<p>Đã nộp tệp mẫu Bao_cao_nhom_01.pdf.</p>", completion=2, state=1),
                    _mod(103, 503, "assign", "Lab 04: Cấu hình Content-Security-Policy & HttpOnly Session", f"{origin}/mod/assign/view.php?id=103", desc="<p>Thực hành cấu hình CSP chống XSS.</p>", completion=2, state=0),
                ],
            }
        ]
    return []


def _grade_item(
    iid: int, name: str, itype: str, mod: str, raw: float | None, weight: str, fb: str = ""
) -> dict[str, Any]:
    g_fmt = f"{raw:.2f}" if raw is not None else "-"
    pct_fmt = f"{raw * 10.0:.2f} %" if raw is not None else "-"
    return {
        "id": iid,
        "itemname": name,
        "itemtype": itype,
        "itemmodule": mod,
        "graderaw": raw,
        "gradeformatted": g_fmt,
        "rangeformatted": "0.00–10.00",
        "percentageformatted": pct_fmt,
        "weightformatted": weight,
        "feedback": fb,
    }


def grade_items_for_course(course_id: int) -> list[dict[str, Any]]:
    table: dict[int, list[dict[str, Any]]] = {
        101: [
            _grade_item(8101, "Điểm danh chuyên cần (20%)", "mod", "attendance", 9.2, "20.00 %", "<p>Tham dự đầy đủ các buổi Lab.</p>"),
            _grade_item(8102, "Kiểm tra trắc nghiệm Chương 1–2 (30%)", "mod", "quiz", 7.5, "30.00 %", "<p>Nắm vững quy trình khởi tạo Socket TCP/UDP.</p>"),
            _grade_item(8103, "Bài tập thực hành tuần 6 (50%)", "mod", "assign", None, "50.00 %"),
            _grade_item(8109, "Tổng kết học phần", "course", "", 8.6, "100.00 %"),
        ],
        102: [
            _grade_item(8201, "Chuyên cần & Thảo luận (20%)", "mod", "attendance", 8.5, "20.00 %"),
            _grade_item(8202, "Bài tập thực hành phân mảnh SQL (30%)", "mod", "assign", 8.0, "30.00 %", "<p>Thiết kế vị từ phân mảnh hợp lý.</p>"),
            _grade_item(8209, "Tổng kết học phần", "course", "", 8.0, "100.00 %"),
        ],
        103: [
            _grade_item(8301, "Chuyên cần (20%)", "mod", "attendance", 9.0, "20.00 %"),
            _grade_item(8302, "Báo cáo nhóm 01 (30%)", "mod", "assign", 8.5, "30.00 %", "<p>Phân tích đầy đủ kịch bản phòng chống XSS/SQLi.</p>"),
            _grade_item(8309, "Tổng kết tạm tính", "course", "", 8.6, "100.00 %"),
        ],
        201: [
            _grade_item(8401, "Attendance & Participation (20%)", "mod", "attendance", 9.0, "20.00 %", "Active participation in technical discussions."),
            _grade_item(8402, "Vocabulary & Reading Quiz - Unit 3 (30%)", "mod", "quiz", 8.0, "30.00 %", "Good command of cloud architecture terminology."),
            _grade_item(8409, "Course total", "course", "", 8.3, "100.00 %"),
        ],
    }
    return table.get(course_id, table[101])
