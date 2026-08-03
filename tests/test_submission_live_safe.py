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

import config
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


_LIVE_ONLY = pytest.mark.skipif(
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


class _LiveAuthUnavailable(RuntimeError):
    pass


class _IsolatedLiveMoodleClient(MoodleClient):
    """In-memory WS token client with no global cache read, refresh, or write."""

    def __init__(self, token: str):
        super().__init__()
        self.__live_token = token

    def _get_ws_token(self, *args: Any, **kwargs: Any) -> str:
        return self.__live_token

    def call_ws_api(self, function: str, **params: Any) -> Any:
        request_params = {
            "wstoken": self.__live_token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **params,
        }
        _, result = self._post(
            f"{settings.MOODLE_BASE_URL}/webservice/rest/server.php",
            request_params,
            timeout=20,
        )
        return result


def live_call_spy(monkeypatch: pytest.MonkeyPatch) -> _LiveCallSpy:
    """Record WS function names without retaining params, tokens, or responses."""
    spy = _LiveCallSpy()
    original = _IsolatedLiveMoodleClient.call_ws_api

    def recorded(client: _IsolatedLiveMoodleClient, function: str, **params: Any) -> Any:
        spy.called_functions.append(function)
        if function == _FORBIDDEN_FINALIZE:
            raise AssertionError("live-safe probes must never finalize a submission")
        return original(client, function, **params)

    monkeypatch.setattr(_IsolatedLiveMoodleClient, "call_ws_api", recorded)
    return spy


def _verified_isolated_client(token: str, expected_username: str) -> _IsolatedLiveMoodleClient:
    client = _IsolatedLiveMoodleClient(token)
    site_info = client.call_ws_api("core_webservice_get_site_info")
    actual_username = site_info.get("username") if isinstance(site_info, dict) else None
    if not isinstance(actual_username, str) or actual_username != expected_username:
        raise _LiveAuthUnavailable("live token account identity could not be verified")
    return client


def _create_isolated_live_client() -> _IsolatedLiveMoodleClient:
    """Use env credentials first, otherwise a verified secure-keyring token only."""
    user = os.environ.get("UTH_TEST_USER", "")
    password = os.environ.get("UTH_TEST_PASS", "")
    if bool(user) != bool(password):
        raise _LiveAuthUnavailable("live credentials are incomplete")

    if user and password:
        bootstrap = MoodleClient()
        _, result = bootstrap._post(  # noqa: SLF001
            f"{settings.MOODLE_BASE_URL}/login/token.php",
            {"username": user, "password": password, "service": "moodle_mobile_app"},
            timeout=15,
        )
        token = result.get("token") if isinstance(result, dict) else None
        if not isinstance(token, str) or not token:
            raise _LiveAuthUnavailable("environment credential authentication failed")
        return _verified_isolated_client(token, user)

    if not config._HAS_KEYRING:  # noqa: SLF001
        raise _LiveAuthUnavailable("secure keyring is unavailable")
    expected_username = settings.UTH_USERNAME
    token = config._read_secret("ws_token")  # noqa: SLF001
    if not expected_username or not token:
        raise _LiveAuthUnavailable("verified secure live authentication is unavailable")
    return _verified_isolated_client(token, expected_username)


def _live_client() -> _IsolatedLiveMoodleClient:
    try:
        return _create_isolated_live_client()
    except _LiveAuthUnavailable as exc:
        pytest.skip(str(exc))


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

        duplicate = client.upload_draft_file_result(
            names[0],
            f"synthetic-intentional-duplicate-{prefix}".encode(),
            item_id,
            "/",
        )
        assert duplicate.record is None
        assert duplicate.error_code == "filenameexist"
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


def _coarse_assignment_reasons(assignment: dict[str, Any], now: int) -> tuple[str, ...]:
    reasons: list[str] = []
    configs = _config_values(assignment)
    due = _as_int(assignment.get("duedate"))
    cutoff = _as_int(assignment.get("cutoffdate"))
    opens = _as_int(assignment.get("allowsubmissionsfromdate"))
    raw_submission_drafts = assignment.get("submissiondrafts")
    if raw_submission_drafts is None:
        raw_submission_drafts = configs.get(("assign", "submissiondrafts"))
    submission_drafts = _as_int(raw_submission_drafts) == 1
    file_enabled = _as_int(
        configs.get(("file", "enabled"), configs.get(("assignsubmission_file", "enabled")))
    ) == 1

    if _as_int(assignment.get("nosubmissions")):
        reasons.append("submissions-disabled")
    if _as_int(assignment.get("teamsubmission")):
        reasons.append("team-submission")
    if not submission_drafts:
        reasons.append("repeated-editing-not-confirmed")
    if not file_enabled:
        reasons.append("file-plugin-not-confirmed")
    if opens and opens > now:
        reasons.append("not-open")
    if due and due < now + _MINIMUM_LEAD_SECONDS:
        reasons.append("due-within-safety-window")
    if cutoff and cutoff < now + _MINIMUM_LEAD_SECONDS:
        reasons.append("cutoff-within-safety-window")
    return tuple(reasons)


def _snapshot_safety_reasons(
    snapshot: SubmissionSnapshot,
    now: int,
    *,
    initial: bool,
    expected_identity: tuple[str, str] | None = None,
    probe: SelectedFile | None = None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if initial and snapshot.raw_status != "new":
        reasons.append("status-not-new")
    if not initial and snapshot.raw_status not in {"new", "draft"}:
        reasons.append("cleanup-status-unsafe")
    identities = set(snapshot.remote_identities)
    allowed = set() if initial or expected_identity is None else {expected_identity}
    if not identities.issubset(allowed):
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
    if not snapshot.file_submission_enabled:
        reasons.append("file-plugin-not-confirmed")
    if snapshot.team_submission:
        reasons.append("team-submission")
    if snapshot.allows_submissions_from_date and snapshot.allows_submissions_from_date > now:
        reasons.append("not-open")
    if snapshot.due_date and snapshot.due_date < now + _MINIMUM_LEAD_SECONDS:
        reasons.append("due-within-safety-window")
    if snapshot.cutoff_date and snapshot.cutoff_date < now + _MINIMUM_LEAD_SECONDS:
        reasons.append("cutoff-within-safety-window")
    if probe is not None and validate_desired_files(snapshot, (probe,)):
        reasons.append("synthetic-file-not-allowed")
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
    probe = SelectedFile("synthetic.txt", 64, "text/plain")
    reasons = _coarse_assignment_reasons(assignment, now) + _snapshot_safety_reasons(
        snapshot, now, initial=True, probe=probe
    )
    if reasons:
        return None, reasons

    return _Candidate(course_id, assignment_id, cmid, assignment, snapshot), ()


def _discover_candidate(client: MoodleClient) -> _Candidate | None:
    response = client.call_ws_api("mod_assign_get_assignments")
    now = int(time.time())
    assignments = [
        (course, assignment)
        for course in _courses(response)
        for assignment in course.get("assignments", ())
        if isinstance(assignment, dict) and not _coarse_assignment_reasons(assignment, now)
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
            if _coarse_assignment_reasons(assignment, now):
                return None, False
            base_reasons = _snapshot_safety_reasons(
                snapshot,
                now,
                initial=False,
                expected_identity=expected_identity,
            )
            identities = set(snapshot.remote_identities)
            if base_reasons:
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
        safety_guard=lambda snapshot: not _snapshot_safety_reasons(
            snapshot,
            int(time.time()),
            initial=False,
            expected_identity=identity,
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
    probe = SelectedFile(name, len(content), "text/plain")
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
                selected_files=(probe,),
                expected_fingerprint=fresh.snapshot.fingerprint,
            ),
            selected_files=(SelectedSubmissionFile(name, content),),
            safety_guard=lambda snapshot: not _snapshot_safety_reasons(
                snapshot,
                int(time.time()),
                initial=True,
                probe=probe,
            ),
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


def test_environment_credentials_ignore_cached_account_and_do_not_persist(monkeypatch):
    monkeypatch.setenv("UTH_TEST_USER", "expected-user")
    monkeypatch.setenv("UTH_TEST_PASS", "synthetic-password")
    monkeypatch.setattr(settings, "UTH_USERNAME", "unrelated-user")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "unrelated-password")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "unrelated-cached-token")
    before = (
        settings.UTH_USERNAME,
        settings.UTH_PASSWORD,
        settings.MOODLE_WS_TOKEN,
    )
    calls: list[str] = []

    def fake_post(_client, url, data, timeout):
        del timeout
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("/login/token.php"):
            assert data["username"] == "expected-user"
            assert data["password"] == "synthetic-password"
            return 200, {"token": "isolated-test-token"}
        assert data["wstoken"] == "isolated-test-token"
        return 200, {"username": "expected-user"}

    monkeypatch.setattr(MoodleClient, "_post", fake_post)
    monkeypatch.setattr(
        config,
        "save_settings",
        lambda: pytest.fail("isolated live auth must not persist settings"),
    )

    client = _create_isolated_live_client()

    assert client._get_ws_token() == "isolated-test-token"  # noqa: SLF001
    assert calls == ["token.php", "server.php"]
    assert (settings.UTH_USERNAME, settings.UTH_PASSWORD, settings.MOODLE_WS_TOKEN) == before


def test_assignment_auth_rejects_plaintext_settings_fallback(monkeypatch):
    monkeypatch.delenv("UTH_TEST_USER", raising=False)
    monkeypatch.delenv("UTH_TEST_PASS", raising=False)
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(settings, "UTH_USERNAME", "configured-user")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "plaintext-fallback-token")

    with pytest.raises(_LiveAuthUnavailable, match="secure keyring"):
        _create_isolated_live_client()


