import os
import sys
import time
import asyncio
import threading
import pytest
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gui.app_controller import AppController
from gui.components.detail_view import DetailView
from core.submission_models import (
    MutationOperation,
    RemoteFile,
    SubmissionSnapshot,
)
from core.submission_snapshot import parse_submission_snapshot
from core.use_cases.submission_workflow import (
    MutationOutcome,
    SelectedSubmissionFile,
    SubmissionError,
    SubmissionErrorCode,
    SubmissionMutationResult,
    SubmissionSnapshotResult,
    SubmissionTarget,
)
from tests.fixtures.moodle_submission_responses import (
    FakeMoodle43,
    captured_real_submission_shape_fixture,
)
import flet as ft


def snapshot(*, files=(), **changes) -> SubmissionSnapshot:
    values = {
        "assignment_id": 42,
        "raw_status": "draft",
        "can_edit": True,
        "can_submit": True,
        "locked": False,
        "graded": False,
        "submissions_enabled": True,
        "submission_drafts": True,
        "statement_required": True,
        "file_submission_enabled": True,
        "team_submission": False,
        "due_date": 0,
        "cutoff_date": 0,
        "allows_submissions_from_date": 0,
        "maximum_file_count": 4,
        "maximum_file_bytes": 8 * 1024 * 1024,
        "accepted_file_types": (".pdf",),
        "remote_files": tuple(files),
        "online_text": "",
        "online_text_format": 1,
        "attempt_number": 0,
        "submission_id": 9,
        "submission_modified_time": 1_700_000_000,
    }
    values.update(changes)
    return SubmissionSnapshot(**values)


def remote(name: str) -> RemoteFile:
    return RemoteFile(
        name=name,
        filepath="/",
        size=1024,
        mimetype="application/pdf",
        modified_time=1_700_000_000,
        url=f"https://moodle.invalid/{name}",
    )


@pytest.fixture
def detail_view_shell():
    shell = SimpleNamespace(
        _submitted_files=[],
        _submission_snapshot=None,
        _submission_fingerprint="",
        _submission_policy=None,
        _is_uploading=False,
        _selected_files=[],
        _submission_statement=SimpleNamespace(value=False),
        _submission_status_value=SimpleNamespace(value="", color=None),
        _upload_status=SimpleNamespace(value="", color=None, visible=False),
        _last_server_status=None,
        _on_status_changed=None,
        _current_url="",
        _current_data={},
        _render_submission_policy=lambda: None,
        _build_submitted_files_ui=lambda: None,
    )
    for name in (
        "_apply_submission_snapshot",
        "_apply_mutation_result",
        "_build_file_intent",
        "_show_upload_status",
        "_request_file_mutation",
        "_on_finalize",
        "_confirm_replace_mutation",
        "_selected_submission_files",
    ):
        setattr(shell, name, MethodType(getattr(DetailView, name), shell))
    return shell


def test_loaded_snapshot_replaces_server_file_list_and_stores_fingerprint(
    detail_view_shell,
):
    snap = snapshot(files=(remote("server.pdf"),))

    detail_view_shell._apply_submission_snapshot(snap)

    assert [item["name"] for item in detail_view_shell._submitted_files] == [
        "server.pdf"
    ]
    assert detail_view_shell._submission_fingerprint == snap.fingerprint


def test_append_action_builds_add_intent_with_displayed_fingerprint(
    detail_view_shell,
):
    snap = snapshot(files=(remote("server.pdf"),))
    detail_view_shell._apply_submission_snapshot(snap)

    intent = detail_view_shell._build_file_intent(
        MutationOperation.ADD, finalize=False
    )

    assert intent.operation is MutationOperation.ADD
    assert intent.expected_fingerprint == snap.fingerprint


def test_post_mutation_ui_uses_result_snapshot_on_verification_failure(
    detail_view_shell,
):
    server = snapshot(files=(remote("actual.pdf"),))
    result = SubmissionMutationResult.failure(
        SubmissionError(
            SubmissionErrorCode.VERIFICATION_FAILED,
            "server mismatch containing unsafe transport details",
        ),
        server,
    )

    detail_view_shell._apply_mutation_result(result)

    assert [item["name"] for item in detail_view_shell._submitted_files] == [
        "actual.pdf"
    ]


