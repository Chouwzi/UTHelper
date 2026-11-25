"""Unified cross-platform embedded WebView host (flet_webview + Windows WebView2)."""

from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import queue
import re
import shutil
import sys
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import urlsplit
import weakref

import flet as ft

import platform_utils
from gui.components.courses._shared import safe_control_update
from gui.core import utils as _gui_utils
from gui.core.theme import C
from gui.view_models.courses_state import safe_http_url

logger = logging.getLogger(__name__)

_UTH_MSG_PREFIX = "__UTH_MSG__:"
_DANGEROUS_WEBVIEW_SCHEMES = frozenset({"file", "javascript", "data", "intent", "vbscript", "ms-appx"})
_LOOPBACK_ITEM_PATH_RE = re.compile(r"^/[0-9a-f]{16}$")

_OCCLUSION_LOCK = threading.Lock()
_STACKED_OCCLUSION_REASONS: frozenset[str] = frozenset({"modal_dialog"})
_ACTIVE_OCCLUSION_REASONS: dict[str, int] = {}
_ACTIVE_OCCLUSION_TOKENS: set[tuple[str, str]] = set()
_ACTIVE_DOCKS: "weakref.WeakSet[WindowsWebView2Dock]" = weakref.WeakSet()


def is_globally_occluded() -> bool:
    """Return True when any modal/overlay surface requires native WebView2 HWNDs to hide."""
    with _OCCLUSION_LOCK:
        return bool(_ACTIVE_OCCLUSION_REASONS or _ACTIVE_OCCLUSION_TOKENS)


def set_global_webview_occlusion(
    reason: str,
    occluded: bool,
    *,
    token: Optional[str] = None,
) -> None:
    """Register or clear a global occlusion reason and hide/restore active WebView2 HWNDs.

    Stacked modal surfaces (``modal_dialog``) are reference-counted so closing an
    inner modal never prematurely un-occludes active docks while an outer modal
    remains open. Single-surface reasons (such as ``detail_view``, ``quiz_player``,
    ``webview_menu``) stay idempotent across repeated ``True`` calls so a single
    close always restores visibility. Callers may also pass an explicit ``token``
    to track overlapping instances idempotently. Unmatched ``occluded=False``
    calls clamp cleanly at zero.
    """
    key = str(reason or "").strip()
    if not key:
        return
    tok = str(token or "").strip() if token is not None else ""
    changed = False
    with _OCCLUSION_LOCK:
        was_occluded = bool(_ACTIVE_OCCLUSION_REASONS or _ACTIVE_OCCLUSION_TOKENS)
        if tok:
            if occluded:
                _ACTIVE_OCCLUSION_TOKENS.add((key, tok))
            else:
                _ACTIVE_OCCLUSION_TOKENS.discard((key, tok))
        elif key in _STACKED_OCCLUSION_REASONS:
            if occluded:
                _ACTIVE_OCCLUSION_REASONS[key] = _ACTIVE_OCCLUSION_REASONS.get(key, 0) + 1
            else:
                remaining = _ACTIVE_OCCLUSION_REASONS.get(key, 0) - 1
                if remaining > 0:
                    _ACTIVE_OCCLUSION_REASONS[key] = remaining
                else:
                    _ACTIVE_OCCLUSION_REASONS.pop(key, None)
        else:
            if occluded:
                _ACTIVE_OCCLUSION_REASONS[key] = 1
            else:
                _ACTIVE_OCCLUSION_REASONS.pop(key, None)
        now_occluded = bool(_ACTIVE_OCCLUSION_REASONS or _ACTIVE_OCCLUSION_TOKENS)
        changed = was_occluded != now_occluded
        docks = list(_ACTIVE_DOCKS) if changed else []
    for dock in docks:
        try:
            dock._notify_occlusion_changed()
        except Exception:
            pass


def clear_global_webview_occlusion() -> None:
    """Clear all active WebView2 occlusion reasons and restore visible docks."""
    with _OCCLUSION_LOCK:
        had_any = bool(_ACTIVE_OCCLUSION_REASONS or _ACTIVE_OCCLUSION_TOKENS)
        _ACTIVE_OCCLUSION_REASONS.clear()
        _ACTIVE_OCCLUSION_TOKENS.clear()
        docks = list(_ACTIVE_DOCKS) if had_any else []
    for dock in docks:
        try:
            dock._notify_occlusion_changed()
        except Exception:
            pass


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


def _is_demo_mode_active() -> bool:
    try:
        from config import settings
        from core.demo_sandbox import is_demo_account
    except ImportError:
        return False
    return bool(is_demo_account(getattr(settings, "UTH_USERNAME", "")))


def _is_demo_lms_blocked(url: object) -> bool:
    if not url or not _is_demo_mode_active():
        return False
    is_uth_lms_fn = getattr(_gui_utils, "is_uth_lms_url", None)
    return bool(callable(is_uth_lms_fn) and is_uth_lms_fn(url))


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
    if _is_demo_lms_blocked(cleaned):
        return ""
    return safe_http_url(cleaned, allow_loopback=False)


def webview2_profile_root() -> Path:
    """Return the root folder storing WebView2 user data profiles on Windows."""
    local_app = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(local_app) / "UTHelper" / "webview2_profile"


