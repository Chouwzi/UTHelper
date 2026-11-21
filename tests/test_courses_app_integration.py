"""Integration and wiring tests for Task 9: 5-Tab Shell, CoursesCoordinator, Shortcuts, and Settings."""

from __future__ import annotations

import asyncio
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import flet as ft
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import config
from core.courses.archive_service import ArchiveService
from core.courses.cache_store import CourseCacheStore
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.models import (
    CourseAttachment,
    CourseModuleItem,
    CourseSummary,
)
from core.courses.preview_cache import PreviewFileCache
from gui.app_controller import AppController
from gui.components.courses.adaptive_nav import (
    NAV_DESTINATIONS,
    RAIL_BREAKPOINT,
    RAIL_WIDTH,
    AdaptiveNavShell,
)
from gui.components.settings_view import SettingsView
from gui.controllers.courses_coordinator import (
    CoursesCoordinator,
    _try_build_embedded_webview,
)
from gui.core.theme import C, apply_theme
from gui.view_manager import ViewManager


class _FakeControl:
    def __init__(self, *, visible: bool = False):
        self.visible = visible
        self.opacity = 1.0 if visible else 0.0
        self.offset = None
        self.icon_color = None
        self.content = None
        self.controls = []
        self.back_calls = 0
        self.back_result = True
        self.theme_updates = 0

    def hide(self):
        self.visible = False

    def show(self, *_args):
        self.visible = True

    def show_loading(self, *_args):
        self.visible = True

    def cancel_pending_load(self):
        pass

    def update_theme(self):
        self.theme_updates += 1

    async def load_current_settings(self):
        return None

    def handle_back(self):
        self.back_calls += 1
        return self.back_result


class _FakePage:
    def __init__(self, width: float = 1120):
        self.width = width
        self.update_calls = 0
        self.dialogs = []
        self.overlay = []
        self.scheduled = []
        self.launched_urls = []

    def update(self):
        self.update_calls += 1

    def run_task(self, coro_fn, *args, **kwargs):
        self.scheduled.append((coro_fn, args, kwargs))
        return object()

    def show_dialog(self, dlg):
        self.dialogs.append(dlg)

    async def launch_url(self, url: str):
        self.launched_urls.append(url)


class _FakeController:
    def __init__(self, *, with_courses_btn: bool = True):
        self.calendar_btn = SimpleNamespace(icon_color=None)
        self.grades_btn = SimpleNamespace(icon_color=None)
        if with_courses_btn:
            self.courses_btn = SimpleNamespace(icon_color=None)

    def _dismiss_desktop_filter_menu(self):
        pass


def _make_course() -> CourseSummary:
    return CourseSummary(
        id=42,
        source="courses",
        fullname="Lập trình Web",
        shortname="LTW",
        display_name="Lập trình Web",
        course_code="0101001",
    )


def _make_mod(
    cmid: int = 10,
    *,
    web_url: str = "https://courses.ut.edu.vn/mod/quiz/view.php?id=10",
) -> CourseModuleItem:
    return CourseModuleItem(
        cmid=cmid,
        instance_id=1,
        modname="resource",
        name="Tài liệu tuần 1",
        section_id=1,
        web_url=web_url,
    )


# ---------------------------------------------------------------------------
# 1. ViewManager
# ---------------------------------------------------------------------------


def test_view_manager_switches_to_courses_and_hides_others():
    page = _FakePage()
    dash = _FakeControl(visible=True)
    detail = _FakeControl()
    settings_v = _FakeControl()
    cal = _FakeControl()
    grades = _FakeControl()
    courses = _FakeControl()
    switcher = _FakeControl()
    detail_switcher = _FakeControl()
    ctrl = _FakeController(with_courses_btn=True)
    destinations: list[str] = []

    vm = ViewManager(
        page,
        dash,
        detail,
        settings_v,
        cal,
        grades,
        ctrl,
        courses_view=courses,
        on_destination_change=destinations.append,
        tab_switcher=switcher,
        detail_switcher=detail_switcher,
    )

    vm.show_courses()

    assert courses.visible is True
    assert courses.opacity == 1.0
    assert courses.offset == ft.Offset(0, 0)
    assert dash.visible is False
    assert cal.visible is False
    assert detail.visible is False
    assert settings_v.visible is False
    assert grades.visible is False
    assert switcher.content is courses
    assert ctrl.courses_btn.icon_color == C.ACCENT
    assert ctrl.calendar_btn.icon_color == C.TEXT_SECONDARY
    assert ctrl.grades_btn.icon_color == C.TEXT_SECONDARY
    assert destinations[-1] == "courses"

    # Switching to other tabs hides courses_view and notifies destination
    vm.show_calendar([])
    assert courses.visible is False
    assert destinations[-1] == "calendar"

    asyncio.run(vm.close_calendar())
    assert courses.visible is False
    assert destinations[-1] == "home"

    vm.show_grades_loading()
    assert courses.visible is False
    assert destinations[-1] == "grades"
    assert grades.theme_updates == 1

    asyncio.run(vm.close_grades())
    assert destinations[-1] == "home"

    asyncio.run(vm.show_settings())
    assert courses.visible is False
    assert destinations[-1] == "settings"

    asyncio.run(vm.close_settings())
    assert destinations[-1] == "home"


