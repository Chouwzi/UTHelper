"""Integration tests for Reviewer Demo seams across MoodleClient, PortalClient,
DataOrchestrator, CourseCatalogService, AcademicGradeService, and SubmissionWorkflow."""

from __future__ import annotations

import asyncio
import io
from datetime import date
import urllib.request
import zipfile

import pypdf
import pytest

from config import settings
from core.academic_radar.gpa_planner import CreditProgress
from core.academic_radar.service import AcademicGradeService
from core.client import MoodleClient
from core.courses.activity_service import CourseActivityService
from core.courses.archive_service import ArchiveService
from core.courses.cache_store import CourseCacheStore
from core.courses.catalog_service import CourseCatalogService
from core.courses.providers import CoursesWsProvider, ThnnCourseProvider
from core.courses.preview_cache import PreviewFileCache
from core.data_orchestrator import DataOrchestrator
from core.demo_sandbox import DEMO_USER_ID
from core.moodle_service import MoodleService
from core.portal_client import PortalClient
from core.submission_models import (
    FileMutationIntent,
    MutationOperation,
    SelectedFile,
)
from core.use_cases.submission_workflow import (
    FinalizeSubmissionIntent,
    SelectedSubmissionFile,
    SubmissionErrorCode,
    SubmissionTarget,
    SubmissionWorkflow,
)
from gui.components.detail.submission_presenter import mutation_message


def _reviewer_password() -> str:
    return "UTH-" + "Review-2026!"


@pytest.fixture
def demo_env(monkeypatch):
    """Forbid all outbound HTTP and configure `uthelper_demo` with valid password."""
    MoodleService(lambda *_a, **_k: None).clear_all_caches()

    def _forbid_network(*_args, **_kwargs):
        raise AssertionError("Outbound network forbidden in demo mode")

    monkeypatch.setattr("core.client._urlopen", _forbid_network)
    monkeypatch.setattr(urllib.request, "build_opener", _forbid_network)

    pw = _reviewer_password()
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(
        settings, "PORTAL_API_BASE", "https://portal-publicapp.ut.edu.vn/api/v1"
    )
    monkeypatch.setattr(settings, "UTH_USERNAME", "uthelper_demo")
    monkeypatch.setattr(settings, "UTH_PASSWORD", pw)
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "stale_token_to_clear")
    monkeypatch.setattr(
        settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn"
    )
    yield pw
    MoodleService(lambda *_a, **_k: None).clear_all_caches()


def test_moodle_client_demo_login_session_reads_and_mutation_guards(demo_env):
    pw = demo_env
    client = MoodleClient()
    thnn_client = MoodleClient("https://thnn.ut.edu.vn")

    assert client.has_site_credentials is True
    assert thnn_client.has_site_credentials is True
    assert MoodleClient("https://evil.example.com").has_site_credentials is False

    assert client.login("  UTHelper_Demo ", pw, force=True) is True
    assert settings.MOODLE_WS_TOKEN == ""
    assert settings.MOODLE_WS_TOKEN_ORIGIN == ""
    assert client.last_login_error == ""

    assert client.login("  UTHelper_Demo ", "wrong-password", force=True) is False
    assert client.last_login_error == "invalid_credentials"
    assert client.login("uthelper_demo", "", force=True) is False
    assert client.last_login_error == "invalid_credentials"
    assert settings.MOODLE_WS_TOKEN == ""

    assert thnn_client.login_session("  UTHelper_Demo ", pw, force=True) is True
    assert thnn_client.last_login_error == ""
    assert (
        thnn_client.login_session("  UTHelper_Demo ", "wrong-password", force=True)
        is False
    )
    assert thnn_client.last_login_error == "invalid_credentials"
    assert thnn_client.login_session("uthelper_demo", "", force=True) is False
    assert thnn_client.last_login_error == "invalid_credentials"

    # Re-establish valid THNN demo session and verify relative URL resolution keeps THNN origin
    assert thnn_client.login_session("uthelper_demo", pw, force=True) is True
    final_url, html_text, redirected = thnn_client.open_session_url(
        "/mod/quiz/view.php?id=2002"
    )
    assert redirected is False
    assert final_url == "https://thnn.ut.edu.vn/mod/quiz/view.php?id=2002"
    assert "quizattemptsummary" in html_text

    status, raw_url, ct, raw_bytes = client.open_session_raw(
        "/mod/attendance/view.php?id=106"
    )
    assert status == 200
    assert raw_url == "https://courses.ut.edu.vn/mod/attendance/view.php?id=106"
    assert "text/html" in ct
    assert b"attlist" in raw_bytes

    post_status, _, _, _ = client.open_session_raw(
        "/mod/attendance/attendance.php", method="POST", body=b"sesskey=x"
    )
    assert post_status == 403

    assert client.get_user_id() == DEMO_USER_ID
    grade_html = client.fetch_grade_report_html(101)
    assert grade_html is not None and "user-grade" in grade_html

    # All client-level mutation methods must fail closed offline
    upload_res = client.upload_draft_file_result("test.pdf", b"%PDF-1.4")
    assert upload_res.record is None
    assert upload_res.error_code != ""
    assert (
        client.remove_assignment_submission(101, expected_user_id=DEMO_USER_ID)
        is False
    )
    assert client.delete_draft_file(12345, "/", "test.pdf") is False
    with pytest.raises(RuntimeError):
        client.submit_session_form("/mod/attendance/attendance.php", {"status": "1"})


