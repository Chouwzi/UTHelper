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