def test_view_manager_keeps_courses_alive_behind_detail_switcher():
    page = _FakePage()
    dash = _FakeControl()
    detail = _FakeControl()
    settings_v = _FakeControl()
    cal = _FakeControl()
    grades = _FakeControl()
    courses = _FakeControl(visible=True)
    switcher = _FakeControl()
    switcher.content = courses
    empty_detail = _FakeControl()
    detail_switcher = _FakeControl()
    detail_switcher.content = empty_detail
    ctrl = _FakeController(with_courses_btn=False)

    vm = ViewManager(
        page,
        dash,
        detail,
        settings_v,
        cal,
        grades,
        ctrl,
        courses_view=courses,
        tab_switcher=switcher,
        detail_switcher=detail_switcher,
        empty_detail=empty_detail,
    )

    vm.show_detail_loading({"id": "assign-1"})
    assert courses.visible is True
    assert dash.visible is False
    assert switcher.content is courses
    assert detail_switcher.content is detail
    assert detail.visible is True

    asyncio.run(vm.close_detail(detail_from_calendar=False))
    assert courses.visible is True
    assert dash.visible is False
    assert detail.visible is False
    assert detail_switcher.content is empty_detail


# ---------------------------------------------------------------------------
# 2. AdaptiveNavShell
# ---------------------------------------------------------------------------


def test_adaptive_nav_shell_switches_bar_and_rail_and_handles_theme():
    seen: list[str] = []
    nav = AdaptiveNavShell(on_navigate=seen.append)

    assert len(NAV_DESTINATIONS) == 5
    assert [d.key for d in NAV_DESTINATIONS] == [
        "home",
        "courses",
        "calendar",
        "grades",
        "settings",
    ]
    assert RAIL_BREAKPOINT == 600
    assert RAIL_WIDTH == 88

    assert nav.apply_width(390) is False
    assert nav.rail_visible is False
    assert nav.rail_host.visible is False
    assert nav.bar.visible is True

    assert nav.apply_width(600) is True
    assert nav.rail_visible is True
    assert nav.rail_host.visible is True
    assert nav.bar.visible is False

    assert nav.apply_width(1120) is True
    assert nav.rail_visible is True

    # Invalid / non-finite / None widths guard safely
    assert nav.apply_width(None) is False  # type: ignore[arg-type]
    assert nav.apply_width(float("nan")) is False
    assert nav.apply_width(float("inf")) is False

    nav.set_active("courses")
    assert nav.rail.selected_index == 1
    assert nav.bar.selected_index == 1

    # Unknown key is ignored without resetting selection
    nav.set_active("nonexistent")
    assert nav.rail.selected_index == 1
    assert nav.bar.selected_index == 1

    # _handle_change extracts control.selected_index (including 0) and e.data fallback
    nav._handle_change(SimpleNamespace(control=SimpleNamespace(selected_index=0), data="1"))
    nav._handle_change(SimpleNamespace(control=SimpleNamespace(selected_index=None), data="2"))
    nav._handle_change(SimpleNamespace(control=SimpleNamespace(selected_index=99), data=None))
    nav._handle_change(SimpleNamespace(control=None, data="bad"))
    assert seen == ["home", "calendar"]

    original_theme = config.settings.THEME
    try:
        apply_theme("steel_light")
        nav.refresh_theme()
        assert nav.rail.bgcolor == C.SURFACE
        assert nav.rail_host.bgcolor == C.SURFACE
        assert nav.bar.bgcolor == C.SURFACE
        assert nav.rail.selected_label_text_style.color == C.ACCENT
        assert nav.rail.unselected_label_text_style.color == C.TEXT_SECONDARY
    finally:
        apply_theme(original_theme)


# ---------------------------------------------------------------------------
# 3. CoursesCoordinator
# ---------------------------------------------------------------------------


def _build_coordinator(tmp_path: Path, catalog=None, page=None):
    pcache = PreviewFileCache(tmp_path / "preview")
    if catalog is None:
        catalog = SimpleNamespace(
            fetch_attachment_bytes=lambda _src, _url: b"hello world",
            record_recent=lambda _entry: None,
        )
    server = LocalPreviewServer()
    launched: list[str] = []
    assigned: list[CourseModuleItem] = []
    coord = CoursesCoordinator(
        page=page or _FakePage(),
        catalog_service=catalog,  # type: ignore[arg-type]
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=server,
        on_open_assignment_fallback=assigned.append,
        on_launch_url=launched.append,
    )
    return coord, server, pcache, launched, assigned


def test_courses_coordinator_routes_document_and_archive_overlays(tmp_path: Path):
    z_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(z_path, "w") as zf:
        zf.writestr("readme.md", "# Hello")
    zip_bytes = z_path.read_bytes()

    recents = []
    catalog = SimpleNamespace(
        fetch_attachment_bytes=lambda _src, url: zip_bytes if url.endswith(".zip") else b"plain text",
        record_recent=lambda entry: recents.append(entry),
    )
    coord, server, _pcache, _launched, _assigned = _build_coordinator(tmp_path, catalog=catalog)
    try:
        course = _make_course()
        mod = _make_mod(cmid=15)
        att_zip = CourseAttachment(
            file_name="bundle.zip",
            file_url="https://courses.ut.edu.vn/bundle.zip",
            file_size=len(zip_bytes),
            extension=".zip",
            preview_kind="archive",
            is_archive=True,
        )
        coord.open_attachment_bytes_in_overlay(course, mod, att_zip, zip_bytes)
        assert len(coord.courses_view._overlays) == 1
        assert coord.courses_view._overlays[-1].data == "archive:15:bundle.zip"
        assert coord.courses_view.handle_back() is True

        att_txt = CourseAttachment(
            file_name="notes.txt",
            file_url="https://courses.ut.edu.vn/notes.txt",
            file_size=10,
            extension=".txt",
            preview_kind="text",
            is_archive=False,
        )
        coord.open_attachment_bytes_in_overlay(course, mod, att_txt, b"plain text")
        assert len(coord.courses_view._overlays) == 1
        assert coord.courses_view._overlays[-1].data == "reader:notes.txt"
        assert coord.courses_view.handle_back() is True
        assert len(recents) == 2
    finally:
        coord.close()


