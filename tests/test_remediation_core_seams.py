"""Zero-trust adversarial TDD regressions for Slice 2 Core Seams:
- SEAM-01: DataOrchestrator.clear_caches() <-> _sync_account_scope() sync & _course_grades_generation
- SEAM-02 / DEMO-10: MoodleClient.login_session WS token preservation, login(persist=False), _get_ws_token Demo guard
- SEC-C03: MoodleClient.get_portal_token & build_autologin_url https:// + moodle_site_from_origin() guards
- DEMO-09: Shared bounded PBKDF2 demo credential cache in demo_sandbox & PortalClient
- SEC-C05: AcademicGradeService empty-password & PortalAuthenticationError cache isolation vs offline fallback
- D-03: ActivitySyncCoordinator.close() & TodayScheduleCoordinator.close() post-loop-close safety
- DEMO-08: SubmissionWorkflow zero `config` AST import & multi-path Demo read-only enforcement
"""

from __future__ import annotations

import ast
import asyncio
from dataclasses import replace
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from config import settings
from core.academic_radar.gpa_planner import CreditProgress
from core.academic_radar.service import AcademicGradeService
from core.client import DraftFileRecord, MoodleClient
from core.data_orchestrator import DataOrchestrator
import core.demo_sandbox as demo_sandbox
from core.moodle_service import MoodleService
from core.portal_client import (
    PortalAuthenticationError,
    PortalClient,
    PortalError,
)
from core.submission_models import (
    FileMutationIntent,
    MutationOperation,
    RemoteFile,
    SelectedFile,
    SubmissionSnapshot,
)
from core.sync_coordinator import ActivitySyncCoordinator
from core.today_schedule import (
    TodayScheduleCache,
    TodayScheduleCoordinator,
)
from core.use_cases.submission_workflow import (
    FinalizeSubmissionIntent,
    SelectedSubmissionFile,
    SubmissionErrorCode,
    SubmissionTarget,
    SubmissionWorkflow,
)
from core.ws_functions import MoodleActionResult


def _reviewer_password() -> str:
    return "UTH-" + "Review-2026!"


def _forbid_network(*_args, **_kwargs):
    raise AssertionError("Live outbound network is forbidden in this test")


# ---------------------------------------------------------------------------
# 1. SEAM-01: DataOrchestrator clear_caches() <-> _sync_account_scope()
# ---------------------------------------------------------------------------


def test_seam01_clear_caches_updates_active_account_preventing_delayed_session_wipe(
    monkeypatch,
):
    monkeypatch.setattr("core.client._urlopen", _forbid_network)
    monkeypatch.setattr(urllib.request, "build_opener", _forbid_network)

    pw = _reviewer_password()
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "student-a-secret")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "student-a-token")
    monkeypatch.setattr(
        settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn"
    )

    orch = DataOrchestrator()

    # Simulate switching Account A -> Demo Account B via AppController teardown:
    # 1) settings.UTH_USERNAME updated to B
    # 2) orch.clear_caches() called
    # 3) CoursesCoordinator immediately establishes thnn_client session for B
    # 4) Background sync calls fetch_activity_outcome_async() -> _sync_account_scope()
    monkeypatch.setattr(settings, "UTH_USERNAME", "uthelper_demo")
    monkeypatch.setattr(settings, "UTH_PASSWORD", pw)
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "")

    orch.clear_caches()

    assert orch.thnn_client.login_session() is True
    established_sesskey = orch.thnn_client._sesskey
    assert established_sesskey != ""

    orch.is_logged_in = True
    detail_url = "https://courses.ut.edu.vn/mod/assign/view.php?id=101"
    orch._set_cached_detail(detail_url, {"title": "Account B Detail"})

    outcome = asyncio.run(orch.fetch_activity_outcome_async())
    assert outcome.success is True
    assert orch.thnn_client._sesskey == established_sesskey
    assert orch.is_logged_in is True
    assert orch._get_cached_detail(detail_url) == {"title": "Account B Detail"}


def test_seam01_direct_username_mutation_bumps_course_grades_generation_without_deadlock(
    monkeypatch,
):
    monkeypatch.setattr(settings, "UTH_USERNAME", "alice")
    with (
        patch("core.data_orchestrator.MoodleClient"),
        patch("core.data_orchestrator.GradeMonitor"),
    ):
        orch = DataOrchestrator()

    orch.course_grade_monitor = MagicMock()
    orch.course_grade_monitor.update.return_value = [{"changed": True}]
    orch.latest_course_grades = [{"course_id": 1, "name": "Alice Old"}]

    started = threading.Event()
    release = threading.Event()
    refresh_results: list[tuple[list, list]] = []

    def slow_fetch(_orch):
        started.set()
        assert release.wait(2.0)
        return [{"course_id": 10, "name": "Alice Stale Grade"}]

    def run_refresh():
        with patch(
            "core.course_grades_service.fetch_current_courses_with_grades",
            side_effect=slow_fetch,
        ):
            refresh_results.append(orch.refresh_course_grades())

    worker = threading.Thread(target=run_refresh)
    worker.start()
    try:
        assert started.wait(1.0)

        gen_before = orch._course_grades_generation

        # Mutate settings.UTH_USERNAME directly WITHOUT calling clear_caches()
        monkeypatch.setattr(settings, "UTH_USERNAME", "bob")
        orch._sync_account_scope()

        assert orch._course_grades_generation == gen_before + 1
        assert orch.latest_course_grades == []
    finally:
        # Release Alice's slow fetch; generation mismatch must discard Alice's stale grades
        release.set()
        worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert refresh_results == [([], [])]
    assert orch.latest_course_grades == []
    orch.course_grade_monitor.update.assert_not_called()

    # Concurrent stress test: clear_caches() vs _sync_account_scope() must never deadlock
    errors: list[BaseException] = []

    def hammer_clear():
        try:
            for i in range(60):
                monkeypatch.setattr(settings, "UTH_USERNAME", f"user_{i % 3}")
                orch.clear_caches()
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    def hammer_sync():
        try:
            for _ in range(60):
                orch._sync_account_scope()
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    t1 = threading.Thread(target=hammer_clear)
    t2 = threading.Thread(target=hammer_sync)
    t1.start()
    t2.start()
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)
    assert not t1.is_alive()
    assert not t2.is_alive()
    assert errors == []