def test_refresh_failure_keeps_last_server_list_and_fingerprint(detail_view_shell):
    previous = snapshot(files=(remote("known.pdf"),))
    detail_view_shell._apply_submission_snapshot(previous)
    result = SubmissionMutationResult.failure(
        SubmissionError(
            SubmissionErrorCode.SNAPSHOT_LOAD_FAILED,
            "https://moodle.invalid/?token=SECRET",
        ),
        None,
        partial=True,
    )

    detail_view_shell._apply_mutation_result(result)

    assert [item["name"] for item in detail_view_shell._submitted_files] == [
        "known.pdf"
    ]
    assert detail_view_shell._submission_fingerprint == previous.fingerprint
    assert detail_view_shell._upload_status.visible is True
    assert "SECRET" not in detail_view_shell._upload_status.value


@pytest.mark.parametrize("accepted", [False, True])
def test_statement_checkbox_boolean_is_copied_exactly(
    detail_view_shell, accepted
):
    detail_view_shell._apply_submission_snapshot(snapshot())
    detail_view_shell._submission_statement.value = accepted

    intent = detail_view_shell._build_file_intent(
        MutationOperation.ADD, finalize=True
    )

    assert intent.accept_statement is accepted


@pytest.mark.anyio
async def test_replace_waits_for_confirmation_before_running_workflow(
    detail_view_shell,
):
    detail_view_shell._apply_submission_snapshot(
        snapshot(files=(remote("old.pdf"),))
    )
    detail_view_shell._show_replace_confirmation = MagicMock()
    detail_view_shell._execute_file_mutation = AsyncMock()

    await detail_view_shell._request_file_mutation(
        MutationOperation.REPLACE, finalize=False
    )

    detail_view_shell._execute_file_mutation.assert_not_awaited()
    detail_view_shell._show_replace_confirmation.assert_called_once()

    await detail_view_shell._confirm_replace_mutation()

    detail_view_shell._execute_file_mutation.assert_awaited_once_with(
        MutationOperation.REPLACE, False
    )


def test_picker_bytes_are_preserved_for_single_and_multiple_pathless_files(
    detail_view_shell,
):
    first = SimpleNamespace(
        name="first.pdf", size=5, path=None, bytes=b"first", mime_type="application/pdf"
    )
    second = SimpleNamespace(
        name="second.pdf", size=6, path=None, bytes=b"second", mime_type="application/pdf"
    )

    detail_view_shell._selected_files = [first]
    assert detail_view_shell._selected_submission_files() == (
        SelectedSubmissionFile("first.pdf", b"first"),
    )

    detail_view_shell._selected_files = [first, second]
    assert detail_view_shell._selected_submission_files() == (
        SelectedSubmissionFile("first.pdf", b"first"),
        SelectedSubmissionFile("second.pdf", b"second"),
    )


def test_partial_finalize_reconciles_confirmed_pending_files(detail_view_shell):
    selected = SimpleNamespace(
        name="new.pdf", size=1024, path=None, bytes=b"new", mime_type="application/pdf"
    )
    detail_view_shell._selected_files = [selected]
    detail_view_shell._update_file_preview = MagicMock()
    verified = snapshot(files=(remote("new.pdf"),))
    result = SubmissionMutationResult.failure(
        SubmissionError(
            SubmissionErrorCode.FINALIZE_REJECTED,
            "Moodle rejected finalization",
        ),
        verified,
        partial=True,
    )

    detail_view_shell._apply_mutation_result(result)

    assert detail_view_shell._selected_files == []
    retry = detail_view_shell._build_file_intent(
        MutationOperation.ADD, finalize=True
    )
    assert retry.selected_files == ()


@pytest.mark.anyio
async def test_finalize_action_is_separate_from_file_mutation_selection(
    detail_view_shell,
):
    detail_view_shell._request_finalize_submission = AsyncMock()

    await detail_view_shell._on_finalize()

    detail_view_shell._request_finalize_submission.assert_awaited_once_with()


