"""Bind a local document to its actual Flet window during asynchronous startup."""

from types import SimpleNamespace
import sys

from gui.components.courses.embedded_webview_host import WindowsWebView2Dock


def _windows(monkeypatch, items):
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(
        IsWindowVisible=lambda hwnd: items[hwnd][3],
        GetWindowText=lambda hwnd: items[hwnd][0],
        GetClassName=lambda hwnd: items[hwnd][1],
        EnumWindows=lambda callback, ctx: [callback(hwnd, ctx) for hwnd in items],
    ))
    monkeypatch.setitem(sys.modules, "win32process", SimpleNamespace(
        GetWindowThreadProcessId=lambda hwnd: (1, items[hwnd][2]),
    ))
    monkeypatch.setattr(WindowsWebView2Dock, "_collect_child_pids", staticmethod(lambda _pid: {100, 101}))


def test_unrelated_flutter_window_is_not_used_while_requested_window_starts(monkeypatch):
    _windows(monkeypatch, {1: ("Schedule fixture preview", "FLUTTER_RUNNER_WIN32_WINDOW", 200, True)})
    assert WindowsWebView2Dock._find_parent_hwnd("UTHelper Office QA") == 0


def test_requested_visible_flet_window_is_selected_over_unrelated_windows(monkeypatch):
    _windows(monkeypatch, {
        1: ("Schedule fixture preview", "FLUTTER_RUNNER_WIN32_WINDOW", 200, True),
        2: ("UTHelper Office QA", "FLUTTER_RUNNER_WIN32_WINDOW", 101, True),
        3: ("UTHelper Office QA", "Chrome_WidgetWin_1", 100, True),
    })
    assert WindowsWebView2Dock._find_parent_hwnd("UTHelper Office QA") == 2


def test_unique_requested_flet_window_supports_detached_client_process(monkeypatch):
    _windows(monkeypatch, {1: ("UTHelper", "FLUTTER_RUNNER_WIN32_WINDOW", 200, True)})
    assert WindowsWebView2Dock._find_parent_hwnd("UTHelper") == 1


def test_ambiguous_unrelated_windows_do_not_receive_private_document(monkeypatch):
    _windows(monkeypatch, {
        1: ("UTHelper", "FLUTTER_RUNNER_WIN32_WINDOW", 200, True),
        2: ("UTHelper", "FLUTTER_RUNNER_WIN32_WINDOW", 201, True),
    })
    assert WindowsWebView2Dock._find_parent_hwnd("UTHelper") == 0


def test_viewer_lock_preserves_mixed_case_token_and_case_insensitive_origin():
    from urllib.parse import urlsplit

    from core.courses.local_preview_server import LocalPreviewServer

    srv = LocalPreviewServer()
    try:
        # Ensure token contains mixed-case characters as produced by secrets.token_urlsafe(24)
        srv._viewer_token = "EH71ecT-j-C17nwPHcDwbsoZQxtKaWeY"
        base_url = srv.viewer_base_url()
        doc_url = f"{base_url}docx_host.html?src=http://127.0.0.1:{srv.port}/0123456789abcdef"
        dock = WindowsWebView2Dock(page_title="UTHelper", url=doc_url)

        assert dock._is_navigation_uri_allowed(doc_url) is True
        parsed = urlsplit(base_url)
        assert dock._is_navigation_uri_allowed(
            f"HTTP://{parsed.netloc.upper()}{parsed.path}viewer.css"
        ) is True
        # Wrong token case, path traversal, and external origin are rejected
        assert dock._is_navigation_uri_allowed(
            f"http://{parsed.netloc}/viewer/{srv._viewer_token.lower()}/docx_host.html"
        ) is False
        assert dock._is_navigation_uri_allowed(f"{base_url}../other") is False
        assert dock._is_navigation_uri_allowed("https://evil.example/") is False
    finally:
        srv.stop()