def test_courses_coordinator_cache_hit_miss_and_put_error_resilience(tmp_path: Path):
    fetch_calls = []

    class _Catalog:
        def fetch_attachment_bytes(self, source: str, url: str) -> bytes:
            fetch_calls.append((source, url))
            return b"fresh-bytes"

        def record_recent(self, _entry):
            pass

    coord, _server, pcache, _launched, _assigned = _build_coordinator(tmp_path, catalog=_Catalog())
    try:
        att = CourseAttachment(
            file_name="slide.txt",
            file_url="https://courses.ut.edu.vn/slide.txt",
            time_modified=12345,
            preview_kind="text",
        )
        # 1. Cache miss downloads and stores
        data1 = asyncio.run(coord._fetch_attachment_bytes_async("courses", att))
        assert data1 == b"fresh-bytes"
        assert len(fetch_calls) == 1

        # 2. Cache hit avoids download
        data2 = asyncio.run(coord._fetch_attachment_bytes_async("courses", att))
        assert data2 == b"fresh-bytes"
        assert len(fetch_calls) == 1

        # 3. Disk OSError during put_bytes never aborts download
        att2 = CourseAttachment(
            file_name="slide2.txt",
            file_url="https://courses.ut.edu.vn/slide2.txt",
            time_modified=999,
            preview_kind="text",
        )
        pcache.put_bytes = Mock(side_effect=OSError("disk full"))
        data3 = asyncio.run(coord._fetch_attachment_bytes_async("thnn", att2))
        assert data3 == b"fresh-bytes"
        assert fetch_calls[-1] == ("thnn", "https://courses.ut.edu.vn/slide2.txt")
    finally:
        coord.close()


def test_courses_coordinator_save_bytes_only_notifies_when_saved(tmp_path: Path, monkeypatch):
    page = _FakePage()
    coord, _server, _pcache, _launched, _assigned = _build_coordinator(tmp_path, page=page)
    try:
        save_mock = AsyncMock(return_value=None)
        monkeypatch.setattr(ft.FilePicker, "save_file", save_mock)

        # Cancelled save must NOT show "Đã lưu"
        asyncio.run(coord._save_bytes_async("notes.pdf", b"pdf-bytes"))
        assert page.dialogs == []

        # Confirmed save shows "Đã lưu: notes.pdf"
        save_mock.return_value = str(tmp_path / "notes.pdf")
        asyncio.run(coord._save_bytes_async("notes.pdf", b"pdf-bytes"))
        assert len(page.dialogs) == 1
        assert "Đã lưu: notes.pdf" in page.dialogs[0].content.value

        # Exception handled cleanly via snack
        save_mock.side_effect = RuntimeError("picker failed")
        asyncio.run(coord._save_bytes_async("notes.pdf", b"pdf-bytes"))
        assert len(page.dialogs) == 2
        assert "Không thể lưu" in page.dialogs[1].content.value
    finally:
        coord.close()


def test_courses_coordinator_open_external_rejects_executables_and_launches_safe_files(
    tmp_path: Path, monkeypatch
):
    coord, _server, pcache, _launched, _assigned = _build_coordinator(tmp_path)
    try:
        launch_mock = AsyncMock()
        monkeypatch.setattr(ft.UrlLauncher, "launch_url", launch_mock)
        saved_calls = []

        async def fake_save(name: str, data: bytes):
            saved_calls.append((name, data))

        monkeypatch.setattr(coord, "_save_bytes_async", fake_save)

        for dangerous in ("setup.exe", "run.BAT", "script.ps1", "bad.cmd", "evil.vbs", "inst.msi"):
            asyncio.run(coord._open_external_async(dangerous, b"MZ"))
        assert launch_mock.await_count == 0
        assert [name for name, _ in saved_calls] == [
            "setup.exe",
            "run.BAT",
            "script.ps1",
            "bad.cmd",
            "evil.vbs",
            "inst.msi",
        ]

        # Safe file writes uniquely under preview_cache.cache_dir / "external" and launches URI
        asyncio.run(coord._open_external_async("lecture.pdf", b"%PDF-1.4"))
        assert launch_mock.await_count == 1
        launched_uri = launch_mock.await_args.args[0]
        assert launched_uri.startswith("file:///")
        assert launched_uri.endswith(".pdf")
        ext_files = list((pcache.cache_dir / "external").iterdir())
        assert len(ext_files) == 1
        assert ext_files[0].read_bytes() == b"%PDF-1.4"

        # Launch error falls back to save_bytes
        launch_mock.side_effect = RuntimeError("no handler")
        asyncio.run(coord._open_external_async("doc.docx", b"PK"))
        assert saved_calls[-1] == ("doc.docx", b"PK")
    finally:
        coord.close()