# ---------------------------------------------------------------------------
# 2. SEAM-02, DEMO-10, SEC-C03: MoodleClient
# ---------------------------------------------------------------------------


def test_seam02_login_session_mismatched_probe_preserves_ws_token_and_case_insensitive_owner(
    monkeypatch,
):
    monkeypatch.setattr("core.client._urlopen", _forbid_network)
    monkeypatch.setattr(urllib.request, "build_opener", _forbid_network)

    pw = _reviewer_password()
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "StudentA")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "student-a-pw")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "live-student-a-token")
    monkeypatch.setattr(
        settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn"
    )

    # 1. Explicit mismatched probe for `uthelper_demo` while `StudentA` is active
    # must NOT wipe StudentA's global MOODLE_WS_TOKEN.
    probe = MoodleClient("https://thnn.ut.edu.vn")
    assert probe.login_session("uthelper_demo", pw) is True
    assert settings.MOODLE_WS_TOKEN == "live-student-a-token"
    assert settings.MOODLE_WS_TOKEN_ORIGIN == "https://courses.ut.edu.vn"

    # 2. Case-insensitive _session_owner check for real accounts (`StudentA` vs `studenta`)
    real_client = MoodleClient("https://courses.ut.edu.vn")
    real_client._sesskey = "sesskey-student-a"
    real_client._session_owner = "StudentA"
    real_client._reset_session_if_account_changed("studenta")
    assert real_client._sesskey == "sesskey-student-a"
    assert real_client._session_owner == "StudentA"

    # 3. When active `settings.UTH_USERNAME` IS the demo account (even mixed-case),
    # login_session() with omitted args clears stale global MOODLE_WS_TOKEN, while explicit
    # empty `""` credentials fail closed offline without substituting stored credentials.
    monkeypatch.setattr(settings, "UTH_USERNAME", "  UTHelper_Demo ")
    monkeypatch.setattr(settings, "UTH_PASSWORD", pw)
    assert probe.login_session("uthelper_demo", pw, force=True) is True
    assert settings.MOODLE_WS_TOKEN == ""
    assert settings.MOODLE_WS_TOKEN_ORIGIN == ""
    assert probe.login_session(force=True) is True
    assert probe.login_session("", "", force=True) is False
    assert probe.last_login_error == "invalid_credentials"
    assert probe.login_session("", "wrong-pw", force=True) is False


def test_demo10_login_persist_false_and_get_ws_token_guard_never_hit_network_or_mutate_settings(
    monkeypatch,
):
    monkeypatch.setattr("core.client._urlopen", _forbid_network)
    pw = _reviewer_password()

    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "real-student-secret")
    monkeypatch.setattr(
        settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn"
    )
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "real-student-ws-token")
    monkeypatch.setattr(
        settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn"
    )

    client = MoodleClient("https://courses.ut.edu.vn")
    monkeypatch.setattr(
        client,
        "_post",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("login/token.php must never be called for demo")
        ),
    )
    client._portal_token = "jwt-2251120001"
    client._portal_token_username = "2251120001"
    client._cached_user_id = 505
    client._cached_user_id_username = "2251120001"

    # 1. persist=False probe in Demo Mode does not mutate global settings or client caches
    assert client.login("  UTHelper_Demo ", pw, force=True, persist=False) is True
    assert settings.UTH_USERNAME == "2251120001"
    assert settings.UTH_PASSWORD == "real-student-secret"
    assert settings.MOODLE_WS_TOKEN == "real-student-ws-token"
    assert settings.MOODLE_WS_TOKEN_ORIGIN == "https://courses.ut.edu.vn"
    assert client._portal_token == "jwt-2251120001"
    assert client._cached_user_id == 505

    assert (
        client.login("uthelper_demo", "wrong-pw", force=True, persist=False) is False
    )
    assert client.last_login_error == "invalid_credentials"

    # 2. Direct _get_ws_token with explicit demo credentials (persist=False) never calls _post
    assert client._get_ws_token("uthelper_demo", pw, force=True, persist=False) == ""
    assert settings.MOODLE_WS_TOKEN == "real-student-ws-token"

    # 3. Direct _get_ws_token when active account is uthelper_demo never reuses stale token or calls _post
    monkeypatch.setattr(settings, "UTH_USERNAME", "uthelper_demo")
    monkeypatch.setattr(settings, "UTH_PASSWORD", pw)
    assert client._get_ws_token(force=False, persist=True) == ""
    assert client._get_ws_token(force=True, persist=True) == ""
    assert settings.MOODLE_WS_TOKEN == ""
    assert settings.MOODLE_WS_TOKEN_ORIGIN == ""


