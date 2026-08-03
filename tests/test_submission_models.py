from dataclasses import replace

import pytest

from core.submission_models import SelectedFile
from core.submission_snapshot import parse_submission_snapshot, validate_desired_files
from tests.fixtures.moodle_submission_responses import assignment_fixture, editable_status_fixture


def selected(name: str, size: int = 1, mimetype: str = "") -> SelectedFile:
    return SelectedFile(name=name, size=size, mimetype=mimetype)


def snapshot_fixture():
    return parse_submission_snapshot(77, assignment_fixture(), editable_status_fixture())


def test_parse_snapshot_uses_lastattempt_permissions_and_file_limits():
    snapshot = parse_submission_snapshot(77, assignment_fixture(), editable_status_fixture())

    assert snapshot.can_edit is True
    assert snapshot.submission_drafts is True
    assert snapshot.statement_required is True
    assert snapshot.maximum_file_count == 2
    assert snapshot.maximum_file_bytes == 1_048_576
    assert snapshot.accepted_file_types == (".pdf",)
    assert snapshot.remote_files[0].identity == ("/", "old.pdf")
    assert snapshot.online_text == "<p>Keep <em>this</em></p>"


def test_parse_snapshot_captures_complete_live_safety_state_from_real_shapes():
    assignment = assignment_fixture()
    assignment.update(
        {
            "submissiondrafts": 1,
            "duedate": 1_800_000_000,
            "cutoffdate": 0,
            "allowsubmissionsfromdate": 1_700_000_000,
            "teamsubmission": 0,
        }
    )
    assignment["configs"].append(
        {
            "subtype": "assignsubmission",
            "plugin": "file",
            "name": "enabled",
            "value": "1",
        }
    )

    snapshot = parse_submission_snapshot(77, assignment, editable_status_fixture())

    assert snapshot.submission_drafts is True
    assert snapshot.file_submission_enabled is True
    assert snapshot.team_submission is False
    assert snapshot.due_date == 1_800_000_000
    assert snapshot.cutoff_date == 0
    assert snapshot.allows_submissions_from_date == 1_700_000_000


def test_top_level_submissiondrafts_wins_with_config_fallback():
    assignment = assignment_fixture()
    assignment["submissiondrafts"] = 0
    assert parse_submission_snapshot(77, assignment, editable_status_fixture()).submission_drafts is False

    assignment.pop("submissiondrafts")
    assert parse_submission_snapshot(77, assignment, editable_status_fixture()).submission_drafts is True


def test_file_submission_requires_explicit_enabled_config_and_status_plugin():
    assignment = assignment_fixture()
    without_enabled = parse_submission_snapshot(77, assignment, editable_status_fixture())
    assert without_enabled.file_submission_enabled is False

    assignment["configs"].append(
        {
            "subtype": "assignsubmission",
            "plugin": "file",
            "name": "enabled",
            "value": 1,
        }
    )
    status = editable_status_fixture()
    status["lastattempt"]["submission"]["plugins"] = [
        plugin
        for plugin in status["lastattempt"]["submission"]["plugins"]
        if plugin["type"] != "file"
    ]
    without_status_plugin = parse_submission_snapshot(77, assignment, status)
    assert without_status_plugin.file_submission_enabled is False


def test_parse_snapshot_prefers_lastattempt_submissions_enabled_over_assignment_fallback():
    assignment = assignment_fixture()
    assignment["nosubmissions"] = False
    status = editable_status_fixture()
    status["lastattempt"]["submissionsenabled"] = False

    snapshot = parse_submission_snapshot(77, assignment, status)

    assert snapshot.submissions_enabled is False


def test_parse_snapshot_uses_assignment_compatibility_fallback_when_attempt_flag_missing():
    assignment = assignment_fixture()
    assignment["nosubmissions"] = True
    status = editable_status_fixture()

    snapshot = parse_submission_snapshot(77, assignment, status)

    assert snapshot.submissions_enabled is False


def test_snapshot_fingerprint_excludes_authenticated_url_query():
    first = parse_submission_snapshot(77, assignment_fixture(), editable_status_fixture("?token=one"))
    second = parse_submission_snapshot(77, assignment_fixture(), editable_status_fixture("?token=two"))

    assert first.fingerprint == second.fingerprint


@pytest.mark.parametrize(
    ("changes"),
    [
        {"raw_status": "submitted"},
        {"can_edit": False},
        {"can_submit": False},
        {"locked": True},
        {"graded": True},
        {"online_text": "new private text"},
        {"online_text_format": 0},
        {"submission_drafts": False},
        {"statement_required": False},
        {"file_submission_enabled": False},
        {"maximum_file_count": 99},
        {"maximum_file_bytes": 99},
        {"accepted_file_types": (".txt",)},
        {"team_submission": True},
        {"due_date": 1_900_000_000},
        {"cutoff_date": 1_900_000_000},
        {"allows_submissions_from_date": 1_900_000_000},
        {"submissions_enabled": False},
        {"remote_files": ()},
    ],
)
def test_snapshot_fingerprint_covers_every_live_safety_invariant(changes):
    assignment = assignment_fixture()
    assignment["configs"].append(
        {
            "subtype": "assignsubmission",
            "plugin": "file",
            "name": "enabled",
            "value": 1,
        }
    )
    snapshot = parse_submission_snapshot(77, assignment, editable_status_fixture())

    assert replace(snapshot, **changes).fingerprint != snapshot.fingerprint


def test_snapshot_repr_does_not_expose_online_text():
    snapshot = snapshot_fixture()

    assert snapshot.online_text not in repr(snapshot)


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


def test_validation_supports_mime_wildcards_and_moodle_file_type_groups():
    image_snapshot = parse_submission_snapshot(
        77,
        {
            "configs": [
                {
                    "subtype": "assignsubmission",
                    "plugin": "file",
                    "name": "acceptedfiletypes",
                    "value": "image,application/pdf",
                }
            ]
        },
        editable_status_fixture(),
    )

    assert validate_desired_files(image_snapshot, [selected("photo.unknown", mimetype="image/png")]) == ()
    assert validate_desired_files(image_snapshot, [selected("report.pdf", mimetype="application/pdf")]) == ()


def test_validation_normalizes_filepath_before_duplicate_detection():
    snapshot = parse_submission_snapshot(77, assignment_fixture(), editable_status_fixture())
    first = SelectedFile(name="same.pdf", size=1, filepath="folder")
    second = SelectedFile(name="same.pdf", size=1, filepath="/folder/")

    assert "duplicate_filename" in {issue.code.value for issue in validate_desired_files(snapshot, [first, second])}
