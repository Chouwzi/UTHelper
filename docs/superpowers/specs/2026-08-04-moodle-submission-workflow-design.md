# Moodle assignment submission workflow design

Date: 2026-08-04

Status: Approved with live-data safety restriction

Branch: `codex/moodle-submission-workflow`

## Context

UTHelper currently exposes a file picker and can upload more than one file, but its
submission workflow does not model Moodle's assignment state machine accurately.
It always calls `mod_assign_submit_for_grading` after saving, reports some Moodle
warnings as success, does not enforce assignment-specific limits, and can overwrite
remote state without detecting that it changed after the detail view was opened.
File metadata controls also imply capabilities that Moodle's mobile web service does
not provide.

The target is only `courses.ut.edu.vn`. `thnn.ut.edu.vn` is explicitly outside this
change.

Read-only discovery against the target site established that it runs Moodle 4.3.5
and exposes the relevant assignment read/save/finalize functions plus the dedicated
`/webservice/upload.php` draft-file endpoint. It does not expose
`mod_assign_remove_submission`, which was added in a later Moodle release. Therefore
the original design assumed that "delete all" could clear the file area while an
editable submission exists without removing its record. Production verification on
2026-08-10 corrected that assumption: an empty file-manager save is rejected, while
Moodle's same-origin web confirmation action can remove the submission and reset it
to `new`. The implemented fallback uses that action and verifies the result through
the web service.

## Goals

1. Correctly submit one file or several files in one user action.
2. Add files to an existing editable submission without losing remote files.
3. Replace the complete submitted file set when the user explicitly chooses to do so.
4. Delete one, several, or all submitted files while preserving a truthful model of
   the remaining Moodle submission record.
5. Rename files and change their draft path using the only supported mechanism:
   rebuild the exact desired draft set and save it atomically at the assignment level.
6. Synchronize the app's displayed file list and submission state from Moodle after
   every mutation and whenever the detail view is refreshed.
7. Respect Moodle's editable, locked, graded, draft, submission-statement, maximum
   file count, maximum byte size, and accepted-file-type constraints.
8. Detect stale UI state before a destructive replacement and fail safely.
9. Return structured, actionable errors instead of ambiguous Booleans.
10. Prove the behavior with unit, integration, UI, and bounded live-safe checks.

## Non-goals

- Supporting `thnn.ut.edu.vn`.
- Automating browser sessions or scraping Moodle HTML when an official web-service
  operation exists. The narrowly scoped removal confirmation is allowed because the
  installed student web-service contract has no equivalent operation.
- Editing author or license metadata. Moodle 4.3's upload endpoint ignores the fields
  the current client sends, so those controls will be removed rather than simulated.
- Removing a submission through an unavailable web-service function. File-only
  removal uses Moodle's own authenticated, same-origin confirmation form instead.
- Mutating an already-submitted, graded, locked, expired, near-due, or otherwise
  sensitive real assignment during testing.
- Claiming byte-for-byte atomicity across draft uploads. Atomicity begins when the
  complete draft item is saved into the assignment; orphan draft items are cleaned
  on failure where the service permits it.

## Reverse-engineered protocol

### Capability discovery

`core_webservice_get_site_info` is the source of truth for function availability.
The implementation must degrade by capability rather than Moodle version strings.
The observed service provides:

- `core_files_get_unused_draft_itemid`;
- `core_files_delete_draft_files`;
- `mod_assign_get_assignments`;
- `mod_assign_get_submission_status`;
- `mod_assign_save_submission`;
- `mod_assign_submit_for_grading`.

It does not provide `mod_assign_remove_submission` or `core_files_upload`. Files are
uploaded using multipart requests to `/webservice/upload.php`.

### Draft-file behavior

An unused draft item ID is allocated first. Every file in a logical replacement set
is uploaded to that same item ID. A later upload with the same item ID appends to the
draft area. A duplicate filename/path is rejected by Moodle with `filenameexist`.
The client must detect duplicate target `(filepath, filename)` pairs before upload and
must surface server rejection if a concurrent or normalization collision still occurs.

