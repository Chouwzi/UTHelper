import gzip
import io
import urllib.error

import pytest

from core.client import MoodleClient
from core.courses.models import CourseAttachment, CourseModuleItem
from core.courses.providers import (
    CourseCatalogProvider,
    CourseProviderError,
    CoursesWsProvider,
    ThnnCourseProvider,
    ThnnSessionError,
    classify_open_strategy,
    classify_preview_kind,
)
from core.moodle_service import MoodleService


class _FakeWsTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, function: str, **params):
        self.calls.append((function, params))
        value = self.responses.get(function)
        if isinstance(value, Exception):
            raise value
        return value


class _FakeHttpResponse:
    def __init__(self, url: str, body: bytes, status: int = 200, headers: dict | None = None):
        self._url = url
        self._body = body
        self.status = status
        self._headers = headers or {}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True
        return False

    def geturl(self):
        return self._url

    def read(self, limit: int = -1):
        if limit is None or limit < 0:
            return self._body
        return self._body[:limit]

    def info(self):
        return self._headers


class _ClosableFp(io.BytesIO):
    def __init__(self, data: bytes = b""):
        super().__init__(data)
        self.was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


def test_moodle_service_pages_and_completion_wrappers():
    transport = _FakeWsTransport(
        {
            "mod_page_get_pages_by_courses": {
                "pages": [
                    {
                        "id": 10,
                        "coursemodule": 501,
                        "name": "Online",
                        "content": "<script>x()</script><p>https://meet.google.com/abc-defg-hij</p>",
                    }
                ]
            },
            "core_completion_update_activity_completion_status_manually": {
                "status": True,
                "warnings": [],
            },
        }
    )
    service = MoodleService(transport)
    pages = service.get_pages_by_courses(31564)
    assert len(pages) == 1
    assert pages[0]["coursemodule"] == 501
    assert service.set_activity_completion_manually(501, True) is True
    assert transport.calls == [
        ("mod_page_get_pages_by_courses", {"courseids[0]": 31564}),
        (
            "core_completion_update_activity_completion_status_manually",
            {"cmid": 501, "completed": 1},
        ),
    ]


def test_moodle_service_pages_and_completion_error_and_warnings_cases():
    transport_none = _FakeWsTransport({})
    service_none = MoodleService(transport_none)
    assert service_none.get_pages_by_courses(100) is None
    assert service_none.set_activity_completion_manually(200, False) is False
    assert transport_none.calls[-1] == (
        "core_completion_update_activity_completion_status_manually",
        {"cmid": 200, "completed": 0},
    )

    transport_bad_pages = _FakeWsTransport(
        {
            "mod_page_get_pages_by_courses": {"pages": "not-a-list"},
            "core_completion_update_activity_completion_status_manually": {"status": False},
        }
    )
    service_bad = MoodleService(transport_bad_pages)
    assert service_bad.get_pages_by_courses(100) is None
    assert service_bad.set_activity_completion_manually(200, True) is False

    transport_ws_exception = _FakeWsTransport(
        {
            "mod_page_get_pages_by_courses": {
                "exception": "moodle_exception",
                "errorcode": "invalidcourse",
                "message": "Invalid course",
                "pages": [],
            },
            "core_completion_update_activity_completion_status_manually": {
                "status": True,
                "warnings": [{"warningcode": "cannotmanual"}],
            },
        }
    )
    service_ws_err = MoodleService(transport_ws_exception)
    assert service_ws_err.get_pages_by_courses(100) is None
    assert service_ws_err.set_activity_completion_manually(200, True) is False

    transport_status_int = _FakeWsTransport(
        {
            "core_completion_update_activity_completion_status_manually": {"status": 1},
        }
    )
    assert MoodleService(transport_status_int).set_activity_completion_manually(200, True) is False

    transport_exc = _FakeWsTransport(
        {
            "mod_page_get_pages_by_courses": RuntimeError("network down"),
            "core_completion_update_activity_completion_status_manually": RuntimeError("boom"),
        }
    )
    service_exc = MoodleService(transport_exc)
    assert service_exc.get_pages_by_courses(100) is None
    assert service_exc.set_activity_completion_manually(200, True) is False