def test_online_text_only_snapshot_renders_finalize_without_file_controls():
    view = DetailView(MockPage(), lambda: None)
    view._apply_submission_snapshot(
        snapshot(
            files=(),
            online_text="<p>Online answer</p>",
            file_submission_enabled=False,
        )
    )

    assert view._pick_btn.visible is False
    assert view._submit_btn.visible is False
    assert view._finalize_btn.visible is True


@pytest.mark.anyio
async def test_newest_same_assignment_snapshot_load_wins_when_older_finishes_last():
    first_started = threading.Event()
    release_first = threading.Event()
    older = snapshot(
        files=(remote("older.pdf"),),
        raw_status="submitted",
        submission_modified_time=1_700_000_001,
    )
    newer = snapshot(
        files=(remote("newer.pdf"),),
        raw_status="draft",
        submission_modified_time=1_700_000_002,
    )

    class OverlappingWorkflow:
        calls = 0
        lock = threading.Lock()

        def load_snapshot(self, target, prefetched_status=None):
            with self.lock:
                self.calls += 1
                call = self.calls
            if call == 1:
                first_started.set()
                assert release_first.wait(5)
                return SubmissionSnapshotResult.success(older)
            return SubmissionSnapshotResult.success(newer)

    page = MockPage()
    workflow = OverlappingWorkflow()
    callbacks = []
    view = DetailView(
        page,
        lambda: None,
        on_status_changed=lambda url, status: callbacks.append((url, status)),
        submission_workflow_factory=lambda _: workflow,
    )
    url = "https://courses.ut.edu.vn/mod/assign/view.php?id=123"
    view.update_detail({"url": url, "course_id": 456, "type": "other"})

    first = asyncio.create_task(
        view._async_load_submitted_files(object(), url, 456)
    )
    assert await asyncio.to_thread(first_started.wait, 2)
    second = asyncio.create_task(
        view._async_load_submitted_files(object(), url, 456)
    )
    await second
    release_first.set()
    await first

    assert view._submission_snapshot is newer
    assert view._submission_fingerprint == newer.fingerprint
    assert [item["name"] for item in view._submitted_files] == ["newer.pdf"]
    assert view._last_server_status == "Bản nháp"
    assert callbacks == [(url, "Bản nháp")]


@pytest.mark.anyio
async def test_real_shape_snapshot_renders_submission_area_and_picker_after_load():
    assignment, status = captured_real_submission_shape_fixture()
    real_snapshot = parse_submission_snapshot(77, assignment, status)

    class Workflow:
        def load_snapshot(self, target, prefetched_status=None):
            return SubmissionSnapshotResult.success(real_snapshot)

    view = DetailView(
        MockPage(),
        lambda: None,
        submission_workflow_factory=lambda _: Workflow(),
    )
    url = "https://courses.ut.edu.vn/mod/assign/view.php?id=77"
    view.update_detail({"url": url, "course_id": 456, "type": "assignment"})

    await view._async_load_submitted_files(object(), url, 456)

    assert view._submission_area.visible is True
    assert view._pick_btn.visible is True
    assert view._submit_btn.visible is False


@pytest.mark.anyio
async def test_late_previous_assignment_snapshot_cannot_expose_new_picker():
    started = threading.Event()
    release = threading.Event()
    assignment, status = captured_real_submission_shape_fixture()
    eligible_a = parse_submission_snapshot(77, assignment, status)
    ineligible_b = snapshot(
        assignment_id=202,
        file_submission_enabled=False,
        submission_id=202,
    )

    class SlowWorkflow:
        def load_snapshot(self, target, prefetched_status=None):
            started.set()
            assert release.wait(5)
            return SubmissionSnapshotResult.success(eligible_a)

    view = DetailView(
        MockPage(),
        lambda: None,
        submission_workflow_factory=lambda _: SlowWorkflow(),
    )
    url_a = "https://courses.ut.edu.vn/mod/assign/view.php?id=77"
    url_b = "https://courses.ut.edu.vn/mod/assign/view.php?id=202"
    view.update_detail({"url": url_a, "course_id": 1, "type": "assignment"})
    first = asyncio.create_task(view._async_load_submitted_files(object(), url_a, 1))
    assert await asyncio.to_thread(started.wait, 2)

    view.update_detail({"url": url_b, "course_id": 2, "type": "assignment"})
    view._apply_submission_snapshot(ineligible_b)
    release.set()
    await first

    assert view._submission_snapshot is ineligible_b
    assert view._pick_btn.visible is False


