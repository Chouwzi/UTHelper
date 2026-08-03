"""Stateful Moodle 4.3 protocol tests for the public submission workflow."""

from dataclasses import dataclass

import pytest

from core.submission_models import FileMutationIntent, MutationOperation, SelectedFile
from core.use_cases.submission_workflow import (
    MutationOutcome,
    SelectedSubmissionFile,
    SubmissionErrorCode,
    SubmissionTarget,
    SubmissionWorkflow,
)
from tests.fixtures.moodle_submission_responses import FakeMoodle43


@dataclass(frozen=True)
class ProtocolWorkflow:
    workflow: SubmissionWorkflow
    server: FakeMoodle43


def target() -> SubmissionTarget:
    return SubmissionTarget(
        "https://courses.ut.edu.vn/mod/assign/view.php?id=123", 456
    )


def selected(name: str, content: bytes, filepath: str = "/") -> tuple[SelectedFile, SelectedSubmissionFile]:
    return (
        SelectedFile(
            name=name,
            size=len(content),
            mimetype="application/pdf",
            filepath=filepath,
        ),
        SelectedSubmissionFile(name, content, filepath),
    )


def mutation(
    operation: MutationOperation,
    *files: tuple[SelectedFile, SelectedSubmissionFile],
    remove: tuple[tuple[str, str], ...] = (),
    rename: tuple[str, str] | None = None,
    new_name: str = "",
    new_filepath: str = "/",
    fingerprint: str = "",
    finalize: bool = False,
    accept_statement: bool = False,
) -> tuple[FileMutationIntent, tuple[SelectedSubmissionFile, ...]]:
    metadata = tuple(item[0] for item in files)
    payloads = tuple(item[1] for item in files)
    return (
        FileMutationIntent(
            operation=operation,
            selected_files=metadata,
            remove_identities=remove,
            rename_identity=rename,
            new_name=new_name,
            new_filepath=new_filepath,
            expected_fingerprint=fingerprint,
            finalize=finalize,
            accept_statement=accept_statement,
        ),
        payloads,
    )


def run_mutation(
    protocol: ProtocolWorkflow,
    operation: MutationOperation,
    *files: tuple[SelectedFile, SelectedSubmissionFile],
    **kwargs,
):
    intent, payloads = mutation(operation, *files, **kwargs)
    return protocol.workflow.mutate_files(
        target(), intent, selected_files=payloads
    )


@pytest.fixture
def protocol_workflow() -> ProtocolWorkflow:
    server = FakeMoodle43(drafts=False, statement=False)
    return ProtocolWorkflow(SubmissionWorkflow(server), server)


@pytest.mark.parametrize("names", [("one.pdf",), ("one.pdf", "two.pdf")])
def test_first_submission_exact_set(names, protocol_workflow):
    files = tuple(selected(name, name.encode()) for name in names)

    result = run_mutation(protocol_workflow, MutationOperation.REPLACE, *files)

    assert result.ok is True
    assert result.outcome is MutationOutcome.SUBMISSION_SAVED
    assert protocol_workflow.server.remote_files == {
        ("/", name): name.encode() for name in names
    }


def test_append_after_existing_submission_preserves_old_bytes(protocol_workflow):
    protocol_workflow.server.remote_files[("/", "old.pdf")] = b"old bytes"

    result = run_mutation(
        protocol_workflow,
        MutationOperation.ADD,
        selected("new.pdf", b"new bytes"),
    )

    assert result.ok is True
    assert protocol_workflow.server.remote_files == {
        ("/", "old.pdf"): b"old bytes",
        ("/", "new.pdf"): b"new bytes",
    }


def test_replace_removes_old_keys_only_after_successful_save(protocol_workflow):
    server = protocol_workflow.server
    server.remote_files.update({("/", "old.pdf"): b"old", ("/proof/", "keep.pdf"): b"keep"})

    result = run_mutation(
        protocol_workflow,
        MutationOperation.REPLACE,
        selected("replacement.pdf", b"replacement"),
    )

    assert result.ok is True
    assert server.remote_files == {("/", "replacement.pdf"): b"replacement"}
    assert server.remote_sets_before_save == [
        {("/", "old.pdf"): b"old", ("/proof/", "keep.pdf"): b"keep"}
    ]