def test_sec_c03_get_portal_token_and_build_autologin_url_enforce_https_and_trusted_origins(
    monkeypatch,
):
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(
        settings, "PORTAL_API_BASE", "https://portal-publicapp.ut.edu.vn/api/v1"
    )
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "student-pw")

    client = MoodleClient("https://courses.ut.edu.vn")
    post_calls: list[str] = []

    def fake_post_json(url: str, _data: dict, timeout: float = 15):
        del timeout
        post_calls.append(url)
        return 200, {"success": True, "token": "jwt-portal-ok"}

    monkeypatch.setattr(client, "_post_json", fake_post_json)

    # 1. Reject http://, userinfo @, ports, query/fragment, and non-UTH Portal origins
    for bad_portal_base in (
        "http://portal-publicapp.ut.edu.vn/api/v1",
        "https://user:pass@portal-publicapp.ut.edu.vn/api/v1",
        "https://user@portal-publicapp.ut.edu.vn/api/v1",
        "https://evil.example.com/api/v1",
        "https://ut.edu.vn.evil.com/api/v1",
        "https://portal-publicapp.ut.edu.vn:8443/api/v1",
        "https://portal-publicapp.ut.edu.vn/api/v1?x=1",
    ):
        monkeypatch.setattr(settings, "PORTAL_API_BASE", bad_portal_base)
        assert client.get_portal_token() == ""
    assert post_calls == []

    # Untrusted MoodleClient origin must also refuse get_portal_token
    monkeypatch.setattr(
        settings, "PORTAL_API_BASE", "https://portal-publicapp.ut.edu.vn/api/v1"
    )
    untrusted_client = MoodleClient("https://evil.example.com")
    monkeypatch.setattr(untrusted_client, "_post_json", fake_post_json)
    assert untrusted_client.get_portal_token() == ""
    assert post_calls == []

    # 2. Reject untrusted MOODLE_BASE_URL in build_autologin_url BEFORE fetching portal token
    for bad_moodle_base in (
        "http://courses.ut.edu.vn",
        "https://attacker@courses.ut.edu.vn",
        "https://evil.example.com",
        "https://courses.ut.edu.vn:8443",
    ):
        monkeypatch.setattr(settings, "MOODLE_BASE_URL", bad_moodle_base)
        assert (
            client.build_autologin_url(
                "https://courses.ut.edu.vn/mod/assign/view.php?id=101"
            )
            == ""
        )
        assert client.build_autologin_url() == ""
    assert post_calls == []

    # 3. Reject http://, userinfo @, and non-UTH activity_url in build_autologin_url
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    for bad_activity_url in (
        "http://courses.ut.edu.vn/mod/assign/view.php?id=101",
        "https://attacker@courses.ut.edu.vn/mod/assign/view.php?id=101",
        "https://evil.example.com/mod/assign/view.php?id=101",
        "https://thnn.ut.edu.vn/mod/assign/view.php?id=101",
        "javascript:alert(1)",
    ):
        assert client.build_autologin_url(bad_activity_url) == ""
    assert post_calls == []

    # 4. Valid HTTPS UTH Moodle origin + activity_url succeeds cleanly
    valid_target = "https://courses.ut.edu.vn/mod/assign/view.php?id=101"
    autologin = client.build_autologin_url(valid_target)
    assert autologin == (
        "https://courses.ut.edu.vn/login/index.php"
        "?token=jwt-portal-ok"
        "&wantsurl=https%3A%2F%2Fcourses.ut.edu.vn%2Fmod%2Fassign%2Fview.php%3Fid%3D101"
    )
    assert post_calls == ["https://portal-publicapp.ut.edu.vn/api/v1/user/login"]


# ---------------------------------------------------------------------------
# 3. DEMO-09: Shared bounded PBKDF2 cache in demo_sandbox & PortalClient
# ---------------------------------------------------------------------------


