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
