"""Unit tests for the pure-Python Reviewer Demo Sandbox (`core.demo_sandbox`)."""

from __future__ import annotations

import io
from datetime import date, datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
import pypdf

from core.academic_radar.calculator import calculate_radar_profile
from core.academic_radar.gpa_planner import (
    CreditProgress,
    calculate_gpa_target,
    calculate_graduation_target,
    parse_credit_progress,
)
from core.academic_radar.normalizer import SemesterNormalizer
from core.course_grade_scraper import parse_course_grade_report
from core.courses.activity_service import (
    CourseActivityService,
    parse_attendance_html,
    parse_quiz_review_html_page,
)
from core.courses.archive_service import ArchiveService
from core.courses.catalog_service import _apply_portal_enrichment
from core.courses.document_converter import convert_document_bytes
from core.courses.pdf_renderer import PdfDocumentSession
from core.courses.providers import CoursesWsProvider, ThnnCourseProvider
from core.demo_sandbox import (
    DEMO_USERNAME,
    DEMO_USER_ID,
    DailyScheduleSnapshot,
    WeekScheduleSnapshot,
    get_demo_credit_progress,
    get_demo_current_semester_courses,
    get_demo_day_schedule,
    get_demo_file_bytes,
    get_demo_full_transcript,
    get_demo_week_schedule,
    handle_demo_ajax_call,
    handle_demo_ws_call,
    is_demo_account,
    is_demo_credentials,
    normalize_demo_username,
    render_demo_grade_report_html,
    render_demo_session_html,
)
from core.moodle_service import MoodleService
from core.submission_snapshot import parse_submission_snapshot


def _reviewer_password() -> str:
    return "UTH-" + "Review-2026!"


def _ws_caller(*, site_origin: str = "https://courses.ut.edu.vn", now: datetime | None = None):
    def _call(function: str, **params):
        res = handle_demo_ws_call(function, params, site_origin=site_origin, now=now)
        if isinstance(res, dict) and "exception" in res:
            return None
        return res

    return _call


class _DemoSessionClientStub:
    def __init__(self, origin: str, *, now: datetime | None = None) -> None:
        self.moodle_site_origin = origin
        self._now = now

    def call_ajax_api(self, function: str, **params):
        return handle_demo_ajax_call(
            function, params, site_origin=self.moodle_site_origin, now=self._now
        )

    def login_session(self, force: bool = False) -> bool:
        return True

    def open_session_url(self, url: str) -> tuple[str, str, bool]:
        return render_demo_session_html(url, now=self._now)

    def download_file(self, url: str, **_kwargs) -> bytes | None:
        return get_demo_file_bytes(url)

    def download_session_file(self, url: str, **_kwargs) -> bytes | None:
        return get_demo_file_bytes(url)


def test_demo_credentials_accept_trimmed_case_insensitive_username_and_reject_wrong_password():
    pw = _reviewer_password()
    assert DEMO_USERNAME == "uthelper_demo"
    assert DEMO_USER_ID == 990_001
    assert is_demo_account("  UTHelper_Demo  ") is True
    assert is_demo_credentials("  UTHelper_Demo  ", pw) is True
    assert normalize_demo_username("  UTHelper_Demo  ") == "uthelper_demo"
    assert normalize_demo_username("  2251120001  ") == "2251120001"
    assert normalize_demo_username(None) == ""
    assert is_demo_credentials("uthelper_demo", "wrong-pass") is False
    assert is_demo_credentials("uthelper_demo", pw + " ") is False
    assert is_demo_credentials("uthelper_demo", "") is False
    assert is_demo_credentials("uthelper_demo", None) is False
    assert is_demo_credentials("2251120001", pw) is False
    assert is_demo_account("2251120001") is False
    assert is_demo_account("") is False
    assert is_demo_account(None) is False


