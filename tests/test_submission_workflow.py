from dataclasses import replace
from pathlib import Path

import pytest

from core.client import DraftFileRecord
from core.submission_models import (
    FileMutationIntent,
    MutationOperation,
    RemoteFile,
    SelectedFile,
    SubmissionSnapshot,
)
from core.use_cases.submission_workflow import (
    FileMetadataUpdate,
    FinalizeSubmissionIntent,
    MutationOutcome,
    SelectedSubmissionFile,
    SubmittedFile,
    SubmissionErrorCode,
    SubmissionTarget,
    SubmissionWorkflow,
)
from core.ws_functions import MoodleActionResult, MoodleWarning


ASSIGN_URL = "https://courses.ut.edu.vn/mod/assign/view.php?id=123"
THNN_ASSIGN_URL = "https://thnn.ut.edu.vn/mod/assign/view.php?id=123"


def target() -> SubmissionTarget:
    return SubmissionTarget(ASSIGN_URL, 456)


def remote(name: str, content: bytes | None = None, filepath: str = "/") -> RemoteFile:
    payload = content if content is not None else Path(name).stem.encode()
    return RemoteFile(
        name=name,
        filepath=filepath,
        size=len(payload),
        mimetype="application/pdf",
        modified_time=1_700_000_001,
        url=f"https://files/{filepath.strip('/')}/{name}",
    )


def editable_snapshot(**changes) -> SubmissionSnapshot:
    base = SubmissionSnapshot(
        assignment_id=77,
        raw_status="draft",
        can_edit=True,
        can_submit=True,
        locked=False,
        graded=False,
        submissions_enabled=True,
        submission_drafts=False,
        statement_required=False,
        file_submission_enabled=True,
        team_submission=False,
        due_date=0,
        cutoff_date=0,
        allows_submissions_from_date=0,
        maximum_file_count=5,
        maximum_file_bytes=1_048_576,
        accepted_file_types=(".pdf",),
        remote_files=(remote("old.pdf", b"old"),),
        online_text="<p>Keep me</p>",
        online_text_format=1,
        attempt_number=2,
        submission_id=333,
        submission_modified_time=1_700_000_000,
    )
    return replace(base, **changes)


def assignment_mapping(snapshot: SubmissionSnapshot) -> dict:
    return {
        "id": snapshot.assignment_id,
        "nosubmissions": not snapshot.submissions_enabled,
        "submissiondrafts": int(snapshot.submission_drafts),
        "teamsubmission": int(snapshot.team_submission),
        "duedate": snapshot.due_date,
        "cutoffdate": snapshot.cutoff_date,
        "allowsubmissionsfromdate": snapshot.allows_submissions_from_date,
        "configs": [
            {"subtype": "assign", "plugin": "assign", "name": "submissiondrafts", "value": int(snapshot.submission_drafts)},
            {"subtype": "assign", "plugin": "assign", "name": "requiresubmissionstatement", "value": int(snapshot.statement_required)},
            {"subtype": "assignsubmission", "plugin": "file", "name": "maxfilesubmission", "value": snapshot.maximum_file_count},
            {"subtype": "assignsubmission", "plugin": "file", "name": "maxsubmissionsizebytes", "value": snapshot.maximum_file_bytes},
            {"subtype": "assignsubmission", "plugin": "file", "name": "acceptedfiletypes", "value": ",".join(snapshot.accepted_file_types)},
            {"subtype": "assignsubmission", "plugin": "file", "name": "enabled", "value": int(snapshot.file_submission_enabled)},
        ],
    }


def status_mapping(snapshot: SubmissionSnapshot) -> dict:
    return {
        "lastattempt": {
            "submission": {
                "id": snapshot.submission_id,
                "status": snapshot.raw_status,
                "timemodified": snapshot.submission_modified_time,
                "plugins": [
                    {
                        "type": "file",
                        "fileareas": [{"files": [{
                            "filename": item.name,
                            "filepath": item.filepath,
                            "filesize": item.size,
                            "mimetype": item.mimetype,
                            "timemodified": item.modified_time,
                            "fileurl": item.url,
                        } for item in snapshot.remote_files]}],
                    },
                    {"type": "onlinetext", "editorfields": [{
                        "name": "onlinetext",
                        "text": snapshot.online_text,
                        "format": snapshot.online_text_format,
                    }]},
                ],
            },
            "attemptnumber": snapshot.attempt_number,
            "canedit": snapshot.can_edit,
            "cansubmit": snapshot.can_submit,
            "locked": snapshot.locked,
            "graded": snapshot.graded,
            "submissionsenabled": snapshot.submissions_enabled,
        }
    }


class Upload:
    def __init__(self, name: str, content: bytes, itemid: int, filepath: str):
        self.name = name
        self.content = content
        self.itemid = itemid
        self.filepath = filepath