def test_courses_coordinator_webview_activity_validates_url_and_supports_theme(
    tmp_path: Path, monkeypatch
):
    coord, _server, _pcache, launched, _assigned = _build_coordinator(tmp_path)
    try:
        # 1. Unsafe URL is rejected completely
        bad_mod = _make_mod(cmid=99, web_url="javascript:alert(1)")
        coord._open_webview_activity(bad_mod)
        assert launched == []
        assert coord.courses_view._overlays == []

        # 2. An unavailable WebView keeps recovery in-app until an explicit action.
        assert _try_build_embedded_webview("javascript:alert(1)") is None
        good_mod = _make_mod(cmid=55, web_url="https://courses.ut.edu.vn/mod/quiz/view.php?id=55")
        monkeypatch.setattr(
            "gui.components.courses.webview_pane.build_embedded_webview_host",
            lambda *_args, **_kwargs: None,
        )
        coord._open_webview_activity(good_mod)
        assert launched == []
        overlay = coord.courses_view._overlays[-1]
        overlay._ext_btn.on_click(None)
        assert launched == [good_mod.web_url]
        coord.courses_view.pop_overlay()

        # 3. Embedded webview pushes overlay with tag & update_theme support
        coord._open_webview_activity(good_mod)
        assert len(coord.courses_view._overlays) == 1
        overlay = coord.courses_view._overlays[0]
        assert overlay.data == "webview:55"
        assert callable(getattr(overlay, "update_theme", None))
        coord.update_theme()
    finally:
        coord.close()


def test_courses_coordinator_ensure_loaded_retries_after_exception(tmp_path: Path):
    coord, server, _pcache, _launched, _assigned = _build_coordinator(tmp_path)
    try:
        calls = 0

        async def failing_then_ok():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("boom")

        coord.courses_view.load_initial_async = failing_then_ok  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            asyncio.run(coord.ensure_loaded_async(background_sync_hours=12))
        assert coord._initial_loaded is False

        asyncio.run(coord.ensure_loaded_async(background_sync_hours=12))
        assert coord._initial_loaded is True
        assert coord.courses_view.CATALOG_STALE_HOURS == 12

        # Revisit does not reload
        asyncio.run(coord.ensure_loaded_async(background_sync_hours=12))
        assert calls == 2
    finally:
        coord.close()
        assert server.port == 0


def test_courses_coordinator_async_attachment_errors_show_snack_without_overlay_bloat(
    tmp_path: Path,
):
    class _FailingCatalog:
        def fetch_attachment_bytes(self, _source: str, _url: str) -> bytes:
            raise RuntimeError("network down")

        def record_recent(self, _entry):
            pass

    page_no_dlg = SimpleNamespace(
        width=1120,
        overlay=[ft.SnackBar(ft.Text("old"))],
        update=lambda: None,
    )
    coord, _server, _pcache, _launched, _assigned = _build_coordinator(
        tmp_path, catalog=_FailingCatalog(), page=page_no_dlg
    )
    try:
        course = _make_course()
        mod = _make_mod()
        att = CourseAttachment(
            file_name="broken.pdf",
            file_url="https://courses.ut.edu.vn/broken.pdf",
            preview_kind="pdf",
        )
        asyncio.run(coord.open_attachment_async(course, mod, att))
        asyncio.run(coord.save_attachment_async(att, source="thnn"))
        # Old SnackBar is pruned; only 1 SnackBar remains in overlay
        snacks = [item for item in page_no_dlg.overlay if isinstance(item, ft.SnackBar)]
        assert len(snacks) == 1
        assert "Lỗi tải file" in snacks[0].content.value
    finally:
        coord.close()


# ---------------------------------------------------------------------------
# 4. AppController wiring
# ---------------------------------------------------------------------------


def test_app_controller_navigate_back_priority_and_keyboard_shortcuts():
    async def scenario():
        ctrl = AppController.__new__(AppController)
        calls = []
        ctrl.courses_view = SimpleNamespace(
            visible=True,
            handle_back=Mock(return_value=True),
            focus_search=AsyncMock(),
        )
        ctrl.settings_view = SimpleNamespace(visible=True, _handle_back=AsyncMock())
        ctrl.detail_view = SimpleNamespace(visible=True)
        ctrl.calendar_view = SimpleNamespace(visible=True)
        ctrl.grade_overview_view = SimpleNamespace(visible=True)
        ctrl._close_detail = AsyncMock()
        ctrl._close_calendar = AsyncMock()
        ctrl._close_grades = AsyncMock()

        # Priority 1: courses_view.handle_back() before settings/detail/calendar/grades
        assert await ctrl._navigate_back() is True
        ctrl.courses_view.handle_back.assert_called_once()
        ctrl.settings_view._handle_back.assert_not_awaited()
        ctrl._close_detail.assert_not_awaited()

        # When courses_view.handle_back() returns False, falls through to settings -> detail -> calendar -> grades
        ctrl.courses_view.handle_back.return_value = False
        assert await ctrl._navigate_back() is True
        ctrl.settings_view._handle_back.assert_awaited_once()

        ctrl.settings_view.visible = False
        assert await ctrl._navigate_back() is True
        ctrl._close_detail.assert_awaited_once()

        # When DetailView is stacked directly on top of courses_view via ViewManager,
        # _navigate_back closes DetailView first without mutating courses_view behind it.
        ctrl.courses_view.visible = True
        ctrl.courses_view.handle_back.reset_mock()
        ctrl.courses_view.handle_back.return_value = True
        ctrl.detail_view.visible = True
        ctrl.view_manager = SimpleNamespace(_detail_origin=ctrl.courses_view)
        ctrl._close_detail.reset_mock()
        assert await ctrl._navigate_back() is True
        ctrl._close_detail.assert_awaited_once()
        ctrl.courses_view.handle_back.assert_not_called()

        # Keyboard shortcuts: Ctrl+K and / focus search when courses_view visible & detail closed
        ctrl.detail_view.visible = False
        await ctrl._on_keyboard_event(SimpleNamespace(key="k", ctrl=True, meta=False, alt=False))
        await ctrl._on_keyboard_event(SimpleNamespace(key="/", ctrl=False, meta=False, alt=False))
        assert ctrl.courses_view.focus_search.await_count == 2

        # Does not focus search when courses_view hidden
        ctrl.courses_view.visible = False
        await ctrl._on_keyboard_event(SimpleNamespace(key="K", ctrl=True, meta=False, alt=False))
        assert ctrl.courses_view.focus_search.await_count == 2

        # Escape calls _navigate_back
        ctrl._navigate_back = AsyncMock(side_effect=lambda: calls.append("back") or True)
        await ctrl._on_keyboard_event(SimpleNamespace(key="Escape", ctrl=False, meta=False, alt=False))
        assert calls == ["back"]

    asyncio.run(scenario())