def test_data_orchestrator_fetches_activities_and_details_offline_in_demo_mode(
    demo_env,
):
    settings.MOODLE_WS_TOKEN = ""
    settings.MOODLE_WS_TOKEN_ORIGIN = ""

    orch = DataOrchestrator()
    assert orch.login() is True
    assert settings.MOODLE_WS_TOKEN == ""

    outcome = asyncio.run(orch.fetch_activity_outcome_async())
    assert outcome.success is True
    assert outcome.snapshot_complete is True
    assert outcome.can_commit is True
    activities = outcome.activities
    assert len(activities) >= 7

    act_types = {a.get("type") for a in activities}
    assert {"assignment", "quiz", "attendance"}.issubset(act_types)
    thnn_items = [a for a in activities if a.get("site_identity") == "thnn"]
    assert len(thnn_items) >= 2

    # Fetch details across assignment, quiz, attendance, and THNN items offline
    courses_assign = next(
        a
        for a in activities
        if a.get("type") == "assignment" and a.get("site_identity") != "thnn"
    )
    assign_detail = orch.fetch_full_details(courses_assign, force_refresh=True)
    assert assign_detail["details"].get("description_html")
    assert assign_detail["details"].get("status_data")
    assert assign_detail["submission_status"] in {"not_submitted", "submitted"}

    courses_quiz = next(
        a
        for a in activities
        if a.get("type") == "quiz" and a.get("site_identity") != "thnn"
    )
    quiz_detail = orch.fetch_full_details(courses_quiz, force_refresh=True)
    assert quiz_detail["details"].get("description_html")
    assert quiz_detail["details"].get("time_limit")
    assert quiz_detail["submission_status"] == "submitted"

    courses_att = next(a for a in activities if a.get("type") == "attendance")
    att_detail = orch.fetch_full_details(courses_att, force_refresh=True)
    assert att_detail["details"].get("course_full_name")

    thnn_detail = orch.fetch_full_details(thnn_items[0], force_refresh=True)
    assert thnn_detail["url"] == thnn_items[0]["url"]

    courses_with_grades, _ = orch.refresh_course_grades()
    assert len(courses_with_grades) == 3
    assert all(
        not item.error and item.report is not None and len(item.report.rows) >= 3
        for item in courses_with_grades
    )


