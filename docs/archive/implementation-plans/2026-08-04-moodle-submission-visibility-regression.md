# Moodle File Submission Visibility Regression — Implementation Plan

> **Archived:** Planning snapshot retained for provenance. It is not the current
> task tracker; use tests and current operator documentation as the source of truth.

**Goal:** Restore the file-submission area for eligible Moodle assignments using response shapes Moodle actually returns, keep permission/deadline safeguards fail-closed, and show a truthful fallback instead of silently hiding controls when context is incomplete.

**Root cause:** `src/core/submission_snapshot.py` currently requires both a status plugin with `type=file` and a synthetic `configs[file/enabled]` value. Moodle 4.3 only returns enabled and visible plugins and does not guarantee an `enabled` config; the captured response in `docs/moodle_ws_api_documentation.md` has `configs: []`. Tests manufacture the missing row and therefore preserve the regression. The same parser reads `requiresubmissionstatement` from configs although Moodle returns it at assignment top level.

**Safety invariants:** Existing gates for `submissionsenabled`, `canedit`, locked, graded, deadline/cutoff, ownership, and finalization remain authoritative. File capability is true only when an enabled/visible file plugin is evidenced by fresh submission status or a real file-plugin config; absence of both remains false. No wildcard Moodle origins are accepted.

---

### Task 1: Normalize real Moodle file-plugin capability and statement requirement

**Files:**

- Modify: `src/core/submission_snapshot.py`
- Modify: `tests/fixtures/moodle_submission_responses.py`
- Modify: `tests/test_submission_models.py`

- [ ] Add a captured-real-shape fixture with `configs: []`, top-level `requiresubmissionstatement=1`, `submissionsenabled=True`, `canedit=True`, and a status submission plugin `type=file`. Confirm RED: current result has `file_submission_enabled=False` and `statement_required=False`.
- [ ] Add independent literals covering: status file plugin only => true; any real `assignsubmission_file`/file-plugin config => true when status is absent; neither source => false; disabled/hidden explicit plugin evidence => false; top-level statement field wins and legacy config remains fallback.
- [ ] Implement capability derivation without requiring a synthetic `enabled` config. Preserve all permission/state gates and do not infer capability from filename, assignment text, or URL.
- [ ] Run focused model tests and Ruff with finite process deadlines.
- [ ] Commit `fix: recognize real moodle file submission capability`.

### Task 2: Prove parse-to-presenter-to-view visibility

**Files:**

- Modify: `tests/test_submission_presenter.py`
- Modify: `tests/test_submission_race_condition.py`
- Modify: `src/gui/components/detail_view.py` only if the real normalized policy still fails to render

- [ ] Add an integration test that parses the real response through `SubmissionUiPolicy` and asserts picker visibility, appropriate draft-save policy, and statement checkbox state.
- [ ] Add a DetailView adapter test: after async snapshot completion for an eligible real-shape assignment, `_submission_area` and `_pick_btn` are visible. Assert the final submit control may remain hidden until a file is selected; the capability area/picker must not.
- [ ] Add stale-generation coverage so a late response for the previous assignment cannot expose controls on the newly selected assignment.
- [ ] Make the minimum production change needed, if any; do not bypass presenter policy in the view.
- [ ] Run focused presenter/view/race tests and Ruff, then commit `test: verify eligible submission controls render` (or `fix:` if production changes).

### Task 3: Replace silent missing-context suppression with a browser fallback

**Files:**

- Modify: `src/gui/components/detail_view.py`
- Modify: `src/core/data_orchestrator.py`
- Modify: `tests/test_submission_race_condition.py`
- Modify: relevant `tests/test_data_orchestrator*.py`

- [ ] Add RED tests for assignment detail with missing client or course ID: it must render a localized reason and a safe browser-open action instead of remaining indefinitely at “Đang đồng bộ…” with no controls.
- [ ] Add merge coverage proving a matching assignment from `mod_assign_get_assignments` backfills missing `course_id` without overwriting newer calendar fields.
- [ ] Implement explicit fallback state and course-id backfill. Continue rejecting mutation when the native workflow lacks trusted context.
- [ ] Run focused tests plus Ruff and commit `fix: explain unavailable native submission context`.

### Task 4: Support both explicitly trusted UTH Moodle origins

**Files:**

- Modify: central Moodle site configuration module(s)
- Modify: `src/core/use_cases/submission_workflow.py`
- Modify: `tests/test_submission_workflow.py`
- Modify: site/config tests

- [ ] Add RED URL parsing tests for configured HTTPS origins `https://courses.ut.edu.vn` and `https://thnn.ut.edu.vn`; retain rejection of HTTP, userinfo, foreign hosts, wildcard subdomains, wrong paths, fragments, duplicate/extra query parameters, and non-integer CMIDs.
- [ ] Inject the selected trusted Moodle origin/site identity into `SubmissionWorkflow`; remove the hard-coded `courses.ut.edu.vn` comparison. Do not accept `*.ut.edu.vn`.
- [ ] Prove the credentials/session/client selected for an assignment belong to the same configured site before any native mutation. If THNN authentication is not configured, render browser fallback rather than borrowing a cross-origin token.
- [ ] Run focused workflow/config tests plus Ruff and commit `fix: honor configured uth moodle origin for submissions`.

### Final verification

- [ ] Run all submission model/presenter/workflow/protocol/race/data-orchestrator tests with the full extension `PYTHONPATH` and hard outer timeout.
- [ ] Run the full Python suite and Ruff.
- [ ] Manually exercise a captured-response fixture for: eligible draft save, statement-required save, locked/graded/cutoff denial, missing context fallback, courses origin, and THNN origin.
- [ ] Request whole-change review against this plan and the original safe-submission design before merge.
