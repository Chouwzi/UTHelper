"""Opt-in, bounded probes against a real Moodle account.

The probes use only synthetic files and deliberately keep all account and
assignment identifiers out of pytest output.  Run this module only through the
documented 180-second process wrapper.
"""

from __future__ import annotations

import logging
import os
import time
import warnings
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from config import settings
from core.client import MoodleClient
from core.moodle_service import MoodleService
from core.submission_models import (
    FileMutationIntent,
    MutationOperation,
    SelectedFile,
    SubmissionSnapshot,
)
from core.submission_snapshot import parse_submission_snapshot, validate_desired_files
from core.use_cases.submission_workflow import (
    SelectedSubmissionFile,
    SubmissionTarget,
    SubmissionWorkflow,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("UTH_LIVE_SUBMISSION_TEST") != "1",
    reason="Set UTH_LIVE_SUBMISSION_TEST=1 for bounded reversible file probes",
)

_MINIMUM_LEAD_SECONDS = 7 * 24 * 60 * 60
_FORBIDDEN_FINALIZE = "mod_assign_submit_for_grading"
_ASSIGNMENT_MUTATIONS = {
    "mod_assign_save_submission",
    _FORBIDDEN_FINALIZE,
}


@dataclass(frozen=True, repr=False)
class _Candidate:
    course_id: int
    assignment_id: int
    cmid: int
    assignment: dict[str, Any]
    snapshot: SubmissionSnapshot


@dataclass(frozen=True)
class _AssignmentProbeReport:
    cleanup_absence_verified: bool
    empty_submission_record_remains: bool


class _LiveCallSpy:
    def __init__(self) -> None:
        self.called_functions: list[str] = []


def live_call_spy(monkeypatch: pytest.MonkeyPatch) -> _LiveCallSpy:
    """Record WS function names without retaining params, tokens, or responses."""
    spy = _LiveCallSpy()
    original = MoodleClient.call_ws_api

    def recorded(client: MoodleClient, function: str, **params: Any) -> Any:
        spy.called_functions.append(function)
        if function == _FORBIDDEN_FINALIZE:
            raise AssertionError("live-safe probes must never finalize a submission")
        return original(client, function, **params)

    monkeypatch.setattr(MoodleClient, "call_ws_api", recorded)
    return spy


def _live_client() -> MoodleClient:
    """Authenticate from opt-in environment variables or existing secure settings."""
    user = os.environ.get("UTH_TEST_USER", "")
    password = os.environ.get("UTH_TEST_PASS", "")
    if bool(user) != bool(password):
        pytest.skip("live credentials are incomplete")

    client = MoodleClient()
    token = client._get_ws_token(user or None, password or None)  # noqa: SLF001
    if not token:
        pytest.skip("no existing secure live authentication is available")
    return client


def _draft_identities(client: MoodleClient, user_id: int, item_id: int) -> set[tuple[str, str]]:
    response = client.call_ws_api(
        "core_files_get_files",
        contextid=-1,
        contextlevel="user",
        instanceid=user_id,
        component="user",
        filearea="draft",
        itemid=item_id,
        filepath="/",
        filename="",
    )
    if not isinstance(response, dict) or not isinstance(response.get("files"), list):
        raise AssertionError("Moodle did not return a verifiable draft listing")
    return {
        (str(item.get("filepath", "/")), str(item.get("filename", "")))
        for item in response["files"]
        if isinstance(item, dict) and not item.get("isdir") and item.get("filename")
    }


