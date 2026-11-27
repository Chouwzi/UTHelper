"""End-to-end simulation & regression suite for submitted-activity & THNN notifications.

Reproduces all 6 confirmed root causes across the Python sync/policy/cache pipeline
and verifies Android/iOS native parity contracts:
1. RC1: Inverted sync order in AppController._perform_data_sync firing notifications
   and scheduling native alarms BEFORE hydrating Courses submission statuses.
2. RC2: Group/team submissions (teamsubmission.status == "submitted"), direct grading
   (gradingstatus == "graded"/"released" or feedback.grade), action_events "new"
   verification, and fail-safe "unknown" on Moodle WS status failure.
3. RC3: Exact policy distinction between Courses "unknown" (unverified -> block) vs
   THNN "unknown" (WS-unverifiable -> accept), plus non-network CourseCacheStore
   completion promotion for known-completed THNN modules.
4. RC4: ActivityNotification.from_value reading site_identity, canonicalizing nested
   status variants, and isolating THNN vs Courses cache keys while preserving
   legacy "activity:<id>" Courses receipt keys.
5. RC5: DataOrchestrator._merge_cached_detail_for_activity preserving cached
   "submitted"/"graded" over stale "new", and immediate schedule cancellation on
   _show_detail_async, _on_quiz_player_closed (Courses & THNN), _prefetch_details_async,
   and _on_activity_status_changed.
6. RC6: Android (Kotlin) & iOS (Swift) parity for THNN key prefixing, background sync
   preservation of THNN activities, team/graded WS parsing, and unknown status policy.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import flet as ft
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from config import settings
from core.courses.cache_store import CourseCacheStore
from core.courses.models import CourseModuleItem, CourseSection, CourseSummary
from core.data_cache import DataCache
from core.data_orchestrator import DataOrchestrator
from core.notification_policy import ActivityNotificationPolicy, NotificationPolicyConfig
from core.notification_types import ActivityNotification
from gui.app_controller import AppController
from gui.controllers.refresh_coordinator import status_hydration_candidates
from notifiers.manager import NotificationManager


REPO_ROOT = Path(__file__).resolve().parents[1]
ANDROID_MAIN = (
    REPO_ROOT
    / "extensions/flet_uth_background_sync/flutter/flet_uth_background_sync"
    / "android/src/main/kotlin/com/uthelper/backgroundsync"
)
IOS_PLUGIN = (
    REPO_ROOT
    / "extensions/flet_uth_background_sync/flutter/flet_uth_background_sync"
    / "ios/Classes/UthBackgroundSyncPlugin.swift"
)


class _RecordingNotifier:
    """Test channel capturing exact notified items and reconciled schedules."""

    channel_name = "recording"

    def __init__(self) -> None:
        self.notified_batches: list[list[dict]] = []
        self.scheduled_batches: list[list] = []

    def notify(self, items: list) -> bool:
        self.notified_batches.append(list(items))
        return True

    async def reconcile_schedules(self, reminders: list):
        from core.notification_types import ScheduleResult

        self.scheduled_batches.append(list(reminders))
        return ScheduleResult(desired=len(reminders), scheduled=len(reminders))


def _make_IsolatedNotificationManager(tmp_path: Path, channel: _RecordingNotifier) -> NotificationManager:
    with patch("notifiers.manager.NotificationManager.__init__", return_value=None):
        mgr = NotificationManager.__new__(NotificationManager)
        mgr.notifiers = [channel]
        mgr._cache_path = str(tmp_path / "notifications_cache_sim.json")
        mgr._cache_lock = threading.RLock()
        mgr._namespace_generation = 0
        mgr._dispatch_lock_state = None
        from core.notification_history import NotificationHistory

        mgr._history = NotificationHistory(history_dir=tmp_path, namespace="sim_student")
        return mgr


def _make_sync_controller(
    tmp_path: Path,
    orchestrator: Any,
    notifier: Any,
    *,
    android_bridge: Any = None,
    course_cache_store: CourseCacheStore | None = None,
) -> AppController:
    ctrl = AppController.__new__(AppController)
    ctrl.page = SimpleNamespace(
        width=1120,
        height=760,
        web=False,
        platform=ft.PagePlatform.WINDOWS,
        overlay=[],
        update=Mock(),
        run_task=lambda fn, *a, **kw: None,
    )
    ctrl._data_lock = threading.Lock()
    ctrl._cards_lock = threading.Lock()
    ctrl._account_epoch = 1
    ctrl._sync_generation = 1
    ctrl._prefetch_epoch = 0
    ctrl._prefetch_cancel_event = threading.Event()
    ctrl._card_detail_warm_cancel_event = threading.Event()
    ctrl._is_loading = False
    ctrl._skeleton_visible = True
    ctrl._pending_updates = {}
    ctrl.all_data = []
    ctrl.active_cards = []
    ctrl._data_cache = DataCache(cache_dir=tmp_path, namespace="courses|2251120999")
    ctrl._course_cache_store = course_cache_store
    ctrl._android_background = android_bridge
    ctrl.notifier = notifier
    ctrl.orchestrator = orchestrator
    ctrl.refresh_btn = SimpleNamespace(disabled=False)
    ctrl.empty_state = SimpleNamespace(visible=False)
    ctrl.error_state = SimpleNamespace(visible=False)
    ctrl.error_text = SimpleNamespace(value="")
    ctrl._diag_breadcrumb = lambda *_a, **_kw: None
    ctrl._set_sync_status = lambda *_a, **_kw: None
    ctrl._update_footer = lambda *_a, **_kw: None
    ctrl._show_snackbar = lambda *_a, **_kw: None
    ctrl._clear_uth_maintenance_banner = lambda **_kw: None
    ctrl._native_background_credentials = lambda: (
        "ws-token-sim",
        "https://courses.ut.edu.vn",
    )
    ctrl._safe_run_task = lambda *_a, **_kw: None
    return ctrl


# ---------------------------------------------------------------------------
# Root Cause 1: Inverted Sync Order in AppController._perform_data_sync
# ---------------------------------------------------------------------------
def test_rc1_perform_data_sync_hydrates_statuses_before_dispatch_and_native_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Already-submitted Courses assignments and finished quizzes re-inserted with
    submission_status='unknown' must be hydrated BEFORE dispatch/import_activities/reconcile.
    """
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120999")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret-pw")
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "ws-token-sim")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "2251120999")
    monkeypatch.setattr(settings, "NOTIFY_IGNORE_SUBMITTED", True)
    monkeypatch.setattr(settings, "NOTIFY_MILESTONES_MINUTES", [180, 30])
    monkeypatch.setattr(settings, "NOTIFY_MILESTONES", [])
    monkeypatch.setattr(settings, "NOTIFY_MINUTES_BEFORE", 0)
    monkeypatch.setattr(settings, "NOTIFY_TYPES", [])
    monkeypatch.setattr(settings, "NOTIFY_MUTED_COURSES", [])
    monkeypatch.setattr(settings, "NOTIFY_DND_ENABLE", False)

    now = datetime.now()
    due_soon_ts = int((now + timedelta(hours=2)).timestamp())
    due_soon_str = datetime.fromtimestamp(due_soon_ts).strftime("%Y-%m-%d %H:%M:%S")

    with patch("core.data_orchestrator.MoodleClient"), patch(
        "core.data_orchestrator.GradeMonitor"
    ):
        orch = DataOrchestrator()

    orch.moodle_service = Mock()
    orch.moodle_service.get_site_info.return_value = {"userid": 42}
    # Only Assignment B (unsubmitted) is in action_events; Assignment A (submitted)
    # and Quiz Q (finished) were already omitted by Moodle action_events!
    action_events_raw = [
        {
            "id": 9002,
            "name": "Assignment B (Chưa nộp)",
            "modulename": "assign",
            "instance": 503,
            "timesort": due_soon_ts,
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=503",
            "course": {"id": 101, "fullname": "Lập trình Mạng"},
        }
    ]
    from core.ws_functions import ws_events_to_assignments

    orch.moodle_service.get_action_events_by_timesort.return_value = {
        "events": action_events_raw,
        "moreevents": False,
    }
    orch.moodle_service.ws_events_to_assignments.side_effect = ws_events_to_assignments
    orch.moodle_service.get_user_courses.return_value = [
        {"id": 101, "fullname": "Lập trình Mạng"}
    ]
    orch.moodle_service.get_assignments.return_value = [
        {
            "id": 101,
            "fullname": "Lập trình Mạng",
            "assignments": [
                {
                    "id": 7001,
                    "cmid": 501,
                    "name": "Assignment A (Đã nộp)",
                    "duedate": due_soon_ts,
                    "cutoffdate": 0,
                },
                {
                    "id": 7002,
                    "cmid": 503,
                    "name": "Assignment B (Chưa nộp)",
                    "duedate": due_soon_ts,
                    "cutoffdate": 0,
                },
            ],
        }
    ]
    orch.moodle_service.get_calendar_events.return_value = [
        {
            "id": 9100,
            "name": "Quiz Q (Đã làm) closes",
            "modulename": "quiz",
            "instance": 8001,
            "courseid": 101,
            "timestart": due_soon_ts,
        }
    ]
    orch.moodle_service.get_quizzes_by_courses.return_value = [
        {
            "id": 8001,
            "coursemodule": 502,
            "course": 101,
            "name": "Quiz Q (Đã làm)",
            "timeclose": due_soon_ts,
        }
    ]

    def _mock_submission_status(assign_id: int) -> dict:
        if assign_id == 7001:
            return {"lastattempt": {"submission": {"status": "submitted"}}}
        if assign_id == 7002:
            return {"lastattempt": {"submission": {"status": "new"}}}
        raise AssertionError(f"Unexpected assign_id: {assign_id}")

    orch.moodle_service.get_submission_status.side_effect = _mock_submission_status
    orch.moodle_service.get_quiz_attempts.return_value = [
        {"id": 1, "attempt": 1, "state": "finished", "sumgrades": 9.5}
    ]
    orch._should_sync_thnn = Mock(return_value=False)
    orch._thnn_activities_cache = [
        {
            "id": "501",
            "title": "THNN Quiz T (Chưa rõ trạng thái)",
            "course_name": "Tiếng Anh 1",
            "course": "Tiếng Anh 1",
            "course_id": 0,
            "deadline": due_soon_str,
            "deadline_str": due_soon_str,
            "url": "https://thnn.ut.edu.vn/mod/quiz/view.php?id=501",
            "type": "quiz",
            "urgency": "critical",
            "source": "thnn_sync",
            "submission_status": "unknown",
            "details": {},
            "is_open": False,
            "site_identity": "thnn",
        }
    ]

    channel = _RecordingNotifier()
    mgr = _make_IsolatedNotificationManager(tmp_path, channel)
    imported_snapshots: list[list[dict]] = []
    fake_bridge = SimpleNamespace(
        available=True,
        configure=AsyncMock(return_value={"enabled": True}),
        import_activities=AsyncMock(
            side_effect=lambda items, authoritative=True: (
                imported_snapshots.append([dict(x) for x in items])
                or {"imported": len(items), "scheduled": 2, "cancelled": 0, "delivered": 0}
            )
        ),
        diagnostics=AsyncMock(return_value={}),
        logout=AsyncMock(),
    )

    # First test desktop path (android_bridge=None -> dispatch + reconcile_schedules)
    ctrl_desktop = _make_sync_controller(tmp_path, orch, mgr, android_bridge=None)
    asyncio.run(ctrl_desktop._perform_data_sync("manual"))

    assert len(channel.notified_batches) == 1
    notified_titles = {item["title"] for item in channel.notified_batches[0]}
    assert notified_titles == {
        "Assignment B (Chưa nộp)",
        "THNN Quiz T (Chưa rõ trạng thái)",
    }

    assert len(channel.scheduled_batches) == 1
    scheduled_titles = {rem.activity.title for rem in channel.scheduled_batches[0]}
    assert scheduled_titles == {
        "Assignment B (Chưa nộp)",
        "THNN Quiz T (Chưa rõ trạng thái)",
    }

    # Now test mobile path (android_bridge=fake_bridge -> import_activities receives hydrated statuses!)
    channel_mobile = _RecordingNotifier()
    mgr_mobile = _make_IsolatedNotificationManager(tmp_path / "mob", channel_mobile)
    os.makedirs(tmp_path / "mob", exist_ok=True)
    ctrl_mobile = _make_sync_controller(
        tmp_path / "mob", orch, mgr_mobile, android_bridge=fake_bridge
    )
    asyncio.run(ctrl_mobile._perform_data_sync("manual"))

    assert len(imported_snapshots) == 1
    by_title = {item["title"]: item["submission_status"] for item in imported_snapshots[0]}
    assert by_title["Assignment A (Đã nộp)"] == "submitted"
    assert by_title["Quiz Q (Đã làm)"] == "submitted"
    assert by_title["Assignment B (Chưa nộp)"] == "not_submitted"
    assert by_title["THNN Quiz T (Chưa rõ trạng thái)"] == "unknown"


