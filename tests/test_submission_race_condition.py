import os
import sys
import time
import asyncio
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
from core.use_cases.submission_workflow import (
    SubmissionError,
    SubmissionErrorCode,
    SubmissionMutationResult,
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
        "_confirm_replace_mutation",
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
