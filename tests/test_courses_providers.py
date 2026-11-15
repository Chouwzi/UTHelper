import urllib.error

import pytest

from core.courses.models import CourseAttachment, CourseModuleItem
from core.courses.providers import (
    CourseCatalogProvider,
    CoursesWsProvider,
    ThnnCourseProvider,
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
            "core_completion_update_activity_completion_status_manually": {"status": True},
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


def test_moodle_service_pages_and_completion_error_and_false_cases():
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
    assert service_bad.get_pages_by_courses(100) == []
    assert service_bad.set_activity_completion_manually(200, True) is False

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
                    "name": "Tổng quan",
                    "section": 0,
                    "summary": "<p>Giảng viên: Nguyễn Văn A (a@ut.edu.vn)</p>",
                    "visible": 1,
                    "modules": [
                        {
                            "id": 501,
                            "instance": 10,
                            "modname": "page",
                            "name": "Thông tin trực tuyến",
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
                        "content": '<script>evil()</script><p><a href="https://meet.google.com/zry-mvjo-dpe">Meet</a></p>',
                    }
                ]
            },
        }
    )
    service = MoodleService(transport)
    provider = CoursesWsProvider(service=service, origin="https://courses.ut.edu.vn")
    sections = provider.fetch_course_sections(31564)
    assert len(sections) == 1
    page_mod, res_mod = sections[0].modules
    assert page_mod.open_strategy == "inline_page"
    assert "<script>" not in page_mod.inline_html
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
                    "fullname": "[HKI2627] - [012012113707] - Lập trình mạng - CNS_CS1",
                    "shortname": "[TT]_HKI2026-2027_Lập trình mạng_012012113707",
                    "summary": '<p onclick="evil()">Giảng viên: Trần Văn B<br/>Email: b@ut.edu.vn<br/>SĐT: 0912345678</p><a href="https://meet.google.com/aaa-bbbb-ccc">Meet</a>',
                    "progress": 60,
                },
                {
                    "id": 31565,
                    "fullname": "Môn 2",
                    "shortname": "M2",
                    "summary": "",
                    "progress": True,
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
                                "itemname": "Lab 1",
                                "itemmodule": "assign",
                                "gradeformatted": "9.00",
                                "rangeformatted": "0-10",
                                "percentageformatted": "90%",
                                "weightformatted": "20%",
                                "feedback": '<p onclick="bad()">Tốt</p>',
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
    assert len(courses) == 2
    assert courses[0].id == 31564
    assert courses[0].display_name == "Lập trình mạng"
    assert courses[0].teacher_name == "Trần Văn B"
    assert courses[0].teacher_email == "b@ut.edu.vn"
    assert courses[0].teacher_phone == "0912345678"
    assert courses[0].meet_url == "https://meet.google.com/aaa-bbbb-ccc"
    assert courses[0].progress == 0.6
    assert "onclick" not in courses[0].summary_text
    assert courses[0].web_url == "https://courses.ut.edu.vn/course/view.php?id=31564"
    assert courses[1].progress is None

    assert provider.fetch_updates_since(31564, 1700000000) == {501, 502}

    grades = provider.fetch_course_grades(31564)
    assert len(grades) == 1
    assert grades[0].item_name == "Lab 1"
    assert grades[0].grade_formatted == "9.00"
    assert "onclick" not in grades[0].feedback_html

    assert provider.set_manual_completion(501, True) is True
    assert provider.download_file("https://courses.ut.edu.vn/file.pdf") == b"file-bytes"

    provider_no_user = CoursesWsProvider(
        service=MoodleService(_FakeWsTransport({})),
        userid_resolver=lambda: None,
    )
    assert provider_no_user.fetch_courses() == []
    assert provider_no_user.fetch_course_sections(1) == []
    assert provider_no_user.fetch_course_grades(1) == []
    assert provider_no_user.fetch_updates_since(1, 0) == set()
    assert provider_no_user.download_file("https://courses.ut.edu.vn/file.pdf") is None


def test_courses_ws_provider_url_module_and_description_fallback():
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
                            "url": "https://courses.ut.edu.vn/mod/url/view.php?id=601",
                            "description": '<p onclick="x()">Xem link</p>',
                            "contents": [
                                {
                                    "filename": "external",
                                    "fileurl": "https://drive.google.com/file/d/abc/view",
                                }
                            ],
                        }
                    ],
                }
            ],
            "mod_page_get_pages_by_courses": None,
        }
    )
    provider = CoursesWsProvider(service=MoodleService(transport))
    sections = provider.fetch_course_sections(99)
    assert sections[0].name == "Chủ đề 1"
    mod = sections[0].modules[0]
    assert mod.open_strategy == "external_url"
    assert mod.url == "https://drive.google.com/file/d/abc/view"
    assert mod.web_url == "https://courses.ut.edu.vn/mod/url/view.php?id=601"
    assert "onclick" not in mod.description_html
    assert mod.inline_html == mod.description_html