class FakeClient:
    def __init__(
        self,
        snapshot: SubmissionSnapshot,
        moodle_site_origin: str = "https://courses.ut.edu.vn",
    ):
        self.downloads = {item.url: Path(item.name).stem.encode() for item in snapshot.remote_files}
        self.uploads: list[Upload] = []
        self.downloads_requested: list[str] = []
        self.fail_upload_at = 0
        self.moodle_site_origin = moodle_site_origin

    def download_file(self, url: str):
        self.downloads_requested.append(url)
        return self.downloads.get(url)

    def upload_draft_file_record(self, name: str, content: bytes, itemid: int, filepath: str):
        self.uploads.append(Upload(name, content, itemid, filepath))
        if self.fail_upload_at == len(self.uploads):
            return None
        return DraftFileRecord(itemid=itemid, filepath=filepath, filename=name)

    def call_ws_api(self, *args, **kwargs):
        raise AssertionError("The fake service owns WS calls")


class FakeService:
    def __init__(
        self,
        snapshot: SubmissionSnapshot,
        client: FakeClient,
        *,
        save_result: MoodleActionResult | None = None,
        finalize_result: MoodleActionResult | None = None,
        verified_files: tuple[RemoteFile, ...] | None = None,
        post_save_can_submit: bool | None = None,
        post_save_raw_status: str | None = None,
        fail_status_calls: tuple[int, ...] = (),
    ):
        self.initial = snapshot
        self.current = snapshot
        self.client = client
        self.save_result = save_result or MoodleActionResult(ok=True)
        self.finalize_result = finalize_result or MoodleActionResult(ok=True)
        self.verified_files = verified_files
        self.post_save_can_submit = post_save_can_submit
        self.post_save_raw_status = post_save_raw_status
        self.fail_status_calls = set(fail_status_calls)
        self.allocated_draft_id = 900
        self._allocations = iter((900, 901, 902, 903))
        self.saved: list[tuple[int, int]] = []
        self.saved_payloads: list[tuple[int, int, str, int, int]] = []
        self.finalized: list[tuple[int, bool]] = []
        self.cleaned: list[tuple[int, tuple[tuple[str, str], ...]]] = []
        self.status_calls = 0
        self.assignment_calls = 0
        self.resolve_calls = 0
        self.allocation_calls = 0

    def resolve_cmid_to_assign_id(self, cmid: int, course_id: int):
        self.resolve_calls += 1
        return 77 if (cmid, course_id) == (123, 456) else None

    def get_assignments(self, course_ids: list[int]):
        self.assignment_calls += 1
        assert course_ids == [456]
        return {"courses": [{"id": 456, "assignments": [assignment_mapping(self.current)]}]}

    def get_submission_status(self, assign_id: int):
        self.status_calls += 1
        assert assign_id == 77
        if self.status_calls in self.fail_status_calls:
            raise RuntimeError("synthetic refresh failure")
        return status_mapping(self.current)

    def get_unused_draft_itemid(self):
        self.allocation_calls += 1
        return next(self._allocations)

    def save_assignment_submission_result(self, assign_id, draft_id, text, text_format, text_draft_id):
        self.saved.append((assign_id, draft_id))
        self.saved_payloads.append((assign_id, draft_id, text, text_format, text_draft_id))
        if self.save_result.ok:
            files = self.verified_files
            if files is None:
                files = tuple(
                    remote(item.name, item.content, item.filepath)
                    for item in self.client.uploads
                )
            self.current = replace(
                self.current,
                raw_status=(
                    self.post_save_raw_status
                    if self.post_save_raw_status is not None
                    else "draft" if self.current.submission_drafts else "submitted"
                ),
                can_submit=self.current.can_submit if self.post_save_can_submit is None else self.post_save_can_submit,
                remote_files=files,
                submission_modified_time=self.current.submission_modified_time + 1,
            )
        return self.save_result

    def submit_for_grading_result(self, assign_id: int, accept: bool):
        self.finalized.append((assign_id, accept))
        if self.finalize_result.ok:
            self.current = replace(self.current, raw_status="submitted", can_edit=False)
        return self.finalize_result

    def delete_draft_files(self, itemid: int, identities):
        identities = tuple(identities)
        self.cleaned.append((itemid, identities))
        return True


def workflow_fixture(
    snapshot=None,
    *,
    site_origin="https://courses.ut.edu.vn",
    client_site_origin=None,
    **kwargs,
):
    snapshot = snapshot or editable_snapshot()
    client = FakeClient(snapshot, client_site_origin or site_origin)
    service = FakeService(snapshot, client, **kwargs)
    return SubmissionWorkflow(client, service, site_origin=site_origin), client, service


def local_file(tmp_path, name="new.pdf", content=b"new", filepath="/") -> SelectedFile:
    path = tmp_path / name
    path.write_bytes(content)
    return SelectedFile(name=name, size=len(content), mimetype="application/pdf", filepath=filepath, source_path=str(path))


def intent(operation, *, selected=(), remove=(), rename=None, name="", filepath="/", finalize=False, accept=False, fingerprint=""):
    return FileMutationIntent(
        operation=operation,
        selected_files=tuple(selected),
        remove_identities=tuple(remove),
        rename_identity=rename,
        new_name=name,
        new_filepath=filepath,
        finalize=finalize,
        accept_statement=accept,
        expected_fingerprint=fingerprint,
    )