def test_demo_password_plaintext_is_never_stored_in_src():
    pw = _reviewer_password()
    src_root = Path(__file__).resolve().parents[1] / "src"
    offenders = [
        str(py_file.relative_to(src_root))
        for py_file in src_root.rglob("*.py")
        if pw in py_file.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_demo_ws_and_schedule_remain_fresh_for_future_review_dates():
    future_now = datetime(2028, 1, 2, 9, 0, 0)  # Year-boundary week (Sunday)
    events = handle_demo_ws_call(
        "core_calendar_get_action_events_by_timesort", {}, now=future_now
    )
    assert isinstance(events, dict)
    event_list = events["events"]
    assert any(ev["timestart"] > int(future_now.timestamp()) for ev in event_list)
    modulenames = {ev["modulename"] for ev in event_list}
    assert {"assign", "quiz", "attendance"}.issubset(modulenames)

    # Check urgency spread relative to future_now (urgent ~8h, warning ~2d, safe ~6d)
    assign_hours = sorted(
        (ev["timestart"] - int(future_now.timestamp())) / 3600.0
        for ev in event_list
        if ev["modulename"] == "assign"
    )
    assert any(0 < h < 24 for h in assign_hours)
    assert any(24 <= h < 72 for h in assign_hours)
    assert any(h >= 72 for h in assign_hours)

    day = get_demo_day_schedule(date(2028, 1, 2))
    assert isinstance(day, DailyScheduleSnapshot)
    assert day.schedule_date == date(2028, 1, 2)
    assert len(day.sessions) >= 2
    assert all(s.campus and s.room and s.has_outline and "##" in s.outline for s in day.sessions)

    week = get_demo_week_schedule(date(2028, 1, 2))
    assert isinstance(week, WeekScheduleSnapshot)
    assert week.monday == date(2027, 12, 27)
    assert len(week.sessions) >= 4
    assert week.days[date(2028, 1, 2)] == day.sessions

    # Dec-31 year-end boundary: submitted assignment stays inside [Dec 1, Jan 1]
    year_end_now = datetime(2028, 12, 31, 23, 50, 0)
    assign_payload = handle_demo_ws_call(
        "mod_assign_get_assignments", {"courseids[0]": 103}, now=year_end_now
    )
    submitted_assign = next(
        a for a in assign_payload["courses"][0]["assignments"] if a["id"] == 504
    )
    assert int(datetime(2028, 12, 1).timestamp()) <= submitted_assign["duedate"] < int(
        datetime(2029, 1, 1).timestamp()
    )


def test_demo_lecture_pdf_and_zip_bytes_are_valid_in_memory():
    service = MoodleService(_ws_caller())
    client = _DemoSessionClientStub("https://courses.ut.edu.vn")
    provider = CoursesWsProvider(service=service, client=client)

    courses = provider.fetch_courses()
    assert len(courses) == 3

    all_attachments = [
        att
        for course in courses
        for sec in provider.fetch_course_sections(course.id)
        for mod in sec.modules
        for att in mod.attachments
    ]
    pdf_att = next(att for att in all_attachments if att.preview_kind == "pdf")
    md_att = next(att for att in all_attachments if att.extension == ".md")
    zip_att = next(att for att in all_attachments if att.is_archive and att.extension == ".zip")

    pdf_bytes = get_demo_file_bytes(f"{pdf_att.file_url}?token=demo_token")
    assert pdf_bytes is not None
    assert len(pdf_bytes) == pdf_att.file_size
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) >= 2
    extracted_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "UTH" in extracted_text

    pdf_session = PdfDocumentSession(pdf_bytes)
    try:
        assert pdf_session.supported is True
        assert pdf_session.page_count >= 2
        assert len(pdf_session.outline) >= 2
    finally:
        pdf_session.close()

    zip_bytes = get_demo_file_bytes(zip_att.file_url)
    assert zip_bytes is not None
    assert len(zip_bytes) == zip_att.file_size
    archive_service = ArchiveService()
    tree = archive_service.inspect_bytes(zip_att.file_name, zip_bytes)
    assert tree.supported is True
    assert tree.total_files >= 3
    readme_bytes = archive_service.extract_entry_bytes(
        zip_att.file_name, zip_bytes, "lab02_tcp_sample/README.md"
    )
    assert b"TCP" in readme_bytes

    md_bytes = get_demo_file_bytes(md_att.file_url)
    assert md_bytes is not None
    assert len(md_bytes) == md_att.file_size
    converted = convert_document_bytes(md_att.file_name, md_bytes, "text")
    assert converted.kind == "text"
    assert "Phân mảnh ngang" in converted.markdown_text
    assert converted.html_document

    assert get_demo_file_bytes("https://evil.example.com/pluginfile.php/1/a.pdf") is None
    assert get_demo_file_bytes("https://courses.ut.edu.vn/pluginfile.php/999/missing.pdf") is None