def test_app_controller_resize_filter_offset_theme_navigation_and_disconnect():
    ctrl = AppController.__new__(AppController)
    updates = []
    ctrl.page = SimpleNamespace(
        width=1120,
        window=SimpleNamespace(width=1120, maximized=False),
        update=lambda: updates.append("update"),
    )
    ctrl.courses_view = SimpleNamespace(
        set_width=Mock(),
        update_theme=Mock(),
        visible=False,
    )
    ctrl._nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)

    # Resize wide -> rail visible, courses width reduced by RAIL_WIDTH
    ctrl._on_page_resize(SimpleNamespace(width=1120))
    assert ctrl._nav_shell.rail_visible is True
    ctrl.courses_view.set_width.assert_called_with(1120 - RAIL_WIDTH)

    # Resize narrow -> bar visible, courses width full
    ctrl._on_page_resize(SimpleNamespace(width=390))
    assert ctrl._nav_shell.rail_visible is False
    ctrl.courses_view.set_width.assert_called_with(390)

    # Desktop filter menu offsets by RAIL_WIDTH when rail is visible
    ctrl._window_header = ft.Container(height=34)
    ctrl._desktop_filter_popup_host = ft.Container(
        visible=True,
        opacity=0,
        scale=0.96,
        offset=ft.Offset(0, -0.025),
        ignore_interactions=True,
    )
    ctrl._desktop_filter_top_dismiss_layer = ft.Container(visible=False)
    ctrl._desktop_filter_upper_body_dismiss_layer = ft.Container(visible=False)
    ctrl._desktop_filter_dismiss_layer = ft.Container(visible=False)
    ctrl._active_desktop_filter_menu = None
    ctrl._safe_run_task = lambda *_a, **_kw: None
    menu = SimpleNamespace(
        trigger=ft.Container(height=40),
        popup_width=224,
        popup_panel=ft.Container(),
        _set_expanded=lambda _v: None,
    )
    event = SimpleNamespace(
        global_position=ft.Offset(120, 160),
        local_position=ft.Offset(10, 10),
    )
    ctrl._on_page_resize(SimpleNamespace(width=1120))
    ctrl._toggle_desktop_filter_menu(menu, event)
    # global x(120) - local x(10) - RAIL_WIDTH(88) = 22.0
    assert ctrl._desktop_filter_popup_host.left == pytest.approx(22.0)

    # Toggle maximize flips window.maximized
    ctrl._toggle_maximize_window()
    assert ctrl.page.window.maximized is True
    ctrl._toggle_maximize_window()
    assert ctrl.page.window.maximized is False

    # _navigate_destination routes cleanly
    diag_screens = []
    ctrl._diag_screen = diag_screens.append
    ctrl._diag_breadcrumb = lambda *_a, **_kw: None
    ctrl.view_manager = SimpleNamespace(
        show_dashboard=Mock(),
        show_courses=Mock(),
        cancel_pending_settings_navigation=Mock(),
    )
    ctrl._courses_coordinator = SimpleNamespace(
        ensure_loaded_async=AsyncMock(),
        update_theme=Mock(),
        close=Mock(),
    )
    ctrl._show_calendar = AsyncMock()
    ctrl._show_grades = AsyncMock()
    ctrl._show_settings = AsyncMock()

    async def nav_flow():
        await ctrl._navigate_destination("home")
        await ctrl._navigate_destination("courses")
        await ctrl._navigate_destination("calendar")
        await ctrl._navigate_destination("grades")
        await ctrl._navigate_destination("settings")

    asyncio.run(nav_flow())
    ctrl.view_manager.show_dashboard.assert_called_once()
    ctrl.view_manager.show_courses.assert_called_once()
    ctrl._courses_coordinator.ensure_loaded_async.assert_awaited_once()
    ctrl._show_calendar.assert_awaited_once()
    ctrl._show_grades.assert_awaited_once()
    ctrl._show_settings.assert_awaited_once()
    assert diag_screens == ["home", "courses"]

    # _on_disconnect closes courses coordinator safely
    ctrl.activation_broker = None
    ctrl.tray = None
    ctrl._page_alive = SimpleNamespace(clear=lambda: None)
    ctrl._prefetch_cancel_event = SimpleNamespace(set=lambda: None)
    ctrl._card_detail_warm_cancel_event = None
    ctrl._sync_coordinator = None
    ctrl._today_schedule_coordinator = None
    ctrl._update_coordinator = None
    ctrl.orchestrator = SimpleNamespace(client=SimpleNamespace(close=lambda: None))
    ctrl._on_disconnect(None)
    ctrl._courses_coordinator.close.assert_called_once()


