import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core.use_cases.submission_workflow import (
    FileMetadataUpdate,
    SelectedSubmissionFile,
    SubmittedFile,
    SubmissionTarget,
    SubmissionWorkflow,
)


ASSIGN_URL = "https://courses.ut.edu.vn/mod/assign/view.php?id=123"


class _FakeClient:
    def __init__(self):
        self.downloads = {
            "https://files/old.txt": b"old",
            "https://files/keep.txt": b"keep",
        }
        self.uploads = []
        self.next_itemid = 10

    def call_ws_api(self, *args, **kwargs):
        return {"ok": True, "args": args, "kwargs": kwargs}

    def download_file(self, url: str):
        return self.downloads.get(url)

    def upload_draft_file(
        self,
        filename: str,
        file_bytes: bytes,
        itemid: int = 0,
        author: str = None,
        license_key: str = None,
    ):
        self.next_itemid += 1
        self.uploads.append(
            {
                "filename": filename,
                "bytes": file_bytes,
                "itemid": itemid,
                "author": author,
                "license_key": license_key,
                "result": self.next_itemid,
            }
        )
        return self.next_itemid


class _FakeMoodleService:
    def __init__(self):
        self.calls = {"saved": [], "submitted": []}

    def resolve_cmid_to_assign_id(self, cmid, course_id):
        return 77 if cmid == 123 and course_id == 456 else None

    def save_assignment_submission(self, assign_id, draft_itemid, onlinetext="", item_id_text=0):
        self.calls["saved"].append((assign_id, draft_itemid))
        return True

    def submit_for_grading(self, assign_id):
        self.calls["submitted"].append(assign_id)
        return True

    def get_submission_status(self, assign_id):
        return {"lastattempt": {"submission": {"status": "submitted"}}}

    def get_submitted_files(self, assign_id, status=None):
        return [{"name": "old.txt", "url": "https://files/old.txt"}]


@pytest.fixture
def moodle_service():
    return _FakeMoodleService()


def test_load_submitted_files_maps_server_status(moodle_service):
    client = _FakeClient()

    result = SubmissionWorkflow(client, moodle_service).load_submitted_files(SubmissionTarget(ASSIGN_URL, 456))

    assert result.last_server_status == "Đã nộp"
    assert result.files == [SubmittedFile(name="old.txt", url="https://files/old.txt")]


def test_submit_files_append_reuploads_existing_file_before_new_file(moodle_service):
    client = _FakeClient()

    ok = SubmissionWorkflow(client, moodle_service).submit_files(
        SubmissionTarget(ASSIGN_URL, 456),
        selected_files=[SelectedSubmissionFile("new.txt", b"new")],
        submitted_files=[SubmittedFile(name="old.txt", url="https://files/old.txt")],
        overwrite=False,
    )

    assert ok is True
    assert [upload["filename"] for upload in client.uploads] == ["old.txt", "new.txt"]
    assert moodle_service.calls["saved"] == [(77, client.uploads[-1]["result"])]
    assert moodle_service.calls["submitted"] == [77]


def test_submit_files_overwrite_skips_existing_files(moodle_service):
    client = _FakeClient()

    ok = SubmissionWorkflow(client, moodle_service).submit_files(
        SubmissionTarget(ASSIGN_URL, 456),
        selected_files=[SelectedSubmissionFile("new.txt", b"new")],
        submitted_files=[SubmittedFile(name="old.txt", url="https://files/old.txt")],
        overwrite=True,
    )

    assert ok is True
    assert [upload["filename"] for upload in client.uploads] == ["new.txt"]


def test_remove_files_rejects_empty_submission(moodle_service):
    client = _FakeClient()

    with pytest.raises(ValueError, match="không hỗ trợ xóa"):
        SubmissionWorkflow(client, moodle_service).remove_files(SubmissionTarget(ASSIGN_URL, 456), files_to_keep=[])


def test_update_file_metadata_reuploads_target_with_new_metadata(moodle_service):
    client = _FakeClient()

    ok = SubmissionWorkflow(client, moodle_service).update_file_metadata(
        SubmissionTarget(ASSIGN_URL, 456),
        submitted_files=[
            SubmittedFile(name="old.txt", url="https://files/old.txt"),
            SubmittedFile(name="keep.txt", url="https://files/keep.txt"),
        ],
        target_idx=0,
        meta=FileMetadataUpdate(
            new_name="renamed.txt",
            author="UTH Student",
            license="cc-4.0",
        ),
    )

    assert ok is True
    assert client.uploads[0]["filename"] == "renamed.txt"
    assert client.uploads[0]["author"] == "UTH Student"
    assert client.uploads[0]["license_key"] == "cc-4.0"
    assert client.uploads[1]["filename"] == "keep.txt"