def test_demo09_portal_client_shares_bounded_pbkdf2_demo_verifier_cache(monkeypatch):
    assert hasattr(demo_sandbox, "verify_demo_credentials_cached")

    real_pbkdf2 = hashlib.pbkdf2_hmac
    pbkdf2_calls = 0

    def counting_pbkdf2(*args, **kwargs):
        nonlocal pbkdf2_calls
        pbkdf2_calls += 1
        return real_pbkdf2(*args, **kwargs)

    monkeypatch.setattr(demo_sandbox.hashlib, "pbkdf2_hmac", counting_pbkdf2)

    cache_obj = getattr(demo_sandbox, "_DEMO_CRED_CACHE", None)
    assert cache_obj is not None
    cache_obj.clear()

    pw = _reviewer_password()
    portal = PortalClient("https://portal-publicapp.ut.edu.vn/api/v1")
    target_day = date(2027, 10, 14)

    assert len(portal.fetch_day(target_day, "  UTHelper_Demo ", pw)) >= 2
    assert target_day in portal.fetch_week(target_day, "uthelper_demo", pw)
    assert portal.fetch_credit_progress("uthelper_demo", pw) is not None
    assert len(portal.fetch_full_transcript("uthelper_demo", pw)) == 3
    assert len(portal.fetch_current_semester_courses("uthelper_demo", pw)[1]) >= 1

    # MoodleClient shares the exact same cache — zero additional PBKDF2 rounds
    moodle = MoodleClient("https://courses.ut.edu.vn")
    assert moodle.login("uthelper_demo", pw, persist=False) is True
    assert pbkdf2_calls == 1

    # Flood with 64 distinct wrong passwords: cache size must stay strictly bounded
    # and never retain plaintext password strings.
    for idx in range(64):
        wrong = f"wrong-demo-attempt-{idx}"
        with pytest.raises(PortalAuthenticationError):
            portal.fetch_day(target_day, "uthelper_demo", wrong)

    max_entries = int(getattr(demo_sandbox, "_DEMO_CRED_CACHE_MAX", 16))
    assert 1 <= len(cache_obj) <= max_entries
    for (cached_user, cached_digest), cached_val in list(cache_obj.items()):
        assert cached_user == "uthelper_demo"
        assert isinstance(cached_digest, bytes) and len(cached_digest) == 32
        assert isinstance(cached_val, bool)


# ---------------------------------------------------------------------------
# 4. SEC-C05: AcademicGradeService empty-password & PortalAuthenticationError
# ---------------------------------------------------------------------------


def test_sec_c05_academic_grade_service_blocks_empty_password_and_auth_error_but_keeps_offline_fallback(
    tmp_path, monkeypatch
):
    student = "2251120099"
    monkeypatch.setattr(
        settings, "PORTAL_API_BASE", "https://portal-publicapp.ut.edu.vn/api/v1"
    )
    monkeypatch.setattr(settings, "UTH_USERNAME", student)
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")

    mock_portal = MagicMock()
    service = AcademicGradeService(portal_client=mock_portal, cache_dir=tmp_path)
    grades_cache_file, progress_cache_file = service._cache_paths(student)

    today_str = date.today().isoformat()
    sample_transcript = [
        {
            "id": 75,
            "tenDot": "HK2 2025-2026",
            "tongKetDot": {"diemTBHocLuc": 8.5},
            "diems": [],
        }
    ]
    grades_cache_file.write_text(
        json.dumps(
            {
                "cached_date": today_str,
                "updated_at": f"{today_str}T08:00:00",
                "transcript": sample_transcript,
            }
        ),
        encoding="utf-8",
    )
    progress_cache_file.write_text(
        json.dumps(
            {
                "cached_date": today_str,
                "body": {"canDat": 132, "hienTai": 90},
            }
        ),
        encoding="utf-8",
    )

    # 1. Empty password (explicit or resolved from settings) MUST NOT return cached data
    assert service.load_academic_data(student, "") == []
    assert service.load_credit_progress(student, "") is None
    assert service.load_academic_data() == []
    assert service.load_credit_progress() is None
    mock_portal.fetch_full_transcript.assert_not_called()
    mock_portal.fetch_credit_progress.assert_not_called()

    # 2. Stale cache + PortalAuthenticationError MUST NOT fall back to stale cache
    grades_cache_file.write_text(
        json.dumps(
            {
                "cached_date": "2026-01-01",
                "updated_at": "2026-01-01T08:00:00",
                "transcript": sample_transcript,
            }
        ),
        encoding="utf-8",
    )
    progress_cache_file.write_text(
        json.dumps(
            {
                "cached_date": "2026-01-01",
                "body": {"canDat": 132, "hienTai": 90},
            }
        ),
        encoding="utf-8",
    )

    mock_portal.fetch_full_transcript.side_effect = PortalAuthenticationError(
        "Portal HTTP 401", status=401
    )
    mock_portal.fetch_credit_progress.side_effect = PortalAuthenticationError(
        "Portal HTTP 401", status=401
    )

    with pytest.raises(PortalAuthenticationError):
        service.load_academic_data(student, "revoked-password")
    with pytest.raises(PortalAuthenticationError):
        service.load_credit_progress(student, "revoked-password")
    assert mock_portal.clear_token.call_count >= 2

    # 3. Transient network error (`PortalError` wrapping `URLError`) STILL falls back to stale cache
    net_err = PortalError("Portal network request failed")
    net_err.__cause__ = urllib.error.URLError("timed out")
    mock_portal.fetch_full_transcript.side_effect = net_err
    mock_portal.fetch_credit_progress.side_effect = net_err

    offline_semesters = service.load_academic_data(student, "valid-password")
    assert len(offline_semesters) == 1
    assert offline_semesters[0].name == "HK2 2025-2026"

    offline_progress = service.load_credit_progress(student, "valid-password")
    assert offline_progress == CreditProgress(
        required_credits=132.0, earned_credits=90.0
    )