def test_visible_submission_status_tracks_each_server_snapshot():
    view = DetailView(MockPage(), lambda: None)
    view.update_detail(
        {
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
            "course_id": 456,
            "type": "assignment",
            "details": {
                "status_data": {"Submission status": "No submission"}
            },
        }
    )
    status_section = view._content_col.controls[0]
    status_row = status_section.content.controls[1].content.controls[0]
    assert status_row.controls[1] is view._submission_status_value

    values = []
    colors = []

    for raw_status in ("new", "draft", "submitted"):
        view._apply_submission_snapshot(snapshot(raw_status=raw_status))
        values.append(view._submission_status_value.value)
        colors.append(view._submission_status_value.color)

    assert values == ["Chưa nộp", "Bản nháp", "Đã nộp"]
    assert colors[0] != colors[1] != colors[2]


@pytest.mark.parametrize("details", [{}, {"status_data": {}}])
def test_assignment_always_mounts_status_control_before_snapshot(details):
    page = MockPage()
    page.run_task = MagicMock()
    view = DetailView(page, lambda: None, get_client=lambda: object())
    view.update_detail(
        {
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
            "course_id": 456,
            "type": "assignment",
            "details": details,
        }
    )

    status_sections = [
        section
        for section in view._content_col.controls
        if section.content.controls[0].value == "TRẠNG THÁI"
    ]
    assert len(status_sections) == 1
    rows = status_sections[0].content.controls[1].content.controls
    submission_rows = [
        row for row in rows if row.controls[0].value == "Trạng thái nộp bài"
    ]
    assert len(submission_rows) == 1
    assert submission_rows[0].controls[1] is view._submission_status_value
    assert view._submission_status_value.value == "Đang đồng bộ với Moodle..."

    view._apply_submission_snapshot(snapshot(raw_status="submitted"))

    assert submission_rows[0].controls[1].value == "Đã nộp"


@pytest.mark.parametrize(
    ("get_client", "course_id", "reason"),
    [
        (
            lambda: None,
            456,
            "Không thể kết nối Moodle trong ứng dụng. Hãy mở bài tập trong trình duyệt.",
        ),
        (
            lambda: object(),
            None,
            "Thiếu thông tin học phần nên không thể đồng bộ bài nộp. Hãy mở bài tập trong trình duyệt.",
        ),
    ],
)
def test_assignment_without_native_context_explains_browser_fallback(
    get_client, course_id, reason
):
    view = DetailView(MockPage(), lambda: None, get_client=get_client)

    view.update_detail(
        {
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
            "course_id": course_id,
            "type": "assignment",
            "details": {},
        }
    )

    assert view._submission_status_value.value == reason
    assert view._cta_text.value == "Mở trong trình duyệt"
    assert callable(view._open_btn.on_click)
    assert view._submission_area.visible is False
    assert view._pick_btn.visible is False
    assert view._submit_btn.visible is False
    assert view._finalize_btn.visible is False


@pytest.mark.parametrize(
    ("url", "course_id", "reason", "browser_available"),
    [
        (
            "",
            456,
            "Không thể xác định đường dẫn bài tập an toàn trên Moodle.",
            False,
        ),
        (
            "https://courses.ut.edu.vn/mod/quiz/view.php?id=123",
            456,
            "Đường dẫn này không phải bài tập Moodle. Hãy mở trong trình duyệt.",
            True,
        ),
        (
            "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
            "unknown",
            "Thông tin học phần không hợp lệ nên không thể đồng bộ bài nộp. Hãy mở bài tập trong trình duyệt.",
            True,
        ),
        (
            "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
            0,
            "Thông tin học phần không hợp lệ nên không thể đồng bộ bài nộp. Hãy mở bài tập trong trình duyệt.",
            True,
        ),
        (
            "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
            -1,
            "Thông tin học phần không hợp lệ nên không thể đồng bộ bài nộp. Hãy mở bài tập trong trình duyệt.",
            True,
        ),
        (
            "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
            1.5,
            "Thông tin học phần không hợp lệ nên không thể đồng bộ bài nộp. Hãy mở bài tập trong trình duyệt.",
            True,
        ),
        (
            "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
            "1.5",
            "Thông tin học phần không hợp lệ nên không thể đồng bộ bài nộp. Hãy mở bài tập trong trình duyệt.",
            True,
        ),
    ],
)
def test_assignment_with_untrusted_native_target_uses_safe_fallback(
    url, course_id, reason, browser_available
):
    page = MockPage()
    page.run_task = MagicMock()
    view = DetailView(page, lambda: None, get_client=lambda: object())

    view.update_detail(
        {
            "url": url,
            "course_id": course_id,
            "type": "assignment",
            "details": {},
        }
    )

    assert view._submission_status_value.value == reason
    assert view._open_btn.visible is browser_available
    assert view._header_open_btn.visible is browser_available
    page.run_task.assert_not_called()
    assert view._submission_area.visible is False
    assert view._pick_btn.visible is False
    assert view._submit_btn.visible is False
    assert view._finalize_btn.visible is False