def test_courses_ws_provider_merges_mod_page_and_sanitizes_html():
    transport = _FakeWsTransport(
        {
            "core_course_get_contents": [
                {
                    "id": 1,
                    "name": "Tổng quan &amp; Giới thiệu",
                    "section": 0,
                    "summary": "<style>body{color:red}</style><p>Giảng viên: Nguyễn Văn A (a@ut.edu.vn)</p>",
                    "visible": 1,
                    "modules": [
                        {
                            "id": 501,
                            "instance": 10,
                            "modname": "page",
                            "name": "Thông tin &amp; Link trực tuyến",
                            "url": "https://courses.ut.edu.vn/mod/page/view.php?id=501",
                            "completion": 1,
                            "completiondata": {"state": 0},
                        },
                        {
                            "id": 502,
                            "instance": 11,
                            "modname": "resource",
                            "name": "Slides Chương 1",
                            "url": "https://courses.ut.edu.vn/mod/resource/view.php?id=502",
                            "contents": [
                                {
                                    "filename": "Chuong1.zip",
                                    "fileurl": "https://courses.ut.edu.vn/webservice/pluginfile.php/1/mod_resource/content/1/Chuong1.zip",
                                    "filesize": 102400,
                                    "mimetype": "application/zip",
                                    "timemodified": 1700000000,
                                }
                            ],
                        },
                    ],
                }
            ],
            "mod_page_get_pages_by_courses": {
                "pages": [
                    {
                        "id": 10,
                        "coursemodule": 501,
                        "content": '<style>.x{display:none}</style><script>evil()</script><p><a href="https://meet.google.com/zry-mvjo-dpe">Meet</a></p>',
                    }
                ]
            },
        }
    )
    service = MoodleService(transport)
    provider = CoursesWsProvider(service=service, origin="https://courses.ut.edu.vn")
    sections = provider.fetch_course_sections(31564)
    assert len(sections) == 1
    assert sections[0].name == "Tổng quan & Giới thiệu"
    assert "color:red" not in sections[0].summary_html
    page_mod, res_mod = sections[0].modules
    assert page_mod.name == "Thông tin & Link trực tuyến"
    assert page_mod.open_strategy == "inline_page"
    assert "<script>" not in page_mod.inline_html
    assert "evil()" not in page_mod.inline_html
    assert "display:none" not in page_mod.inline_html
    assert "meet.google.com/zry-mvjo-dpe" in page_mod.inline_html
    assert res_mod.open_strategy == "archive_tree"
    assert res_mod.attachments[0].preview_kind == "archive"
    assert res_mod.attachments[0].is_archive is True
    assert res_mod.timemodified == 1700000000