def test_thnn_provider_parses_courses_and_courseformat_state():
    class _FakeThnnClient:
        base_url = "https://thnn.ut.edu.vn"

        def login_session(self):
            return True

        def call_ajax_api(self, fn: str, **params):
            if fn == "core_course_get_enrolled_courses_by_timeline_classification":
                return {
                    "error": False,
                    "data": {
                        "courses": [
                            {
                                "id": 4458,
                                "fullname": "[HKI 26-27] Anh văn 4",
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
                                "title": "Unit 1",
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
                            }
                        ],
                    },
                }
            return None

        def _open_html(self, path: str):
            return path, '<a href="https://meet.google.com/xyz-abcd-efg">Join</a>'

    provider = ThnnCourseProvider(client=_FakeThnnClient())
    courses = provider.fetch_courses()
    assert len(courses) == 1
    assert courses[0].source == "thnn"
    assert courses[0].id == 4458

    sections = provider.fetch_course_sections(4458)
    assert len(sections) == 1
    assert sections[0].modules[0].open_strategy == "external_url"
    resolved_url = provider.resolve_module_external_url(sections[0].modules[0])
    assert resolved_url == "https://meet.google.com/xyz-abcd-efg"


def test_thnn_provider_supports_moodle_site_origin_and_edge_cases():
    class _OriginClient:
        moodle_site_origin = "https://thnn.ut.edu.vn/"

        def __init__(self):
            self.ajax_mode = "ok"
            self.html_response = ("", "")
            self.open_calls = []

        def call_ajax_api(self, fn: str, **params):
            if self.ajax_mode == "error":
                return {"error": True, "message": "not logged in"}
            if self.ajax_mode == "none":
                return None
            if fn == "core_course_get_enrolled_courses_by_timeline_classification":
                return {
                    "error": False,
                    "data": {
                        "courses": [
                            {"id": 0, "fullname": "Bad"},
                            {"id": 500, "fullname": "Anh văn 3", "shortname": "AV3"},
                        ]
                    },
                }
            return {
                "error": False,
                "data": {
                    "section": [
                        {
                            "id": 21,
                            "section": 0,
                            "title": "",
                            "cmlist": [101, 999],
                        }
                    ],
                    "cm": [
                        {
                            "id": 101,
                            "module": "resource",
                            "name": "Tài liệu",
                            "isoverallcomplete": True,
                            "completionstate": 1,
                        }
                    ],
                },
            }

        def _open_html(self, path: str):
            self.open_calls.append(path)
            return self.html_response

        def download_file(self, url: str):
            return b"thnn-data"

    client = _OriginClient()
    provider = ThnnCourseProvider(client=client)

    courses = provider.fetch_courses()
    assert len(courses) == 1
    assert courses[0].web_url == "https://thnn.ut.edu.vn/course/view.php?id=500"
    assert courses[0].program_tag == "THNN"

    sections = provider.fetch_course_sections(500)
    assert len(sections) == 1
    assert sections[0].name == "Chủ đề 0"
    assert len(sections[0].modules) == 1
    mod = sections[0].modules[0]
    assert mod.web_url == "https://thnn.ut.edu.vn/mod/resource/view.php?id=101"
    assert mod.completion_mode == 1
    assert mod.completion_state == 1

    # Relative workaround link is joined with origin
    client.html_response = (
        "https://thnn.ut.edu.vn/mod/resource/view.php?id=101",
        '<div class="resourceworkaround"><a href="/pluginfile.php/10/mod_resource/content/1/doc.pdf">doc</a></div>',
    )
    assert (
        provider.resolve_module_external_url(mod)
        == "https://thnn.ut.edu.vn/pluginfile.php/10/mod_resource/content/1/doc.pdf"
    )

    # Cross-origin final redirect URL is returned immediately
    client.html_response = ("https://drive.google.com/file/d/xyz", "")
    assert provider.resolve_module_external_url(mod) == "https://drive.google.com/file/d/xyz"

    # Unsafe javascript: href in workaround is ignored; falls back to resolved URL
    client.html_response = (
        "/mod/resource/view.php?id=101",
        '<div class="urlworkaround"><a href="javascript:alert(1)">bad</a></div>',
    )
    assert (
        provider.resolve_module_external_url(mod)
        == "https://thnn.ut.edu.vn/mod/resource/view.php?id=101"
    )

    # Empty web_url returns "" without opening network
    before_calls = len(client.open_calls)
    empty_mod = CourseModuleItem(
        cmid=1, instance_id=0, modname="url", name="Empty", section_id=1, web_url=""
    )
    assert provider.resolve_module_external_url(empty_mod) == ""
    assert len(client.open_calls) == before_calls

    assert provider.fetch_course_grades(500) == []
    assert provider.download_file("https://thnn.ut.edu.vn/a.pdf") == b"thnn-data"

    client.ajax_mode = "error"
    assert provider.fetch_courses() == []
    assert provider.fetch_course_sections(500) == []
    client.ajax_mode = "none"
    assert provider.fetch_courses() == []
    assert provider.fetch_course_sections(500) == []