def test_app_controller_rebuild_colors_and_open_course_assignment():
    from gui.core.theme import semantic_palette

    ctrl = AppController.__new__(AppController)
    ctrl.page = SimpleNamespace(bgcolor=None, update=Mock())
    ctrl._rendered_palette = semantic_palette()
    ctrl.dashboard = ft.Container()
    ctrl._header_container = ft.Container()
    ctrl._app_title = ft.Text("UTHelper")
    ctrl._version_text = ft.Text("v1.0.0")
    ctrl._version_badge = ft.Container()
    ctrl.status_text = ft.Text()
    ctrl._filter_container = ft.Container()
    ctrl.search_field = ft.TextField()
    ctrl.urgency_filter_menu = SimpleNamespace(options=[], refresh_theme=Mock())
    ctrl.type_filter_menu = SimpleNamespace(options=[], refresh_theme=Mock())
    ctrl.course_filter_menu = SimpleNamespace(options=[], refresh_theme=Mock())
    ctrl._footer = ft.Container()
    ctrl.footer_critical = ft.Text()
    ctrl.footer_warning = ft.Text()
    ctrl.footer_safe = ft.Text()
    ctrl.footer_overdue = ft.Text()
    ctrl._content_area = ft.Container()
    ctrl.calendar_btn = ft.IconButton(ft.Icons.CALENDAR_MONTH_ROUNDED)
    ctrl.grades_btn = ft.IconButton(ft.Icons.INSIGHTS_ROUNDED)
    ctrl.courses_btn = ft.IconButton(ft.Icons.MENU_BOOK_ROUNDED)
    ctrl.refresh_btn = ft.IconButton(ft.Icons.REFRESH_ROUNDED)
    ctrl.settings_btn = ft.IconButton(ft.Icons.SETTINGS_ROUNDED)
    ctrl._sync_status = SimpleNamespace(refresh_theme=Mock())
    ctrl._update_banner = ft.Container()
    ctrl._update_icon = ft.Icon(ft.Icons.SYSTEM_UPDATE_ROUNDED)
    ctrl._update_text = ft.Text()
    ctrl._update_progress = ft.ProgressBar()
    ctrl._update_btn = ft.TextButton("Cập nhật")
    ctrl.settings_view = ft.Container()
    ctrl._nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    ctrl._nav_shell.refresh_theme = Mock(wraps=ctrl._nav_shell.refresh_theme)
    ctrl.courses_view = SimpleNamespace(
        update_theme=Mock(),
        _state=SimpleNamespace(selected_course=lambda: _make_course()),
    )
    ctrl._courses_coordinator = SimpleNamespace(update_theme=Mock())

    original_theme = config.settings.THEME
    try:
        apply_theme("steel_light")
        ctrl._rebuild_colors()
        assert ctrl.courses_btn.icon_color == C.TEXT_SECONDARY
        ctrl._nav_shell.refresh_theme.assert_called_once()
        ctrl.courses_view.update_theme.assert_called_once()
        ctrl._courses_coordinator.update_theme.assert_called_once()
    finally:
        apply_theme(original_theme)

    # _open_course_assignment builds detail payload from module & selected course
    import threading

    ctrl._data_lock = threading.Lock()
    ctrl.all_data = []
    shown = []
    ctrl._show_detail = shown.append
    assign_mod = CourseModuleItem(
        cmid=77,
        instance_id=12,
        modname="assign",
        name="Bài tập lớn",
        section_id=2,
        web_url="https://courses.ut.edu.vn/mod/assign/view.php?id=77",
        description_html="<p>Nộp PDF</p>",
    )
    ctrl._open_course_assignment(assign_mod)
    assert len(shown) == 1
    assert shown[0]["title"] == "Bài tập lớn"
    assert shown[0]["course_id"] == 42
    assert shown[0]["cmid"] == 77
    assert shown[0]["type"] == "assignment"
    assert shown[0]["site_identity"] == "courses"


# ---------------------------------------------------------------------------
# 5. SettingsView cache stats & clear
# ---------------------------------------------------------------------------