@pytest.mark.parametrize(
    "drift",
    [
        {"online_text": "appeared between reads"},
        {"submission_drafts": False},
    ],
)
def test_live_safety_drift_aborts_before_upload_or_save(tmp_path, drift):
    displayed = editable_snapshot(
        raw_status="new",
        submission_drafts=True,
        remote_files=(),
        online_text="",
        accepted_file_types=(".txt",),
    )
    workflow, client, service = workflow_fixture(snapshot=displayed)
    service.current = replace(displayed, **drift)
    selected = local_file(tmp_path, "probe.txt", b"synthetic")

    result = workflow.mutate_files(
        target(),
        intent(
            MutationOperation.ADD,
            selected=(selected,),
            fingerprint=displayed.fingerprint,
        ),
        safety_guard=lambda snapshot: (
            snapshot.raw_status == "new"
            and snapshot.submission_drafts
            and not snapshot.online_text
        ),
    )

    assert result.issue.code is SubmissionErrorCode.STALE_SNAPSHOT
    assert client.uploads == []
    assert service.saved == []


def test_safety_guard_checks_fresh_workflow_snapshot_before_any_file_io(tmp_path):
    current = editable_snapshot(remote_files=(), online_text="")
    workflow, client, service = workflow_fixture(snapshot=current)
    selected = local_file(tmp_path)
    observed = []

    result = workflow.mutate_files(
        target(),
        intent(MutationOperation.ADD, selected=(selected,)),
        safety_guard=lambda snapshot: observed.append(snapshot) or False,
    )

    assert observed == [service.current]
    assert result.issue.code is SubmissionErrorCode.STALE_SNAPSHOT
    assert client.downloads_requested == []
    assert client.uploads == []
    assert service.saved == []


def test_load_snapshot_resolves_config_and_uses_prefetched_status_for_display():
    workflow, _, service = workflow_fixture()
    prefetched = status_mapping(replace(service.initial, raw_status="submitted"))

    result = workflow.load_snapshot(target(), prefetched_status=prefetched)

    assert result.ok is True
    assert result.snapshot.raw_status == "submitted"
    assert service.resolve_calls == service.assignment_calls == 1
    assert service.status_calls == 0


def test_add_rebuilds_existing_and_new_files_without_finalizing_non_draft(tmp_path):
    workflow, client, service = workflow_fixture(snapshot=editable_snapshot(submission_drafts=False))

    result = workflow.mutate_files(target(), intent(MutationOperation.ADD, selected=(local_file(tmp_path),)))

    assert result.ok is True
    assert result.outcome is MutationOutcome.SUBMISSION_SAVED
    assert [upload.name for upload in client.uploads] == ["old.pdf", "new.pdf"]
    assert len(service.saved) == 1
    assert service.finalized == []
    assert result.snapshot.remote_identities == (("/", "new.pdf"), ("/", "old.pdf"))


def test_draft_assignment_finalizes_only_when_requested_and_statement_accepted(tmp_path):
    workflow, _, service = workflow_fixture(snapshot=editable_snapshot(submission_drafts=True, statement_required=True))

    result = workflow.mutate_files(target(), intent(
        MutationOperation.REPLACE,
        selected=(local_file(tmp_path),),
        finalize=True,
        accept=True,
    ))

    assert result.ok is True
    assert result.outcome is MutationOutcome.SUBMITTED_FOR_GRADING
    assert service.finalized == [(77, True)]


def test_draft_assignment_can_be_saved_without_finalizing(tmp_path):
    workflow, _, service = workflow_fixture(snapshot=editable_snapshot(submission_drafts=True))

    result = workflow.mutate_files(target(), intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),)))

    assert result.ok is True
    assert result.outcome is MutationOutcome.DRAFT_SAVED
    assert result.snapshot.raw_status == "draft"
    assert service.finalized == []


def test_expected_fingerprint_conflict_stops_before_download_or_upload(tmp_path):
    workflow, client, service = workflow_fixture()

    result = workflow.mutate_files(target(), intent(MutationOperation.ADD, selected=(local_file(tmp_path),), fingerprint="stale"))

    assert result.issue.code is SubmissionErrorCode.STALE_SNAPSHOT
    assert client.downloads_requested == []
    assert client.uploads == []
    assert service.saved == []


def test_server_drift_during_draft_upload_aborts_immediately_before_save_and_cleans_exact_files(
    tmp_path,
):
    displayed = editable_snapshot()
    workflow, client, service = workflow_fixture(snapshot=displayed)
    original_upload = client.upload_draft_file_record

    def upload_then_change_server(name, content, itemid, filepath):
        record = original_upload(name, content, itemid, filepath)
        if len(client.uploads) == 1:
            service.current = replace(
                displayed,
                online_text="<p>concurrent authoritative text</p>",
                submission_modified_time=displayed.submission_modified_time + 7,
            )
        return record

    client.upload_draft_file_record = upload_then_change_server

    result = workflow.mutate_files(
        target(),
        intent(
            MutationOperation.ADD,
            selected=(local_file(tmp_path),),
            fingerprint=displayed.fingerprint,
        ),
    )

    assert result.ok is False
    assert result.issue.code is SubmissionErrorCode.STALE_SNAPSHOT
    assert result.snapshot == service.current
    assert service.saved == []
    assert service.cleaned == [
        (900, (("/", "old.pdf"), ("/", "new.pdf")))
    ]