The upload endpoint returns file records but does not accept meaningful author or
license updates. The app must use server-returned filename/path/size information and
must not report unsupported metadata changes as successful.

### Saving versus final submission

`mod_assign_save_submission` attaches the draft file manager to the assignment.
Whether that save is already final depends on the assignment's `submissiondrafts`
configuration:

- When drafts are disabled, saving is the submission operation. Calling
  `mod_assign_submit_for_grading` is unnecessary and can produce misleading warnings.
- When drafts are enabled, saving creates or updates a draft. Finalization is a
  separate explicit action and is allowed only when refreshed status says
  `cansubmit=true`.
- When a submission statement is required, finalization must send
  `acceptsubmissionstatement=1` only after the user explicitly accepts it.

Any warning from a save or finalization call is a failure unless it is explicitly
classified as harmless by a tested Moodle contract. In particular,
`couldnotsavesubmission` is a failure, not evidence of an empty successful save.

### Editing an existing file submission

Moodle's mobile app uses replacement semantics for the file manager. To append,
delete, rename, or change paths safely, the client builds the complete desired file
set:

1. Download each remote file that should remain.
2. Verify each download completed and its size matches the status metadata when size
   is available.
3. Apply additions, removals, names, and paths locally in memory or bounded temporary
   storage.
4. Upload the complete desired set into one new draft item ID.
5. Save that draft item ID into the assignment.
6. Fetch status again and verify the remote result.

An append therefore means "existing verified files plus selected new files". A
replace operation means "selected new files only". A delete-all operation saves an
empty file manager where Moodle permits it; the resulting assignment may still have
an empty editable submission record.

## Domain model

### Submission snapshot

The workflow reads a fresh typed snapshot before exposing or executing actions. It
contains:

```text
assignment_id
submission_id / attempt_number / submission_time
raw_status
can_edit
can_submit
locked
graded
submissions_enabled
submission_drafts
submission_statement_required
maximum_file_count
maximum_file_bytes
accepted_file_types
online_text_enabled and current online text
remote_files[]: filename, filepath, size, MIME type, modified time, download URL
fingerprint
```

The fingerprint is deterministic over the assignment/submission identity, attempt,
status flags, and sorted remote file metadata. Secrets, download tokens, and full
download URLs are excluded. The fingerprint is used for optimistic concurrency, not
authentication.

### Desired file set

Mutations are expressed as a desired final state, not a sequence of partially
persisted edits:

```text
add selected files
replace all with selected files
remove selected remote identities
remove all files
rename one remote identity
move one remote identity to another draft path
```

File identity is `(normalized filepath, filename)` within a snapshot. Display names
alone are not sufficient because Moodle paths may differ.

### Structured result and errors

Successful operations return the verified post-mutation snapshot and a mutation
kind (`draft_saved`, `submission_saved`, or `submitted_for_grading`). Failures use
typed codes with safe user messages and log-only technical details:

- `not_editable`, `locked`, `graded`, `submissions_closed`;
- `stale_snapshot`;
- `duplicate_filename`, `too_many_files`, `file_too_large`,
  `file_type_not_allowed`;
- `download_failed`, `download_size_mismatch`, `upload_failed`,
  `draft_cleanup_failed`;
- `save_rejected`, `finalize_rejected`, `statement_not_accepted`;
- `verification_failed`, `unsupported_operation`.

The GUI updates its success state only from a successful verified result.

## State machine and permission gates

Top-level labels such as `new`, `draft`, or `submitted` are not sufficient. The
latest `lastattempt` flags are authoritative.

