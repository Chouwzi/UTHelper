"""Draft file transport tests without a live Moodle server."""
from unittest.mock import Mock

from core.client import MoodleClient
from core.moodle_service import MoodleService


def configured_client(monkeypatch):
    client = MoodleClient()
    monkeypatch.setattr(client, "_get_ws_token", Mock(return_value="test-token"))
    return client


def test_upload_draft_file_record_sends_normalized_filepath(monkeypatch):
    client = configured_client(monkeypatch)
    client._post_multipart = Mock(return_value=(200, [{
        "itemid": 900, "filename": "answer.pdf", "filepath": "/proof/"
    }]))

    record = client.upload_draft_file_record("answer.pdf", b"pdf", 900, "proof")

    assert record.itemid == 900
    assert record.identity == ("/proof/", "answer.pdf")
    fields = client._post_multipart.call_args.kwargs["fields"]
    assert fields["filepath"] == "/proof/"
    assert "author" not in fields
    assert "license" not in fields


def test_upload_draft_file_record_rejects_unsuccessful_or_malformed_response(monkeypatch):
    client = configured_client(monkeypatch)
    client._post_multipart = Mock(return_value=(500, []))
    assert client.upload_draft_file_record("answer.pdf", b"pdf") is None


def test_upload_draft_file_result_preserves_sanitized_moodle_error_code(monkeypatch):
    client = configured_client(monkeypatch)
    client._post_multipart = Mock(
        return_value=(200, {"errorcode": "filenameexist", "error": "already exists"})
    )

    result = client.upload_draft_file_result("answer.pdf", b"synthetic", 900)

    assert result.record is None
    assert result.error_code == "filenameexist"
    assert "synthetic" not in repr(result)

    client._post_multipart = Mock(return_value=(200, [{"itemid": "not-an-id"}]))
    assert client.upload_draft_file_record("answer.pdf", b"pdf") is None


def test_legacy_upload_returns_only_draft_itemid(monkeypatch):
    client = configured_client(monkeypatch)
    client.upload_draft_file_record = Mock(return_value=Mock(itemid=900))

    assert client.upload_draft_file("answer.pdf", b"pdf", itemid=17) == 900
    client.upload_draft_file_record.assert_called_once_with("answer.pdf", b"pdf", 17, "/")


def test_delete_draft_files_uses_each_tracked_identity_and_requires_confirmation():
    call_api = Mock(return_value={"parentpaths": []})
    service = MoodleService(call_api)

    assert service.delete_draft_files(900, (("/proof/", "answer.pdf"), ("/", "notes.txt"))) is True
    assert call_api.call_args.kwargs == {
        "draftitemid": 900,
        "files[0][filepath]": "/proof/",
        "files[0][filename]": "answer.pdf",
        "files[1][filepath]": "/",
        "files[1][filename]": "notes.txt",
    }

    assert MoodleService(Mock(return_value={})).delete_draft_files(900, (("/", "answer.pdf"),)) is False
    warning_result = {"parentpaths": [], "warnings": [{"message": "not deleted"}]}
    assert MoodleService(Mock(return_value=warning_result)).delete_draft_files(900, (("/", "answer.pdf"),)) is False


def test_service_allocates_draft_itemid_and_delegates_structured_mutations():
    call_api = Mock(side_effect=[{"itemid": 901}, [], []])
    service = MoodleService(call_api)

    assert service.get_unused_draft_itemid() == 901
    assert service.save_assignment_submission_result(77, 900, "text", 1, 901).ok is True
    assert service.submit_for_grading_result(77, False).ok is True
    assert call_api.call_args.kwargs["acceptsubmissionstatement"] == 0
