import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core.moodle_service import MoodleService


def test_action_event_pagination_passes_after_event_id():
    calls = []
    service = MoodleService(lambda function, **params: calls.append((function, params)) or {})

    service.get_action_events_by_timesort(100, 200, limit=50, after_event_id=42)

    assert calls == [
        (
            "core_calendar_get_action_events_by_timesort",
            {
                "timesortfrom": 100,
                "timesortto": 200,
                "limitnum": 50,
                "aftereventid": 42,
            },
        )
    ]


def test_get_current_user_id_from_site_info(monkeypatch):
    service = MoodleService(lambda *args, **kwargs: None)

    monkeypatch.setattr(service, "get_site_info", lambda: {"userid": "42"})

    assert service.get_current_user_id() == 42


def test_get_current_user_id_returns_none_for_missing_user(monkeypatch):
    service = MoodleService(lambda *args, **kwargs: None)

    monkeypatch.setattr(service, "get_site_info", lambda: {})

    assert service.get_current_user_id() is None


def test_fetch_all_grades_loads_course_items(monkeypatch):
    service = MoodleService(lambda *args, **kwargs: None)
    courses = [{"courseid": 10, "coursename": "Math"}, {"courseid": 20, "coursename": "Physics"}]

    monkeypatch.setattr(service, "get_course_grades", lambda userid: courses)
    monkeypatch.setattr(
        service,
        "get_grade_items",
        lambda courseid, userid: [{"courseid": courseid, "grade": "10"}],
    )

    courses_grades, grade_items = service.fetch_all_grades(userid=7, max_workers=2)

    assert courses_grades == courses
    assert set(grade_items) == {"10", "20"}
    assert grade_items["10"] == [{"courseid": "10", "grade": "10"}]



def test_upload_file_to_draft_encodes_bytes_and_resolves_user_id(tmp_path, monkeypatch):
    import base64

    sample = tmp_path / "report.pdf"
    sample.write_bytes(b"%PDF-1.4 test")
    ws_calls = []

    def fake_ws(function, **params):
        ws_calls.append((function, params))
        if function == "core_webservice_get_site_info":
            return {"userid": 99}
        if function == "core_files_upload":
            return {"itemid": 555}
        return {}

    from core import ws_functions

    ws_functions.clear_all_caches()
    service = MoodleService(fake_ws)
    draft_itemid = service.upload_file_to_draft(str(sample), draft_id=0)

    assert draft_itemid == 555
    upload_calls = [c for c in ws_calls if c[0] == "core_files_upload"]
    assert len(upload_calls) == 1
    _, params = upload_calls[0]
    assert params["filename"] == "report.pdf"
    assert params["instanceid"] == 99
    assert params["filecontent"] == base64.b64encode(b"%PDF-1.4 test").decode("ascii")


def test_bound_client_draft_download_user_and_remove_delegation():
    class DummyClient:
        moodle_site_origin = "https://courses.ut.edu.vn"

        def call_ws_api(self, *_args, **_kwargs):
            return {}

        def upload_draft_file_record(self, filename, content, *, itemid=0, filepath="/"):
            return (filename, content, itemid, filepath)

        def download_file(self, url):
            return f"bytes:{url}".encode("utf-8")

        def get_user_id(self, *, refresh=False):
            return 777 if refresh else 111

        def remove_assignment_submission(self, cmid, *, expected_user_id, before_commit=None):
            assert before_commit is None or before_commit()
            return cmid == 12 and expected_user_id == 777

    client = DummyClient()
    service = MoodleService(client.call_ws_api)

    assert service.moodle_site_origin == "https://courses.ut.edu.vn"
    assert service.upload_draft_file_record("a.pdf", b"data", itemid=9, filepath="/sub/") == (
        "a.pdf",
        b"data",
        9,
        "/sub/",
    )
    assert service.download_file("https://courses.ut.edu.vn/f.pdf") == b"bytes:https://courses.ut.edu.vn/f.pdf"
    assert service.get_user_id(refresh=True) == 777
    assert service.remove_assignment_submission(12, expected_user_id=777, before_commit=lambda: True) is True
