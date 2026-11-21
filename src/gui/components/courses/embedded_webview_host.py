"""Unified cross-platform embedded WebView host (flet_webview + Windows WebView2)."""

from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes
import inspect
import logging
import os
from pathlib import Path
import queue
import secrets
import shutil
import sys
import threading
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import flet as ft

import platform_utils
from gui.components.courses._shared import safe_control_update
from gui.core.theme import C
from gui.view_models.courses_state import safe_http_url

logger = logging.getLogger(__name__)

_UTH_MSG_PREFIX = "__UTH_MSG__:"
_DANGEROUS_WEBVIEW_SCHEMES = frozenset({"file", "javascript", "data", "intent", "vbscript", "ms-appx"})


def _extract_loopback_port(url: str) -> Optional[int]:
    try:
        parsed = urlsplit(str(url or "").strip())
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() != "http":
        return None
    host = (parsed.hostname or "").rstrip(".").casefold()
    if host not in ("127.0.0.1", "localhost") or port is None or not (1 <= port <= 65535):
        return None
    return int(port)


def _validate_webview_url(
    url: object,
    *,
    allowed_loopback_ports: Optional[set[int]] = None,
    allow_any_loopback: bool = False,
) -> str:
    """Validate an embedded WebView URL against strict HTTPS or active loopback rules."""
    cleaned = safe_http_url(url, allow_loopback=True)
    if not cleaned:
        return ""
    port = _extract_loopback_port(cleaned)
    if port is not None:
        if allow_any_loopback:
            return cleaned
        if allowed_loopback_ports is not None and port in allowed_loopback_ports:
            return cleaned
        return ""
    return safe_http_url(cleaned, allow_loopback=False)


def _sweep_stale_profiles(profile_root: Path, *, active_profile: str) -> None:
    """Best-effort cleanup of stale ``uth*`` per-pane WebView2 profile directories."""
    for base in (profile_root, profile_root / "EBWebView"):
        if not base.is_dir():
            continue
        try:
            for entry in base.iterdir():
                if not entry.is_dir():
                    continue
                name = entry.name
                if not name.startswith("uth") or name == active_profile:
                    continue
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def _safe_control_page(control: Any) -> Any:
    """Return ``control.page`` safely without raising when unmounted in Flet 0.86.5."""
    if control is None:
        return None
    try:
        return control.page
    except RuntimeError:
        return None


def _is_flet_webview_platform(page: Optional[ft.Page]) -> bool:
    """Return True only when the active runtime supports ``flet_webview.WebView``."""
    if bool(getattr(page, "web", False)):
        return False
    platform_val = str(getattr(page, "platform", "") or "").lower()
    if any(p in platform_val for p in ("android", "ios", "macos")):
        return True
    if page is None and (
        platform_utils.IS_ANDROID
        or platform_utils.IS_IOS
        or sys.platform == "darwin"
    ):
        return True
    return False


def _is_windows_webview2_platform(page: Optional[ft.Page]) -> bool:
    """Return True on Windows when a non-web target Page can host WebView2."""
    if sys.platform != "win32" or page is None or bool(getattr(page, "web", False)):
        return False
    plat_str = str(getattr(page, "platform", "") or "").lower()
    if plat_str and "windows" not in plat_str:
        return False
    return True


def _webview2_runtime_available() -> bool:
    """Return True on Windows when Edge WebView2 runtime and Win32 bindings exist."""
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