def test_courses_ws_provider_catalog_grades_updates_completion_and_download():
    class _FakeClient:
        def __init__(self):
            self.downloaded = []

        def download_file(self, url: str):
            self.downloaded.append(url)
            return b"file-bytes"

    transport = _FakeWsTransport(
        {
            "core_webservice_get_site_info": {"userid": "77"},
            "core_enrol_get_users_courses": [
                {"id": 0, "fullname": "Skip"},
                {
                    "id": 31564,
                    "fullname": "[HKI2627] - [012012113707] - Lập trình mạng &amp; Web - CNS_CS1",
                    "shortname": "[TT]_HKI2026-2027_Lập trình mạng &amp; Web_012012113707",
                    "summary": '<script>steal()</script><p onclick="evil()">Giảng viên: Trần Văn B<br/>Email: b@ut.edu.vn<br/>SĐT: 0912345678</p><a href="https://meet.google.com/aaa-bbbb-ccc">Meet</a>',
                    "progress": 60,
                },
                {
                    "id": 31565,
                    "fullname": "Môn 2",
                    "shortname": "M2",
                    "summary": "",
                    "progress": True,
                },
                {
                    "id": 31566,
                    "fullname": "Môn 3",
                    "shortname": "M3",
                    "summary": "",
                    "progress": 150,
                },
                {
                    "id": 31567,
                    "fullname": "Môn 4",
                    "shortname": "M4",
                    "summary": "",
                    "progress": float("nan"),
                },
            ],
            "core_course_get_updates_since": {
                "instances": [{"id": 501}, {"id": 502}, {"id": 0}, {}]
            },
            "gradereport_user_get_grade_items": {
                "usergrades": [
                    {
                        "gradeitems": [
                            {"id": 0, "itemname": "", "itemtype": ""},
                            {
                                "id": 12,
                                "itemname": "Lab 1 &amp; 2",
                                "itemmodule": "assign",
                                "gradeformatted": "9.00",
                                "rangeformatted": "0-10",
                                "percentageformatted": "90%",
                                "weightformatted": "20%",
                                "feedback": '<style>p{}</style><p onclick="bad()">Tốt</p>',
                            },
                        ]
                    }
                ]
            },
            "core_completion_update_activity_completion_status_manually": {"status": True},
        }
    )
    client = _FakeClient()
    service = MoodleService(transport)
    provider: CourseCatalogProvider = CoursesWsProvider(
        service=service, client=client, origin="https://courses.ut.edu.vn/"
    )

    courses = provider.fetch_courses()
    assert len(courses) == 4
    assert courses[0].id == 31564
    assert courses[0].display_name == "Lập trình mạng & Web"
    assert courses[0].teacher_name == "Trần Văn B"
    assert courses[0].teacher_email == "b@ut.edu.vn"
    assert courses[0].teacher_phone == "0912345678"
    assert courses[0].meet_url == "https://meet.google.com/aaa-bbbb-ccc"
    assert courses[0].progress == 0.6
    assert "steal()" not in courses[0].summary_text
    assert "onclick" not in courses[0].summary_text
    assert courses[0].web_url == "https://courses.ut.edu.vn/course/view.php?id=31564"
    assert courses[1].progress is None
    assert courses[2].progress == 1.0
    assert courses[3].progress is None

    assert provider.fetch_updates_since(31564, 1700000000) == {501, 502}

    grades = provider.fetch_course_grades(31564)
    assert len(grades) == 1
    assert grades[0].item_name == "Lab 1 & 2"
    assert grades[0].grade_formatted == "9.00"
    assert "onclick" not in grades[0].feedback_html
    assert "p{}" not in grades[0].feedback_html

    assert provider.set_manual_completion(501, True) is True
    assert provider.download_file("https://courses.ut.edu.vn/file.pdf") == b"file-bytes"
    assert provider.download_file("https://evil.invalid/file.pdf") is None
    assert provider.download_file("javascript:alert(1)") is None
    assert client.downloaded == ["https://courses.ut.edu.vn/file.pdf"]

    provider_no_user = CoursesWsProvider(
        service=MoodleService(_FakeWsTransport({})),
        userid_resolver=lambda: "not-an-int",
    )
    assert provider_no_user.fetch_courses() == []
    assert provider_no_user.fetch_course_sections(1) == []
    assert provider_no_user.fetch_course_grades(1) == []
    assert provider_no_user.fetch_updates_since(1, 0) == set()
    assert provider_no_user.download_file("https://courses.ut.edu.vn/file.pdf") is None


def test_courses_ws_provider_rejects_unsafe_url_schemes_and_malformed_lists():
    transport = _FakeWsTransport(
        {
            "core_course_get_contents": [
                {
                    "id": 2,
                    "section": 1,
                    "modules": [
                        {
                            "id": 601,
                            "instance": 20,
                            "modname": "url",
                            "name": "Tài liệu tham khảo",
                            "url": "javascript:alert(1)",
                            "description": '<p onclick="x()">Xem link</p>',
                            "contents": [
                                {
                                    "filename": "bad.pdf",
                                    "fileurl": "javascript:alert(1)",
                                },
                                {
                                    "filename": "external",
                                    "fileurl": "https://drive.google.com/file/d/abc/view",
                                },
                            ],
                        },
                        {
                            "id": 602,
                            "instance": 21,
                            "modname": "resource",
                            "name": "Only Unsafe",
                            "url": "https://courses.ut.edu.vn/mod/resource/view.php?id=602",
                            "contents": [
                                {
                                    "filename": "payload.pdf",
                                    "fileurl": "data:text/html,<script>1</script>",
                                }
                            ],
                        },
                    ],
                },
                {
                    "id": 3,
                    "section": "bad",
                    "name": "Weird",
                    "modules": "not-a-list",
                },
            ],
            "mod_page_get_pages_by_courses": None,
        }
    )
    provider = CoursesWsProvider(service=MoodleService(transport))
    sections = provider.fetch_course_sections(99)
    assert len(sections) == 2
    assert sections[0].name == "Chủ đề 1"
    url_mod, res_mod = sections[0].modules
    assert url_mod.open_strategy == "external_url"
    assert url_mod.url == "https://drive.google.com/file/d/abc/view"
    assert url_mod.web_url == "https://courses.ut.edu.vn/mod/url/view.php?id=601"
    assert len(url_mod.attachments) == 1
    assert "onclick" not in url_mod.description_html
    assert url_mod.inline_html == url_mod.description_html
    assert res_mod.attachments == ()
    assert res_mod.open_strategy == "web_fallback"
    assert sections[1].section_num == 0
    assert sections[1].modules == ()