def webview2_account_profile_name(account_namespace: Optional[str] = None) -> str:
    """Return a deterministic persistent WebView2 profile name for an account namespace."""
    raw_ns = str(account_namespace or "").strip()
    if not raw_ns:
        try:
            from config import settings

            base = str(getattr(settings, "MOODLE_BASE_URL", "") or "").strip().casefold()
            user = (
                str(getattr(settings, "UTH_USERNAME", "") or "").strip().casefold()
                or "anon"
            )
            ns = f"{base}|{user}"
        except Exception:
            ns = "default|anon"
    elif "|" in raw_ns:
        base_part, user_part = raw_ns.rsplit("|", 1)
        base_norm = base_part.strip().casefold()
        user_norm = user_part.strip().casefold() or "anon"
        ns = f"{base_norm}|{user_norm}"
    else:
        ns = raw_ns.casefold()
    digest = hashlib.sha256(
        f"uth-webview2-profile-v1\0{ns}".encode("utf-8")
    ).hexdigest()[:24]
    return f"acct_{digest}"


def clear_persisted_webview2_profiles(
    *,
    keep_profile_name: Optional[str] = None,
    profile_root: Optional[Path] = None,
) -> None:
    """Delete persisted ``acct_*`` and legacy ``uth*`` WebView2 profiles on logout/account switch."""
    root = Path(profile_root) if profile_root is not None else webview2_profile_root()
    retry_delays = (0.08, 0.2, 0.4)
    for base in (root, root / "EBWebView"):
        if not base.is_dir():
            continue
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            name = entry.name
            if not (name.startswith("acct_") or name.startswith("uth")):
                continue
            if keep_profile_name and name == keep_profile_name:
                continue
            for attempt in range(len(retry_delays) + 1):
                try:
                    shutil.rmtree(entry, ignore_errors=False)
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    if attempt < len(retry_delays):
                        time.sleep(retry_delays[attempt])
                    else:
                        shutil.rmtree(entry, ignore_errors=True)


def _sweep_stale_profiles(profile_root: Path, *, active_profile: str) -> None:
    """Best-effort cleanup of legacy ephemeral ``uth*`` WebView2 profile directories."""
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


def _load_uth_app_webview_cls() -> Optional[type]:
    """Lazily resolve UthAppWebView from packaged site-packages or local checkout."""
    try:
        from flet_uth_quiz import UthAppWebView

        return UthAppWebView
    except ImportError:
        try:
            from platform_utils.quiz_client_bootstrap import (
                add_source_quiz_package_to_path,
            )

            repo_root = Path(__file__).resolve().parents[4]
            add_source_quiz_package_to_path(repo_root)
            from flet_uth_quiz import UthAppWebView

            return UthAppWebView
        except Exception:
            return None
    except Exception:
        return None


def _is_inappwebview_mobile_platform(page: Optional[ft.Page]) -> bool:
    """Return True on live Android/iOS Flet pages where UthAppWebView is available."""
    if not isinstance(page, ft.Page) or bool(getattr(page, "web", False)):
        return False
    platform_val = str(getattr(page, "platform", "") or "").lower()
    if "android" not in platform_val and "ios" not in platform_val:
        return False
    return _load_uth_app_webview_cls() is not None


def _is_viewer_locked_url(url: str) -> bool:
    """Return True when ``url`` targets an isolated local Office, PDF, or HTML item viewer."""
    if _extract_loopback_port(url) is None:
        return False
    try:
        path = urlsplit(url).path
        if _LOOPBACK_ITEM_PATH_RE.fullmatch(path):
            return True
        segs = [s for s in path.split("/") if s]
        return len(segs) >= 2 and segs[0] in ("viewer", "pdf-viewer")
    except ValueError:
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


