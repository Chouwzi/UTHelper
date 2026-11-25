"""Read-only Moodle WS and AJAX routers for Reviewer Demo Mode."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from core.demo.files import COURSES_ORIGIN, THNN_ORIGIN
from core.demo.moodle_fixtures import (
    DEMO_STUDENT_DISPLAY_MSSV,
    DEMO_STUDENT_FULLNAME,
    assignments_by_course,
    course_contents,
    courses_catalog,
    grade_items_for_course,
    quiz_review_questions_spec,
    render_question_dom_html,
    submission_status_payload,
    thnn_catalog,
)

_WRITE_WS_FUNCTIONS = frozenset(
    {
        "mod_assign_save_submission",
        "mod_assign_submit_for_grading",
        "core_completion_update_activity_completion_status_manually",
        "core_files_upload",
        "core_files_get_unused_draft_itemid",
        "core_files_delete_draft_files",
        "mod_choice_submit_choice_response",
    }
)

_WRITE_MARKERS = (
    "_save_",
    "_submit_",
    "_update_",
    "_delete_",
    "_upload",
    "_create_",
    "_remove_",
    "_set_",
)


def is_write_function(function: str) -> bool:
    fn = str(function or "").strip().lower()
    return fn in _WRITE_WS_FUNCTIONS or any(m in fn for m in _WRITE_MARKERS)


def demo_read_only_ws_error() -> dict[str, str]:
    return {
        "exception": "moodle_exception",
        "errorcode": "demo_read_only",
        "message": "Demo mode is read-only",
    }


def demo_read_only_ajax_error() -> dict[str, Any]:
    return {
        "error": True,
        "errorcode": "demo_read_only",
        "message": "Demo mode is read-only",
        "exception": {"message": "Demo mode is read-only", "errorcode": "demo_read_only"},
    }


def _as_int(val: Any, default: int = 0) -> int:
    if val is None or isinstance(val, bool):
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _extract_int_list_param(params: Mapping[str, Any], base_key: str) -> list[int]:
    direct = params.get(base_key)
    if isinstance(direct, (list, tuple, set)):
        return [v for x in direct if (v := _as_int(x, -1)) > 0]
    prefix = f"{base_key}["
    indexed: list[tuple[int, int]] = []
    for k, raw_val in params.items():
        key_str = str(k)
        if key_str.startswith(prefix) and key_str.endswith("]"):
            idx = _as_int(key_str[len(prefix) : -1], len(indexed))
            parsed = _as_int(raw_val, -1)
            if parsed > 0:
                indexed.append((idx, parsed))
    indexed.sort(key=lambda item: item[0])
    return [val for _, val in indexed]


def _quiz_catalog(now: datetime) -> list[dict[str, Any]]:
    now_ts = int(now.timestamp())
    return [
        {
            "id": 601,
            "course": 101,
            "coursemodule": 105,
            "name": "Kiểm tra trắc nghiệm Chương 1–2: Lập trình Socket",
            "intro": "<p>Bài kiểm tra 30 phút ôn tập kiến trúc TCP/UDP, vòng đời Socket và kỹ thuật đóng gói bản tin (Message Framing). Cho phép tối đa 2 lượt làm bài.</p>",
            "timeopen": now_ts - 3 * 86400,
            "timeclose": now_ts + 96 * 3600,
            "timelimit": 1800,
            "attempts": 2,
            "grademethod": 1,
            "sumgrades": 10.0,
            "grade": 10.0,
            "browsersecurity": "-",
        }
    ]


def _quiz_attempt_review(attempt_id: int, user_id: int, now: datetime) -> dict[str, Any]:
    finish_ts = int((now - timedelta(hours=18)).timestamp())
    start_ts = finish_ts - 1120
    questions = [
        {
            "slot": q["slot"],
            "type": "multichoice",
            "page": 0,
            "number": q["number"],
            "state": q["state"],
            "status": q["status"],
            "mark": q["mark"],
            "maxmark": q["maxmark"],
            "html": render_question_dom_html(q),
        }
        for q in quiz_review_questions_spec()
    ]
    return {
        "grade": "7.50",
        "attempt": {
            "id": attempt_id,
            "quiz": 601,
            "userid": user_id,
            "attempt": 1,
            "state": "finished",
            "timestart": start_ts,
            "timefinish": finish_ts,
            "sumgrades": 7.5,
        },
        "additionaldata": [
            {"id": "grade", "title": "Điểm", "content": "<b>7.50</b> trên 10.00 (<b>75%</b>)"}
        ],
        "questions": questions,
        "warnings": [],
    }


def handle_ws_call(
    function: str,
    params: Mapping[str, Any],
    *,
    user_id: int,
    username: str,
    site_origin: str = COURSES_ORIGIN,
    now: datetime | None = None,
) -> Any:
    """Route a Moodle REST Web Service call in Demo Mode."""
    fn = str(function or "").strip()
    if is_write_function(fn):
        return demo_read_only_ws_error()

    ref_now = now or datetime.now()
    origin = (site_origin or COURSES_ORIGIN).rstrip("/")
    courses = courses_catalog(origin, ref_now)
    course_by_id = {int(c["id"]): c for c in courses}

    if fn == "core_webservice_get_site_info":
        return {
            "sitename": f"UTH E-Learning Demo (MSSV mẫu: {DEMO_STUDENT_DISPLAY_MSSV})",
            "username": username,
            "firstname": "Minh Khôi",
            "lastname": "Nguyễn",
            "fullname": DEMO_STUDENT_FULLNAME,
            "idnumber": DEMO_STUDENT_DISPLAY_MSSV,
            "lang": "vi",
            "userid": user_id,
            "siteurl": origin,
            "userpictureurl": "",
            "release": "4.3+ (UTH Demo Sandbox)",
        }

    if fn == "core_enrol_get_users_courses":
        return courses

    if fn == "core_course_get_enrolled_courses_by_timeline_classification":
        return {"courses": courses, "nextoffset": len(courses)}

    if fn == "core_calendar_get_action_events_by_timesort":
        now_ts = int(ref_now.timestamp())
        raw_events = [
            {"id": 9001, "name": "Bài tập thực hành tuần 6: Xây dựng TCP Echo Server đa luồng tới hạn", "modulename": "assign", "instance": 101, "eventtype": "due", "timestart": now_ts + 8 * 3600, "timesort": now_ts + 8 * 3600, "url": f"{origin}/mod/assign/view.php?id=101", "course": {"id": 101, "fullname": course_by_id[101]["fullname"]}},
            {"id": 9006, "name": "Điểm danh chuyên cần Lập trình mạng", "modulename": "attendance", "instance": 106, "eventtype": "attendance", "timestart": now_ts + 26 * 3600, "timesort": now_ts + 26 * 3600, "url": f"{origin}/mod/attendance/view.php?id=106", "course": {"id": 101, "fullname": course_by_id[101]["fullname"]}},
            {"id": 9002, "name": "Thiết kế lược đồ phân mảnh ngang và nhân bản dữ liệu tới hạn", "modulename": "assign", "instance": 102, "eventtype": "due", "timestart": now_ts + 48 * 3600, "timesort": now_ts + 48 * 3600, "url": f"{origin}/mod/assign/view.php?id=102", "course": {"id": 102, "fullname": course_by_id[102]["fullname"]}},
            {"id": 9005, "name": "Kiểm tra trắc nghiệm Chương 1–2: Lập trình Socket đóng", "modulename": "quiz", "instance": 105, "eventtype": "close", "timestart": now_ts + 96 * 3600, "timesort": now_ts + 96 * 3600, "url": f"{origin}/mod/quiz/view.php?id=105", "course": {"id": 101, "fullname": course_by_id[101]["fullname"]}},
            {"id": 9003, "name": "Lab 04: Cấu hình Content-Security-Policy & HttpOnly Session tới hạn", "modulename": "assign", "instance": 103, "eventtype": "due", "timestart": now_ts + 6 * 86400, "timesort": now_ts + 6 * 86400, "url": f"{origin}/mod/assign/view.php?id=103", "course": {"id": 103, "fullname": course_by_id[103]["fullname"]}},
        ]
        ts_from = _as_int(params.get("timesortfrom"), 0)
        ts_to = _as_int(params.get("timesortto"), 0)
        after_id = _as_int(params.get("aftereventid"), 0)
        limit = max(1, _as_int(params.get("limitnum"), 50))
        filtered = [
            ev
            for ev in raw_events
            if (ts_from <= 0 or ev["timesort"] >= ts_from)
            and (ts_to <= 0 or ev["timesort"] <= ts_to)
            and (after_id <= 0 or ev["id"] > after_id)
        ][:limit]
        return {
            "events": filtered,
            "firstid": filtered[0]["id"] if filtered else None,
            "lastid": filtered[-1]["id"] if filtered else None,
            "moreevents": False,
        }

    if fn == "core_calendar_get_calendar_events":
        req_cids = set(_extract_int_list_param(params, "events[courseids]"))
        now_ts = int(ref_now.timestamp())
        cal_events = [
            {"id": 9501, "name": "Kiểm tra trắc nghiệm Chương 1–2: Lập trình Socket", "courseid": 101, "modulename": "quiz", "instance": 601, "eventtype": "close", "timestart": now_ts + 96 * 3600},
            {"id": 9502, "name": "Bài tập thực hành tuần 6: Xây dựng TCP Echo Server đa luồng", "courseid": 101, "modulename": "assign", "instance": 501, "eventtype": "due", "timestart": now_ts + 8 * 3600},
        ]
        if req_cids:
            cal_events = [e for e in cal_events if e["courseid"] in req_cids]
        return {"events": cal_events, "warnings": []}

    if fn == "mod_assign_get_assignments":
        req_cids = set(_extract_int_list_param(params, "courseids"))
        assign_map = assignments_by_course(ref_now)
        selected = [cid for cid in (101, 102, 103) if not req_cids or cid in req_cids]
        return {
            "courses": [
                {
                    "id": cid,
                    "fullname": course_by_id[cid]["fullname"],
                    "shortname": course_by_id[cid]["shortname"],
                    "assignments": assign_map.get(cid, []),
                }
                for cid in selected
            ],
            "warnings": [],
        }

    if fn == "mod_assign_get_submission_status":
        return submission_status_payload(_as_int(params.get("assignid"), 501), origin, ref_now)

    if fn == "mod_quiz_get_quizzes_by_courses":
        req_cids = set(_extract_int_list_param(params, "courseids"))
        quizzes = [q for q in _quiz_catalog(ref_now) if not req_cids or q["course"] in req_cids]
        return {"quizzes": quizzes, "warnings": []}

    if fn == "mod_quiz_get_user_attempts":
        if _as_int(params.get("quizid"), 601) != 601:
            return {"attempts": [], "warnings": []}
        finish_ts = int((ref_now - timedelta(hours=18)).timestamp())
        return {
            "attempts": [
                {
                    "id": 701,
                    "quiz": 601,
                    "userid": user_id,
                    "attempt": 1,
                    "uniqueid": 7701,
                    "state": "finished",
                    "timestart": finish_ts - 1120,
                    "timefinish": finish_ts,
                    "timemodified": finish_ts,
                    "sumgrades": 7.5,
                }
            ],
            "warnings": [],
        }

    if fn == "mod_quiz_get_quiz_access_information":
        return {
            "canattempt": True,
            "canmanage": False,
            "canpreview": False,
            "isfinished": False,
            "ispreflightcheckrequired": False,
            "accessrules": [
                "Thời gian làm bài giới hạn: 30 phút.",
                "Phương thức tính điểm: Điểm cao nhất trong tối đa 2 lượt.",
            ],
            "activerulenames": [],
            "preventaccessreasons": [],
            "preventnewattemptreasons": [],
            "warnings": [],
        }

    if fn == "mod_quiz_get_attempt_review":
        return _quiz_attempt_review(_as_int(params.get("attemptid"), 701), user_id, ref_now)

    if fn == "gradereport_overview_get_course_grades":
        return {
            "grades": [
                {"courseid": 101, "grade": "8.60", "rawgrade": "8.60"},
                {"courseid": 102, "grade": "8.00", "rawgrade": "8.00"},
                {"courseid": 103, "grade": "8.60", "rawgrade": "8.60"},
            ],
            "warnings": [],
        }

    if fn == "gradereport_user_get_grade_items":
        cid = _as_int(params.get("courseid"), 101)
        return {
            "usergrades": [
                {
                    "courseid": cid,
                    "userid": user_id,
                    "userfullname": DEMO_STUDENT_FULLNAME,
                    "gradeitems": grade_items_for_course(cid),
                }
            ],
            "warnings": [],
        }

    if fn == "message_popup_get_unread_popup_notification_count":
        return 2

    if fn == "core_course_get_updates_since":
        return {"instances": [], "warnings": []}

    if fn == "core_course_get_contents":
        return course_contents(_as_int(params.get("courseid"), 101), origin, ref_now)

    if fn == "mod_page_get_pages_by_courses":
        req_cids = set(_extract_int_list_param(params, "courseids"))
        if (single := _as_int(params.get("courseid"), 0)) > 0:
            req_cids.add(single)
        pages = [
            {
                "id": 451,
                "coursemodule": 113,
                "course": 101,
                "name": "Đề cương & Hướng dẫn chấm đồ án môn học",
                "intro": "<p>Quy định nộp đồ án môn Lập trình mạng.</p>",
                "content": "<h2>Quy chuẩn đồ án Lập trình mạng</h2><p>Sinh viên làm việc theo nhóm 3–4 thành viên, xây dựng ứng dụng mạng client-server có xử lý đồng thời và giao thức đóng gói rõ ràng.</p><ul><li>Điểm thiết kế giao thức & tài liệu: 30%</li><li>Điểm demo chịu tải & xử lý lỗi mất kết nối: 50%</li><li>Vấn đáp cá nhân: 20%</li></ul>",
            },
            {
                "id": 452,
                "coursemodule": 121,
                "course": 102,
                "name": "Hướng dẫn cài đặt cụm PostgreSQL thực hành",
                "intro": "<p>Môi trường Docker cho thực hành phân mảnh.</p>",
                "content": "<h2>Khởi tạo cụm CSDL phân tán cục bộ</h2><p>Sử dụng Docker Compose khởi chạy 1 nút điều phối và 2 nút dữ liệu đại diện cho chi nhánh Quận 12 và Bình Thạnh.</p>",
            },
        ]
        if req_cids:
            pages = [p for p in pages if p["course"] in req_cids]
        return {"pages": pages, "warnings": []}

    if fn == "core_group_get_course_user_groups":
        return {
            "groups": [{"id": 401, "name": "Nhóm 04 - CNTT", "description": "Nhóm thực hành môn học (Nguyễn Minh Khôi)"}],
            "warnings": [],
        }

    if fn == "mod_forum_get_forums_by_courses":
        req_cids = set(_extract_int_list_param(params, "courseids"))
        forums = [
            {"id": 301, "course": 101, "cmid": 111, "type": "news", "name": "Thông báo từ giảng viên", "intro": "<p>Thông báo chính thức học phần Lập trình mạng.</p>"},
            {"id": 303, "course": 103, "cmid": 131, "type": "general", "name": "Diễn đàn thảo luận An toàn thông tin", "intro": "<p>Thảo luận chuyên đề bảo mật ứng dụng Web.</p>"},
        ]
        return [f for f in forums if not req_cids or f["course"] in req_cids]

    if fn == "mod_forum_get_forum_discussions":
        fid = _as_int(params.get("forumid"), 301)
        created_str = (ref_now - timedelta(days=2)).strftime("%d/%m/%Y %H:%M")
        disc = (
            {
                "id": 3301,
                "discussion": 3301,
                "name": "Hướng dẫn rà soát CSP và cookie phiên cho Lab 04",
                "subject": "Hướng dẫn rà soát CSP và cookie phiên cho Lab 04",
                "userfullname": "ThS. Phạm Thu Hà",
                "created": created_str,
                "numreplies": 1,
                "pinned": True,
                "message": "<p>Các nhóm lưu ý không dùng <code>unsafe-inline</code> trong chính sách Content-Security-Policy của bài thực hành tuần này.</p>",
            }
            if fid == 303
            else {
                "id": 3101,
                "discussion": 3101,
                "name": "Lịch thực hành phòng máy A.202 và hướng dẫn nộp bài tuần 6",
                "subject": "Lịch thực hành phòng máy A.202 và hướng dẫn nộp bài tuần 6",
                "userfullname": "TS. Trần Quốc Bảo",
                "created": created_str,
                "numreplies": 1,
                "pinned": True,
                "message": "<p>Chào lớp, thầy đã đăng mã nguồn mẫu <code>Lab02_TCP_MultiClient_Sample.zip</code> trong Chương 1–2 để các em tham khảo cách tách khung dữ liệu trước khi làm bài tuần 6.</p>",
            }
        )
        return {"discussions": [disc], "warnings": []}

    if fn == "mod_forum_get_discussion_posts":
        did = _as_int(params.get("discussionid"), 3101)
        return {
            "posts": [
                {
                    "id": did * 10 + 1,
                    "subject": "Thông báo hướng dẫn thực hành học phần",
                    "timecreated": int((ref_now - timedelta(days=2)).timestamp()),
                    "author": {"fullname": "TS. Trần Quốc Bảo"},
                    "message": "<p>Các em tải tệp bài giảng PDF và bộ khung mã nguồn mẫu `.zip` ngay trong tab <strong>Môn học</strong> để đọc trực tiếp ngoại tuyến.</p>",
                    "attachments": [],
                }
            ],
            "warnings": [],
        }

    if fn == "mod_choice_get_choice_options":
        return {
            "options": [
                {"id": 1, "text": "Ca thực hành Sáng Thứ 2 (Tiết 1–3)", "checked": True, "disabled": False, "numberofuser": 18, "maxanswers": 30},
                {"id": 2, "text": "Ca thực hành Chiều Thứ 2 (Tiết 7–9)", "checked": False, "disabled": False, "numberofuser": 15, "maxanswers": 30},
            ],
            "warnings": [],
        }

    if fn == "mod_book_get_books_by_courses":
        return {"books": [], "warnings": []}

    return {"warnings": []}


def handle_ajax_call(
    function: str,
    params: Mapping[str, Any],
    *,
    site_origin: str = THNN_ORIGIN,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Route a Moodle AJAX call (`thnn.ut.edu.vn`) in Demo Mode."""
    fn = str(function or "").strip()
    if is_write_function(fn):
        return demo_read_only_ajax_error()

    ref_now = now or datetime.now()
    origin = (site_origin or THNN_ORIGIN).rstrip("/")

    if fn == "core_course_get_enrolled_courses_by_timeline_classification":
        all_courses = thnn_catalog(origin, ref_now)
        offset = max(0, _as_int(params.get("offset"), 0))
        limit = max(1, _as_int(params.get("limit"), 50))
        sliced = all_courses[offset : offset + limit]
        return {"error": False, "data": {"courses": sliced, "nextoffset": offset + len(sliced)}}

    if fn == "core_courseformat_get_state":
        cid = _as_int(params.get("courseid"), 201)
        return {
            "error": False,
            "data": {
                "course": {"id": cid, "numsections": 2},
                "section": [
                    {"id": 2101, "section": 0, "title": "Course Announcements & Guidelines", "visible": True, "cmlist": ["2000", "2004"]},
                    {"id": 2102, "section": 1, "title": "Unit 3: Cloud Computing & Distributed Telemetry", "visible": True, "cmlist": ["2003", "2002", "2001"]},
                ],
                "cm": [
                    {"id": 2000, "module": "forum", "name": "Thông báo học phần Tiếng Anh chuyên ngành CNTT", "url": f"{origin}/mod/forum/view.php?id=2000", "completion": 0, "completionstate": 0},
                    {"id": 2004, "module": "page", "name": "Hướng dẫn viết bài luận kỹ thuật (Technical Writing)", "url": f"{origin}/mod/page/view.php?id=2004", "completion": 1, "completionstate": 1},
                    {"id": 2003, "module": "resource", "name": "Unit 3 Reading: Cloud Architecture (PDF)", "url": f"{origin}/mod/resource/view.php?id=2003", "completion": 1, "completionstate": 1},
                    {"id": 2002, "module": "quiz", "name": "Vocabulary & Reading Quiz - Unit 3", "url": f"{origin}/mod/quiz/view.php?id=2002", "completion": 2, "completionstate": 1},
                    {"id": 2001, "module": "assign", "name": "Bài viết luận: Technical Summary on Cloud Gateways", "url": f"{origin}/mod/assign/view.php?id=2001", "completion": 2, "completionstate": 0},
                ],
            },
        }

    return {"error": False, "data": {}}