```text
refresh snapshot
  -> reject if disabled, locked, graded, or can_edit=false
  -> validate desired final set against current assignment constraints
  -> re-read snapshot immediately before replacement
  -> reject if fingerprint changed
  -> create/upload exact draft set
  -> preserve current online text while saving file changes
  -> save submission
  -> refresh and verify exact file set
  -> if drafts disabled: return verified submission
  -> if user selected "save draft": return verified draft
  -> refresh cansubmit and statement requirements
  -> explicitly finalize if requested and permitted
  -> refresh and verify final status and exact file set
```

File count is checked on the desired final set. Each newly selected and re-downloaded
file is checked against the assignment byte limit. Accepted types are validated
client-side for early feedback, but Moodle remains authoritative because MIME and
extension category matching can differ by server configuration.

When constraints or editability change between refresh and save, the workflow stops
and asks the user to reload. It never silently retries a destructive replacement.

## Synchronization contract

The Moodle status endpoint is authoritative. Local selection state is only pending
UI state and is never merged into the displayed "Đã nộp" list before server
verification.

The app refreshes the snapshot:

- when assignment detail opens;
- immediately before mutation;
- immediately after save;
- immediately after optional finalization;
- when the user explicitly refreshes;
- after dashboard synchronization observes an assignment status change.

Post-save verification compares sorted `(filepath, filename, size)` values and the
expected draft/final state. A mismatch is an error even if the save endpoint returned
without warnings. The returned server snapshot still replaces stale cached data so
the UI truthfully displays what Moodle currently holds, together with an error notice.

## User experience

The submission card will show:

- server state: not submitted, draft, or submitted;
- editability/lock reason;
- file limits and accepted types;
- the synchronized remote file list;
- a multi-file picker;
- explicit `Thêm file` and `Thay thế tất cả` actions;
- selection summary and duplicate/limit validation before confirmation;
- `Lưu bản nháp` and `Nộp bài` as separate actions only for draft-enabled
  assignments;
- a required statement confirmation before final submission;
- edit/delete controls only when the fresh snapshot permits edits;
- one confirmation for destructive replace, multi-delete, or delete-all operations.

Rename/path changes are presented as file organization, not author/license editing.
After every operation, the entire file list and status are replaced with the verified
server snapshot. The app explains that deleting all files may leave an empty Moodle
submission record because this server cannot remove that record through its enabled
web service.

## Component boundaries

- `core/ws_functions.py` remains a thin Moodle transport adapter. It parses warnings
  faithfully and exposes typed raw responses without deciding UI policy.
- A core snapshot/capability parser converts assignment configuration and status into
  domain values.
- `core/use_cases/submission_workflow.py` owns the state machine, validation,
  optimistic concurrency, exact-set rebuild, finalization, cleanup, and post-save
  verification.
- GUI components render snapshots, collect intent, and map structured results to
  messages. They do not assemble Moodle parameter arrays or infer server success.
- Existing composition code injects the workflow; no Moodle protocol logic is added
  directly to Flet controls.

## Cleanup and recovery

Draft upload is isolated until save. If upload or validation fails, the client calls
`core_files_delete_draft_files` for the allocated item and records cleanup failure
without hiding the primary error. Once `mod_assign_save_submission` succeeds, the
draft is owned by Moodle's submission workflow and must not be deleted blindly.

If finalization fails after a draft save, the verified draft remains visible and the
user is told that files were saved but not finalized. This is a partial outcome with
an explicit result state, not an all-or-nothing success message.

Downloaded remote files are bounded by assignment limits and held in memory only for
the operation unless a tested temporary-file path is needed. Any temporary file or
directory must be uniquely scoped and removed in `finally`.

## Live-data safety policy

Automated correctness comes from fakes and an isolated Moodle 4.3 integration
environment. Production-account probes are deliberately narrower than product
capability.

Real-site tests may perform only:

- read-only capability, assignment configuration, status, and file-list queries;
- read-only download verification of an existing file without printing content;
- upload/list/delete inside an unused, unlinked draft item ID with guaranteed cleanup;
- an assignment mutation only if all of these are freshly true immediately before
  the request: the assignment is not yet due, is comfortably outside a configured
  safety window, has no submitted work, is ungraded/unlocked, and permits repeated
  editing.