def test_thnn_provider_parses_courses_and_courseformat_state():
    class _FakeThnnClient:
        base_url = "https://thnn.ut.edu.vn"

        def login_session(self, force: bool = False):
            return True

        def call_ajax_api(self, fn: str, **params):
            if fn == "core_course_get_enrolled_courses_by_timeline_classification":
                return {
                    "error": False,
                    "data": {
                        "courses": [
                            {
                                "id": 4458,
                                "fullname": "[HKI 26-27] Anh văn 4 &amp; Kỹ năng",
                                "shortname": "AV4_N01",
                                "viewurl": "https://thnn.ut.edu.vn/course/view.php?id=4458",
                            }
                        ]
                    },
                }
            if fn == "core_courseformat_get_state":
                return {
                    "error": False,
                    "data": {
                        "section": [
                            {
                                "id": 11,
                                "section": 1,
                                "title": "Unit 1 &amp; Warmup",
                                "cmlist": [9001],
                                "visible": True,
                            }
                        ],
                        "cm": [
                            {
                                "id": 9001,
                                "module": "googlemeet",
                                "name": "Phòng học Meet",
                                "url": "https://thnn.ut.edu.vn/mod/googlemeet/view.php?id=9001",
                                "completionstate": 0,
                                "isoverallcomplete": False,
                                "isautomatic": True,
                            }
                        ],
                    },
                }
            return None

        def open_session_url(self, url: str):
            return url, '<a href="https://meet.google.com/xyz-abcd-efg">Join</a>', False

    provider = ThnnCourseProvider(client=_FakeThnnClient())
    courses = provider.fetch_courses()
    assert len(courses) == 1
    assert courses[0].source == "thnn"
    assert courses[0].id == 4458
    assert courses[0].display_name == "Anh văn 4 & Kỹ năng"

    sections = provider.fetch_course_sections(4458)
    assert len(sections) == 1
    assert sections[0].name == "Unit 1 & Warmup"
    assert sections[0].modules[0].open_strategy == "external_url"
    assert sections[0].modules[0].completion_mode == 2
    resolved_url = provider.resolve_module_external_url(sections[0].modules[0])
    assert resolved_url == "https://meet.google.com/xyz-abcd-efg"


