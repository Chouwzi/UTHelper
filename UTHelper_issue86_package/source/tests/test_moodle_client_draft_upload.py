"""Draft file transport tests without a live Moodle server."""
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest

from core.client import MoodleClient, _SameMoodleOriginRedirectHandler
from core.moodle_service import MoodleService


LIVE_DUPLICATE_RESPONSE_ENVELOPE = [
    {
        "error": "synthetic duplicate",
        "errortype": "filenameexist",
        "filename": "synthetic.txt",
        "filepath": "/synthetic/",
        "size": 0,
    }
]


class _HtmlResponse:
    def __init__(self, url: str, body: str, status: int = 200):
        self.url = url
        self.body = body.encode()
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, _limit: int = -1):
        return self.body


class _MoodleWebOpener:
    def __init__(self):
        self.requests = []
        self.responses = iter(
            (
                _HtmlResponse(
                    "https://courses.ut.edu.vn/login/index.php",
                    '<input type="hidden" name="logintoken" value="login-csrf">',
                ),
                _HtmlResponse(
                    "https://courses.ut.edu.vn/my/",
                    '<a href="/login/logout.php?sesskey=sessionkey">Logout</a>',
                ),
                _HtmlResponse(
                    "https://courses.ut.edu.vn/mod/assign/view.php?id=123&action=removesubmissionconfirm",
                    """
                    <form method="post" action="/mod/assign/view.php">
                      <input name="id" value="123">
                      <input name="action" value="removesubmission">
                      <input name="userid" value="42">
                      <input name="sesskey" value="sessionkey">
                    </form>
                    """,
                ),
                _HtmlResponse(
                    "https://courses.ut.edu.vn/mod/assign/view.php?id=123",
                    "removed",
                ),
            )
        )

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return next(self.responses)


def test_moodle_web_redirect_handler_rejects_cross_origin_location():
    handler = _SameMoodleOriginRedirectHandler("https://courses.ut.edu.vn")

    with pytest.raises(urllib.error.HTTPError, match="Untrusted Moodle redirect"):
        handler.redirect_request(
            urllib.request.Request("https://courses.ut.edu.vn/login/index.php"),
            None,
            302,
            "redirect",
            {},
            "https://attacker.invalid/collect",
        )


def configured_client(monkeypatch):
    client = MoodleClient()
    monkeypatch.setattr(client, "_get_ws_token", Mock(return_value="test-token"))
    return client


def test_get_user_id_refresh_bypasses_stale_cached_ws_identity(monkeypatch):
    client = MoodleClient()
    client._cached_user_id = 41
    site_info = Mock(return_value={"userid": 42})
    monkeypatch.setattr(client, "call_ws_api", site_info)

    assert client.get_user_id() == 41
    assert client.get_user_id(refresh=True) == 42
    assert client.get_user_id() == 42
    site_info.assert_called_once_with("core_webservice_get_site_info")


def test_remove_assignment_submission_replays_confirmed_moodle_web_action(monkeypatch):
    client = MoodleClient()
    opener = _MoodleWebOpener()
    monkeypatch.setattr("core.client.urllib.request.build_opener", lambda *_: opener)
    monkeypatch.setattr("core.client.settings.UTH_USERNAME", "student")
    monkeypatch.setattr("core.client.settings.UTH_PASSWORD", "secret")
    monkeypatch.setattr(
        "core.client.settings.UTH_CREDENTIALS_ORIGIN",
        "https://courses.ut.edu.vn",
    )

    assert hasattr(client, "remove_assignment_submission")
    assert client.remove_assignment_submission(123, expected_user_id=42) is True

    requests = [item[0] for item in opener.requests]
    assert [request.get_method() for request in requests] == ["GET", "POST", "GET", "POST"]
    assert [urlsplit(request.full_url).path for request in requests] == [
        "/login/index.php",
        "/login/index.php",
        "/mod/assign/view.php",
        "/mod/assign/view.php",
    ]
    login = parse_qs(requests[1].data.decode(), keep_blank_values=True)
    assert login == {
        "logintoken": ["login-csrf"],
        "username": ["student"],
        "password": ["secret"],
        "anchor": [""],
    }
    remove = parse_qs(requests[3].data.decode())
    assert remove == {
        "id": ["123"],
        "action": ["removesubmission"],
        "userid": ["42"],
        "sesskey": ["sessionkey"],
    }
    assert all(timeout == 20 for _, timeout in opener.requests)