# ---------------------------------------------------------------------------
# 5. D-03: Post-loop-close safety in SyncCoordinator & TodayScheduleCoordinator
# ---------------------------------------------------------------------------


def test_d03_coordinators_close_without_runtime_error_after_event_loop_is_closed(
    tmp_path,
):
    loop = asyncio.new_event_loop()
    sync_entered = False

    async def slow_sync(_trigger: str):
        nonlocal sync_entered
        sync_entered = True
        await asyncio.Event().wait()

    sync_coord = ActivitySyncCoordinator(
        slow_sync,
        interval_provider=lambda: 60,
        last_success_provider=lambda: time.time(),
    )
    sched_cache = TodayScheduleCache(cache_dir=tmp_path, namespace="portal|student")
    sched_coord = TodayScheduleCoordinator(
        fetch_day=lambda *_: (),
        cache=sched_cache,
        credentials_provider=lambda: ("student", "secret"),
        state_sink=lambda _s: None,
        now_provider=lambda: datetime(2026, 8, 11, 8, 0),
    )

    async def _arm_tasks():
        t_sync_run = asyncio.create_task(sync_coord.run())
        t_sync_req = asyncio.create_task(sync_coord.request("manual", force=True))
        t_sched_run = asyncio.create_task(sched_coord.run())
        await asyncio.sleep(0.05)
        return t_sync_run, t_sync_req, t_sched_run

    armed_tasks = loop.run_until_complete(_arm_tasks())
    assert sync_entered is True
    in_flight_task = sync_coord._in_flight
    assert in_flight_task is not None and not in_flight_task.done()

    # Close the event loop directly (simulating Flet tearing down loop before _on_disconnect)
    loop.close()
    assert loop.is_closed() is True

    # Neither close(), wake(), nor replace_cache() may raise RuntimeError("Event loop is closed")
    sync_coord.close()
    sync_coord.wake()
    sched_coord.close()
    sched_coord.replace_cache(
        TodayScheduleCache(cache_dir=tmp_path, namespace="portal|other")
    )

    assert sync_coord._closed is True
    assert sync_coord._in_flight is None
    assert sched_coord._closed is True

    # Silence GC warnings for tasks intentionally stranded on the closed loop
    for stranded in (*armed_tasks, in_flight_task):
        stranded._log_destroy_pending = False


# ---------------------------------------------------------------------------
# 6. DEMO-08: SubmissionWorkflow zero `config` import & multi-path Demo guard
# ---------------------------------------------------------------------------