def test_thnn_provider_pagination_completion_modes_and_error_raising():
    class _PagedAjaxClient:
        moodle_site_origin = "https://thnn.ut.edu.vn"

        def __init__(self):
            self.login_force_calls = 0
            self.ajax_calls = []
            self.mode = "paged"
            self.login_succeeds = True

        def login_session(self, force: bool = False):
            if force:
                self.login_force_calls += 1
            return self.login_succeeds

        def call_ajax_api(self, fn: str, **params):
            self.ajax_calls.append((fn, params))
            if self.mode == "relogin_then_ok":
                if len(self.ajax_calls) == 1:
                    return {
                        "error": True,
                        "errorcode": "requireloginerror",
                        "message": "Invalid sesskey",
                    }
                return {
                    "error": False,
                    "data": {
                        "courses": [
                            {"id": 700, "fullname": "Recovered", "shortname": "REC"}
                        ]
                    },
                }
            if self.mode == "session_error_forever":
                return {
                    "error": True,
                    "errorcode": "requireloginerror",
                    "message": "Session expired",
                }
            if self.mode == "generic_error":
                return {"error": True, "errorcode": "dmlreadexception", "message": "DB error"}
            if self.mode == "none":
                return None

            if fn == "core_course_get_enrolled_courses_by_timeline_classification":
                offset = params["offset"]
                if offset == 0:
                    return {
                        "error": False,
                        "data": {
                            "courses": [
                                {"id": idx, "fullname": f"Course {idx}", "shortname": f"C{idx}"}
                                for idx in range(1, 51)
                            ]
                        },
                    }
                if offset == 50:
                    return {
                        "error": False,
                        "data": {
                            "courses": [
                                {"id": 50, "fullname": "Dup 50", "shortname": "C50"},
                                {"id": 51, "fullname": "Course 51", "shortname": "C51"},
                            ]
                        },
                    }
                return {"error": False, "data": {"courses": []}}

            return {
                "error": False,
                "data": {
                    "section": [
                        {
                            "id": 21,
                            "section": 0,
                            "title": "",
                            "cmlist": [101, 102, 103, 104, 999],
                        }
                    ],
                    "cm": [
                        {
                            "id": 101,
                            "module": "resource",
                            "name": "Manual by flag",
                            "hascompletion": True,
                            "isautomatic": False,
                            "completionstate": 1,
                        },
                        {
                            "id": 102,
                            "module": "quiz",
                            "name": "Auto by flag",
                            "isoverallcomplete": True,
                            "isautomatic": True,
                            "completionstate": 1,
                        },
                        {
                            "id": 103,
                            "module": "assign",
                            "name": "Tracked default auto",
                            "isoverallcomplete": False,
                            "completionstate": 0,
                        },
                        {
                            "id": 104,
                            "module": "url",
                            "name": "Untracked with bad url",
                            "url": "javascript:alert(1)",
                        },
                    ],
                },
            }

    client = _PagedAjaxClient()
    provider = ThnnCourseProvider(client=client)

    courses = provider.fetch_courses()
    assert len(courses) == 51
    assert [c.id for c in courses[:2]] == [1, 2]
    assert courses[-1].id == 51
    assert [params["offset"] for _, params in client.ajax_calls] == [0, 50]

    sections = provider.fetch_course_sections(1)
    assert sections[0].name == "Chủ đề 0"
    mods = sections[0].modules
    assert [m.completion_mode for m in mods] == [1, 2, 2, 0]
    assert mods[3].web_url == "https://thnn.ut.edu.vn/mod/url/view.php?id=104"

    client.ajax_calls.clear()
    client.mode = "relogin_then_ok"
    recovered = provider.fetch_courses()
    assert [c.id for c in recovered] == [700]
    assert client.login_force_calls == 1

    client.mode = "session_error_forever"
    with pytest.raises(ThnnSessionError):
        provider.fetch_courses()

    client.login_succeeds = False
    with pytest.raises(ThnnSessionError):
        provider.fetch_courses()

    client.login_succeeds = True
    client.mode = "generic_error"
    with pytest.raises(CourseProviderError):
        provider.fetch_course_sections(1)

    client.mode = "none"
    with pytest.raises(CourseProviderError):
        provider.fetch_courses()


def test_moodle_client_download_file_enforces_same_origin_and_closes_http_error(monkeypatch):
    client = MoodleClient("https://courses.ut.edu.vn")
    token_calls = []

    def _fake_token(*_args, **_kwargs):
        token_calls.append(1)
        return "ws-secret-token"

    monkeypatch.setattr(client, "_get_ws_token", _fake_token)

    # Off-origin, protocol downgrade, and unsafe schemes are blocked BEFORE token lookup
    assert client.download_file("https://evil.invalid/file.pdf") is None
    assert client.download_file("http://courses.ut.edu.vn/file.pdf") is None
    assert client.download_file("javascript:alert(1)") is None
    assert client.download_file("/relative/file.pdf") is None
    assert token_calls == []

    # Same-origin gzipped download succeeds cleanly
    seen_requests = []

    class _FakeOpener:
        def __init__(self, handler):
            self.handler = handler

        def open(self, request, timeout=60):
            seen_requests.append((request.full_url, timeout))
            return self.handler(request)

    gz_body = gzip.compress(b"%PDF-1.7-ok")
    monkeypatch.setattr(
        "core.client.urllib.request.build_opener",
        lambda *_handlers: _FakeOpener(
            lambda req: _FakeHttpResponse(
                req.full_url,
                gz_body,
                status=200,
                headers={"Content-Encoding": "gzip"},
            )
        ),
    )

    provider = CoursesWsProvider(
        service=MoodleService(lambda *_args, **_kwargs: None),
        client=client,
        origin="https://courses.ut.edu.vn",
    )
    assert provider.download_file("https://courses.ut.edu.vn/pluginfile.php/1/a.pdf") == b"%PDF-1.7-ok"
    assert seen_requests[0][0] == "https://courses.ut.edu.vn/pluginfile.php/1/a.pdf?token=ws-secret-token"

    # Cross-origin redirect raises HTTPError -> fp closed, returns None
    fp = _ClosableFp(b"redirect")
    monkeypatch.setattr(
        "core.client.urllib.request.build_opener",
        lambda *_handlers: _FakeOpener(
            lambda _req: (_ for _ in ()).throw(
                urllib.error.HTTPError(
                    "https://attacker.invalid/steal",
                    302,
                    "Untrusted Moodle redirect",
                    hdrs=None,
                    fp=fp,
                )
            )
        ),
    )
    assert client.download_file("https://courses.ut.edu.vn/pluginfile.php/1/a.pdf") is None
    assert fp.was_closed is True