def test_global_webview_occlusion_hides_and_restores_visible_dock():
    from gui.components.courses.embedded_webview_host import (
        _ACTIVE_DOCKS,
        _OCCLUSION_LOCK,
        clear_global_webview_occlusion,
        is_globally_occluded,
        set_global_webview_occlusion,
    )

    dock = WindowsWebView2Dock(page_title="UTHelper", url="https://courses.ut.edu.vn/")
    try:
        clear_global_webview_occlusion()
        with _OCCLUSION_LOCK:
            _ACTIVE_DOCKS.add(dock)
        assert is_globally_occluded() is False
        assert dock._effective_visible() is True

        set_global_webview_occlusion("detail_view", True)
        set_global_webview_occlusion("modal_dialog", True)
        assert is_globally_occluded() is True
        assert dock._effective_visible() is False

        # Clearing one reason while another remains keeps dock occluded
        set_global_webview_occlusion("detail_view", False)
        assert is_globally_occluded() is True
        assert dock._effective_visible() is False

        # Clearing final reason restores effective visibility
        set_global_webview_occlusion("modal_dialog", False)
        assert is_globally_occluded() is False
        assert dock._effective_visible() is True
    finally:
        clear_global_webview_occlusion()
        dock.dispose()


def test_apply_window_bounds_respects_explicit_width_height_and_bottom_inset(monkeypatch):
    from gui.components.courses.embedded_webview_host import clear_global_webview_occlusion

    clear_global_webview_occlusion()
    pos_calls: list[tuple[int, int, int, int]] = []
    show_calls: list[int] = []

    monkeypatch.setitem(
        sys.modules,
        "win32con",
        SimpleNamespace(
            SW_HIDE=0,
            SW_SHOWNOACTIVATE=4,
            SWP_NOACTIVATE=0x0010,
            SWP_NOZORDER=0x0004,
            WS_EX_TOPMOST=0x00000008,
            GW_HWNDPREV=3,
            HWND_TOP=0,
            HWND_TOPMOST=-1,
            HWND_NOTOPMOST=-2,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            IsWindow=lambda _h: True,
            IsIconic=lambda _h: False,
            IsWindowVisible=lambda _h: True,
            GetClientRect=lambda _h: (0, 0, 1000, 800),
            ClientToScreen=lambda _h, pt: pt,
            GetWindow=lambda _h, _cmd: 0,
            SetWindowPos=lambda _h, _ins, x, y, w, h, _fl: pos_calls.append((x, y, w, h)),
            ShowWindow=lambda _h, cmd: show_calls.append(cmd),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "winrt.windows.foundation",
        SimpleNamespace(Rect=lambda x, y, w, h: (x, y, w, h)),
    )

    dock = WindowsWebView2Dock(page_title="UTHelper", url="https://courses.ut.edu.vn/")
    try:
        dock._parent_hwnd = 101
        dock._hwnd = 202
        dock._paint_ready = True
        # Centered tablet portrait 820px width inside 1000px client + 64px mobile bottom nav inset
        dock.set_bounds(90.0, 36.0, 820.0, 700.0, bottom_inset=64.0)
        dock._apply_window_bounds()
        assert pos_calls
        x, y, w, h = pos_calls[-1]
        assert (x, y) == (90, 36)
        assert w == 820
        assert h == 700
    finally:
        dock.dispose()


def test_viewer_lock_covers_html_fallback_16hex_route():
    from core.courses.local_preview_server import LocalPreviewServer

    srv = LocalPreviewServer()
    try:
        html_url = srv.register_html("<html><body>Fallback</body></html>")
        dock = WindowsWebView2Dock(page_title="UTHelper", url=html_url)
        assert dock._is_viewer_locked() is True
        assert dock._is_navigation_uri_allowed(html_url) is True
        assert dock._is_navigation_uri_allowed(f"http://127.0.0.1:{srv.port}/ffffffffffffffff") is False
        assert dock._is_navigation_uri_allowed("https://courses.ut.edu.vn/") is False
    finally:
        srv.stop()