class WindowsWebView2Dock:
    """Owned borderless WebView2 host window synced over Pane 3 on Windows Desktop."""

    _class_registered = False
    _class_name = "UTHelperCoursesWebView2Host"

    def __init__(
        self,
        *,
        page_title: str,
        url: str = "",
        initial_html: Optional[str] = None,
        cookies: Optional[list[dict[str, Any]]] = None,
        cookie_provider: Optional[Callable[[], list[dict[str, Any]]]] = None,
        on_title_changed: Optional[Callable[[str], None]] = None,
        on_url_changed: Optional[Callable[[str], None]] = None,
        on_loading_changed: Optional[Callable[[bool], None]] = None,
        on_external_url: Optional[Callable[[str], None]] = None,
        on_ready_failed: Optional[Callable[[str], None]] = None,
        on_ready: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_message: Optional[Callable[[str], None]] = None,
        on_history_changed: Optional[Callable[[bool, bool], None]] = None,
    ) -> None:
        self._page_title = page_title or "UTHelper"
        self._profile_name = f"uth{secrets.token_hex(12)}"
        self._url = url
        self._html: Optional[str] = initial_html
        self._cookies = list(cookies or ())
        self._cookie_provider = cookie_provider
        self._on_title_changed = on_title_changed
        self._on_url_changed = on_url_changed
        self._on_loading_changed = on_loading_changed
        self._on_external_url = on_external_url
        self._on_ready_failed = on_ready_failed
        self._on_ready = on_ready
        self._on_error = on_error
        self._on_message = on_message
        self._on_history_changed = on_history_changed

        self._cmd_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._ready = False
        self._failed = False
        self._visible = True
        self._can_go_back = False
        self._can_go_forward = False
        self._pending_scripts: list[tuple[str, Any, Any]] = []
        self._allowed_loopback_ports: set[int] = set()
        init_loop_port = _extract_loopback_port(url)
        if init_loop_port is not None:
            self._allowed_loopback_ports.add(init_loop_port)
        self._bounds_dip: tuple[float, float, float, float] = (410.0, 90.0, 640.0, 480.0)
        self._bottom_inset_dip: float = 0.0
        self._hwnd: int = 0
        self._parent_hwnd: int = 0
        self._controller: Any = None
        self._core: Any = None
        self._thread: Optional[threading.Thread] = None
        self._thread_id: int = 0
        self._hwnd_retries: int = 15

    def start(self) -> None:
        if self._thread is not None or sys.platform != "win32":
            return
        self._thread = threading.Thread(
            target=self._run_sta_thread,
            name="CoursesWebView2STA",
            daemon=True,
        )
        self._thread.start()

    def _is_viewer_locked(self) -> bool:
        if self._html is not None:
            return True
        if not self._url:
            return False
        try:
            return urlsplit(self._url).path.startswith("/viewer/")
        except ValueError:
            return False

    def _viewer_prefix(self) -> str:
        if not self._url:
            return ""
        try:
            parts = urlsplit(self._url)
            segs = [s for s in parts.path.split("/") if s]
            if len(segs) >= 2 and segs[0] == "viewer":
                return f"{parts.scheme.lower()}://{parts.netloc.lower()}/viewer/{segs[1]}/"
        except ValueError:
            pass
        return ""

    def _is_navigation_uri_allowed(self, uri: str) -> bool:
        raw = str(uri or "").strip()
        if not raw:
            return True
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return False
        scheme = parsed.scheme.lower()
        if scheme in _DANGEROUS_WEBVIEW_SCHEMES:
            return False
        if self._is_viewer_locked():
            if raw.lower() == "about:blank":
                return True
            if scheme == "blob":
                return True
            prefix = self._viewer_prefix()
            if not prefix or "\\" in raw or ".." in parsed.path.split("/"):
                return False
            normalized_target = f"{scheme}://{parsed.netloc.lower()}{parsed.path}"
            return normalized_target.startswith(prefix)
        return bool(
            _validate_webview_url(
                raw,
                allowed_loopback_ports=self._allowed_loopback_ports,
            )
        )

    def navigate(self, url: str) -> None:
        safe_url = _validate_webview_url(
            url,
            allowed_loopback_ports=self._allowed_loopback_ports,
        )
        if not safe_url:
            return
        self._html = None
        self._url = safe_url
        self._enqueue("navigate", safe_url)

    def load_html(self, html: str) -> None:
        self._html = html
        self._url = ""
        self._enqueue("load_html", html)

    async def eval_js_async(self, script: str) -> None:
        if self._stop_event.is_set() or self._failed:
            return
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._enqueue("eval_js", (script, loop, fut))
        try:
            await fut
        except (asyncio.CancelledError, Exception):
            return

    def go_back(self) -> None:
        self._enqueue("go_back", None)

    def go_forward(self) -> None:
        self._enqueue("go_forward", None)

    def reload(self) -> None:
        self._enqueue("reload", None)

    def can_go_back(self) -> bool:
        return self._can_go_back

    def can_go_forward(self) -> bool:
        return self._can_go_forward

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)
        self._enqueue("visible", self._visible)

    def set_bounds(
        self,
        left: float,
        top: float,
        width: float,
        height: float,
        *,
        bottom_inset: float = 0.0,
    ) -> None:
        self._bounds_dip = (
            max(0.0, float(left)),
            max(0.0, float(top)),
            max(120.0, float(width)),
            max(120.0, float(height)),
        )
        self._bottom_inset_dip = max(0.0, float(bottom_inset))
        self._enqueue("bounds", self._bounds_dip)

    def dispose(self) -> None:
        with self._lock:
            if self._stop_event.is_set():
                return
            self._stop_event.set()
        self._resolve_pending_scripts()
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

    def _resolve_pending_scripts(self) -> None:
        with self._lock:
            pending = list(self._pending_scripts)
            self._pending_scripts.clear()
        for _script, loop, fut in pending:
            try:
                loop.call_soon_threadsafe(
                    lambda f=fut: None if f.done() else f.set_result(None)
                )
            except Exception:
                pass

    def _exec_js_on_core(self, script: str, loop: Any, fut: Any) -> None:
        if self._core is None:
            try:
                loop.call_soon_threadsafe(
                    lambda f=fut: None if f.done() else f.set_result(None)
                )
            except Exception:
                pass
            return
        try:
            op = self._core.execute_script_async(str(script))
            op.completed = lambda _op, _st, l=loop, f=fut: l.call_soon_threadsafe(
                lambda: None if f.done() else f.set_result(None)
            )
        except Exception:
            try:
                loop.call_soon_threadsafe(
                    lambda f=fut: None if f.done() else f.set_result(None)
                )
            except Exception:
                pass

    @staticmethod
    def _collect_child_pids(parent_pid: int) -> set[int]:
        """Collect parent + all descendant process IDs on Windows (python.exe <-> flet.exe)."""
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
            k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
            k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)
            if snap in (0, -1, ctypes.c_void_p(-1).value):
                return pids
            parent_map: dict[int, int] = {}
            children_map: dict[int, list[int]] = {}
            try:
                entry = _PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
                if k32.Process32FirstW(snap, ctypes.byref(entry)):
                    while True:
                        pid = int(entry.th32ProcessID)
                        ppid = int(entry.th32ParentProcessID)
                        parent_map[pid] = ppid
                        children_map.setdefault(ppid, []).append(pid)
                        if not k32.Process32NextW(snap, ctypes.byref(entry)):
                            break
            finally:
                k32.CloseHandle(snap)
            if parent_pid in parent_map and parent_map[parent_pid] > 0:
                pids.add(parent_map[parent_pid])
            frontier = [parent_pid]
            while frontier:
                cur = frontier.pop()
                for child in children_map.get(cur, ()):
                    if child not in pids:
                        pids.add(child)
                        frontier.append(child)
        except Exception:
            pass
        return pids

    @staticmethod
    def _find_parent_hwnd(expected_title: str) -> int:
        import win32gui
        import win32process

        cur_pid = os.getpid()
        family_pids = WindowsWebView2Dock._collect_child_pids(cur_pid)
        matched: list[tuple[bool, int]] = []

        def _enum_cb(hwnd: int, _ctx: Any) -> bool:
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                title = win32gui.GetWindowText(hwnd) or ""
                cls_name = win32gui.GetClassName(hwnd) or ""
                if cls_name != "FLUTTER_RUNNER_WIN32_WINDOW":
                    return True
                if not expected_title or title.casefold() != expected_title.casefold():
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                matched.append((pid in family_pids, hwnd))
            except Exception:
                pass
            return True

        win32gui.EnumWindows(_enum_cb, None)
        if not matched:
            return 0
        owned = [hwnd for related, hwnd in matched if related]
        if owned:
            return owned[0] if len(owned) == 1 else 0
        # Detached Flet clients can be outside the Python process tree. A unique
        # exact-title Flet window is still valid; unrelated/ambiguous windows are
        # never a fallback while the requested window is being created.
        return matched[0][1] if len(matched) == 1 else 0

    def _run_sta_thread(self) -> None:
        try:
            ctypes.oledll.ole32.CoInitializeEx(None, 2)
        except Exception:
            pass
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        try:
            self._init_window_and_webview2()
            if not self._failed and not self._stop_event.is_set():
                self._pump_messages()
        except Exception as exc:
            logger.warning("WindowsWebView2Dock STA failed: %s", exc)
            self._fail(str(exc))
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

        retries = max(1, int(getattr(self, "_hwnd_retries", 15)))
        for attempt in range(retries):
            self._parent_hwnd = self._find_parent_hwnd(self._page_title)
            if self._parent_hwnd:
                break
            if attempt + 1 < retries:
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
        try:
            user32 = ctypes.windll.user32
            user32.SetWindowLongPtrW.argtypes = [
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_void_p,
            ]
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW(
                int(hwnd), -8, ctypes.c_void_p(int(self._parent_hwnd))
            )
        except Exception:
            pass
        self._hwnd = hwnd
        self._apply_window_bounds()

        local_app = os.environ.get("LOCALAPPDATA") or str(Path.home())
        profile_root = Path(local_app) / "UTHelper" / "webview2_profile"
        profile_root.mkdir(parents=True, exist_ok=True)
        _sweep_stale_profiles(profile_root, active_profile=self._profile_name)
        user_data_dir = str(profile_root)

        env_op = wv2.CoreWebView2Environment.create_with_options_async(
            "", user_data_dir, None
        )

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
            ctrl_op = self._create_private_controller(env, win_ref)
            ctrl_op.completed = lambda cop, st: self._enqueue("_ctrl_done", (cop, st))
        except Exception as exc:
            self._fail(str(exc))

    def _create_private_controller(self, environment: Any, window_ref: Any) -> Any:
        options = environment.create_core_webview2_controller_options()
        options.profile_name = self._profile_name
        options.is_in_private_mode_enabled = True
        return environment.create_core_webview2_controller_async(window_ref, options)

    def _handle_ctrl_done(self, op: Any) -> None:
        if self._stop_event.is_set() or not self._hwnd:
            return
        ctrl: Any = None
        try:
            ctrl = op.get_results()
            if self._stop_event.is_set() or not self._hwnd:
                try:
                    ctrl.close()
                except Exception:
                    pass
                return
            core = ctrl.core_webview2
            self._controller = ctrl
            self._core = core
            try:
                settings_obj = getattr(core, "settings", None)
                if settings_obj is not None:
                    settings_obj.is_status_bar_enabled = False
                    settings_obj.are_dev_tools_enabled = False
                    settings_obj.are_host_objects_allowed = False
            except Exception:
                pass
            if not self._cookies and callable(self._cookie_provider):
                try:
                    self._cookies = list(self._cookie_provider() or ())
                except Exception as exc:
                    logger.debug("WebView2 async cookie provider failed: %s", exc)
                    self._fail("Không thể khôi phục phiên đăng nhập. Hãy đăng nhập lại.")
                    return
            if self._stop_event.is_set():
                try:
                    ctrl.close()
                except Exception:
                    pass
                self._controller = None
                self._core = None
                return
            self._inject_cookies(core)
            if self._stop_event.is_set():
                try:
                    ctrl.close()
                except Exception:
                    pass
                self._controller = None
                self._core = None
                return
            self._ready = True
            self._bind_events(core)
            self._apply_window_bounds()
            ctrl.is_visible = self._visible
            if self._visible:
                import win32con
                import win32gui

                win32gui.ShowWindow(self._hwnd, win32con.SW_SHOWNOACTIVATE)
            if self._html is not None:
                core.navigate_to_string(self._html)
            elif self._url:
                core.navigate(self._url)
            with self._lock:
                queued = list(self._pending_scripts)
                self._pending_scripts.clear()
            for script, loop, fut in queued:
                self._exec_js_on_core(script, loop, fut)
        except Exception as exc:
            self._fail(str(exc))

    def _inject_cookies(self, core: Any) -> None:
        cm = getattr(core, "cookie_manager", None)
        if cm is None:
            return
        for ck in self._cookies:
            if self._stop_event.is_set():
                return
            name = str(ck.get("name") or "")
            value = str(ck.get("value") or "")
            domain = str(ck.get("domain") or "")
            path = str(ck.get("path") or "/")
            if not name or not domain:
                continue
            try:
                cookie_obj = cm.create_cookie(name, value, domain, path)
                try:
                    is_https = str(self._url or "").lower().startswith("https://")
                    cookie_obj.is_secure = bool(ck.get("secure", False)) or is_https
                    cookie_obj.is_http_only = bool(ck.get("httpOnly", False))
                except Exception:
                    pass
                cm.add_or_update_cookie(cookie_obj)
            except Exception as exc:
                logger.debug("WebView2 cookie injection skipped for %s: %s", domain, exc)

    def _update_history_state(self, core: Any) -> None:
        self._can_go_back = bool(getattr(core, "can_go_back", False))
        self._can_go_forward = bool(getattr(core, "can_go_forward", False))
        if self._on_history_changed is not None:
            try:
                self._on_history_changed(self._can_go_back, self._can_go_forward)
            except Exception:
                pass

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

            def _on_nav_starting(_sender: Any, args: Any) -> None:
                uri = str(getattr(args, "uri", "") or "")
                if uri and not self._is_navigation_uri_allowed(uri):
                    try:
                        args.cancel = True
                    except Exception:
                        pass
                    return
                if self._on_loading_changed:
                    self._on_loading_changed(True)

            core.add_navigation_starting(_on_nav_starting)

            def _on_nav_completed(_sender: Any, args: Any) -> None:
                if self._on_loading_changed:
                    self._on_loading_changed(False)
                self._update_history_state(core)
                if getattr(args, "is_success", True):
                    if self._on_ready is not None:
                        self._on_ready()
                else:
                    if self._on_error is not None:
                        self._on_error(
                            f"Navigation failed: {getattr(args, 'web_error_status', '')}"
                        )

            core.add_navigation_completed(_on_nav_completed)
            if hasattr(core, "add_history_changed"):
                core.add_history_changed(
                    lambda _s, _a: self._update_history_state(core)
                )
            if hasattr(core, "add_web_message_received"):

                def _on_web_msg(_sender: Any, args: Any) -> None:
                    if self._on_message is None:
                        return
                    msg: Optional[str] = None
                    try:
                        msg = args.try_get_web_message_as_string()
                    except Exception:
                        msg = None
                    if msg is None:
                        raw_json = getattr(args, "web_message_as_json", None)
                        if raw_json is not None:
                            msg = str(raw_json)
                    if msg is not None:
                        self._on_message(str(msg))

                core.add_web_message_received(_on_web_msg)

            def _on_new_window(_sender: Any, args: Any) -> None:
                try:
                    args.handled = True
                except Exception:
                    pass
                if self._is_viewer_locked():
                    return
                uri = _validate_webview_url(
                    str(getattr(args, "uri", "") or ""),
                    allowed_loopback_ports=self._allowed_loopback_ports,
                )
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
            if (
                win32gui.IsIconic(parent)
                or not win32gui.IsWindowVisible(parent)
                or not self._visible
            ):
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
            bot_px = max(0, int(round(self._bottom_inset_dip * scale)))
            w_px = max(160, max_w - left_px)
            h_px = max(160, max_h - top_px - bot_px)
            fg_hwnd = 0
            try:
                fg_hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
            except Exception:
                fg_hwnd = 0
            app_is_fg = fg_hwnd in (int(parent), int(self._hwnd))
            keep_z = preserve_zorder or not app_is_fg
            flags = win32con.SWP_NOACTIVATE | (
                win32con.SWP_NOZORDER if keep_z else 0
            )
            win32gui.SetWindowPos(
                self._hwnd,
                0 if keep_z else win32con.HWND_TOP,
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
        self._resolve_pending_scripts()
        if self._on_ready_failed is not None:
            self._on_ready_failed(reason)
        elif self._on_error is not None:
            self._on_error(reason)

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
                    cur_state = (
                        cur_rect[0],
                        cur_rect[1],
                        cur_rect[2],
                        cur_rect[3],
                        is_min,
                    )
                    if cur_state != last_parent_state:
                        last_parent_state = cur_state
                        self._apply_window_bounds(preserve_zorder=True)
                except Exception:
                    pass
            while ctypes.windll.user32.PeekMessageW(
                ctypes.byref(msg), 0, 0, 0, win32con.PM_REMOVE
            ):
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
                nav_url = str(payload)
                if self._is_navigation_uri_allowed(nav_url):
                    self._core.navigate(nav_url)
            elif action == "load_html" and self._core is not None:
                self._core.navigate_to_string(str(payload or ""))
            elif action == "eval_js":
                script, loop, fut = payload
                if self._core is None:
                    enqueued = False
                    with self._lock:
                        if not self._stop_event.is_set() and not self._failed:
                            self._pending_scripts.append((script, loop, fut))
                            enqueued = True
                    if not enqueued:
                        try:
                            loop.call_soon_threadsafe(
                                lambda f=fut: None if f.done() else f.set_result(None)
                            )
                        except Exception:
                            pass
                else:
                    self._exec_js_on_core(script, loop, fut)
            elif (
                action == "go_back"
                and self._core is not None
                and getattr(self._core, "can_go_back", False)
            ):
                self._core.go_back()
            elif (
                action == "go_forward"
                and self._core is not None
                and getattr(self._core, "can_go_forward", False)
            ):
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
        self._resolve_pending_scripts()
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
        try:
            local_app = os.environ.get("LOCALAPPDATA") or str(Path.home())
            profile_root = Path(local_app) / "UTHelper" / "webview2_profile"
            for candidate in (profile_root / self._profile_name, profile_root / "EBWebView" / self._profile_name):
                if candidate.is_dir():
                    shutil.rmtree(candidate, ignore_errors=True)
        except Exception:
            pass


class _FletWebViewSlot(ft.Container):
    """Lifecycle-aware container flushing deferred HTML once mounted on a Page."""

    def __init__(
        self,
        *,
        webview: ft.Control,
        on_mount: Callable[[], None],
        bgcolor: Optional[str] = None,
    ) -> None:
        super().__init__(expand=True, bgcolor=bgcolor or C.BG, content=webview)
        self._on_mount_cb = on_mount

    def did_mount(self) -> None:
        if callable(self._on_mount_cb):
            self._on_mount_cb()


class EmbeddedWebViewHost:
    """Unified inline WebView adapter across mobile/macOS (flet_webview) and Windows (WebView2)."""

    def __init__(
        self,
        *,
        backend: str,
        page: Optional[ft.Page] = None,
        url: str = "",
        html: Optional[str] = None,
        webview_control: Any = None,
        dock: Optional[WindowsWebView2Dock] = None,
        on_ready: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_message: Optional[Callable[[str], None]] = None,
        on_url_changed: Optional[Callable[[str], None]] = None,
        on_title_changed: Optional[Callable[[str], None]] = None,
        on_loading_changed: Optional[Callable[[bool], None]] = None,
        on_history_changed: Optional[Callable[[bool, bool], None]] = None,
    ) -> None:
        self._backend = backend
        self._page = page
        self._url = url
        self._allowed_loopback_ports: set[int] = set()
        init_loop_port = _extract_loopback_port(url)
        if init_loop_port is not None:
            self._allowed_loopback_ports.add(init_loop_port)
        self._wv = webview_control
        self._dock = dock
        self._on_ready = on_ready
        self._on_error = on_error
        self._on_message = on_message
        self._on_url_changed = on_url_changed
        self._on_title_changed = on_title_changed
        self._on_loading_changed = on_loading_changed
        self._on_history_changed = on_history_changed

        self._pending_html: Optional[tuple[str, Optional[str]]] = (
            (html, None) if (backend == "flet_webview" and html is not None) else None
        )
        self._can_go_back = False
        self._can_go_forward = False
        self._suspended = False
        self._disposed = False
        self._last_bounds: Optional[tuple[float, float, float, float, float]] = None

        self._loading_ring: Optional[ft.ProgressRing] = None
        self._loading_text: Optional[ft.Text] = None
        self._slot: ft.Container
        if backend == "flet_webview":
            self._slot = _FletWebViewSlot(
                webview=webview_control,
                on_mount=self._on_flet_mount,
                bgcolor=C.BG,
            )
        else:
            self._loading_ring = ft.ProgressRing(
                width=28, height=28, stroke_width=3, color=C.ACCENT
            )
            self._loading_text = ft.Text(
                "Đang khởi tạo trình duyệt ngay trong ứng dụng...",
                size=13,
                color=C.TEXT_SECONDARY,
                text_align=ft.TextAlign.CENTER,
            )
            self._slot = ft.Container(
                expand=True,
                bgcolor=C.BG,
                alignment=ft.Alignment.CENTER,
                padding=24,
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=10,
                    controls=[self._loading_ring, self._loading_text],
                ),
            )

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def dock(self) -> Optional[WindowsWebView2Dock]:
        return self._dock

    def control(self) -> ft.Control:
        return self._slot

    def _schedule_task(self, coro_fn: Callable[[], Any]) -> None:
        if self._disposed:
            return
        page = (
            _safe_control_page(self._wv)
            or _safe_control_page(self._slot)
            or self._page
        )
        run_task = getattr(page, "run_task", None) if page is not None else None
        if callable(run_task):
            try:
                res = run_task(coro_fn)
                if inspect.isawaitable(res):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)  # type: ignore[arg-type]
                    except RuntimeError:
                        pass
                return
            except Exception:
                pass
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro_fn())
        except RuntimeError:
            pass

    def _on_flet_mount(self) -> None:
        if self._disposed:
            return
        if self._pending_html is not None:
            html, base_url = self._pending_html
            self._pending_html = None

            async def _flush_html() -> None:
                if self._wv is not None and not self._disposed:
                    try:
                        await self._wv.load_html(html, base_url=base_url)
                    except Exception:
                        pass

            self._schedule_task(_flush_html)
        self._schedule_history_refresh()

    async def _refresh_history_async(self) -> None:
        if self._wv is None or self._disposed:
            return
        try:
            back = bool(await self._wv.can_go_back())
            fwd = bool(await self._wv.can_go_forward())
            self._can_go_back = back
            self._can_go_forward = fwd
            if self._on_history_changed is not None:
                self._on_history_changed(back, fwd)
        except Exception:
            pass

    def _schedule_history_refresh(self) -> None:
        if self._backend != "flet_webview" or self._wv is None or self._disposed:
            return
        self._schedule_task(self._refresh_history_async)

    def _handle_flet_page_started(self, e: Any) -> None:
        if self._disposed:
            return
        url = str(getattr(e, "data", "") or "")
        if url and not _validate_webview_url(
            url,
            allowed_loopback_ports=self._allowed_loopback_ports,
        ):
            if self._on_loading_changed is not None:
                self._on_loading_changed(False)
            if self._on_error is not None:
                self._on_error(f"Blocked disallowed WebView URL: {url}")
            return
        if self._on_loading_changed is not None:
            self._on_loading_changed(True)
        if url and self._on_url_changed is not None:
            self._on_url_changed(url)
        self._schedule_history_refresh()

    def _handle_flet_page_ended(self, e: Any) -> None:
        if self._disposed:
            return
        if self._on_loading_changed is not None:
            self._on_loading_changed(False)
        if self._on_ready is not None:
            self._on_ready()
        self._schedule_history_refresh()
        if self._on_title_changed is not None and self._wv is not None:

            async def _fetch_title() -> None:
                if self._wv is None or self._disposed or self._on_title_changed is None:
                    return
                try:
                    title = await self._wv.get_title()
                    if title:
                        self._on_title_changed(str(title))
                except Exception:
                    pass

            self._schedule_task(_fetch_title)

    def _handle_flet_error(self, e: Any) -> None:
        if self._disposed:
            return
        if self._on_loading_changed is not None:
            self._on_loading_changed(False)
        if self._on_error is not None:
            self._on_error(str(getattr(e, "data", "") or "WebView load error"))

    def _handle_flet_url_change(self, e: Any) -> None:
        if self._disposed:
            return
        url = str(getattr(e, "data", "") or "")
        if url and not _validate_webview_url(
            url,
            allowed_loopback_ports=self._allowed_loopback_ports,
        ):
            if self._on_error is not None:
                self._on_error(f"Blocked disallowed WebView URL: {url}")
            return
        if url:
            self._url = url
        if url and self._on_url_changed is not None:
            self._on_url_changed(url)
        self._schedule_history_refresh()

    def _handle_flet_console_message(self, e: Any) -> None:
        if self._disposed or self._on_message is None:
            return
        msg = str(getattr(e, "message", "") or "")
        if msg.startswith(_UTH_MSG_PREFIX):
            self._on_message(msg[len(_UTH_MSG_PREFIX) :])

    def _handle_webview2_loading(self, loading: bool) -> None:
        if self._disposed:
            return
        if not loading and self._dock is not None:
            self._slot.content = ft.Container(expand=True, bgcolor=C.BG)
            safe_control_update(self._slot)
        if self._on_loading_changed is not None:
            self._on_loading_changed(loading)

    def _handle_webview2_history(self, can_back: bool, can_forward: bool) -> None:
        self._can_go_back = bool(can_back)
        self._can_go_forward = bool(can_forward)
        if self._on_history_changed is not None:
            self._on_history_changed(self._can_go_back, self._can_go_forward)

    async def eval_js(self, script: str) -> None:
        if self._disposed or not script:
            return
        if self._backend == "flet_webview" and self._wv is not None:
            try:
                await self._wv.run_javascript(script)
            except Exception:
                pass
            return
        if self._backend == "webview2" and self._dock is not None:
            await self._dock.eval_js_async(script)

    def navigate(self, url: str) -> None:
        if self._disposed:
            return
        safe_url = _validate_webview_url(
            url,
            allowed_loopback_ports=self._allowed_loopback_ports,
        )
        if not safe_url:
            return
        self._url = safe_url
        if self._backend == "flet_webview" and self._wv is not None:
            self._pending_html = None
            self._wv.url = safe_url
            if _safe_control_page(self._wv) is not None or self._page is not None:

                async def _nav() -> None:
                    if self._wv is not None and not self._disposed:
                        try:
                            await self._wv.load_request(safe_url)
                        except Exception:
                            pass

                self._schedule_task(_nav)
            return
        if self._backend == "webview2" and self._dock is not None:
            self._dock.navigate(safe_url)

    def load_html(self, html: str, *, base_url: Optional[str] = None) -> None:
        if self._disposed:
            return
        if self._backend == "flet_webview" and self._wv is not None:
            if _safe_control_page(self._wv) is None:
                self._pending_html = (html, base_url)
                return

            async def _load() -> None:
                if self._wv is not None and not self._disposed:
                    try:
                        await self._wv.load_html(html, base_url=base_url)
                    except Exception:
                        pass

            self._schedule_task(_load)
            return
        if self._backend == "webview2" and self._dock is not None:
            self._dock.load_html(html)

    def go_back(self) -> None:
        if self._disposed:
            return
        if self._backend == "flet_webview" and self._wv is not None:

            async def _back() -> None:
                if self._wv is not None and not self._disposed:
                    try:
                        await self._wv.go_back()
                    except Exception:
                        pass
                    await self._refresh_history_async()

            self._schedule_task(_back)
            return
        if self._backend == "webview2" and self._dock is not None:
            self._dock.go_back()

    def go_forward(self) -> None:
        if self._disposed:
            return
        if self._backend == "flet_webview" and self._wv is not None:

            async def _fwd() -> None:
                if self._wv is not None and not self._disposed:
                    try:
                        await self._wv.go_forward()
                    except Exception:
                        pass
                    await self._refresh_history_async()

            self._schedule_task(_fwd)
            return
        if self._backend == "webview2" and self._dock is not None:
            self._dock.go_forward()

    def reload(self) -> None:
        if self._disposed:
            return
        if self._backend == "flet_webview" and self._wv is not None:

            async def _reload() -> None:
                if self._wv is not None and not self._disposed:
                    try:
                        await self._wv.reload()
                    except Exception:
                        pass

            self._schedule_task(_reload)
            return
        if self._backend == "webview2" and self._dock is not None:
            self._dock.reload()

    def can_go_back(self) -> bool:
        if self._backend == "webview2" and self._dock is not None:
            return self._dock.can_go_back()
        return self._can_go_back

    def can_go_forward(self) -> bool:
        if self._backend == "webview2" and self._dock is not None:
            return self._dock.can_go_forward()
        return self._can_go_forward

    def suspend(self) -> None:
        self._suspended = True
        if self._backend == "flet_webview":
            self._slot.visible = False
            safe_control_update(self._slot)
        elif self._dock is not None:
            self._dock.set_visible(False)

    def resume(self) -> None:
        self._suspended = False
        if self._disposed:
            return
        if self._backend == "flet_webview":
            self._slot.visible = True
            safe_control_update(self._slot)
        elif self._dock is not None:
            self._dock.set_visible(True)

    def sync_bounds(
        self,
        left: Optional[float] = None,
        top: Optional[float] = None,
        width: Optional[float] = None,
        height: Optional[float] = None,
        *,
        bottom_inset: float = 0.0,
    ) -> None:
        if (
            left is not None
            and top is not None
            and width is not None
            and height is not None
        ):
            self._last_bounds = (
                float(left),
                float(top),
                float(width),
                float(height),
                float(bottom_inset),
            )
        if (
            self._backend == "webview2"
            and self._dock is not None
            and not self._disposed
            and self._last_bounds is not None
        ):
            l_pos, t_pos, w_val, h_val, b_inset = self._last_bounds
            self._dock.set_bounds(
                l_pos, t_pos, w_val, h_val, bottom_inset=b_inset
            )

    def dispose(self) -> None:
        self._disposed = True
        self._pending_html = None
        if self._dock is not None:
            self._dock.dispose()
            self._dock = None

    def update_theme(self) -> None:
        self._slot.bgcolor = C.BG
        if self._wv is not None and hasattr(self._wv, "bgcolor"):
            self._wv.bgcolor = C.BG
        if self._loading_ring is not None:
            self._loading_ring.color = C.ACCENT
        if self._loading_text is not None:
            self._loading_text.color = C.TEXT_SECONDARY
        safe_control_update(self._slot)