@pytest.mark.parametrize(
    ("operation", "removed", "remaining"),
    [
        (MutationOperation.REMOVE, (("/", "a.pdf"),), {("/", "b.pdf"), ("/proof/", "c.pdf")}),
        (MutationOperation.REMOVE, (("/", "a.pdf"), ("/proof/", "c.pdf")), {("/", "b.pdf")}),
        (MutationOperation.CLEAR, (), set()),
    ],
)
def test_remove_one_many_or_all_produces_exact_remaining_set(
    protocol_workflow, operation, removed, remaining
):
    protocol_workflow.server.remote_files.update(
        {
            ("/", "a.pdf"): b"a",
            ("/", "b.pdf"): b"b",
            ("/proof/", "c.pdf"): b"c",
        }
    )

    result = run_mutation(
        protocol_workflow,
        operation,
        remove=removed,
    )

    assert result.ok is True
    assert set(protocol_workflow.server.remote_files) == remaining


def test_rename_and_path_move_preserve_exact_bytes(protocol_workflow):
    protocol_workflow.server.remote_files.update(
        {("/", "move.pdf"): b"exact bytes", ("/", "other.pdf"): b"other"}
    )

    result = run_mutation(
        protocol_workflow,
        MutationOperation.RENAME,
        rename=("/", "move.pdf"),
        new_name="renamed.pdf",
        new_filepath="evidence",
    )

    assert result.ok is True
    assert protocol_workflow.server.remote_files == {
        ("/evidence/", "renamed.pdf"): b"exact bytes",
        ("/", "other.pdf"): b"other",
    }


def test_same_item_duplicate_rejection_cleans_tracked_uploads(protocol_workflow):
    server = protocol_workflow.server
    server.preseed_duplicate_upload_number = 2

    result = run_mutation(
        protocol_workflow,
        MutationOperation.REPLACE,
        selected("first.pdf", b"first"),
        selected("answer.pdf", b"answer", "proof"),
    )

    assert result.ok is False
    assert result.issue.code is SubmissionErrorCode.UPLOAD_FAILED
    assert server.save_calls == 0
    draft_id = server.allocated_itemids[0]
    assert server.duplicate_collisions == [
        (draft_id, ("/proof/", "answer.pdf"))
    ]
    assert server.drafts[draft_id] == {}
    assert server.cleanup_calls == [
        (draft_id, (("/", "first.pdf"),))
    ]


def test_concurrent_status_change_aborts_before_save(protocol_workflow):
    displayed = protocol_workflow.workflow.load_snapshot(target()).snapshot
    protocol_workflow.server.remote_files[("/", "concurrent.pdf")] = b"other"

    result = run_mutation(
        protocol_workflow,
        MutationOperation.ADD,
        selected("new.pdf", b"new"),
        fingerprint=displayed.fingerprint,
    )

    assert result.issue.code is SubmissionErrorCode.STALE_SNAPSHOT
    assert protocol_workflow.server.save_calls == 0
    assert protocol_workflow.server.allocated_itemids == []


def test_draft_statement_submission_changes_status_only_after_accepted_finalize():
    server = FakeMoodle43(drafts=True, statement=True)
    protocol = ProtocolWorkflow(SubmissionWorkflow(server), server)
    file = selected("answer.pdf", b"answer")

    saved = run_mutation(protocol, MutationOperation.REPLACE, file)
    rejected = run_mutation(
        protocol,
        MutationOperation.REPLACE,
        file,
        finalize=True,
    )

    assert saved.ok is True
    assert saved.outcome is MutationOutcome.DRAFT_SAVED
    assert rejected.issue.code is SubmissionErrorCode.STATEMENT_NOT_ACCEPTED
    assert rejected.partial is True
    assert server.submission_status == "draft"
    assert server.finalize_attempts == []

    finalized = run_mutation(
        protocol,
        MutationOperation.REPLACE,
        file,
        finalize=True,
        accept_statement=True,
    )

    assert finalized.ok is True
    assert finalized.outcome is MutationOutcome.SUBMITTED_FOR_GRADING
    assert server.submission_status == "submitted"
    assert server.finalize_attempts == [True]