def test_demo_ws_catalog_assignments_submission_quiz_and_grades_parse_cleanly():
    fixed_now = datetime(2027, 4, 15, 10, 0, 0)
    service = MoodleService(_ws_caller(now=fixed_now))
    client = _DemoSessionClientStub("https://courses.ut.edu.vn", now=fixed_now)
    provider = CoursesWsProvider(service=service, client=client)
    act_service = CourseActivityService(
        moodle_service=service,
        courses_client=client,
        thnn_client=_DemoSessionClientStub("https://thnn.ut.edu.vn", now=fixed_now),
    )

    site_info = service.get_site_info()
    assert site_info is not None
    assert site_info["userid"] == DEMO_USER_ID
    assert site_info["fullname"] == "Nguyễn Minh Khôi"
    assert "00002026" in str(site_info.get("idnumber") or site_info.get("sitename") or "")

    courses = provider.fetch_courses()
    assert [c.display_name for c in courses] == [
        "Lập trình mạng",
        "Hệ cơ sở dữ liệu phân tán",
        "An toàn thông tin",
    ]

    # Verify assignments + submitted Bao_cao_nhom_01.pdf snapshot
    assign_courses = service.get_assignments([c.id for c in courses]) or []
    flat_assigns = [a for c in assign_courses for a in c.get("assignments", [])]
    assert len(flat_assigns) >= 4

    snapshots = []
    for assign in flat_assigns:
        aid = int(assign["id"])
        raw_status = service.get_submission_status(aid)
        assert raw_status is not None
        snap = parse_submission_snapshot(aid, assign, raw_status)
        assert snap.submissions_enabled is True
        assert snap.file_submission_enabled is True
        snapshots.append(snap)

    submitted_snaps = [s for s in snapshots if s.raw_status == "submitted"]
    assert submitted_snaps
    submitted_file = submitted_snaps[0].remote_files[0]
    assert submitted_file.name == "Bao_cao_nhom_01.pdf"
    submitted_bytes = get_demo_file_bytes(submitted_file.url)
    assert submitted_bytes is not None
    assert len(submitted_bytes) == submitted_file.size

    # Verify CourseSection module coverage across the 3 courses
    all_mods = [
        (course.id, mod)
        for course in courses
        for sec in provider.fetch_course_sections(course.id)
        for mod in sec.modules
    ]
    mod_types = {mod.modname for _, mod in all_mods}
    assert {"forum", "page", "label", "resource", "assign", "quiz", "attendance"}.issubset(
        mod_types
    )

    # Forum announcements & posts
    forum_cid, forum_mod = next((cid, m) for cid, m in all_mods if m.modname == "forum")
    forum_summary = act_service.fetch_forum_activity("courses", forum_mod, course_id=forum_cid)
    assert len(forum_summary.discussions) >= 1
    posts = act_service.fetch_discussion_posts(
        "courses", forum_summary.discussions[0].discussion_id
    )
    assert len(posts) >= 1

    # Timed quiz + reviewable attempt (both WS and HTML review page)
    quiz_cid, quiz_mod = next((cid, m) for cid, m in all_mods if m.modname == "quiz")
    quiz_summary = act_service.fetch_quiz_activity("courses", quiz_mod, course_id=quiz_cid)
    assert quiz_summary.time_limit_seconds > 0
    assert len(quiz_summary.attempts) >= 1
    attempt = quiz_summary.attempts[0]
    report = act_service.fetch_quiz_review_report(
        attempt.attempt_id, source="courses", review_url=attempt.review_url
    )
    assert len(report.questions) >= 3
    assert report.correct_count >= 1
    assert report.incorrect_count >= 1

    _, review_html, redir = render_demo_session_html(attempt.review_url, now=fixed_now)
    assert redir is False
    html_report = parse_quiz_review_html_page(review_html, attempt_id=attempt.attempt_id)
    assert len(html_report.questions) >= 3

    # Attendance module HTML
    _, att_mod = next((cid, m) for cid, m in all_mods if m.modname == "attendance")
    att_summary = act_service.fetch_attendance("courses", att_mod)
    assert len(att_summary.records) >= 4
    assert "%" in att_summary.percentage

    # Course grades (WS & HTML grade report)
    for course in courses:
        ws_grades = provider.fetch_course_grades(course.id)
        assert len(ws_grades) >= 3
        grade_html = render_demo_grade_report_html(
            course.id, site_origin="https://courses.ut.edu.vn"
        )
        scraped = parse_course_grade_report(grade_html, course.id)
        assert len(scraped.rows) >= 3