def test_thnn_assignment_uses_native_loading_when_the_client_is_bound_to_thnn():
    class Workflow:
        def load_snapshot(self, target, prefetched_status=None):
            return SubmissionSnapshotResult.success(snapshot())

    page = MockPage()
    page.run_task = MagicMock()
    client = SimpleNamespace(
        moodle_site_origin="https://thnn.ut.edu.vn",
        has_site_credentials=True,
    )
    view = DetailView(
        page,
        lambda: None,
        get_client=lambda: client,
        submission_workflow_factory=lambda _: Workflow(),
    )

    view.update_detail(
        {
            "url": "https://thnn.ut.edu.vn/mod/assign/view.php?id=123",
            "course_id": 456,
            "type": "assignment",
            "details": {},
        }
    )

    page.run_task.assert_called_once()


def test_thnn_assignment_with_courses_client_uses_browser_fallback():
    page = MockPage()
    page.run_task = MagicMock()
    client = SimpleNamespace(
        moodle_site_origin="https://courses.ut.edu.vn",
        has_site_credentials=True,
    )
    view = DetailView(page, lambda: None, get_client=lambda: client)

    view.update_detail(
        {
            "url": "https://thnn.ut.edu.vn/mod/assign/view.php?id=123",
            "course_id": 456,
            "type": "assignment",
            "details": {},
        }
    )

    assert view._submission_status_value.value == (
        "Moodle cho trang bài tập này chưa được cấu hình. "
        "Hãy mở bài tập trong trình duyệt."
    )
    assert view._cta_text.value == "Mở trong trình duyệt"
    assert view._submission_area.visible is False
    page.run_task.assert_not_called()


@pytest.mark.parametrize(
    "origin",
    ("https://courses.ut.edu.vn", "https://thnn.ut.edu.vn"),
)
def test_app_controller_binds_real_workflow_to_the_current_moodle_client(
    origin, monkeypatch
):
    server = FakeMoodle43(drafts=True, statement=False)
    server.moodle_site_origin = origin
    controller = AppController.__new__(AppController)
    controller.orchestrator = SimpleNamespace(client=server)

    monkeypatch.setattr("gui.app_controller.settings.MOODLE_BASE_URL", origin)
    workflow = controller._submission_workflow_factory(server)
    result = workflow.load_snapshot(
        SubmissionTarget(f"{origin}/mod/assign/view.php?id=123", 456)
    )

    assert result.ok is True
    assert workflow.client is server
    assert workflow.moodle_service.call_ws_api == server.call_ws_api

    page = MockPage()
    page.run_task = MagicMock()
    view = DetailView(
        page,
        lambda: None,
        get_client=lambda: server,
        submission_workflow_factory=controller._submission_workflow_factory,
    )
    view.update_detail(
        {
            "url": f"{origin}/mod/assign/view.php?id=123",
            "course_id": 456,
            "type": "assignment",
            "details": {},
        }
    )

    page.run_task.assert_called_once()


def test_app_controller_never_builds_workflow_for_a_stale_or_other_site_client():
    current = FakeMoodle43(drafts=True, statement=False)
    current.moodle_site_origin = "https://courses.ut.edu.vn"
    stale = FakeMoodle43(drafts=True, statement=False)
    stale.moodle_site_origin = "https://thnn.ut.edu.vn"
    controller = AppController.__new__(AppController)
    controller.orchestrator = SimpleNamespace(client=current)

    assert controller._submission_workflow_factory(stale) is None


