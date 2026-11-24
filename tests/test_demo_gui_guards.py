"""Unit and wiring tests for Reviewer Demo Mode GUI guards (`uthelper_demo`)."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import flet as ft
import pytest

from config import settings
from core.courses.models import CourseModuleItem, CourseSummary
from core.demo_sandbox import DEMO_USERNAME
import gui.app_controller as app_controller_module
from gui.app_controller import AppController
import gui.components.login_dialog as login_dialog_module
from gui.components.detail_view import DetailView
from gui.components.login_dialog import show_login_dialog
from gui.components.settings_view import SettingsView
from gui.controllers.courses_coordinator import CoursesCoordinator
from gui.core.theme import THEME_PRESETS, apply_theme

DEMO_LMS_GUARD_COPY = (
    "Phiên xem thử (Demo) sử dụng dữ liệu mẫu ngoại tuyến. "
    "Đăng nhập bằng tài khoản sinh viên UTH để mở cổng LMS trực tuyến."
)
DEMO_ACCOUNT_BANNER_COPY = (
    "Đang dùng tài khoản xem thử (Demo) với dữ liệu mẫu ngoại tuyến. "
    "Nhập MSSV và mật khẩu thật bên dưới để chuyển sang tài khoản sinh viên UTH."
)


def _reviewer_password() -> str:
    return "UTH-" + "Review-2026!"


def _walk_controls(root: object) -> list[object]:
    visited: list[object] = []
    stack = [root]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        visited.append(node)
        for attr in ("content", "title", "leading", "trailing"):
            child = getattr(node, attr, None)
            if child is not None:
                stack.append(child)
        for list_attr in ("controls", "actions"):
            children = getattr(node, list_attr, None)
            if isinstance(children, (list, tuple)):
                stack.extend(reversed(children))
    return visited


def _extract_snackbar_texts(controls: list[object]) -> list[str]:
    texts: list[str] = []
    for item in controls:
        if isinstance(item, ft.SnackBar):
            for node in _walk_controls(item):
                if isinstance(node, ft.Text) and isinstance(node.value, str) and node.value:
                    texts.append(node.value)
    return texts


def _make_dummy_page() -> Mock:
    return Mock(spec=ft.Page, window=Mock(), theme_mode="dark", on_route_change=None)


def test_login_dialog_normalizes_demo_username_without_extra_demo_controls(monkeypatch):
    shown_dialogs: list[ft.AlertDialog] = []
    scheduled_tasks: list[object] = []
    page = SimpleNamespace(
        width=420,
        show_dialog=shown_dialogs.append,
        pop_dialog=Mock(),
        update=Mock(),
        run_task=scheduled_tasks.append,
    )
    login_calls: list[tuple[str, str, bool]] = []

    def fake_login(username: str, password: str, force: bool = False) -> bool:
        login_calls.append((username, password, force))
        return username == DEMO_USERNAME and password == _reviewer_password()

    orchestrator = SimpleNamespace(
        client=SimpleNamespace(login=fake_login),
        is_logged_in=False,
    )
    monkeypatch.setattr(settings, "UTH_USERNAME", "")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")
    monkeypatch.setattr(login_dialog_module, "save_settings", lambda: True)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    on_success = AsyncMock()
    asyncio.run(show_login_dialog(page, orchestrator, on_success, allow_cancel=False))

    dialog = shown_dialogs[0]
    nodes = _walk_controls(dialog)
    text_fields = [n for n in nodes if isinstance(n, ft.TextField)]
    assert len(text_fields) == 2
    username_field, password_field = text_fields

    # Approach B: no extra public "Demo" / "Xem thử" button or label in login_dialog.
    assert dialog.actions == []
    clickable_nodes = [
        n
        for n in nodes
        if not isinstance(n, ft.TextField) and callable(getattr(n, "on_click", None))
    ]
    assert len(clickable_nodes) == 1
    all_texts = [
        n.value.lower()
        for n in nodes
        if isinstance(n, ft.Text) and isinstance(n.value, str)
    ]
    assert not any("demo" in t or "xem thử" in t for t in all_texts)

    username_field.value = "  UTHelper_Demo  "
    password_field.value = _reviewer_password()
    btn_login = clickable_nodes[0]
    asyncio.run(btn_login.on_click(None))

    assert login_calls == [(DEMO_USERNAME, _reviewer_password(), True)]
    assert settings.UTH_USERNAME == DEMO_USERNAME
    assert username_field.value == DEMO_USERNAME
    assert orchestrator.is_logged_in is True
    assert scheduled_tasks == [on_success]


def test_native_background_credentials_returns_empty_in_demo_mode(monkeypatch):
    controller = AppController.__new__(AppController)
    controller.orchestrator = SimpleNamespace(
        client=SimpleNamespace(moodle_site_origin="https://courses.ut.edu.vn")
    )
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "residual-ws-token")
    monkeypatch.setattr(settings, "UTH_USERNAME", "  UTHelper_Demo  ")

    assert controller._native_background_credentials() == ("", "")

    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    assert controller._native_background_credentials() == (
        "residual-ws-token",
        "https://courses.ut.edu.vn",
    )


def test_app_controller_blocks_uth_lms_webview_external_and_quiz_in_demo_mode(
    monkeypatch,
):
    monkeypatch.setattr(settings, "UTH_USERNAME", DEMO_USERNAME)

    controller = AppController.__new__(AppController)
    overlay_controls: list[object] = []
    launched_urls: list[str] = []
    page = SimpleNamespace(
        width=1120,
        height=760,
        web=False,
        platform=ft.PagePlatform.ANDROID,
        overlay=overlay_controls,
        show_dialog=overlay_controls.append,
        pop_dialog=Mock(),
        update=Mock(),
    )
    controller.page = page
    controller._app_webview_host = ft.Container(visible=False)
    controller._app_webview_pane = None

    def run_coro_immediately(fn, *args, **kwargs):
        res = fn(*args, **kwargs)
        if asyncio.iscoroutine(res):
            asyncio.run(res)
        return object()

    controller._safe_run_task = run_coro_immediately

    async def fake_launch_url(_self, url: str, **_kwargs):
        launched_urls.append(url)

    monkeypatch.setattr(ft.UrlLauncher, "launch_url", fake_launch_url)

    quiz_player = SimpleNamespace(
        visible=False,
        prepare_activity=Mock(return_value=(object(), None)),
        open_activity_for_account=Mock(return_value=True),
    )
    controller.quiz_player_view = quiz_player
    courses_coord = SimpleNamespace(open_in_app_webview=Mock())
    controller._courses_coordinator = courses_coord

    # 1. In-app WebView for UTH LMS URL is blocked with Demo snackbar.
    controller._open_url_in_app(
        "https://courses.ut.edu.vn/mod/assign/view.php?id=101",
        "Bài tập 1",
    )
    assert controller._app_webview_pane is None
    assert controller._app_webview_host.visible is False
    assert DEMO_LMS_GUARD_COPY in _extract_snackbar_texts(overlay_controls)

    # 2. External browser for UTH LMS URL (`_open_url_external` & `_launch_safe_url`) is blocked.
    overlay_controls.clear()
    controller._open_url_external("https://courses.ut.edu.vn/course/view.php?id=10")
    assert launched_urls == []
    assert DEMO_LMS_GUARD_COPY in _extract_snackbar_texts(overlay_controls)

    overlay_controls.clear()
    controller._launch_safe_url("https://thnn.ut.edu.vn/mod/quiz/view.php?id=55")
    assert launched_urls == []
    assert DEMO_LMS_GUARD_COPY in _extract_snackbar_texts(overlay_controls)

    # 3. Non-UTH external HTTPS links (e.g. Google Play Store) still open normally!
    overlay_controls.clear()
    play_url = "https://play.google.com/store/apps/details?id=io.github.chouwzi.uthelper"
    controller._open_url_external(play_url)
    assert launched_urls == [play_url]
    assert _extract_snackbar_texts(overlay_controls) == []

    # 4. Starting a live Course Quiz is blocked with Demo snackbar.
    overlay_controls.clear()
    course = CourseSummary(
        id=201,
        source="courses",
        shortname="IT001",
        fullname="Lập trình Python",
        display_name="Lập trình Python",
    )
    quiz_mod = CourseModuleItem(
        cmid=301,
        instance_id=301,
        modname="quiz",
        name="Kiểm tra giữa kỳ",
        section_id=1,
        url="https://courses.ut.edu.vn/mod/quiz/view.php?id=301",
        web_url="https://courses.ut.edu.vn/mod/quiz/view.php?id=301",
    )
    controller._data_lock = threading.Lock()
    controller.all_data = []
    controller._start_course_quiz(course, quiz_mod)

    quiz_player.prepare_activity.assert_not_called()
    quiz_player.open_activity_for_account.assert_not_called()
    courses_coord.open_in_app_webview.assert_not_called()
    assert DEMO_LMS_GUARD_COPY in _extract_snackbar_texts(overlay_controls)


def test_courses_coordinator_and_detail_view_guard_demo_lms_while_keeping_local_preview(
    monkeypatch,
):
    monkeypatch.setattr(settings, "UTH_USERNAME", DEMO_USERNAME)

    overlay_snacks: list[object] = []
    pushed_overlays: list[tuple[object, str]] = []
    launched_external: list[str] = []
    page = SimpleNamespace(
        width=1120,
        height=760,
        web=False,
        platform=ft.PagePlatform.ANDROID,
        overlay=overlay_snacks,
        update=Mock(),
    )

    coord = CoursesCoordinator.__new__(CoursesCoordinator)
    coord._page = page
    coord.preview_server = SimpleNamespace()
    coord._courses_client = SimpleNamespace()
    coord._thnn_client = SimpleNamespace()
    coord._account_namespace = "https://courses.ut.edu.vn|uthelper_demo"
    coord._on_launch_url = launched_external.append
    coord.courses_view = SimpleNamespace(
        pop_overlay=Mock(),
        toggle_pane3_expanded=Mock(),
        push_overlay=lambda ctrl, tag="": pushed_overlays.append((ctrl, tag)),
        _compute_pane3_bounds=lambda: (0.0, 0.0, 680.0, 700.0),
    )

    # UTH LMS WebView inside Courses is blocked with Demo snackbar.
    coord.open_in_app_webview(
        "courses",
        "Bài tập",
        "https://courses.ut.edu.vn/mod/assign/view.php?id=101",
    )
    assert pushed_overlays == []
    assert DEMO_LMS_GUARD_COPY in _extract_snackbar_texts(overlay_snacks)

    # UTH external URL via CoursesCoordinator._launch_url_safe is blocked.
    overlay_snacks.clear()
    coord._launch_url_safe("https://thnn.ut.edu.vn/course/view.php?id=9")
    assert launched_external == []
    assert DEMO_LMS_GUARD_COPY in _extract_snackbar_texts(overlay_snacks)

    # Local 127.0.0.1 preview URLs (`build_webview_host`) and non-UTH HTTPS links still work!
    overlay_snacks.clear()
    preview_host = coord.build_webview_host(
        "http://127.0.0.1:54123/pdf-viewer/token123/"
    )
    assert preview_host is not None
    coord.open_in_app_webview(
        "courses",
        "Python Docs",
        "https://docs.python.org/3/",
    )
    assert len(pushed_overlays) == 1
    assert _extract_snackbar_texts(overlay_snacks) == []

    coord._launch_url_safe("https://github.com/Chouwzi/UTHelper")
    assert launched_external == ["https://github.com/Chouwzi/UTHelper"]

    # DetailView "Làm bài" quiz button shows Demo snackbar instead of opening QuizPlayerView.
    overlay_snacks.clear()
    quiz_player_mock = SimpleNamespace(
        visible=False,
        open_activity_for_account=Mock(return_value=True),
        open_activity=Mock(return_value=True),
    )
    detail_view = DetailView(
        page,
        on_close=Mock(),
        get_client=lambda: SimpleNamespace(),
        quiz_player_view=quiz_player_mock,
        get_quiz_account_id=lambda: DEMO_USERNAME,
    )
    detail_view._quiz_open_btn.disabled = False
    detail_view._quiz_launch_target = object()
    detail_view._current_data = {
        "title": "Quiz Chương 1",
        "type": "quiz",
        "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=301",
    }
    asyncio.run(detail_view._open_quiz(None))

    quiz_player_mock.open_activity_for_account.assert_not_called()
    quiz_player_mock.open_activity.assert_not_called()
    assert DEMO_LMS_GUARD_COPY in _extract_snackbar_texts(overlay_snacks)


def test_card_detail_warmup_and_hydration_run_in_demo_mode_without_ws_token(
    monkeypatch,
):
    monkeypatch.setattr(app_controller_module.settings, "UTH_USERNAME", DEMO_USERNAME)
    monkeypatch.setattr(app_controller_module.settings, "MOODLE_WS_TOKEN", "")

    controller = AppController.__new__(AppController)
    quiz = {
        "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=301",
        "type": "quiz",
        "deadline": "2026-11-26 13:00:00",
        "submission_status": "unknown",
    }
    controller.all_data = [quiz]
    controller._data_lock = threading.Lock()
    controller._prefetch_cancel_event = threading.Event()
    controller._prefetch_epoch = 0
    scheduled: list[tuple] = []
    controller._safe_run_task = lambda *args: scheduled.append(args) or object()

    # Valid demo credentials -> `has_site_credentials` is True -> both schedule!
    controller.orchestrator = SimpleNamespace(
        client=SimpleNamespace(has_site_credentials=True)
    )
    assert controller._schedule_card_detail_warmup() is True
    assert controller._schedule_cached_detail_hydration(True) is True
    assert len(scheduled) == 2

    # Unverified demo credentials -> `has_site_credentials` is False -> blocked!
    scheduled.clear()
    controller.orchestrator = SimpleNamespace(
        client=SimpleNamespace(has_site_credentials=False)
    )
    assert controller._schedule_card_detail_warmup() is False
    assert controller._schedule_cached_detail_hydration(True) is False
    assert scheduled == []


@pytest.mark.parametrize(
    ("username", "expected_visible"),
    [
        ("uthelper_demo", True),
        ("  UTHelper_Demo  ", True),
        ("2251120001", False),
        ("", False),
    ],
)
def test_settings_account_section_toggles_demo_banner_and_normalizes_username(
    monkeypatch, username, expected_visible
):
    monkeypatch.setattr(settings, "UTH_USERNAME", username)
    page = _make_dummy_page()
    orchestrator = SimpleNamespace(
        client=SimpleNamespace(login=Mock(return_value=True)),
        get_cached_details_snapshot=lambda: {},
    )
    view = SettingsView(
        page=page,
        orchestrator=orchestrator,
        on_close=Mock(),
        autostart_coordinator=None,
    )
    view.update = Mock()
    view._navigate_to_detail("account")

    banner = getattr(view, "_demo_account_banner", None)
    assert isinstance(banner, ft.Container)
    assert banner.visible is expected_visible
    banner_texts = [
        n.value
        for n in _walk_controls(banner)
        if isinstance(n, ft.Text) and isinstance(n.value, str)
    ]
    assert DEMO_ACCOUNT_BANNER_COPY in banner_texts

    # Verify theme refresh updates banner styling cleanly without breaking visibility.
    initial_theme = "midnight_blue"
    try:
        apply_theme("nord_frost")
        view._refresh_section_colors()
        assert banner.visible is expected_visible
        assert banner.border is not None
        assert banner.bgcolor is not None
        assert THEME_PRESETS["nord_frost"]["accent"].lower() in str(banner.bgcolor).lower()
    finally:
        apply_theme(initial_theme)


def test_settings_test_login_and_save_normalize_demo_username_and_refresh_banner(
    monkeypatch,
):
    import gui.components.settings_view as settings_view_module

    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "student-pass")
    monkeypatch.setattr(settings_view_module, "save_settings", lambda: True)

    login_calls: list[tuple[str, str, bool]] = []

    def fake_login(username: str, password: str, force: bool = False) -> bool:
        login_calls.append((username, password, force))
        settings.UTH_USERNAME = username
        settings.UTH_PASSWORD = password
        return True

    page = _make_dummy_page()
    orchestrator = SimpleNamespace(
        client=SimpleNamespace(login=fake_login),
        get_cached_details_snapshot=lambda: {},
    )
    view = SettingsView(
        page=page,
        orchestrator=orchestrator,
        on_close=Mock(),
        autostart_coordinator=None,
    )
    view.update = Mock()
    view._baseline_snapshot = view._capture_form_snapshot()
    assert view._demo_account_banner.visible is False

    # 1. `_handle_test_login` normalizes `"  UTHelper_Demo  "` -> `"uthelper_demo"` and shows banner.
    view._username_field.value = "  UTHelper_Demo  "
    view._password_field.value = _reviewer_password()
    asyncio.run(view._handle_test_login(None))

    assert login_calls == [(DEMO_USERNAME, _reviewer_password(), True)]
    assert view._username_field.value == DEMO_USERNAME
    assert view._demo_account_banner.visible is True

    # 2. Saving a real MSSV hides the Demo banner; saving `"  UTHelper_Demo  "` normalizes & shows it.
    view._username_field.value = "2251120999"
    view._password_field.value = "new-student-pass"
    assert asyncio.run(view._save(None)) is True
    assert settings.UTH_USERNAME == "2251120999"
    assert view._demo_account_banner.visible is False

    view._username_field.value = "  UTHelper_Demo  "
    view._password_field.value = _reviewer_password()
    assert asyncio.run(view._save(None)) is True
    assert settings.UTH_USERNAME == DEMO_USERNAME
    assert view._username_field.value == DEMO_USERNAME
    assert view._demo_account_banner.visible is True