def run_unlinked_draft_probe() -> None:
    """Upload/list/reject-duplicate/delete in one unused, unlinked draft area."""
    client = _live_client()
    service = MoodleService(client.call_ws_api)
    user_id = service.get_current_user_id()
    item_id = service.get_unused_draft_itemid()
    if not user_id or not item_id:
        pytest.skip("Moodle did not expose the required draft APIs")

    prefix = f"uthelper-live-{uuid4().hex}"
    names = tuple(f"{prefix}-{index}.txt" for index in range(3))
    expected = {("/", name) for name in names}
    # Track every identity before its upload attempt.  A malformed success
    # response must not make an actually-created file untraceable to cleanup.
    tracked: list[tuple[str, str]] = [("/", name) for name in names]
    try:
        for index, name in enumerate(names[:2]):
            marker = f"synthetic-draft-probe-{prefix}-{index}".encode()
            record = client.upload_draft_file_record(name, marker, item_id, "/")
            assert record is not None, "synthetic draft upload was rejected"
            assert record.itemid == item_id, "draft upload changed the allocated item"
            assert record.identity == tracked[index], "draft upload identity changed"

        marker = f"synthetic-draft-probe-{prefix}-2".encode()
        record = client.upload_draft_file_record(names[2], marker, item_id, "/")
        assert record is not None, "synthetic append upload was rejected"
        assert record.itemid == item_id, "draft append changed the allocated item"
        assert record.identity == tracked[2], "draft append identity changed"

        assert set(tracked) == expected, "returned draft identities did not match"
        assert expected.issubset(_draft_identities(client, user_id, item_id))

        duplicate = client.upload_draft_file_record(
            names[0],
            f"synthetic-intentional-duplicate-{prefix}".encode(),
            item_id,
            "/",
        )
        assert duplicate is None, "Moodle unexpectedly accepted a duplicate draft identity"
        assert expected.issubset(_draft_identities(client, user_id, item_id))
    finally:
        if tracked:
            deleted = service.delete_draft_files(item_id, tuple(tracked))
            remaining = expected.intersection(_draft_identities(client, user_id, item_id))
            assert deleted and not remaining, "tracked draft cleanup could not be verified"


def _courses(response: Any) -> list[dict[str, Any]]:
    if not isinstance(response, dict) or not isinstance(response.get("courses"), list):
        return []
    return [course for course in response["courses"] if isinstance(course, dict)]


def _config_values(assignment: dict[str, Any]) -> dict[tuple[str, str], Any]:
    values: dict[tuple[str, str], Any] = {}
    for item in assignment.get("configs", ()):
        if isinstance(item, dict):
            values[(str(item.get("plugin", "")).lower(), str(item.get("name", "")).lower())] = item.get("value")
    return values


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _assignment_precheck(assignment: dict[str, Any], now: int) -> tuple[str, ...]:
    reasons: list[str] = []
    configs = _config_values(assignment)
    due = _as_int(assignment.get("duedate"))
    cutoff = _as_int(assignment.get("cutoffdate"))
    opens = _as_int(assignment.get("allowsubmissionsfromdate"))
    file_count = _as_int(
        configs.get(("file", "maxfilesubmission"), configs.get(("assignsubmission_file", "maxfilesubmission")))
    )
    submission_drafts = _as_int(configs.get(("assign", "submissiondrafts"))) == 1

    if _as_int(assignment.get("nosubmissions")):
        reasons.append("submissions-disabled")
    if _as_int(assignment.get("teamsubmission")):
        reasons.append("team-submission")
    if not submission_drafts:
        reasons.append("repeated-editing-not-confirmed")
    if file_count < 1:
        reasons.append("file-plugin-not-confirmed")
    if opens and opens > now:
        reasons.append("not-open")
    if not due or due < now + _MINIMUM_LEAD_SECONDS:
        reasons.append("due-within-safety-window")
    if cutoff and cutoff < now + _MINIMUM_LEAD_SECONDS:
        reasons.append("cutoff-within-safety-window")
    return tuple(reasons)


def _status_reasons(snapshot: SubmissionSnapshot, *, initial: bool) -> tuple[str, ...]:
    reasons: list[str] = []
    if initial and snapshot.raw_status != "new":
        reasons.append("status-not-new")
    if not initial and snapshot.raw_status not in {"new", "draft"}:
        reasons.append("cleanup-status-unsafe")
    if snapshot.remote_files:
        reasons.append("remote-files-present")
    if snapshot.online_text.strip():
        reasons.append("online-text-present")
    if not snapshot.submissions_enabled:
        reasons.append("submissions-disabled")
    if not snapshot.can_edit:
        reasons.append("not-editable")
    if snapshot.locked:
        reasons.append("locked")
    if snapshot.graded:
        reasons.append("graded")
    if not snapshot.submission_drafts:
        reasons.append("repeated-editing-not-confirmed")
    return tuple(reasons)