def test_course_catalog_and_activity_services_run_offline_for_courses_and_thnn(
    demo_env, tmp_path
):
    pw = demo_env
    settings.MOODLE_WS_TOKEN = ""
    settings.MOODLE_WS_TOKEN_ORIGIN = ""

    courses_client = MoodleClient()
    thnn_client = MoodleClient("https://thnn.ut.edu.vn")
    moodle_service = MoodleService(courses_client.call_ws_api)
    portal_client = PortalClient(settings.PORTAL_API_BASE)

    courses_provider = CoursesWsProvider(
        service=moodle_service, client=courses_client
    )
    thnn_provider = ThnnCourseProvider(client=thnn_client)
    catalog_service = CourseCatalogService(
        cache_store=CourseCacheStore(cache_dir=tmp_path / "catalog", namespace="uthelper_demo"),
        preview_cache=PreviewFileCache(cache_dir=tmp_path / "preview"),
        courses_provider=courses_provider,
        thnn_provider=thnn_provider,
        portal_courses_fetcher=lambda: portal_client.fetch_current_semester_courses(
            "uthelper_demo", pw
        ),
    )
    activity_service = CourseActivityService(
        moodle_service=moodle_service,
        courses_client=courses_client,
        thnn_client=thnn_client,
    )

    summaries = catalog_service.refresh_catalog(include_thnn=True)
    assert len(summaries) == 4
    assert {c.source for c in summaries} == {"courses", "thnn"}
    assert all(c.is_current_term and c.credits > 0 for c in summaries)

    # Sync sections and grades for Courses 101 and THNN 201
    sync_101 = catalog_service.sync_course_delta("courses", 101, force=True)
    assert sync_101.error == ""
    assert len(sync_101.sections) >= 2
    grades_101 = catalog_service.load_course_grades("courses", 101, force=True)
    assert len(grades_101) >= 3

    sync_201 = catalog_service.sync_course_delta("thnn", 201, force=True)
    assert sync_201.error == ""
    assert len(sync_201.sections) >= 2
    grades_201 = catalog_service.load_course_grades("thnn", 201, force=True)
    assert len(grades_201) >= 3

    # Verify PDF and ZIP downloads + caching via real MoodleClient streams
    mods_101 = [m for s in sync_101.sections for m in s.modules]
    atts_101 = [att for m in mods_101 for att in m.attachments]
    pdf_att = next(att for att in atts_101 if att.preview_kind == "pdf")
    zip_att = next(att for att in atts_101 if att.is_archive)

    pdf_path = catalog_service.fetch_or_cache_preview_file(
        "courses", pdf_att.file_url, pdf_att.time_modified, pdf_att.file_name
    )
    assert pdf_path is not None and pdf_path.is_file()
    pdf_reader = pypdf.PdfReader(io.BytesIO(pdf_path.read_bytes()))
    assert len(pdf_reader.pages) >= 2

    zip_bytes = catalog_service.fetch_attachment_bytes("courses", zip_att.file_url)
    assert zipfile.is_zipfile(io.BytesIO(zip_bytes))
    tree = ArchiveService().inspect_bytes(zip_att.file_name, zip_bytes)
    assert tree.supported is True and tree.total_files >= 3

    # Also verify THNN session PDF attachment resolution and download
    mods_201 = [m for s in sync_201.sections for m in s.modules]
    thnn_res_mod = next(m for m in mods_201 if m.modname == "resource")
    thnn_atts = thnn_provider.resolve_module_attachments(thnn_res_mod)
    assert len(thnn_atts) >= 1
    thnn_pdf_path = catalog_service.fetch_or_cache_preview_file(
        "thnn",
        thnn_atts[0].file_url,
        thnn_atts[0].time_modified,
        thnn_atts[0].file_name,
    )
    assert thnn_pdf_path is not None and thnn_pdf_path.read_bytes().startswith(b"%PDF-")
    thnn_pdf_bytes = thnn_client.download_session_file(thnn_atts[0].file_url)
    assert thnn_pdf_bytes is not None and thnn_pdf_bytes.startswith(b"%PDF-")

    # Verify CourseActivityService quiz + attendance offline and write guards
    quiz_mod = next(m for m in mods_101 if m.modname == "quiz")
    quiz_summary = activity_service.fetch_quiz_activity("courses", quiz_mod, course_id=101)
    assert len(quiz_summary.attempts) >= 1
    att_mod = next(m for m in mods_101 if m.modname == "attendance")
    att_summary = activity_service.fetch_attendance("courses", att_mod)
    assert len(att_summary.records) >= 4
    assert courses_provider.set_manual_completion(101, True) is False
    att_ok, _, _ = activity_service.submit_attendance(
        "courses", "https://courses.ut.edu.vn/mod/attendance/attendance.php", {"status": "1"}
    )
    assert att_ok is False