def test_classify_preview_kind_extensions():
    assert classify_preview_kind("a.DOCX", "") == "docx"
    assert classify_preview_kind("sheet.xlsx", "") == "xlsx"
    assert classify_preview_kind("archive.rar", "") == "archive"
    assert classify_preview_kind("slides.pptx", "") == "pptx"
    assert classify_preview_kind("data.csv", "") == "csv"
    assert classify_preview_kind("notes.tsv", "") == "csv"
    assert classify_preview_kind("paper.pdf", "") == "pdf"
    assert classify_preview_kind("download", "application/pdf") == "pdf"
    assert classify_preview_kind("export", "text/csv") == "csv"
    assert classify_preview_kind("photo.JPG", "") == "image"
    assert classify_preview_kind("blob", "image/png") == "image"
    assert classify_preview_kind("main.py", "") == "text"
    assert classify_preview_kind("readme", "text/plain") == "text"
    assert classify_preview_kind("bundle.tar.gz", "") == "archive"
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


def test_thnn_provider_handles_cold_session_and_untrusted_redirect():
    class _ColdThnnClient:
        moodle_site_origin = "https://thnn.ut.edu.vn"

        def __init__(self):
            self._sesskey = ""
            self.login_calls = 0
            self.mode = "redirect"

        def login_session(self):
            self.login_calls += 1
            self._sesskey = "fresh-key"
            return True

        def _open_html(self, path: str):
            if self.mode == "redirect":
                raise urllib.error.HTTPError(
                    "https://meet.google.com/cold-session-room",
                    302,
                    "Untrusted Moodle redirect",
                    hdrs=None,
                    fp=None,
                )
            raise urllib.error.HTTPError(
                f"https://thnn.ut.edu.vn{path}",
                404,
                "Not Found",
                hdrs=None,
                fp=None,
            )

    client = _ColdThnnClient()
    provider = ThnnCourseProvider(client=client)
    mod = CourseModuleItem(
        cmid=77,
        instance_id=0,
        modname="url",
        name="Room",
        section_id=1,
        web_url="https://thnn.ut.edu.vn/mod/url/view.php?id=77",
    )

    assert provider.resolve_module_external_url(mod) == "https://meet.google.com/cold-session-room"
    assert client.login_calls == 1

    client.mode = "not_found"
    with pytest.raises(urllib.error.HTTPError):
        provider.resolve_module_external_url(mod)


def test_courses_ws_provider_guards_unsafe_url_scheme_and_weird_ints():
    transport = _FakeWsTransport(
        {
            "core_course_get_contents": [
                {
                    "id": 3,
                    "section": "bad",
                    "name": "Weird",
                    "modules": [
                        {
                            "id": "701",
                            "instance": True,
                            "modname": "url",
                            "name": "Bad Scheme",
                            "url": "https://courses.ut.edu.vn/mod/url/view.php?id=701",
                            "contents": [
                                {
                                    "filename": "link",
                                    "fileurl": "javascript:alert(1)",
                                    "filesize": None,
                                    "timemodified": "not-an-int",
                                }
                            ],
                        }
                    ],
                }
            ],
            "mod_page_get_pages_by_courses": [],
        }
    )
    provider = CoursesWsProvider(service=MoodleService(transport))
    sections = provider.fetch_course_sections(12)
    assert len(sections) == 1
    assert sections[0].section_num == 0
    mod = sections[0].modules[0]
    assert mod.cmid == 701
    assert mod.instance_id == 0
    assert mod.url == "https://courses.ut.edu.vn/mod/url/view.php?id=701"
    assert mod.attachments[0].file_size == 0
    assert mod.timemodified == 0
