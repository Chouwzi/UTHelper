# Moodle Submission Workflow Implementation Plan

> **Archived:** Planning snapshot retained for provenance. It is not the current
> task tracker; use tests and current operator documentation as the source of truth.

**Goal:** Build a verified Moodle 4.3 assignment file state machine that safely submits one or many files, appends, replaces, removes, renames, synchronizes server state, and exposes only actions Moodle currently permits.

**Architecture:** Add immutable submission domain values and a parser that combines assignment configuration with `mod_assign_get_submission_status`; keep raw Moodle response interpretation at the WS boundary and orchestrate exact-set replacement in `SubmissionWorkflow`. The Flet view consumes a small pure presentation policy plus structured workflow results, refreshes from Moodle after every mutation, and never treats pending local files as submitted.

**Tech Stack:** Python 3.11 dataclasses/enums, existing urllib-based `MoodleClient`, Moodle 4.3 mobile web services, Flet 0.86.5, pytest, Ruff.

## Global Constraints

- Target only `https://courses.ut.edu.vn`; do not add or alter `thnn.ut.edu.vn` behavior.
- Never log or commit credentials, web-service tokens, authenticated download URLs, or user file contents.
- Never write-test an already-submitted, graded, locked, expired, near-due, or otherwise sensitive real assignment.
- The live suite must delete every generated draft/assignment test file in `finally`; an assignment write is eligible only when a fresh check proves it is empty, editable repeatedly, comfortably before its deadline, unlocked, and ungraded.
- Do not claim that this Moodle 4.3.5 service can remove a submission record; `mod_assign_remove_submission` is unavailable.
- Every HTTP request, process, poll, and test command has a finite timeout; no unbounded waits.
- Use TDD for every behavior: observe the focused test fail for the expected reason before implementation, then pass it.
- Preserve current online text when changing files.
- A mutation is successful only after a fresh status response verifies the exact remote file set and expected draft/final state.
- Update `REFAC_KNOWLEDGE.md` and rerun the complete suite and Ruff before and after merging to `main`.

---

## File map

- Create `src/core/submission_models.py`: immutable snapshots, file identities, intents, action results, error codes, and deterministic fingerprints.
- Create `src/core/submission_snapshot.py`: parse Moodle assignment configuration/status and validate file names, counts, sizes, and accepted types.
- Modify `src/core/ws_functions.py`: faithful structured save/finalize response parsing; no warning-as-success behavior.
- Modify `src/core/moodle_service.py`: expose assignment lookup, unused draft IDs, structured save/finalize methods, and draft cleanup.
- Modify `src/core/client.py`: filepath-aware multipart draft upload with a structured returned file record while retaining the legacy integer wrapper.
- Replace `src/core/use_cases/submission_workflow.py`: fresh-snapshot permission gate, optimistic conflict check, exact desired-set rebuild, cleanup, finalization, and post-save verification.
- Create `src/gui/components/detail/submission_presenter.py`: pure mapping from snapshots/results to control visibility, labels, and messages.
- Modify `src/gui/components/detail_view.py`: store the snapshot/fingerprint, collect explicit add/replace/draft/final intent, and replace UI state only with verified server state.
- Modify `src/gui/components/detail/submitted_files_table.py`: conditionally expose edit/delete/multi-select controls.
- Create `tests/fixtures/moodle_submission_responses.py`: synthetic Moodle 4.3 assignment/status response builders.
- Create `tests/test_submission_models.py`: parser, fingerprint, type, and constraint tests.
- Modify `tests/test_ws_functions.py` and `tests/test_ws_functions_extended.py`: warning/result contract tests.
- Replace `tests/test_submission_workflow.py`: state-machine unit tests.
- Create `tests/test_submission_protocol_integration.py`: stateful fake Moodle 4.3 protocol integration tests.
- Create `tests/test_submission_presenter.py`: GUI policy tests without a Flet window.
- Create `tests/test_submission_live_safe.py`: opt-in, bounded unlinked-draft checks plus one strictly gated empty-assignment write/delete probe.
- Modify `REFAC_KNOWLEDGE.md`: document the state machine boundary and live-data rules.

---

### Task 1: Immutable submission domain and Moodle snapshot parser

**Files:**

- Create: `src/core/submission_models.py`
- Create: `src/core/submission_snapshot.py`
- Create: `tests/fixtures/moodle_submission_responses.py`
- Create: `tests/test_submission_models.py`

**Interfaces:**

- Produces: `RemoteFile.identity -> tuple[str, str]`, `SubmissionSnapshot.fingerprint -> str`, `SubmissionSnapshot.is_editable -> bool`, `parse_submission_snapshot(assign_id, assignment, status) -> SubmissionSnapshot`, and `validate_desired_files(snapshot, files) -> tuple[SubmissionIssue, ...]`.
- Produces: `FileMutationIntent(operation, selected_files, remove_identities, rename_identity, new_name, new_filepath, finalize, accept_statement, expected_fingerprint)` consumed by Task 3.