def _workflow_ast_imports() -> set[str]:
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "core"
        / "use_cases"
        / "submission_workflow.py"
    )
    tree = ast.parse(workflow_path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _minimal_editable_snapshot() -> SubmissionSnapshot:
    return SubmissionSnapshot(
        assignment_id=77,
        raw_status="draft",
        can_edit=True,
        can_submit=True,
        locked=False,
        graded=False,
        submissions_enabled=True,
        submission_drafts=True,
        statement_required=False,
        file_submission_enabled=True,
        team_submission=False,
        due_date=0,
        cutoff_date=0,
        allows_submissions_from_date=0,
        maximum_file_count=5,
        maximum_file_bytes=1_048_576,
        accepted_file_types=(".pdf",),
        remote_files=(
            RemoteFile(
                name="old.pdf",
                filepath="/",
                size=3,
                mimetype="application/pdf",
                modified_time=1_700_000_001,
                url="https://files/old.pdf",
            ),
        ),
        online_text="",
        online_text_format=1,
        attempt_number=0,
        submission_id=333,
        submission_modified_time=1_700_000_000,
    )


class _StandaloneFakeClient:
    def __init__(self, moodle_site_origin: str = "https://courses.ut.edu.vn"):
        self.moodle_site_origin = moodle_site_origin
        self.uploads: list = []

    def download_file(self, _url: str) -> bytes:
        return b"old"

    def upload_draft_file_record(
        self, name: str, _content: bytes, itemid: int, filepath: str
    ):
        self.uploads.append((name, itemid, filepath))
        return DraftFileRecord(itemid=itemid, filepath=filepath, filename=name)

    def call_ws_api(self, *_a, **_k):
        raise AssertionError("Fake service handles WS calls")


class _StandaloneFakeService:
    def __init__(self, snapshot: SubmissionSnapshot, client: object):
        self.current = snapshot
        self.client = client

    def resolve_cmid_to_assign_id(self, cmid: int, course_id: int):
        return 77 if (cmid, course_id) == (123, 456) else None

    def get_assignments(self, _course_ids: list[int]):
        s = self.current
        return {
            "courses": [
                {
                    "id": 456,
                    "assignments": [
                        {
                            "id": s.assignment_id,
                            "nosubmissions": not s.submissions_enabled,
                            "submissiondrafts": int(s.submission_drafts),
                            "teamsubmission": int(s.team_submission),
                            "duedate": 0,
                            "cutoffdate": 0,
                            "allowsubmissionsfromdate": 0,
                            "configs": [
                                {
                                    "subtype": "assign",
                                    "plugin": "assign",
                                    "name": "submissiondrafts",
                                    "value": int(s.submission_drafts),
                                },
                                {
                                    "subtype": "assignsubmission",
                                    "plugin": "file",
                                    "name": "maxfilesubmission",
                                    "value": s.maximum_file_count,
                                },
                                {
                                    "subtype": "assignsubmission",
                                    "plugin": "file",
                                    "name": "maxsubmissionsizebytes",
                                    "value": s.maximum_file_bytes,
                                },
                                {
                                    "subtype": "assignsubmission",
                                    "plugin": "file",
                                    "name": "acceptedfiletypes",
                                    "value": ".pdf",
                                },
                                {
                                    "subtype": "assignsubmission",
                                    "plugin": "file",
                                    "name": "enabled",
                                    "value": 1,
                                },
                            ],
                        }
                    ],
                }
            ]
        }

    def get_submission_status(self, _assign_id: int):
        s = self.current
        return {
            "lastattempt": {
                "submission": {
                    "id": s.submission_id,
                    "status": s.raw_status,
                    "timemodified": s.submission_modified_time,
                    "plugins": [
                        {
                            "type": "file",
                            "fileareas": [
                                {
                                    "files": [
                                        {
                                            "filename": f.name,
                                            "filepath": f.filepath,
                                            "filesize": f.size,
                                            "mimetype": f.mimetype,
                                            "timemodified": f.modified_time,
                                            "fileurl": f.url,
                                        }
                                        for f in s.remote_files
                                    ]
                                }
                            ],
                        }
                    ],
                },
                "attemptnumber": s.attempt_number,
                "canedit": s.can_edit,
                "cansubmit": s.can_submit,
                "locked": s.locked,
                "graded": s.graded,
                "submissionsenabled": s.submissions_enabled,
            }
        }

    def get_unused_draft_itemid(self):
        return 901

    def save_assignment_submission_result(self, *_a, **_k):
        self.current = replace(
            self.current,
            remote_files=(
                RemoteFile(
                    name="new.pdf",
                    filepath="/",
                    size=8,
                    mimetype="application/pdf",
                    modified_time=1_700_000_002,
                    url="https://files/new.pdf",
                ),
            ),
        )
        return MoodleActionResult(ok=True)


def test_demo08_submission_workflow_eliminates_config_import_and_enforces_demo_guard_everywhere(
    monkeypatch,
):
    # 1. Zero AST `config` import in src/core/use_cases/submission_workflow.py
    imports = _workflow_ast_imports()
    assert not any(
        imp == "config" or imp.startswith("config.") for imp in imports
    ), f"Unexpected config import in submission_workflow.py: {imports}"

    target = SubmissionTarget(
        "https://courses.ut.edu.vn/mod/assign/view.php?id=123", 456
    )
    add_intent = FileMutationIntent(
        operation=MutationOperation.REPLACE,
        selected_files=(SelectedFile("new.pdf", 8, source_path=""),),
    )
    picked = (SelectedSubmissionFile("new.pdf", b"%PDF-1.4"),)

    # 2. Injected `username_getter` blocks Demo Mode even when global settings has a real student
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    fake_client = _StandaloneFakeClient()
    fake_service = _StandaloneFakeService(_minimal_editable_snapshot(), fake_client)
    wf_injected = SubmissionWorkflow(
        fake_client,
        fake_service,  # type: ignore[arg-type]
        username_getter=lambda: "  UTHelper_Demo ",
    )
    assert (
        wf_injected.mutate_files(target, add_intent, selected_files=picked).issue.code
        is SubmissionErrorCode.DEMO_READ_ONLY
    )
    assert (
        wf_injected.finalize_submission(
            target, FinalizeSubmissionIntent(accept_statement=True)
        ).issue.code
        is SubmissionErrorCode.DEMO_READ_ONLY
    )
    assert fake_client.uploads == []

    # 3. Client `_username` attribute on fake client/service blocks Demo Mode without `username_getter`
    client_with_attr = _StandaloneFakeClient()
    client_with_attr._username = "uthelper_demo"  # type: ignore[attr-defined]
    service_with_attr = _StandaloneFakeService(
        _minimal_editable_snapshot(), client_with_attr
    )
    wf_attr = SubmissionWorkflow(client_with_attr, service_with_attr)  # type: ignore[arg-type]
    assert (
        wf_attr.mutate_files(target, add_intent, selected_files=picked).issue.code
        is SubmissionErrorCode.DEMO_READ_ONLY
    )

    # 4. Standalone DetailView / unit test without AppController (both real MoodleClient+MoodleService
    # and plain FakeClient+FakeService) still fails closed when Demo Mode is active!
    monkeypatch.setattr("core.client._urlopen", _forbid_network)
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "uthelper_demo")
    monkeypatch.setattr(settings, "UTH_PASSWORD", _reviewer_password())

    real_client = MoodleClient("https://courses.ut.edu.vn")
    standalone_wf = SubmissionWorkflow(
        real_client, MoodleService(real_client.call_ws_api)
    )
    demo_target = SubmissionTarget(
        "https://courses.ut.edu.vn/mod/assign/view.php?id=101", 101
    )
    assert (
        standalone_wf.mutate_files(
            demo_target, add_intent, selected_files=picked
        ).issue.code
        is SubmissionErrorCode.DEMO_READ_ONLY
    )

    plain_fake_client = _StandaloneFakeClient()
    plain_fake_service = _StandaloneFakeService(
        _minimal_editable_snapshot(), plain_fake_client
    )
    wf_plain_fake = SubmissionWorkflow(plain_fake_client, plain_fake_service)  # type: ignore[arg-type]
    assert (
        wf_plain_fake.mutate_files(target, add_intent, selected_files=picked).issue.code
        is SubmissionErrorCode.DEMO_READ_ONLY
    )

    # 5. Explicit non-demo `username_getter` isolates unit tests from global demo state,
    # and MagicMock clients never trigger false-positive `_is_demo_mode()` truthiness.
    wf_isolated = SubmissionWorkflow(
        plain_fake_client,
        plain_fake_service,  # type: ignore[arg-type]
        username_getter=lambda: "2251120001",
    )
    isolated_res = wf_isolated.mutate_files(target, add_intent, selected_files=picked)
    assert isolated_res.ok is True

    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    mock_client = MagicMock()
    mock_client.moodle_site_origin = "https://courses.ut.edu.vn"
    mock_client.download_file.return_value = b"old"
    mock_client.upload_draft_file_record.side_effect = (
        lambda name, _content, itemid, filepath: DraftFileRecord(
            itemid=itemid, filepath=filepath, filename=name
        )
    )
    mock_backed_service = _StandaloneFakeService(
        _minimal_editable_snapshot(), mock_client
    )
    wf_mock_client = SubmissionWorkflow(mock_client, mock_backed_service)  # type: ignore[arg-type]
    assert wf_mock_client.mutate_files(target, add_intent, selected_files=picked).ok is True


