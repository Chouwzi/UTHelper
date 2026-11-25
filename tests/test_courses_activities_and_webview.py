"""Tests for THNN live fixes, CourseActivityService, session bridge, Pane-3 UX & WebView routing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.error
import urllib.request

import flet as ft
import pytest

from core.courses.activity_service import (
    CourseActivityService,
    QuizActivitySummary,
    clean_moodle_question_text,
    parse_attendance_html,
    parse_book_html,
    parse_choicegroup_html,
    parse_groupselect_html,
    parse_quiz_attempt_review_payload,
)
from core.courses.archive_service import ArchiveService
from core.courses.cache_store import CourseCacheStore
from core.courses.catalog_service import CourseCatalogService
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.models import CourseAttachment, CourseModuleItem, CourseSummary
from core.courses.preview_cache import PreviewFileCache
from core.courses.providers import ThnnCourseProvider
from gui.components.courses._shared import PaneErrorOverlay, PaneLoadingOverlay
from gui.components.courses.activity_views import (
    AttendanceActivityView,
    ForumActivityView,
    GroupActivityView,
    PaneAssignmentView,
    QuizActivityView,
    RichContentActivityView,
)
from gui.components.courses.archive_explorer_view import ArchiveExplorerView
from gui.components.courses.course_detail_pane import CourseDetailPane
from gui.components.courses.courses_tab_view import CoursesTabView
from gui.components.courses.document_reader_view import DocumentReaderView
from gui.components.courses.webview_pane import (
    CoursesWebViewPane,
    _webview2_runtime_available,
    is_external_meeting_url,
)
from gui.components.detail_view import DetailView
from gui.controllers.courses_coordinator import CoursesCoordinator
from gui.view_models.courses_state import CoursesState


def _sample_course(source: str = "courses", cid: int = 101) -> CourseSummary:
    return CourseSummary(
        id=cid,
        source=source,  # type: ignore[arg-type]
        fullname="Lập trình Python [2025-2026 HK2]",
        shortname="PY101",
        display_name="Lập trình Python",
        semester_label="2025-2026 HK2",
        semester_sort_key=(2025, 2),
        web_url=f"https://{source}.ut.edu.vn/course/view.php?id={cid}",
    )


def _sample_module(
    cmid: int,
    modname: str,
    name: str,
    *,
    source: str = "courses",
    instance_id: int = 50,
    open_strategy: str = "webview_activity",
) -> CourseModuleItem:
    return CourseModuleItem(
        cmid=cmid,
        instance_id=instance_id,
        modname=modname,
        name=name,
        section_id=1,
        url=f"https://{source}.ut.edu.vn/mod/{modname}/view.php?id={cmid}",
        web_url=f"https://{source}.ut.edu.vn/mod/{modname}/view.php?id={cmid}",
        open_strategy=open_strategy,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 1. THNN Live Fixes (JSON-string course state, HTML grades, resource attachments)
# ---------------------------------------------------------------------------


def test_thnn_provider_parses_json_string_state_grades_and_resource_attachments():
    state_json = json.dumps(
        {
            "section": [
                {"id": 10, "section": 1, "title": "Tuần 1", "cmlist": ["201"], "visible": True}
            ],
            "cm": [
                {
                    "id": 201,
                    "module": "resource",
                    "name": "Slide Chương 1",
                    "url": "https://thnn.ut.edu.vn/mod/resource/view.php?id=201",
                    "completionstate": 1,
                }
            ],
        }
    )
    grades_html = """
    <table class="user-grade">
      <tbody>
        <tr class="level1 baggt">
          <th class="column-itemname">Tổng kết môn</th>
          <td class="column-weight">-</td>
          <td class="column-grade">8.75</td>
          <td class="column-range">0–10</td>
          <td class="column-percentage">87.50 %</td>
          <td class="column-feedback">Đạt yêu cầu</td>
        </tr>
        <tr>
          <td class="column-itemname">Bài kiểm tra giữa kỳ</td>
          <td class="column-weight">30 %</td>
          <td class="column-grade">9.00</td>
          <td class="column-range">0–10</td>
          <td class="column-percentage">90.00 %</td>
          <td class="column-feedback">Tốt</td>
        </tr>
      </tbody>
    </table>
    """
    resource_html = """
    <div class="resourceworkaround">
      Nhấn vào <a href="https://thnn.ut.edu.vn/pluginfile.php/88/mod_resource/content/1/BaiGiang1.pdf">BaiGiang1.pdf</a> để tải.
    </div>
    """

    def _open_session(url: str):
        if "grade/report/user" in url:
            return url, grades_html, False
        if "mod/resource/view.php" in url:
            return url, resource_html, False
        return url, "", False

    fake_client = SimpleNamespace(
        moodle_site_origin="https://thnn.ut.edu.vn",
        call_ajax_api=lambda fn, **_k: {"data": state_json}
        if fn == "core_courseformat_get_state"
        else {},
        open_session_url=_open_session,
        login_session=lambda **_k: True,
        download_session_file=lambda _u: b"%PDF",
    )

    provider = ThnnCourseProvider(client=fake_client)
    sections = provider.fetch_course_sections(9)
    assert len(sections) == 1
    assert sections[0].modules[0].name == "Slide Chương 1"

    grades = provider.fetch_course_grades(9)
    assert len(grades) == 2
    assert grades[0].item_module == "course"
    assert grades[0].grade_formatted == "8.75"
    assert grades[1].weight == "30 %"

    atts = provider.resolve_module_attachments(sections[0].modules[0])
    assert len(atts) == 1
    assert atts[0].file_name == "BaiGiang1.pdf"
    assert atts[0].preview_kind == "pdf"


# ---------------------------------------------------------------------------
# 2. CourseActivityService HTML/WS Parsers & Actions
# ---------------------------------------------------------------------------


def test_activity_parsers_attendance_groups_choice_quiz_forum_and_book():
    att_html = """
    <h2>Điểm danh Thực hành</h2>
    <table class="attlist">
      <tr><th>Taken sessions</th><td>8</td></tr>
      <tr><th>Points over taken sessions</th><td>7 / 8</td></tr>
      <tr><th>Percentage</th><td>87.5%</td></tr>
    </table>
    <table class="generaltable attwidth">
      <tbody>
        <tr>
          <td>17/11/2026 07:30</td>
          <td>Buổi 8</td>
          <td>Present</td>
          <td>1 / 1</td>
          <td>Đúng giờ</td>
        </tr>
      </tbody>
    </table>
    <form action="/mod/attendance/attendance.php" method="post">
      <input type="hidden" name="sessid" value="44" />
      <input type="hidden" name="sesskey" value="abc123" />
      <label for="st1"><input id="st1" type="radio" name="status" value="9" checked /> Present</label>
      <label for="st2"><input id="st2" type="radio" name="status" value="10" /> Late</label>
      <input type="text" name="studentpassword" value="" />
      <input type="submit" value="Lưu" />
    </form>
    """
    att = parse_attendance_html(
        att_html,
        base_url="https://courses.ut.edu.vn",
        page_url="https://courses.ut.edu.vn/mod/attendance/view.php?id=71",
    )
    assert att.cmid == 71
    assert att.taken_sessions == "8"
    assert att.percentage == "87.5%"
    assert att.requires_password is True
    assert len(att.records) == 1
    assert att.status_options == (("9", "Present"), ("10", "Late"))
    assert att.submit_action_url == "https://courses.ut.edu.vn/mod/attendance/attendance.php"

    gs_html = """
    <table class="groupselect-table">
      <tbody>
        <tr>
          <td>Nhóm 01</td>
          <td>Đề tài AI</td>
          <td>3/4</td>
          <td>An, Bình, Chi</td>
          <td>
            <form action="/mod/groupselect/view.php" method="post">
              <input type="hidden" name="id" value="80" />
              <input type="hidden" name="sesskey" value="abc" />
              <button type="submit" name="select" value="10">Tham gia</button>
            </form>
          </td>
        </tr>
        <tr>
          <td>Nhóm 02</td>
          <td>Đề tài Web</td>
          <td>4/4</td>
          <td>Dũng, Hà</td>
          <td>
            <form action="/mod/groupselect/view.php" method="post">
              <input type="hidden" name="id" value="80" />
              <input type="hidden" name="sesskey" value="abc" />
              <button type="submit" name="unselect" value="11">Rời nhóm</button>
            </form>
          </td>
        </tr>
      </tbody>
    </table>
    """
    gs = parse_groupselect_html(
        gs_html,
        base_url="https://courses.ut.edu.vn",
        page_url="https://courses.ut.edu.vn/mod/groupselect/view.php?id=80",
    )
    assert len(gs.groups) == 2
    assert gs.groups[0].can_join is True
    assert gs.groups[1].is_member is True and gs.groups[1].can_leave is True

    cg_html = """
    <form action="/mod/choicegroup/view.php" method="post">
      <input type="hidden" name="id" value="85" />
      <input type="hidden" name="sesskey" value="xyz" />
      <table class="choicegroups">
        <tbody>
          <tr>
            <td><input type="radio" name="answer" value="5" checked /></td>
            <td>Nhóm React</td>
            <td>2/3</td>
            <td>Minh, Nam</td>
          </tr>
        </tbody>
      </table>
    </form>
    """
    cg = parse_choicegroup_html(
        cg_html,
        base_url="https://courses.ut.edu.vn",
        page_url="https://courses.ut.edu.vn/mod/choicegroup/view.php?id=85",
    )
    assert cg.is_radio_choice is True
    assert len(cg.groups) == 1
    assert cg.groups[0].is_member is True
    assert cg.groups[0].name == "Nhóm React"

    book_html = """
    <div class="book_toc">
      <ul>
        <li><a href="view.php?id=99&chapterid=10">Chương 1: Mở đầu</a></li>
        <li class="subchapter"><a href="view.php?id=99&chapterid=11">1.1 Cài đặt</a></li>
      </ul>
    </div>
    <div class="book_content"><h3>Chương 1: Mở đầu</h3><p>Xin chào UTH.</p></div>
    """
    chapters = parse_book_html(book_html, title="Giáo trình")
    assert len(chapters) == 2
    assert "Xin chào UTH" in chapters[0].content_markdown
    assert chapters[1].subchapter is True

    # Service integration for Quiz, Forum & Choice WS
    fake_service = SimpleNamespace(
        get_course_user_groups=lambda _cid: [{"id": 5, "name": "Nhóm React"}],
        get_choice_options=lambda _id: [
            {"id": 1, "text": "Ca sáng", "checked": False, "disabled": False, "numberofuser": 2, "maxanswers": 10}
        ],
        submit_choice_response=lambda _cid, _r: True,
        get_quizzes_by_courses=lambda _cids: [
            {"id": 300, "coursemodule": 55, "name": "Quiz 1", "timeopen": 1000, "timeclose": 2000, "timelimit": 900, "attempts": 2, "grademethod": 1, "sumgrades": 10.0}
        ],
        get_quiz_access_information=lambda _qid: {"accessrules": ["15 phút"], "preventaccessreasons": []},
        get_quiz_attempts=lambda _qid: [{"id": 77, "attempt": 1, "state": "finished", "sumgrades": 9.5}],
        get_quiz_attempt_review=lambda _aid: {"questions": [{"slot": 1, "status": "Correct", "mark": "1.0", "maxmark": "1.0", "html": "<div class='qtext'>2+2=?</div>"}]},
        get_forums_by_courses=lambda _cids: [{"id": 400, "cmid": 60, "intro": "<p>Thảo luận</p>"}],
        get_forum_discussions=lambda _fid: [{"discussion": 901, "name": "Hỏi đáp tuần 1", "userfullname": "GV A", "numreplies": 2, "pinned": True, "message": "<p>Nội dung</p>"}],
        get_discussion_posts=lambda _did: [{"id": 1, "author": {"fullname": "GV A"}, "subject": "Re", "message": "<p>Trả lời chi tiết</p>"}],
    )
    svc = CourseActivityService(moodle_service=fake_service)
    quiz = svc.fetch_quiz_activity("courses", _sample_module(55, "quiz", "Quiz 1"), course_id=101)
    assert quiz.attempts[0].score_text == "9.5 / 10.0"
    assert svc.fetch_quiz_review(77)[0]["question_text"] == "2+2=?"

    forum = svc.fetch_forum_activity("courses", _sample_module(60, "forum", "Diễn đàn"), course_id=101)
    assert forum.discussions[0].pinned is True
    assert "Trả lời chi tiết" in svc.fetch_discussion_posts("courses", 901)[0].message_text

    choice_mod = _sample_module(61, "choice", "Chọn ca", instance_id=12)
    grp_choice = svc.fetch_group_activity("courses", choice_mod, course_id=101)
    assert grp_choice.groups[0].name == "Ca sáng"
    ok, _msg, _ = svc.submit_group_action("courses", choice_mod, choice_option_id=1, course_id=101)
    assert ok is True


# ---------------------------------------------------------------------------
# 3. LocalPreviewServer Authenticated Session Bridge (/session/...)
# ---------------------------------------------------------------------------


def test_local_preview_server_authenticated_session_bridge_get_post_and_ssrf_guard():
    seen_requests: list[tuple[str, str, bytes | None]] = []

    class _FakeSessionClient:
        moodle_site_origin = "https://courses.ut.edu.vn"

        def open_session_raw(
            self,
            url: str,
            *,
            method: str = "GET",
            body: bytes | None = None,
            **_kwargs,
        ):
            seen_requests.append((method, url, body))
            if "evil.example" in url:
                raise ValueError("Untrusted origin")
            if "bounce" in url:
                return 302, "https://evil.example/steal", "text/plain", b""
            if method == "POST":
                return 200, url, "text/html; charset=utf-8", b"<html><body>Saved!</body></html>"
            html_page = (
                '<html><head><title>Quiz</title></head>'
                '<body><a href="https://courses.ut.edu.vn/mod/quiz/attempt.php?id=5">Attempt</a>'
                '<form action="/mod/quiz/processattempt.php" method="post"></form></body></html>'
            )
            return 200, url, "text/html; charset=utf-8", html_page.encode("utf-8")

    server = LocalPreviewServer()
    try:
        server.register_session_provider("courses", _FakeSessionClient())
        bridge_url = server.build_session_bridge_url(
            "courses", "https://courses.ut.edu.vn/mod/quiz/view.php?id=55"
        )
        assert bridge_url is not None
        # Cross-origin target refused cleanly
        assert (
            server.build_session_bridge_url("courses", "https://evil.example/phish")
            is None
        )

        with urllib.request.urlopen(bridge_url, timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers.get("X-Frame-Options") is None
            assert resp.headers.get("Cross-Origin-Resource-Policy") == "same-origin"
            assert resp.headers.get("Referrer-Policy") == "no-referrer"
            assert resp.headers.get("X-Content-Type-Options") == "nosniff"
            assert "Content-Security-Policy" in resp.headers
            body = resp.read().decode("utf-8")
            assert "<base " in body
            assert "/session/" in body
            assert "https://courses.ut.edu.vn/mod/quiz/attempt.php" not in body

        # POST request passes body through cleanly
        post_url = server.build_session_bridge_url(
            "courses", "https://courses.ut.edu.vn/mod/quiz/processattempt.php"
        )
        assert post_url is not None
        req = urllib.request.Request(post_url, data=b"q1=A", method="POST")
        with urllib.request.urlopen(req, timeout=5) as post_resp:
            assert b"Saved!" in post_resp.read()
        assert seen_requests[-1][0] == "POST"
        assert seen_requests[-1][2] == b"q1=A"

        # Invalid bridge token returns 403
        tampered = bridge_url.replace(server._bridge_token, "invalidtoken000")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(tampered, timeout=5)
        assert exc_info.value.code == 403
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# 4. Pane-3 Default Workspace, 2+3 Expand Toggle & Coordinator V2 Routing
# ---------------------------------------------------------------------------


def test_pane3_default_overlay_expand_toggle_and_course_switch_cleanup(tmp_path: Path):
    store = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|u")
    c1 = _sample_course(cid=1)
    c2 = _sample_course(cid=2)
    store.save_catalog([c1, c2], synced_at=100)
    svc = CourseCatalogService(
        cache_store=store, preview_cache=PreviewFileCache(tmp_path / "pc")
    )

    view = CoursesTabView(catalog_service=svc)
    view.set_width(1200)
    view.load_initial()

    assert view._list_pane_host.width == 320
    assert view._split_divider.visible is True

    disposed_flags: list[bool] = []

    class _DummyOverlay(ft.Container):
        def __init__(self):
            super().__init__(expand=True)
            self.expanded_arg = False

        def set_expanded(self, expanded: bool, *, visible: bool = True):
            self.expanded_arg = expanded

        def dispose(self):
            disposed_flags.append(True)

    ov = _DummyOverlay()
    view.push_overlay(ov, tag="doc:1")

    # By default opening in Pane 3 MUST keep Pane 2 visible (width == 320)
    assert len(view._overlays) == 1
    assert ov in view._workspace_stack.controls
    assert view._list_pane_host.width == 320
    assert view._split_divider.visible is True

    # Expand toggle switches Pane 3 -> 2+3 (Pane 2 collapses cleanly to 0)
    view.toggle_pane3_expanded()
    assert view.pane3_expanded is True
    assert view._list_pane_host.width == 0
    assert view._split_divider.visible is False
    assert ov.expanded_arg is True

    # Popping overlay restores Pane 3, Escape collapses 2+3 back to 3
    assert view.handle_back() is True
    assert disposed_flags == [True]
    assert view.handle_back() is True
    assert view.pane3_expanded is False
    assert view._list_pane_host.width == 320

    # Switching courses clears/disposes active overlays cleanly
    ov2 = _DummyOverlay()
    view.push_overlay(ov2, tag="doc:2")
    asyncio.run(view._select_course_async(c1.course_key))
    asyncio.run(view._select_course_async(c2.course_key))
    assert view._overlays == []
    assert disposed_flags == [True, True]


def test_coordinator_routes_all_v2_activities_and_in_app_webview(tmp_path: Path):
    assert is_external_meeting_url("https://meet.google.com/abc-defg-hij") is True
    assert is_external_meeting_url("https://courses.ut.edu.vn/mod/vpl/view.php?id=9") is False

    store = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|u")
    pcache = PreviewFileCache(tmp_path / "preview")
    server = LocalPreviewServer()
    launched_external: list[str] = []
    act_service = CourseActivityService(moodle_service=SimpleNamespace(
        get_quizzes_by_courses=lambda _c: [],
        get_forums_by_courses=lambda _c: [],
        get_course_user_groups=lambda _c: [],
    ))
    fake_client = SimpleNamespace(
        moodle_site_origin="https://courses.ut.edu.vn",
        get_session_cookies=lambda **_k: [],
        open_session_url=lambda u: (u, "<html></html>", False),
    )

    fake_page = SimpleNamespace(
        overlay=[],
        web=False,
        platform="windows",
        title="UTHelper",
        width=1200,
        height=800,
        update=lambda: None,
        run_task=lambda fn, *a: asyncio.run(fn(*a)),
    )
    coord = CoursesCoordinator(
        page=fake_page,  # type: ignore[arg-type]
        catalog_service=CourseCatalogService(cache_store=store, preview_cache=pcache),
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=server,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=launched_external.append,
        activity_service=act_service,
        courses_client=fake_client,
        assignment_detail_fetcher=lambda d: d,
    )
    try:
        course = _sample_course()

        coord._handle_open_assignment(course, _sample_module(11, "assign", "BT1", open_strategy="assignment_detail"))
        assert isinstance(coord.courses_view._overlays[-1], PaneAssignmentView)

        coord._handle_open_webview_activity(course, _sample_module(12, "attendance", "Diem danh"))
        assert isinstance(coord.courses_view._overlays[-1], AttendanceActivityView)

        coord._handle_open_webview_activity(course, _sample_module(13, "groupselect", "Chon nhom"))
        assert isinstance(coord.courses_view._overlays[-1], GroupActivityView)

        coord._handle_open_webview_activity(course, _sample_module(14, "quiz", "Quiz 1"))
        assert isinstance(coord.courses_view._overlays[-1], QuizActivityView)

        coord._handle_open_custom_activity(course, _sample_module(15, "forum", "Dien dan", open_strategy="web_fallback"))
        assert isinstance(coord.courses_view._overlays[-1], ForumActivityView)

        coord._handle_open_custom_activity(course, _sample_module(16, "book", "Sach", open_strategy="web_fallback"))
        assert isinstance(coord.courses_view._overlays[-1], RichContentActivityView)

        # Unsupported activity, Moodle web URL, AND external meeting/Zalo links open in Pane-3 WebView by default
        coord._handle_course_url_action("https://courses.ut.edu.vn/mod/vpl/view.php?id=99")
        assert isinstance(coord.courses_view._overlays[-1], CoursesWebViewPane)
        assert launched_external == []

        coord._handle_course_url_action("https://meet.google.com/aaa-bbbb-ccc")
        assert isinstance(coord.courses_view._overlays[-1], CoursesWebViewPane)
        assert launched_external == []

        coord._handle_course_url_action("https://zalo.me/g/nhom_lop_a")
        zalo_pane = coord.courses_view._overlays[-1]
        assert isinstance(zalo_pane, CoursesWebViewPane)
        assert launched_external == []

        # External OS browser only launches when user explicitly clicks external button
        zalo_pane._ext_btn.on_click(None)
        assert launched_external == ["https://zalo.me/g/nhom_lop_a"]

        # Nested archive opens ArchiveExplorerView directly
        att = CourseAttachment(
            file_name="inner.zip",
            file_url="https://courses.ut.edu.vn/inner.zip",
            preview_kind="archive",
            is_archive=True,
        )
        coord.open_attachment_bytes_in_overlay(course, _sample_module(20, "resource", "Zip", open_strategy="archive_tree"), att, b"PK\x05\x06" + b"\x00" * 18)
        assert isinstance(coord.courses_view._overlays[-1], ArchiveExplorerView)
        coord._open_entry_reader("nested.7z", b"7z", "archive")
        assert isinstance(coord.courses_view._overlays[-1], ArchiveExplorerView)
    finally:
        coord.close()


def _iter_controls(root: ft.Control):
    yield root
    for attr in ("content", "leading", "trailing", "title", "subtitle"):
        child = getattr(root, attr, None)
        if isinstance(child, ft.Control):
            yield from _iter_controls(child)
    for attr in ("controls", "actions", "tabs", "views"):
        items = getattr(root, attr, None)
        if isinstance(items, (list, tuple)):
            for item in items:
                if isinstance(item, ft.Control):
                    yield from _iter_controls(item)


def test_detail_view_embedded_mode_hides_back_bar_shows_unlimited_and_routes_urls():
    """Embedded DetailView hides its top back bar, labels empty deadlines, and routes links in-app."""
    page_launched: list[str] = []
    in_app_opened: list[str] = []
    fake_page = SimpleNamespace(
        overlay=[],
        web=False,
        platform="windows",
        width=1200,
        height=800,
        update=lambda: None,
        launch_url=page_launched.append,
    )
    dv = DetailView(
        fake_page,  # type: ignore[arg-type]
        on_close=lambda: None,
        embedded_in_pane=True,
        on_open_url=in_app_opened.append,
    )
    assert dv._top_bar_container.visible is False

    base_data = {
        "id": "10",
        "title": "Nop Bai tap nhom",
        "course": "PY101",
        "deadline": 0,
        "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=10",
        "submission_url": "https://courses.ut.edu.vn/mod/assign/view.php?id=10&action=editsubmission",
    }
    dv.show_loading(base_data)
    dv.update_detail(
        {
            **base_data,
            "description": "Xem huong dan tai https://zalo.me/g/nhom_lop_a",
            "submission_status": "submitted",
            "allow_submissions_from": 0,
        }
    )
    texts = [c.value for c in _iter_controls(dv) if isinstance(c, ft.Text) and c.value]
    assert "Không giới hạn" in texts
    assert "Không giới hạn thời gian" in texts

    async def _trigger_urls() -> None:
        await dv._open_browser(None)
        await dv._on_edit_submitted(None)
        await dv._handle_link_tap(SimpleNamespace(data="https://zalo.me/g/nhom_lop_a"))

    asyncio.run(_trigger_urls())
    assert in_app_opened == [
        "https://courses.ut.edu.vn/mod/assign/view.php?id=10",
        "https://courses.ut.edu.vn/mod/assign/view.php?id=10",
        "https://zalo.me/g/nhom_lop_a",
    ]
    assert page_launched == []


def test_pane_assignment_view_skeleton_loading_and_error_recovery():
    """PaneAssignmentView swaps between skeleton, DetailView, and recoverable error strip with single header."""
    refreshed: list[bool] = []
    fake_page = SimpleNamespace(
        overlay=[],
        web=False,
        platform="windows",
        width=1200,
        height=800,
        update=lambda: None,
        launch_url=lambda _u: None,
    )
    dv = DetailView(
        fake_page,  # type: ignore[arg-type]
        on_close=lambda: None,
        embedded_in_pane=True,
        on_open_url=lambda _u: None,
    )
    pav = PaneAssignmentView(
        detail_view=dv,
        title="Bai tap nhom",
        on_close=lambda: None,
        on_refresh=lambda: refreshed.append(True),
    )

    data = {"id": "10", "title": "Bai tap nhom", "course": "PY101", "deadline": 0}
    pav.show_loading(data)
    assert pav._body_switcher.content is not dv
    assert pav._error_strip.visible is False

    pav.show_error_banner()
    assert pav._error_strip.visible is True

    refresh_btn = next(
        c
        for c in _iter_controls(pav._header_container)
        if isinstance(c, ft.IconButton) and c.tooltip == "Tải lại chi tiết bài tập"
    )
    refresh_btn.on_click(None)
    assert refreshed == [True]

    pav.update_detail({**data, "description": "Noi dung day du"})
    assert pav._body_switcher.content is dv
    assert pav._error_strip.visible is False


def test_course_detail_pane_shows_skeleton_while_loading_detail():
    """CourseDetailPane renders loading skeletons instead of premature empty messages during detail fetch."""
    course = _sample_course()
    pane = CourseDetailPane()
    state = CoursesState(
        courses=[course],
        selected_course_key=course.course_key,
        loading_detail=True,
        sections=[],
        grades=[],
    )
    pane.render(state, show_back=False, update=False)

    content_texts = [
        c.value for c in _iter_controls(pane._content_tab_body) if isinstance(c, ft.Text) and c.value
    ]
    grades_texts = [
        c.value for c in _iter_controls(pane._grades_tab_body) if isinstance(c, ft.Text) and c.value
    ]
    assert not any("chưa có nội dung" in t.lower() for t in content_texts)
    assert not any("chưa có dữ liệu điểm" in t.lower() for t in grades_texts)
    assert len(list(_iter_controls(pane._content_tab_body))) > 5


def test_coordinator_open_and_save_attachment_ux_feedback(tmp_path: Path):
    """open_attachment_async shows immediate PaneLoadingOverlay, transitions to reader or error retry; save emits SnackBar."""
    store = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|u")
    pcache = PreviewFileCache(tmp_path / "preview")
    server = LocalPreviewServer()
    fake_page = SimpleNamespace(
        overlay=[],
        web=False,
        platform="windows",
        title="UTHelper",
        width=1200,
        height=800,
        update=lambda: None,
        run_task=lambda fn, *a: asyncio.run(fn(*a)),
    )
    coord = CoursesCoordinator(
        page=fake_page,  # type: ignore[arg-type]
        catalog_service=CourseCatalogService(cache_store=store, preview_cache=pcache),
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=server,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        course = _sample_course()
        mod = _sample_module(30, "resource", "Ghi chu", open_strategy="document_reader")
        att = CourseAttachment(
            file_name="notes.txt",
            file_url="https://courses.ut.edu.vn/pluginfile.php/1/notes.txt",
            preview_kind="text",
            file_size=128,
        )

        # 1. Verify loading overlay is active while slow download is in progress
        saw_loading: list[bool] = []
        should_fail = [False]

        def _fake_fetch(_source: str, _url: str) -> bytes:
            saw_loading.append(
                bool(coord.courses_view._overlays)
                and isinstance(coord.courses_view._overlays[-1], PaneLoadingOverlay)
            )
            if should_fail[0]:
                raise RuntimeError("Moodle timeout")
            return b"Noi dung tai lieu"

        coord.catalog_service.fetch_attachment_bytes = _fake_fetch  # type: ignore[method-assign]
        asyncio.run(coord.open_attachment_async(course, mod, att))
        assert saw_loading == [True]
        assert isinstance(coord.courses_view._overlays[-1], DocumentReaderView)

        # 2. Verify error path shows PaneErrorOverlay and retry recovers cleanly
        uncached_att = CourseAttachment(
            file_name="notes2.txt",
            file_url="https://courses.ut.edu.vn/pluginfile.php/2/notes2.txt",
            preview_kind="text",
            file_size=64,
        )
        should_fail[0] = True
        asyncio.run(coord.open_attachment_async(course, mod, uncached_att))
        err_overlay = coord.courses_view._overlays[-1]
        assert isinstance(err_overlay, PaneErrorOverlay)

        should_fail[0] = False
        retry_btn = next(
            c
            for c in _iter_controls(err_overlay)
            if isinstance(c, ft.FilledButton)
            and (
                getattr(c, "content", None) == "Thử tải lại"
                or getattr(c, "text", None) == "Thử tải lại"
            )
        )
        retry_btn.on_click(None)
        assert isinstance(coord.courses_view._overlays[-1], DocumentReaderView)

        # 3. Verify save_attachment_async emits immediate download SnackBar feedback
        saved_files: list[tuple[str, bytes]] = []

        async def _fake_save(name: str, data: bytes) -> None:
            saved_files.append((name, data))

        coord._save_bytes_async = _fake_save  # type: ignore[method-assign]
        asyncio.run(coord.save_attachment_async(att, course.source))
        assert saved_files == [("notes.txt", b"Noi dung tai lieu")]
        snacks = [item for item in fake_page.overlay if isinstance(item, ft.SnackBar)]
        assert len(snacks) == 1
        assert "notes.txt" in (snacks[0].content.value or "")
    finally:
        coord.close()


def test_webview2_runtime_probe_and_zero_js_leakage_in_fallback(monkeypatch: pytest.MonkeyPatch):
    """WebView2 probe calls 0-arg version check; fallback never leaks raw SPA/Moodle JS or CSS."""
    import sys
    import types

    calls: list[str] = []

    class _FakeEnv:
        @staticmethod
        def get_available_browser_version_string(*args):
            if args:
                raise TypeError("Invalid parameter count")
            calls.append("zero_arg")
            return "157.0.4341.46"

    fake_wv2 = types.ModuleType("webview2.microsoft.web.webview2.core")
    fake_wv2.CoreWebView2Environment = _FakeEnv  # type: ignore[attr-defined]
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "webview2", types.ModuleType("webview2"))
    monkeypatch.setitem(sys.modules, "webview2.microsoft", types.ModuleType("webview2.microsoft"))
    monkeypatch.setitem(sys.modules, "webview2.microsoft.web", types.ModuleType("webview2.microsoft.web"))
    monkeypatch.setitem(sys.modules, "webview2.microsoft.web.webview2", types.ModuleType("webview2.microsoft.web.webview2"))
    monkeypatch.setitem(sys.modules, "webview2.microsoft.web.webview2.core", fake_wv2)
    fake_winrt_foundation = types.ModuleType("winrt.windows.foundation")
    fake_winrt_foundation.Rect = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "winrt", types.ModuleType("winrt"))
    monkeypatch.setitem(sys.modules, "winrt.windows", types.ModuleType("winrt.windows"))
    monkeypatch.setitem(sys.modules, "winrt.windows.foundation", fake_winrt_foundation)
    monkeypatch.setitem(sys.modules, "win32con", types.ModuleType("win32con"))
    monkeypatch.setitem(sys.modules, "win32gui", types.ModuleType("win32gui"))

    assert _webview2_runtime_available() is True
    assert calls == ["zero_arg"]

    # Force fallback mode (no embedded webview) and verify Zalo / Google Meet never leak raw JS
    monkeypatch.setattr(
        "gui.components.courses.webview_pane.build_embedded_webview_host",
        lambda *_args, **_kwargs: None,
    )
    fake_page = SimpleNamespace(
        overlay=[],
        web=False,
        platform="windows",
        title="UTHelper",
        width=1200,
        height=800,
        update=lambda: None,
    )
    server = LocalPreviewServer()
    try:
        for spa_url in (
            "https://zalo.me/g/4galccv0l49afuffmqnq",
            "https://meet.google.com/fsy-iqay-rgs",
        ):
            pane = CoursesWebViewPane(
                page=fake_page,  # type: ignore[arg-type]
                source="courses",
                title="Link lop hoc",
                url=spa_url,
                preview_server=server,
                session_client=None,
                on_back=lambda: None,
                on_open_external=lambda _u: None,
                embedded_webview_factory=lambda _u: None,
            )
            # Wait briefly for background fallback thread if any
            import time

            for _ in range(30):
                if not any(
                    isinstance(c, ft.ProgressRing) for c in _iter_controls(pane._body_host)
                ):
                    break
                time.sleep(0.02)
            all_text = " ".join(
                str(getattr(c, "value", "") or "")
                for c in _iter_controls(pane)
                if isinstance(c, (ft.Text, ft.Markdown))
            )
            assert "window.AppConfig" not in all_text
            assert "MeetingsUnsupportedUi" not in all_text
            assert "JSON.parse(atob" not in all_text
            pane.dispose()

        # Authenticated UTH Moodle HTML decomposes <script> and <style> before sanitization
        uth_html = """
        <html>
          <head>
            <title>VPL Lab</title>
            <style>.secret-css { display: none; }</style>
            <script>window.M = {cfg: "SECRET_JS_CONFIG"};</script>
          </head>
          <body>
            <div role="main">
              <h3>Bài thực hành tuần 4</h3>
              <p>Sinh viên nộp mã nguồn Python đúng hạn.</p>
              <script>console.log("inline_tracker_leak");</script>
            </div>
          </body>
        </html>
        """
        fake_client = SimpleNamespace(
            get_session_cookies=lambda **_k: {"MoodleSession": "abc"},
            open_session_url=lambda u: (u, uth_html, False),
            login_session=lambda **_k: True,
        )
        uth_pane = CoursesWebViewPane(
            page=fake_page,  # type: ignore[arg-type]
            source="courses",
            title="VPL Lab",
            url="https://courses.ut.edu.vn/mod/vpl/view.php?id=99",
            preview_server=server,
            session_client=fake_client,
            on_back=lambda: None,
            on_open_external=lambda _u: None,
            embedded_webview_factory=lambda _u: None,
        )
        import time

        for _ in range(40):
            if not any(
                isinstance(c, ft.ProgressRing) for c in _iter_controls(uth_pane._body_host)
            ):
                break
            time.sleep(0.02)
        rendered_md = " ".join(
            str(getattr(c, "value", "") or "")
            for c in _iter_controls(uth_pane)
            if isinstance(c, (ft.Text, ft.Markdown))
        )
        assert "Bài thực hành tuần 4" in rendered_md
        assert "Sinh viên nộp mã nguồn Python đúng hạn." in rendered_md
        assert "SECRET_JS_CONFIG" not in rendered_md
        assert "inline_tracker_leak" not in rendered_md
        assert "secret-css" not in rendered_md
        uth_pane.dispose()
    finally:
        server.stop()


def test_quiz_attempt_review_report_sequential_numbering_gift_strip_choices_and_filters():
    """Quiz review strips GIFT codes, numbers shuffled slots 1..N, highlights student & right choices, and filters."""
    cleaned, code = clean_moodle_question_text(
        "::C2-GCCN VN-CLO2.1-VD-C.067::[html] Trong một nhà máy, công nhân cần gì? _______"
    )
    assert code == "C2-GCCN VN-CLO2.1-VD-C.067"
    assert "::" not in cleaned and "[html]" not in cleaned
    assert "Trong một nhà máy, công nhân cần gì?" in cleaned

    ws_payload = {
        "grade": "6.67",
        "attempt": {
            "id": 501,
            "attempt": 1,
            "state": "finished",
            "timestart": 1700000000,
            "timefinish": 1700000900,
            "sumgrades": 2.0,
        },
        "questions": [
            {
                "slot": 30,
                "number": 1,
                "type": "multichoice",
                "status": "Incorrect",
                "state": "gradedwrong",
                "mark": "0.00",
                "maxmark": 1.0,
                "html": """
                <div class="que multichoice deferredfeedback incorrect">
                  <div class="info"><h3 class="no">Question <span class="qno">1</span></h3></div>
                  <div class="content">
                    <div class="formulation clearfix">
                      <div class="qtext">::C2-GCCN VN-CLO2.1-VD-C.067::[html] Trong một nhà máy, công nhân ít hiểu biết về quyền lao động. Vấn đề nào cần nhận diện? _____</div>
                      <div class="ablock">
                        <div class="answer">
                          <div class="r0"><input type="radio" name="q1:30_answer" value="0" /><div class="d-flex">a. Kỹ năng ngoại ngữ</div></div>
                          <div class="r1 incorrect"><input type="radio" name="q1:30_answer" value="1" checked="checked" /><div class="d-flex">b. Thiếu máy móc hiện đại</div><div class="specificfeedback">Chưa chính xác</div></div>
                          <div class="r0"><input type="radio" name="q1:30_answer" value="2" /><div class="d-flex">c. Quyền lao động và cơ chế đại diện tập thể</div></div>
                          <div class="r1"><input type="radio" name="q1:30_answer" value="3" /><div class="d-flex">d. Thiếu nguyên vật liệu</div></div>
                        </div>
                      </div>
                    </div>
                    <div class="outcome clearfix">
                      <div class="feedback">
                        <div class="specificfeedback">Câu trả lời của bạn chưa đúng.</div>
                        <div class="generalfeedback">Xem mục 2.1 giáo trình CNXHKH.</div>
                        <div class="rightanswer">The correct answer is: Quyền lao động và cơ chế đại diện tập thể</div>
                      </div>
                    </div>
                  </div>
                </div>
                """,
            },
            {
                "slot": 22,
                "number": 2,
                "type": "multichoice",
                "status": "Correct",
                "state": "gradedright",
                "mark": "1.00",
                "maxmark": 1.0,
                "html": """
                <div class="que multichoice deferredfeedback correct">
                  <div class="content">
                    <div class="formulation clearfix">
                      <div class="qtext">::C2-GCCN VN-CLO2.1-VD-C.059::[html] Đặc điểm xuất thân từ nông dân tạo ra tác động gì?</div>
                      <div class="answer">
                        <div class="r0 correct"><input type="radio" checked="checked" /><label>a. Tác phong công nghiệp còn hạn chế ở một bộ phận</label></div>
                        <div class="r1"><input type="radio" /><label>b. Không có tinh thần đoàn kết</label></div>
                      </div>
                    </div>
                    <div class="outcome"><div class="rightanswer">Đáp án đúng là: Tác phong công nghiệp còn hạn chế ở một bộ phận</div></div>
                  </div>
                </div>
                """,
            },
            {
                "slot": 115,
                "number": 3,
                "type": "multichoice",
                "status": "Correct",
                "state": "gradedright",
                "mark": "1.00",
                "maxmark": 1.0,
                "html": """
                <div class="que multichoice deferredfeedback correct">
                  <div class="content">
                    <div class="formulation clearfix">
                      <div class="qtext">::C2-DK-SMLS-040::[html] Địa vị kinh tế - xã hội của GCCN là gì?</div>
                      <div class="answer">
                        <div class="r0 correct"><input type="radio" checked="checked" /><label>a. Đại diện lực lượng sản xuất tiên tiến</label></div>
                        <div class="r1"><input type="radio" /><label>b. Sở hữu tư liệu sản xuất tư nhân</label></div>
                      </div>
                    </div>
                  </div>
                </div>
                """,
            },
        ],
    }

    report = parse_quiz_attempt_review_payload(ws_payload, fallback_attempt_id=501)
    assert [q.display_number for q in report.questions] == ["1", "2", "3"]
    assert [q.slot for q in report.questions] == ["30", "22", "115"]
    assert report.correct_count == 2
    assert report.incorrect_count == 1
    assert report.questions[0].question_code == "C2-GCCN VN-CLO2.1-VD-C.067"
    assert "::C2-GCCN" not in report.questions[0].question_text
    assert len(report.questions[0].choices) == 4
    # Picked choice B is marked selected + incorrect; Choice C is cross-matched from .rightanswer as correct!
    ch_b = report.questions[0].choices[1]
    ch_c = report.questions[0].choices[2]
    assert ch_b.label == "B" and ch_b.is_selected is True and ch_b.state == "incorrect"
    assert ch_c.label == "C" and ch_c.is_selected is False and ch_c.state == "correct"
    assert "2.1 giáo trình" in report.questions[0].general_feedback

    # Verify QuizActivityView renders sequential Câu 1..3, hero stats, and interactive wrong-only filter
    quiz_mod = _sample_module(55, "quiz", "Bài tập tự học Chương 2", instance_id=77)
    summary_obj = QuizActivitySummary(
        cmid=55,
        quiz_id=77,
        title="Bài tập tự học Chương 2",
        intro="",
        attempts=(),
    )
    fake_act_svc = SimpleNamespace(
        fetch_quiz_activity=lambda *_a, **_k: summary_obj,
        fetch_quiz_review_report=lambda *_a, **_k: report,
    )
    view = QuizActivityView(
        source="courses",
        course_id=101,
        module=quiz_mod,
        activity_service=fake_act_svc,  # type: ignore[arg-type]
        on_close=lambda: None,
        on_open_webview=lambda _t, _u: None,
    )
    import time

    for _ in range(25):
        if not any(isinstance(c, ft.ProgressRing) for c in _iter_controls(view._body_host)):
            break
        time.sleep(0.01)
    view._render_attempt_review(summary_obj, None, report)

    texts = [c.value for c in _iter_controls(view) if isinstance(c, ft.Text) and c.value]
    assert "Câu 1" in texts and "Câu 2" in texts and "Câu 3" in texts
    assert "Câu 30" not in texts and "Câu 22" not in texts and "Câu 115" not in texts
    assert not any("::C2-GCCN" in t for t in texts)
    assert any("Bạn chọn · Sai" in t for t in texts)
    assert any("Đáp án đúng" in t for t in texts)
    assert not any(isinstance(c, ft.ProgressBar) for c in _iter_controls(view))

    # Filter to "Câu sai" via actual UI filter chip on_select -> only Câu 1 card remains
    wrong_chip = next(
        c
        for c in _iter_controls(view)
        if isinstance(c, ft.Chip)
        and "Câu sai" in str(getattr(c.label, "value", "") or "")
    )
    wrong_chip.on_select(None)
    wrong_texts = [c.value for c in _iter_controls(view) if isinstance(c, ft.Text) and c.value]
    assert "Câu 1" in wrong_texts
    assert "Câu 2" not in wrong_texts
    assert "Câu 3" not in wrong_texts
    view.dispose()


def test_detail_view_never_hangs_on_syncing_status_and_courses_pane_bounds(tmp_path: Path):
    """DetailView resolves stuck 'Đang đồng bộ với Moodle...' on error, and CoursesTabView bounds/tap behave cleanly."""
    fake_page = SimpleNamespace(
        overlay=[],
        web=False,
        platform="windows",
        width=1120,
        height=760,
        update=lambda: None,
        run_task=lambda *_a: None,
    )
    fake_client = SimpleNamespace(
        moodle_site_origin="https://courses.ut.edu.vn",
    )

    class _FailingWorkflow:
        def load_snapshot(self, **_k):
            raise RuntimeError("HTTP 403 / Offline")

    dv = DetailView(
        fake_page,  # type: ignore[arg-type]
        on_close=lambda: None,
        get_client=lambda: fake_client,
        submission_workflow_factory=lambda _c: _FailingWorkflow(),
        embedded_in_pane=True,
        on_open_url=lambda _u: None,
    )
    payload = {
        "id": "10",
        "title": "Nộp Bài tập nhóm thuyết trình",
        "course": "Chủ nghĩa xã hội khoa học",
        "course_id": 101,
        "type": "assignment",
        "deadline": 0,
        "submission_status": "unknown",
        "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=10",
        "details": {"description_html": "<p>Hướng dẫn</p>"},
    }
    dv.update_detail(payload)
    assert dv._submission_status_value.value == "Đang đồng bộ với Moodle..."

    asyncio.run(
        dv._async_load_submitted_files(
            fake_client,
            payload["url"],
            101,
            None,
            dv._snapshot_load_generation,
            dv._view_generation,
        )
    )
    assert dv._submission_status_value.value != "Đang đồng bộ với Moodle..."
    assert "đồng bộ" in dv._submission_status_value.value.lower()

    # Verify CoursesTabView._compute_pane3_bounds and tapping active course clears Pane-3 overlay
    store = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|u2")
    pcache = PreviewFileCache(tmp_path / "preview2")
    c_svc = CourseCatalogService(cache_store=store, preview_cache=pcache)
    c1 = _sample_course("courses", 101)
    store.save_catalog([c1], synced_at=1000)

    tab_view = CoursesTabView(catalog_service=c_svc)
    tab_view.set_width(1048.0)  # content_width (1120 - 72 rail)
    left, top, width, height = tab_view._compute_pane3_bounds()
    assert left == 410.0
    assert top == 36.0
    assert width == 727.0

    asyncio.run(tab_view.load_initial_async())
    asyncio.run(tab_view._select_course_async(c1.course_key))
    tab_view.push_overlay(ft.Container(), tag="webview:test")
    assert len(tab_view._overlays) == 1
    # Clicking the already-selected course card in Pane 1 dismisses the overlay cleanly
    asyncio.run(tab_view._on_user_select_course_async(c1.course_key))
    assert len(tab_view._overlays) == 0


def test_courses_webview_pane_blocks_html_fallback_ssrf_and_flet_webview_disallowed_urls(
    monkeypatch: pytest.MonkeyPatch,
):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    from unittest.mock import Mock

    from gui.components.courses import webview_pane as webview_pane_mod
    from gui.components.courses.embedded_webview_host import EmbeddedWebViewHost

    # 1. Local server returning HTML with unsafe links & a 302 redirect off origin
    class _TrapHandler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            return

        def do_GET(self):
            if self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", "https://evil.example/steal")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<main><p>Trap reached</p></main>")

    trap_srv = HTTPServer(("127.0.0.1", 0), _TrapHandler)
    trap_port = trap_srv.server_address[1]
    trap_thread = threading.Thread(target=trap_srv.serve_forever, daemon=True)
    trap_thread.start()

    try:
        monkeypatch.setattr(webview_pane_mod, "build_embedded_webview_host", lambda *_a, **_kw: None)
        pane = CoursesWebViewPane(
            page=None,
            source="courses",
            title="Test",
            url=f"http://127.0.0.1:{trap_port}/secret",
            preview_server=None,
            on_back=lambda: None,
            on_open_external=lambda _u: None,
        )
        # Unowned loopback URL must not be fetched via urllib in fallback mode
        pane._load_authenticated_html_fallback()
        pane._navigate_link(f"http://127.0.0.1:{trap_port}/secret")
        pane._navigate_link("javascript:alert(1)")
        pane._navigate_link("http://evil.example/")
        assert pane._url == f"http://127.0.0.1:{trap_port}/secret"
        texts = [c.value for c in _iter_controls(pane._body_host) if isinstance(c, ft.Text) and c.value]
        assert not any("Trap reached" in t for t in texts)
        pane.dispose()

        # 2. Active preview server origin blocks off-origin 302 redirect & filters extracted_links
        active_srv = LocalPreviewServer()
        try:
            active_port = active_srv._ensure_started_locked()
            fake_preview = SimpleNamespace(
                is_active_loopback_origin=lambda u: f"127.0.0.1:{trap_port}" in str(u),
            )
            pane_redir = CoursesWebViewPane(
                page=None,
                source="courses",
                title="Redir",
                url=f"http://127.0.0.1:{trap_port}/redir",
                preview_server=fake_preview,
                on_back=lambda: None,
                on_open_external=lambda _u: None,
            )
            pane_redir._load_authenticated_html_fallback()
            redir_texts = [
                c.value for c in _iter_controls(pane_redir._body_host) if isinstance(c, ft.Text) and c.value
            ]
            assert any("redirect" in t.lower() or "preview" in t.lower() for t in redir_texts)
            pane_redir.dispose()

            # extracted_links filters out unsafe/loopback links when rendering Moodle HTML
            moodle_html = (
                "<main><p>Xin chào</p>"
                "<a href='https://courses.ut.edu.vn/mod/page/view.php?id=2'>Safe Link</a>"
                f"<a href='http://127.0.0.1:{active_port}/steal'>Local SSRF</a>"
                "<a href='http://evil.example/'>Plain HTTP</a>"
                "<a href='javascript:alert(1)'>XSS</a>"
                "</main>"
            )
            pane_links = CoursesWebViewPane(
                page=None,
                source="courses",
                title="Moodle",
                url="https://courses.ut.edu.vn/mod/page/view.php?id=1",
                preview_server=None,
                session_client=SimpleNamespace(
                    open_session_url=lambda u: (u, moodle_html, False),
                ),
                on_back=lambda: None,
                on_open_external=lambda _u: None,
            )
            pane_links._load_authenticated_html_fallback()
            link_labels = [
                c.content
                for c in _iter_controls(pane_links._body_host)
                if isinstance(c, ft.OutlinedButton)
            ]
            assert link_labels == ["Safe Link"]
            pane_links.dispose()
        finally:
            active_srv.stop()

        # 3. _on_host_error enforces Blocked disallowed WebView URL on flet_webview
        wv_ctrl = ft.Container(expand=True, data="fake-flet-webview")
        flet_host = EmbeddedWebViewHost(
            backend="flet_webview",
            url="https://courses.ut.edu.vn/mod/page/view.php?id=1",
            webview_control=wv_ctrl,
        )
        flet_host.dispose = Mock(wraps=flet_host.dispose)  # type: ignore[method-assign]
        pane_fwv = CoursesWebViewPane(
            page=None,
            source="courses",
            title="Mobile",
            url="https://courses.ut.edu.vn/mod/page/view.php?id=1",
            embedded_webview_factory=lambda _u: flet_host,
            on_back=lambda: None,
            on_open_external=lambda _u: None,
        )
        assert pane_fwv._host is flet_host
        pane_fwv._on_host_error("Blocked disallowed WebView URL: http://evil.example/phish")
        flet_host.dispose.assert_called_once()
        assert pane_fwv._host is None
        assert wv_ctrl not in list(_iter_controls(pane_fwv._body_host))
        blocked_texts = [
            c.value for c in _iter_controls(pane_fwv._body_host) if isinstance(c, ft.Text) and c.value
        ]
        assert any("chặn" in t.lower() for t in blocked_texts)
        pane_fwv.dispose()
    finally:
        trap_srv.shutdown()
        trap_srv.server_close()


def test_course_detail_pane_segment_semantics_exclude_duplicate_children():
    pane = CourseDetailPane()
    assert len(pane._segment_semantics) == 3
    assert all(sem.exclude_semantics is True for sem in pane._segment_semantics)