def test_demo_thnn_ajax_and_session_html_parse_cleanly():
    fixed_now = datetime(2028, 3, 10, 8, 30, 0)
    thnn_client = _DemoSessionClientStub("https://thnn.ut.edu.vn", now=fixed_now)
    thnn_provider = ThnnCourseProvider(client=thnn_client)

    thnn_courses = thnn_provider.fetch_courses()
    assert len(thnn_courses) == 1
    assert thnn_courses[0].display_name == "Tiếng Anh chuyên ngành CNTT"

    # Pagination offset beyond catalog returns empty list cleanly
    page2 = handle_demo_ajax_call(
        "core_course_get_enrolled_courses_by_timeline_classification",
        {"classification": "all", "limit": 50, "offset": 50},
        now=fixed_now,
    )
    assert page2["error"] is False
    assert page2["data"]["courses"] == []

    sections = thnn_provider.fetch_course_sections(thnn_courses[0].id)
    assert len(sections) >= 2
    thnn_mods = [m for sec in sections for m in sec.modules]
    res_mod = next(m for m in thnn_mods if m.modname == "resource")
    resolved_atts = thnn_provider.resolve_module_attachments(res_mod)
    assert len(resolved_atts) >= 1
    thnn_pdf = thnn_provider.download_file(resolved_atts[0].file_url)
    assert thnn_pdf is not None and thnn_pdf.startswith(b"%PDF-")

    thnn_grades = thnn_provider.fetch_course_grades(thnn_courses[0].id)
    assert len(thnn_grades) >= 3

    # THNN ICS export + month calendar HTML for DataOrchestrator._fetch_thnn_activities
    _, ics_text, ics_redir = render_demo_session_html(
        "https://thnn.ut.edu.vn/calendar/export.php", now=fixed_now
    )
    assert ics_redir is False
    assert ics_text.startswith("BEGIN:VCALENDAR")
    assert ics_text.rstrip().endswith("END:VCALENDAR")
    assert "BEGIN:VEVENT" in ics_text

    month_ts = int(datetime(fixed_now.year, fixed_now.month, 1, tzinfo=timezone.utc).timestamp())
    _, month_html, m_redir = render_demo_session_html(
        f"https://thnn.ut.edu.vn/calendar/view.php?view=month&time={month_ts}",
        now=fixed_now,
    )
    assert m_redir is False
    soup = BeautifulSoup(month_html, "html.parser")
    event_links = soup.find_all("a", attrs={"data-action": "view-event"})
    assert len(event_links) >= 1
    assert any("thnn.ut.edu.vn/mod/" in (a.get("href") or "") for a in event_links)

    att_url = "https://courses.ut.edu.vn/mod/attendance/view.php?id=106"
    _, att_html, _ = render_demo_session_html(att_url, now=fixed_now)
    parsed_att = parse_attendance_html(
        att_html, base_url="https://courses.ut.edu.vn", page_url=att_url
    )
    assert len(parsed_att.records) >= 4


def test_demo_write_ws_and_ajax_calls_are_rejected_read_only():
    expected = {
        "exception": "moodle_exception",
        "errorcode": "demo_read_only",
        "message": "Demo mode is read-only",
    }
    for write_fn in (
        "mod_assign_save_submission",
        "mod_assign_submit_for_grading",
        "core_completion_update_activity_completion_status_manually",
        "core_files_upload",
        "core_files_get_unused_draft_itemid",
        "core_files_delete_draft_files",
        "mod_choice_submit_choice_response",
    ):
        assert handle_demo_ws_call(write_fn, {}) == expected

    ajax_err = handle_demo_ajax_call(
        "core_completion_update_activity_completion_status_manually", {"cmid": 101}
    )
    assert ajax_err["error"] is True
    assert ajax_err["errorcode"] == "demo_read_only"


def test_demo_portal_transcript_credit_progress_and_current_semester_parse_cleanly():
    progress = parse_credit_progress(get_demo_credit_progress())
    assert progress == CreditProgress(required_credits=132.0, earned_credits=78.0)

    raw_transcript = get_demo_full_transcript()
    assert len(raw_transcript) == 3
    semesters = SemesterNormalizer().normalize_transcript(raw_transcript)
    assert len(semesters) == 3

    for idx, sem in enumerate(semesters):
        prev_sem = semesters[idx + 1] if idx + 1 < len(semesters) else None
        profile = calculate_radar_profile(sem, previous_semester=prev_sem)
        scored_axes = [m for m in profile.metrics.values() if m.score is not None]
        assert len(scored_axes) == 6

    target_res = calculate_gpa_target(semesters[0], 3.5)
    assert target_res.status in {"needed", "achieved"}
    assert target_res.pending_credits > 0

    grad_res = calculate_graduation_target(semesters, progress, 3.5)
    assert grad_res.status in {"needed", "achieved"}
    assert grad_res.earned_credits == 78.0
    assert grad_res.required_credits == 132.0

    term_label, portal_specs = get_demo_current_semester_courses()
    assert term_label
    assert len(portal_specs) == 4

    # Verify Portal current-semester enrichment matches all 4 demo courses (3 Courses + 1 THNN)
    service = MoodleService(_ws_caller())
    courses_provider = CoursesWsProvider(
        service=service, client=_DemoSessionClientStub("https://courses.ut.edu.vn")
    )
    thnn_provider = ThnnCourseProvider(
        client=_DemoSessionClientStub("https://thnn.ut.edu.vn")
    )
    combined = courses_provider.fetch_courses() + thnn_provider.fetch_courses()
    enriched = _apply_portal_enrichment(combined, term_label, portal_specs)
    assert len(enriched) == 4
    assert all(c.is_current_term and c.credits > 0 for c in enriched)