- [ ] **Step 1: Write failing parser and fingerprint tests**

```python
def test_parse_snapshot_uses_lastattempt_permissions_and_file_limits():
    snapshot = parse_submission_snapshot(77, assignment_fixture(), editable_status_fixture())
    assert snapshot.can_edit is True
    assert snapshot.submission_drafts is True
    assert snapshot.statement_required is True
    assert snapshot.maximum_file_count == 2
    assert snapshot.maximum_file_bytes == 1_048_576
    assert snapshot.accepted_file_types == (".pdf",)
    assert snapshot.remote_files[0].identity == ("/", "old.pdf")


def test_snapshot_fingerprint_excludes_authenticated_url_query():
    first = parse_submission_snapshot(77, assignment_fixture(), editable_status_fixture("?token=one"))
    second = parse_submission_snapshot(77, assignment_fixture(), editable_status_fixture("?token=two"))
    assert first.fingerprint == second.fingerprint
```

- [ ] **Step 2: Run the focused tests and verify the expected import failure**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_submission_models.py -q --tb=short`

Expected: FAIL because `core.submission_models` and `core.submission_snapshot` do not exist.

- [ ] **Step 3: Implement immutable domain values and the flexible Moodle 4.3 parser**

```python
class MutationOperation(str, Enum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"
    CLEAR = "clear"
    RENAME = "rename"


@dataclass(frozen=True)
class RemoteFile:
    name: str
    filepath: str
    size: int
    mimetype: str
    modified_time: int
    url: str = field(repr=False, compare=False)

    @property
    def identity(self) -> tuple[str, str]:
        return normalize_filepath(self.filepath), self.name


@dataclass(frozen=True)
class SubmissionSnapshot:
    assignment_id: int
    raw_status: str
    can_edit: bool
    can_submit: bool
    locked: bool
    graded: bool
    submissions_enabled: bool
    submission_drafts: bool
    statement_required: bool
    maximum_file_count: int
    maximum_file_bytes: int
    accepted_file_types: tuple[str, ...]
    remote_files: tuple[RemoteFile, ...]
    online_text: str
    online_text_format: int
    attempt_number: int
    submission_id: int
    submission_modified_time: int

    @property
    def fingerprint(self) -> str:
        payload = {
            "assignment_id": self.assignment_id,
            "submission_id": self.submission_id,
            "attempt_number": self.attempt_number,
            "raw_status": self.raw_status,
            "can_edit": self.can_edit,
            "can_submit": self.can_submit,
            "locked": self.locked,
            "graded": self.graded,
            "submission_modified_time": self.submission_modified_time,
            "files": sorted(
                (f.filepath, f.name, f.size, f.modified_time)
                for f in self.remote_files
            ),
        }
        return sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
```

Parse `configs` by `(subtype, plugin, name)` with tolerant integer conversion. Treat a missing limit or numeric zero as unlimited, normalize filepaths to leading/trailing slash form, and extract online text from the `onlinetext` plugin without HTML transformation.

- [ ] **Step 4: Add failing constraint tests**

```python
@pytest.mark.parametrize(
    ("files", "code"),
    [
        ([selected("one.pdf"), selected("two.pdf"), selected("three.pdf")], "too_many_files"),
        ([selected("huge.pdf", size=1_048_577)], "file_too_large"),
        ([selected("script.exe")], "file_type_not_allowed"),
        ([selected("same.pdf"), selected("same.pdf")], "duplicate_filename"),
    ],
)
def test_validate_desired_files_rejects_assignment_constraint_violation(files, code):
    issues = validate_desired_files(snapshot_fixture(), files)
    assert code in {issue.code.value for issue in issues}
```

- [ ] **Step 5: Implement deterministic validation**

Normalize identity before duplicate detection. Support explicit extensions, MIME wildcards, and Moodle's observed `document`, `image`, and `web_image` groups using a small documented extension/MIME mapping. Validation gives early feedback; Moodle remains authoritative.

- [ ] **Step 6: Run focused tests and commit**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_submission_models.py -q --tb=short`

Expected: PASS.

```powershell
git add src/core/submission_models.py src/core/submission_snapshot.py tests/fixtures/moodle_submission_responses.py tests/test_submission_models.py
git commit -m "feat: model Moodle submission snapshots"
```

---

### Task 2: Truthful WS mutations and filepath-aware draft transport

**Files:**

- Modify: `src/core/ws_functions.py`
- Modify: `src/core/moodle_service.py`
- Modify: `src/core/client.py`
- Modify: `tests/test_ws_functions.py`
- Modify: `tests/test_ws_functions_extended.py`
- Create: `tests/test_moodle_client_draft_upload.py`

**Interfaces:**

- Consumes: `SubmissionIssue`-compatible warning codes from Task 1.
- Produces: `MoodleActionResult(ok: bool, warnings: tuple[MoodleWarning, ...], message: str)`, `MoodleService.save_assignment_submission_result(assign_id, draft_itemid, online_text, online_text_format, text_draft_itemid)`, `MoodleService.submit_for_grading_result(assign_id, accept_submission_statement)`, `MoodleService.get_unused_draft_itemid()`, and `MoodleClient.upload_draft_file_record(filename, file_bytes, itemid=0, filepath="/") -> DraftFileRecord | None`.

- [ ] **Step 1: Write failing tests proving warnings are failures**

```python
def test_save_submission_could_not_save_warning_is_failure():
    call_api = Mock(return_value=[{"warningcode": "couldnotsavesubmission", "message": "closed"}])
    result = ws_functions.save_assignment_submission_result(call_api, 77, 900, "", 1, 0)
    assert result.ok is False
    assert result.warnings[0].code == "couldnotsavesubmission"


def test_submit_for_grading_passes_explicit_statement_choice():
    call_api = Mock(return_value=[])
    result = ws_functions.submit_for_grading_result(call_api, 77, False)
    assert result.ok is True
    assert call_api.call_args.kwargs["acceptsubmissionstatement"] == 0
```

- [ ] **Step 2: Run focused tests and verify missing-result-function failures**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_ws_functions.py tests/test_ws_functions_extended.py -q --tb=short`

Expected: FAIL because the structured result functions do not exist and current code accepts `couldnotsavesubmission`.

- [ ] **Step 3: Implement one response parser shared by save and finalize**

```python
@dataclass(frozen=True)
class MoodleWarning:
    code: str
    message: str


@dataclass(frozen=True)
class MoodleActionResult:
    ok: bool
    warnings: tuple[MoodleWarning, ...] = ()
    message: str = ""


def _parse_empty_success_response(result: Any) -> MoodleActionResult:
    if result == []:
        return MoodleActionResult(ok=True)
    warnings = _extract_warnings(result)
    if warnings:
        return MoodleActionResult(ok=False, warnings=warnings)
    if isinstance(result, dict) and result.get("exception"):
        return MoodleActionResult(ok=False, message=str(result.get("message", "")))
    return MoodleActionResult(ok=False, message="Unexpected Moodle response")
```

Keep the existing Boolean wrappers temporarily, implemented as `.ok`, so unrelated callers remain compatible. Move online-text acquisition out of exception-swallowing behavior: the new method receives the snapshot's text, format, and an independently allocated text draft ID.

- [ ] **Step 4: Write failing multipart/path and cleanup tests**

```python
def test_upload_draft_file_record_sends_normalized_filepath(monkeypatch):
    client = configured_client(monkeypatch)
    client._post_multipart = Mock(return_value=(200, [{
        "itemid": 900, "filename": "answer.pdf", "filepath": "/proof/"
    }]))
    record = client.upload_draft_file_record("answer.pdf", b"pdf", 900, "/proof/")
    assert record.itemid == 900
    assert client._post_multipart.call_args.kwargs["fields"]["filepath"] == "/proof/"
    assert "author" not in client._post_multipart.call_args.kwargs["fields"]
    assert "license" not in client._post_multipart.call_args.kwargs["fields"]
```

- [ ] **Step 5: Implement draft allocation, structured upload, and exact cleanup seams**

`upload_draft_file_record` validates HTTP status and response shape, returning the server path/name/item ID. The legacy `upload_draft_file` calls it and returns only `.itemid`. Add `MoodleService.delete_draft_files(itemid, identities)` that sends one indexed `files[n]` entry per tracked upload; it returns false if Moodle does not confirm deletion.

- [ ] **Step 6: Run focused and legacy WS tests, then commit**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_ws_functions.py tests/test_ws_functions_extended.py tests/test_moodle_client_draft_upload.py -q --tb=short`

Expected: PASS.

```powershell
git add src/core/ws_functions.py src/core/moodle_service.py src/core/client.py tests/test_ws_functions.py tests/test_ws_functions_extended.py tests/test_moodle_client_draft_upload.py
git commit -m "fix: make Moodle submission responses truthful"
```

---

### Task 3: Exact-set submission state machine

**Files:**

- Replace: `src/core/use_cases/submission_workflow.py`
- Replace: `tests/test_submission_workflow.py`

**Interfaces:**

- Consumes: Task 1 snapshots/intents/validation and Task 2 structured Moodle transport.
- Produces: `SubmissionWorkflow.load_snapshot(target, prefetched_status=None) -> SubmissionSnapshotResult` and `SubmissionWorkflow.mutate_files(target, intent) -> SubmissionMutationResult`.
- Preserves compatibility: `load_submitted_files`, `submit_files`, `remove_files`, and `update_file_metadata` remain thin deprecated adapters until Task 4 migrates the GUI.

- [ ] **Step 1: Write failing tests for add, replace, and draft/final semantics**

```python
def test_add_rebuilds_existing_and_new_files_but_does_not_finalize_non_draft_assignment():
    workflow, client, service = workflow_fixture(snapshot=editable_snapshot(submission_drafts=False))
    result = workflow.mutate_files(target(), add_intent("new.pdf", b"new"))
    assert result.ok is True
    assert [upload.name for upload in client.uploads] == ["old.pdf", "new.pdf"]
    assert len(service.saved) == 1
    assert service.finalized == []
    assert result.snapshot.remote_identities == (("/", "new.pdf"), ("/", "old.pdf"))


def test_draft_assignment_finalizes_only_when_requested_and_statement_accepted():
    workflow, _, service = workflow_fixture(snapshot=editable_snapshot(
        submission_drafts=True, statement_required=True, can_submit=True
    ))
    result = workflow.mutate_files(target(), replace_intent("new.pdf", b"new", finalize=True, accept=True))
    assert result.ok is True
    assert service.finalized == [(77, True)]
```

- [ ] **Step 2: Run focused tests and verify they fail because `mutate_files` is absent**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_submission_workflow.py -q --tb=short`

Expected: FAIL at the new state-machine API.

- [ ] **Step 3: Implement fresh snapshot loading and preflight permission/constraint gates**

```python
def mutate_files(self, target: SubmissionTarget, intent: FileMutationIntent) -> SubmissionMutationResult:
    before = self.load_snapshot(target)
    if not before.ok:
        return before.as_mutation_failure()
    snapshot = before.snapshot
    issue = permission_issue(snapshot, intent)
    if issue:
        return SubmissionMutationResult.failure(issue, snapshot)
    plan = build_desired_file_plan(snapshot, intent)
    issues = validate_desired_files(snapshot, plan.metadata)
    if issues:
        return SubmissionMutationResult.failure(issues[0], snapshot)
    return self._execute_verified_plan(target, snapshot, plan, intent)
```

Resolve the assignment once per public operation, load its configuration from
`get_assignments([course_id])`, and use a supplied prefetched status only for display.
Every mutation obtains its own fresh status.

- [ ] **Step 4: Add failing tests for stale state, all mutation kinds, and download integrity**

```python
def test_expected_fingerprint_conflict_stops_before_download_or_upload():
    workflow, client, service = workflow_fixture(snapshot=editable_snapshot())
    result = workflow.mutate_files(target(), add_intent("new.pdf", b"new", fingerprint="stale"))
    assert result.issue.code is SubmissionErrorCode.STALE_SNAPSHOT
    assert client.downloads_requested == []
    assert client.uploads == []
    assert service.saved == []


def test_remove_one_reuploads_only_remaining_remote_files():
    workflow, client, service = workflow_fixture(
        snapshot=editable_snapshot(files=(remote("a.pdf"), remote("b.pdf")))
    )
    result = workflow.mutate_files(target(), remove_intent(("/", "a.pdf")))
    assert result.ok is True
    assert [item.name for item in client.uploads] == ["b.pdf"]
    assert service.verified_names == ["b.pdf"]


def test_clear_allocates_empty_draft_and_saves_it():
    workflow, client, service = workflow_fixture(
        snapshot=editable_snapshot(files=(remote("a.pdf"),))
    )
    result = workflow.mutate_files(target(), clear_intent())
    assert result.ok is True
    assert client.uploads == []
    assert service.saved == [(77, service.allocated_draft_id)]


def test_rename_changes_only_target_identity_and_preserves_bytes():
    workflow, client, _ = workflow_fixture(
        snapshot=editable_snapshot(files=(remote("a.pdf"), remote("b.pdf")))
    )
    result = workflow.mutate_files(
        target(), rename_intent(("/", "a.pdf"), "renamed.pdf", "/")
    )
    assert result.ok is True
    assert [(item.name, item.content) for item in client.uploads] == [
        ("renamed.pdf", b"a"), ("b.pdf", b"b")
    ]


@pytest.mark.parametrize("locked,graded", [(True, False), (False, True)])
def test_locked_or_graded_snapshot_never_uploads(locked, graded):
    workflow, client, _ = workflow_fixture(
        snapshot=editable_snapshot(locked=locked, graded=graded)
    )
    result = workflow.mutate_files(target(), replace_intent("new.pdf", b"new"))
    assert result.ok is False
    assert client.uploads == []
```

Add concrete tests using the same fixtures that assert: moving to `proof` uploads to
`/proof/`; a duplicate against a retained remote identity returns
`DUPLICATE_FILENAME` before download; and a nonzero remote size mismatch returns
`DOWNLOAD_SIZE_MISMATCH` without calling save.

- [ ] **Step 5: Implement exact-set materialization and pre-save cleanup**

Download every retained remote file with a URL and compare `len(bytes)` to nonzero
server size. Allocate one draft ID, upload every planned file to its normalized path,
and track returned identities. On any pre-save failure, delete the tracked draft
files in `finally`; after save succeeds, never invoke draft cleanup blindly.

- [ ] **Step 6: Add failing tests for warning, partial draft, and post-save verification outcomes**

```python
def test_save_warning_is_failure_and_uploaded_draft_is_not_reported_submitted():
    workflow, _, _ = workflow_fixture(
        snapshot=editable_snapshot(),
        save_result=warning_result("couldnotsavesubmission"),
    )
    result = workflow.mutate_files(target(), replace_intent("new.pdf", b"new"))
    assert result.issue.code is SubmissionErrorCode.SAVE_REJECTED
    assert result.snapshot.remote_names == ("old.pdf",)


def test_finalize_failure_returns_draft_saved_partial_outcome():
    workflow, _, _ = workflow_fixture(
        snapshot=editable_snapshot(submission_drafts=True),
        finalize_result=failure_result(),
    )
    result = workflow.mutate_files(
        target(), replace_intent("new.pdf", b"new", finalize=True)
    )
    assert result.ok is False
    assert result.partial is True
    assert result.snapshot.raw_status == "draft"


def test_post_save_file_mismatch_returns_server_snapshot():
    workflow, _, service = workflow_fixture(
        snapshot=editable_snapshot(), verified_files=(remote("other.pdf"),)
    )
    result = workflow.mutate_files(target(), replace_intent("new.pdf", b"new"))
    assert result.issue.code is SubmissionErrorCode.VERIFICATION_FAILED
    assert result.snapshot.remote_names == ("other.pdf",)
    assert service.saved


def test_statement_required_without_acceptance_stops_before_finalize():
    workflow, _, service = workflow_fixture(
        snapshot=editable_snapshot(submission_drafts=True, statement_required=True)
    )
    result = workflow.mutate_files(
        target(), replace_intent("new.pdf", b"new", finalize=True, accept=False)
    )
    assert result.issue.code is SubmissionErrorCode.STATEMENT_NOT_ACCEPTED
    assert service.finalized == []
```

Add a fixture sequence returning `can_submit=False` before save and `True` after
save, then assert finalization occurs; this proves the workflow refreshes the flag.

- [ ] **Step 7: Implement save/finalize/verification state transitions**

Save the current online-text value and format with the file draft. Refresh after
save, compare exact `(filepath, filename, size)` values, and return that server
snapshot. For draft-enabled final intent, require refreshed `can_submit`, require
explicit statement acceptance when configured, finalize, refresh once more, and
verify the final state. Represent "draft saved, finalization failed" as
`partial=True`, `ok=False`, with the verified draft snapshot.

- [ ] **Step 8: Run workflow/model/transport tests and commit**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_submission_workflow.py tests/test_submission_models.py tests/test_ws_functions.py tests/test_ws_functions_extended.py tests/test_moodle_client_draft_upload.py -q --tb=short`

Expected: PASS.

```powershell
git add src/core/use_cases/submission_workflow.py tests/test_submission_workflow.py
git commit -m "feat: add verified Moodle file state machine"
```

---

### Task 4: Server-driven Flet submission experience

**Files:**

- Create: `src/gui/components/detail/submission_presenter.py`
- Modify: `src/gui/components/detail_view.py`
- Modify: `src/gui/components/detail/submitted_files_table.py`
- Create: `tests/test_submission_presenter.py`
- Modify: `tests/test_submission_race_condition.py`

**Interfaces:**

- Consumes: `SubmissionSnapshot`, `SubmissionMutationResult`, and `FileMutationIntent` from Tasks 1 and 3.
- Produces: `SubmissionUiPolicy.from_snapshot(snapshot)`, `mutation_message(result)`, and GUI actions that always pass the displayed snapshot fingerprint.

- [ ] **Step 1: Write failing pure presentation-policy tests**

```python
def test_locked_snapshot_hides_all_mutating_controls():
    policy = SubmissionUiPolicy.from_snapshot(snapshot(locked=True, can_edit=False))
    assert policy.show_picker is False
    assert policy.show_file_actions is False
    assert policy.edit_reason == "Bài nộp đã bị khóa trên Moodle."


def test_draft_assignment_exposes_separate_save_and_final_actions():
    policy = SubmissionUiPolicy.from_snapshot(snapshot(submission_drafts=True, can_submit=True))
    assert policy.show_save_draft is True
    assert policy.show_finalize is True
```

- [ ] **Step 2: Run focused tests and verify the presenter import fails**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_submission_presenter.py -q --tb=short`

Expected: FAIL because the presenter module does not exist.

- [ ] **Step 3: Implement the pure presenter and user-safe result messages**

Map every workflow code to concise Vietnamese text. Do not include exception
representations, tokens, URLs, or file bytes. Format limits as file count, MB, and
accepted type labels.

- [ ] **Step 4: Add failing view-adapter tests for snapshot replacement and intent construction**

```python
def test_loaded_snapshot_replaces_server_file_list_and_stores_fingerprint(detail_view_shell):
    snap = snapshot(files=(remote("server.pdf"),))
    detail_view_shell._apply_submission_snapshot(snap)
    assert [item["name"] for item in detail_view_shell._submitted_files] == ["server.pdf"]
    assert detail_view_shell._submission_fingerprint == snap.fingerprint


def test_append_action_builds_add_intent_with_displayed_fingerprint(detail_view_shell):
    snap = snapshot(files=(remote("server.pdf"),))
    detail_view_shell._apply_submission_snapshot(snap)
    intent = detail_view_shell._build_file_intent(MutationOperation.ADD, finalize=False)
    assert intent.operation is MutationOperation.ADD
    assert intent.expected_fingerprint == snap.fingerprint


def test_post_mutation_ui_uses_result_snapshot_on_verification_failure(detail_view_shell):
    server = snapshot(files=(remote("actual.pdf"),))
    result = SubmissionMutationResult.failure(verification_issue(), server)
    detail_view_shell._apply_mutation_result(result)
    assert [item["name"] for item in detail_view_shell._submitted_files] == ["actual.pdf"]
```

Add a replace-confirmation test asserting the workflow call count stays zero before
confirmation and becomes one after confirmation. Add a statement test asserting the
checkbox Boolean is copied exactly to `intent.accept_statement`.

- [ ] **Step 5: Wire the Flet view to the state machine**

Store `_submission_snapshot`. Replace `_load_submitted_files` with
`load_snapshot`, derive `_submitted_files` only from `snapshot.remote_files`, and
rebuild controls from `SubmissionUiPolicy`. Change `_do_submit_sync`, remove, and
rename handlers to `mutate_files`. Remove author/license fields from the edit dialog;
retain filename and filepath. Allow delete-all with the explicit empty-record warning.

For non-draft assignments, label the primary action `Lưu bài nộp`. For draft-enabled
assignments, expose `Lưu bản nháp` and `Nộp bài`; the latter requires the statement
checkbox when configured. Disable file actions during mutation and after any fresh
snapshot says not editable.

- [ ] **Step 6: Make the submitted-file table capability-aware**

Pass `policy.show_file_actions` into `build_submitted_files_ui`; edit/delete icons,
checkboxes, multi-select, and batch-delete remain absent when false. Continue showing
download metadata and the server file list when read-only.

- [ ] **Step 7: Run GUI policy, race, workflow, and countdown tests and commit**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_submission_presenter.py tests/test_submission_race_condition.py tests/test_submission_workflow.py tests/test_detail_countdown_refresh.py -q --tb=short`

Expected: PASS.

```powershell
git add src/gui/components/detail/submission_presenter.py src/gui/components/detail_view.py src/gui/components/detail/submitted_files_table.py tests/test_submission_presenter.py tests/test_submission_race_condition.py
git commit -m "feat: synchronize assignment files in detail view"
```

---

### Task 5: Stateful Moodle 4.3 protocol integration tests

**Files:**

- Create: `tests/test_submission_protocol_integration.py`
- Modify: `tests/fixtures/moodle_submission_responses.py`

**Interfaces:**

- Consumes: the public `SubmissionWorkflow` API only.
- Produces: a stateful in-process fake implementing the observed endpoint contracts and proving end-to-end state transitions without real user data.

- [ ] **Step 1: Write a bounded stateful fake Moodle transport**

```python
class FakeMoodle43:
    def __init__(self, *, drafts: bool, statement: bool):
        self.remote_files = {}
        self.drafts = {}
        self.submission_status = "new"
        self.submission_drafts = drafts
        self.statement_required = statement

    def upload(self, itemid, filepath, filename, content):
        key = (normalize_filepath(filepath), filename)
        if key in self.drafts.setdefault(itemid, {}):
            return {"errorcode": "filenameexist", "error": "already exists"}
        self.drafts[itemid][key] = content
        return [{"itemid": itemid, "filepath": key[0], "filename": key[1]}]
```

The fake exposes no network, sleeps, credentials, or real URLs. It models
same-item-ID append, duplicate rejection, exact file-manager replacement, draft/final
state, and status reads.

- [ ] **Step 2: Add parameterized end-to-end scenarios and observe failures**

```python
@pytest.mark.parametrize("names", [["one.pdf"], ["one.pdf", "two.pdf"]])
def test_first_submission_exact_set(names, protocol_workflow):
    selected = tuple(SelectedSubmissionFile(name, name.encode()) for name in names)
    result = protocol_workflow.workflow.mutate_files(
        target(), replace_many_intent(selected)
    )
    assert result.ok is True
    assert set(protocol_workflow.server.remote_files) == {
        ("/", name) for name in names
    }


def test_append_after_existing_submission_preserves_old_bytes(protocol_workflow):
    protocol_workflow.server.remote_files[("/", "old.pdf")] = b"old"
    result = protocol_workflow.workflow.mutate_files(
        target(), add_intent("new.pdf", b"new")
    )
    assert result.ok is True
    assert protocol_workflow.server.remote_files == {
        ("/", "old.pdf"): b"old",
        ("/", "new.pdf"): b"new",
    }


def test_concurrent_status_change_aborts_before_save(protocol_workflow):
    displayed = protocol_workflow.workflow.load_snapshot(target()).snapshot
    protocol_workflow.server.remote_files[("/", "concurrent.pdf")] = b"other"
    result = protocol_workflow.workflow.mutate_files(
        target(), add_intent("new.pdf", b"new", displayed.fingerprint)
    )
    assert result.issue.code is SubmissionErrorCode.STALE_SNAPSHOT
    assert protocol_workflow.server.save_calls == 0
```

Using the same stateful fake, assert replace removes old keys only after save; remove
one/many/all produces the exact remaining key set; rename/path move preserves bytes;
a duplicate leaves the allocated draft empty after cleanup; and a draft changes to
`submitted` only after the statement-accepted finalize call.

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_submission_protocol_integration.py -q --tb=short`

Expected: at least one FAIL revealing any adapter/contract mismatch; record the exact
failure in the task notes before changing production code.

- [ ] **Step 3: Correct only the exposed contract mismatches**

Keep fixes within the existing Task 1-4 boundaries. Do not add a second submission
workflow or fake-specific production branches.

- [ ] **Step 4: Run integration plus all submission tests and commit**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_submission_protocol_integration.py tests/test_submission_workflow.py tests/test_submission_models.py tests/test_submission_presenter.py tests/test_ws_functions.py tests/test_ws_functions_extended.py tests/test_moodle_client_draft_upload.py -q --tb=short`

Expected: PASS.

```powershell
git add tests/test_submission_protocol_integration.py tests/fixtures/moodle_submission_responses.py src/core src/gui/components/detail_view.py src/gui/components/detail
git commit -m "test: exercise Moodle submission protocol end to end"
```

---

### Task 6: Opt-in live-safe draft and empty-assignment probes

**Files:**

- Create: `tests/test_submission_live_safe.py`
- Modify: `REFAC_KNOWLEDGE.md`
- Modify: `docs/moodle_ws_api_documentation.md`

**Interfaces:**

- Consumes: existing environment-based live authentication and the Task 2 draft APIs.
- Produces: no generated file left on production; an empty submission record is the only allowed residual state and is reported explicitly.

- [ ] **Step 1: Write the opt-in safety gate tests without credentials**

```python
pytestmark = pytest.mark.skipif(
    os.environ.get("UTH_LIVE_SUBMISSION_TEST") != "1",
    reason="Set UTH_LIVE_SUBMISSION_TEST=1 for bounded reversible file probes",
)


def test_live_probe_never_calls_assignment_mutation(monkeypatch):
    forbidden = {"mod_assign_save_submission", "mod_assign_submit_for_grading"}
    spy = live_call_spy(monkeypatch)
    run_unlinked_draft_probe()
    assert forbidden.isdisjoint(spy.called_functions)
```

Use `UTH_TEST_USER`/`UTH_TEST_PASS` or the app's existing secure configuration. Never
place credential literals in the file and never print token fragments.

- [ ] **Step 2: Implement one unique unlinked-draft lifecycle with `finally` cleanup**

Allocate an unused item ID, upload two small generated files to the same item ID,
append a third, verify returned/listed identities, reject an intentional duplicate,
delete the tracked identities, and verify no tracked identity remains. All generated
names include a random prefix and content contains only a synthetic marker.

- [ ] **Step 3: Implement the strictly gated empty-assignment write/delete probe**

Discover candidates read-only, then select exactly one whose fresh status is `new`,
has no remote files, `canedit=true`, `locked=false`, `graded=false`, supports the file
plugin, permits repeated editing, and whose due/cutoff time is at least seven days
away. Immediately before upload and immediately before cleanup, fetch status again and
abort on drift. Save one uniquely named synthetic file, verify it appears, clear the
file set through the production workflow, and verify that exact name/path is absent.
Never call `submit_for_grading` during this probe. In `finally`, retry only the same
idempotent clear operation under a fresh editable-state check; otherwise stop and
report the exact cleanup risk without touching any other file.

- [ ] **Step 4: Run the safe probes with a hard test timeout wrapper**

Run from a bounded PowerShell process:

```powershell
$job = Start-Job { Set-Location $using:PWD; $env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_submission_live_safe.py -q --tb=short -x }
if (-not (Wait-Job $job -Timeout 180)) { Stop-Job $job; Remove-Job $job -Force; throw 'Live draft probe timed out after 180 seconds' }
Receive-Job $job
Remove-Job $job
```

Expected: PASS with no token, URL query, credential, or file content in output. If the
opt-in environment flag is absent, the normal full suite reports this module skipped.

- [ ] **Step 5: Document the boundary and operator contract**

Add the snapshot/state-machine ownership to `REFAC_KNOWLEDGE.md`. In
`docs/moodle_ws_api_documentation.md`, document same-item-ID multipart upload,
replacement semantics, draft versus final behavior, unsupported removal of the
submission record, warning handling, and the exact live safety/timeout environment
contract.

- [ ] **Step 6: Scan for secrets/authenticated URLs and commit**

Run: `rg -n "NoBoi|080206011901|wstoken=|token=[A-Za-z0-9]{10,}" src tests docs REFAC_KNOWLEDGE.md`

Expected: no newly introduced credential or authenticated URL. Existing generic
parameter names such as `wstoken` in source are reviewed manually and are not secret
values.

```powershell
git add tests/test_submission_live_safe.py REFAC_KNOWLEDGE.md docs/moodle_ws_api_documentation.md
git commit -m "docs: define safe Moodle submission verification"
```

---

### Task 7: Full verification, review, merge, and post-merge verification

**Files:**

- Modify only files needed to correct failures discovered by the gates.

**Interfaces:**

- Consumes: all prior tasks.
- Produces: a reviewed `main` commit whose post-merge test evidence matches the feature branch evidence.

- [ ] **Step 1: Run targeted submission tests**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests/test_submission_models.py tests/test_moodle_client_draft_upload.py tests/test_submission_workflow.py tests/test_submission_protocol_integration.py tests/test_submission_presenter.py tests/test_submission_race_condition.py tests/test_ws_functions.py tests/test_ws_functions_extended.py -q --tb=short`

Expected: PASS.

- [ ] **Step 2: Run the complete suite with a bounded command timeout**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests -q --tb=short`

Expected: PASS with live-only modules skipped unless explicitly opted in.

- [ ] **Step 3: Run Ruff and diff hygiene checks**

Run: `ruff check src tests`

Run: `git diff main...HEAD --check`

Expected: both exit 0.

- [ ] **Step 4: Perform a bounded headless app/import smoke test**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -c "from core.use_cases.submission_workflow import SubmissionWorkflow; from gui.components.detail_view import DetailView; print('submission-smoke-ok')"`

Expected: `submission-smoke-ok` and exit 0.

- [ ] **Step 5: Review the complete diff against the spec**

Check every spec goal has a test, all real-write call sites are behind the workflow,
all mutation controls are permission-driven, no Boolean-only result remains in the
new GUI path, no unsupported author/license promise remains, every post-save path
refreshes, and no secret/authenticated URL appears in tracked changes.

- [ ] **Step 6: Commit any review corrections and rerun Steps 1-4**

```powershell
git add src/core/submission_models.py src/core/submission_snapshot.py src/core/ws_functions.py src/core/moodle_service.py src/core/client.py src/core/use_cases/submission_workflow.py src/gui/components/detail/submission_presenter.py src/gui/components/detail/submitted_files_table.py src/gui/components/detail_view.py tests REFAC_KNOWLEDGE.md docs/moodle_ws_api_documentation.md
git commit -m "fix: address submission workflow review"
```

Expected: no correction commit when no issues are found; otherwise all gates pass
again after the correction.

- [ ] **Step 7: Merge into `main` from the root worktree**

Verify both worktrees are clean and `main` has not moved unexpectedly. Then run:

```powershell
git -C E:\Projects\UTH-Elearning-Alert merge --no-ff codex/moodle-submission-workflow -m "Merge Moodle submission workflow"
```

Expected: merge succeeds without conflicts. If `main` moved, merge/rebase it into the
feature worktree first, rerun all gates, and only then merge.

- [ ] **Step 8: Rerun complete verification on merged `main`**

Run from `E:\Projects\UTH-Elearning-Alert`:

```powershell
$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'
python -m pytest tests -q --tb=short
ruff check src tests
git status --short --branch
```

Expected: complete suite PASS, Ruff PASS, and clean `main`.