def test_permission_drift_during_upload_returns_precise_fresh_failure_and_cleans(
    tmp_path,
):
    displayed = editable_snapshot(remote_files=())
    workflow, client, service = workflow_fixture(snapshot=displayed)
    original_upload = client.upload_draft_file_record

    def upload_then_lock(name, content, itemid, filepath):
        record = original_upload(name, content, itemid, filepath)
        service.current = replace(displayed, locked=True, can_edit=False)
        return record

    client.upload_draft_file_record = upload_then_lock

    result = workflow.mutate_files(
        target(),
        intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),)),
    )

    assert result.issue.code is SubmissionErrorCode.LOCKED
    assert result.snapshot.locked is True
    assert service.saved == []
    assert service.cleaned == [(900, (("/", "new.pdf"),))]


def test_assignment_config_drift_during_upload_is_included_in_pre_save_fingerprint(
    tmp_path,
):
    displayed = editable_snapshot(remote_files=())
    workflow, client, service = workflow_fixture(snapshot=displayed)
    original_upload = client.upload_draft_file_record

    def upload_then_change_limit(name, content, itemid, filepath):
        record = original_upload(name, content, itemid, filepath)
        service.current = replace(
            displayed,
            maximum_file_count=displayed.maximum_file_count + 1,
        )
        return record

    client.upload_draft_file_record = upload_then_change_limit

    result = workflow.mutate_files(
        target(),
        intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),)),
    )

    assert result.issue.code is SubmissionErrorCode.STALE_SNAPSHOT
    assert result.snapshot.maximum_file_count == displayed.maximum_file_count + 1
    assert service.assignment_calls == 2
    assert service.saved == []
    assert service.cleaned == [(900, (("/", "new.pdf"),))]


def test_safety_guard_is_repeated_immediately_before_save_and_cleans_tracked_files(
    tmp_path,
):
    displayed = editable_snapshot(remote_files=())
    workflow, client, service = workflow_fixture(snapshot=displayed)
    guard_results = iter((True, False))
    observed = []

    result = workflow.mutate_files(
        target(),
        intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),)),
        safety_guard=lambda fresh: observed.append(fresh) or next(guard_results),
    )

    assert result.issue.code is SubmissionErrorCode.STALE_SNAPSHOT
    assert observed == [displayed, displayed]
    assert service.saved == []
    assert service.cleaned == [(900, (("/", "new.pdf"),))]


@pytest.mark.parametrize(
    "file_intent",
    [
        intent(MutationOperation.ADD, selected=(SelectedFile("new.pdf", 3),)),
        intent(MutationOperation.REPLACE, selected=(SelectedFile("new.pdf", 3),)),
        intent(MutationOperation.REMOVE, remove=(("/", "old.pdf"),)),
        intent(MutationOperation.CLEAR),
        intent(
            MutationOperation.RENAME,
            rename=("/", "old.pdf"),
            name="renamed.pdf",
        ),
        intent(
            MutationOperation.RENAME,
            rename=("/", "old.pdf"),
            name="old.pdf",
            filepath="moved",
        ),
    ],
    ids=("add", "replace", "remove", "clear", "rename", "move"),
)
def test_file_submission_capability_gates_every_file_mutation_before_io(file_intent):
    workflow, client, service = workflow_fixture(
        snapshot=editable_snapshot(file_submission_enabled=False)
    )

    result = workflow.mutate_files(target(), file_intent)

    assert result.issue.code is SubmissionErrorCode.UNSUPPORTED_OPERATION
    assert client.downloads_requested == []
    assert client.uploads == []
    assert service.allocation_calls == 0
    assert service.saved == []


@pytest.mark.parametrize(
    "operation",
    (
        MutationOperation.ADD,
        MutationOperation.REPLACE,
        MutationOperation.REMOVE,
        MutationOperation.CLEAR,
        MutationOperation.RENAME,
    ),
)
def test_team_submission_is_browser_only_for_file_mutations(operation, tmp_path):
    workflow, client, service = workflow_fixture(
        snapshot=editable_snapshot(team_submission=True)
    )
    kwargs = {}
    if operation in (MutationOperation.ADD, MutationOperation.REPLACE):
        kwargs["selected"] = (local_file(tmp_path),)
    elif operation is MutationOperation.REMOVE:
        kwargs["remove"] = (("/", "old.pdf"),)
    elif operation is MutationOperation.RENAME:
        kwargs.update(rename=("/", "old.pdf"), name="renamed.pdf")

    result = workflow.mutate_files(target(), intent(operation, **kwargs))

    assert result.issue.code is SubmissionErrorCode.UNSUPPORTED_OPERATION
    assert client.downloads_requested == []
    assert client.uploads == []
    assert service.allocation_calls == 0
    assert service.saved == []


