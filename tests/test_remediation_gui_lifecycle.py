"""Zero-trust adversarial TDD regression tests for Slice 3 (GUI Lifecycle & Demo Guards)."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import flet as ft
import pytest

from config import settings
from core.demo_sandbox import DEMO_USERNAME
from core.submission_models import (
    MutationOperation,
    RemoteFile,
    SubmissionSnapshot,
)
from core.use_cases.submission_workflow import (
    MutationOutcome,
    SelectedSubmissionFile,
    SubmissionMutationResult,
    SubmissionSnapshotResult,
)
from gui.app_controller import AppController
from gui.components.detail_view import DetailView
import gui.components.detail_view as detail_view_module
from gui.components.schedule_week_view import ScheduleWeekView
import gui.components.schedule_week_view as schedule_week_view_module
from gui.components.settings_view import SettingsView
from gui.core.utils import DEMO_LMS_WEBVIEW_GUARD_MESSAGE
from gui.view_manager import ViewManager


def _make_snapshot(*, raw_status: str = "submitted", file_name: str = "bai_nop.pdf") -> SubmissionSnapshot:
    remote_file = RemoteFile(
        name=file_name,
        filepath="/",
        size=2048,
        mimetype="application/pdf",
        modified_time=1_700_000_000,
        url=f"https://courses.ut.edu.vn/pluginfile.php/{file_name}",
    )
    return SubmissionSnapshot(
        assignment_id=55555,
        raw_status=raw_status,
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
        maximum_file_count=4,
        maximum_file_bytes=8 * 1024 * 1024,
        accepted_file_types=(".pdf",),
        remote_files=(remote_file,),
        online_text="",
        online_text_format=1,
        attempt_number=0,
        submission_id=101,
        submission_modified_time=1_700_000_000,
    )


def _make_mock_page() -> SimpleNamespace:
    overlay: list[object] = []

    def _run_task(func, *args, **kwargs):
        if asyncio.iscoroutinefunction(func):
            return asyncio.get_running_loop().create_task(func(*args, **kwargs))
        res = func(*args, **kwargs)
        if asyncio.iscoroutine(res):
            return asyncio.get_running_loop().create_task(res)
        return res

    return SimpleNamespace(
        width=1120,
        height=760,
        web=False,
        platform=ft.PagePlatform.WINDOWS,
        overlay=overlay,
        controls=[],
        views=[],
        window=Mock(),
        show_dialog=overlay.append,
        pop_dialog=Mock(),
        update=Mock(),
        run_task=_run_task,
    )


def _walk_controls(root: object) -> list[object]:
    visited: list[object] = []
    stack = [root]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        visited.append(node)
        for attr in ("content", "title", "leading", "trailing"):
            child = getattr(node, attr, None)
            if child is not None:
                stack.append(child)
        for list_attr in ("controls", "actions"):
            children = getattr(node, list_attr, None)
            if isinstance(children, (list, tuple)):
                stack.extend(reversed(children))
    return visited


def _extract_snackbar_texts(controls: list[object]) -> list[str]:
    texts: list[str] = []
    for item in controls:
        if isinstance(item, ft.SnackBar):
            for node in _walk_controls(item):
                if isinstance(node, ft.Text) and isinstance(node.value, str) and node.value:
                    texts.append(node.value)
    return texts


# ===========================================================================
# D-02: DetailView.reset_for_account_switch & Cross-Account Mutation Races
# ===========================================================================


def test_d02_detail_view_reset_for_account_switch_clears_all_mutable_state():
    page = _make_mock_page()
    view = DetailView(
        page,
        on_close=Mock(),
        get_client=lambda: object(),
    )
    url = "https://courses.ut.edu.vn/mod/assign/view.php?id=55555"
    view.update_detail(
        {
            "title": "Bài tập A",
            "course": "Môn A",
            "course_id": 101,
            "type": "other",
            "url": url,
        }
    )
    view.visible = True
    view._apply_submission_snapshot(_make_snapshot(raw_status="draft"))
    view._selected_files = [
        SimpleNamespace(name="draft.pdf", size=10, path=None, bytes=b"abc", mime_type="application/pdf")
    ]
    view._selected_file_indices = {0}
    view._is_multiselect_mode = True
    view._editing_file_index = 0
    view._pending_delete_indices = [0]
    view._pending_file_mutation = (MutationOperation.REPLACE, False)
    view._is_uploading = True
    view._upload_progress.visible = True
    view._upload_status.visible = True
    view._file_edit_dialog.open = True
    view._delete_confirm_dialog.open = True
    view._replace_confirm_dialog.open = True
    view._edit_filename.value = "secret_student_a.pdf"
    view._edit_filepath.value = "/private_folder/"
    view._edit_status.value = "Tên file không hợp lệ"
    view._edit_status.visible = True
    view._delete_confirm_text.value = "Xóa file secret_student_a.pdf?"
    view._submission_status_value.value = "Đã nộp"
    view._countdown_txt.value = "Còn 2 giờ"
    view._deadline_txt.value = "25/11/2026 23:59"
    view._opentime_txt.value = "20/11/2026 08:00"
    view._opentime_row.visible = True
    view._cutoff_txt.value = "26/11/2026 23:59"
    view._cutoff_row.visible = True
    view._quiz_launch_target = object()

    prev_view_gen = view._view_generation
    prev_load_gen = view._snapshot_load_generation

    view.reset_for_account_switch()

    assert view._view_generation > prev_view_gen
    assert view._snapshot_load_generation > prev_load_gen
    assert view.visible is False
    assert view._current_url == ""
    assert view._current_data == {}
    assert view._submitted_files == []
    assert view._selected_files == []
    assert view._selected_file_indices == set()
    assert view._is_multiselect_mode is False
    assert view._editing_file_index == -1
    assert view._pending_delete_indices == []
    assert view._pending_file_mutation is None
    assert view._submission_snapshot is None
    assert view._submission_fingerprint == ""
    assert view._submission_policy is None
    assert view._quiz_launch_target is None
    assert view._is_uploading is False
    assert view._upload_progress.visible is False
    assert view._upload_status.visible is False
    assert view._submitted_area.visible is False
    assert view._submission_area.visible is False
    assert view._file_edit_dialog.open is False
    assert view._delete_confirm_dialog.open is False
    assert view._replace_confirm_dialog.open is False
    assert view._edit_filename.value == ""
    assert view._edit_filepath.value == "/"
    assert view._edit_status.value == ""
    assert view._edit_status.visible is False
    assert view._delete_confirm_text.value == ""
    assert view._submission_status_value.value == ""
    assert view._countdown_txt.value == ""
    assert view._deadline_txt.value == ""
    assert view._opentime_txt.value == ""
    assert view._opentime_row.visible is False
    assert view._cutoff_txt.value == ""
    assert view._cutoff_row.visible is False


def test_d02_inflight_mutation_and_finalize_dropped_after_reset_for_account_switch():
    async def _scenario():
        mutate_started = threading.Event()
        mutate_release = threading.Event()
        finalize_started = threading.Event()
        finalize_release = threading.Event()
        status_callbacks: list[tuple[str, str]] = []

        class _SlowWorkflow:
            def mutate_files(self, target, intent, *, selected_files=()):
                mutate_started.set()
                assert mutate_release.wait(5)
                return SubmissionMutationResult.success(
                    _make_snapshot(raw_status="submitted"),
                    MutationOutcome.SUBMITTED_FOR_GRADING,
                )

            def finalize_submission(self, target, intent):
                finalize_started.set()
                assert finalize_release.wait(5)
                return SubmissionMutationResult.success(
                    _make_snapshot(raw_status="submitted"),
                    MutationOutcome.SUBMITTED_FOR_GRADING,
                )

        page = _make_mock_page()
        view = DetailView(
            page,
            on_close=Mock(),
            get_client=lambda: object(),
            on_status_changed=lambda u, s: status_callbacks.append((u, s)),
            submission_workflow_factory=lambda _c: _SlowWorkflow(),
        )
        shared_url = "https://courses.ut.edu.vn/mod/assign/view.php?id=55555"

        # 1. File mutation in flight when account switches
        view.update_detail({"url": shared_url, "course_id": 101, "type": "other"})
        view._apply_submission_snapshot(_make_snapshot(raw_status="draft"))
        status_callbacks.clear()
        view._selected_files = [
            SimpleNamespace(
                name="student_a.pdf",
                size=5,
                path=None,
                bytes=b"A-data",
                mime_type="application/pdf",
            )
        ]

        task1 = asyncio.create_task(
            view._execute_file_mutation(MutationOperation.ADD, False)
        )
        assert await asyncio.to_thread(mutate_started.wait, 2)

        # Account switches mid-flight!
        view.reset_for_account_switch()
        mutate_release.set()
        await task1

        assert status_callbacks == []
        assert view._submitted_files == []
        assert view._current_url == ""

        # 2. Finalize submission in flight when account switches
        view.update_detail({"url": shared_url, "course_id": 101, "type": "other"})
        view._apply_submission_snapshot(_make_snapshot(raw_status="draft"))
        status_callbacks.clear()

        task2 = asyncio.create_task(view._execute_finalize_submission())
        assert await asyncio.to_thread(finalize_started.wait, 2)

        view.reset_for_account_switch()
        finalize_release.set()
        await task2

        assert status_callbacks == []
        assert view._submitted_files == []

    asyncio.run(_scenario())


def test_d02_pane_assignment_embedded_detail_view_cannot_contaminate_new_account(
    monkeypatch,
):
    """Attack vector: PaneAssignmentView constructs its own DetailView instance
    separate from AppController.detail_view; reset_for_account_switch() is NOT
    called on that orphaned instance when switching accounts."""

    async def _scenario():
        monkeypatch.setattr(settings, "UTH_USERNAME", "student_a")
        mutate_started = threading.Event()
        mutate_release = threading.Event()
        load_started = threading.Event()
        load_release = threading.Event()

        class _SlowPaneWorkflow:
            def mutate_files(self, target, intent, *, selected_files=()):
                mutate_started.set()
                assert mutate_release.wait(5)
                return SubmissionMutationResult.success(
                    _make_snapshot(raw_status="submitted"),
                    MutationOutcome.SUBMITTED_FOR_GRADING,
                )

            def load_snapshot(self, target, prefetched_status=None):
                load_started.set()
                assert load_release.wait(5)
                return SubmissionSnapshotResult.success(
                    _make_snapshot(raw_status="submitted")
                )

        shared_url = "https://courses.ut.edu.vn/mod/assign/view.php?id=55555"
        controller = AppController.__new__(AppController)
        controller._account_epoch = 10
        controller._data_lock = threading.Lock()
        controller._pending_updates = {}
        controller.all_data = [
            {
                "url": shared_url,
                "title": "Shared Assignment",
                "submission_status": "not_submitted",
                "details": {},
            }
        ]
        save_local_mock = Mock()
        controller._data_cache = SimpleNamespace(save_local_activities=save_local_mock)
        controller.orchestrator = SimpleNamespace(invalidate_detail_cache=Mock())
        controller._update_footer = Mock()
        controller._safe_run_task = Mock(return_value=object())
        controller.notifier = SimpleNamespace(reconcile_schedules=AsyncMock())
        controller._android_background = None

        page = _make_mock_page()
        # Separate DetailView instance just like CoursesCoordinator._handle_open_assignment!
        embedded_detail = DetailView(
            page,
            on_close=Mock(),
            get_client=lambda: object(),
            on_status_changed=controller._on_activity_status_changed,
            submission_workflow_factory=lambda _c: _SlowPaneWorkflow(),
            embedded_in_pane=True,
        )
        embedded_detail.update_detail(
            {"url": shared_url, "course_id": 101, "type": "other"}
        )
        embedded_detail._apply_submission_snapshot(_make_snapshot(raw_status="draft"))
        # Clear Student A's initial draft status update before starting the race
        controller._pending_updates.clear()
        controller.all_data[0]["submission_status"] = "not_submitted"
        save_local_mock.reset_mock()
        controller._safe_run_task.reset_mock()

        embedded_detail._selected_files = [
            SimpleNamespace(
                name="pane_a.pdf",
                size=6,
                path=None,
                bytes=b"Pane-A",
                mime_type="application/pdf",
            )
        ]
        mutation_task = asyncio.create_task(
            embedded_detail._execute_file_mutation(MutationOperation.ADD, False)
        )
        assert await asyncio.to_thread(mutate_started.wait, 2)

        # Also start an async snapshot load on a second embedded DetailView under epoch 10
        embedded_detail_2 = DetailView(
            page,
            on_close=Mock(),
            get_client=lambda: object(),
            on_status_changed=controller._on_activity_status_changed,
            submission_workflow_factory=lambda _c: _SlowPaneWorkflow(),
            embedded_in_pane=True,
        )
        embedded_detail_2._current_url = shared_url
        embedded_detail_2._current_data = {"url": shared_url, "course_id": 101}
        load_gen, view_gen = embedded_detail_2._reserve_submission_snapshot_load()
        load_task = asyncio.create_task(
            embedded_detail_2._async_load_submitted_files(
                object(), shared_url, 101, None, load_gen, view_gen
            )
        )
        assert await asyncio.to_thread(load_started.wait, 2)

        # Switch to Student B (bumps controller._account_epoch to 11; does NOT touch embedded_detail!)
        monkeypatch.setattr(settings, "UTH_USERNAME", "student_b")
        controller._account_epoch = 11

        mutate_release.set()
        load_release.set()
        await mutation_task
        await load_task

        # Student B's session MUST have zero contamination!
        assert controller._pending_updates == {}
        assert controller.all_data[0]["submission_status"] == "not_submitted"
        save_local_mock.assert_not_called()
        controller._safe_run_task.assert_not_called()

        # Direct call with explicit stale expected_account_epoch is also rejected
        controller._on_activity_status_changed(
            shared_url, "Đã nộp", expected_account_epoch=10
        )
        assert controller._pending_updates == {}
        assert controller.all_data[0]["submission_status"] == "not_submitted"

        # Direct call with matching current expected_account_epoch succeeds
        controller._on_activity_status_changed(
            shared_url, "Đã nộp", expected_account_epoch=11
        )
        assert controller.all_data[0]["submission_status"] == "submitted"

    asyncio.run(_scenario())


def test_d02_teardown_account_session_resets_detail_view_and_closes_overlay(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "student_b")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "pass_b")

    page = _make_mock_page()
    dashboard = ft.Container(visible=False, opacity=1.0)
    settings_view = ft.Container(visible=False, opacity=1.0)
    calendar_view = ft.Container(visible=False, opacity=1.0)
    grade_view = ft.Container(visible=False, opacity=1.0)
    detail_view = DetailView(page, on_close=Mock(), get_client=lambda: object())
    tab_switcher = ft.AnimatedSwitcher(content=dashboard, expand=True)
    empty_detail = ft.Container(expand=True, ignore_interactions=True)
    detail_switcher = ft.AnimatedSwitcher(content=empty_detail, expand=True)

    controller = AppController.__new__(AppController)
    controller.page = page
    controller.dashboard = dashboard
    controller.detail_view = detail_view
    controller.settings_view = settings_view
    controller.calendar_view = calendar_view
    controller.grade_overview_view = grade_view
    controller.tab_switcher = tab_switcher
    controller.detail_switcher = detail_switcher
    controller._detail_host = ft.Container(content=detail_switcher, ignore_interactions=True)
    controller._clear_selected_activity = Mock()
    controller._dismiss_desktop_filter_menu = Mock()
    controller._close_app_webview = Mock()
    controller._safe_run_task = Mock(return_value=object())
    controller._settings_identity = ("student_a", "pass_a")
    controller._account_epoch = 0
    controller._data_lock = threading.Lock()
    controller._cards_lock = threading.Lock()
    controller.all_data = []
    controller._pending_updates = {}
    controller.active_cards = []

    vm = ViewManager(
        page=page,
        dashboard=dashboard,
        detail_view=detail_view,
        settings_view=settings_view,
        calendar_view=calendar_view,
        grade_overview_view=grade_view,
        controller=controller,
        tab_switcher=tab_switcher,
        detail_switcher=detail_switcher,
        empty_detail=empty_detail,
    )
    controller.view_manager = vm

    # Open DetailView overlay for Student A with populated files
    vm.show_detail_loading(
        {
            "title": "Bài tập A",
            "course": "Môn A",
            "course_id": 101,
            "type": "other",
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=55555",
        }
    )
    detail_view._submitted_files = [{"name": "secret_a.pdf"}]
    assert detail_view.visible is True
    assert detail_switcher.content is detail_view
    assert controller._detail_host.ignore_interactions is False

    # Teardown on account switch -> must reset detail_view and close overlay!
    new_epoch = controller._teardown_account_session(
        ("student_b", "pass_b"),
        reset_moodle_and_caches=True,
        account_switched_or_logged_out=True,
        schedule_refresh_trigger=None,
        trigger_android_logout=False,
        clear_ui=True,
    )

    assert new_epoch == 1
    assert detail_view.visible is False
    assert detail_view._submitted_files == []
    assert detail_view._current_url == ""
    assert detail_switcher.content is empty_detail
    assert controller._detail_host.ignore_interactions is True


def test_d02_same_identity_relogin_preserves_singleton_detail_view_and_mounted_pane_assignment(
    monkeypatch,
):
    """Regression guard for critic #36 [HIGH]:
    Same-identity _complete_login_success() (e.g., Settings 'Kiểm tra kết nối' or
    Quiz LoginDialog with unchanged credentials) must cancel in-flight syncs via
    _sync_generation WITHOUT bricking singleton DetailView or mounted PaneAssignmentView,
    and un-reset DetailViews must never wedge _is_uploading = True in finally."""
    from core.sync_coordinator import FetchOutcome

    async def _scenario():
        monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
        monkeypatch.setattr(settings, "UTH_PASSWORD", "pass_a")
        monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "tok_a")

        snapshot_loads: list[str] = []
        mutate_calls: list[str] = []
        finalize_calls: list[str] = []
        stale_mutate_started = threading.Event()
        stale_mutate_release = threading.Event()
        stale_finalize_started = threading.Event()
        stale_finalize_release = threading.Event()
        block_mode = {"block": False}

        class _TrackingWorkflow:
            def load_snapshot(self, target, prefetched_status=None):
                snapshot_loads.append(target.url)
                return SubmissionSnapshotResult.success(
                    _make_snapshot(raw_status="draft")
                )

            def mutate_files(self, target, intent, *, selected_files=()):
                if block_mode["block"]:
                    stale_mutate_started.set()
                    assert stale_mutate_release.wait(5)
                mutate_calls.append(target.url)
                return SubmissionMutationResult.success(
                    _make_snapshot(raw_status="submitted"),
                    MutationOutcome.SUBMITTED_FOR_GRADING,
                )

            def finalize_submission(self, target, intent):
                if block_mode["block"]:
                    stale_finalize_started.set()
                    assert stale_finalize_release.wait(5)
                finalize_calls.append(target.url)
                return SubmissionMutationResult.success(
                    _make_snapshot(raw_status="submitted"),
                    MutationOutcome.SUBMITTED_FOR_GRADING,
                )

        first_sync_started = asyncio.Event()
        first_sync_release = asyncio.Event()
        sync_invocations = {"count": 0}

        async def _fetch_outcomes():
            sync_invocations["count"] += 1
            if sync_invocations["count"] == 1:
                first_sync_started.set()
                await first_sync_release.wait()
                return FetchOutcome.complete(
                    [
                        {
                            "title": "Stale First Sync",
                            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=99999",
                            "submission_status": "not_submitted",
                        }
                    ]
                )
            return FetchOutcome.complete(
                [
                    {
                        "title": "Fresh Re-Login Sync",
                        "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=55555",
                        "submission_status": "not_submitted",
                        "details": {},
                    }
                ]
            )

        orch = SimpleNamespace(
            is_logged_in=True,
            fetch_activity_outcome_async=_fetch_outcomes,
            get_cached_details_snapshot=lambda: {},
            moodle_service=SimpleNamespace(clear_all_caches=Mock()),
            clear_detail_cache=Mock(),
            invalidate_detail_cache=Mock(),
        )
        controller = _build_minimal_sync_controller(orch)
        controller._settings_identity = ("2251120001", "pass_a")
        controller._account_epoch = 0

        page = controller.page
        assign_url = "https://courses.ut.edu.vn/mod/assign/view.php?id=55555"
        pane_url = "https://courses.ut.edu.vn/mod/assign/view.php?id=66666"

        # Singleton DetailView constructed at AppController.__init__ (epoch 0)
        singleton_dv = DetailView(
            page,
            on_close=Mock(),
            get_client=lambda: object(),
            get_account_epoch=lambda: getattr(controller, "_account_epoch", 0),
            on_status_changed=controller._on_activity_status_changed,
            submission_workflow_factory=lambda _c: _TrackingWorkflow(),
            embedded_in_pane=False,
        )
        controller.detail_view = singleton_dv

        # Mounted CoursesTabView PaneAssignmentView DetailView (embedded_in_pane=True)
        pane_dv = DetailView(
            page,
            on_close=Mock(),
            get_client=lambda: object(),
            get_account_epoch=lambda: getattr(controller, "_account_epoch", 0),
            on_status_changed=controller._on_activity_status_changed,
            submission_workflow_factory=lambda _c: _TrackingWorkflow(),
            embedded_in_pane=True,
        )

        # Start an in-flight sync #1 before same-identity re-login
        stale_sync_task = asyncio.create_task(controller._perform_data_sync("manual"))
        await first_sync_started.wait()

        # Trigger same-identity _complete_login_success() ("Kiểm tra kết nối" / Quiz reauth)
        await controller._complete_login_success()
        first_sync_release.set()
        await stale_sync_task

        # Same-identity re-login cancelled stale sync #1 & committed fresh sync #2,
        # WITHOUT advancing _account_epoch!
        assert controller._account_epoch == 0
        assert [item["title"] for item in controller.all_data] == ["Fresh Re-Login Sync"]

        # 1. Singleton DetailView MUST still load snapshots and complete mutations + finalize!
        snapshot_loads.clear()
        singleton_dv.update_detail(
            {"url": assign_url, "course_id": 101, "type": "assignment"}
        )
        await asyncio.sleep(0.05)
        assert snapshot_loads == [assign_url]
        assert singleton_dv._submission_snapshot is not None

        singleton_dv._selected_files = [
            SimpleNamespace(
                name="work.pdf",
                size=4,
                path=None,
                bytes=b"work",
                mime_type="application/pdf",
            )
        ]
        await singleton_dv._execute_file_mutation(MutationOperation.ADD, False)
        assert singleton_dv._is_uploading is False
        assert singleton_dv._upload_progress.visible is False
        assert mutate_calls == [assign_url]

        await singleton_dv._execute_finalize_submission()
        assert singleton_dv._is_uploading is False
        assert singleton_dv._upload_progress.visible is False
        assert finalize_calls == [assign_url]

        # 2. Mounted PaneAssignmentView (embedded_in_pane=True) MUST ALSO still work!
        snapshot_loads.clear()
        pane_dv.update_detail(
            {"url": pane_url, "course_id": 202, "type": "assignment"}
        )
        await asyncio.sleep(0.05)
        assert snapshot_loads == [pane_url]

        pane_dv._selected_files = [
            SimpleNamespace(
                name="pane.pdf",
                size=4,
                path=None,
                bytes=b"pane",
                mime_type="application/pdf",
            )
        ]
        await pane_dv._execute_file_mutation(MutationOperation.ADD, False)
        assert pane_dv._is_uploading is False
        assert pane_dv._upload_progress.visible is False
        assert mutate_calls[-1] == pane_url

        # 3. Defense-in-depth: if an un-reset embedded DetailView has an in-flight
        #    mutation or finalize when _account_epoch DOES advance (real account switch),
        #    finally: MUST still reset _is_uploading=False and _upload_progress.visible=False
        #    without calling _page.update()!
        block_mode["block"] = True
        pane_dv._selected_files = [
            SimpleNamespace(
                name="race.pdf",
                size=4,
                path=None,
                bytes=b"race",
                mime_type="application/pdf",
            )
        ]
        mut_task = asyncio.create_task(
            pane_dv._execute_file_mutation(MutationOperation.ADD, False)
        )
        assert await asyncio.to_thread(stale_mutate_started.wait, 2)
        assert pane_dv._is_uploading is True
        assert pane_dv._upload_progress.visible is True

        # Switch accounts (epoch 0 -> 1) without calling reset_for_account_switch on pane_dv
        controller._account_epoch = 1
        page.update.reset_mock()
        stale_mutate_release.set()
        await mut_task
        assert pane_dv._is_uploading is False
        assert pane_dv._upload_progress.visible is False
        page.update.assert_not_called()

        # Same check for _execute_finalize_submission on un-reset embedded view
        pane_dv2 = DetailView(
            page,
            on_close=Mock(),
            get_client=lambda: object(),
            get_account_epoch=lambda: getattr(controller, "_account_epoch", 0),
            on_status_changed=controller._on_activity_status_changed,
            submission_workflow_factory=lambda _c: _TrackingWorkflow(),
            embedded_in_pane=True,
        )
        block_mode["block"] = False
        pane_dv2.update_detail(
            {"url": pane_url, "course_id": 202, "type": "assignment"}
        )
        await asyncio.sleep(0.05)
        block_mode["block"] = True
        fin_task = asyncio.create_task(pane_dv2._execute_finalize_submission())
        assert await asyncio.to_thread(stale_finalize_started.wait, 2)
        assert pane_dv2._is_uploading is True
        controller._account_epoch = 2
        page.update.reset_mock()
        stale_finalize_release.set()
        await fin_task
        assert pane_dv2._is_uploading is False
        assert pane_dv2._upload_progress.visible is False
        page.update.assert_not_called()

    asyncio.run(_scenario())


def test_d02_forwarded_expected_epoch_and_embedded_pane_late_update_detail_guard():
    """Regression guard for critic #36 [MEDIUM]:
    1. _execute_file_mutation, _execute_finalize_submission, and _async_load_submitted_files
       forward captured expected_account_epoch explicitly through _apply_mutation_result /
       _apply_submission_snapshot into _notify_submission_status.
    2. Embedded PaneAssignmentView DetailView (embedded_in_pane=True) bails out at the very top
       of update_detail() / show_loading() if account context changed, without mutating controls
       or invoking _quiz_player_view.prepare_activity."""

    async def _scenario():
        state = {"epoch": 7}
        recorded_status: list[tuple[str, str, int | None]] = []

        def _on_status(
            url: str, status: str, *, expected_account_epoch: int | None = None
        ):
            recorded_status.append((url, status, expected_account_epoch))

        class _EpochFlippingBeforeNotifyWorkflow:
            def load_snapshot(self, target, prefetched_status=None):
                return SubmissionSnapshotResult.success(
                    _make_snapshot(raw_status="draft")
                )

            def mutate_files(self, target, intent, *, selected_files=()):
                return SubmissionMutationResult.success(
                    _make_snapshot(raw_status="submitted"),
                    MutationOutcome.SUBMITTED_FOR_GRADING,
                )

            def finalize_submission(self, target, intent):
                return SubmissionMutationResult.success(
                    _make_snapshot(raw_status="graded"),
                    MutationOutcome.SUBMITTED_FOR_GRADING,
                )

        page = _make_mock_page()
        assign_url = "https://courses.ut.edu.vn/mod/assign/view.php?id=55555"
        singleton_dv = DetailView(
            page,
            on_close=Mock(),
            get_client=lambda: object(),
            get_account_epoch=lambda: state["epoch"],
            on_status_changed=_on_status,
            submission_workflow_factory=lambda _c: _EpochFlippingBeforeNotifyWorkflow(),
            embedded_in_pane=False,
        )
        singleton_dv.update_detail(
            {"url": assign_url, "course_id": 101, "type": "other"}
        )

        # Spy on _apply_submission_snapshot to prove expected_account_epoch=7 is passed explicitly
        orig_apply_snap = singleton_dv._apply_submission_snapshot
        forwarded_epochs: list[int | None] = []

        def _spy_apply_snap(snapshot, *, expected_account_epoch: int | None = None):
            forwarded_epochs.append(expected_account_epoch)
            # Even if _owner_account_epoch is cleared mid-apply, explicit arg wins!
            singleton_dv._owner_account_epoch = 999
            return orig_apply_snap(
                snapshot, expected_account_epoch=expected_account_epoch
            )

        singleton_dv._apply_submission_snapshot = _spy_apply_snap

        # (a) _async_load_submitted_files forwards expected_epoch=7
        singleton_dv._owner_account_epoch = 7
        load_gen, view_gen = singleton_dv._reserve_submission_snapshot_load()
        await singleton_dv._async_load_submitted_files(
            object(), assign_url, 101, None, load_gen, view_gen
        )

        # (b) _execute_file_mutation forwards expected_epoch=7
        singleton_dv._owner_account_epoch = 7
        singleton_dv._selected_files = [
            SimpleNamespace(
                name="a.pdf", size=3, path=None, bytes=b"abc", mime_type="application/pdf"
            )
        ]
        await singleton_dv._execute_file_mutation(MutationOperation.ADD, False)

        # (c) _execute_finalize_submission forwards expected_epoch=7
        singleton_dv._owner_account_epoch = 7
        await singleton_dv._execute_finalize_submission()

        assert forwarded_epochs == [7, 7, 7]
        assert [ep for (_u, _s, ep) in recorded_status] == [7, 7, 7]

        # Part B: Embedded PaneAssignmentView DetailView ignores late update_detail / show_loading
        quiz_player = SimpleNamespace(
            prepare_activity=Mock(return_value=(object(), None))
        )
        state["epoch"] = 3
        embedded_pane = DetailView(
            page,
            on_close=Mock(),
            get_client=lambda: object(),
            get_account_epoch=lambda: state["epoch"],
            quiz_player_view=quiz_player,
            submission_workflow_factory=lambda _c: _EpochFlippingBeforeNotifyWorkflow(),
            embedded_in_pane=True,
        )

        # Switch to Account B (epoch 3 -> 4) BEFORE CoursesCoordinator._load_pane_assignment_async finishes!
        state["epoch"] = 4
        run_task_mock = Mock()
        page.run_task = run_task_mock

        embedded_pane.show_loading(
            {
                "title": "Account A Loading",
                "url": assign_url,
                "course_id": 101,
                "type": "assignment",
            }
        )
        assert embedded_pane._title_text.value == ""
        assert embedded_pane._current_url == ""
        assert embedded_pane.visible is False

        embedded_pane.update_detail(
            {
                "title": "Account A Secret Quiz",
                "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=777",
                "course_id": 101,
                "type": "quiz",
            }
        )
        assert embedded_pane._title_text.value == ""
        assert embedded_pane._current_url == ""
        quiz_player.prepare_activity.assert_not_called()
        run_task_mock.assert_not_called()

    asyncio.run(_scenario())


# ===========================================================================
# Standalone Demo Mode URL Guards: DetailView & ScheduleWeekView (DEMO-10)
# ===========================================================================


def test_standalone_detail_view_and_schedule_week_view_demo_url_guards(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", DEMO_USERNAME)
    page = _make_mock_page()

    webview_opened: list[str] = []
    external_launched: list[str] = []

    class _FakeWebViewDialog:
        def __init__(self, _page, *, url: str, title: str = "", on_open_external=None):
            self.url = url
            self.title = title
            self.on_open_external = on_open_external

        def show(self):
            webview_opened.append(self.url)

    async def _fake_launch_url(_self, url: str, **_kwargs):
        external_launched.append(url)

    monkeypatch.setattr(detail_view_module, "InAppWebViewDialog", _FakeWebViewDialog)
    monkeypatch.setattr(
        schedule_week_view_module, "InAppWebViewDialog", _FakeWebViewDialog
    )
    monkeypatch.setattr(ft.UrlLauncher, "launch_url", _fake_launch_url)

    # 1. Standalone DetailView (on_open_url=None, on_open_external=None)
    dv = DetailView(page, on_close=Mock(), on_open_url=None, on_open_external=None)
    uth_url = "https://courses.ut.edu.vn/mod/assign/view.php?id=101"
    non_uth_url = "https://docs.python.org/3/"

    async def _exercise_detail():
        page.overlay.clear()
        dv._open_url_in_app(uth_url, "Bài tập")
        assert webview_opened == []
        assert DEMO_LMS_WEBVIEW_GUARD_MESSAGE in _extract_snackbar_texts(page.overlay)

        page.overlay.clear()
        dv._launch_external_sync(uth_url)
        await asyncio.sleep(0)
        assert external_launched == []
        assert DEMO_LMS_WEBVIEW_GUARD_MESSAGE in _extract_snackbar_texts(page.overlay)

        page.overlay.clear()
        dv._current_url = uth_url
        await dv._open_external(None)
        assert external_launched == []
        assert DEMO_LMS_WEBVIEW_GUARD_MESSAGE in _extract_snackbar_texts(page.overlay)

        # Non-UTH HTTPS links still work in standalone DetailView!
        page.overlay.clear()
        dv._open_url_in_app(non_uth_url, "Docs")
        assert webview_opened == [non_uth_url]
        dv._launch_external_sync(non_uth_url)
        await asyncio.sleep(0)
        assert external_launched == [non_uth_url]

    asyncio.run(_exercise_detail())

    # 2. Standalone ScheduleWeekView (on_open_url=None, on_open_external=None, DEMO-10)
    webview_opened.clear()
    external_launched.clear()
    swv = ScheduleWeekView(page, fetch_week=None, on_open_url=None, on_open_external=None)

    async def _exercise_schedule():
        page.overlay.clear()
        swv._open_url(uth_url, "LMS", require_uth_lms=True)
        assert webview_opened == []
        assert DEMO_LMS_WEBVIEW_GUARD_MESSAGE in _extract_snackbar_texts(page.overlay)

        page.overlay.clear()
        swv._open_external(uth_url, require_uth_lms=True)
        await asyncio.sleep(0)
        assert external_launched == []
        assert DEMO_LMS_WEBVIEW_GUARD_MESSAGE in _extract_snackbar_texts(page.overlay)

        # Non-UTH Zoom/Docs HTTPS links still open in standalone ScheduleWeekView!
        page.overlay.clear()
        zoom_url = "https://zoom.us/j/123456789"
        swv._open_url(zoom_url, "Zoom", require_uth_lms=False)
        assert webview_opened == [zoom_url]
        swv._open_external(zoom_url, require_uth_lms=False)
        await asyncio.sleep(0)
        assert external_launched == [zoom_url]

    asyncio.run(_exercise_schedule())


# ===========================================================================
# D-03: Resilient _shutdown_for_exit & _destroy_window Async Teardown Prep
# ===========================================================================


def test_d03_shutdown_for_exit_isolates_every_teardown_step_on_closed_loop_or_errors():
    steps: list[str] = []

    def _step_and_raise(name: str, exc_msg: str = "Event loop is closed"):
        def _fn(*_args, **_kwargs):
            steps.append(name)
            raise RuntimeError(exc_msg)

        return _fn

    controller = AppController.__new__(AppController)
    controller._exit_cleanup_lock = threading.Lock()
    controller._exit_cleanup_done = False
    controller._page_alive = threading.Event()
    controller._page_alive.set()
    controller._prefetch_epoch = 3
    controller._prefetch_cancel_event = threading.Event()
    controller._card_detail_warm_cancel_event = threading.Event()

    controller.view_manager = SimpleNamespace(
        cancel_pending_settings_navigation=_step_and_raise("view_manager_cancel")
    )
    controller.activation_broker = SimpleNamespace(
        close=_step_and_raise("activation_broker_close", "broker boom")
    )
    controller.tray = SimpleNamespace(
        close=_step_and_raise("tray_close", "tray boom")
    )
    controller._sync_coordinator = SimpleNamespace(
        close=_step_and_raise("sync_coordinator_close")
    )
    controller._today_schedule_coordinator = SimpleNamespace(
        close=_step_and_raise("today_schedule_close")
    )
    controller._update_coordinator = SimpleNamespace(
        shutdown=_step_and_raise("update_coordinator_shutdown", "updater boom")
    )
    controller._close_app_webview = _step_and_raise("close_app_webview", "webview boom")
    controller._courses_coordinator = SimpleNamespace(
        close=_step_and_raise("courses_coordinator_close", "preview server boom")
    )
    ch1 = SimpleNamespace(close=_step_and_raise("ch1_close", "ch1 boom"))
    ch2 = SimpleNamespace(close=lambda: steps.append("ch2_close"))
    controller.notifier = SimpleNamespace(
        close=_step_and_raise("notifier_mgr_close", "notifier boom"),
        notifiers=[ch1, ch2],
    )
    controller.orchestrator = SimpleNamespace(
        notifier=controller.notifier,
        client=SimpleNamespace(close=_step_and_raise("client_close", "client boom")),
        thnn_client=SimpleNamespace(close=lambda: steps.append("thnn_client_close")),
    )

    # Must NOT raise, and EVERY step must run despite earlier RuntimeErrors!
    controller._shutdown_for_exit()

    assert steps == [
        "view_manager_cancel",
        "activation_broker_close",
        "tray_close",
        "sync_coordinator_close",
        "today_schedule_close",
        "update_coordinator_shutdown",
        "close_app_webview",
        "courses_coordinator_close",
        "notifier_mgr_close",
        "ch1_close",
        "ch2_close",
        "client_close",
        "thnn_client_close",
    ]
    assert not controller._page_alive.is_set()
    assert controller._prefetch_epoch == 4
    assert controller._prefetch_cancel_event.is_set()
    assert controller._card_detail_warm_cancel_event.is_set()

    # Idempotent second call does nothing
    controller._shutdown_for_exit()
    assert len(steps) == 13

    # Minimal partial test double without optional methods/attributes must not raise AttributeError
    bare = AppController.__new__(AppController)
    bare._shutdown_for_exit()
    assert bare._exit_cleanup_done is True


def test_d03_destroy_window_prepares_async_shutdown_before_window_destroy():
    events: list[str] = []

    def _sync_close_boom():
        events.append("sync_close")
        raise RuntimeError("Event loop edge error")

    controller = AppController.__new__(AppController)
    controller._page_alive = threading.Event()
    controller._page_alive.set()
    controller._prefetch_cancel_event = threading.Event()
    controller._card_detail_warm_cancel_event = threading.Event()
    controller._sync_coordinator = SimpleNamespace(close=_sync_close_boom)
    controller._today_schedule_coordinator = SimpleNamespace(
        close=lambda: events.append("today_close")
    )
    controller._close_app_webview = lambda update_page=False: events.append(
        f"webview_close:{update_page}"
    )

    async def _fake_destroy():
        events.append("window_destroy")
        raise RuntimeError("Session closed")

    controller.page = SimpleNamespace(window=SimpleNamespace(destroy=_fake_destroy))

    asyncio.run(controller._destroy_window())

    assert events == [
        "sync_close",
        "today_close",
        "webview_close:False",
        "window_destroy",
    ]
    assert not controller._page_alive.is_set()
    assert controller._prefetch_cancel_event.is_set()
    assert controller._card_detail_warm_cancel_event.is_set()


# ===========================================================================
# SEAM-02 (DEMO-07), D-01, DEMO-02, DEMO-09: SettingsView & Account Switch Auth
# ===========================================================================


def _build_minimal_sync_controller(orchestrator, *, save_settings_ok: bool = True) -> AppController:
    controller = AppController.__new__(AppController)
    controller.orchestrator = orchestrator
    controller._settings_identity = (settings.UTH_USERNAME, settings.UTH_PASSWORD)
    controller._account_epoch = 0
    controller._prefetch_epoch = 0
    controller._detail_request_epoch = 0
    controller._grades_request_epoch = 0
    controller._course_grades_request_epoch = 0
    controller._is_loading = False
    controller._active_sync_epoch = None
    controller._skeleton_visible = True
    controller._prefetch_cancel_event = threading.Event()
    controller._card_detail_warm_cancel_event = threading.Event()
    controller._data_lock = threading.Lock()
    controller._cards_lock = threading.Lock()
    controller.all_data = []
    controller.active_cards = []
    controller._pending_updates = {}
    controller._last_successful_sync_at = None
    controller.refresh_btn = SimpleNamespace(disabled=False)
    controller.empty_state = SimpleNamespace(visible=False)
    controller.error_state = SimpleNamespace(visible=False)
    controller.error_text = SimpleNamespace(value="")
    controller.page = _make_mock_page()
    controller._diag_breadcrumb = Mock()
    controller._set_sync_status = Mock()
    controller._update_footer = Mock()
    controller._safe_run_task = Mock(return_value=object())
    controller._close_app_webview = Mock()
    controller._android_background = None
    controller.notifier = None
    controller._sync_coordinator = None
    controller._data_cache = SimpleNamespace(
        load=lambda: ([], None),
        save_server_snapshot=lambda _items: save_settings_ok,
        load_snapshot=lambda: SimpleNamespace(last_successful_sync_at="2026-11-25T12:00:00"),
        record_failed_attempt=lambda _err: None,
    )
    return controller


def test_seam02_teardown_and_failed_settings_sync_never_force_is_logged_in_true(
    monkeypatch,
):
    import gui.app_controller as app_controller_module
    import gui.components.settings_view as settings_view_module
    from core.data_orchestrator import DataOrchestrator
    from core.sync_coordinator import FetchOutcome

    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "pass_a")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "token_a")
    monkeypatch.setattr(app_controller_module, "save_settings", lambda: True)
    monkeypatch.setattr(settings_view_module, "save_settings", lambda: True)

    # 1. Direct _teardown_account_session must NOT leak was_logged_in=True from Account A!
    orch = DataOrchestrator()
    orch.is_logged_in = True
    controller = _build_minimal_sync_controller(orch)

    controller._teardown_account_session(
        ("2251120888", "pass_b"),
        reset_moodle_and_caches=True,
        account_switched_or_logged_out=True,
        schedule_refresh_trigger=None,
        trigger_android_logout=False,
        clear_ui=False,
        mark_logged_out=False,
    )
    assert orch.is_logged_in is False

    # 2. SettingsView._handle_test_login -> _handle_settings_account_login_async
    #    where probe succeeds (persist=False), then _load_data_async FAILS to obtain token!
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "pass_a")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "token_a")
    orch.is_logged_in = True
    controller._settings_identity = ("2251120001", "pass_a")

    probe_calls: list[tuple[str | None, str | None, bool]] = []

    def _fake_client_login(
        username: str | None = None,
        password: str | None = None,
        force: bool = False,
        persist: bool = True,
    ) -> bool:
        probe_calls.append((username, password, persist))
        if persist:
            settings.MOODLE_WS_TOKEN = "recovered_token_b"
        return True

    monkeypatch.setattr(orch.client, "login", _fake_client_login)
    monkeypatch.setattr(
        orch,
        "fetch_activity_outcome_async",
        AsyncMock(return_value=FetchOutcome.failed("Moodle token endpoint timed out")),
    )
    # Prevent _teardown_account_session from replacing our fake _data_cache
    monkeypatch.setattr(
        app_controller_module,
        "_build_account_data_cache",
        lambda _u: controller._data_cache,
    )

    view = SettingsView(
        page=_make_mock_page(),
        orchestrator=orch,
        on_close=Mock(),
        autostart_coordinator=None,
    )
    view.update = Mock()
    view.on_account_login = controller._handle_settings_account_login_async
    view._baseline_snapshot = view._capture_form_snapshot()

    view._username_field.value = "2251120888"
    view._password_field.value = "pass_b"
    asyncio.run(view._handle_test_login(None))

    assert settings.UTH_USERNAME == "2251120888"
    assert settings.MOODLE_WS_TOKEN == ""
    # CRITICAL SEAM-02 invariant: is_logged_in MUST be False because post-login sync failed!
    assert orch.is_logged_in is False

    # Consequently, subsequent orch.login() does NOT short-circuit True with empty token;
    # it actually calls client.login(persist=True) and recovers the token!
    probe_calls.clear()
    assert orch.login() is True
    assert len(probe_calls) == 1
    assert settings.MOODLE_WS_TOKEN == "recovered_token_b"


def test_seam02_settings_login_sets_is_logged_in_true_on_successful_real_and_demo_sync(
    monkeypatch,
):
    import gui.app_controller as app_controller_module
    import gui.components.settings_view as settings_view_module
    from core.data_orchestrator import DataOrchestrator
    from core.sync_coordinator import FetchOutcome

    reviewer_pw = "UTH-" + "Review-2026!"
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "pass_a")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "")
    monkeypatch.setattr(app_controller_module, "save_settings", lambda: True)
    monkeypatch.setattr(settings_view_module, "save_settings", lambda: True)

    orch = DataOrchestrator()
    controller = _build_minimal_sync_controller(orch)
    monkeypatch.setattr(
        app_controller_module,
        "_build_account_data_cache",
        lambda _u: controller._data_cache,
    )

    def _fake_login(username=None, password=None, force=False, persist=True) -> bool:
        return True

    monkeypatch.setattr(orch.client, "login", _fake_login)
    # Test empty activity list `[]` (valid student with 0 assignments) as well!
    monkeypatch.setattr(
        orch,
        "fetch_activity_outcome_async",
        AsyncMock(return_value=FetchOutcome.complete([])),
    )

    view = SettingsView(
        page=_make_mock_page(),
        orchestrator=orch,
        on_close=Mock(),
        autostart_coordinator=None,
    )
    view.update = Mock()
    view.on_account_login = controller._handle_settings_account_login_async
    view._baseline_snapshot = view._capture_form_snapshot()

    # 1. Real account with successful sync (even 0 activities) -> is_logged_in is True!
    view._username_field.value = "2251120999"
    view._password_field.value = "real_pw_999"
    asyncio.run(view._handle_test_login(None))
    assert settings.UTH_USERNAME == "2251120999"
    assert orch.is_logged_in is True

    # 2. Demo account via Settings -> is_logged_in is True & WS token stays empty!
    view._username_field.value = "  UTHelper_Demo  "
    view._password_field.value = reviewer_pw
    asyncio.run(view._handle_test_login(None))
    assert settings.UTH_USERNAME == DEMO_USERNAME
    assert settings.MOODLE_WS_TOKEN == ""
    assert orch.is_logged_in is True


def test_d01_settings_logout_and_test_login_finally_tolerate_unmounted_update_error(
    monkeypatch,
):
    import gui.components.settings_view as settings_view_module

    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret")
    monkeypatch.setattr(settings_view_module, "save_settings", lambda: True)

    unmounted = {"flag": False}

    def _update_raising_when_unmounted():
        if unmounted["flag"]:
            raise RuntimeError("Control must be added to the page first")

    orch = SimpleNamespace(
        client=SimpleNamespace(login=lambda **_kw: True, clear_session=Mock()),
        is_logged_in=True,
        get_cached_details_snapshot=lambda: {},
    )
    view = SettingsView(
        page=_make_mock_page(),
        orchestrator=orch,
        on_close=Mock(),
        autostart_coordinator=None,
    )
    view.update = _update_raising_when_unmounted
    view._baseline_snapshot = view._capture_form_snapshot()

    async def _unmounting_logout():
        unmounted["flag"] = True

    async def _unmounting_login(_u: str, _p: str):
        unmounted["flag"] = True

    view.on_logout = _unmounting_logout
    view.on_account_login = _unmounting_login

    # 1. Logout unmounts SettingsView before finally: runs -> must NOT raise RuntimeError!
    asyncio.run(view._handle_logout(None))
    assert view._logout_btn.disabled is False
    assert view._test_login_btn.disabled is False

    # 2. Test login unmounts SettingsView -> must NOT raise RuntimeError!
    unmounted["flag"] = False
    view._username_field.value = "2251120002"
    view._password_field.value = "secret2"
    asyncio.run(view._handle_test_login(None))
    assert view._test_login_btn.disabled is False
    assert view._username_field.disabled is False
    assert view._password_field.disabled is False


def test_demo02_settings_debug_external_channel_tests_blocked_in_demo_mode(monkeypatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", DEMO_USERNAME)
    tele_mock = Mock()
    discord_mock = Mock()
    mail_mock = Mock()
    tray_mock = Mock()
    mobile_mock = Mock()

    view = SettingsView(
        page=_make_mock_page(),
        orchestrator=SimpleNamespace(get_cached_details_snapshot=lambda: {}),
        on_close=Mock(),
        on_test_tray=tray_mock,
        on_test_mobile=mobile_mock,
        on_test_tele=tele_mock,
        on_test_discord=discord_mock,
        on_test_mail=mail_mock,
        autostart_coordinator=None,
    )
    view.update = Mock()
    view._debug_info_text.update = Mock()

    for action in (
        view._do_test_tele,
        view._do_test_discord,
        view._do_test_mail,
        view._do_test_broadcast,
    ):
        view._debug_info_text.value = ""
        action()
        assert "Demo" in view._debug_info_text.value

    assert tele_mock.call_count == 0
    assert discord_mock.call_count == 0
    assert mail_mock.call_count == 0
    assert tray_mock.call_count == 0
    assert mobile_mock.call_count == 0

    # Real student account still invokes the debug test callbacks normally!
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    view._do_test_tele()
    view._do_test_discord()
    view._do_test_mail()
    assert tele_mock.call_count == 1
    assert discord_mock.call_count == 1
    assert mail_mock.call_count == 1


def test_demo09_settings_test_login_preserves_exact_password_without_stripping(
    monkeypatch,
):
    import gui.components.settings_view as settings_view_module

    monkeypatch.setattr(settings, "UTH_USERNAME", "")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")
    monkeypatch.setattr(settings_view_module, "save_settings", lambda: True)

    received: list[tuple[str, str]] = []

    def _fake_login(username: str, password: str, force: bool = False, persist: bool = True) -> bool:
        received.append((username, password))
        return True

    orch = SimpleNamespace(
        client=SimpleNamespace(login=_fake_login),
        is_logged_in=False,
        get_cached_details_snapshot=lambda: {},
    )
    view = SettingsView(
        page=_make_mock_page(),
        orchestrator=orch,
        on_close=Mock(),
        autostart_coordinator=None,
    )
    view.update = Mock()
    view._baseline_snapshot = view._capture_form_snapshot()

    # Whitespace-only password must still be rejected as empty
    view._username_field.value = "2251120555"
    view._password_field.value = "    "
    asyncio.run(view._handle_test_login(None))
    assert received == []

    # Password with intentional leading/trailing spaces is preserved verbatim;
    # username is stripped.
    exact_pw = "  exact-S3cret!  "
    view._username_field.value = "  2251120555  "
    view._password_field.value = exact_pw
    asyncio.run(view._handle_test_login(None))

    assert received == [("2251120555", exact_pw)]
    assert settings.UTH_USERNAME == "2251120555"
    assert settings.UTH_PASSWORD == exact_pw
    assert view._password_field.value == exact_pw
    assert view.has_changes() is False


# ===========================================================================
# DEMO-01, DEMO-04, D-05, D-06: AppController Cache, Grade, Refresh & Epoch
# ===========================================================================


def test_demo01_build_account_data_cache_disables_legacy_migration_in_demo_mode(
    monkeypatch, tmp_path
):
    import json
    import config
    from gui.app_controller import _build_account_data_cache

    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")

    legacy_path = tmp_path / "activities_cache.json"
    legacy_payload = {
        "version": 2,
        "activities": [
            {
                "title": "Real Student Private Assignment",
                "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=999",
            }
        ],
        "last_successful_sync_at": "2026-11-24T10:00:00+00:00",
    }
    legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    # 1. Demo login MUST NOT ingest or unlink legacy activities_cache.json!
    demo_cache = _build_account_data_cache("  UTHelper_Demo  ")
    assert demo_cache._allow_legacy_migration is False
    demo_snap = demo_cache.load_snapshot()
    assert demo_snap.activities == []
    assert legacy_path.exists() is True

    # 2. Subsequent real student login DOES migrate legacy activities_cache.json cleanly!
    real_cache = _build_account_data_cache("2251120001")
    assert real_cache._allow_legacy_migration is True
    real_snap = real_cache.load_snapshot()
    assert len(real_snap.activities) == 1
    assert real_snap.activities[0]["title"] == "Real Student Private Assignment"
    assert legacy_path.exists() is False


def test_demo04_show_course_grades_suppresses_grade_notifications_in_demo_mode(
    monkeypatch,
):
    shown_courses: list[object] = []
    change = SimpleNamespace(item_name="Giữa kỳ", old_grade="7.0", new_grade="9.5")

    controller = AppController.__new__(AppController)
    controller._account_epoch = 1
    controller._course_grades_request_epoch = 0
    controller._diag_screen = Mock()
    controller._diag_breadcrumb = Mock()
    controller.view_manager = SimpleNamespace(
        show_course_grades_loading=Mock(),
        show_course_grades_data=shown_courses.append,
        show_course_grades_error=Mock(),
    )
    controller.orchestrator = SimpleNamespace(
        latest_course_grades=[],
        course_grade_monitor=SimpleNamespace(load_cached=lambda: []),
        refresh_course_grades=lambda: (["Course1"], [change]),
    )
    controller._notify_grade_changes = AsyncMock()

    # Demo Mode: UI still renders courses, but grade notifications are suppressed!
    monkeypatch.setattr(settings, "UTH_USERNAME", DEMO_USERNAME)
    asyncio.run(controller._show_course_grades(force_refresh=True))
    assert shown_courses == [["Course1"]]
    controller._notify_grade_changes.assert_not_awaited()

    # Real account: grade notifications fire normally!
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    asyncio.run(controller._show_course_grades(force_refresh=True))
    controller._notify_grade_changes.assert_awaited_once_with([change])


def test_d05_refresh_btn_reenabled_after_mid_sync_logout_or_missing_credentials_early_exit(
    monkeypatch,
):
    from core.sync_coordinator import FetchOutcome

    # 1. Early exit at _has_sync_credentials() when refresh_btn was stuck disabled=True
    monkeypatch.setattr(settings, "UTH_USERNAME", "")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "")

    orch = SimpleNamespace(
        fetch_activity_outcome_async=AsyncMock(),
        moodle_service=SimpleNamespace(clear_all_caches=Mock()),
        clear_detail_cache=Mock(),
    )
    controller = _build_minimal_sync_controller(orch)
    controller.refresh_btn = SimpleNamespace(
        disabled=True,
        update=Mock(side_effect=RuntimeError("unmounted")),
    )

    asyncio.run(controller._perform_data_sync("manual"))
    assert controller.refresh_btn.disabled is False
    orch.fetch_activity_outcome_async.assert_not_awaited()

    # 2. Mid-sync logout while _perform_data_sync is in flight
    async def _mid_sync_logout_scenario():
        monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
        monkeypatch.setattr(settings, "UTH_PASSWORD", "pass_a")
        fetch_started = asyncio.Event()
        fetch_release = asyncio.Event()

        async def _slow_outcome():
            fetch_started.set()
            await fetch_release.wait()
            return FetchOutcome.complete([])

        orch2 = SimpleNamespace(
            fetch_activity_outcome_async=_slow_outcome,
            moodle_service=SimpleNamespace(clear_all_caches=Mock()),
            clear_detail_cache=Mock(),
        )
        ctrl2 = _build_minimal_sync_controller(orch2)
        sync_task = asyncio.create_task(ctrl2._perform_data_sync("manual"))
        await fetch_started.wait()
        assert ctrl2.refresh_btn.disabled is True

        # User logs out mid-sync!
        monkeypatch.setattr(settings, "UTH_USERNAME", "")
        monkeypatch.setattr(settings, "UTH_PASSWORD", "")
        ctrl2._teardown_account_session(
            ("", ""),
            reset_moodle_and_caches=True,
            account_switched_or_logged_out=True,
            schedule_refresh_trigger=None,
            trigger_android_logout=False,
            clear_ui=True,
            mark_logged_out=True,
        )
        assert ctrl2.refresh_btn.disabled is False

        fetch_release.set()
        await sync_task
        assert ctrl2.refresh_btn.disabled is False

    asyncio.run(_mid_sync_logout_scenario())


def test_d06_fetch_moodle_course_grades_async_aborts_mid_step_on_account_epoch_change():
    from core.courses.models import CourseGradeItem as CoreMoodleGradeItem, CourseSummary

    course_summary = CourseSummary(
        id=777,
        source="courses",
        shortname="010112345601",
        fullname="010112345601 - Lập trình Python",
        display_name="Lập trình Python",
        course_code="010112345601",
    )
    portal_course = SimpleNamespace(
        course_code="010112345601",
        class_code="010112345601",
        course_name="Lập trình Python",
    )
    grade_item = CoreMoodleGradeItem(
        id=1,
        item_name="Bài tập 1",
        grade_formatted="9.0",
        range_formatted="0–10",
    )

    controller = AppController.__new__(AppController)
    controller._account_epoch = 5
    controller._lookup_legacy_monitor_grades = Mock()

    # Subcase A: Account switches during refresh_catalog -> load_course_grades MUST NOT be called!
    load_grades_mock = Mock(return_value=[grade_item])

    def _refresh_then_switch(*, include_thnn: bool = True):
        assert include_thnn is True
        controller._account_epoch = 6
        return [course_summary]

    controller._course_cache_store = SimpleNamespace(
        load_catalog=lambda: SimpleNamespace(courses=[]),
        load_course_detail=lambda _s, _id: SimpleNamespace(last_synced_at=1700000000),
    )
    controller._course_catalog_service = SimpleNamespace(
        refresh_catalog=_refresh_then_switch,
        load_course_grades=load_grades_mock,
    )

    res_a = asyncio.run(controller._fetch_moodle_course_grades_async(portal_course))
    load_grades_mock.assert_not_called()
    controller._lookup_legacy_monitor_grades.assert_not_called()
    assert res_a.grades == []
    assert res_a.course_id is None

    # Subcase B: Account switches during load_course_grades -> stale grades discarded!
    controller._account_epoch = 10

    def _load_grades_then_switch(_src: str, _cid: int, _force: bool = False):
        controller._account_epoch = 11
        return [grade_item]

    controller._course_cache_store = SimpleNamespace(
        load_catalog=lambda: SimpleNamespace(courses=[course_summary]),
        load_course_detail=lambda _s, _id: SimpleNamespace(last_synced_at=1700000000),
    )
    controller._course_catalog_service = SimpleNamespace(
        refresh_catalog=Mock(return_value=[course_summary]),
        load_course_grades=_load_grades_then_switch,
    )

    res_b = asyncio.run(controller._fetch_moodle_course_grades_async(portal_course))
    assert res_b.grades == []
    assert res_b.course_id is None

    # Subcase C: Epoch stays stable -> valid grades returned!
    controller._account_epoch = 20
    controller._course_catalog_service = SimpleNamespace(
        refresh_catalog=Mock(return_value=[course_summary]),
        load_course_grades=Mock(return_value=[grade_item]),
    )
    res_c = asyncio.run(controller._fetch_moodle_course_grades_async(portal_course))
    assert res_c.grades == [grade_item]
    assert res_c.course_id == 777