def _candidate_from(
    course: dict[str, Any], assignment: dict[str, Any], status: Any, now: int
) -> tuple[_Candidate | None, tuple[str, ...]]:
    course_id = _as_int(course.get("id", assignment.get("course")))
    assignment_id = _as_int(assignment.get("id"))
    cmid = _as_int(assignment.get("cmid"))
    if not course_id or not assignment_id or not cmid or not isinstance(status, dict):
        return None, ("invalid-response-shape",)
    snapshot = parse_submission_snapshot(assignment_id, assignment, status)
    reasons = _assignment_precheck(assignment, now) + _status_reasons(snapshot, initial=True)
    if reasons:
        return None, reasons

    probe = SelectedFile("synthetic.txt", 64, "text/plain")
    if validate_desired_files(snapshot, (probe,)):
        return None, ("synthetic-file-not-allowed",)
    return _Candidate(course_id, assignment_id, cmid, assignment, snapshot), ()


def _discover_candidate(client: MoodleClient) -> _Candidate | None:
    response = client.call_ws_api("mod_assign_get_assignments")
    now = int(time.time())
    assignments = [
        (course, assignment)
        for course in _courses(response)
        for assignment in course.get("assignments", ())
        if isinstance(assignment, dict) and not _assignment_precheck(assignment, now)
    ]
    assignments.sort(key=lambda pair: (_as_int(pair[1].get("duedate")), _as_int(pair[1].get("id"))))
    for course, assignment in assignments:
        assignment_id = _as_int(assignment.get("id"))
        status = client.call_ws_api("mod_assign_get_submission_status", assignid=assignment_id)
        candidate, _ = _candidate_from(course, assignment, status, now)
        if candidate is not None:
            return candidate
    return None


def _fresh_candidate(client: MoodleClient, selected: _Candidate) -> _Candidate | None:
    response = client.call_ws_api(
        "mod_assign_get_assignments", **{"courseids[0]": selected.course_id}
    )
    for course in _courses(response):
        for assignment in course.get("assignments", ()):
            if isinstance(assignment, dict) and _as_int(assignment.get("id")) == selected.assignment_id:
                status = client.call_ws_api(
                    "mod_assign_get_submission_status", assignid=selected.assignment_id
                )
                candidate, _ = _candidate_from(course, assignment, status, int(time.time()))
                return candidate
    return None


def _fresh_cleanup_snapshot(
    client: MoodleClient,
    selected: _Candidate,
    expected_identity: tuple[str, str],
) -> tuple[_Candidate | None, bool]:
    response = client.call_ws_api(
        "mod_assign_get_assignments", **{"courseids[0]": selected.course_id}
    )
    now = int(time.time())
    for course in _courses(response):
        for assignment in course.get("assignments", ()):
            if not isinstance(assignment, dict) or _as_int(assignment.get("id")) != selected.assignment_id:
                continue
            status = client.call_ws_api(
                "mod_assign_get_submission_status", assignid=selected.assignment_id
            )
            if not isinstance(status, dict):
                return None, False
            snapshot = parse_submission_snapshot(selected.assignment_id, assignment, status)
            if _assignment_precheck(assignment, now):
                return None, False
            base_reasons = tuple(
                reason for reason in _status_reasons(snapshot, initial=False) if reason != "remote-files-present"
            )
            identities = set(snapshot.remote_identities)
            if base_reasons or not identities.issubset({expected_identity}):
                return None, False
            return _Candidate(selected.course_id, selected.assignment_id, selected.cmid, assignment, snapshot), not identities
    return None, False