Because this server lacks submission-record removal, an assignment probe cannot
guarantee an exact rollback to the original `new` state. The user has explicitly
authorized a bounded probe on a qualifying empty assignment when the generated test
file can be removed afterward. Such a probe must use a clearly synthetic unique file,
verify that the assignment is still empty/editable and comfortably before its due
date immediately before saving, then remove the file and verify that no test file
remains. An empty submission record may remain and must be reported as a known,
non-sensitive side effect. The harness records no credentials, tokens, authenticated
URLs, or file contents.

The suite always aborts on status drift. It never accesses a submitted sensitive
assignment for write testing, never attempts late submission, and never logs tokens,
passwords, authenticated URLs, or user file content.

All network and process operations use finite connection, read, and total timeouts.
Test waits have explicit deadlines and clean up exact resources in `finally`; no
unbounded terminal command or polling loop is allowed.

## Test strategy

### Unit tests

- Parse snapshots and assignment constraints from representative Moodle 4.3
  responses, including missing/optional fields.
- Validate one file, many files, append counts, duplicate paths/names, file size, and
  accepted type categories.
- Permission matrix for editable, locked, graded, closed, draft, final, and statement
  states.
- Exact-set construction for add, replace, remove one/many/all, rename, and path move.
- Preserve online text when only files are changed.
- Warning/error mapping for upload, save, and finalization; specifically prove that
  `couldnotsavesubmission` is failure.
- Draft cleanup on pre-save failure and no unsafe cleanup after save.
- Optimistic conflict detection and post-save mismatch detection.
- Draft save versus final submit behavior and explicit statement acceptance.
- GUI visibility/enabled-state and intent wiring from snapshot capabilities.

### Integration tests

A protocol-level fake server or fixture suite will emulate Moodle 4.3 endpoints and
state transitions for:

- single and multipart logical submissions;
- sequential same-item-ID uploads;
- append to an existing submission;
- replace, delete, delete-all, rename, and path changes;
- draft save and finalization;
- duplicate filename and Moodle warning responses;
- concurrent modification between reads;
- successful save with a mismatching refresh response.

Fixtures contain synthetic identities and files only.

### Live-safe checks

The bounded live suite verifies enabled capabilities, reads assignment/status shapes,
and exercises an unlinked draft item upload/list/delete cycle. It confirms all created
draft files are gone before passing. It may also exercise one qualifying empty,
editable, comfortably-before-deadline assignment under the explicit user
authorization above; it must delete the generated file and verify its absence before
passing. Previously submitted assignments are never eligible.

### Release gates

1. Targeted domain/workflow/GUI tests.
2. Full `tests` suite with the repository's configured import path.
3. Ruff over `src` and `tests`.
4. Bounded app smoke test for the submission view.
5. Review the diff for secret/authenticated-URL leakage.
6. Merge the feature branch into `main` only after all gates pass.
7. Repeat full tests and Ruff on the merged `main` worktree.

## Delivery sequence

1. Commit this approved design.
2. Write a file-level implementation plan with exact failing tests.
3. Add snapshot, result, validation, and transport-warning tests first.
4. Implement the state machine and exact-set operations in the use-case boundary.
5. Add GUI behavior tests, then wire the refreshed synchronized experience.
6. Add safe protocol integration fixtures and the opt-in live-safe harness.
7. Update architecture/refactoring knowledge and user-facing documentation.
8. Run release gates, review, merge to `main`, and rerun gates after merge.

## References

- Moodle web-service file handling:
  <https://moodledev.io/docs/4.3/apis/subsystems/external/files>
- Moodle assignment web-service implementation:
  <https://github.com/moodle/moodle/blob/MOODLE_403_STABLE/mod/assign/externallib.php>
- Moodle app assignment submission service:
  <https://github.com/moodlehq/moodleapp/tree/main/src/addons/mod/assign>