def test_portal_and_academic_grade_service_offline_snapshots_and_wrong_password(
    demo_env, tmp_path
):
    from core.portal_client import PortalAuthError

    pw = demo_env
    portal = PortalClient(settings.PORTAL_API_BASE)
    target = date(2027, 10, 14)

    day_sessions = portal.fetch_day(target, "  UTHelper_Demo ", pw)
    assert isinstance(day_sessions, tuple)
    assert len(day_sessions) >= 2

    week_map = portal.fetch_week(target, "uthelper_demo", pw)
    assert isinstance(week_map, dict)
    assert target in week_map
    assert week_map[target] == day_sessions

    grade_service = AcademicGradeService(
        portal_client=portal, cache_dir=tmp_path / "grades"
    )
    semesters = grade_service.load_academic_data("uthelper_demo", pw)
    assert len(semesters) == 3
    profiles = grade_service.get_radar_profiles(semesters)
    assert len(profiles) == 3

    progress = grade_service.load_credit_progress("uthelper_demo", pw)
    assert progress == CreditProgress(required_credits=132.0, earned_credits=78.0)

    # Wrong password must raise PortalAuthError everywhere (never masked by cache)
    with pytest.raises(PortalAuthError):
        portal.fetch_day(target, "uthelper_demo", "wrong-pw")
    with pytest.raises(PortalAuthError):
        portal.fetch_week(target, "uthelper_demo", "wrong-pw")
    with pytest.raises(PortalAuthError):
        portal.fetch_current_semester_courses("uthelper_demo", "wrong-pw")
    with pytest.raises(PortalAuthError):
        grade_service.load_academic_data("uthelper_demo", "wrong-pw")
    with pytest.raises(PortalAuthError):
        grade_service.load_credit_progress("uthelper_demo", "wrong-pw")


def test_submission_workflow_loads_snapshot_and_blocks_mutations_read_only(demo_env):
    settings.MOODLE_WS_TOKEN = ""
    settings.MOODLE_WS_TOKEN_ORIGIN = ""

    client = MoodleClient()
    workflow = SubmissionWorkflow(client)

    submitted_target = SubmissionTarget(
        "https://courses.ut.edu.vn/mod/assign/view.php?id=104", 103
    )
    snap_res = workflow.load_snapshot(submitted_target)
    assert snap_res.ok is True
    assert snap_res.snapshot is not None
    assert snap_res.snapshot.raw_status == "submitted"
    assert snap_res.snapshot.remote_files[0].name == "Bao_cao_nhom_01.pdf"

    open_target = SubmissionTarget(
        "https://courses.ut.edu.vn/mod/assign/view.php?id=101", 101
    )
    open_snap = workflow.load_snapshot(open_target)
    assert open_snap.ok is True
    assert open_snap.snapshot is not None

    mut_res = workflow.mutate_files(
        open_target,
        FileMutationIntent(
            operation=MutationOperation.ADD,
            selected_files=(SelectedFile("bai_lam.pdf", 8, source_path=""),),
        ),
        selected_files=(SelectedSubmissionFile("bai_lam.pdf", b"%PDF-1.4"),),
    )
    assert mut_res.ok is False
    assert mut_res.issue is not None
    assert mut_res.issue.code is SubmissionErrorCode.DEMO_READ_ONLY
    assert mut_res.snapshot is not None
    assert (
        mutation_message(mut_res)
        == "Tài khoản xem thử (Demo) chỉ hỗ trợ xem dữ liệu mẫu, không gửi bài nộp lên máy chủ UTH."
    )

    fin_res = workflow.finalize_submission(
        submitted_target,
        FinalizeSubmissionIntent(accept_statement=True),
    )
    assert fin_res.ok is False
    assert fin_res.issue is not None
    assert fin_res.issue.code is SubmissionErrorCode.DEMO_READ_ONLY
    assert (
        mutation_message(fin_res)
        == "Tài khoản xem thử (Demo) chỉ hỗ trợ xem dữ liệu mẫu, không gửi bài nộp lên máy chủ UTH."
    )