def test_remove_assignment_submission_rejects_web_account_mismatch_before_post(monkeypatch):
    client = MoodleClient()
    opener = _MoodleWebOpener()
    monkeypatch.setattr("core.client.urllib.request.build_opener", lambda *_: opener)
    monkeypatch.setattr("core.client.settings.UTH_USERNAME", "student")
    monkeypatch.setattr("core.client.settings.UTH_PASSWORD", "secret")
    monkeypatch.setattr(
        "core.client.settings.UTH_CREDENTIALS_ORIGIN",
        "https://courses.ut.edu.vn",
    )

    assert client.remove_assignment_submission(123, expected_user_id=41) is False
    assert [request.get_method() for request, _ in opener.requests] == [
        "GET",
        "POST",
        "GET",
    ]


def test_remove_assignment_submission_revalidates_immediately_before_post(monkeypatch):
    client = MoodleClient()
    opener = _MoodleWebOpener()
    monkeypatch.setattr("core.client.urllib.request.build_opener", lambda *_: opener)
    monkeypatch.setattr("core.client.settings.UTH_USERNAME", "student")
    monkeypatch.setattr("core.client.settings.UTH_PASSWORD", "secret")
    monkeypatch.setattr(
        "core.client.settings.UTH_CREDENTIALS_ORIGIN",
        "https://courses.ut.edu.vn",
    )
    checks = []

    assert client.remove_assignment_submission(
        123,
        expected_user_id=42,
        before_commit=lambda: checks.append("checked") or False,
    ) is False
    assert checks == ["checked"]
    assert [request.get_method() for request, _ in opener.requests] == [
        "GET",
        "POST",
        "GET",
    ]


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


def test_upload_result_decodes_live_list_wrapped_duplicate_error(monkeypatch):
    client = configured_client(monkeypatch)
    client._post_multipart = Mock(
        return_value=(200, LIVE_DUPLICATE_RESPONSE_ENVELOPE)
    )

    result = client.upload_draft_file_result("answer.pdf", b"synthetic", 900)

    assert result.record is None
    assert result.error_code == "filenameexist"


def test_upload_result_decodes_direct_structured_errortype(monkeypatch):
    client = configured_client(monkeypatch)
    client._post_multipart = Mock(
        return_value=(
            200,
            {"error": "synthetic redacted detail", "errortype": "filenameexist"},
        )
    )

    result = client.upload_draft_file_result("answer.pdf", b"synthetic", 900)

    assert result.record is None
    assert result.error_code == "filenameexist"


def test_upload_result_does_not_infer_duplicate_from_arbitrary_message(monkeypatch):
    client = configured_client(monkeypatch)
    client._post_multipart = Mock(
        return_value=(200, [{"error": "synthetic filename already exists"}])
    )

    result = client.upload_draft_file_result("answer.pdf", b"synthetic", 900)

    assert result.record is None
    assert result.error_code == "invalidresponse"


def test_upload_result_preserves_unknown_explicit_errortype_without_inferring_duplicate(
    monkeypatch,
):
    client = configured_client(monkeypatch)
    client._post_multipart = Mock(
        return_value=(
            200,
            [{"error": "synthetic duplicate words", "errortype": "DifferentCode"}],
        )
    )

    result = client.upload_draft_file_result("answer.pdf", b"synthetic", 900)

    assert result.record is None
    assert result.error_code == "DifferentCode"


def test_upload_result_rejects_non_code_like_errortype(monkeypatch):
    client = configured_client(monkeypatch)
    client._post_multipart = Mock(
        return_value=(200, [{"errortype": "x" * 65}])
    )

    result = client.upload_draft_file_result("answer.pdf", b"synthetic", 900)

    assert result.record is None
    assert result.error_code == "moodleerror"


def test_upload_result_keeps_transport_http_and_malformed_failures_distinct(monkeypatch):
    client = configured_client(monkeypatch)

    client._post_multipart = Mock(side_effect=OSError("synthetic transport failure"))
    transport = client.upload_draft_file_result("answer.pdf", b"synthetic", 900)

    client._post_multipart = Mock(return_value=(503, []))
    http = client.upload_draft_file_result("answer.pdf", b"synthetic", 900)

    client._post_multipart = Mock(return_value=(200, [{"error": "synthetic"}]))
    malformed = client.upload_draft_file_result("answer.pdf", b"synthetic", 900)

    assert transport.error_code == "transporterror"
    assert http.error_code == "httpstatus"
    assert malformed.error_code == "invalidresponse"


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