def test_secure_token_account_identity_must_match_expected_username(monkeypatch):
    monkeypatch.delenv("UTH_TEST_USER", raising=False)
    monkeypatch.delenv("UTH_TEST_PASS", raising=False)
    monkeypatch.setattr(config, "_HAS_KEYRING", True)
    monkeypatch.setattr(config, "_read_secret", lambda key: "secure-test-token")
    monkeypatch.setattr(settings, "UTH_USERNAME", "expected-user")
    monkeypatch.setattr(
        MoodleClient,
        "_post",
        lambda *_args, **_kwargs: (200, {"username": "different-user"}),
    )

    with pytest.raises(_LiveAuthUnavailable, match="identity"):
        _create_isolated_live_client()


def _real_shape_candidate(
    now: int,
    *,
    due: int = 0,
    cutoff: int = 0,
    top_level_drafts: int | None = 1,
    config_drafts: int = 0,
    file_enabled: int | None = 1,
    include_status_file_plugin: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    assignment: dict[str, Any] = {
        "id": 77,
        "cmid": 123,
        "course": 456,
        "nosubmissions": 0,
        "teamsubmission": 0,
        "duedate": due,
        "cutoffdate": cutoff,
        "allowsubmissionsfromdate": now - 60,
        "configs": [
            {
                "subtype": "assign",
                "plugin": "assign",
                "name": "submissiondrafts",
                "value": config_drafts,
            },
            {
                "subtype": "assignsubmission",
                "plugin": "file",
                "name": "maxfilesubmission",
                "value": 1,
            },
            {
                "subtype": "assignsubmission",
                "plugin": "file",
                "name": "maxsubmissionsizebytes",
                "value": 1024,
            },
            {
                "subtype": "assignsubmission",
                "plugin": "file",
                "name": "acceptedfiletypes",
                "value": ".txt",
            },
        ],
    }
    if top_level_drafts is not None:
        assignment["submissiondrafts"] = top_level_drafts
    if file_enabled is not None:
        assignment["configs"].append(
            {
                "subtype": "assignsubmission",
                "plugin": "file",
                "name": "enabled",
                "value": file_enabled,
            }
        )
    plugins = []
    if include_status_file_plugin:
        plugins.append({"type": "file", "fileareas": [{"files": []}]})
    status = {
        "lastattempt": {
            "submission": {"id": 0, "status": "new", "plugins": plugins},
            "canedit": True,
            "cansubmit": True,
            "locked": False,
            "graded": False,
            "submissionsenabled": True,
        }
    }
    return assignment, status


@pytest.mark.parametrize(
    ("due_offset", "cutoff_offset", "eligible"),
    [
        (0, 0, True),
        (_MINIMUM_LEAD_SECONDS, 0, True),
        (0, _MINIMUM_LEAD_SECONDS, True),
        (_MINIMUM_LEAD_SECONDS - 1, 0, False),
        (0, _MINIMUM_LEAD_SECONDS - 1, False),
    ],
)
def test_candidate_deadline_zero_means_unbounded_and_nonzero_respects_window(
    due_offset, cutoff_offset, eligible
):
    now = 1_800_000_000
    assignment, status = _real_shape_candidate(
        now,
        due=now + due_offset if due_offset else 0,
        cutoff=now + cutoff_offset if cutoff_offset else 0,
    )

    candidate, reasons = _candidate_from({"id": 456}, assignment, status, now)

    assert (candidate is not None) is eligible
    assert bool(reasons) is not eligible


@pytest.mark.parametrize(
    ("shape_changes", "eligible"),
    [
        ({"top_level_drafts": 1, "config_drafts": 0}, True),
        ({"top_level_drafts": None, "config_drafts": 1}, True),
        ({"file_enabled": None}, False),
        ({"file_enabled": 0}, False),
        ({"include_status_file_plugin": False}, False),
    ],
)
def test_candidate_requires_explicit_file_enablement_and_fresh_status_capability(
    shape_changes, eligible
):
    now = 1_800_000_000
    assignment, status = _real_shape_candidate(now, **shape_changes)

    candidate, _ = _candidate_from({"id": 456}, assignment, status, now)

    assert (candidate is not None) is eligible


@_LIVE_ONLY
def test_live_probe_never_calls_assignment_mutation(monkeypatch):
    logging.disable(logging.CRITICAL)
    spy = live_call_spy(monkeypatch)
    try:
        run_unlinked_draft_probe()
    finally:
        logging.disable(logging.NOTSET)
    assert _ASSIGNMENT_MUTATIONS.isdisjoint(spy.called_functions)


@_LIVE_ONLY
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