def test_switching_away_from_demo_account_restores_normal_routing(demo_env, monkeypatch):
    client = MoodleClient()
    portal = PortalClient(settings.PORTAL_API_BASE)

    # Confirm demo routing works first
    assert client.get_user_id() == DEMO_USER_ID
    assert len(portal.fetch_day(date(2027, 10, 14), "uthelper_demo", demo_env)) >= 2

    # Switch to a normal UTH student MSSV; outbound calls must now hit transport guard
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "student-secret")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "real-student-token")
    monkeypatch.setattr(
        settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn"
    )

    with pytest.raises(AssertionError, match="Outbound network forbidden in demo mode"):
        client._post("https://courses.ut.edu.vn/login/token.php", {})

    assert client.call_ws_api("core_webservice_get_site_info") is None

    with pytest.raises(AssertionError, match="Outbound network forbidden in demo mode"):
        portal.fetch_day(date(2027, 10, 14), "2251120001", "student-secret")

    workflow = SubmissionWorkflow(client)
    snap_after_switch = workflow.load_snapshot(
        SubmissionTarget("https://courses.ut.edu.vn/mod/assign/view.php?id=101", 101)
    )
    assert snap_after_switch.ok is False
    assert snap_after_switch.issue is not None
    assert snap_after_switch.issue.code is not SubmissionErrorCode.DEMO_READ_ONLY


@pytest.mark.parametrize("bad_password", ["wrong-password", ""])
def test_demo_account_with_invalid_or_empty_password_fails_closed_offline(
    demo_env, monkeypatch, tmp_path, bad_password
):
    monkeypatch.setattr(settings, "UTH_USERNAME", "uthelper_demo")
    monkeypatch.setattr(settings, "UTH_PASSWORD", bad_password)
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "")

    client = MoodleClient()
    thnn_client = MoodleClient("https://thnn.ut.edu.vn")

    assert client.has_site_credentials is False
    assert thnn_client.has_site_credentials is False
    assert client.call_ws_api("core_webservice_get_site_info") is None
    assert thnn_client.call_ajax_api("core_courseformat_get_state", courseid=201) is None
    assert client.get_user_id() is None
    assert client.fetch_grade_report_html(101) is None
    assert client.last_grade_report_error == "login_session:invalid_credentials"

    with pytest.raises(RuntimeError, match="Moodle session login failed"):
        client.open_session_url("/mod/attendance/view.php?id=106")
    with pytest.raises(RuntimeError, match="Moodle session login failed"):
        client.open_session_raw("/mod/attendance/view.php?id=106")

    assert (
        client.download_file(
            "https://courses.ut.edu.vn/webservice/pluginfile.php/990101/mod_resource/content/1/Bai_giang_Chuong_01_Socket_TCP_UDP.pdf"
        )
        is None
    )
    assert (
        thnn_client.download_session_file(
            "https://thnn.ut.edu.vn/pluginfile.php/992001/mod_resource/content/1/IT_English_Unit_03_Cloud_Architecture.pdf"
        )
        is None
    )

    opener_resp = thnn_client._ensure_html_opener().open("/calendar/export.php")
    assert opener_resp.geturl() == "https://thnn.ut.edu.vn/login/index.php"

    orch = DataOrchestrator()
    outcome = asyncio.run(orch.fetch_activity_outcome_async())
    assert outcome.success is False
    assert outcome.activities == []

    sample_card = {
        "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=101",
        "course_id": 101,
        "type": "assignment",
        "details": {},
    }
    unchanged = orch.fetch_full_details(sample_card, force_refresh=True)
    assert not unchanged.get("details", {}).get("description_html")

    moodle_service = MoodleService(client.call_ws_api)
    catalog_service = CourseCatalogService(
        cache_store=CourseCacheStore(
            cache_dir=tmp_path / f"cat_{len(bad_password)}", namespace="uthelper_demo"
        ),
        preview_cache=PreviewFileCache(cache_dir=tmp_path / f"prev_{len(bad_password)}"),
        courses_provider=CoursesWsProvider(service=moodle_service, client=client),
        thnn_provider=ThnnCourseProvider(client=thnn_client),
    )
    assert catalog_service.refresh_catalog(include_thnn=True) == []

    workflow = SubmissionWorkflow(client)
    target = SubmissionTarget("https://courses.ut.edu.vn/mod/assign/view.php?id=101", 101)
    assert workflow.load_snapshot(target).ok is False
    assert (
        workflow.mutate_files(
            target,
            FileMutationIntent(
                operation=MutationOperation.ADD,
                selected_files=(SelectedFile("a.pdf", 4, source_path=""),),
            ),
            selected_files=(SelectedSubmissionFile("a.pdf", b"%PDF"),),
        ).ok
        is False
    )
    assert (
        workflow.finalize_submission(
            target, FinalizeSubmissionIntent(accept_statement=True)
        ).ok
        is False
    )