def is_embedded_webview_supported(page: Optional[ft.Page]) -> bool:
    """Return True when the current runtime can host an inline WebView."""
    return _is_flet_webview_platform(page) or (
        _is_windows_webview2_platform(page) and _webview2_runtime_available()
    )


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
        account_namespace: Optional[str] = None,
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
        self._account_namespace = account_namespace
        self._profile_name = webview2_account_profile_name(account_namespace)
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
        self._paint_ready = False
        self._paint_deadline: float = 0.0
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
        self._explicit_bounds_set: bool = False
        self._bottom_inset_dip: float = 0.0
        self._hwnd: int = 0
        self._parent_hwnd: int = 0
        self._controller: Any = None
        self._core: Any = None
        self._pending_cookie_ctrl: Any = None
        self._cookie_prefetch_started: bool = False
        self._prefetched_cookies: Optional[tuple[Optional[list[dict[str, Any]]], Optional[BaseException]]] = None
        self._thread: Optional[threading.Thread] = None
        self._thread_id: int = 0
        self._hwnd_retries: int = 15
        with _OCCLUSION_LOCK:
            _ACTIVE_DOCKS.add(self)

    def _effective_visible(self) -> bool:
        return bool(self._visible and not is_globally_occluded())

    def _notify_occlusion_changed(self) -> None:
        if not self._stop_event.is_set():
            self._enqueue("occlusion", None)

    def _start_cookie_prefetch(self) -> None:
        with self._lock:
            if (
                self._cookie_prefetch_started
                or self._cookies
                or not callable(self._cookie_provider)
            ):
                return
            self._cookie_prefetch_started = True
        provider = self._cookie_provider

        def _worker() -> None:
            result: Optional[list[dict[str, Any]]] = None
            err: Optional[BaseException] = None
            try:
                result = list(provider() or ())
            except BaseException as exc:
                err = exc
            with self._lock:
                self._prefetched_cookies = (result, err)
                has_waiting_ctrl = self._pending_cookie_ctrl is not None
            if has_waiting_ctrl and not self._stop_event.is_set():
                self._enqueue("_cookies_ready", None)

        threading.Thread(
            target=_worker,
            name="CoursesWebView2CookiePrefetch",
            daemon=True,
        ).start()

    def start(self) -> None:
        if self._thread is not None or sys.platform != "win32":
            return
        self._start_cookie_prefetch()
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
            path = urlsplit(self._url).path
            if path.startswith("/viewer/") or path.startswith("/pdf-viewer/"):
                return True
            if _extract_loopback_port(self._url) is not None and _LOOPBACK_ITEM_PATH_RE.fullmatch(path):
                return True
            return False
        except ValueError:
            return False

    def _viewer_prefix(self) -> str:
        if not self._url:
            return ""
        try:
            parts = urlsplit(self._url)
            segs = [s for s in parts.path.split("/") if s]
            if len(segs) >= 2 and segs[0] in ("viewer", "pdf-viewer"):
                return f"{parts.scheme.lower()}://{parts.netloc.lower()}/{segs[0]}/{segs[1]}/"
            if _extract_loopback_port(self._url) is not None and _LOOPBACK_ITEM_PATH_RE.fullmatch(parts.path):
                return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}"
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
        self._paint_ready = False
        self._paint_deadline = 0.0
        self._enqueue("navigate", safe_url)

    def load_html(self, html: str) -> None:
        self._html = html
        self._url = ""
        self._paint_ready = False
        self._paint_deadline = 0.0
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
        self._paint_ready = False
        self._paint_deadline = 0.0
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
        self._explicit_bounds_set = True
        self._bottom_inset_dip = max(0.0, float(bottom_inset))
        self._enqueue("bounds", self._bounds_dip)

    def dispose(self) -> None:
        with _OCCLUSION_LOCK:
            _ACTIVE_DOCKS.discard(self)
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
        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                set_dpi = getattr(user32, "SetThreadDpiAwarenessContext", None)
                if set_dpi is not None:
                    set_dpi.argtypes = [ctypes.c_void_p]
                    set_dpi.restype = ctypes.c_void_p
                    if not set_dpi(ctypes.c_void_p(-4)):
                        set_dpi(ctypes.c_void_p(-3))
            except Exception:
                pass
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

        profile_root = webview2_profile_root()
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

    def _create_controller(self, environment: Any, window_ref: Any) -> Any:
        options = environment.create_core_webview2_controller_options()
        options.profile_name = self._profile_name
        options.is_in_private_mode_enabled = False
        return environment.create_core_webview2_controller_async(window_ref, options)

    def _create_private_controller(self, environment: Any, window_ref: Any) -> Any:
        """Compatibility alias returning the persistent per-account controller."""
        return self._create_controller(environment, window_ref)

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
                if self._thread is not None and self._thread.is_alive():
                    with self._lock:
                        prefetched = self._prefetched_cookies
                        if prefetched is None:
                            self._pending_cookie_ctrl = ctrl
                    if prefetched is None:
                        self._start_cookie_prefetch()
                        with self._lock:
                            prefetched = self._prefetched_cookies
                            if prefetched is not None:
                                self._pending_cookie_ctrl = None
                        if prefetched is None:
                            return
                    cookies_val, cookie_err = prefetched
                    if cookie_err is not None:
                        logger.debug("WebView2 async cookie provider failed: %s", cookie_err)
                        self._fail("Không thể khôi phục phiên đăng nhập. Hãy đăng nhập lại.")
                        return
                    self._cookies = list(cookies_val or ())
                else:
                    try:
                        self._cookies = list(self._cookie_provider() or ())
                    except Exception as exc:
                        logger.debug("WebView2 async cookie provider failed: %s", exc)
                        self._fail("Không thể khôi phục phiên đăng nhập. Hãy đăng nhập lại.")
                        return
            self._complete_ctrl_setup(ctrl, core)
        except Exception as exc:
            self._fail(str(exc))

    def _handle_cookies_ready(self) -> None:
        with self._lock:
            ctrl = self._pending_cookie_ctrl
            self._pending_cookie_ctrl = None
            prefetched = self._prefetched_cookies
        if ctrl is None:
            return
        if self._stop_event.is_set() or not self._hwnd:
            try:
                ctrl.close()
            except Exception:
                pass
            self._controller = None
            self._core = None
            return
        if prefetched is not None:
            cookies_val, cookie_err = prefetched
            if cookie_err is not None:
                logger.debug("WebView2 async cookie provider failed: %s", cookie_err)
                self._fail("Không thể khôi phục phiên đăng nhập. Hãy đăng nhập lại.")
                return
            self._cookies = list(cookies_val or ())
        core = self._core
        if core is None:
            return
        try:
            self._complete_ctrl_setup(ctrl, core)
        except Exception as exc:
            self._fail(str(exc))

    def _complete_ctrl_setup(self, ctrl: Any, core: Any) -> None:
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
        self._paint_ready = False
        self._paint_deadline = 0.0
        self._bind_events(core)
        self._apply_window_bounds()
        ctrl.is_visible = self._effective_visible()
        if self._html is not None:
            core.navigate_to_string(self._html)
        elif self._url:
            core.navigate(self._url)
        with self._lock:
            queued = list(self._pending_scripts)
            self._pending_scripts.clear()
        for script, loop, fut in queued:
            self._exec_js_on_core(script, loop, fut)

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

    def _mark_paint_ready(self) -> None:
        if self._paint_ready:
            return
        self._paint_ready = True
        self._paint_deadline = 0.0
        try:
            self._apply_window_bounds()
        except Exception:
            pass
        if self._on_loading_changed is not None:
            self._on_loading_changed(False)

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
                self._paint_ready = False
                self._paint_deadline = 0.0
                if self._on_loading_changed:
                    self._on_loading_changed(True)

            core.add_navigation_starting(_on_nav_starting)

            def _on_nav_completed(_sender: Any, args: Any) -> None:
                self._update_history_state(core)
                if getattr(args, "is_success", True):
                    if self._is_viewer_locked():
                        self._paint_deadline = time.monotonic() + 12.0
                    else:
                        self._paint_deadline = time.monotonic() + 0.25
                        try:
                            core.execute_script_async(
                                "requestAnimationFrame(() => requestAnimationFrame(() => "
                                "window.chrome && window.chrome.webview && "
                                "window.chrome.webview.postMessage('__UTH_VIEWER_PAINTED__')))"
                            )
                        except Exception:
                            self._mark_paint_ready()
                    if self._on_ready is not None:
                        self._on_ready()
                else:
                    self._mark_paint_ready()
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
                    msg: Optional[str] = None
                    try:
                        msg = args.try_get_web_message_as_string()
                    except Exception:
                        msg = None
                    if msg is None:
                        raw_json = getattr(args, "web_message_as_json", None)
                        if raw_json is not None:
                            msg = str(raw_json)
                    if msg is None:
                        return
                    text = str(msg)
                    if text in ("__UTH_VIEWER_READY__", "__UTH_VIEWER_PAINTED__"):
                        self._mark_paint_ready()
                        return
                    if text.startswith("__UTH_VIEWER_ERROR__:"):
                        self._mark_paint_ready()
                        if self._on_error is not None:
                            self._on_error(
                                text[len("__UTH_VIEWER_ERROR__:") :] or "Viewer failed"
                            )
                        return
                    forwarded = (
                        text[len(_UTH_MSG_PREFIX) :]
                        if text.startswith(_UTH_MSG_PREFIX)
                        else text
                    )
                    try:
                        parsed = json.loads(forwarded)
                        if isinstance(parsed, dict) and parsed.get("type") in (
                            "ready",
                            "error",
                        ):
                            self._mark_paint_ready()
                    except Exception:
                        pass
                    if self._on_message is not None:
                        self._on_message(forwarded)

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
            user32 = ctypes.windll.user32
            eff_visible = self._effective_visible()
            if (
                win32gui.IsIconic(parent)
                or not win32gui.IsWindowVisible(parent)
                or not eff_visible
            ):
                win32gui.ShowWindow(self._hwnd, win32con.SW_HIDE)
                if self._controller is not None:
                    self._controller.is_visible = False
                return
            try:
                dpi = float(user32.GetDpiForWindow(parent))
            except Exception:
                dpi = 96.0
            awareness = 2
            try:
                get_ctx = getattr(user32, "GetThreadDpiAwarenessContext", None)
                get_aw = getattr(user32, "GetAwarenessFromDpiAwarenessContext", None)
                if get_ctx is not None and get_aw is not None:
                    get_ctx.restype = ctypes.c_void_p
                    get_aw.argtypes = [ctypes.c_void_p]
                    get_aw.restype = ctypes.c_int
                    awareness = int(get_aw(get_ctx()))
            except Exception:
                awareness = 2
            scale = 1.0 if awareness == 0 else max(1.0, dpi / 96.0)
            origin_x, origin_y = win32gui.ClientToScreen(parent, (0, 0))
            crect = win32gui.GetClientRect(parent)
            max_w = max(160, crect[2] - crect[0])
            max_h = max(160, crect[3] - crect[1])
            left_px = max(0, int(round(self._bounds_dip[0] * scale)))
            top_px = max(0, int(round(self._bounds_dip[1] * scale)))
            bot_px = max(0, int(round(self._bottom_inset_dip * scale)))
            avail_w = max(160, max_w - left_px)
            avail_h = max(160, max_h - top_px - bot_px)
            if self._explicit_bounds_set:
                req_w = max(160, int(round(self._bounds_dip[2] * scale)))
                req_h = max(160, int(round(self._bounds_dip[3] * scale)))
                w_px = min(avail_w, req_w)
                h_px = min(avail_h, req_h)
            else:
                w_px = avail_w
                h_px = avail_h
            fg_hwnd = 0
            fg_root_owner = 0
            try:
                fg_hwnd = int(user32.GetForegroundWindow() or 0)
                if fg_hwnd:
                    get_anc = getattr(user32, "GetAncestor", None)
                    if get_anc is not None:
                        get_anc.argtypes = [wintypes.HWND, wintypes.UINT]
                        get_anc.restype = wintypes.HWND
                        fg_root_owner = int(get_anc(fg_hwnd, 3) or 0)  # GA_ROOTOWNER
            except Exception:
                fg_hwnd = 0
                fg_root_owner = 0
            app_is_fg = fg_hwnd in (int(parent), int(self._hwnd)) or fg_root_owner in (
                int(parent),
                int(self._hwnd),
            )
            parent_topmost = False
            self_topmost = False
            prev_above = 0
            try:
                parent_topmost = bool(
                    int(user32.GetWindowLongW(int(parent), -20)) & win32con.WS_EX_TOPMOST
                )
                self_topmost = bool(
                    int(user32.GetWindowLongW(int(self._hwnd), -20)) & win32con.WS_EX_TOPMOST
                )
                prev_above = int(win32gui.GetWindow(int(parent), win32con.GW_HWNDPREV) or 0)
            except Exception:
                pass
            if self_topmost != parent_topmost:
                insert_after = (
                    win32con.HWND_TOPMOST if parent_topmost else win32con.HWND_NOTOPMOST
                )
                z_flag = 0
            elif app_is_fg and not preserve_zorder:
                insert_after = win32con.HWND_TOP
                z_flag = 0
            elif prev_above != int(self._hwnd):
                insert_after = (
                    prev_above
                    if prev_above
                    else (win32con.HWND_TOPMOST if parent_topmost else win32con.HWND_TOP)
                )
                z_flag = 0
            else:
                insert_after = 0
                z_flag = win32con.SWP_NOZORDER
            flags = win32con.SWP_NOACTIVATE | z_flag
            win32gui.SetWindowPos(
                self._hwnd,
                insert_after,
                origin_x + left_px,
                origin_y + top_px,
                w_px,
                h_px,
                flags,
            )
            if eff_visible and self._paint_ready:
                win32gui.ShowWindow(self._hwnd, win32con.SW_SHOWNOACTIVATE)
            else:
                win32gui.ShowWindow(self._hwnd, win32con.SW_HIDE)
            if self._controller is not None:
                self._controller.bounds = Rect(0.0, 0.0, float(w_px), float(h_px))
                self._controller.is_visible = eff_visible
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
        last_parent_state: tuple[int, int, int, int, bool, bool, bool, bool] = (
            0, 0, 0, 0, False, True, False, True,
        )
        while not self._stop_event.is_set():
            self._drain_commands()
            if (
                not self._paint_ready
                and self._paint_deadline > 0.0
                and time.monotonic() >= self._paint_deadline
            ):
                self._mark_paint_ready()
            parent_inactive = False
            if self._parent_hwnd and win32gui.IsWindow(self._parent_hwnd):
                try:
                    cur_rect = win32gui.GetWindowRect(self._parent_hwnd)
                    is_min = bool(win32gui.IsIconic(self._parent_hwnd))
                    is_vis = bool(win32gui.IsWindowVisible(self._parent_hwnd))
                    parent_inactive = is_min or not is_vis
                    parent_topmost = bool(
                        int(ctypes.windll.user32.GetWindowLongW(int(self._parent_hwnd), -20))
                        & win32con.WS_EX_TOPMOST
                    )
                    z_aligned = (
                        int(win32gui.GetWindow(int(self._parent_hwnd), win32con.GW_HWNDPREV) or 0)
                        == int(self._hwnd)
                    )
                    cur_state = (
                        cur_rect[0],
                        cur_rect[1],
                        cur_rect[2],
                        cur_rect[3],
                        is_min,
                        is_vis,
                        parent_topmost,
                        z_aligned,
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
            awaiting_paint = not self._paint_ready and self._paint_deadline > 0.0
            wait_ms = (
                16
                if (awaiting_paint or (self._effective_visible() and not parent_inactive))
                else 250
            )
            ctypes.windll.user32.MsgWaitForMultipleObjects(0, None, False, wait_ms, 0x04FF)

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
            elif action == "_cookies_ready":
                self._handle_cookies_ready()
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
                eff_vis = self._effective_visible()
                if self._controller is not None:
                    self._controller.is_visible = eff_vis
                if self._hwnd and not eff_vis:
                    win32gui.ShowWindow(self._hwnd, win32con.SW_HIDE)
                self._apply_window_bounds()
            elif action == "occlusion":
                eff_vis = self._effective_visible()
                if self._controller is not None:
                    self._controller.is_visible = eff_vis
                if self._hwnd and not eff_vis:
                    win32gui.ShowWindow(self._hwnd, win32con.SW_HIDE)
                self._apply_window_bounds()
            elif action == "bounds":
                self._apply_window_bounds()
            elif action == "dispose":
                self._stop_event.set()
                break

    def _cleanup_native(self) -> None:
        with _OCCLUSION_LOCK:
            _ACTIVE_DOCKS.discard(self)
        self._resolve_pending_scripts()
        with self._lock:
            pending_ctrl = self._pending_cookie_ctrl
            self._pending_cookie_ctrl = None
        if pending_ctrl is not None and pending_ctrl is not self._controller:
            try:
                pending_ctrl.close()
            except Exception:
                pass
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
    """Unified inline WebView adapter across mobile (inappwebview/flet_webview) and Windows (WebView2)."""

    def __init__(
        self,
        *,
        backend: str,
        page: Optional[ft.Page] = None,
        url: str = "",
        html: Optional[str] = None,
        webview_control: Any = None,
        dock: Optional[WindowsWebView2Dock] = None,
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
    ) -> None:
        self._backend = backend
        self._page = page
        self._url = url
        self._cookie_origin_host = (
            (urlsplit(url).hostname or "").lower() if url else ""
        )
        self._is_viewer_locked = bool(
            html is not None or (url and _is_viewer_locked_url(url))
        )
        self._allowed_loopback_ports: set[int] = set()
        init_loop_port = _extract_loopback_port(url)
        if init_loop_port is not None:
            self._allowed_loopback_ports.add(init_loop_port)
        self._wv = webview_control
        if backend == "inappwebview" and webview_control is not None:
            try:
                webview_control._on_rebootstrap_requested = (
                    self._schedule_inapp_cookie_sync
                )
            except Exception:
                pass
        self._dock = dock
        self._cookies: list[dict[str, Any]] = list(cookies or ())
        self._cookie_provider = cookie_provider
        self._on_ready = on_ready
        self._on_error = on_error
        self._on_message = on_message
        self._on_url_changed = on_url_changed
        self._on_title_changed = on_title_changed
        self._on_loading_changed = on_loading_changed
        self._on_history_changed = on_history_changed
        self._on_external_url = on_external_url

        self._pending_html: Optional[tuple[str, Optional[str]]] = (
            (html, None) if (backend == "flet_webview" and html is not None) else None
        )
        self._can_go_back = False
        self._can_go_forward = False
        self._suspended = False
        self._disposed = False
        self._cookie_sync_in_progress = False
        self._last_bounds: Optional[tuple[float, float, float, float, float]] = None

        self._loading_ring: Optional[ft.ProgressRing] = None
        self._loading_text: Optional[ft.Text] = None
        self._slot: ft.Container
        if backend in ("flet_webview", "inappwebview"):
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

    def _schedule_inapp_cookie_sync(self) -> None:
        if (
            self._disposed
            or self._backend != "inappwebview"
            or self._wv is None
            or self._cookie_sync_in_progress
        ):
            return
        self._cookie_sync_in_progress = True

        async def _bootstrap_inapp_cookies() -> None:
            try:
                if self._wv is None or self._disposed:
                    return
                cur_host = (urlsplit(self._url or "").hostname or "").lower()
                same_cookie_host = bool(
                    not self._is_viewer_locked
                    and self._cookie_origin_host
                    and cur_host == self._cookie_origin_host
                    and (self._cookies or callable(self._cookie_provider))
                )
                if not same_cookie_host:
                    if getattr(self._wv, "require_initial_cookies", False):
                        abort_fn = getattr(self._wv, "abort_cookie_bootstrap", None)
                        if callable(abort_fn):
                            try:
                                await abort_fn()
                            except Exception:
                                pass
                    return
                resolved_cookies = list(self._cookies or ())
                if not resolved_cookies and callable(self._cookie_provider):
                    try:
                        resolved_cookies = (
                            await asyncio.to_thread(self._cookie_provider) or []
                        )
                    except Exception as exc:
                        logger.debug("InAppWebView cookie provider failed: %s", exc)
                        resolved_cookies = []
                if self._wv is not None and not self._disposed:
                    try:
                        await self._wv.bootstrap_cookies(
                            list(resolved_cookies),
                            target_url=self._url,
                        )
                    except Exception:
                        pass
            finally:
                self._cookie_sync_in_progress = False

        self._schedule_task(_bootstrap_inapp_cookies)

    def _on_flet_mount(self) -> None:
        if self._disposed:
            return
        if self._backend == "inappwebview":
            has_cookie_bootstrap = bool(
                self._wv is not None
                and getattr(self._wv, "require_initial_cookies", False)
            )
            if has_cookie_bootstrap:
                self._schedule_inapp_cookie_sync()
            if self._pending_html is not None:
                html, base_url = self._pending_html
                self._pending_html = None

                async def _flush_inapp_html() -> None:
                    if self._wv is not None and not self._disposed:
                        try:
                            await self._wv.load_html(html, base_url=base_url)
                        except Exception:
                            pass

                self._schedule_task(_flush_inapp_html)
            return

        pending = self._pending_html
        self._pending_html = None

        async def _init_flet_webview() -> None:
            if self._wv is None or self._disposed:
                return
            try:
                import flet_webview as fwv

                set_js = getattr(self._wv, "set_javascript_mode", None)
                js_enum = getattr(fwv, "JavaScriptMode", None)
                if callable(set_js) and js_enum is not None:
                    await set_js(js_enum.UNRESTRICTED)
            except Exception:
                pass
            if self._wv is None or self._disposed:
                return
            if pending is not None:
                html, base_url = pending
                try:
                    await self._wv.load_html(html, base_url=base_url)
                except Exception:
                    pass
            elif self._url and _safe_control_page(self._wv) is not None:
                try:
                    await self._wv.reload()
                except Exception:
                    pass
            await self._refresh_history_async()

        self._schedule_task(_init_flet_webview)

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

    def _is_allowed_event_url(self, url: str) -> bool:
        cleaned = str(url or "").strip()
        if not cleaned:
            return True
        lowered = cleaned.lower()
        if lowered == "about:blank" or lowered.startswith(("about:blank?", "about:blank#")):
            return True
        if not self._is_viewer_locked and (
            lowered == "about:srcdoc"
            or lowered.startswith(("about:srcdoc?", "about:srcdoc#"))
        ):
            return True
        if lowered.startswith("blob:"):
            inner = cleaned[5:]
            return bool(
                _validate_webview_url(
                    inner,
                    allowed_loopback_ports=self._allowed_loopback_ports,
                )
            )
        return bool(
            _validate_webview_url(
                cleaned,
                allowed_loopback_ports=self._allowed_loopback_ports,
            )
        )

    @staticmethod
    def _is_transient_event_url(url: str) -> bool:
        lowered = str(url or "").strip().lower()
        return (
            lowered == "about:blank"
            or lowered.startswith(("about:blank?", "about:blank#"))
            or lowered == "about:srcdoc"
            or lowered.startswith(("about:srcdoc?", "about:srcdoc#"))
            or lowered.startswith("blob:")
        )

    def _sync_live_event_url(self, url: str) -> None:
        self._url = url
        if self._backend == "inappwebview" and self._wv is not None:
            try:
                self._wv.url = url
                cur_host = (urlsplit(url).hostname or "").lower()
                self._wv.require_initial_cookies = bool(
                    not self._is_viewer_locked
                    and self._cookie_origin_host
                    and cur_host == self._cookie_origin_host
                    and (self._cookies or callable(self._cookie_provider))
                )
            except Exception:
                pass

    def _handle_flet_page_started(self, e: Any) -> None:
        if self._disposed:
            return
        url = str(getattr(e, "data", "") or "")
        if url and not self._is_allowed_event_url(url):
            if self._on_loading_changed is not None:
                self._on_loading_changed(False)
            if self._on_error is not None:
                self._on_error(f"Blocked disallowed WebView URL: {url}")
            return
        if self._on_loading_changed is not None:
            self._on_loading_changed(True)
        if url and not self._is_transient_event_url(url):
            self._sync_live_event_url(url)
            if self._on_url_changed is not None:
                self._on_url_changed(url)
        self._schedule_history_refresh()

    def _handle_flet_page_ended(self, e: Any) -> None:
        if self._disposed:
            return
        if self._on_loading_changed is not None:
            self._on_loading_changed(False)
        if self._on_ready is not None:
            self._on_ready()
        if self._backend != "flet_webview":
            return
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
        if getattr(e, "is_for_main_frame", None) is False:
            return
        err_text = str(
            getattr(e, "data", "")
            or getattr(e, "description", "")
            or "WebView load error"
        )
        upper_err = err_text.upper()
        if "ERR_ABORTED" in upper_err or "USER_CANCELLED" in upper_err:
            return
        if self._on_loading_changed is not None:
            self._on_loading_changed(False)
        if self._on_error is not None:
            self._on_error(err_text)

    def _handle_flet_url_change(self, e: Any) -> None:
        if self._disposed:
            return
        url = str(getattr(e, "data", "") or "")
        if url and not self._is_allowed_event_url(url):
            if self._on_error is not None:
                self._on_error(f"Blocked disallowed WebView URL: {url}")
            return
        if url and not self._is_transient_event_url(url):
            self._sync_live_event_url(url)
            if self._on_url_changed is not None:
                self._on_url_changed(url)
        self._schedule_history_refresh()

    def _handle_flet_console_message(self, e: Any) -> None:
        if self._disposed or self._on_message is None:
            return
        msg = str(
            getattr(e, "message", None) or getattr(e, "data", "") or ""
        )
        if msg.startswith(_UTH_MSG_PREFIX):
            self._on_message(msg[len(_UTH_MSG_PREFIX) :])

    def _handle_inapp_title_change(self, e: Any) -> None:
        if self._disposed or self._on_title_changed is None:
            return
        title = str(getattr(e, "data", "") or "").strip()
        if title:
            self._on_title_changed(title)

    def _handle_inapp_history_change(self, e: Any) -> None:
        if self._disposed:
            return
        raw = str(getattr(e, "data", "") or "")
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        self._can_go_back = bool(payload.get("can_go_back", False))
        self._can_go_forward = bool(payload.get("can_go_forward", False))
        if self._on_history_changed is not None:
            self._on_history_changed(self._can_go_back, self._can_go_forward)

    def _handle_inapp_external_url(self, e: Any) -> None:
        if self._disposed or self._on_external_url is None:
            return
        ext = safe_http_url(str(getattr(e, "data", "") or ""))
        if ext and not _is_demo_lms_blocked(ext):
            self._on_external_url(ext)

    def _handle_webview2_loading(self, loading: bool) -> None:
        if self._disposed:
            return
        if not loading and self._dock is not None:
            if not getattr(self._dock, "_paint_ready", True):
                return
            if self._loading_ring is not None:
                self._loading_ring.visible = False
            if self._loading_text is not None:
                self._loading_text.visible = False
            self._slot.content = ft.Container(expand=True, bgcolor=C.BG)
            safe_control_update(self._slot)
        elif loading and self._dock is not None:
            if self._loading_ring is not None:
                self._loading_ring.visible = True
            if self._loading_text is not None:
                self._loading_text.visible = True
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
        if self._backend in ("flet_webview", "inappwebview") and self._wv is not None:
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
        if self._backend in ("flet_webview", "inappwebview") and self._wv is not None:
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
        if self._backend in ("flet_webview", "inappwebview") and self._wv is not None:
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
        if self._backend in ("flet_webview", "inappwebview") and self._wv is not None:

            async def _back() -> None:
                if self._wv is not None and not self._disposed:
                    try:
                        await self._wv.go_back()
                    except Exception:
                        pass
                    if self._backend == "flet_webview":
                        await self._refresh_history_async()

            self._schedule_task(_back)
            return
        if self._backend == "webview2" and self._dock is not None:
            self._dock.go_back()

    def go_forward(self) -> None:
        if self._disposed:
            return
        if self._backend in ("flet_webview", "inappwebview") and self._wv is not None:

            async def _fwd() -> None:
                if self._wv is not None and not self._disposed:
                    try:
                        await self._wv.go_forward()
                    except Exception:
                        pass
                    if self._backend == "flet_webview":
                        await self._refresh_history_async()

            self._schedule_task(_fwd)
            return
        if self._backend == "webview2" and self._dock is not None:
            self._dock.go_forward()

    def reload(self) -> None:
        if self._disposed:
            return
        if self._backend in ("flet_webview", "inappwebview") and self._wv is not None:

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
        if self._backend == "inappwebview":
            self._slot.ignore_interactions = True
            safe_control_update(self._slot)
        elif self._backend == "flet_webview":
            self._slot.visible = False
            safe_control_update(self._slot)
        elif self._dock is not None:
            self._dock.set_visible(False)

    def resume(self) -> None:
        self._suspended = False
        if self._disposed:
            return
        if self._backend == "inappwebview":
            self._slot.ignore_interactions = False
            safe_control_update(self._slot)
        elif self._backend == "flet_webview":
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
    account_namespace: Optional[str] = None,
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

    if _is_inappwebview_mobile_platform(page):
        app_wv_cls = _load_uth_app_webview_cls()
        if app_wv_cls is not None:
            try:
                loop_port = _extract_loopback_port(safe_url)
                is_viewer_locked = bool(
                    html is not None or _is_viewer_locked_url(safe_url)
                )
                require_cookies = bool(
                    not is_viewer_locked
                    and safe_url.lower().startswith("https://")
                    and (cookies or callable(cookie_provider))
                )
                holder_inapp: dict[str, EmbeddedWebViewHost] = {}
                wv_inapp = app_wv_cls(
                    url=safe_url,
                    html=html,
                    bgcolor=bgcolor or C.BG,
                    viewer_locked=is_viewer_locked,
                    allowed_loopback_ports=[loop_port] if loop_port is not None else [],
                    require_initial_cookies=require_cookies,
                    block_uth_lms=_is_demo_mode_active(),
                    expand=True,
                    on_page_started=lambda e: holder_inapp["host"]._handle_flet_page_started(e)
                    if "host" in holder_inapp
                    else None,
                    on_page_ended=lambda e: holder_inapp["host"]._handle_flet_page_ended(e)
                    if "host" in holder_inapp
                    else None,
                    on_url_change=lambda e: holder_inapp["host"]._handle_flet_url_change(e)
                    if "host" in holder_inapp
                    else None,
                    on_title_change=lambda e: holder_inapp["host"]._handle_inapp_title_change(e)
                    if "host" in holder_inapp
                    else None,
                    on_history_change=lambda e: holder_inapp["host"]._handle_inapp_history_change(e)
                    if "host" in holder_inapp
                    else None,
                    on_web_error=lambda e: holder_inapp["host"]._handle_flet_error(e)
                    if "host" in holder_inapp
                    else None,
                    on_console_message=lambda e: holder_inapp["host"]._handle_flet_console_message(e)
                    if "host" in holder_inapp
                    else None,
                    on_external_url=lambda e: holder_inapp["host"]._handle_inapp_external_url(e)
                    if "host" in holder_inapp
                    else None,
                )
                host_inapp = EmbeddedWebViewHost(
                    backend="inappwebview",
                    page=page,
                    url=safe_url,
                    html=html,
                    webview_control=wv_inapp,
                    cookies=cookies,
                    cookie_provider=cookie_provider,
                    on_ready=on_ready,
                    on_error=on_error,
                    on_message=on_message,
                    on_url_changed=on_url_changed,
                    on_title_changed=on_title_changed,
                    on_loading_changed=on_loading_changed,
                    on_history_changed=on_history_changed,
                    on_external_url=on_external_url,
                )
                holder_inapp["host"] = host_inapp
                return host_inapp
            except Exception as exc:
                logger.debug("UthAppWebView host initialization failed: %s", exc)

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
            account_namespace=account_namespace,
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