@pytest.mark.parametrize(
    "configured_base",
    ("https://thnn.ut.edu.vn", "https://moodle.example.edu"),
)
def test_app_controller_rejects_current_client_after_configured_moodle_site_changes(
    configured_base,
):
    current = FakeMoodle43(drafts=True, statement=False)
    current.moodle_site_origin = "https://courses.ut.edu.vn"
    controller = AppController.__new__(AppController)
    controller.orchestrator = SimpleNamespace(client=current)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "gui.app_controller.settings.MOODLE_BASE_URL",
            configured_base,
        )

        assert controller._submission_workflow_factory(current) is None

        page = MockPage()
        page.run_task = MagicMock()
        view = DetailView(
            page,
            lambda: None,
            get_client=lambda: current,
            submission_workflow_factory=controller._submission_workflow_factory,
        )
        view.update_detail(
            {
                "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
                "course_id": 456,
                "type": "assignment",
                "details": {},
            }
        )

    assert view._cta_text.value == "Mở trong trình duyệt"
    assert view._submission_area.visible is False
    page.run_task.assert_not_called()


def test_bound_client_without_workflow_factory_uses_immediate_browser_fallback():
    page = MockPage()
    page.run_task = MagicMock()
    client = SimpleNamespace(
        moodle_site_origin="https://courses.ut.edu.vn",
        has_site_credentials=True,
    )
    view = DetailView(page, lambda: None, get_client=lambda: client)

    view.update_detail(
        {
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
            "course_id": 456,
            "type": "assignment",
            "details": {},
        }
    )

    assert view._submission_status_value.value == (
        "Moodle cho trang bài tập này chưa được cấu hình. "
        "Hãy mở bài tập trong trình duyệt."
    )
    page.run_task.assert_not_called()


def test_detail_view_rejects_assignment_urls_with_explicit_https_port():
    assert DetailView._is_native_submission_url(
        "https://courses.ut.edu.vn:443/mod/assign/view.php?id=123"
    ) is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1),
        ("456", 456),
        (True, None),
        (1.5, None),
        (-1.5, None),
        (float("inf"), None),
        (float("nan"), None),
        ("1.5", None),
        ("+1", None),
        (" 1", None),
    ],
)
def test_positive_course_id_accepts_only_canonical_positive_integers(
    value, expected
):
    assert DetailView._positive_course_id(value) == expected


def test_non_assignment_does_not_mount_submission_status_row():
    view = DetailView(MockPage(), lambda: None)
    view.update_detail(
        {
            "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=123",
            "course_id": 456,
            "type": "quiz",
            "details": {
                "status_data": {"Submission status": "No submission"}
            },
        }
    )

    labels = [
        row.controls[0].value
        for section in view._content_col.controls
        if section.content.controls[0].value == "TRẠNG THÁI"
        for row in section.content.controls[1].content.controls
    ]
    assert "Trạng thái nộp bài" not in labels