# ---------------------------------------------------------------------------
# Root Cause 2: Group/Team Submissions, Direct Grading & WS Failure Safety
# ---------------------------------------------------------------------------
def test_rc2_team_submissions_direct_grading_and_ws_failure_safety(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Group submissions (teamsubmission.status == 'submitted') and direct grading
    must resolve to 'submitted'/'graded' even when submission.status == 'new' or in
    action_events; and WS status failure must degrade Courses items to 'unknown'.
    """
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120999")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret-pw")
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "ws-token-sim")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "2251120999")
    monkeypatch.setattr(settings, "NOTIFY_IGNORE_SUBMITTED", True)
    monkeypatch.setattr(settings, "NOTIFY_MILESTONES_MINUTES", [180])
    monkeypatch.setattr(settings, "NOTIFY_MILESTONES", [])
    monkeypatch.setattr(settings, "NOTIFY_MINUTES_BEFORE", 0)
    monkeypatch.setattr(settings, "NOTIFY_DND_ENABLE", False)

    due_ts = int((datetime.now() + timedelta(hours=2)).timestamp())
    due_str = datetime.fromtimestamp(due_ts).strftime("%Y-%m-%d %H:%M:%S")

    with patch("core.data_orchestrator.MoodleClient"), patch(
        "core.data_orchestrator.GradeMonitor"
    ):
        orch = DataOrchestrator()

    orch.moodle_service = Mock()
    # 1: Group assignment where individual submission is 'new' while teamsubmission is 'submitted'
    # 2: Direct lecturer grading where submission is 'new' while gradingstatus is 'graded'
    # 3: Direct feedback grade where submission is 'new', gradingstatus is 'notgraded', feedback.grade.grade == '8.50000'
    # 4: Moodle ungraded sentinel feedback.grade.grade == '-1.00000' -> stays 'not_submitted'
    # 5: WS API failure (returns None) on an action_events assignment -> degrades to 'unknown' -> blocked!
    status_payloads = {
        7101: {
            "lastattempt": {
                "submission": {"status": "new"},
                "teamsubmission": {"status": "submitted", "timemodified": due_ts - 600},
                "gradingstatus": "notgraded",
            }
        },
        7102: {
            "lastattempt": {
                "submission": {"status": "new"},
                "gradingstatus": "graded",
            }
        },
        7103: {
            "lastattempt": {
                "submission": {"status": "new"},
                "gradingstatus": "notgraded",
            },
            "feedback": {"grade": {"grade": "8.50000"}},
        },
        7104: {
            "lastattempt": {
                "submission": {"status": "new"},
                "gradingstatus": "notgraded",
            },
            "feedback": {"grade": {"grade": "-1.00000"}},
        },
        7105: None,
    }
    orch.moodle_service.get_submission_status.side_effect = status_payloads.get

    # Action events cards start with submission_status='new' and source='ws_api'
    action_cards = [
        {
            "id": str(9000 + idx),
            "title": f"Assign {aid}",
            "course_name": "Đồ án CNPM",
            "course": "Đồ án CNPM",
            "course_id": 202,
            "assignment_id": aid,
            "deadline": due_str,
            "deadline_str": due_str,
            "url": f"https://courses.ut.edu.vn/mod/assign/view.php?id={600 + idx}",
            "type": "assignment",
            "urgency": "critical",
            "source": "ws_api",
            "submission_status": "new",
            "details": {},
            "is_open": False,
        }
        for idx, aid in enumerate((7101, 7102, 7103, 7104, 7105), start=1)
    ]

    # Unverified action_events assignments must be included in status_hydration_candidates
    candidates = status_hydration_candidates(action_cards)
    assert len(candidates) == 5

    from core.sync_coordinator import FetchOutcome

    orch.fetch_activity_outcome_async = AsyncMock(
        return_value=FetchOutcome.complete(action_cards)
    )

    channel = _RecordingNotifier()
    mgr = _make_IsolatedNotificationManager(tmp_path, channel)
    ctrl = _make_sync_controller(tmp_path, orch, mgr)

    asyncio.run(ctrl._perform_data_sync("manual"))

    by_title = {item["title"]: item["submission_status"] for item in ctrl.all_data}
    assert by_title["Assign 7101"] == "submitted"
    assert by_title["Assign 7102"] == "graded"
    assert by_title["Assign 7103"] == "graded"
    assert by_title["Assign 7104"] == "not_submitted"
    assert by_title["Assign 7105"] == "unknown"

    # ONLY Assign 7104 (verified 'not_submitted') may notify!
    # 7101 ('submitted'), 7102 ('graded'), 7103 ('graded'), and 7105 ('unknown' due to WS failure) MUST NOT notify!
    assert len(channel.notified_batches) == 1
    assert [item["title"] for item in channel.notified_batches[0]] == ["Assign 7104"]

    # Also verify _get_assign_detail + _fetch_detail_via_ws handle team submission & direct grading
    from core import ws_functions

    def _call_ws_team(func_name: str, **kwargs):
        if func_name == "mod_assign_get_assignments":
            return {
                "courses": [
                    {
                        "id": 202,
                        "fullname": "Đồ án CNPM",
                        "assignments": [{"id": 7101, "cmid": 601, "name": "Assign 7101"}],
                    }
                ]
            }
        if func_name == "mod_assign_get_submission_status":
            return status_payloads[7101]
        return {}

    orch.moodle_service.get_assign_details_via_ws.side_effect = (
        lambda cmid, course_id, modulename="assign": ws_functions.get_assign_details_via_ws(
            _call_ws_team, cmid, course_id, modulename
        )
    )
    enriched_team = orch._fetch_detail_via_ws(action_cards[0])
    assert enriched_team is not None
    assert enriched_team["submission_status"] == "submitted"


# ---------------------------------------------------------------------------
# Root Cause 3: Courses 'unknown' Blocked vs THNN 'unknown' Accepted + THNN Cache Bonus
# ---------------------------------------------------------------------------
def test_rc3_policy_distinguishes_courses_unknown_vs_thnn_unknown_and_promotes_cached_thnn_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = ActivityNotificationPolicy(
        NotificationPolicyConfig(
            ignore_submitted=True,
            milestone_minutes=(180, 60),
        )
    )
    now = datetime(2026, 11, 28, 10, 0)
    due = now + timedelta(hours=2)

    courses_unknown_assign = ActivityNotification.from_value(
        {
            "id": "assign_1",
            "title": "Courses Unverified Assign",
            "course": "CS101",
            "deadline": due,
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=101",
            "type": "assignment",
            "submission_status": "unknown",
            "site_identity": "courses",
        }
    )
    courses_unknown_quiz = ActivityNotification.from_value(
        {
            "id": "quiz_2",
            "title": "Courses Unverified Quiz",
            "course": "CS101",
            "deadline": due,
            "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=102",
            "type": "quiz",
            "submission_status": "unknown",
            "source": "ws_quiz_calendar_api",
        }
    )
    thnn_unknown_quiz = ActivityNotification.from_value(
        {
            "id": "55",
            "title": "THNN Unknown Quiz",
            "course": "Anh văn 2",
            "deadline": due,
            "url": "https://thnn.ut.edu.vn/mod/quiz/view.php?id=889",
            "type": "quiz",
            "submission_status": "unknown",
            "site_identity": "thnn",
            "source": "thnn_sync",
        }
    )
    thnn_submitted_quiz = ActivityNotification.from_value(
        {
            "id": "56",
            "title": "THNN Completed Quiz",
            "course": "Anh văn 2",
            "deadline": due,
            "url": "https://thnn.ut.edu.vn/mod/quiz/view.php?id=888",
            "type": "quiz",
            "submission_status": "submitted",
            "site_identity": "thnn",
            "source": "thnn_sync",
        }
    )
    synthetic_unknown = ActivityNotification(
        activity_id="42",
        course_id="7",
        course_name="Lập Trình Python",
        title="Bài tập lớn",
        event_type="assignment",
        deadline=due,
        url="https://courses.example/mod/assign/view.php?id=42",
        submission_status="unknown",
    )

    assert policy.accepts(courses_unknown_assign) is False
    assert policy.accepts(courses_unknown_quiz) is False
    assert policy.desired_schedules([courses_unknown_assign, courses_unknown_quiz], now) == []

    assert policy.accepts(thnn_unknown_quiz) is True
    assert len(policy.desired_schedules([thnn_unknown_quiz], now)) == 1

    assert policy.accepts(thnn_submitted_quiz) is False
    assert policy.accepts(synthetic_unknown) is True

    # Bonus precision: CourseCacheStore already has cached THNN sections where cmid=888
    # has completion_mode=2, completion_state=1 (done) and cmid=889 has completion_state=0.
    store = CourseCacheStore(cache_dir=tmp_path / "courses_cache", namespace="sim_thnn")
    thnn_course = CourseSummary(
        id=301,
        source="thnn",
        fullname="Anh văn 2",
        shortname="AV2",
        display_name="Anh văn 2",
    )
    store.save_catalog([thnn_course], synced_at=1_700_000_000)
    store.save_course_detail(
        "thnn",
        301,
        sections=[
            CourseSection(
                id=1,
                course_id=301,
                source="thnn",
                section_num=1,
                name="Unit 1",
                modules=(
                    CourseModuleItem(
                        cmid=888,
                        instance_id=10,
                        modname="quiz",
                        name="THNN Done Quiz",
                        section_id=1,
                        url="https://thnn.ut.edu.vn/mod/quiz/view.php?id=888",
                        web_url="https://thnn.ut.edu.vn/mod/quiz/view.php?id=888",
                        completion_mode=2,
                        completion_state=1,
                    ),
                    CourseModuleItem(
                        cmid=889,
                        instance_id=11,
                        modname="quiz",
                        name="THNN Todo Quiz",
                        section_id=1,
                        url="https://thnn.ut.edu.vn/mod/quiz/view.php?id=889",
                        web_url="https://thnn.ut.edu.vn/mod/quiz/view.php?id=889",
                        completion_mode=2,
                        completion_state=0,
                    ),
                ),
            )
        ],
        grades=[],
        synced_at=1_700_000_000,
    )

    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120999")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret-pw")
    monkeypatch.setattr(settings, "NOTIFY_IGNORE_SUBMITTED", True)
    monkeypatch.setattr(settings, "NOTIFY_MILESTONES_MINUTES", [180])
    monkeypatch.setattr(settings, "NOTIFY_MILESTONES", [])
    monkeypatch.setattr(settings, "NOTIFY_MINUTES_BEFORE", 0)
    monkeypatch.setattr(settings, "NOTIFY_DND_ENABLE", False)

    due_live_str = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    from core.sync_coordinator import FetchOutcome

    orch = SimpleNamespace(
        is_logged_in=True,
        fetch_activity_outcome_async=AsyncMock(
            return_value=FetchOutcome.complete(
                [
                    {
                        "id": "56",
                        "title": "THNN Done Quiz",
                        "course": "Anh văn 2",
                        "course_name": "Anh văn 2",
                        "deadline": due_live_str,
                        "url": "https://thnn.ut.edu.vn/mod/quiz/view.php?id=888",
                        "type": "quiz",
                        "source": "thnn_sync",
                        "site_identity": "thnn",
                        "submission_status": "unknown",
                    },
                    {
                        "id": "55",
                        "title": "THNN Todo Quiz",
                        "course": "Anh văn 2",
                        "course_name": "Anh văn 2",
                        "deadline": due_live_str,
                        "url": "https://thnn.ut.edu.vn/mod/quiz/view.php?id=889",
                        "type": "quiz",
                        "source": "thnn_sync",
                        "site_identity": "thnn",
                        "submission_status": "unknown",
                    },
                ]
            )
        ),
        get_cached_details_snapshot=lambda: {},
        moodle_service=SimpleNamespace(clear_all_caches=lambda: None),
        clear_detail_cache=lambda: None,
    )
    channel = _RecordingNotifier()
    mgr = _make_IsolatedNotificationManager(tmp_path / "thnn_mgr", channel)
    os.makedirs(tmp_path / "thnn_mgr", exist_ok=True)
    ctrl = _make_sync_controller(
        tmp_path / "thnn_mgr", orch, mgr, course_cache_store=store
    )
    asyncio.run(ctrl._perform_data_sync("timer"))

    statuses = {item["title"]: item["submission_status"] for item in ctrl.all_data}
    assert statuses["THNN Done Quiz"] == "submitted"
    assert statuses["THNN Todo Quiz"] == "unknown"
    assert len(channel.notified_batches) == 1
    assert [item["title"] for item in channel.notified_batches[0]] == ["THNN Todo Quiz"]


# ---------------------------------------------------------------------------
# Root Cause 4: ActivityNotification.from_value Normalization & Key Isolation
# ---------------------------------------------------------------------------
def test_rc4_activity_notification_status_normalization_and_thnn_courses_key_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    due = datetime.now() + timedelta(hours=2)

    # 1. Nested detail status extraction & released -> graded
    quiz_from_details = ActivityNotification.from_value(
        {
            "id": "quiz_99",
            "title": "Quiz nested finished",
            "course": "Math",
            "deadline": due,
            "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=99",
            "type": "quiz",
            "submission_status": "unknown",
            "site_identity": "courses",
            "details": {"quiz_attempt_status": "finished"},
        }
    )
    assert quiz_from_details.site == "courses"
    assert quiz_from_details.submission_status == "submitted"
    # Backward compatibility for existing Courses receipt keys!
    assert quiz_from_details.key == "activity:quiz_99"

    released_assign = ActivityNotification.from_value(
        {
            "id": "assign_88",
            "title": "Assign released grade",
            "course": "OS",
            "deadline": due,
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=88",
            "type": "assignment",
            "submission_status": "new",
            "site_identity": "courses",
            "details": {"status_data": {"Chấm điểm": "released"}},
        }
    )
    assert released_assign.submission_status == "graded"

    # 2. Key collision check when Courses and THNN share numeric event ID "105"
    courses_105 = {
        "id": "105",
        "title": "Courses Event 105",
        "course": "Mạng máy tính",
        "course_name": "Mạng máy tính",
        "deadline": due,
        "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=301",
        "type": "assignment",
        "submission_status": "not_submitted",
        "site_identity": "courses",
    }
    thnn_105 = {
        "id": "105",
        "title": "THNN Event 105",
        "course": "Tiếng Anh 3",
        "course_name": "Tiếng Anh 3",
        "deadline": due,
        "url": "https://thnn.ut.edu.vn/mod/quiz/view.php?id=301",
        "type": "quiz",
        "submission_status": "unknown",
        "site_identity": "thnn",
        "source": "thnn_sync",
    }
    act_courses = ActivityNotification.from_value(courses_105)
    act_thnn = ActivityNotification.from_value(thnn_105)
    assert act_courses.key == "activity:105"
    assert act_thnn.key.startswith("thnn:")
    assert act_courses.key != act_thnn.key

    monkeypatch.setattr(settings, "NOTIFY_IGNORE_SUBMITTED", True)
    monkeypatch.setattr(settings, "NOTIFY_MILESTONES_MINUTES", [180])
    monkeypatch.setattr(settings, "NOTIFY_MILESTONES", [])
    monkeypatch.setattr(settings, "NOTIFY_MINUTES_BEFORE", 0)
    monkeypatch.setattr(settings, "NOTIFY_TYPES", [])
    monkeypatch.setattr(settings, "NOTIFY_MUTED_COURSES", [])
    monkeypatch.setattr(settings, "NOTIFY_DND_ENABLE", False)

    channel = _RecordingNotifier()
    mgr = _make_IsolatedNotificationManager(tmp_path, channel)
    res1 = asyncio.run(mgr.dispatch([courses_105]))
    res2 = asyncio.run(mgr.dispatch([thnn_105]))
    assert res1.delivered == 1
    assert res2.delivered == 1


# ---------------------------------------------------------------------------
# Root Cause 5: _merge_cached_detail_for_activity & Post-Action Alarm Cancellation
# ---------------------------------------------------------------------------
def test_rc5_merge_cached_detail_preserves_submitted_and_post_actions_cancel_schedules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120999")

    # 5a. _merge_cached_detail_for_activity must NOT let stale "new" overwrite cached "submitted"
    stale_card = {
        "id": "901",
        "title": "Group Lab",
        "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=901",
        "type": "assignment",
        "submission_status": "new",
        "details": {},
    }
    cached_submitted = {
        "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=901",
        "type": "assignment",
        "submission_status": "submitted",
        "details": {"status_data": {"Trạng thái nộp bài": "Đã nộp"}},
    }
    merged = DataOrchestrator._merge_cached_detail_for_activity(
        stale_card, cached_submitted
    )
    assert merged["submission_status"] == "submitted"

    # 5b. _show_detail_async discovering "submitted" updates all_data + DataCache + cancels schedules
    quiz_url = "https://courses.ut.edu.vn/mod/quiz/view.php?id=777"
    thnn_quiz_url = "https://thnn.ut.edu.vn/mod/quiz/view.php?id=888"
    due_str = (datetime.now() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    reconcile_calls: list[list[dict]] = []
    bridge_imports: list[list[dict]] = []
    fake_notifier = SimpleNamespace(
        reconcile_schedules=AsyncMock(
            side_effect=lambda items: reconcile_calls.append([dict(x) for x in items])
        )
    )
    fake_bridge = SimpleNamespace(
        available=True,
        import_activities=AsyncMock(
            side_effect=lambda items, authoritative=True: (
                bridge_imports.append([dict(x) for x in items]) or {"scheduled": 0}
            )
        ),
    )

    orch = SimpleNamespace(
        invalidate_detail_cache=Mock(),
        fetch_full_details=lambda data, *_a, **_kw: {
            **data,
            "submission_status": "submitted",
            "details": {
                "quiz_attempt_status": "submitted",
                "status_data": {"Trạng thái": "Đã hoàn thành"},
            },
        },
    )
    ctrl = _make_sync_controller(
        tmp_path, orch, fake_notifier, android_bridge=fake_bridge
    )
    ctrl.calendar_view = SimpleNamespace(visible=False)
    ctrl.courses_view = SimpleNamespace(visible=False)
    ctrl.detail_view = SimpleNamespace(
        visible=True,
        _current_url=quiz_url,
        focus_quiz_opener=AsyncMock(),
    )
    ctrl._detail_request_epoch = 0
    ctrl._diag_screen = lambda *_a, **_kw: None
    ctrl._set_selected_activity = lambda *_a, **_kw: None
    ctrl._sync_quiz_immersive = lambda *_a, **_kw: None
    ctrl.view_manager = SimpleNamespace(
        show_detail_loading=lambda _d: None,
        show_detail_data=lambda _d: None,
        show_detail_error=lambda _d: None,
    )
    scheduled_coros: list = []
    ctrl._safe_run_task = lambda fn, *a, **kw: scheduled_coros.append((fn, a, kw)) or object()

    ctrl.all_data = [
        {
            "id": "quiz_777",
            "title": "Courses Quiz 777",
            "url": quiz_url,
            "type": "quiz",
            "deadline": due_str,
            "submission_status": "not_submitted",
            "site_identity": "courses",
        },
        {
            "id": "888",
            "title": "THNN Quiz 888",
            "url": thnn_quiz_url,
            "type": "quiz",
            "cmid": 888,
            "course_id": 301,
            "deadline": due_str,
            "submission_status": "unknown",
            "site_identity": "thnn",
            "source": "thnn_sync",
        },
    ]

    # Closing QuizPlayerView on Courses Quiz -> refreshes detail -> updates all_data & cancels schedules!
    asyncio.run(ctrl._on_quiz_player_closed(dict(ctrl.all_data[0])))
    for fn, a, kw in list(scheduled_coros):
        scheduled_coros.clear()
        res = fn(*a, **kw)
        if asyncio.iscoroutine(res):
            asyncio.run(res)

    assert ctrl.all_data[0]["submission_status"] == "submitted"
    assert len(bridge_imports) >= 1
    assert len(reconcile_calls) >= 1

    # Closing QuizPlayerView on THNN Quiz (where DetailView is NOT open, using _course_activity_service)
    from core.courses.activity_service import QuizActivitySummary, QuizAttemptInfo

    ctrl.detail_view.visible = False
    ctrl._course_activity_service = SimpleNamespace(
        fetch_quiz_activity=lambda source, module, *, course_id: QuizActivitySummary(
            cmid=888,
            quiz_id=10,
            title="THNN Quiz 888",
            attempts=(
                QuizAttemptInfo(
                    attempt_number=1,
                    attempt_id=501,
                    state="Đã hoàn thành",
                ),
            ),
        )
    )
    bridge_imports.clear()
    reconcile_calls.clear()
    asyncio.run(ctrl._on_quiz_player_closed(dict(ctrl.all_data[1])))
    for fn, a, kw in list(scheduled_coros):
        scheduled_coros.clear()
        res = fn(*a, **kw)
        if asyncio.iscoroutine(res):
            asyncio.run(res)

    assert ctrl.all_data[1]["submission_status"] == "submitted"
    assert len(bridge_imports) >= 1
    assert len(reconcile_calls) >= 1


# ---------------------------------------------------------------------------
# Root Cause 6: Android (Kotlin) & iOS (Swift) Parity Contracts
# ---------------------------------------------------------------------------
def test_rc6_native_android_and_ios_parity_contracts() -> None:
    importer = (ANDROID_MAIN / "data/ForegroundActivityImporter.kt").read_text(
        encoding="utf-8"
    )
    worker = (ANDROID_MAIN / "worker/MoodleSyncWorker.kt").read_text(encoding="utf-8")
    ws_client = (ANDROID_MAIN / "network/MoodleWsClient.kt").read_text(encoding="utf-8")
    policy = (ANDROID_MAIN / "notification/NotificationPolicy.kt").read_text(
        encoding="utf-8"
    )
    swift = IOS_PLUGIN.read_text(encoding="utf-8")

    # 1. ForegroundActivityImporter prefixes thnn:$module:$cmid and normalizes status aliases
    assert "thnn:$module:$cmid" in importer
    assert "submitted for grading" in importer
    assert "released" in importer

    # 2. MoodleSyncWorker preserves foreign-host (THNN) activities across replaceAuthoritativeSnapshot
    assert "thnn:" in worker or "isForeign" in worker or "foreign" in worker.lower()

    # 3. MoodleWsClient checks teamsubmission + gradingstatus + feedback.grade and returns unknown when missing
    assert "teamsubmission" in ws_client
    assert "gradingstatus" in ws_client
    assert "feedback" in ws_client

    # 4. Android NotificationPolicy & iOS UthBackgroundSyncPlugin distinguish Courses unknown vs THNN unknown
    assert "thnn.ut.edu.vn" in policy
    assert "courses.ut.edu.vn" in policy
    assert "released" in policy

    assert "thnn.ut.edu.vn" in swift
    assert "courses.ut.edu.vn" in swift
    assert "released" in swift