def test_online_text_only_draft_finalizes_without_file_save_or_upload():
    draft = editable_snapshot(
        raw_status="draft",
        submission_drafts=True,
        statement_required=True,
        file_submission_enabled=False,
        remote_files=(),
        online_text="<p>Online answer</p>",
    )
    workflow, client, service = workflow_fixture(snapshot=draft)

    result = workflow.finalize_submission(
        target(),
        FinalizeSubmissionIntent(
            accept_statement=True,
            expected_fingerprint=draft.fingerprint,
        ),
    )

    assert result.ok is True
    assert result.outcome is MutationOutcome.SUBMITTED_FOR_GRADING
    assert result.snapshot.raw_status == "submitted"
    assert client.downloads_requested == []
    assert client.uploads == []
    assert service.allocation_calls == 0
    assert service.saved == []
    assert service.finalized == [(77, True)]
    assert service.status_calls == 3


def test_finalize_only_verifies_current_draft_content_was_not_replaced():
    draft = editable_snapshot(
        raw_status="draft",
        submission_drafts=True,
        remote_files=(),
        online_text="<p>Expected online answer</p>",
    )
    workflow, _, service = workflow_fixture(snapshot=draft)
    original_finalize = service.submit_for_grading_result

    def finalize_with_content_drift(assign_id, accept):
        result = original_finalize(assign_id, accept)
        service.current = replace(
            service.current,
            online_text="<p>Unexpected concurrent answer</p>",
        )
        return result

    service.submit_for_grading_result = finalize_with_content_drift

    result = workflow.finalize_submission(
        target(), FinalizeSubmissionIntent(expected_fingerprint=draft.fingerprint)
    )

    assert result.issue.code is SubmissionErrorCode.VERIFICATION_FAILED
    assert result.partial is True
    assert result.snapshot.online_text == "<p>Unexpected concurrent answer</p>"
    assert service.saved == []


@pytest.mark.parametrize(
    "url",
    (
        "http://courses.ut.edu.vn/mod/assign/view.php?id=123",
        "https://evil.example/mod/assign/view.php?id=123",
        "https://child.courses.ut.edu.vn/mod/assign/view.php?id=123",
        "https://courses.ut.edu.vn:444/mod/assign/view.php?id=123",
        "https://user:pass@courses.ut.edu.vn/mod/assign/view.php?id=123",
        "https://courses.ut.edu.vn/mod/assign/view.php/extra?id=123",
        "https://courses.ut.edu.vn/mod/quiz/view.php?id=123",
        "https://courses.ut.edu.vn/mod/assign/view.php?id=123&id=124",
        "https://courses.ut.edu.vn/mod/assign/view.php?id=123#fragment",
        "https://courses.ut.edu.vn/mod/assign/view.php?id=123&extra=1",
        "https://courses.ut.edu.vn/mod/assign/view.php?id=not-an-int",
    ),
)
def test_invalid_target_boundary_rejects_before_assignment_resolution(url):
    workflow, client, service = workflow_fixture()

    result = workflow.load_snapshot(SubmissionTarget(url, 456))

    assert result.issue.code is SubmissionErrorCode.INVALID_TARGET
    assert service.resolve_calls == 0
    assert service.assignment_calls == 0
    assert service.status_calls == 0
    assert client.downloads_requested == []
    assert client.uploads == []
    assert service.saved == []


def test_foreign_target_mutation_performs_zero_network_or_local_file_io(tmp_path):
    workflow, client, service = workflow_fixture()
    selected = local_file(tmp_path)

    result = workflow.mutate_files(
        SubmissionTarget(
            "https://attacker.example/mod/assign/view.php?id=123", 456
        ),
        intent(MutationOperation.REPLACE, selected=(selected,)),
    )

    assert result.issue.code is SubmissionErrorCode.INVALID_TARGET
    assert service.resolve_calls == 0
    assert service.assignment_calls == 0
    assert service.status_calls == 0
    assert service.allocation_calls == 0
    assert client.downloads_requested == []
    assert client.uploads == []
    assert service.saved == []


def test_target_boundary_rejects_explicit_https_port_even_when_effective():
    workflow, _, service = workflow_fixture()

    result = workflow.load_snapshot(
        SubmissionTarget(
            "https://COURSES.UT.EDU.VN:443/mod/assign/view.php?id=123",
            456,
        )
    )

    assert result.issue.code is SubmissionErrorCode.INVALID_TARGET
    assert service.resolve_calls == 0


@pytest.mark.parametrize(
    ("site_origin", "url"),
    (
        ("https://courses.ut.edu.vn", ASSIGN_URL),
        ("https://thnn.ut.edu.vn", THNN_ASSIGN_URL),
    ),
)
def test_target_boundary_accepts_the_selected_explicit_trusted_site(
    site_origin, url
):
    workflow, _, service = workflow_fixture(site_origin=site_origin)

    result = workflow.load_snapshot(SubmissionTarget(url, 456))

    assert result.ok is True
    assert service.resolve_calls == 1


def test_target_boundary_rejects_the_other_trusted_site_without_resolution():
    workflow, client, service = workflow_fixture(
        site_origin="https://courses.ut.edu.vn"
    )

    result = workflow.load_snapshot(SubmissionTarget(THNN_ASSIGN_URL, 456))

    assert result.issue.code is SubmissionErrorCode.INVALID_TARGET
    assert service.resolve_calls == 0
    assert client.downloads_requested == []


