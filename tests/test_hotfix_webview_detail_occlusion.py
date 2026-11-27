"""TDD regression tests for Hotfix 2.6.4: AppController & ViewManager WebView occlusion & lifecycle."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import flet as ft
import pytest

import gui.app_controller as app_controller_mod
from config import settings
from gui.app_controller import AppController
from gui.components.adaptive_filter_menu import AdaptiveFilterMenu, FilterMenuOption
from gui.components.courses.adaptive_nav import AdaptiveNavShell
from gui.components.courses.embedded_webview_host import (
    _ACTIVE_OCCLUSION_REASONS,
    _ACTIVE_OCCLUSION_TOKENS,
    clear_global_webview_occlusion,
    set_global_webview_occlusion,
)
from gui.view_manager import ViewManager


@pytest.fixture(autouse=True)
def _reset_webview_occlusion():
    clear_global_webview_occlusion()
    yield
    clear_global_webview_occlusion()


def _has_occlusion_reason(reason: str) -> bool:
    if _ACTIVE_OCCLUSION_REASONS.get(reason, 0) > 0:
        return True
    return any(r == reason for r, _tok in _ACTIVE_OCCLUSION_TOKENS)


class _FakeOverlayWebViewPane(ft.Container):
    """Test double matching the integrated CoursesWebViewPane(occlusion_layer=...) signature (no **kwargs)."""

    instances: list["_FakeOverlayWebViewPane"] = []

    def __init__(
        self,
        *,
        page,
        source: str,
        title: str,
        url: str,
        preview_server=None,
        session_client=None,
        webview_auth_service=None,
        account_epoch_getter=None,
        on_back,
        on_open_external,
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand=None,
        embedded_webview_factory=None,
        pane_width: float | None = None,
        account_namespace: str | None = None,
        occlusion_layer: str = "courses_pane",
    ) -> None:
        super().__init__(expand=True)
        self.init_kwargs = {
            "page": page,
            "source": source,
            "title": title,
            "url": url,
            "preview_server": preview_server,
            "session_client": session_client,
            "webview_auth_service": webview_auth_service,
            "account_epoch_getter": account_epoch_getter,
            "on_back": on_back,
            "on_open_external": on_open_external,
            "is_expanded": is_expanded,
            "show_expand_toggle": show_expand_toggle,
            "on_toggle_expand": on_toggle_expand,
            "embedded_webview_factory": embedded_webview_factory,
            "pane_width": pane_width,
            "account_namespace": account_namespace,
            "occlusion_layer": occlusion_layer,
        }
        self.occlusion_layer = occlusion_layer
        self.bounds: tuple[float, float, float, float, float] | None = None
        self.disposed = False
        self.suspended = False
        self.resume_count = 0
        self.back_steps = 0
        self.instances.append(self)

    def sync_bounds(
        self,
        left: float,
        top: float,
        width: float,
        height: float,
        *,
        nav_bottom_inset: float = 0.0,
    ) -> None:
        self.bounds = (left, top, width, height, nav_bottom_inset)

    def handle_back(self) -> bool:
        if self.back_steps > 0:
            self.back_steps -= 1
            return True
        return False

    def suspend(self) -> None:
        self.suspended = True

    def resume(self) -> None:
        if not self.disposed:
            self.suspended = False
            self.resume_count += 1

    def dispose(self, *, clear_browser_storage: bool = False) -> None:
        del clear_browser_storage
        self.disposed = True


def _build_controller_with_view_manager(monkeypatch) -> tuple[AppController, list[_FakeOverlayWebViewPane]]:
    mounted_panes: list[_FakeOverlayWebViewPane] = []
    _FakeOverlayWebViewPane.instances = mounted_panes

    monkeypatch.setattr(
        "gui.components.courses.embedded_webview_host._is_windows_webview2_platform",
        lambda _page: True,
    )
    monkeypatch.setattr(
        "gui.components.courses.embedded_webview_host._webview2_runtime_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "gui.components.courses.webview_pane.CoursesWebViewPane",
        _FakeOverlayWebViewPane,
    )
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret-pass")

    dialogs_controls: list[object] = []

    def _show_dialog(dlg):
        dlg.open = True
        dialogs_controls.append(dlg)

    def _pop_dialog():
        # Match Flet 0.86.5 Page.pop_dialog: marks open=False WITHOUT calling on_dismiss!
        dlg = next((d for d in reversed(dialogs_controls) if getattr(d, "open", False)), None)
        if dlg is None:
            return None
        dlg.open = False
        return dlg

    page = SimpleNamespace(
        width=1280,
        height=820,
        web=False,
        platform=ft.PagePlatform.WINDOWS,
        overlay=[],
        dialog=None,
        _dialogs=SimpleNamespace(controls=dialogs_controls),
        window=SimpleNamespace(
            width=1280,
            height=820,
            visible=True,
            minimized=False,
            maximized=False,
            focused=True,
            to_front=AsyncMock(),
        ),
        show_dialog=_show_dialog,
        pop_dialog=_pop_dialog,
        update=Mock(),
        run_task=lambda fn, *a, **k: None,
    )

    controller = AppController.__new__(AppController)
    controller.page = page
    controller._account_epoch = 3
    controller._settings_identity = ("2251120001", "secret-pass")
    controller._nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)
    controller._nav_shell.apply_width(1280)
    controller._rail_items = None
    controller._window_header = ft.Container(height=34)

    controller.dashboard = ft.Container(visible=True, opacity=1.0)
    controller.detail_view = ft.Container(visible=False, opacity=1.0)
    controller.detail_view.show_loading = lambda _d: None
    controller.detail_view.update_detail = lambda _d: None
    controller.settings_view = ft.Container(visible=False, opacity=1.0)
    controller.settings_view.has_changes = lambda: False
    controller.settings_view.load_current_settings = AsyncMock()
    controller.settings_view.cancel_pending_load = lambda: None
    controller.calendar_view = ft.Container(visible=False, opacity=1.0)
    controller.calendar_view.show = lambda _d: None
    controller.calendar_view.hide = lambda: None
    controller.grade_overview_view = ft.Container(visible=False, opacity=1.0)
    controller.grade_overview_view.show_loading = lambda: None
    controller.grade_overview_view.hide = lambda: None
    controller.grade_overview_view.update_theme = lambda: None
    controller.courses_view = ft.Container(visible=False, opacity=1.0)
    controller.courses_view.handle_back = lambda: False
    controller.course_grades_view = ft.Container(visible=False, opacity=1.0)
    controller.course_grades_view.hide = lambda: None
    controller.quiz_player_view = SimpleNamespace(handle_back=lambda: False, visible=False)
    controller._today_schedule_component = SimpleNamespace(
        surface_open=False,
        handle_back=lambda: False,
        close_surface=Mock(),
    )

    controller._desktop_filter_top_dismiss_layer = ft.Container(visible=False)
    controller._desktop_filter_upper_body_dismiss_layer = ft.Container(visible=False)
    controller._desktop_filter_dismiss_layer = ft.Container(visible=False)
    controller._desktop_filter_popup_host = ft.Container(
        visible=True,
        opacity=0,
        scale=0.96,
        offset=ft.Offset(0, -0.025),
        ignore_interactions=True,
    )
    controller._active_desktop_filter_menu = None

    controller._app_webview_pane = None
    controller._app_webview_host = ft.Container(visible=False, expand=True)

    controller.tab_switcher = ft.AnimatedSwitcher(content=controller.dashboard)
    controller._empty_detail = ft.Container(expand=True, ignore_interactions=True)
    controller.detail_switcher = ft.AnimatedSwitcher(content=controller._empty_detail)
    controller._detail_host = ft.Container(
        content=controller.detail_switcher,
        ignore_interactions=True,
    )
    overlays = [
        controller._desktop_filter_top_dismiss_layer,
        controller._desktop_filter_upper_body_dismiss_layer,
        controller._desktop_filter_dismiss_layer,
        controller._desktop_filter_popup_host,
        controller._app_webview_host,
    ]
    main_stack = ft.Stack(
        controls=[controller.tab_switcher, controller._detail_host, *overlays],
        expand=True,
    )
    controller.view_manager = ViewManager(
        page=page,
        dashboard=controller.dashboard,
        detail_view=controller.detail_view,
        settings_view=controller.settings_view,
        calendar_view=controller.calendar_view,
        grade_overview_view=controller.grade_overview_view,
        controller=controller,
        courses_view=controller.courses_view,
        course_grades_view=controller.course_grades_view,
        view_host=main_stack,
        overlays=overlays,
        tab_switcher=controller.tab_switcher,
        detail_switcher=controller.detail_switcher,
        empty_detail=controller._empty_detail,
        on_destination_change=controller._nav_shell.set_active,
    )
    courses_client = object()
    controller._courses_coordinator = SimpleNamespace(
        preview_server="preview_srv",
        _client_for=lambda src: courses_client if src == "courses" else None,
        ensure_loaded_async=AsyncMock(),
    )
    controller.orchestrator = SimpleNamespace(
        client=courses_client,
        thnn_client=object(),
    )
    controller._portal_client = object()
    controller._webview_auth_service = None
    controller._diag_screen = lambda *_a, **_kw: None
    controller._diag_breadcrumb = lambda *_a, **_kw: None
    controller._safe_run_task = lambda *_a, **_kw: None
    return controller, mounted_panes


def test_detail_view_open_url_in_app_passes_app_overlay_occlusion_layer_and_preserves_detail(
    monkeypatch,
):
    controller, mounted_panes = _build_controller_with_view_manager(monkeypatch)

    # 1. User clicks an ActivityCard on Tổng quan -> ViewManager opens DetailView
    controller.view_manager.show_detail_loading({"id": "assign-42", "title": "Bài tập lớn"})
    assert controller.detail_view.visible is True
    assert controller.detail_switcher.content is controller.detail_view
    assert _has_occlusion_reason("detail_view") is True

    # 2. User clicks "Mở trong ứng dụng" inside DetailView
    controller._open_url_in_app(
        "https://courses.ut.edu.vn/mod/assign/view.php?id=42",
        "Bài tập lớn",
    )

    assert len(mounted_panes) == 1
    pane = mounted_panes[0]
    assert pane.occlusion_layer == "app_overlay"
    assert pane.init_kwargs["occlusion_layer"] == "app_overlay"
    assert controller._app_webview_host.visible is True
    assert controller._app_webview_pane is pane
    # DetailView remains open underneath, and "detail_view" occlusion stays active
    # so any Courses Pane-3 dock below DetailView stays hidden!
    assert controller.detail_view.visible is True
    assert controller.detail_switcher.content is controller.detail_view
    assert _has_occlusion_reason("detail_view") is True


def test_closing_app_webview_via_escape_or_on_back_returns_cleanly_to_detail_view(
    monkeypatch,
):
    controller, mounted_panes = _build_controller_with_view_manager(monkeypatch)

    controller.view_manager.show_detail_loading({"id": "assign-42"})
    controller._open_url_in_app(
        "https://courses.ut.edu.vn/mod/assign/view.php?id=42",
        "Bài tập lớn",
    )
    pane = mounted_panes[0]
    pane.back_steps = 1

    # 1st Escape steps back inside WebView history without closing _app_webview_host or DetailView
    asyncio.run(controller._on_keyboard_event(SimpleNamespace(key="Escape")))
    assert controller._app_webview_host.visible is True
    assert pane.disposed is False
    assert controller.detail_view.visible is True
    assert _has_occlusion_reason("detail_view") is True

    # 2nd Escape closes _app_webview_host FIRST and leaves DetailView open & intact!
    asyncio.run(controller._on_keyboard_event(SimpleNamespace(key="Escape")))
    assert controller._app_webview_host.visible is False
    assert controller._app_webview_pane is None
    assert pane.disposed is True
    assert controller.detail_view.visible is True
    assert controller.detail_switcher.content is controller.detail_view
    assert _has_occlusion_reason("detail_view") is True

    # Re-open in-app WebView and close via toolbar Back button (on_back callback)
    controller._open_url_in_app(
        "https://courses.ut.edu.vn/mod/assign/view.php?id=42",
        "Bài tập lớn",
    )
    pane2 = mounted_panes[1]
    pane2.init_kwargs["on_back"]()
    assert controller._app_webview_host.visible is False
    assert controller._app_webview_pane is None
    assert pane2.disposed is True
    assert controller.detail_view.visible is True
    assert _has_occlusion_reason("detail_view") is True

    # Final Escape now closes DetailView and clears "detail_view" occlusion
    asyncio.run(controller._on_keyboard_event(SimpleNamespace(key="Escape")))
    assert controller.detail_view.visible is False
    assert _has_occlusion_reason("detail_view") is False


def test_keyboard_events_do_not_leak_to_courses_view_underneath_app_webview_overlay(
    monkeypatch,
):
    controller, mounted_panes = _build_controller_with_view_manager(monkeypatch)

    # Open Môn học tab with a Pane-3 overlay whose handle_key_event would swallow Escape
    controller.view_manager.show_courses()
    swallowed = []
    search_focused = []
    controller.courses_view._overlays = [
        SimpleNamespace(handle_key_event=lambda _e: swallowed.append(True) or True)
    ]
    controller.courses_view.focus_search = lambda: search_focused.append(True)

    # Open _app_webview_host on top of Môn học (without DetailView open)
    controller._open_url_in_app(
        "https://portal.ut.edu.vn/dashboard",
        "Portal",
    )
    pane = mounted_panes[0]
    assert controller._app_webview_host.visible is True
    assert controller.detail_view.visible is False

    # Ctrl+K must NOT focus Courses search while _app_webview_host covers the screen
    asyncio.run(controller._on_keyboard_event(SimpleNamespace(key="k", ctrl=True, meta=False, alt=False)))
    assert search_focused == []

    # Escape must close _app_webview_host instead of being swallowed by courses_view._overlays
    asyncio.run(controller._on_keyboard_event(SimpleNamespace(key="Escape")))
    assert swallowed == []
    assert controller._app_webview_host.visible is False
    assert pane.disposed is True


def test_rail_navigate_and_view_manager_close_dispose_app_webview_across_all_tabs(
    monkeypatch,
):
    controller, mounted_panes = _build_controller_with_view_manager(monkeypatch)
    controller._data_lock = app_controller_mod.threading.Lock()
    controller.all_data = []

    # Case 1: User is on Tổng quan -> DetailView -> Mở trong ứng dụng, then clicks Tổng quan ("home")
    controller.view_manager.show_detail_loading({"id": "assign-1"})
    controller._open_url_in_app("https://courses.ut.edu.vn/mod/assign/view.php?id=1", "BT 1")
    pane_home = mounted_panes[-1]
    assert controller._app_webview_host.visible is True

    asyncio.run(controller._rail_navigate("home"))
    assert controller._app_webview_host.visible is False
    assert controller._app_webview_pane is None
    assert pane_home.disposed is True
    assert controller.detail_view.visible is False

    # Case 2: User is on Lịch -> opens _app_webview_host -> clicks Lịch ("calendar") again
    asyncio.run(controller._rail_navigate("calendar"))
    assert controller.calendar_view.visible is True
    controller._open_url_in_app("https://courses.ut.edu.vn/course/view.php?id=7", "LMS")
    pane_cal_same = mounted_panes[-1]
    assert controller._app_webview_host.visible is True

    asyncio.run(controller._rail_navigate("calendar"))
    assert controller._app_webview_host.visible is False
    assert controller._app_webview_pane is None
    assert pane_cal_same.disposed is True
    assert controller.calendar_view.visible is True

    # Case 3: User is on Lịch -> opens _app_webview_host -> switches to Tổng quan ("home")
    controller._open_url_in_app("https://courses.ut.edu.vn/course/view.php?id=8", "LMS 2")
    pane_cal_to_home = mounted_panes[-1]
    assert controller._app_webview_host.visible is True

    asyncio.run(controller._rail_navigate("home"))
    assert controller._app_webview_host.visible is False
    assert controller._app_webview_pane is None
    assert pane_cal_to_home.disposed is True
    assert controller.dashboard.visible is True

    # Case 4: ViewManager.close_grades / close_settings / close_course_grades also close _app_webview_host
    controller.view_manager.show_grades_loading()
    controller._open_url_in_app("https://portal.ut.edu.vn/dashboard", "Portal")
    pane_grades = mounted_panes[-1]
    asyncio.run(controller.view_manager.close_grades())
    assert controller._app_webview_host.visible is False
    assert pane_grades.disposed is True


def test_open_url_in_app_releases_modal_and_desktop_filter_occlusion_without_clearing_detail(
    monkeypatch,
):
    controller, mounted_panes = _build_controller_with_view_manager(monkeypatch)

    # DetailView is open underneath
    controller.view_manager.show_detail_loading({"id": "assign-99"})
    assert _has_occlusion_reason("detail_view") is True

    # Open desktop filter menu -> must set "desktop_filter" occlusion True
    menu = AdaptiveFilterMenu(
        page=controller.page,
        title="Mức độ",
        options=[FilterMenuOption("all", "Tất cả", "#64748B", count=3)],
        selected_key="all",
        on_select=lambda _k: None,
        mobile=False,
    )
    tap_event = SimpleNamespace(
        global_position=ft.Offset(180, 140),
        local_position=ft.Offset(20, 15),
    )
    controller._toggle_desktop_filter_menu(menu, tap_event)
    assert controller._active_desktop_filter_menu is menu
    assert _has_occlusion_reason("desktop_filter") is True

    # Also open TodaySchedulePanel surface + stacked modal dialogs whose pop_dialog()
    # will NOT fire on_dismiss synchronously!
    controller._today_schedule_component.surface_open = True
    dlg_outer = ft.AlertDialog(title=ft.Text("Chi tiết buổi học"))
    dlg_inner = ft.AlertDialog(title=ft.Text("Đề cương chi tiết"))
    controller.page.show_dialog(dlg_outer)
    controller.page.show_dialog(dlg_inner)
    set_global_webview_occlusion("modal_dialog", True, token="dlg:leaked-token")
    set_global_webview_occlusion("modal_dialog", True)
    assert _has_occlusion_reason("modal_dialog") is True

    # Trigger _open_url_in_app while filter + dialogs are open
    controller._open_url_in_app(
        "https://courses.ut.edu.vn/course/view.php?id=99",
        "LMS",
    )

    # Desktop filter menu & TodaySchedulePanel & stacked dialogs are all closed,
    # and BOTH "desktop_filter" and "modal_dialog" occlusions are released!
    assert controller._active_desktop_filter_menu is None
    assert menu._expanded is False
    controller._today_schedule_component.close_surface.assert_called_once()
    assert dlg_outer.open is False
    assert dlg_inner.open is False
    assert _has_occlusion_reason("desktop_filter") is False
    assert _has_occlusion_reason("modal_dialog") is False
    # Crucially, "detail_view" occlusion is STILL preserved for DetailView underneath!
    assert _has_occlusion_reason("detail_view") is True
    assert controller._app_webview_host.visible is True
    assert mounted_panes[-1].occlusion_layer == "app_overlay"


def test_page_dialog_occlusion_bridge_hides_app_webview_for_modals_not_snackbars(
    monkeypatch,
):
    controller, mounted_panes = _build_controller_with_view_manager(monkeypatch)
    controller._install_page_dialog_occlusion_bridge()

    controller._open_url_in_app(
        "https://courses.ut.edu.vn/mod/assign/view.php?id=55",
        "Bài tập 55",
    )
    assert len(mounted_panes) == 1
    assert _has_occlusion_reason("modal_dialog") is False

    # SnackBar is non-modal and must NEVER occlude WebViews
    sb = ft.SnackBar(content=ft.Text("Đã lưu"))
    controller.page.show_dialog(sb)
    assert _has_occlusion_reason("modal_dialog") is False
    sb.open = False

    # Opening an AlertDialog on top of _app_webview_host immediately sets modal_dialog occlusion
    dismiss_calls = []
    modal = ft.AlertDialog(
        title=ft.Text("Xác nhận"),
        on_dismiss=lambda _e: dismiss_calls.append("dismissed"),
    )
    controller.page.show_dialog(modal)
    assert _has_occlusion_reason("modal_dialog") is True

    # Programmatic pop_dialog() releases modal_dialog occlusion immediately (even before on_dismiss)
    controller.page.pop_dialog()
    assert _has_occlusion_reason("modal_dialog") is False

    # Subsequent delayed on_dismiss from Flutter still runs original handler and stays idempotent
    modal.on_dismiss(None)
    assert dismiss_calls == ["dismissed"]
    assert _has_occlusion_reason("modal_dialog") is False


def test_window_hide_to_tray_restore_and_resize_keep_app_webview_clean(
    monkeypatch,
):
    controller, mounted_panes = _build_controller_with_view_manager(monkeypatch)
    monkeypatch.setattr(settings, "MINIMIZE_TO_TRAY", True)
    controller.tray = SimpleNamespace(is_ready=True, notify=Mock(), _icon=None)
    controller._tray_balloon_shown = True
    controller.window_activator = app_controller_mod._ControllerWindowActivator(
        controller.page,
        on_shown=controller._on_window_restored,
    )

    controller._open_url_in_app(
        "https://courses.ut.edu.vn/mod/assign/view.php?id=77",
        "Bài tập 77",
    )
    pane = mounted_panes[0]
    assert pane.suspended is False
    assert pane.bounds == (89.0, 36.0, 1191.0, 784.0, 0.0)

    # 1. Hiding main window to system tray suspends native WebView immediately while keeping host state
    asyncio.run(controller._handle_window_close_request())
    assert controller.page.window.visible is False
    assert controller._app_webview_host.visible is True
    assert pane.suspended is True

    # 2. Restoring from tray via window_activator.show() resumes WebView and syncs bounds
    asyncio.run(controller.window_activator.show())
    assert controller.page.window.visible is True
    assert pane.suspended is False
    assert pane.resume_count >= 1

    # 3. Resizing below rail breakpoint (<600px) and back to wide desktop updates bounds & bottom inset
    controller.page.width = 520
    controller.page.height = 700
    controller._on_page_resize(SimpleNamespace(width=520, height=700))
    assert pane.bounds == (0.0, 36.0, 520.0, 600.0, 64.0)

    controller.page.width = 1280
    controller.page.height = 820
    controller._on_page_resize(SimpleNamespace(width=1280, height=820))
    assert pane.bounds == (89.0, 36.0, 1191.0, 784.0, 0.0)

    # 4. OS window minimize/restore events also suspend and resume cleanly
    monkeypatch.setattr(app_controller_mod.platform_utils, "IS_MOBILE", False)
    controller.page.window.minimized = True
    asyncio.run(controller._on_window_event(SimpleNamespace(type=None, data="minimize")))
    assert pane.suspended is True

    controller.page.window.minimized = False
    asyncio.run(controller._on_window_event(SimpleNamespace(type=None, data="restore")))
    assert pane.suspended is False


def test_open_url_in_app_supports_var_keyword_legacy_signature_and_reused_dialogs(
    monkeypatch,
):
    controller, _ = _build_controller_with_view_manager(monkeypatch)
    controller._install_page_dialog_occlusion_bridge()

    # 1. **kwargs signature receives occlusion_layer="app_overlay"
    captured_var_kw: list[dict] = []

    class _VarKwPane(ft.Container):
        def __init__(self, **kwargs):
            super().__init__()
            captured_var_kw.append(kwargs)

        def sync_bounds(self, *_a, **_kw):
            pass

        def dispose(self, **_kw):
            pass

    monkeypatch.setattr(
        "gui.components.courses.webview_pane.CoursesWebViewPane",
        _VarKwPane,
    )
    controller._open_url_in_app("https://courses.ut.edu.vn/course/view.php?id=11", "T1")
    assert captured_var_kw[-1]["occlusion_layer"] == "app_overlay"

    # 2. Standalone pre-merge signature without occlusion_layer mounts cleanly without TypeError
    mounted_legacy = []

    class _LegacyPane(ft.Container):
        def __init__(
            self,
            *,
            page,
            source,
            title,
            url,
            preview_server=None,
            session_client=None,
            webview_auth_service=None,
            account_epoch_getter=None,
            on_back=None,
            on_open_external=None,
            show_expand_toggle=False,
            pane_width=None,
            account_namespace=None,
        ):
            super().__init__()
            del (
                page,
                source,
                title,
                preview_server,
                session_client,
                webview_auth_service,
                account_epoch_getter,
                on_back,
                on_open_external,
                show_expand_toggle,
                pane_width,
                account_namespace,
            )
            mounted_legacy.append(url)

        def sync_bounds(self, *_a, **_kw):
            pass

        def dispose(self, **_kw):
            pass

    monkeypatch.setattr(
        "gui.components.courses.webview_pane.CoursesWebViewPane",
        _LegacyPane,
    )
    controller._open_url_in_app("https://courses.ut.edu.vn/course/view.php?id=12", "T2")
    assert mounted_legacy == ["https://courses.ut.edu.vn/course/view.php?id=12"]

    # 3. Reused dialog instance occludes on every re-open and clears on every pop
    reusable_dlg = ft.AlertDialog(title=ft.Text("Reusable"))
    for _ in range(2):
        controller.page.show_dialog(reusable_dlg)
        assert _has_occlusion_reason("modal_dialog") is True
        controller.page.pop_dialog()
        assert _has_occlusion_reason("modal_dialog") is False

    # 4. Escape dismisses an open desktop filter menu before closing DetailView
    controller._close_app_webview()
    controller.view_manager.show_detail_loading({"id": "assign-esc"})
    menu = AdaptiveFilterMenu(
        page=controller.page,
        title="Loại bài",
        options=[FilterMenuOption("all", "Tất cả", "#64748B", count=1)],
        selected_key="all",
        on_select=lambda _k: None,
        mobile=False,
    )
    controller._toggle_desktop_filter_menu(
        menu,
        SimpleNamespace(
            global_position=ft.Offset(150, 130),
            local_position=ft.Offset(10, 10),
        ),
    )
    assert controller._active_desktop_filter_menu is menu
    assert _has_occlusion_reason("desktop_filter") is True

    assert asyncio.run(controller._navigate_back()) is True
    assert controller._active_desktop_filter_menu is None
    assert _has_occlusion_reason("desktop_filter") is False
    assert controller.detail_view.visible is True


def test_programmatic_dialog_open_false_releases_modal_occlusion_via_update_and_navigate_back(
    monkeypatch,
):
    controller, _ = _build_controller_with_view_manager(monkeypatch)
    controller._install_page_dialog_occlusion_bridge()

    # 1. TodaySchedulePanel.close_surface() pattern: dlg.open = False + _safe_update(dlg)
    #    (where unmounted ft.AlertDialog.update() raises AssertionError/RuntimeError)
    dlg_sched = ft.AlertDialog(title=ft.Text("Lịch học hôm nay"))
    controller.page.show_dialog(dlg_sched)
    assert dlg_sched.open is True
    assert _has_occlusion_reason("modal_dialog") is True

    dlg_sched.open = False
    try:
        dlg_sched.update()
    except (AssertionError, RuntimeError):
        pass
    assert _has_occlusion_reason("modal_dialog") is False

    # 2. _navigate_back() -> schedule_component.handle_back() setting open=False without dlg.update()
    sheet = ft.BottomSheet(content=ft.Text("Chi tiết buổi học"))
    controller.page.show_dialog(sheet)
    assert sheet.open is True
    assert _has_occlusion_reason("modal_dialog") is True

    def _close_sheet_on_back() -> bool:
        sheet.open = False
        return True

    controller._today_schedule_component.handle_back = _close_sheet_on_back
    assert asyncio.run(controller._navigate_back()) is True
    assert _has_occlusion_reason("modal_dialog") is False

    # 3. DocumentReaderView._close_pdf_outline_sheet() via _on_keyboard_event(Escape)
    #    (safe_control_update skips sheet.update() when sheet.page is None)
    outline_sheet = ft.BottomSheet(content=ft.Text("Mục lục PDF"))
    controller.view_manager.show_courses()
    controller.page.show_dialog(outline_sheet)
    assert _has_occlusion_reason("modal_dialog") is True

    def _overlay_esc(ev) -> bool:
        if getattr(ev, "key", "") == "Escape":
            outline_sheet.open = False
            return True
        return False

    controller.courses_view._overlays = [SimpleNamespace(handle_key_event=_overlay_esc)]
    asyncio.run(controller._on_keyboard_event(SimpleNamespace(key="Escape")))
    assert _has_occlusion_reason("modal_dialog") is False

    # 4. page.update() sweeps closed dialogs while preserving Mock.assert_called()
    dlg_page_upd = ft.AlertDialog(title=ft.Text("Đóng qua page.update"))
    controller.page.show_dialog(dlg_page_upd)
    assert _has_occlusion_reason("modal_dialog") is True
    dlg_page_upd.open = False
    controller.page.update()
    controller.page.update.assert_called()
    assert _has_occlusion_reason("modal_dialog") is False

    # 5. Mock() show_dialog that leaves dialog.open == False is NOT swept prematurely by page.update()
    mock_page = SimpleNamespace(
        show_dialog=Mock(),
        pop_dialog=Mock(return_value=None),
        update=Mock(),
    )
    mock_ctrl = AppController.__new__(AppController)
    mock_ctrl.page = mock_page
    mock_ctrl._install_page_dialog_occlusion_bridge()
    inert_dlg = ft.AlertDialog(title=ft.Text("Mock Show"))
    assert inert_dlg.open is False
    mock_ctrl.page.show_dialog(inert_dlg)
    assert _has_occlusion_reason("modal_dialog") is True
    mock_ctrl.page.update()
    assert _has_occlusion_reason("modal_dialog") is True
    inert_dlg.on_dismiss(None)
    assert _has_occlusion_reason("modal_dialog") is False


def test_reused_dialog_delayed_on_dismiss_from_previous_open_never_unoccludes_active_reopen(
    monkeypatch,
):
    controller, _ = _build_controller_with_view_manager(monkeypatch)
    controller._install_page_dialog_occlusion_bridge()

    dismiss_events: list[str] = []
    reusable_dlg = ft.AlertDialog(
        title=ft.Text("Xác nhận thoát"),
        on_dismiss=lambda _e: dismiss_events.append("dismiss"),
    )

    # Open #1
    controller.page.show_dialog(reusable_dlg)
    tok_1 = getattr(reusable_dlg, "_uth_modal_occlusion_token", None)
    assert tok_1
    assert _has_occlusion_reason("modal_dialog") is True

    # Close #1 via programmatic pop_dialog() (Flutter's async on_dismiss has NOT arrived yet!)
    controller.page.pop_dialog()
    assert reusable_dlg.open is False
    assert _has_occlusion_reason("modal_dialog") is False

    # Open #2 immediately on the SAME dialog instance before Open #1's on_dismiss fires
    controller.page.show_dialog(reusable_dlg)
    tok_2 = getattr(reusable_dlg, "_uth_modal_occlusion_token", None)
    assert tok_2 and tok_2 != tok_1
    assert reusable_dlg.open is True
    assert _has_occlusion_reason("modal_dialog") is True

    # Delayed async on_dismiss from Open #1 arrives while Open #2 is still open!
    reusable_dlg.on_dismiss(None)
    assert dismiss_events == ["dismiss"]
    # Open #2 MUST stay occluded!
    assert _has_occlusion_reason("modal_dialog") is True

    # Finally, Open #2 closes via barrier dismiss (open -> False, then on_dismiss)
    reusable_dlg.open = False
    reusable_dlg.on_dismiss(None)
    assert dismiss_events == ["dismiss", "dismiss"]
    assert _has_occlusion_reason("modal_dialog") is False