# ---------------------------------------------------------------------------
# 7. Round-2 Adversarial Critic Regressions (#37 items 1..5)
# ---------------------------------------------------------------------------


def test_r2_item1_seam01_sync_account_scope_reads_current_key_inside_lock_no_toctou(
    monkeypatch,
):
    monkeypatch.setattr("core.client._urlopen", _forbid_network)
    monkeypatch.setattr(urllib.request, "build_opener", _forbid_network)

    pw = _reviewer_password()
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "alice")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "alice-pw")

    orch = DataOrchestrator()
    thread1_about_to_lock = threading.Event()
    errors: list[BaseException] = []

    def alice_tail_sync():
        try:
            thread1_about_to_lock.set()
            orch._sync_account_scope()
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    # Hold `_account_state_lock` on Thread 2 while `settings.UTH_USERNAME` is still `alice`.
    # Thread 1 starts `_sync_account_scope()` and blocks waiting on `_account_state_lock`.
    # Before releasing the lock, Thread 2 switches to `uthelper_demo`, runs `clear_caches()`,
    # and establishes Account B's THNN session.
    with orch._account_state_lock:
        t1 = threading.Thread(target=alice_tail_sync)
        t1.start()
        assert thread1_about_to_lock.wait(1.0)
        # Give Thread 1 a moment to reach `with lock:`
        time.sleep(0.03)

        monkeypatch.setattr(settings, "UTH_USERNAME", "uthelper_demo")
        monkeypatch.setattr(settings, "UTH_PASSWORD", pw)
        orch.clear_caches()
        assert orch.thnn_client.login_session() is True
        orch.is_logged_in = True

    t1.join(timeout=2.0)
    assert not t1.is_alive()
    assert errors == []
    assert orch._active_account_key == "uthelper_demo"
    assert orch.thnn_client._sesskey == "demoSessKey2026"
    assert orch.is_logged_in is True


def test_r2_item2_seam02_demo08_mismatched_demo_probe_and_stale_session_owner_do_not_poison_submissions(
    monkeypatch,
):
    monkeypatch.setattr("core.client._urlopen", _forbid_network)
    monkeypatch.setattr(urllib.request, "build_opener", _forbid_network)

    pw = _reviewer_password()
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "StudentA")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "student-a-pw")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "student-a-ws-tok")

    target = SubmissionTarget(
        "https://courses.ut.edu.vn/mod/assign/view.php?id=123", 456
    )
    replace_intent = FileMutationIntent(
        operation=MutationOperation.REPLACE,
        selected_files=(SelectedFile("new.pdf", 8, source_path=""),),
    )
    picked = (SelectedSubmissionFile("new.pdf", b"%PDF-1.4"),)

    # Path A: Mismatched demo probe on an active real student's MoodleClient
    client = MoodleClient("https://courses.ut.edu.vn")
    client._sesskey = "real-student-a-sesskey"
    client._session_owner = "StudentA"
    monkeypatch.setattr(
        client,
        "download_file",
        lambda _url: b"old",
    )
    monkeypatch.setattr(
        client,
        "upload_draft_file_record",
        lambda name, _content, itemid, filepath: DraftFileRecord(
            itemid=itemid, filepath=filepath, filename=name
        ),
    )

    assert client.login_session("uthelper_demo", pw) is True
    assert client.login_session("uthelper_demo", "wrong-pw") is False
    assert client._sesskey == "real-student-a-sesskey"
    assert client._session_owner == "StudentA"
    assert client.is_demo_mode is False
    assert settings.MOODLE_WS_TOKEN == "student-a-ws-tok"

    wf_a = SubmissionWorkflow(
        client,
        _StandaloneFakeService(_minimal_editable_snapshot(), client),  # type: ignore[arg-type]
    )
    res_a = wf_a.mutate_files(target, replace_intent, selected_files=picked)
    assert res_a.ok is True

    # Path B: Demo -> Real switch without `clear_session()` leaving stale `_session_owner`
    monkeypatch.setattr(settings, "UTH_USERNAME", "uthelper_demo")
    monkeypatch.setattr(settings, "UTH_PASSWORD", pw)
    assert client.login_session(force=True) is True
    assert client._session_owner == "uthelper_demo"
    assert client.is_demo_mode is True

    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "real-2251-pw")
    assert client.is_demo_mode is False

    wf_b = SubmissionWorkflow(
        client,
        _StandaloneFakeService(_minimal_editable_snapshot(), client),  # type: ignore[arg-type]
    )
    res_b = wf_b.mutate_files(target, replace_intent, selected_files=picked)
    assert res_b.ok is True