@pytest.mark.parametrize(
    "url",
    (
        "http://thnn.ut.edu.vn/mod/assign/view.php?id=123",
        "https://user:pass@thnn.ut.edu.vn/mod/assign/view.php?id=123",
        "https://child.thnn.ut.edu.vn/mod/assign/view.php?id=123",
        "https://thnn.ut.edu.vn/mod/assign/view.php/extra?id=123",
        "https://thnn.ut.edu.vn/mod/assign/view.php?id=123&id=124",
        "https://thnn.ut.edu.vn/mod/assign/view.php?id=123&extra=1",
        "https://thnn.ut.edu.vn/mod/assign/view.php?id=1.5",
        "https://thnn.ut.edu.vn/mod/assign/view.php?id=123#fragment",
    ),
)
def test_thnn_target_boundary_rejects_non_exact_assignment_urls(url):
    workflow, client, service = workflow_fixture(
        site_origin="https://thnn.ut.edu.vn"
    )

    result = workflow.load_snapshot(SubmissionTarget(url, 456))

    assert result.issue.code is SubmissionErrorCode.INVALID_TARGET
    assert service.resolve_calls == 0
    assert client.downloads_requested == []


def test_client_from_another_site_cannot_mutate_thnn_assignment(tmp_path):
    workflow, client, service = workflow_fixture(
        site_origin="https://thnn.ut.edu.vn",
        client_site_origin="https://courses.ut.edu.vn",
    )

    result = workflow.mutate_files(
        SubmissionTarget(THNN_ASSIGN_URL, 456),
        intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),)),
    )

    assert result.issue.code is SubmissionErrorCode.CLIENT_ORIGIN_MISMATCH
    assert service.resolve_calls == 0
    assert service.allocation_calls == 0
    assert client.downloads_requested == []
    assert client.uploads == []


def test_invalid_target_is_rejected_before_client_site_mismatch():
    workflow, client, service = workflow_fixture(
        site_origin="https://thnn.ut.edu.vn",
        client_site_origin="https://courses.ut.edu.vn",
    )

    result = workflow.load_snapshot(
        SubmissionTarget("https://attacker.example/mod/assign/view.php?id=123", 456)
    )

    assert result.issue.code is SubmissionErrorCode.INVALID_TARGET
    assert service.resolve_calls == 0
    assert client.downloads_requested == []


def test_remove_one_reuploads_only_remaining_remote_files():
    snapshot = editable_snapshot(remote_files=(remote("a.pdf"), remote("b.pdf")))
    workflow, client, service = workflow_fixture(snapshot=snapshot)

    result = workflow.mutate_files(target(), intent(MutationOperation.REMOVE, remove=(("/", "a.pdf"),)))

    assert result.ok is True
    assert [item.name for item in client.uploads] == ["b.pdf"]
    assert [item.name for item in result.snapshot.remote_files] == ["b.pdf"]
    assert service.saved


def test_clear_allocates_empty_draft_and_saves_it():
    workflow, client, service = workflow_fixture()

    result = workflow.mutate_files(target(), intent(MutationOperation.CLEAR))

    assert result.ok is True
    assert client.uploads == []
    assert service.saved == [(77, service.allocated_draft_id)]
    assert result.snapshot.remote_files == ()


def test_rename_changes_only_target_identity_and_preserves_bytes():
    snapshot = editable_snapshot(remote_files=(remote("a.pdf"), remote("b.pdf")))
    workflow, client, _ = workflow_fixture(snapshot=snapshot)

    result = workflow.mutate_files(target(), intent(
        MutationOperation.RENAME,
        rename=("/", "a.pdf"),
        name="renamed.pdf",
    ))

    assert result.ok is True
    assert [(item.name, item.content) for item in client.uploads] == [("renamed.pdf", b"a"), ("b.pdf", b"b")]


def test_move_normalizes_and_uploads_to_requested_path():
    workflow, client, _ = workflow_fixture()

    result = workflow.mutate_files(target(), intent(
        MutationOperation.RENAME,
        rename=("/", "old.pdf"),
        name="old.pdf",
        filepath="proof",
    ))

    assert result.ok is True
    assert client.uploads[0].filepath == "/proof/"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"locked": True}, SubmissionErrorCode.LOCKED),
        ({"graded": True}, SubmissionErrorCode.GRADED),
        ({"can_edit": False}, SubmissionErrorCode.NOT_EDITABLE),
        ({"submissions_enabled": False}, SubmissionErrorCode.SUBMISSIONS_CLOSED),
    ],
)
def test_permission_gates_never_upload(tmp_path, changes, code):
    workflow, client, _ = workflow_fixture(snapshot=editable_snapshot(**changes))

    result = workflow.mutate_files(target(), intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),)))

    assert result.issue.code is code
    assert client.uploads == []


