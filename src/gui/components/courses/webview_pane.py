"""Authenticated in-app WebView pane (Windows WebView2 + flet-webview + HTML reader fallback)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import queue
import sys
import threading
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import flet as ft
from bs4 import BeautifulSoup

from core.courses.models import CourseSource
from core.html_compat import BS4_PARSER
from core.security import HTMLSanitizer
from gui.components.courses._shared import (
    build_viewer_header,
    refresh_viewer_header,
    safe_control_update,
)
from gui.core.theme import C

logger = logging.getLogger(__name__)

_EXTERNAL_MEETING_HOSTS = (
    "meet.google.com",
    "zoom.us",
    "teams.microsoft.com",
    "zalo.me",
)


def is_external_meeting_url(url: str) -> bool:
    """Return True only for external video/chat tools better opened in native apps."""
    host = (urlsplit(str(url or "").strip()).hostname or "").lower()
    if not host:
        return False
    return any(host == m or host.endswith(f".{m}") for m in _EXTERNAL_MEETING_HOSTS)


def _is_flet_webview_platform(page: Optional[ft.Page]) -> bool:
    if bool(getattr(page, "web", False)):
        return True
    platform_val = str(getattr(page, "platform", "") or "").lower()
    if any(p in platform_val for p in ("android", "ios", "macos")):
        return True
    return sys.platform != "win32"


def _webview2_runtime_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        from webview2.microsoft.web.webview2 import core as wv2  # noqa: F401
        from winrt.windows.foundation import Rect  # noqa: F401
        import win32con  # noqa: F401
        import win32gui  # noqa: F401

        try:
            ver = wv2.CoreWebView2Environment.get_available_browser_version_string()
        except TypeError:
            ver = wv2.CoreWebView2Environment.get_available_browser_version_string_with_options(
                "", None
            )
        return bool(ver)
    except Exception:
        return False


_NON_READABLE_HTML_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "head",
    "title",
    "meta",
    "link",
    "iframe",
    "object",
    "embed",
)


class WindowsWebView2Dock:
    """Owned borderless WebView2 host window synced over Pane 3 on Windows Desktop."""

    _class_registered = False
    _class_name = "UTHelperCoursesWebView2Host"

    def __init__(
        self,
        *,
        page_title: str,
        url: str,
        cookies: list[dict[str, Any]],
        cookie_provider: Optional[Callable[[], list[dict[str, Any]]]] = None,
        on_title_changed: Optional[Callable[[str], None]] = None,
        on_url_changed: Optional[Callable[[str], None]] = None,
        on_loading_changed: Optional[Callable[[bool], None]] = None,
        on_external_url: Optional[Callable[[str], None]] = None,
        on_ready_failed: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._page_title = page_title or "UTHelper"
        self._url = url
        self._cookies = list(cookies or ())
        self._cookie_provider = cookie_provider
        self._on_title_changed = on_title_changed
        self._on_url_changed = on_url_changed
        self._on_loading_changed = on_loading_changed
        self._on_external_url = on_external_url
        self._on_ready_failed = on_ready_failed

        self._cmd_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop_event = threading.Event()
        self._ready = False
        self._failed = False
        self._visible = True
        self._bounds_dip: tuple[float, float, float, float] = (380.0, 120.0, 640.0, 480.0)
        self._hwnd: int = 0
        self._parent_hwnd: int = 0
        self._controller: Any = None
        self._core: Any = None
        self._thread: Optional[threading.Thread] = None
        self._thread_id: int = 0

    def start(self) -> None:
        if self._thread is not None or sys.platform != "win32":
            return
        self._thread = threading.Thread(
            target=self._run_sta_thread,
            name="CoursesWebView2STA",
            daemon=True,
        )
        self._thread.start()

    def navigate(self, url: str) -> None:
        self._url = url
        self._enqueue("navigate", url)

    def go_back(self) -> None:
        self._enqueue("go_back", None)

    def go_forward(self) -> None:
        self._enqueue("go_forward", None)

    def reload(self) -> None:
        self._enqueue("reload", None)

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)
        self._enqueue("visible", self._visible)

    def set_bounds(self, left: float, top: float, width: float, height: float) -> None:
        self._bounds_dip = (
            max(0.0, float(left)),
            max(0.0, float(top)),
            max(120.0, float(width)),
            max(120.0, float(height)),
        )
        self._enqueue("bounds", self._bounds_dip)

    def dispose(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self._enqueue("dispose", None)
        if self._thread_id and sys.platform == "win32":
            try:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
            except Exception:
                pass

    def _enqueue(self, action: str, payload: Any) -> None:
        self._cmd_queue.put((action, payload))
        if self._thread_id and sys.platform == "win32":
            try:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0400 + 42, 0, 0)
            except Exception:
                pass

    @staticmethod
    def _collect_child_pids(parent_pid: int) -> set[int]:
        """Collect direct child process IDs on Windows (e.g., flet.exe spawned by python.exe)."""
        pids: set[int] = {parent_pid}
        if sys.platform != "win32":
            return pids
        try:
            class _PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260),
                ]

            k32 = ctypes.windll.kernel32
            snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)
            if snap in (0, -1, ctypes.c_void_p(-1).value):
                return pids
            try:
                entry = _PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
                if k32.Process32FirstW(snap, ctypes.byref(entry)):
                    while True:
                        if int(entry.th32ParentProcessID) == parent_pid:
                            pids.add(int(entry.th32ProcessID))
                        if not k32.Process32NextW(snap, ctypes.byref(entry)):
                            break
            finally:
                k32.CloseHandle(snap)
        except Exception:
            pass
        return pids

    @staticmethod
    def _find_parent_hwnd(expected_title: str) -> int:
        import win32gui
        import win32process

        cur_pid = os.getpid()
        family_pids = WindowsWebView2Dock._collect_child_pids(cur_pid)
        matched: list[tuple[int, int]] = []

        def _enum_cb(hwnd: int, _ctx: Any) -> bool:
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                title = win32gui.GetWindowText(hwnd) or ""
                cls_name = win32gui.GetClassName(hwnd) or ""
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                score = 0
                if pid == cur_pid:
                    score += 12
                elif pid in family_pids:
                    score += 10
                if cls_name == "FLUTTER_RUNNER_WIN32_WINDOW":
                    score += 6
                if expected_title and expected_title.lower() in title.lower():
                    score += 5
                if "uthelper" in title.lower():
                    score += 4
                if score > 0 and (title.strip() or cls_name == "FLUTTER_RUNNER_WIN32_WINDOW"):
                    matched.append((score, hwnd))
            except Exception:
                pass
            return True

        win32gui.EnumWindows(_enum_cb, None)
        if not matched:
            return 0
        matched.sort(key=lambda item: item[0], reverse=True)
        return matched[0][1]

    def _run_sta_thread(self) -> None:
        try:
            ctypes.oledll.ole32.CoInitializeEx(None, 2)
        except Exception:
            pass
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        try:
            self._init_window_and_webview2()
            self._pump_messages()
        except Exception as exc:
            logger.warning("WindowsWebView2Dock STA failed: %s", exc)
            if not self._failed and self._on_ready_failed is not None:
                self._failed = True
                self._on_ready_failed(str(exc))
        finally:
            self._cleanup_native()
            try:
                ctypes.oledll.ole32.CoUninitialize()
            except Exception:
                pass

    def _init_window_and_webview2(self) -> None:
        import time as _time
        import win32api
        import win32con
        import win32gui
        from webview2.microsoft.web.webview2 import core as wv2

        for _ in range(5):
            self._parent_hwnd = self._find_parent_hwnd(self._page_title)
            if self._parent_hwnd:
                break
            _time.sleep(0.04)
        if not self._parent_hwnd:
            self._fail("Không tìm thấy cửa sổ chính để gắn WebView2.")
            return

        hinst = win32api.GetModuleHandle(None)
        if not WindowsWebView2Dock._class_registered:
            wc = win32gui.WNDCLASS()
            wc.lpfnWndProc = win32gui.DefWindowProc
            wc.hInstance = hinst
            wc.lpszClassName = WindowsWebView2Dock._class_name
            try:
                win32gui.RegisterClass(wc)
            except Exception:
                pass
            WindowsWebView2Dock._class_registered = True

        style = win32con.WS_POPUP | win32con.WS_CLIPCHILDREN
        ex_style = win32con.WS_EX_TOOLWINDOW
        hwnd = win32gui.CreateWindowEx(
            ex_style,
            WindowsWebView2Dock._class_name,
            "UTHelper WebView",
            style,
            100,
            100,
            640,
            480,
            self._parent_hwnd,
            0,
            hinst,
            None,
        )
        self._hwnd = hwnd
        self._apply_window_bounds()

        local_app = os.environ.get("LOCALAPPDATA") or str(Path.home())
        user_data_dir = str(Path(local_app) / "UTHelper" / "webview2_profile")
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

        env_op = wv2.CoreWebView2Environment.create_with_options_async("", user_data_dir, None)

        def _on_env_done(op: Any, status: Any) -> None:
            self._enqueue("_env_done", (op, status))

        env_op.completed = _on_env_done

    def _handle_env_done(self, op: Any) -> None:
        from webview2.microsoft.web.webview2 import core as wv2

        if self._stop_event.is_set() or not self._hwnd:
            return
        try:
            env = op.get_results()
            win_ref = wv2.CoreWebView2ControllerWindowReference.create_from_window_handle(
                int(self._hwnd)
            )
            ctrl_op = env.create_core_webview2_controller_async(win_ref)
            ctrl_op.completed = lambda cop, st: self._enqueue("_ctrl_done", (cop, st))
        except Exception as exc:
            self._fail(str(exc))

    def _handle_ctrl_done(self, op: Any) -> None:
        if self._stop_event.is_set() or not self._hwnd:
            return
        try:
            ctrl = op.get_results()
            core = ctrl.core_webview2
            self._controller = ctrl
            self._core = core
            self._ready = True
            try:
                settings_obj = getattr(core, "settings", None)
                if settings_obj is not None:
                    settings_obj.is_status_bar_enabled = False
            except Exception:
                pass
            if not self._cookies and callable(self._cookie_provider):
                try:
                    self._cookies = list(self._cookie_provider() or ())
                except Exception as exc:
                    logger.debug("WebView2 async cookie provider failed: %s", exc)
            self._inject_cookies(core)
            self._bind_events(core)
            self._apply_window_bounds()
            ctrl.is_visible = self._visible
            if self._visible:
                import win32con
                import win32gui

                win32gui.ShowWindow(self._hwnd, win32con.SW_SHOWNOACTIVATE)
            if self._url:
                core.navigate(self._url)
        except Exception as exc:
            self._fail(str(exc))

    def _inject_cookies(self, core: Any) -> None:
        cm = getattr(core, "cookie_manager", None)
        if cm is None:
            return
        for ck in self._cookies:
            name = str(ck.get("name") or "")
            value = str(ck.get("value") or "")
            domain = str(ck.get("domain") or "")
            path = str(ck.get("path") or "/")
            if not name or not domain:
                continue
            try:
                cookie_obj = cm.create_cookie(name, value, domain, path)
                try:
                    cookie_obj.is_secure = bool(ck.get("secure", False))
                    cookie_obj.is_http_only = bool(ck.get("httpOnly", False))
                except Exception:
                    pass
                cm.add_or_update_cookie(cookie_obj)
            except Exception as exc:
                logger.debug("WebView2 cookie injection skipped for %s: %s", domain, exc)

    def _bind_events(self, core: Any) -> None:
        try:
            core.add_document_title_changed(
                lambda _s, _a: self._on_title_changed(str(core.document_title or ""))
                if self._on_title_changed
                else None
            )
            core.add_source_changed(
                lambda _s, _a: self._on_url_changed(str(core.source or ""))
                if self._on_url_changed
                else None
            )

            core.add_navigation_starting(
                lambda _s, _a: self._on_loading_changed(True)
                if self._on_loading_changed
                else None
            )
            core.add_navigation_completed(
                lambda _s, _a: self._on_loading_changed(False)
                if self._on_loading_changed
                else None
            )

            def _on_new_window(_sender: Any, args: Any) -> None:
                uri = str(getattr(args, "uri", "") or "")
                try:
                    args.handled = True
                except Exception:
                    pass
                if uri:
                    core.navigate(uri)

            core.add_new_window_requested(_on_new_window)
        except Exception:
            pass

    def _apply_window_bounds(self, *, preserve_zorder: bool = False) -> None:
        if not self._hwnd or sys.platform != "win32":
            return
        import win32con
        import win32gui
        from winrt.windows.foundation import Rect

        parent = self._parent_hwnd
        if parent and win32gui.IsWindow(parent):
            if win32gui.IsIconic(parent) or not win32gui.IsWindowVisible(parent) or not self._visible:
                win32gui.ShowWindow(self._hwnd, win32con.SW_HIDE)
                if self._controller is not None:
                    self._controller.is_visible = False
                return
            try:
                dpi = float(ctypes.windll.user32.GetDpiForWindow(parent))
            except Exception:
                dpi = 96.0
            scale = max(1.0, dpi / 96.0)
            origin_x, origin_y = win32gui.ClientToScreen(parent, (0, 0))
            crect = win32gui.GetClientRect(parent)
            max_w = max(160, crect[2] - crect[0])
            max_h = max(160, crect[3] - crect[1])
            left_px = max(0, int(round(self._bounds_dip[0] * scale)))
            top_px = max(0, int(round(self._bounds_dip[1] * scale)))
            w_px = min(max_w - left_px, max(160, int(round(self._bounds_dip[2] * scale))))
            h_px = min(max_h - top_px, max(160, int(round(self._bounds_dip[3] * scale))))
            flags = win32con.SWP_NOACTIVATE | (
                win32con.SWP_NOZORDER if preserve_zorder else 0
            )
            win32gui.SetWindowPos(
                self._hwnd,
                0 if preserve_zorder else win32con.HWND_TOP,
                origin_x + left_px,
                origin_y + top_px,
                w_px,
                h_px,
                flags,
            )
            if self._visible:
                win32gui.ShowWindow(self._hwnd, win32con.SW_SHOWNOACTIVATE)
            if self._controller is not None:
                self._controller.bounds = Rect(0.0, 0.0, float(w_px), float(h_px))
                self._controller.is_visible = self._visible
                self._controller.notify_parent_window_position_changed()

    def _fail(self, reason: str) -> None:
        if self._failed:
            return
        self._failed = True
        if self._on_ready_failed is not None:
            self._on_ready_failed(reason)

    def _pump_messages(self) -> None:
        import win32con
        import win32gui

        msg = wintypes.MSG()
        last_parent_state: tuple[int, int, int, int, bool] = (0, 0, 0, 0, False)
        while not self._stop_event.is_set():
            self._drain_commands()
            if self._parent_hwnd and win32gui.IsWindow(self._parent_hwnd):
                try:
                    cur_rect = win32gui.GetWindowRect(self._parent_hwnd)
                    is_min = bool(win32gui.IsIconic(self._parent_hwnd))
                    cur_state = (cur_rect[0], cur_rect[1], cur_rect[2], cur_rect[3], is_min)
                    if cur_state != last_parent_state:
                        last_parent_state = cur_state
                        self._apply_window_bounds(preserve_zorder=True)
                except Exception:
                    pass
            while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, win32con.PM_REMOVE):
                if msg.message == win32con.WM_QUIT:
                    return
                ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
            ctypes.windll.user32.MsgWaitForMultipleObjects(0, None, False, 16, 0x04FF)

    def _drain_commands(self) -> None:
        import win32con
        import win32gui

        while True:
            try:
                action, payload = self._cmd_queue.get_nowait()
            except queue.Empty:
                break
            if action == "_env_done":
                self._handle_env_done(payload[0])
            elif action == "_ctrl_done":
                self._handle_ctrl_done(payload[0])
            elif action == "navigate" and self._core is not None and payload:
                self._core.navigate(str(payload))
            elif action == "go_back" and self._core is not None and getattr(self._core, "can_go_back", False):
                self._core.go_back()
            elif action == "go_forward" and self._core is not None and getattr(self._core, "can_go_forward", False):
                self._core.go_forward()
            elif action == "reload" and self._core is not None:
                self._core.reload()
            elif action == "visible":
                if self._controller is not None:
                    self._controller.is_visible = bool(payload)
                if self._hwnd:
                    win32gui.ShowWindow(
                        self._hwnd,
                        win32con.SW_SHOWNOACTIVATE if payload else win32con.SW_HIDE,
                    )
                self._apply_window_bounds()
            elif action == "bounds":
                self._apply_window_bounds()
            elif action == "dispose":
                self._stop_event.set()
                break

    def _cleanup_native(self) -> None:
        if self._controller is not None:
            try:
                self._controller.close()
            except Exception:
                pass
            self._controller = None
        self._core = None
        if self._hwnd and sys.platform == "win32":
            try:
                import win32gui

                win32gui.DestroyWindow(self._hwnd)
            except Exception:
                pass
            self._hwnd = 0


class CoursesWebViewPane(ft.Container):
    """Pane-3 interactive authenticated browser workspace for Moodle activities & pages."""

    def __init__(
        self,
        *,
        page: Optional[ft.Page],
        source: CourseSource,
        title: str,
        url: str,
        preview_server: Any = None,
        session_client: Any = None,
        on_back: Callable[[], None],
        on_open_external: Callable[[str], None],
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
        embedded_webview_factory: Optional[Callable[[str], Optional[ft.Control]]] = None,
    ) -> None:
        super().__init__(expand=True, bgcolor=C.BG)
        self._page = page
        self._source = source
        self._title = title or "Trình duyệt học tập"
        self._url = url
        self._preview_server = preview_server
        self._session_client = session_client
        self._on_back = on_back
        self._on_open_external = on_open_external
        self._expanded = bool(is_expanded)
        self._show_expand_toggle = bool(show_expand_toggle)
        self._on_toggle_expand = on_toggle_expand
        self._embedded_factory = embedded_webview_factory

        self._dock: Optional[WindowsWebView2Dock] = None
        self._flet_webview: Any = None
        self._fallback_html_loaded = False
        self._disposed = False

        self._wv_back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED,
            icon_size=16,
            tooltip="Trang trước",
            icon_color=C.TEXT_PRIMARY,
            on_click=self._on_webview_back,
        )
        self._wv_fwd_btn = ft.IconButton(
            icon=ft.Icons.ARROW_FORWARD_IOS_ROUNDED,
            icon_size=16,
            tooltip="Trang kế",
            icon_color=C.TEXT_PRIMARY,
            on_click=self._on_webview_forward,
        )
        self._wv_reload_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_size=18,
            tooltip="Tải lại trang",
            icon_color=C.TEXT_PRIMARY,
            on_click=self._on_webview_reload,
        )
        self._ext_btn = ft.IconButton(
            icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
            icon_size=18,
            tooltip="Mở bằng trình duyệt ngoài",
            icon_color=C.TEXT_PRIMARY,
            on_click=lambda _e: self._on_open_external(self._url),
        )
        self._expand_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_FULLSCREEN_ROUNDED
            if self._expanded
            else ft.Icons.OPEN_IN_FULL_ROUNDED,
            icon_size=18,
            tooltip="Thu về khung nội dung (Ctrl+\\)"
            if self._expanded
            else "Mở rộng toàn vùng môn học (Ctrl+\\)",
            icon_color=C.TEXT_PRIMARY,
            visible=self._show_expand_toggle,
            on_click=lambda _e: self._on_toggle_expand()
            if self._on_toggle_expand is not None
            else None,
        )

        self._header_container, self._back_btn, self._title_text, self._subtitle_text = (
            build_viewer_header(
                title=self._title,
                subtitle=self._short_subtitle(self._url),
                back_tooltip="Đóng trình xem",
                on_back=lambda _e: self._handle_back(),
                trailing_actions=[
                    self._wv_back_btn,
                    self._wv_fwd_btn,
                    self._wv_reload_btn,
                    self._expand_btn,
                    self._ext_btn,
                ],
            )
        )
        self._nav_progress = ft.ProgressBar(
            value=None,
            bar_height=2,
            color=C.ACCENT,
            bgcolor=C.BORDER,
            visible=True,
        )
        self._body_host = ft.Container(expand=True, bgcolor=C.BG)
        self.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[self._header_container, self._nav_progress, self._body_host],
        )
        self._mount_engine()

    @staticmethod
    def _short_subtitle(url: str) -> str:
        parsed = urlsplit(url or "")
        return f"{parsed.netloc}{parsed.path}" if parsed.netloc else (url or "")

    def _build_bridge_or_target_url(self) -> str:
        if self._preview_server is not None and hasattr(
            self._preview_server, "build_session_bridge_url"
        ):
            bridged = self._preview_server.build_session_bridge_url(self._source, self._url)
            if bridged:
                return bridged
        return self._url

    def _mount_engine(self) -> None:
        if self._embedded_factory is not None:
            load_url = (
                self._build_bridge_or_target_url()
                if _is_flet_webview_platform(self._page)
                else self._url
            )
            custom = self._embedded_factory(load_url)
            if custom is not None:
                self._nav_progress.visible = False
                self._body_host.content = ft.Container(expand=True, content=custom)
                return

        if _is_flet_webview_platform(self._page):
            try:
                import flet_webview as fwv

                load_url = self._build_bridge_or_target_url()
                wv = fwv.WebView(
                    url=load_url,
                    expand=True,
                    on_page_started=lambda _e: self._set_nav_loading(True),
                    on_page_ended=lambda _e: self._set_nav_loading(False),
                    on_url_change=lambda e: self._update_subtitle(str(getattr(e, "data", "") or "")),
                    on_web_resource_error=lambda e: (
                        self._set_nav_loading(False),
                        logger.debug("flet_webview resource error: %s", getattr(e, "data", "")),
                    ),
                )
                self._flet_webview = wv
                self._body_host.content = ft.Container(expand=True, content=wv)
                return
            except Exception as exc:
                logger.debug("flet_webview unavailable, falling back: %s", exc)

        if isinstance(self._page, ft.Page) and _webview2_runtime_available():
            target_host = (urlsplit(self._url or "").hostname or "").lower()
            is_uth_site = target_host == "ut.edu.vn" or target_host.endswith(".ut.edu.vn")
            cookie_provider: Optional[Callable[[], list[dict[str, Any]]]] = None
            if (
                is_uth_site
                and self._session_client is not None
                and hasattr(self._session_client, "get_session_cookies")
            ):
                client_ref = self._session_client

                def _provide_cookies() -> list[dict[str, Any]]:
                    return client_ref.get_session_cookies(ensure_login=True)

                cookie_provider = _provide_cookies
            dock = WindowsWebView2Dock(
                page_title=str(getattr(self._page, "title", "") or "UTHelper"),
                url=self._url,
                cookies=[],
                cookie_provider=cookie_provider,
                on_title_changed=self._on_native_title,
                on_url_changed=self._on_native_url,
                on_loading_changed=self._on_webview2_loading_changed,
                on_external_url=self._on_open_external,
                on_ready_failed=self._on_webview2_failed,
            )
            self._dock = dock
            dock.start()
            self._nav_progress.visible = True
            self._body_host.content = ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                padding=24,
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=10,
                    controls=[
                        ft.ProgressRing(width=28, height=28, stroke_width=3, color=C.ACCENT),
                        ft.Text(
                            "Đang khởi tạo trình duyệt ngay trong ứng dụng...",
                            size=13,
                            color=C.TEXT_SECONDARY,
                            text_align=ft.TextAlign.CENTER,
                        ),
                    ],
                ),
            )
            return

        self._show_html_fallback_or_card()

    def _set_nav_loading(self, loading: bool) -> None:
        if self._disposed:
            return
        self._nav_progress.visible = bool(loading)
        safe_control_update(self._nav_progress)

    def _on_webview2_loading_changed(self, loading: bool) -> None:
        if self._disposed:
            return
        self._nav_progress.visible = bool(loading)
        if not loading and self._dock is not None:
            self._body_host.content = ft.Container(expand=True, bgcolor=C.BG)
            safe_control_update(self)
        else:
            safe_control_update(self._nav_progress)

    def _on_native_title(self, title: str) -> None:
        if title and not self._disposed:
            self._title_text.value = title
            safe_control_update(self._title_text)

    def _on_native_url(self, url: str) -> None:
        if url and not self._disposed:
            self._url = url
            self._subtitle_text.value = self._short_subtitle(url)
            safe_control_update(self._subtitle_text)

    def _update_subtitle(self, url: str) -> None:
        if url and "/session/" not in url:
            self._subtitle_text.value = self._short_subtitle(url)
            safe_control_update(self._subtitle_text)

    def _on_webview2_failed(self, _reason: str) -> None:
        if self._disposed:
            return
        self._dock = None
        self._show_html_fallback_or_card()
        safe_control_update(self)

    def _show_html_fallback_or_card(self) -> None:
        self._body_host.content = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            padding=24,
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=10,
                controls=[
                    ft.ProgressRing(width=24, height=24, stroke_width=2.5),
                    ft.Text(
                        "Đang tải nội dung hoạt động qua phiên Moodle...",
                        size=13,
                        color=C.TEXT_SECONDARY,
                    ),
                ],
            ),
        )
        if not self._fallback_html_loaded:
            self._fallback_html_loaded = True
            threading.Thread(
                target=self._load_authenticated_html_fallback,
                name="CoursesWebViewHtmlFallback",
                daemon=True,
            ).start()

    def _load_authenticated_html_fallback(self) -> None:
        markdown_text = ""
        extracted_links: list[tuple[str, str]] = []
        error_text = ""
        target_host = (urlsplit(self._url or "").hostname or "").lower()
        is_uth_site = target_host == "ut.edu.vn" or target_host.endswith(".ut.edu.vn")
        is_local_preview = target_host in {"127.0.0.1", "localhost"}
        html_text = ""
        try:
            if is_uth_site and self._session_client is not None and hasattr(
                self._session_client, "open_session_url"
            ):
                _final_url, html_text, redirected = self._session_client.open_session_url(
                    self._url
                )
                if redirected:
                    error_text = "Liên kết điều hướng ra ngoài hệ thống UTH."
            elif is_local_preview and self._url.startswith(("http://", "https://")):
                import urllib.request

                req = urllib.request.Request(
                    self._url,
                    headers={"User-Agent": "UTHelper/2.5"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310
                    raw_bytes = resp.read(512 * 1024)
                    html_text = raw_bytes.decode("utf-8", errors="replace")
            elif not is_uth_site:
                error_text = (
                    "Liên kết ngoài hệ thống yêu cầu trình duyệt tương tác trực tiếp (WebView2 / trình duyệt)."
                )

            if html_text and not error_text:
                raw_soup = BeautifulSoup(html_text, BS4_PARSER)
                for junk_tag in list(raw_soup.find_all(_NON_READABLE_HTML_TAGS)):
                    if not getattr(junk_tag, "decomposed", False):
                        junk_tag.decompose()
                HTMLSanitizer.sanitize_soup(raw_soup)
                main = (
                    raw_soup.select_one("#region-main")
                    or raw_soup.select_one("main")
                    or raw_soup.body
                    or raw_soup
                )
                lines: list[str] = []
                for el in main.find_all(["h1", "h2", "h3", "p", "li"]):
                    txt = " ".join(el.get_text(" ", strip=True).split())
                    if txt:
                        prefix = "## " if el.name in ("h1", "h2", "h3") else ("- " if el.name == "li" else "")
                        lines.append(f"{prefix}{txt}")
                markdown_text = "\n\n".join(lines[:140])
                for anchor in main.select("a[href]"):
                    href = str(anchor.get("href") or "").strip()
                    label = " ".join(anchor.get_text(" ", strip=True).split())
                    if label and href.startswith(("http://", "https://")) and len(extracted_links) < 12:
                        extracted_links.append((label, href))
        except Exception as exc:
            error_text = str(exc)

        if self._disposed:
            return

        self._nav_progress.visible = False
        safe_control_update(self._nav_progress)

        if error_text or not markdown_text:
            self._body_host.content = ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                padding=24,
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=12,
                    controls=[
                        ft.Icon(ft.Icons.LANGUAGE_ROUNDED, size=44, color=C.ACCENT),
                        ft.Text(
                            self._title,
                            size=16,
                            weight=ft.FontWeight.W_600,
                            color=C.TEXT_PRIMARY,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ft.Text(
                            error_text
                            or "Hoạt động này yêu cầu trình duyệt tương tác trực tiếp trên thiết bị.",
                            size=13,
                            color=C.TEXT_SECONDARY,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ft.FilledButton(
                            "Mở bằng trình duyệt ngoài",
                            icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                            on_click=lambda _e: self._on_open_external(self._url),
                        ),
                    ],
                ),
            )
            safe_control_update(self._body_host)
            return

        controls: list[ft.Control] = [
            ft.Markdown(
                value=markdown_text,
                selectable=True,
                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                on_tap_link=lambda e: self._navigate_link(str(getattr(e, "data", "") or "")),
            )
        ]
        if extracted_links:
            controls.append(ft.Divider(height=16, color=C.BORDER))
            controls.append(
                ft.Text(
                    "Liên kết nhanh trong trang",
                    size=11,
                    weight=ft.FontWeight.W_600,
                    color=C.TEXT_SECONDARY,
                )
            )
            controls.append(
                ft.Row(
                    wrap=True,
                    spacing=8,
                    run_spacing=8,
                    controls=[
                        ft.OutlinedButton(
                            label[:40],
                            on_click=lambda _e, target=href: self._navigate_link(target),
                        )
                        for label, href in extracted_links
                    ],
                )
            )
        self._body_host.content = ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=20, vertical=16),
            content=ft.ListView(expand=True, spacing=8, controls=controls),
        )
        safe_control_update(self._body_host)

    def _navigate_link(self, target_url: str) -> None:
        clean = str(target_url or "").strip()
        if not clean:
            return
        self._url = clean
        self._subtitle_text.value = self._short_subtitle(clean)
        safe_control_update(self._subtitle_text)
        if self._dock is not None:
            self._set_nav_loading(True)
            self._dock.navigate(clean)
        elif self._flet_webview is not None:
            self._mount_engine()
            safe_control_update(self._body_host)
        else:
            self._fallback_html_loaded = False
            self._show_html_fallback_or_card()
            safe_control_update(self._body_host)

    def _on_webview_back(self, _e: Any) -> None:
        if self._dock is not None:
            self._dock.go_back()

    def _on_webview_forward(self, _e: Any) -> None:
        if self._dock is not None:
            self._dock.go_forward()

    def _on_webview_reload(self, _e: Any) -> None:
        if self._dock is not None:
            self._dock.reload()
        elif self._flet_webview is not None:
            self._mount_engine()
            safe_control_update(self._body_host)
        else:
            self._fallback_html_loaded = False
            self._show_html_fallback_or_card()
            safe_control_update(self._body_host)

    def _handle_back(self) -> None:
        self.dispose()
        self._on_back()

    def set_expanded(self, expanded: bool, *, visible: bool = True) -> None:
        self._expanded = bool(expanded)
        self._show_expand_toggle = bool(visible)
        self._expand_btn.icon = (
            ft.Icons.CLOSE_FULLSCREEN_ROUNDED
            if self._expanded
            else ft.Icons.OPEN_IN_FULL_ROUNDED
        )
        self._expand_btn.tooltip = (
            "Thu về khung nội dung (Ctrl+\\)"
            if self._expanded
            else "Mở rộng toàn vùng môn học (Ctrl+\\)"
        )
        self._expand_btn.visible = self._show_expand_toggle
        safe_control_update(self._expand_btn)

    def sync_bounds(self, left: float, top: float, width: float, height: float) -> None:
        if self._dock is not None and not self._disposed:
            header_h = 58.0
            self._dock.set_bounds(left, top + header_h, width, max(160.0, height - header_h))

    def suspend(self) -> None:
        if self._dock is not None:
            self._dock.set_visible(False)

    def resume(self) -> None:
        if self._dock is not None and not self._disposed:
            self._dock.set_visible(True)

    def dispose(self) -> None:
        self._disposed = True
        if self._dock is not None:
            self._dock.dispose()
            self._dock = None

    def update_theme(self) -> None:
        self.bgcolor = C.BG
        self._body_host.bgcolor = C.BG
        self._nav_progress.color = C.ACCENT
        self._nav_progress.bgcolor = C.BORDER
        refresh_viewer_header(
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        )
        for btn in (
            self._wv_back_btn,
            self._wv_fwd_btn,
            self._wv_reload_btn,
            self._expand_btn,
            self._ext_btn,
        ):
            btn.icon_color = C.TEXT_PRIMARY
        safe_control_update(self)
