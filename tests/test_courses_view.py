from __future__ import annotations

from types import SimpleNamespace

import flet as ft

from core.course_grade_monitor import CourseGradeMonitor
from core.course_grade_scraper import CourseGradeReport, GradeRow
from core.course_grades_service import CourseWithGrades
from core.moodle_service import MoodleService
from gui.components.courses_view import CoursesView
from gui.components.grade_overview_view import GradeOverviewView
from gui.core.theme import C, apply_theme
from gui.view_manager import ViewManager


def _walk(control):
    if control is None:
        return
    yield control
    content = getattr(control, "content", None)
    if content is not None:
        yield from _walk(content)
    for child in getattr(control, "controls", None) or ():
        yield from _walk(child)


def _sample_course(course_id=10, name="Lập trình Web", grade="8.5", pct="90%"):
    report = CourseGradeReport(
        course_id=course_id,
        rows=[
            GradeRow(name="Chuyên cần", grade=grade, percentage=pct, is_attendance=True),
            GradeRow(name="Tổng khóa học", grade=grade, percentage=pct),
        ],
    )
    return CourseWithGrades(
        course_id=course_id,
        name=name,
        shortname="WEB101",
        report=report,
    )


def test_courses_view_update_theme_and_loading_refresh():
    try:
        apply_theme("midnight_blue")
        view = CoursesView()
        view.update_courses([_sample_course()])
        apply_theme("steel_light")
        view.update_theme()

        assert view.bgcolor == C.BG
        assert view._title.color == C.TEXT_PRIMARY
        assert view._header_divider.color == C.BORDER

        # ViewManager.show_courses_loading() also refreshes CoursesView theme
        apply_theme("midnight_blue")
        vm = ViewManager(
            page=SimpleNamespace(update=lambda: None),
            dashboard=SimpleNamespace(visible=True),
            detail_view=SimpleNamespace(visible=False),
            settings_view=SimpleNamespace(visible=False),
            calendar_view=SimpleNamespace(visible=False),
            grade_overview_view=SimpleNamespace(visible=False),
            controller=SimpleNamespace(
                calendar_btn=SimpleNamespace(icon_color=None),
                grades_btn=SimpleNamespace(icon_color=None),
                courses_btn=SimpleNamespace(icon_color=None),
            ),
            courses_view=view,
        )
        vm.show_courses_loading()
        assert view.bgcolor == C.BG
        assert view.visible is True
    finally:
        apply_theme("midnight_blue")


def test_courses_view_summary_bar_and_stale_notice():
    view = CoursesView()
    view.update_courses([_sample_course()])

    summary_controls = view._list.controls[0]
    texts = [c.value for c in _walk(summary_controls) if isinstance(c, ft.Text)]
    assert any("Môn học" in t or "môn" in t.lower() for t in texts)
    assert view._stale_banner.visible is False

    view.set_stale_notice("Đang hiển thị dữ liệu đã lưu")
    assert view._stale_banner.visible is True
    assert view._stale_text.value == "Đang hiển thị dữ liệu đã lưu"

    view.set_stale_notice("")
    assert view._stale_banner.visible is False


def test_grade_overview_view_show_error_renders_accessible_retry():
    retried = []
    view = GradeOverviewView(
        page=SimpleNamespace(update=lambda: None, width=390, height=700),
        on_refresh=lambda: retried.append(True),
    )
    view.show_error("Không thể tải bảng điểm")

    assert view._error_state.visible is True
    assert view._loading_row.visible is False
    assert view._empty_state.visible is False
    buttons = [c for c in _walk(view._error_state) if isinstance(c, ft.Button)]
    assert len(buttons) == 1
    assert buttons[0].height >= 44
    buttons[0].on_click(None)
    assert retried == [True]


def test_course_grade_monitor_namespaces_snapshots_and_deduplicates_grade_pct_alert(
    tmp_path, monkeypatch
):
    import config

    monkeypatch.setattr(config, "_USER_DATA_DIR", str(tmp_path))
    m1 = CourseGradeMonitor(namespace="https://courses.ut.edu.vn|student1")
    m2 = CourseGradeMonitor(namespace="https://courses.ut.edu.vn|student2")
    assert m1._path != m2._path

    # Seed baseline snapshot for student1
    course_v1 = _sample_course(course_id=10, grade="7.0", pct="70%")
    assert m1.update([course_v1]) == []
    assert m1._path.exists()

    # Changing both grade and pct on each item must emit ONE alert per row (grade), not two
    course_v2 = _sample_course(course_id=10, grade="8.5", pct="85%")
    changes = m1.update([course_v2])
    assert [c.item_name for c in changes] == ["Chuyên cần", "Tổng khóa học"]
    assert all(c.old_grade == "7.0" and c.new_grade == "8.5" for c in changes)

    # student2 remains untouched
    assert m2.update([course_v1]) == []


def test_moodle_client_and_service_grade_report_isolation_and_lock(monkeypatch):
    import threading
    from core.client import MoodleClient

    client = MoodleClient()
    lock_states = []

    def _fake_login(username=None, password=None, force=False, quiz_lease=None):
        del password, force, quiz_lease
        client._session_owner = str(username or "user1").casefold()
        client._sesskey = f"sess-{client._session_owner}"
        return True

    def _fake_open_html(path, data=None):
        del path, data
        contended = []

        def _probe():
            acquired = client._session_lock.acquire(blocking=False)
            contended.append(not acquired)
            if acquired:
                client._session_lock.release()

        t = threading.Thread(target=_probe)
        t.start()
        t.join()
        lock_states.extend(contended)
        return "https://courses.ut.edu.vn/grade/report/user/index.php?id=5", "<html></html>"

    monkeypatch.setattr(client, "login_session", _fake_login)
    monkeypatch.setattr(client, "_open_html", _fake_open_html)

    client._session_owner = "user_a"
    client._sesskey = "old-sess"
    client._reset_session_if_account_changed("user_b")
    assert client._sesskey == ""

    html = client.fetch_grade_report_html(5)
    assert html == "<html></html>"
    assert lock_states == [True]

    service = MoodleService(
        lambda *args, **kwargs: None,
        grade_report_fetcher=lambda _cid: "<table>ok</table>",
        grade_report_error_getter=lambda: "",
    )
    assert service.fetch_grade_report_html(5) == "<table>ok</table>"
    assert service.last_grade_report_error == ""
