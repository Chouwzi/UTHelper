"""Offline HTML and RFC 5545 ICS page renderers for Demo Mode Moodle sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
from typing import Any
from urllib.parse import parse_qs, urlsplit

from core.demo.files import (
    COURSES_ORIGIN,
    LECTURE_PDF_PATH,
    THNN_ORIGIN,
    THNN_READING_PDF_PATH,
)
from core.demo.moodle_fixtures import (
    grade_items_for_course,
    quiz_review_questions_spec,
    render_question_dom_html,
)


def _as_int(val: Any, default: int = 0) -> int:
    if val is None or isinstance(val, bool):
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def render_grade_report_html(course_id: int, *, site_origin: str) -> str:
    """Render Moodle `/grade/report/user/index.php` HTML compatible with both scrapers."""
    origin = (site_origin or COURSES_ORIGIN).rstrip("/")
    items = grade_items_for_course(int(course_id or 101))
    rows_html: list[str] = []
    for item in items:
        iid = item["id"]
        tr_cls = "level1 baggt" if item["itemtype"] == "course" else "item"
        mod = item["itemmodule"]
        href = (
            f"{origin}/mod/attendance/view.php?id=106"
            if mod == "attendance"
            else (f"{origin}/mod/{mod}/view.php?id={iid}" if mod else "")
        )
        name_html = (
            f'<a class="gradeitemheader" href="{html.escape(href)}">{html.escape(item["itemname"])}</a>'
            if href
            else f'<span class="rowtitle">{html.escape(item["itemname"])}</span>'
        )
        rows_html.append(
            f'<tr class="{tr_cls}">'
            f'<th class="column-itemname" id="row_{iid}">{name_html}</th>'
            f'<td class="column-weight">{html.escape(item["weightformatted"])}</td>'
            f'<td class="column-grade" headers="grade{iid}">{html.escape(item["gradeformatted"])}</td>'
            f'<td class="column-range">{html.escape(item["rangeformatted"])}</td>'
            f'<td class="column-percentage" headers="percentage{iid}">{html.escape(item["percentageformatted"])}</td>'
            f'<td class="column-feedback">{item["feedback"]}</td>'
            "</tr>"
        )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Grade report</title></head>"
        "<body><div id='region-main'>"
        f"<table class='generaltable user-grade'><tbody>{''.join(rows_html)}</tbody></table>"
        "</div></body></html>"
    )


def render_thnn_calendar_ics(now: datetime | None = None) -> str:
    """Render valid RFC 5545 ICS calendar export for THNN activities."""
    ref_now = now or datetime.now()
    base_utc = ref_now.astimezone(timezone.utc)
    due_assign = (base_utc + timedelta(hours=76)).strftime("%Y%m%dT%H%M%SZ")
    due_quiz = (base_utc + timedelta(hours=120)).strftime("%Y%m%dT%H%M%SZ")
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//UTH THNN Demo Sandbox//VN\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:801@thnn.ut.edu.vn\r\n"
        "SUMMARY:Bài viết luận: Technical Summary on Cloud Gateways tới hạn\r\n"
        f"DTEND:{due_assign}\r\n"
        "CATEGORIES:[THNN] Tiếng Anh chuyên ngành CNTT\r\n"
        "END:VEVENT\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:802@thnn.ut.edu.vn\r\n"
        "SUMMARY:Vocabulary & Reading Quiz - Unit 3 closes\r\n"
        f"DTEND:{due_quiz}\r\n"
        "CATEGORIES:[THNN] Tiếng Anh chuyên ngành CNTT\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR"
    )


def _render_attendance_page_html(now: datetime) -> str:
    statuses = (
        ("Có mặt (P)", "2 / 2", "Đúng giờ - Phòng B.304"),
        ("Có mặt (P)", "2 / 2", "Thực hành Lab Mạng A.202"),
        ("Có mặt (P)", "2 / 2", "Đúng giờ - Phòng B.304"),
        ("Đến trễ (L)", "1 / 2", "Báo trước lý do kẹt xe tuyến Quốc lộ 1A"),
        ("Có mặt (P)", "2 / 2", "Thực hành Lab Mạng A.202"),
        ("Có mặt (P)", "2 / 2", "Thảo luận thiết kế giao thức nhóm"),
    )
    rows = "".join(
        "<tr>"
        f"<td>{(now - timedelta(days=(6 - idx) * 4)).strftime('%d/%m/%Y 07:00')}</td>"
        f"<td>Buổi học tuần {idx + 1}</td>"
        f"<td>{st_label}</td><td>{pts}</td><td>{remark}</td></tr>"
        for idx, (st_label, pts, remark) in enumerate(statuses)
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
        "<div class='page-header-headings'><h1>Điểm danh chuyên cần Lập trình mạng</h1></div>"
        "<table class='attlist'>"
        "<tr><th>Số buổi đã điểm danh</th><td>6</td></tr>"
        "<tr><th>Điểm trên số buổi</th><td>11 / 12</td></tr>"
        "<tr><th>Tỷ lệ chuyên cần (%)</th><td>91.7%</td></tr>"
        "</table>"
        f"<table class='generaltable attwidth'><tbody>{rows}</tbody></table>"
        "</body></html>"
    )


def render_session_html(
    url: str,
    *,
    now: datetime | None = None,
) -> tuple[str, str, bool]:
    """Render offline Moodle session HTML/ICS for `MoodleClient.open_session_url`."""
    ref_now = now or datetime.now()
    raw = str(url or "").strip()
    if not raw:
        return (f"{COURSES_ORIGIN}/", "<html><body></body></html>", False)

    parsed = urlsplit(raw)
    if parsed.scheme and parsed.netloc:
        origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        if origin not in {COURSES_ORIGIN, THNN_ORIGIN}:
            return (raw, "", True)
        final_url = raw
    else:
        origin = COURSES_ORIGIN
        final_url = f"{origin}/{raw.lstrip('/')}"
        parsed = urlsplit(final_url)

    path = parsed.path or "/"
    qs = parse_qs(parsed.query)

    if path == "/calendar/export.php":
        return (final_url, render_thnn_calendar_ics(ref_now), False)

    if path == "/calendar/view.php":
        return (
            final_url,
            (
                "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
                f"<a data-action='view-event' data-event-id='801' href='{origin}/mod/assign/view.php?id=2001'>"
                "Bài viết luận: Technical Summary on Cloud Gateways</a>"
                f"<a data-action='view-event' data-event-id='802' href='{origin}/mod/quiz/view.php?id=2002'>"
                "Vocabulary &amp; Reading Quiz - Unit 3</a></body></html>"
            ),
            False,
        )

    if path == "/grade/report/user/index.php":
        cid = _as_int((qs.get("id") or ["101"])[0], 101)
        return (final_url, render_grade_report_html(cid, site_origin=origin), False)

    if "/mod/attendance/" in path:
        return (final_url, _render_attendance_page_html(ref_now), False)

    if path == "/mod/quiz/review.php":
        finished_str = (ref_now - timedelta(hours=18)).strftime("%H:%M %d/%m/%Y")
        q_blocks = "".join(render_question_dom_html(q) for q in quiz_review_questions_spec())
        return (
            final_url,
            (
                "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
                "<table class='quizreviewsummary'>"
                f"<tr><th>Kết thúc vào</th><td>{finished_str}</td></tr>"
                "<tr><th>Thời gian thực hiện</th><td>18 phút 40 giây</td></tr>"
                "<tr><th>Điểm</th><td>7.50 trên 10.00 (75%)</td></tr>"
                f"</table>{q_blocks}</body></html>"
            ),
            False,
        )

    if path == "/mod/quiz/view.php":
        return (
            final_url,
            (
                "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
                "<h2>Vocabulary &amp; Reading Quiz - Unit 3</h2>"
                "<table class='generaltable quizattemptsummary'><tbody>"
                "<tr><td>1</td><td>Đã hoàn thành</td><td>8.00 / 10.00</td>"
                f"<td><a href='{origin}/mod/quiz/review.php?attempt=2701'>Xem lại</a></td></tr>"
                "</tbody></table></body></html>"
            ),
            False,
        )

    if path == "/mod/resource/view.php":
        cmid = _as_int((qs.get("id") or ["2003"])[0], 2003)
        file_href, label = (
            (f"{THNN_ORIGIN}{THNN_READING_PDF_PATH}", "IT_English_Unit_03_Cloud_Architecture.pdf")
            if origin == THNN_ORIGIN or cmid >= 2000
            else (f"{COURSES_ORIGIN}{LECTURE_PDF_PATH.removeprefix('/webservice')}", "Bai_giang_Chuong_01_Socket_TCP_UDP.pdf")
        )
        return (
            final_url,
            (
                "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
                "<div class='resourceworkaround'>"
                f"Nhấn vào liên kết <a href='{file_href}'>{label}</a> để mở tài liệu."
                "</div></body></html>"
            ),
            False,
        )

    if path == "/mod/forum/view.php":
        return (
            final_url,
            (
                "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
                "<table class='discussion-list'><tbody><tr>"
                f"<th class='topic'><a href='{origin}/mod/forum/discuss.php?d=3901'>"
                "Hướng dẫn học tập và lịch kiểm tra giữa kỳ</a></th>"
                "<td class='author'>ThS. Đặng Minh Châu</td><td class='replies'>2</td>"
                "</tr></tbody></table></body></html>"
            ),
            False,
        )

    if path == "/mod/page/view.php":
        return (
            final_url,
            (
                "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
                "<div id='region-main'><div class='box generalbox'><div class='no-overflow'>"
                "<h2>Technical Writing Structure for IT Students</h2>"
                "<p>Bài viết tóm tắt kỹ thuật cần nêu rõ bài toán, kiến trúc đề xuất và các chỉ số độ trễ / khả năng chịu lỗi.</p>"
                "<ul><li>Mở bài: giới thiệu ngữ cảnh hệ thống giao thông thông minh.</li>"
                "<li>Thân bài: phân tích kiến trúc Edge Gateway &amp; Cloud Cluster.</li>"
                "<li>Kết luận: tóm tắt ưu nhược điểm trong 2 câu ngắn gọn.</li></ul>"
                "</div></div></div></body></html>"
            ),
            False,
        )

    return (
        final_url,
        (
            "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
            '<script>M.cfg = {"sesskey":"demoSessKey2026"};</script>'
            "<div id='region-main'><h1>UTH Demo Sandbox</h1>"
            "<p>Phiên xem thử ngoại tuyến dành cho kiểm duyệt ứng dụng.</p></div></body></html>"
        ),
        False,
    )