def test_r2_item3_seam02_login_session_rejects_explicit_empty_or_unpaired_credentials_offline(
    monkeypatch,
):
    monkeypatch.setattr("core.client._urlopen", _forbid_network)
    monkeypatch.setattr(urllib.request, "build_opener", _forbid_network)

    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "stored-real-secret")

    client = MoodleClient("https://courses.ut.edu.vn")
    for bad_user, bad_pwd in (
        ("", ""),
        ("   ", "stored-real-secret"),
        ("2251120001", ""),
        ("", "stored-real-secret"),
        ("2251120999", None),
    ):
        assert client.login_session(bad_user, bad_pwd, force=True) is False
        assert client.last_login_error == "invalid_credentials"


def test_r2_item4_sec_c03_rejects_trailing_query_fragment_delimiters_and_none_activity_url(
    monkeypatch,
):
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "student-pw")

    client = MoodleClient("https://courses.ut.edu.vn")
    post_calls: list[str] = []
    monkeypatch.setattr(
        client,
        "_post_json",
        lambda url, _d, timeout=15: post_calls.append(url)
        or (200, {"success": True, "token": "jwt-ok"}),
    )

    for tricky_portal_base in (
        "https://portal-publicapp.ut.edu.vn/api/v1#",
        "https://portal-publicapp.ut.edu.vn/api/v1?",
        "https://portal-publicapp.ut.edu.vn/api/v1/?",
        "https://portal-publicapp.ut.edu.vn/api/v1/#",
    ):
        monkeypatch.setattr(settings, "PORTAL_API_BASE", tricky_portal_base)
        assert client.get_portal_token() == ""
    assert post_calls == []

    # When `not token`, `build_autologin_url(None)` must return `""`, never `None`
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")
    monkeypatch.setattr(
        settings, "PORTAL_API_BASE", "https://portal-publicapp.ut.edu.vn/api/v1"
    )
    assert client.build_autologin_url(None) == ""  # type: ignore[arg-type]


def test_r2_item5_sec_c05_resolve_credentials_prevents_cross_account_password_bleed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        settings, "PORTAL_API_BASE", "https://portal-publicapp.ut.edu.vn/api/v1"
    )
    monkeypatch.setattr(settings, "UTH_USERNAME", "studentA")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret_password_of_A")

    mock_portal = MagicMock()
    service = AcademicGradeService(portal_client=mock_portal, cache_dir=tmp_path)

    today_str = date.today().isoformat()
    b_grades_cache, b_progress_cache = service._cache_paths("studentB")
    b_grades_cache.write_text(
        json.dumps(
            {
                "cached_date": today_str,
                "updated_at": f"{today_str}T08:00:00",
                "transcript": [
                    {
                        "id": 88,
                        "tenDot": "HK1 StudentB Private",
                        "tongKetDot": {"diemTBHocLuc": 9.1},
                        "diems": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    b_progress_cache.write_text(
        json.dumps(
            {
                "cached_date": today_str,
                "body": {"canDat": 132, "hienTai": 100},
            }
        ),
        encoding="utf-8",
    )

    # Calling with `username="studentB"` (`password=None`) while `studentA` is active
    # must NOT use `studentA`'s password to unlock `studentB`'s cache or hit Portal!
    assert service.load_academic_data("studentB") == []
    assert service.load_credit_progress("studentB") is None

    b_grades_cache.unlink()
    b_progress_cache.unlink()
    assert service.load_academic_data("studentB") == []
    assert service.load_credit_progress("studentB") is None
    mock_portal.fetch_full_transcript.assert_not_called()
    mock_portal.fetch_credit_progress.assert_not_called()

    # Calling with matching active username (`"StudentA"`, case-insensitive, `password=None`)
    # still resolves `studentA`'s stored password cleanly.
    mock_portal.fetch_full_transcript.return_value = [
        {
            "id": 89,
            "tenDot": "HK1 StudentA",
            "tongKetDot": {"diemTBHocLuc": 8.0},
            "diems": [],
        }
    ]
    a_semesters = service.load_academic_data("StudentA")
    assert len(a_semesters) == 1
    mock_portal.fetch_full_transcript.assert_called_once_with(
        "StudentA", "secret_password_of_A"
    )