def test_moodle_client_download_session_file_and_open_session_url_with_real_client(monkeypatch):
    client = MoodleClient("https://thnn.ut.edu.vn")
    monkeypatch.setattr(client, "_throttle", lambda: None)

    login_calls = []

    def _fake_login(username=None, password=None, force=False):
        login_calls.append(force)
        client._sesskey = "valid-sesskey"
        return True

    monkeypatch.setattr(client, "login_session", _fake_login)

    # 1. download_session_file: rejects off-origin and unsafe URLs
    assert client.download_session_file("https://evil.invalid/a.pdf") is None
    assert client.download_session_file("http://thnn.ut.edu.vn/a.pdf") is None
    assert client.download_session_file("javascript:alert(1)") is None
    assert login_calls == []

    # 2. download_session_file: cold login + expired /login/index.php recovery + gzip decode
    responses = iter(
        [
            _FakeHttpResponse("https://thnn.ut.edu.vn/login/index.php", b"<html>login</html>"),
            _FakeHttpResponse(
                "https://thnn.ut.edu.vn/pluginfile.php/9/a.pdf",
                gzip.compress(b"thnn-pdf-bytes"),
                status=200,
                headers={"Content-Encoding": "gzip"},
            ),
        ]
    )
    opened_urls = []

    class _SessionOpener:
        def open(self, request, timeout=60):
            opened_urls.append(request.full_url)
            return next(responses)

    monkeypatch.setattr(client, "_ensure_html_opener", lambda: _SessionOpener())

    provider = ThnnCourseProvider(client=client)
    assert provider.download_file("https://thnn.ut.edu.vn/pluginfile.php/9/a.pdf") == b"thnn-pdf-bytes"
    assert login_calls == [False, True]
    assert opened_urls == [
        "https://thnn.ut.edu.vn/pluginfile.php/9/a.pdf",
        "https://thnn.ut.edu.vn/pluginfile.php/9/a.pdf",
    ]
    assert provider.download_file("https://evil.invalid/a.pdf") is None

    # 3. download_session_file: enforces max_bytes and closes HTTPError
    responses = iter(
        [
            _FakeHttpResponse("https://thnn.ut.edu.vn/pluginfile.php/9/big.bin", b"0123456789"),
        ]
    )
    assert client.download_session_file("https://thnn.ut.edu.vn/pluginfile.php/9/big.bin", max_bytes=5) is None

    fp_404 = _ClosableFp(b"missing")

    class _ErrOpener:
        def open(self, request, timeout=60):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=fp_404)

    monkeypatch.setattr(client, "_ensure_html_opener", lambda: _ErrOpener())
    assert client.download_session_file("https://thnn.ut.edu.vn/pluginfile.php/9/missing.pdf") is None
    assert fp_404.was_closed is True

    # 4. open_session_url: rejects off-origin, recovers /login/index.php, closes & handles redirect
    with pytest.raises(ValueError):
        client.open_session_url("https://evil.invalid/mod/url/view.php?id=1")

    html_turns = iter(
        [
            ("https://thnn.ut.edu.vn/login/index.php", "<html>login</html>"),
            (
                "https://thnn.ut.edu.vn/mod/resource/view.php?id=101",
                '<div class="resourceworkaround"><a href="/pluginfile.php/10/mod_resource/content/1/doc.pdf">doc</a></div>',
            ),
        ]
    )
    monkeypatch.setattr(client, "_open_html", lambda _path: next(html_turns))
    mod = CourseModuleItem(
        cmid=101,
        instance_id=0,
        modname="resource",
        name="Doc",
        section_id=1,
        web_url="https://thnn.ut.edu.vn/mod/resource/view.php?id=101",
    )
    assert (
        provider.resolve_module_external_url(mod)
        == "https://thnn.ut.edu.vn/pluginfile.php/10/mod_resource/content/1/doc.pdf"
    )

    redirect_fp = _ClosableFp(b"redirect")

    def _raise_redirect(_path):
        raise urllib.error.HTTPError(
            "https://meet.google.com/direct-room",
            302,
            "Untrusted Moodle redirect",
            hdrs=None,
            fp=redirect_fp,
        )

    monkeypatch.setattr(client, "_open_html", _raise_redirect)
    assert provider.resolve_module_external_url(mod) == "https://meet.google.com/direct-room"
    assert redirect_fp.was_closed is True

    # Off-origin module.web_url returns directly without querying THNN; /login/index.php falls back safely
    external_mod = CourseModuleItem(
        cmid=102,
        instance_id=0,
        modname="url",
        name="External",
        section_id=1,
        web_url="https://drive.google.com/file/d/direct",
    )
    assert provider.resolve_module_external_url(external_mod) == "https://drive.google.com/file/d/direct"

    monkeypatch.setattr(
        client,
        "_open_html",
        lambda _path: ("https://thnn.ut.edu.vn/login/index.php", "<html>login</html>"),
    )
    monkeypatch.setattr(client, "login_session", lambda force=False: False)
    assert provider.resolve_module_external_url(mod) == mod.web_url