def _clear_exact_probe_file(
    client: MoodleClient,
    workflow: SubmissionWorkflow,
    target: SubmissionTarget,
    selected: _Candidate,
    identity: tuple[str, str],
) -> SubmissionSnapshot:
    fresh, already_absent = _fresh_cleanup_snapshot(client, selected, identity)
    if fresh is None:
        raise AssertionError("cleanup risk: fresh editable empty/draft state could not be confirmed")
    if already_absent:
        return fresh.snapshot

    result = workflow.mutate_files(
        target,
        FileMutationIntent(
            operation=MutationOperation.CLEAR,
            expected_fingerprint=fresh.snapshot.fingerprint,
        ),
    )
    if not result.ok or result.snapshot is None:
        raise AssertionError("cleanup risk: Moodle rejected the exact idempotent clear")
    if identity in result.snapshot.remote_identities or result.snapshot.remote_files:
        raise AssertionError("cleanup risk: generated assignment file remains")
    return result.snapshot


def run_empty_assignment_probe() -> _AssignmentProbeReport:
    """Write and clear one file only after every live safety gate passes freshly."""
    client = _live_client()
    selected = _discover_candidate(client)
    if selected is None:
        pytest.skip(
            "no assignment met every gate: new, empty, editable, unlocked, ungraded, "
            "file-enabled, repeatedly editable, and at least seven days before due/cutoff"
        )

    fresh = _fresh_candidate(client, selected)
    if fresh is None:
        pytest.skip("the selected empty assignment failed its immediate pre-save recheck")

    prefix = uuid4().hex
    name = f"uthelper-live-{prefix}.txt"
    content = f"synthetic-assignment-probe-{prefix}".encode()
    identity = ("/", name)
    target = SubmissionTarget(
        url=f"{settings.MOODLE_BASE_URL}/mod/assign/view.php?id={fresh.cmid}",
        course_id=fresh.course_id,
    )
    workflow = SubmissionWorkflow(client)
    cleared: SubmissionSnapshot | None = None
    mutation_started = False
    try:
        mutation_started = True
        saved = workflow.mutate_files(
            target,
            FileMutationIntent(
                operation=MutationOperation.ADD,
                selected_files=(SelectedFile(name, len(content), "text/plain"),),
                expected_fingerprint=fresh.snapshot.fingerprint,
            ),
            selected_files=(SelectedSubmissionFile(name, content),),
        )
        if not saved.ok or saved.snapshot is None or saved.snapshot.remote_identities != (identity,):
            raise AssertionError("the synthetic assignment save was not verified exactly")

        cleared = _clear_exact_probe_file(client, workflow, target, fresh, identity)
    finally:
        if mutation_started and (cleared is None or identity in cleared.remote_identities):
            cleared = _clear_exact_probe_file(client, workflow, target, fresh, identity)

    final, absent = _fresh_cleanup_snapshot(client, fresh, identity)
    if final is None or not absent:
        raise AssertionError("cleanup risk: final generated-file absence could not be verified")
    return _AssignmentProbeReport(
        cleanup_absence_verified=True,
        empty_submission_record_remains=(
            final.snapshot.submission_id > 0 or final.snapshot.raw_status != "new"
        ),
    )


def test_live_probe_never_calls_assignment_mutation(monkeypatch):
    logging.disable(logging.CRITICAL)
    spy = live_call_spy(monkeypatch)
    try:
        run_unlinked_draft_probe()
    finally:
        logging.disable(logging.NOTSET)
    assert _ASSIGNMENT_MUTATIONS.isdisjoint(spy.called_functions)


def test_live_empty_assignment_write_delete(monkeypatch):
    logging.disable(logging.CRITICAL)
    spy = live_call_spy(monkeypatch)
    try:
        report = run_empty_assignment_probe()
    finally:
        logging.disable(logging.NOTSET)
    assert report.cleanup_absence_verified
    assert _FORBIDDEN_FINALIZE not in spy.called_functions
    if report.empty_submission_record_remains:
        warnings.warn(
            "live-safe probe left the allowed empty submission-record residual",
            stacklevel=1,
        )
