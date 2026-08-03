from core.submission_models import RemoteFile, SubmissionSnapshot
from core.use_cases.submission_workflow import (
    MutationOutcome,
    SubmissionError,
    SubmissionErrorCode,
    SubmissionMutationResult,
)
from gui.components.detail.submission_presenter import (
    SubmissionUiPolicy,
    mutation_message,
)


def snapshot(**changes) -> SubmissionSnapshot:
    values = {
        "assignment_id": 42,
        "raw_status": "draft",
        "can_edit": True,
        "can_submit": True,
        "locked": False,
        "graded": False,
        "submissions_enabled": True,
        "submission_drafts": False,
        "statement_required": False,
        "file_submission_enabled": True,
        "team_submission": False,
        "due_date": 0,
        "cutoff_date": 0,
        "allows_submissions_from_date": 0,
        "maximum_file_count": 3,
        "maximum_file_bytes": 10 * 1024 * 1024,
        "accepted_file_types": (".pdf", ".docx"),
        "remote_files": (),
        "online_text": "",
        "online_text_format": 1,
        "attempt_number": 0,
        "submission_id": 7,
        "submission_modified_time": 1_700_000_000,
    }
    values.update(changes)
    return SubmissionSnapshot(**values)


def remote(name: str = "server.pdf") -> RemoteFile:
    return RemoteFile(
        name=name,
        filepath="/",
        size=1024,
        mimetype="application/pdf",
        modified_time=1_700_000_000,
        url="https://moodle.invalid/token-secret/server.pdf",
    )


def test_locked_snapshot_hides_all_mutating_controls():
    policy = SubmissionUiPolicy.from_snapshot(snapshot(locked=True, can_edit=False))

    assert policy.show_picker is False
    assert policy.show_file_actions is False
    assert policy.edit_reason == "Bài nộp đã bị khóa trên Moodle."


def test_draft_assignment_exposes_separate_save_and_final_actions():
    policy = SubmissionUiPolicy.from_snapshot(
        snapshot(submission_drafts=True, can_submit=True)
    )

    assert policy.show_save_draft is True
    assert policy.show_finalize is True


def test_non_draft_assignment_uses_single_save_action():
    policy = SubmissionUiPolicy.from_snapshot(snapshot(submission_drafts=False))

    assert policy.show_save_submission is True
    assert policy.primary_action_label == "Lưu bài nộp"


def test_policy_formats_server_limits_for_people():
    policy = SubmissionUiPolicy.from_snapshot(snapshot())

    assert policy.limit_text == "Tối đa 3 file · 10 MB · .pdf, .docx"


def test_online_text_only_draft_exposes_finalize_but_no_file_controls():
    policy = SubmissionUiPolicy.from_snapshot(
        snapshot(
            submission_drafts=True,
            file_submission_enabled=False,
            online_text="<p>Online answer</p>",
        )
    )

    assert policy.show_picker is False
    assert policy.show_file_actions is False
    assert policy.show_save_draft is False
    assert policy.show_finalize is True


def test_team_submission_uses_browser_fallback_reason_and_hides_mutations():
    policy = SubmissionUiPolicy.from_snapshot(
        snapshot(
            submission_drafts=True,
            team_submission=True,
            remote_files=(remote(),),
        )
    )

    assert policy.show_picker is False
    assert policy.show_file_actions is False
    assert policy.show_save_draft is False
    assert policy.show_finalize is False
    assert "trình duyệt" in policy.edit_reason


def test_every_workflow_error_has_a_safe_vietnamese_message():
    secret = "https://moodle.invalid/?token=SECRET"

    for code in SubmissionErrorCode:
        result = SubmissionMutationResult.failure(
            SubmissionError(code, secret), snapshot(remote_files=(remote(),))
        )
        message = mutation_message(result)

        assert message
        assert secret not in message
        assert "SECRET" not in message


def test_mutation_outcomes_have_distinct_messages():
    messages = {
        outcome: mutation_message(SubmissionMutationResult.success(snapshot(), outcome))
        for outcome in MutationOutcome
    }

    assert len(set(messages.values())) == len(MutationOutcome)
    assert all(message for message in messages.values())