def test_settings_view_cache_stats_and_clear_handles_courses_and_preview_caches(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)

    pcache = PreviewFileCache(tmp_path / "preview_cache")
    pcache.put_bytes("https://courses.ut.edu.vn/a.pdf", 1, "a.pdf", b"123456")
    ext_dir = tmp_path / "preview_cache" / "external"
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "open.pdf").write_bytes(b"1234")

    store = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|u1")
    store.save_catalog([_make_course()], synced_at=100)
    store.save_course_detail("courses", 42, [], [], synced_at=100)
    (tmp_path / "courses_cache_legacy.json").write_text("{}", encoding="utf-8")
    (tmp_path / "courses_catalog_deadbeef.lock").write_text("", encoding="utf-8")
    (tmp_path / "course_tree_courses_1_deadbeef.lock").write_text("", encoding="utf-8")

    view = SimpleNamespace(
        _orchestrator=SimpleNamespace(get_cached_details_snapshot=lambda: {}),
        _debug_cache_stats=ft.Text(),
        _debug_info_text=ft.Text(),
    )

    SettingsView._do_show_cache_stats(view)
    stats_text = view._debug_cache_stats.value or ""
    assert "Cache xem trước tài liệu:" in stats_text
    assert "Cache môn học:" in stats_text
    assert "Cache xem trước tài liệu: trống" not in stats_text
    assert "Cache môn học: trống" not in stats_text

    SettingsView._do_clear_data_cache(view)
    assert "Cache offline đã xóa" in (view._debug_info_text.value or "")
    assert list(tmp_path.glob("courses_catalog_*")) == []
    assert list(tmp_path.glob("course_tree_*")) == []
    assert list(tmp_path.glob("courses_cache_*")) == []
    assert [p for p in (tmp_path / "preview_cache").rglob("*") if p.is_file()] == []

    SettingsView._do_show_cache_stats(view)
    cleared_stats = view._debug_cache_stats.value or ""
    assert "Cache xem trước tài liệu: trống" in cleared_stats
    assert "Cache môn học: trống" in cleared_stats


# ---------------------------------------------------------------------------
# 6. M3B Integration Wiring: Courses <-> Quiz & GradeOverview <-> course_grades_view
# ---------------------------------------------------------------------------


def test_grade_overview_open_courses_routes_to_course_grades_view_preserving_grades_rail():
    async def _run() -> None:
        from gui.components.grade_overview_view import GradeOverviewView

        opened_courses = Mock()
        grade_view = GradeOverviewView(on_open_courses=opened_courses)
        courses_btn = grade_view._courses_btn
        assert courses_btn is not None
        assert courses_btn.height == 44
        courses_btn.on_click(None)
        opened_courses.assert_called_once()

        destinations: list[str] = []
        page = _FakePage(width=1200)
        dash = _FakeControl(visible=False)
        detail = _FakeControl(visible=False)
        settings_v = _FakeControl(visible=False)
        cal = _FakeControl(visible=False)
        grade_overview = _FakeControl(visible=True)
        grade_overview.show_loading = Mock()
        courses = _FakeControl(visible=False)
        course_grades_view = _FakeControl(visible=False)
        course_grades_view.show_loading = Mock()
        course_grades_view.show_courses = Mock()
        fake_ctrl = _FakeController()

        vm = ViewManager(
            page,
            dash,
            detail,
            settings_v,
            cal,
            grade_overview,
            fake_ctrl,
            courses_view=courses,
            course_grades_view=course_grades_view,
            on_destination_change=destinations.append,
        )
        vm.show_grades_loading()
        assert destinations[-1] == "grades"

        vm.show_course_grades_loading()
        assert course_grades_view.visible is True
        assert grade_overview.visible is False
        assert destinations[-1] == "grades"
        assert fake_ctrl.grades_btn.icon_color == C.ACCENT
        assert fake_ctrl.courses_btn.icon_color == C.TEXT_SECONDARY

        await vm.close_course_grades()
        assert course_grades_view.visible is False
        assert grade_overview.visible is True
        assert destinations[-1] == "grades"

    asyncio.run(_run())


def test_courses_quiz_launch_and_close_wire_immersive_shell_and_cleanup():
    async def _run() -> None:
        ctrl = AppController.__new__(AppController)
        ctrl.page = SimpleNamespace(
            width=412,
            height=880,
            web=False,
            platform=ft.PagePlatform.ANDROID,
            update=Mock(),
            show_dialog=Mock(),
            pop_dialog=Mock(),
        )
        ctrl._mobile_back_enabled = True
        ctrl._nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)
        ctrl._nav_shell.apply_width(412)
        ctrl._quiz_mode_window_state = None
        ctrl._window_maximized = False
        ctrl._data_lock = Mock()
        ctrl._data_lock.__enter__ = lambda s: None
        ctrl._data_lock.__exit__ = lambda s, *a: None
        ctrl.all_data = []
        ctrl.orchestrator = SimpleNamespace(invalidate_detail_cache=Mock())

        reloaded: list[bool] = []
        notice_calls: list[str] = []
        quiz_mod = CourseModuleItem(
            cmid=11,
            instance_id=99,
            modname="quiz",
            name="Midterm Quiz",
            section_id=1,
            web_url="https://courses.ut.edu.vn/mod/quiz/view.php?id=11",
        )
        fake_quiz_overlay = SimpleNamespace(
            _module=quiz_mod,
            reload=lambda: reloaded.append(True),
            set_runtime_notice=lambda msg: notice_calls.append(msg),
        )
        ctrl.courses_view = SimpleNamespace(
            visible=True,
            _overlays=[fake_quiz_overlay],
            _compute_immersive=lambda: False,
            set_immersive=Mock(),
            handle_back=lambda: False,
        )
        ctrl._courses_coordinator = SimpleNamespace(reset_for_account_switch=Mock())
        ctrl.quiz_player_view = SimpleNamespace(
            visible=False,
            clear_persisted_sessions=Mock(),
            request_close=AsyncMock(return_value=True),
            prepare_activity=Mock(return_value=("target", "ready")),
            open_activity_for_account=Mock(return_value=True),
            handle_back=lambda: False,
        )

        course = _make_course()
        activity = ctrl._build_course_quiz_activity(course, quiz_mod)
        ctrl._start_course_quiz(course, quiz_mod)
        ctrl.quiz_player_view.prepare_activity.assert_called_once()
        ctrl.quiz_player_view.open_activity_for_account.assert_called_once()
        assert ctrl._nav_shell._immersive is True

        # Simulate SEB / unavailable runtime notice
        await ctrl._on_course_quiz_entry_unavailable(activity, "requires_safe_exam_browser")
        assert any("Safe Exam Browser" in msg for msg in notice_calls)
        assert ctrl._nav_shell._immersive is False

        # Re-enter quiz immersive, then close quiz player -> refreshes overlay & clears immersive
        ctrl._sync_quiz_immersive(True)
        assert ctrl._nav_shell._immersive is True
        ctrl.quiz_player_view.visible = False
        await ctrl._on_quiz_player_closed(activity)
        ctrl.orchestrator.invalidate_detail_cache.assert_called_once_with(activity["url"])
        assert reloaded == [True]
        assert ctrl._nav_shell._immersive is False

        # Exit/nav request_close & account switch cleanup
        assert await ctrl.quiz_player_view.request_close() is True
        ctrl.quiz_player_view.clear_persisted_sessions()
        ctrl._courses_coordinator.reset_for_account_switch()
        ctrl.quiz_player_view.clear_persisted_sessions.assert_called_once()
        ctrl._courses_coordinator.reset_for_account_switch.assert_called_once()

    asyncio.run(_run())


