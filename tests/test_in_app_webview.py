from __future__ import annotations

from types import SimpleNamespace

import flet as ft

from gui.components import in_app_webview as webview_module
from gui.components.in_app_webview import InAppWebViewDialog, supports_native_webview


def _walk(control):
    if control is None:
        return
    yield control
    content = getattr(control, "content", None)
    if content is not None:
        yield from _walk(content)
    for child in getattr(control, "controls", None) or ():
        yield from _walk(child)
    for child in getattr(control, "actions", None) or ():
        yield from _walk(child)
    title = getattr(control, "title", None)
    if isinstance(title, ft.Control):
        yield from _walk(title)


class _DummyPage:
    def __init__(self, *, platform=ft.PagePlatform.WINDOWS, web=False):
        self.platform = platform
        self.web = web
        self.width = 400
        self.height = 800
        self.dialogs = []
        self.pop_calls = 0
        self.on_keyboard_event = None
        self.tasks = []

    def show_dialog(self, dialog):
        self.dialogs.append(dialog)

    def pop_dialog(self):
        self.pop_calls += 1

    def run_task(self, coro_fn, *args, **kwargs):
        self.tasks.append((coro_fn, args, kwargs))

    def update(self):
        pass


def test_supports_native_webview_checks_platform_and_web(monkeypatch):
    monkeypatch.setattr(webview_module, "flet_webview", object())
    assert supports_native_webview(_DummyPage(platform=ft.PagePlatform.ANDROID, web=False))
    assert supports_native_webview(_DummyPage(platform=ft.PagePlatform.IOS, web=False))
    assert supports_native_webview(_DummyPage(platform=ft.PagePlatform.MACOS, web=False))
    assert not supports_native_webview(_DummyPage(platform=ft.PagePlatform.WINDOWS, web=False))
    assert not supports_native_webview(_DummyPage(platform=ft.PagePlatform.ANDROID, web=True))


def test_in_app_webview_rejects_non_https_urls():
    page = _DummyPage()
    dialog = InAppWebViewDialog(page, url="http://courses.ut.edu.vn/course/1")
    assert dialog.show() is False
    assert page.dialogs == []


def test_in_app_webview_fallback_offers_explicit_external_opt_out_and_escape_close():
    page = _DummyPage(platform=ft.PagePlatform.WINDOWS, web=False)
    external_calls = []
    dialog_ctrl = InAppWebViewDialog(
        page,
        url="https://courses.ut.edu.vn/course/view.php?id=9",
        title="Mở LMS",
        on_open_external=external_calls.append,
    )

    assert dialog_ctrl.show() is True
    assert len(page.dialogs) == 1
    alert = page.dialogs[0]
    assert alert.modal is True
    assert "Mở LMS" in alert.semantics_label
    assert external_calls == []  # Never auto-opens external browser

    ext_btns = [
        item
        for item in _walk(alert)
        if isinstance(item, ft.IconButton)
        and getattr(item, "icon", None) == ft.Icons.OPEN_IN_NEW_ROUNDED
    ]
    assert len(ext_btns) == 1
    ext_btns[0].on_click(None)
    assert external_calls == ["https://courses.ut.edu.vn/course/view.php?id=9"]

    assert isinstance(alert.content, ft.KeyboardListener)
    assert callable(alert.content.on_key_down)
    alert.content.on_key_down(SimpleNamespace(key="Escape"))
    assert page.pop_calls == 1


def test_in_app_webview_uses_native_webview_on_android(monkeypatch):
    created = []

    class _FakeWebView(ft.Container):
        def __init__(self, url, **kwargs):
            created.append((url, kwargs))
            super().__init__()

    monkeypatch.setattr(
        webview_module,
        "flet_webview",
        SimpleNamespace(WebView=_FakeWebView),
    )
    page = _DummyPage(platform=ft.PagePlatform.ANDROID, web=False)
    dialog_ctrl = InAppWebViewDialog(
        page,
        url="https://portal.ut.edu.vn/schedule",
        title="Lịch học",
    )

    assert dialog_ctrl.show() is True
    assert created
    assert created[0][0] == "https://portal.ut.edu.vn/schedule"
    assert len(page.dialogs) == 1

    # Case-insensitive native navigation guard closes on unsafe schemes or local targets
    kwargs = created[0][1]
    on_started = kwargs["on_page_started"]
    on_url_change = kwargs["on_url_change"]

    on_started(SimpleNamespace(data="HTTP://evil.example/steal"))
    assert page.pop_calls == 1
    on_url_change(SimpleNamespace(data="JAVASCRIPT:alert(1)"))
    assert page.pop_calls == 2
    on_started(SimpleNamespace(data="https://127.0.0.1/admin"))
    assert page.pop_calls == 3


def test_in_app_webview_blocks_ssrf_targets_and_redirects(monkeypatch):
    import socket
    import urllib.request
    from email.message import Message
    from urllib.error import HTTPError
    import pytest
    from gui.components.in_app_webview import _HttpsOnlyRedirectHandler, _fetch_https_preview

    page = _DummyPage()
    for bad_url in (
        "https://127.0.0.1/secret",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/admin",
        "https://10.0.0.1/internal",
        "https://localhost/debug",
    ):
        assert InAppWebViewDialog(page, url=bad_url).show() is False
        assert _fetch_https_preview(bad_url) == ("", "")

    # DNS resolving to private IP must also be blocked in _fetch_https_preview
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))],
    )
    assert _fetch_https_preview("https://metadata-alias.example.com/") == ("", "")

    handler = _HttpsOnlyRedirectHandler()
    orig_req = urllib.request.Request("https://courses.ut.edu.vn/course/1")
    for redir_target in (
        "https://127.0.0.1/admin",
        "https://169.254.169.254/latest/meta-data/",
        "https://rebind.example.com/secret",
    ):
        with pytest.raises(HTTPError):
            handler.redirect_request(orig_req, None, 302, "Found", Message(), redir_target)


def test_html_to_markdown_sanitizes_unsafe_links_and_images():
    from gui.core.utils import html_to_markdown

    digest = "a" * 64
    raw_html = (
        '<p>Hello <a href="javascript:alert(1)">ClickMe</a> '
        '<a href="&#106;avascript:alert(2)">Entity</a> '
        '<a href="data:text/html,xss">DataLink</a> '
        '<a href="vbscript:msgbox(1)">VB</a> '
        '<a href="file:///etc/passwd">FileLink</a> '
        '<a href="https://courses.ut.edu.vn/mod/assign/view.php?id=9">SafeLink</a></p>'
        '<img src="javascript:alert(3)" alt="](">'
        '<img src="data:image/svg+xml,<svg>" alt="SvgAlt">'
        '<img src="https://courses.ut.edu.vn/logo.png" alt="Logo">'
        f'<img src="cache/images/{digest}.png" alt="Cached">'
    )
    md = html_to_markdown(raw_html)

    assert "javascript:" not in md.casefold()
    assert "vbscript:" not in md.casefold()
    assert "data:text/html" not in md.casefold()
    assert "data:image/svg" not in md.casefold()
    assert "file:///" not in md.casefold()
    assert "ClickMe" in md
    assert "Entity" in md
    assert "DataLink" in md
    assert "SvgAlt" in md
    assert "[SafeLink](https://courses.ut.edu.vn/mod/assign/view.php?id=9)" in md
    assert "![Logo](https://courses.ut.edu.vn/logo.png)" in md
    assert f"![Cached](cache/images/{digest}.png)" in md