def test_classify_preview_kind_extensions():
    assert classify_preview_kind("a.DOCX", "") == "docx"
    assert classify_preview_kind("sheet.xlsx", "") == "xlsx"
    assert classify_preview_kind("archive.rar", "") == "archive"
    assert classify_preview_kind("slides.pptx", "") == "pptx"
    assert classify_preview_kind("data.csv", "") == "csv"
    assert classify_preview_kind("notes.tsv", "") == "csv"
    assert classify_preview_kind("paper.pdf", "") == "pdf"
    assert classify_preview_kind("download", "application/pdf; charset=binary") == "pdf"
    assert classify_preview_kind("export", "text/csv; charset=utf-8") == "csv"
    assert classify_preview_kind("photo.JPG", "") == "image"
    assert classify_preview_kind("blob", "image/png") == "image"
    assert classify_preview_kind("main.py", "") == "text"
    assert classify_preview_kind("readme", "text/plain") == "text"
    assert classify_preview_kind("bundle.tar.gz", "") == "archive"
    assert classify_preview_kind("bundle", "application/zip; charset=binary") == "archive"
    assert classify_preview_kind("unknown", "application/x-notpdf") == "unsupported"
    assert classify_preview_kind("unknown", "application/octet-target") == "unsupported"
    assert classify_preview_kind("unknown.bin", "application/octet-stream") == "unsupported"


def test_classify_open_strategy_matrix():
    pdf_att = CourseAttachment(
        file_name="a.pdf", file_url="https://x/a.pdf", preview_kind="pdf", is_archive=False
    )
    zip_att = CourseAttachment(
        file_name="a.zip", file_url="https://x/a.zip", preview_kind="archive", is_archive=True
    )
    assert classify_open_strategy("assign", ()) == "assignment_detail"
    assert classify_open_strategy("quiz", ()) == "webview_activity"
    assert classify_open_strategy("attendance", ()) == "webview_activity"
    assert classify_open_strategy("scorm", ()) == "webview_activity"
    assert classify_open_strategy("url", ()) == "external_url"
    assert classify_open_strategy("googlemeet", ()) == "external_url"
    assert classify_open_strategy("page", ()) == "inline_page"
    assert classify_open_strategy("label", ()) == "inline_page"
    assert classify_open_strategy("folder", ()) == "folder_files"
    assert classify_open_strategy("resource", (pdf_att,)) == "preview_file"
    assert classify_open_strategy("resource", (zip_att,)) == "archive_tree"
    assert classify_open_strategy("resource", ()) == "web_fallback"
    assert classify_open_strategy("forum", ()) == "web_fallback"