def test_lastattempt_disabled_stops_mutation_even_when_assignment_allows_submissions(tmp_path):
    workflow, client, service = workflow_fixture(
        snapshot=editable_snapshot(submissions_enabled=False)
    )
    service.get_assignments = lambda course_ids: {
        "courses": [{
            "id": 456,
            "assignments": [{
                **assignment_mapping(service.current),
                "nosubmissions": False,
            }],
        }]
    }

    result = workflow.mutate_files(
        target(),
        intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),)),
    )

    assert result.issue.code is SubmissionErrorCode.SUBMISSIONS_CLOSED
    assert client.downloads_requested == []
    assert client.uploads == []
    assert service.saved == []


def test_duplicate_against_retained_remote_file_stops_before_download(tmp_path):
    workflow, client, service = workflow_fixture()

    result = workflow.mutate_files(target(), intent(
        MutationOperation.ADD,
        selected=(local_file(tmp_path, name="old.pdf", content=b"different"),),
    ))

    assert result.issue.code is SubmissionErrorCode.DUPLICATE_FILENAME
    assert client.downloads_requested == []
    assert service.saved == []


def test_nonzero_remote_size_mismatch_stops_before_upload_and_save():
    workflow, client, service = workflow_fixture()
    client.downloads[next(iter(client.downloads))] = b"wrong-size"

    result = workflow.mutate_files(target(), intent(MutationOperation.ADD))

    assert result.issue.code is SubmissionErrorCode.DOWNLOAD_SIZE_MISMATCH
    assert client.uploads == []
    assert service.saved == []


@pytest.mark.parametrize("invalid_content", [b"", "not bytes", bytearray(b"old")])
def test_invalid_or_empty_retained_download_stops_before_upload_and_save(invalid_content):
    zero_sized = remote("old.pdf", b"")
    workflow, client, service = workflow_fixture(
        snapshot=editable_snapshot(remote_files=(zero_sized,))
    )
    client.downloads[zero_sized.url] = invalid_content

    result = workflow.mutate_files(target(), intent(MutationOperation.ADD))

    assert result.issue.code is SubmissionErrorCode.DOWNLOAD_FAILED
    assert client.uploads == []
    assert service.saved == []


def test_partial_upload_failure_cleans_only_tracked_draft_files(tmp_path):
    workflow, client, service = workflow_fixture()
    client.fail_upload_at = 2

    result = workflow.mutate_files(target(), intent(MutationOperation.ADD, selected=(local_file(tmp_path),)))

    assert result.issue.code is SubmissionErrorCode.UPLOAD_FAILED
    assert service.cleaned == [(900, (("/", "old.pdf"),))]


def test_save_warning_is_failure_and_draft_is_not_reported_submitted(tmp_path):
    warning = MoodleActionResult(ok=False, warnings=(MoodleWarning("couldnotsavesubmission", "rejected"),))
    workflow, _, service = workflow_fixture(save_result=warning)

    result = workflow.mutate_files(target(), intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),)))

    assert result.issue.code is SubmissionErrorCode.SAVE_REJECTED
    assert [item.name for item in result.snapshot.remote_files] == ["old.pdf"]
    assert service.cleaned == []


def test_finalize_failure_returns_draft_saved_partial_outcome(tmp_path):
    workflow, _, _ = workflow_fixture(
        snapshot=editable_snapshot(submission_drafts=True),
        finalize_result=MoodleActionResult(ok=False, message="rejected"),
    )

    result = workflow.mutate_files(target(), intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),), finalize=True))

    assert result.ok is False
    assert result.partial is True
    assert result.issue.code is SubmissionErrorCode.FINALIZE_REJECTED
    assert result.snapshot.raw_status == "draft"


def test_post_save_file_mismatch_returns_server_snapshot(tmp_path):
    workflow, _, service = workflow_fixture(verified_files=(remote("other.pdf"),))

    result = workflow.mutate_files(target(), intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),)))

    assert result.issue.code is SubmissionErrorCode.VERIFICATION_FAILED
    assert result.snapshot.remote_names == ("other.pdf",)
    assert service.saved


def test_non_draft_requires_exact_submitted_status_after_save(tmp_path):
    workflow, _, service = workflow_fixture(post_save_raw_status="new")

    result = workflow.mutate_files(
        target(),
        intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),)),
    )

    assert result.issue.code is SubmissionErrorCode.VERIFICATION_FAILED
    assert result.snapshot.raw_status == "new"
    assert service.saved


def test_post_save_refresh_failure_does_not_return_stale_snapshot(tmp_path):
    workflow, _, service = workflow_fixture(fail_status_calls=(3,))

    result = workflow.mutate_files(
        target(),
        intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),)),
    )

    assert result.issue.code is SubmissionErrorCode.SNAPSHOT_LOAD_FAILED
    assert result.partial is True
    assert result.snapshot is None
    assert service.saved


def test_post_finalize_refresh_failure_does_not_return_stale_snapshot(tmp_path):
    workflow, _, service = workflow_fixture(
        snapshot=editable_snapshot(submission_drafts=True),
        fail_status_calls=(4,),
    )

    result = workflow.mutate_files(
        target(),
        intent(
            MutationOperation.REPLACE,
            selected=(local_file(tmp_path),),
            finalize=True,
        ),
    )

    assert result.issue.code is SubmissionErrorCode.SNAPSHOT_LOAD_FAILED
    assert result.partial is True
    assert result.snapshot is None
    assert service.saved
    assert service.finalized == [(77, False)]