@pytest.mark.anyio
async def test_late_mutation_updates_dashboard_but_never_current_detail_view():
    started = threading.Event()
    release = threading.Event()
    callbacks = []
    server_a = snapshot(
        files=(remote("a-result.pdf"),),
        raw_status="submitted",
        can_edit=False,
    )

    class SlowWorkflow:
        def mutate_files(self, target, intent, *, selected_files=()):
            assert selected_files == (
                SelectedSubmissionFile("a-new.pdf", b"A-new"),
            )
            started.set()
            assert release.wait(5)
            return SubmissionMutationResult.success(
                server_a, MutationOutcome.SUBMITTED_FOR_GRADING
            )

    page = MockPage()
    view = DetailView(
        page,
        lambda: None,
        get_client=lambda: object(),
        on_status_changed=lambda url, status: callbacks.append((url, status)),
        submission_workflow_factory=lambda _: SlowWorkflow(),
    )
    url_a = "https://courses.ut.edu.vn/mod/assign/view.php?id=101"
    url_b = "https://courses.ut.edu.vn/mod/assign/view.php?id=202"
    view.update_detail({"url": url_a, "course_id": 1, "type": "other"})
    view._apply_submission_snapshot(snapshot(files=(remote("a-old.pdf"),)))
    view._selected_files = [
        SimpleNamespace(
            name="a-new.pdf", size=5, path=None, bytes=b"A-new", mime_type="application/pdf"
        )
    ]

    mutation = asyncio.create_task(
        view._execute_file_mutation(MutationOperation.ADD, False)
    )
    assert await asyncio.to_thread(started.wait, 2)

    view.update_detail({"url": url_b, "course_id": 2, "type": "other"})
    server_b = snapshot(
        assignment_id=202,
        files=(remote("b-current.pdf"),),
        submission_id=202,
    )
    view._apply_submission_snapshot(server_b)
    pending_b = SimpleNamespace(
        name="b-new.pdf", size=5, path=None, bytes=b"B-new", mime_type="application/pdf"
    )
    view._selected_files = [pending_b]
    view._is_uploading = True
    view._render_submission_policy()

    release.set()
    await mutation

    assert view._current_url == url_b
    assert view._submission_snapshot is server_b
    assert [item["name"] for item in view._submitted_files] == ["b-current.pdf"]
    assert view._selected_files == [pending_b]
    assert view._is_uploading is True
    assert view._pick_btn.visible is False
    assert callbacks[-1] == (url_a, "Đã nộp")

class MockPage:
    def __init__(self):
        self.controls = []
        self.views = []
        self.window = MagicMock()
        self.on_disconnect = None
        self.on_keyboard_event = None
        self.on_view_pop = None
    
    def add(self, *args, **kwargs):
        pass

    def update(self):
        pass

    def run_task(self, func, *args, **kwargs):
        if asyncio.iscoroutinefunction(func):
            loop = asyncio.get_event_loop()
            return loop.create_task(func(*args, **kwargs))
        else:
            return func(*args, **kwargs)



@pytest.mark.anyio
async def test_app_controller_submission_race_condition():
    # Khởi tạo Mock Page
    page = MockPage()
    
    # Patch config để tránh load settings từ file thật
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("config.settings.UTH_USERNAME", "test_user")
        mp.setattr("config.settings.UTH_PASSWORD", "test_pass")
        mp.setattr("config.settings.BACKGROUND_CHECK_ANDROID", False)
        
        controller = AppController(page)
        
        # Thiết lập dữ liệu ban đầu
        url = "https://courses.ut.edu.vn/mod/assign/view.php?id=123"
        controller.all_data = [
            {
                "url": url,
                "title": "Bài tập test",
                "submission_status": "Chưa nộp",
                "deadline": "2026-12-31 23:59:59"
            }
        ]
        
        # Giả lập Moodle API trả về trạng thái cũ "Chưa nộp" chậm hơn
        # Mock orchestrator.get_latest_activities_async
        async def mock_get_latest():
            await asyncio.sleep(0.1) # Trễ mạng
            return [
                {
                    "url": url,
                    "title": "Bài tập test",
                    "submission_status": "Chưa nộp",
                    "deadline": "2026-12-31 23:59:59"
                }
            ]
        
        controller.orchestrator.get_latest_activities_async = mock_get_latest
        async def mock_outcome():
            from core.sync_coordinator import FetchOutcome

            return FetchOutcome.complete(await mock_get_latest())

        controller.orchestrator.fetch_activity_outcome_async = mock_outcome
        
        # 1. Kích hoạt cập nhật ngầm (sẽ gọi mock_get_latest)
        refresh_task = asyncio.create_task(controller._load_data_async())
        
        # Chờ 0.02s để luồng ngầm đã bắt đầu thời gian fetch
        await asyncio.sleep(0.02)
        
        # 2. Người dùng nộp bài tập (trạng thái chuyển thành "Đã nộp")
        controller._on_activity_status_changed(url, "Đã nộp")
        assert controller.all_data[0]["submission_status"] == "Đã nộp"
        
        # 3. Chờ tiến trình fetch nền hoàn thành hoàn toàn
        await refresh_task
        
        # 4. Kiểm tra xem Smart Merge có hoạt động chính xác để giữ trạng thái "Đã nộp" hay không
        assert controller.all_data[0]["submission_status"] == "Đã nộp"