def build_embedded_webview_host(
    url: str = "",
    *,
    html: Optional[str] = None,
    page: Optional[ft.Page] = None,
    bgcolor: Optional[str] = None,
    cookies: Optional[list[dict[str, Any]]] = None,
    cookie_provider: Optional[Callable[[], list[dict[str, Any]]]] = None,
    on_ready: Optional[Callable[[], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
    on_message: Optional[Callable[[str], None]] = None,
    on_url_changed: Optional[Callable[[str], None]] = None,
    on_title_changed: Optional[Callable[[str], None]] = None,
    on_loading_changed: Optional[Callable[[bool], None]] = None,
    on_history_changed: Optional[Callable[[bool, bool], None]] = None,
    on_external_url: Optional[Callable[[str], None]] = None,
) -> Optional[EmbeddedWebViewHost]:
    """Build a platform-appropriate ``EmbeddedWebViewHost``, or ``None`` if unsupported."""
    safe_url = ""
    if url:
        safe_url = _validate_webview_url(url, allow_any_loopback=True)
        if not safe_url:
            return None
    if not safe_url and html is None:
        return None

    if _is_flet_webview_platform(page):
        try:
            import flet_webview as fwv

            prevent_links = ["javascript:", "file:", "data:", "intent:"]
            if _extract_loopback_port(safe_url) is None:
                prevent_links.append("http://")
            holder: dict[str, EmbeddedWebViewHost] = {}
            wv = fwv.WebView(
                url=safe_url or None,
                prevent_links=prevent_links,
                bgcolor=bgcolor or C.BG,
                expand=True,
                on_page_started=lambda e: holder["host"]._handle_flet_page_started(e)
                if "host" in holder
                else None,
                on_page_ended=lambda e: holder["host"]._handle_flet_page_ended(e)
                if "host" in holder
                else None,
                on_web_resource_error=lambda e: holder["host"]._handle_flet_error(e)
                if "host" in holder
                else None,
                on_url_change=lambda e: holder["host"]._handle_flet_url_change(e)
                if "host" in holder
                else None,
                on_console_message=lambda e: holder["host"]._handle_flet_console_message(e)
                if "host" in holder
                else None,
            )
            host = EmbeddedWebViewHost(
                backend="flet_webview",
                page=page,
                url=safe_url,
                html=html,
                webview_control=wv,
                on_ready=on_ready,
                on_error=on_error,
                on_message=on_message,
                on_url_changed=on_url_changed,
                on_title_changed=on_title_changed,
                on_loading_changed=on_loading_changed,
                on_history_changed=on_history_changed,
            )
            holder["host"] = host
            return host
        except Exception as exc:
            logger.debug("flet_webview host initialization failed: %s", exc)
            return None

    if _is_windows_webview2_platform(page) and _webview2_runtime_available():
        holder_wv2: dict[str, EmbeddedWebViewHost] = {}
        dock = WindowsWebView2Dock(
            page_title=str(getattr(page, "title", "") or "UTHelper"),
            url=safe_url,
            initial_html=html,
            cookies=cookies,
            cookie_provider=cookie_provider,
            on_title_changed=on_title_changed,
            on_url_changed=on_url_changed,
            on_loading_changed=lambda loading: holder_wv2["host"]._handle_webview2_loading(
                loading
            )
            if "host" in holder_wv2
            else None,
            on_external_url=on_external_url,
            on_ready_failed=on_error,
            on_ready=on_ready,
            on_error=on_error,
            on_message=on_message,
            on_history_changed=lambda back, fwd: holder_wv2["host"]._handle_webview2_history(
                back, fwd
            )
            if "host" in holder_wv2
            else None,
        )
        host = EmbeddedWebViewHost(
            backend="webview2",
            page=page,
            url=safe_url,
            html=html,
            dock=dock,
            on_ready=on_ready,
            on_error=on_error,
            on_message=on_message,
            on_url_changed=on_url_changed,
            on_title_changed=on_title_changed,
            on_loading_changed=on_loading_changed,
            on_history_changed=on_history_changed,
        )
        holder_wv2["host"] = host
        dock._hwnd_retries = 50 if isinstance(page, ft.Page) else 1
        dock.start()
        return host

    return None