def test_statement_required_without_acceptance_stops_before_finalize(tmp_path):
    workflow, _, service = workflow_fixture(snapshot=editable_snapshot(submission_drafts=True, statement_required=True))

    result = workflow.mutate_files(target(), intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),), finalize=True))

    assert result.issue.code is SubmissionErrorCode.STATEMENT_NOT_ACCEPTED
    assert result.partial is True
    assert service.finalized == []


def test_finalization_uses_refreshed_can_submit_flag(tmp_path):
    workflow, _, service = workflow_fixture(
        snapshot=editable_snapshot(submission_drafts=True, can_submit=False),
        post_save_can_submit=True,
    )

    result = workflow.mutate_files(target(), intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),), finalize=True))

    assert result.ok is True
    assert service.finalized == [(77, False)]


def test_file_mutation_preserves_online_text_and_format(tmp_path):
    workflow, _, service = workflow_fixture()

    workflow.mutate_files(target(), intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),)))

    assert service.saved_payloads[0][2:4] == ("<p>Keep me</p>", 1)


def test_mutation_resolves_assignment_only_once(tmp_path):
    workflow, _, service = workflow_fixture(snapshot=editable_snapshot(submission_drafts=True))

    workflow.mutate_files(target(), intent(MutationOperation.REPLACE, selected=(local_file(tmp_path),), finalize=True))

    assert service.resolve_calls == 1
    assert service.assignment_calls == 2
    assert service.status_calls == 4


def test_legacy_load_adapter_maps_verified_snapshot():
    workflow, _, _ = workflow_fixture(snapshot=editable_snapshot(raw_status="submitted"))

    result = workflow.load_submitted_files(target())

    assert result.last_server_status == "Đã nộp"
    assert result.files == [SubmittedFile(name="old.pdf", url="https://files//old.pdf", filepath="/")]


def test_legacy_submit_adapter_remains_boolean(tmp_path):
    workflow, _, _ = workflow_fixture()

    ok = workflow.submit_files(
        target(),
        [SelectedSubmissionFile("new.pdf", b"new")],
        [SubmittedFile("old.pdf", "https://files//old.pdf")],
        overwrite=True,
    )

    assert ok is True


def test_mutate_files_uses_selected_bytes_without_a_local_path():
    workflow, client, _ = workflow_fixture()
    content = b"exact picker bytes"
    selected = SelectedFile(
        name="new.pdf",
        size=len(content),
        mimetype="application/pdf",
        source_path="",
    )
    payload = SelectedSubmissionFile("new.pdf", content)

    result = workflow.mutate_files(
        target(),
        intent(MutationOperation.REPLACE, selected=(selected,)),
        selected_files=(payload,),
    )

    assert result.ok is True
    assert client.uploads[0].content == content
    assert "exact picker bytes" not in repr(payload)


def test_mutate_files_preserves_multiple_selected_byte_payloads_without_paths():
    workflow, client, _ = workflow_fixture()
    contents = (b"first exact bytes", b"second exact bytes")
    selected = tuple(
        SelectedFile(
            name=name,
            size=len(content),
            mimetype="application/pdf",
            source_path="",
        )
        for name, content in zip(("first.pdf", "second.pdf"), contents)
    )
    payloads = tuple(
        SelectedSubmissionFile(item.name, content)
        for item, content in zip(selected, contents)
    )

    result = workflow.mutate_files(
        target(),
        intent(MutationOperation.REPLACE, selected=selected),
        selected_files=payloads,
    )

    assert result.ok is True
    assert [upload.content for upload in client.uploads] == list(contents)


def test_selected_bytes_are_not_logged_when_upload_raises(caplog):
    workflow, client, _ = workflow_fixture()
    content = b"NEVER_LOG_THESE_PICKER_BYTES"
    selected = SelectedFile(
        name="new.pdf",
        size=len(content),
        mimetype="application/pdf",
        source_path="",
    )

    def fail_upload(name, body, itemid, filepath):
        raise RuntimeError(repr(body))

    client.upload_draft_file_record = fail_upload

    result = workflow.mutate_files(
        target(),
        intent(MutationOperation.REPLACE, selected=(selected,)),
        selected_files=(SelectedSubmissionFile("new.pdf", content),),
    )

    assert result.ok is False
    assert result.issue.code is SubmissionErrorCode.UPLOAD_FAILED
    assert "NEVER_LOG_THESE_PICKER_BYTES" not in caplog.text


def test_legacy_remove_adapter_keeps_empty_guard():
    workflow, _, _ = workflow_fixture()

    with pytest.raises(ValueError, match="không hỗ trợ xóa"):
        workflow.remove_files(target(), [])


def test_legacy_metadata_adapter_renames_target():
    workflow, client, _ = workflow_fixture()

    ok = workflow.update_file_metadata(
        target(),
        [SubmittedFile("old.pdf", "https://files//old.pdf")],
        0,
        FileMetadataUpdate(new_name="renamed.pdf", filepath="proof"),
    )

    assert ok is True
    assert (client.uploads[0].name, client.uploads[0].filepath) == ("renamed.pdf", "/proof/")