def test_account_switch_clears_orchestrator_thnn_and_client_caches(
    demo_env, monkeypatch
):
    settings.MOODLE_WS_TOKEN = ""
    settings.MOODLE_WS_TOKEN_ORIGIN = ""

    orch = DataOrchestrator()
    demo_outcome = asyncio.run(orch.fetch_activity_outcome_async())
    assert demo_outcome.success is True
    assert any(a.get("site_identity") == "thnn" for a in demo_outcome.activities)

    demo_assign = next(
        a
        for a in demo_outcome.activities
        if a.get("type") == "assignment" and a.get("site_identity") != "thnn"
    )
    orch.fetch_full_details(demo_assign, force_refresh=True)
    assert orch.get_cached_details_snapshot() != {}
    assert orch._userid_cache == DEMO_USER_ID
    assert len(orch._thnn_activities_cache) >= 2

    # Populate client-level cached identity/portal token and verify clear_session & login reset them
    orch.client._cached_user_id = 777
    orch.client._portal_token = "stale-portal-jwt"
    orch.client.clear_session()
    assert orch.client._cached_user_id is None
    assert orch.client._portal_token == ""

    orch.client._cached_user_id = 888
    orch.client._portal_token = "stale-portal-jwt-2"
    assert orch.client.login("uthelper_demo", demo_env, force=True) is True
    assert orch.client._cached_user_id is None
    assert orch.client._portal_token == ""

    # Switch to real student MSSV within the same 6h window; THNN login fails offline
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "student-secret")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "student-ws-token")
    monkeypatch.setattr(
        settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn"
    )

    def _student_post(_url: str, data: dict, **_kwargs):
        fn = data.get("wsfunction", "")
        if fn == "core_webservice_get_site_info":
            return 200, {"userid": 42, "fullname": "Real Student"}
        if fn == "core_calendar_get_action_events_by_timesort":
            return 200, {"events": [], "moreevents": False}
        if fn == "core_enrol_get_users_courses":
            return 200, [{"id": 301, "fullname": "Môn thật", "shortname": "REAL301"}]
        if fn == "mod_assign_get_assignments":
            return 200, {"courses": [], "warnings": []}
        if fn == "core_calendar_get_calendar_events":
            return 200, {"events": [], "warnings": []}
        return 200, {}

    monkeypatch.setattr(orch.client, "_post", _student_post)
    monkeypatch.setattr(orch.thnn_client, "login_session", lambda *_a, **_k: False)

    switched_outcome = asyncio.run(orch.fetch_activity_outcome_async())
    assert switched_outcome.success is True
    assert switched_outcome.activities == []
    assert orch._userid_cache == 42
    assert orch._thnn_activities_cache == []
    assert orch.get_cached_details_snapshot() == {}


def test_academic_grade_service_empty_string_fallback_vs_explicit_empty_demo_password(
    demo_env, tmp_path
):
    from core.portal_client import PortalAuthError

    portal = PortalClient(settings.PORTAL_API_BASE)
    grade_service = AcademicGradeService(
        portal_client=portal, cache_dir=tmp_path / "grades_fallback"
    )

    # Default `""` arguments must fall back to `settings.UTH_USERNAME` / `settings.UTH_PASSWORD`
    assert len(grade_service.load_academic_data()) == 3
    assert len(grade_service.load_academic_data("", "")) == 3
    assert grade_service.load_credit_progress() == CreditProgress(
        required_credits=132.0, earned_credits=78.0
    )
    assert grade_service.load_credit_progress("", "") == CreditProgress(
        required_credits=132.0, earned_credits=78.0
    )

    # Explicit demo username with empty password `""` must NOT fall back to `settings.UTH_PASSWORD`
    with pytest.raises(PortalAuthError):
        grade_service.load_academic_data("uthelper_demo", "")
    with pytest.raises(PortalAuthError):
        grade_service.load_credit_progress("uthelper_demo", "")