def test_start_course_quiz_missing_account_reauth_callback_succeeds(monkeypatch):
    import gui.components.login_dialog as login_dialog_mod

    async def _run() -> None:
        scheduled_tasks: list[asyncio.Task] = []
        ctrl = AppController.__new__(AppController)
        ctrl.page = SimpleNamespace(
            width=1120,
            height=800,
            web=False,
            platform=ft.PagePlatform.WINDOWS,
            session=SimpleNamespace(connection=object()),
            update=Mock(),
            run_task=lambda fn, *a, **kw: scheduled_tasks.append(asyncio.create_task(fn(*a, **kw))) or scheduled_tasks[-1],
        )
        ctrl._nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)
        ctrl._quiz_mode_window_state = None
        ctrl._window_maximized = False
        ctrl._data_lock = Mock()
        ctrl._data_lock.__enter__ = lambda s: None
        ctrl._data_lock.__exit__ = lambda s, *a: None
        ctrl.all_data = []
        ctrl._settings_identity = ("", "")
        ctrl._portal_client = SimpleNamespace(clear_token=Mock())
        ctrl._portal_week_client = None
        ctrl.orchestrator = SimpleNamespace(
            moodle_service=SimpleNamespace(clear_all_caches=Mock()),
            clear_detail_cache=Mock(),
            _userid_cache=None,
            client=SimpleNamespace(clear_session=Mock()),
            thnn_client=SimpleNamespace(clear_session=Mock()),
            course_grade_monitor=None,
        )
        ctrl._courses_coordinator = None
        ctrl._week_schedule_cache = None
        ctrl.calendar_view = None
        ctrl._today_schedule_coordinator = SimpleNamespace(
            replace_cache=Mock(),
            refresh=AsyncMock(),
        )
        ctrl._today_schedule_component = None
        ctrl._reconcile_class_schedule_notifications = AsyncMock()
        ctrl._sync_coordinator = None
        ctrl._diag_screen = Mock()
        ctrl._diag_breadcrumb = Mock()
        ctrl._load_data_async = AsyncMock()

        prepare_calls = [0]

        def _prepare(activity, account_id):
            prepare_calls[0] += 1
            if prepare_calls[0] == 1:
                return None, "missing_account"
            return "target", "ready"

        ctrl.quiz_player_view = SimpleNamespace(
            visible=False,
            prepare_activity=Mock(side_effect=_prepare),
            open_activity_for_account=Mock(return_value=True),
            request_app_auth=lambda cb: ctrl._show_quiz_auth_dialog(cb),
            clear_persisted_sessions=AsyncMock(),
        )

        async def _fake_show_login(_page, _orch, on_success, **_kw):
            monkeypatch.setattr(config.settings, "UTH_USERNAME", "sv001")
            monkeypatch.setattr(config.settings, "UTH_PASSWORD", "secret")
            await on_success()

        monkeypatch.setattr(login_dialog_mod, "show_login_dialog", _fake_show_login)
        monkeypatch.setattr(config.settings, "UTH_USERNAME", "")
        monkeypatch.setattr(config.settings, "UTH_PASSWORD", "")

        course = _make_course()
        quiz_mod = CourseModuleItem(
            cmid=11,
            instance_id=99,
            modname="quiz",
            name="Midterm Quiz",
            section_id=1,
            web_url="https://courses.ut.edu.vn/mod/quiz/view.php?id=11",
        )
        ctrl._start_course_quiz(course, quiz_mod)
        assert scheduled_tasks
        await asyncio.gather(*list(scheduled_tasks))

        assert prepare_calls[0] == 2
        ctrl.quiz_player_view.open_activity_for_account.assert_called_once()

        # _show_quiz_auth_dialog also supports synchronous callbacks defensively
        sync_called = []

        async def _fake_show_login_sync(_page, _orch, on_success, **_kw):
            await on_success()

        monkeypatch.setattr(login_dialog_mod, "show_login_dialog", _fake_show_login_sync)
        await ctrl._show_quiz_auth_dialog(lambda: sync_called.append(True))
        await asyncio.gather(*list(scheduled_tasks))
        assert sync_called == [True]

    asyncio.run(_run())
