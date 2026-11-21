"""Tests for Milestone M1: store-safe packaging, PDF backends, LocalPreviewServer /viewer/, EmbeddedWebViewHost, WebViewPane mobile nav, and mobile external file save."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
import sys
import tomllib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import urllib.error
import urllib.request
from xml.etree import ElementTree

import flet as ft
import pytest

import core.courses.local_preview_server as preview_mod
from core.courses.local_preview_server import (
    _HTML_CSP,
    _VIEWER_APP_CSP,
    LocalPreviewServer,
    _resolve_viewer_assets_dir,
)
import core.courses.pdf_renderer as pdf_mod
from core.courses.pdf_renderer import PdfDocumentSession
import gui.components.courses.embedded_webview_host as host_mod
from gui.components.courses.embedded_webview_host import (
    EmbeddedWebViewHost,
    WindowsWebView2Dock,
    build_embedded_webview_host,
)
from gui.components.courses.webview_pane import (
    CoursesWebViewPane,
    _is_loopback_url,
)
from gui.controllers.courses_coordinator import (
    CoursesCoordinator,
    _try_build_embedded_webview,
)
import platform_utils


REPO_ROOT = Path(__file__).resolve().parents[1]


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return (
                int(resp.status),
                {k.lower(): v for k, v in resp.headers.items()},
                resp.read(),
            )
    except urllib.error.HTTPError as err:
        return (
            int(err.code),
            {k.lower(): v for k, v in err.headers.items()},
            err.read(),
        )


def _build_minimal_pdf_with_outline() -> bytes:
    """Create a valid 2-page PDF with text and bookmarks using pypdf + PIL."""
    from PIL import Image
    import pypdf

    writer = pypdf.PdfWriter()
    buf1 = io.BytesIO()
    Image.new("RGB", (200, 300), (255, 255, 255)).save(buf1, format="PDF")
    buf1.seek(0)
    r1 = pypdf.PdfReader(buf1)
    writer.add_page(r1.pages[0])

    buf2 = io.BytesIO()
    Image.new("RGB", (200, 300), (240, 240, 240)).save(buf2, format="PDF")
    buf2.seek(0)
    r2 = pypdf.PdfReader(buf2)
    writer.add_page(r2.pages[0])

    parent = writer.add_outline_item(title="Chuong 1", page_number=0)
    writer.add_outline_item(title="Muc 1.1", page_number=1, parent=parent)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# F1: Packaging, Android Loopback Network Config & PDF Backends
# ---------------------------------------------------------------------------


def test_f1_pyproject_dependencies_and_manifest_are_store_safe():
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = config["project"]["dependencies"]
    assert "pypdf>=7.1.1" in deps
    assert "pillow>=13.0.0" in deps

    win_opt = config["project"]["optional-dependencies"]["windows"]
    win_flet = config["tool"]["flet"]["windows"]["dependencies"]
    for pkg in ("lxml>=6.1.0", "pypdfium2>=5.9.0", "webview2-Microsoft.Web.WebView2.Core==3.2.1"):
        assert pkg in win_opt
        assert pkg in win_flet

    android_deps = config["tool"]["flet"]["android"]["dependencies"]
    assert not any(str(d).lower().startswith("pypdfium2") for d in android_deps)
    assert not any(str(d).lower().startswith("pymupdf") for d in android_deps)

    manifest_app = config["tool"]["flet"]["android"]["manifest_application"]
    assert manifest_app.get("allowBackup") == "false"
    assert manifest_app.get("networkSecurityConfig") == "@xml/network_security_config"
    assert manifest_app.get("usesCleartextTraffic") in (None, "false")


def test_f1_android_network_security_config_permits_loopback_only():
    xml_path = (
        REPO_ROOT
        / "extensions/flet_uth_background_sync/flutter/flet_uth_background_sync"
        / "android/src/main/res/xml/network_security_config.xml"
    )
    assert xml_path.is_file()
    root = ElementTree.fromstring(xml_path.read_text(encoding="utf-8"))
    assert root.tag == "network-security-config"

    base_cfg = root.find("base-config")
    assert base_cfg is not None
    assert base_cfg.attrib.get("cleartextTrafficPermitted") == "false"

    domain_cfgs = root.findall("domain-config")
    assert len(domain_cfgs) == 1
    assert domain_cfgs[0].attrib.get("cleartextTrafficPermitted") == "true"
    domains = {
        el.text: el.attrib.get("includeSubdomains")
        for el in domain_cfgs[0].findall("domain")
    }
    assert domains == {"127.0.0.1": "false", "localhost": "false"}


def test_f1_pdf_renderer_pdfium_bookmark_get_title_and_get_dest():
    class _Dest:
        def __init__(self, idx: int | None):
            self._idx = idx

        def get_index(self) -> int | None:
            return self._idx

    class _Bookmark:
        def __init__(self, title: str, idx: int | None, level: int = 0):
            self._title = title
            self._idx = idx
            self.level = level

        def get_title(self) -> str:
            return self._title

        def get_dest(self):
            return _Dest(self._idx) if self._idx is not None else None

    class _AttrBookmark:
        def __init__(self, title: str, page_index: int, level: int = 0):
            self.title = title
            self.page_index = page_index
            self.level = level

    class _FakePdfDoc:
        def get_toc(self, max_depth: int = 6):
            return [
                _Bookmark("Chuong 1", 0, level=0),
                _Bookmark("Muc 1.1", 1, level=1),
                _Bookmark("Missing dest", None, level=0),
                _Bookmark("Out of range", 9, level=0),
                _AttrBookmark("Attr fallback", 2, level=1),
            ]

    entries = PdfDocumentSession._read_pdfium_outline(_FakePdfDoc(), 3)
    assert [(e.title, e.page_index, e.level) for e in entries] == [
        ("Chuong 1", 0, 0),
        ("Muc 1.1", 1, 1),
        ("Attr fallback", 2, 1),
    ]

    # Also verify against a real PDF with /Outlines when pypdfium2 is installed
    pdf_bytes = _build_minimal_pdf_with_outline()
    session = PdfDocumentSession(pdf_bytes)
    try:
        assert session.supported is True
        assert session.page_count == 2
        if pdf_mod._pdfium is not None:
            assert [(e.title, e.page_index, e.level) for e in session.outline] == [
                ("Chuong 1", 0, 0),
                ("Muc 1.1", 1, 1),
            ]
    finally:
        session.close()


def test_f1_pdf_renderer_pymupdf_fallback_and_pypdf_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    pdf_bytes = _build_minimal_pdf_with_outline()

    # 1. Mock _pdfium=None and fake _pymupdf
    class _FakeRect:
        width = 400.0
        height = 600.0

    class _FakePixmap:
        width = 8
        height = 6
        n = 3
        samples = bytes([245, 245, 245] * (8 * 6))

    class _FakePage:
        rect = _FakeRect()

        def __init__(self, text: str):
            self._text = text

        def get_pixmap(self, matrix=None, alpha=False):
            return _FakePixmap()

        def get_text(self, mode="text"):
            return self._text

    class _FakeMuPdfDoc:
        needs_pass = False
        page_count = 2
        closed = False

        def __init__(self):
            self._pages = [_FakePage("Trang mot PyMuPDF"), _FakePage("Trang hai tim kiem")]

        def __getitem__(self, idx: int):
            return self._pages[idx]

        def get_toc(self, simple: bool = True):
            return [[1, "Phan 1", 1], [2, "Phan 1.1", 2]]

        def close(self):
            self.closed = True

    class _FakeMuPdf:
        class Matrix:
            def __init__(self, sx: float, sy: float):
                self.sx = sx
                self.sy = sy

        @staticmethod
        def open(*, stream: bytes, filetype: str):
            return _FakeMuPdfDoc()

    monkeypatch.setattr(pdf_mod, "_pdfium", None)
    monkeypatch.setattr(pdf_mod, "_pymupdf", _FakeMuPdf)

    sess = PdfDocumentSession(pdf_bytes, cache_dir=tmp_path)
    try:
        assert sess.supported is True
        assert sess.can_render_images is True
        assert sess.page_count == 2
        assert [(e.title, e.page_index, e.level) for e in sess.outline] == [
            ("Phan 1", 0, 0),
            ("Phan 1.1", 1, 1),
        ]
        assert sess.get_page_size(0) == (400.0, 600.0)

        light = sess.render_page_png(0, scale=1.5, dark_mode=False)
        dark = sess.render_page_png(0, scale=1.5, dark_mode=True)
        assert light is not None and light.png_bytes.startswith(b"\x89PNG")
        assert dark is not None and dark.png_bytes != light.png_bytes
        assert sess.peek_cached_page_png(0, scale=1.5, dark_mode=False) is not None

        hits = sess.search("tim kiem")
        assert len(hits) == 1 and hits[0].page_index == 1
        mupdf_doc = sess._mupdf_doc
    finally:
        sess.close()
    assert mupdf_doc.closed is True

    # Password-protected PyMuPDF document rejected cleanly
    class _LockedMuPdfDoc(_FakeMuPdfDoc):
        needs_pass = True

    class _LockedMuPdf(_FakeMuPdf):
        @staticmethod
        def open(*, stream: bytes, filetype: str):
            return _LockedMuPdfDoc()

    monkeypatch.setattr(pdf_mod, "_pymupdf", _LockedMuPdf)
    locked_sess = PdfDocumentSession(pdf_bytes)
    assert locked_sess.supported is False
    assert "mật khẩu" in locked_sess.error_message.lower()

    # 2. Pure-Python pypdf-only fallback (_pdfium=None, _pymupdf=None)
    monkeypatch.setattr(pdf_mod, "_pymupdf", None)
    pypdf_sess = PdfDocumentSession(pdf_bytes)
    try:
        assert pypdf_sess.supported is True
        assert pypdf_sess.can_render_images is False
        assert pypdf_sess.page_count == 2
        assert pypdf_sess.render_page_png(0) is None
        assert len(pypdf_sess.outline) == 2
    finally:
        pypdf_sess.close()


# ---------------------------------------------------------------------------
# F2: Unified EmbeddedWebViewHost & CoursesCoordinator wiring
# ---------------------------------------------------------------------------


def test_f2_build_embedded_webview_host_rejects_unsafe_and_headless_platforms(
    monkeypatch: pytest.MonkeyPatch,
):
    import threading

    assert build_embedded_webview_host("") is None
    assert build_embedded_webview_host("javascript:alert(1)") is None
    assert build_embedded_webview_host("file:///C:/secret.html") is None

    assert not hasattr(host_mod, "IS_ANDROID")
    assert not hasattr(host_mod, "IS_IOS")
    monkeypatch.setattr(platform_utils, "IS_ANDROID", False)
    monkeypatch.setattr(platform_utils, "IS_IOS", False)
    monkeypatch.setattr(sys, "platform", "win32")

    assert build_embedded_webview_host("https://courses.ut.edu.vn/") is None

    # Dynamic lookup on platform_utils switches page=None without reloading or patching host_mod
    monkeypatch.setattr(platform_utils, "IS_ANDROID", True)
    dyn_host = build_embedded_webview_host("https://courses.ut.edu.vn/", page=None)
    assert dyn_host is not None
    assert dyn_host.backend == "flet_webview"
    dyn_host.dispose()
    monkeypatch.setattr(platform_utils, "IS_ANDROID", False)
    assert build_embedded_webview_host("https://courses.ut.edu.vn/", page=None) is None
    assert (
        build_embedded_webview_host(
            "https://courses.ut.edu.vn/",
            page=SimpleNamespace(web=True, platform="android"),  # type: ignore[arg-type]
        )
        is None
    )
    assert (
        build_embedded_webview_host(
            "https://courses.ut.edu.vn/",
            page=SimpleNamespace(web=True, platform="windows"),  # type: ignore[arg-type]
        )
        is None
    )
    assert (
        build_embedded_webview_host(
            "https://courses.ut.edu.vn/",
            page=SimpleNamespace(web=False, platform="linux"),  # type: ignore[arg-type]
        )
        is None
    )

    # Duck-typed FakePage(web=False, platform="windows") respects _webview2_runtime_available
    monkeypatch.setattr(host_mod, "_webview2_runtime_available", lambda: False)
    assert (
        build_embedded_webview_host(
            "https://courses.ut.edu.vn/",
            page=SimpleNamespace(web=False, platform="windows"),  # type: ignore[arg-type]
        )
        is None
    )

    # With _webview2_runtime_available() True and monkeypatched dock, duck-typed FakePage routes to WebView2
    started_docks: list[WindowsWebView2Dock] = []

    class _FakeDock(WindowsWebView2Dock):
        def start(self) -> None:
            started_docks.append(self)

    with monkeypatch.context() as mctx:
        mctx.setattr(host_mod, "_webview2_runtime_available", lambda: True)
        mctx.setattr(host_mod, "WindowsWebView2Dock", _FakeDock)
        fake_win_host = build_embedded_webview_host(
            "https://courses.ut.edu.vn/",
            page=SimpleNamespace(web=False, platform="windows", title="UTHelper"),  # type: ignore[arg-type]
        )
        assert fake_win_host is not None
        try:
            assert fake_win_host.backend == "webview2"
            assert len(started_docks) == 1
            assert started_docks[0]._hwnd_retries == 1
        finally:
            fake_win_host.dispose()

    # Real (unmocked) WindowsWebView2Dock fails fast via on_error when no real Win32 HWND exists
    if host_mod._webview2_runtime_available():
        try:
            existing_hwnd = WindowsWebView2Dock._find_parent_hwnd("UTHelper")
        except Exception:
            existing_hwnd = 0
        if existing_hwnd:
            monkeypatch.setattr(
                WindowsWebView2Dock,
                "_find_parent_hwnd",
                staticmethod(lambda _title: 0),
            )
        err_event = threading.Event()
        errors: list[str] = []
        real_host = build_embedded_webview_host(
            "https://courses.ut.edu.vn/",
            page=SimpleNamespace(web=False, platform="windows", title="UTHelper"),  # type: ignore[arg-type]
            on_error=lambda reason: (errors.append(reason), err_event.set()),
        )
        assert real_host is not None
        try:
            assert real_host.backend == "webview2"
            assert real_host.dock is not None
            assert real_host.dock._hwnd_retries == 1
            assert err_event.wait(timeout=1.5) is True
            assert len(errors) == 1 and errors[0]
        finally:
            real_host.dispose()


def test_f2_embedded_webview_host_flet_webview_backend(monkeypatch: pytest.MonkeyPatch):
    import flet_webview as fwv

    ran_tasks: list[str] = []

    def _run_task(coro_fn, *args):
        ran_tasks.append(getattr(coro_fn, "__name__", "task"))
        asyncio.run(coro_fn(*args))

    fake_page = SimpleNamespace(web=False, platform="android", run_task=_run_task)

    events: list[tuple[str, object]] = []
    host = build_embedded_webview_host(
        "https://courses.ut.edu.vn/mod/page/view.php?id=1",
        html="<p>Initial</p>",
        page=fake_page,  # type: ignore[arg-type]
        on_ready=lambda: events.append(("ready", True)),
        on_error=lambda err: events.append(("error", err)),
        on_message=lambda msg: events.append(("msg", msg)),
        on_url_changed=lambda u: events.append(("url", u)),
        on_title_changed=lambda t: events.append(("title", t)),
        on_loading_changed=lambda b: events.append(("loading", b)),
        on_history_changed=lambda b, f: events.append(("hist", (b, f))),
    )
    assert host is not None
    assert host.backend == "flet_webview"

    slot = host.control()
    wv = slot.content
    assert isinstance(wv, fwv.WebView)

    wv.load_html = AsyncMock()  # type: ignore[method-assign]
    wv.load_request = AsyncMock()  # type: ignore[method-assign]
    wv.run_javascript = AsyncMock()  # type: ignore[method-assign]
    wv.go_back = AsyncMock()  # type: ignore[method-assign]
    wv.go_forward = AsyncMock()  # type: ignore[method-assign]
    wv.reload = AsyncMock()  # type: ignore[method-assign]
    wv.can_go_back = AsyncMock(return_value=True)  # type: ignore[method-assign]
    wv.can_go_forward = AsyncMock(return_value=False)  # type: ignore[method-assign]
    wv.get_title = AsyncMock(return_value="Bai giang")  # type: ignore[method-assign]

    # Pre-mount load_html is buffered and flushed on did_mount()
    host.load_html("<h1>PreMount</h1>", base_url="http://127.0.0.1:9000/viewer/t/")
    assert wv.load_html.await_count == 0
    slot.did_mount()
    assert wv.load_html.await_count == 1
    assert host.can_go_back() is True
    assert host.can_go_forward() is False
    assert ("hist", (True, False)) in events

    # Post-mount load_html when attached to page
    monkeypatch.setattr(fwv.WebView, "page", property(lambda _self: fake_page))
    host.load_html("<h1>Mounted</h1>", base_url="http://127.0.0.1:9000/viewer/t/")
    assert wv.load_html.await_count == 2

    asyncio.run(host.eval_js("window.setTheme('dark')"))
    wv.run_javascript.assert_awaited_once_with("window.setTheme('dark')")

    host.navigate("https://courses.ut.edu.vn/course/view.php?id=2")
    wv.load_request.assert_awaited_once()
    host.go_back()
    wv.go_back.assert_awaited_once()
    host.go_forward()
    wv.go_forward.assert_awaited_once()
    host.reload()
    wv.reload.assert_awaited_once()

    # Event bridging including __UTH_MSG__: prefix filtering
    wv.on_page_started(SimpleNamespace(data="https://courses.ut.edu.vn/a"))
    wv.on_page_ended(SimpleNamespace(data="https://courses.ut.edu.vn/a"))
    wv.on_url_change(SimpleNamespace(data="https://courses.ut.edu.vn/b"))
    wv.on_web_resource_error(SimpleNamespace(data="ERR_CONN"))
    wv.on_console_message(SimpleNamespace(message="normal debug log"))
    wv.on_console_message(SimpleNamespace(message='__UTH_MSG__:{"page":3}'))

    assert ("loading", True) in events
    assert ("ready", True) in events
    assert ("title", "Bai giang") in events
    assert ("url", "https://courses.ut.edu.vn/b") in events
    assert ("error", "ERR_CONN") in events
    assert ("msg", '{"page":3}') in events
    assert ("msg", "normal debug log") not in events

    host.suspend()
    assert slot.visible is False
    host.resume()
    assert slot.visible is True
    host.sync_bounds()
    host.sync_bounds(0, 50, 400, 700)
    host.update_theme()
    host.dispose()


def test_f2_embedded_webview_host_webview2_backend_and_eval_js(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_page = MagicMock(spec=ft.Page)
    fake_page.web = False
    fake_page.platform = ft.PagePlatform.WINDOWS
    fake_page.title = "UTHelper"

    created_docks: list[WindowsWebView2Dock] = []

    class _FakeDock(WindowsWebView2Dock):
        def start(self) -> None:
            created_docks.append(self)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(host_mod, "_webview2_runtime_available", lambda: True)
    monkeypatch.setattr(host_mod, "WindowsWebView2Dock", _FakeDock)
    # The logical command bridge is exercised on Linux CI too; native window
    # operations belong to the separate Windows fixture, not this fake dock.
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(SW_SHOWNOACTIVATE=4, SW_HIDE=0))
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(ShowWindow=lambda *_: None))

    ready_events: list[str] = []
    messages: list[str] = []
    history_events: list[tuple[bool, bool]] = []

    host = build_embedded_webview_host(
        "http://127.0.0.1:5000/viewer/tok/docx_host.html",
        page=fake_page,
        on_ready=lambda: ready_events.append("ready"),
        on_error=lambda err: ready_events.append(f"err:{err}"),
        on_message=messages.append,
        on_history_changed=lambda b, f: history_events.append((b, f)),
    )
    assert host is not None
    assert host.backend == "webview2"
    assert len(created_docks) == 1
    dock = created_docks[0]

    # Exercise CoreWebView2 event bindings & execute_script_async
    executed_scripts: list[str] = []
    navigated_html: list[str] = []
    callbacks: dict[str, object] = {}

    class _FakeOp:
        def __init__(self):
            self._completed = None

        @property
        def completed(self):
            return self._completed

        @completed.setter
        def completed(self, fn):
            self._completed = fn
            fn(self, 0)

    class _FakeCore:
        can_go_back = True
        can_go_forward = False
        document_title = "Docx Viewer"
        source = "http://127.0.0.1:5000/viewer/tok/docx_host.html"

        def navigate(self, _u: str) -> None:
            pass

        def navigate_to_string(self, html: str) -> None:
            navigated_html.append(html)

        def execute_script_async(self, script: str):
            executed_scripts.append(script)
            return _FakeOp()

        def add_document_title_changed(self, fn):
            callbacks["title"] = fn

        def add_source_changed(self, fn):
            callbacks["source"] = fn

        def add_navigation_starting(self, fn):
            callbacks["nav_start"] = fn

        def add_navigation_completed(self, fn):
            callbacks["nav_done"] = fn

        def add_history_changed(self, fn):
            callbacks["history"] = fn

        def add_web_message_received(self, fn):
            callbacks["web_msg"] = fn

        def add_new_window_requested(self, fn):
            callbacks["new_win"] = fn

    core = _FakeCore()
    dock._core = core
    dock._bind_events(core)

    callbacks["nav_done"](core, SimpleNamespace(is_success=True))  # type: ignore[operator]
    assert ready_events == ["ready"]
    assert host.can_go_back() is True
    assert history_events[-1] == (True, False)

    callbacks["web_msg"](  # type: ignore[operator]
        core,
        SimpleNamespace(try_get_web_message_as_string=lambda: '{"event":"loaded"}'),
    )
    assert messages == ['{"event":"loaded"}']

    host.load_html("<html><body>Inline</body></html>")
    dock._drain_commands()
    assert navigated_html == ["<html><body>Inline</body></html>"]

    async def _run_eval() -> None:
        task = asyncio.create_task(host.eval_js("window.renderDoc()"))
        await asyncio.sleep(0.01)
        dock._drain_commands()
        await task

    asyncio.run(_run_eval())
    assert executed_scripts == ["window.renderDoc()"]

    host.sync_bounds(10, 60, 700, 500, bottom_inset=44)
    assert dock._bounds_dip == (10.0, 60.0, 700.0, 500.0)
    assert dock._bottom_inset_dip == 44.0

    host.suspend()
    assert dock._visible is False
    host.resume()
    assert dock._visible is True
    host.dispose()


def test_f2_coordinator_removes_quiz_import_and_wires_webview_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    src_text = (REPO_ROOT / "src/gui/controllers/courses_coordinator.py").read_text(
        encoding="utf-8"
    )
    assert "flet_uth_quiz" not in src_text

    assert _try_build_embedded_webview("javascript:alert(1)") is None
    monkeypatch.setattr(platform_utils, "IS_ANDROID", False)
    monkeypatch.setattr(platform_utils, "IS_IOS", False)
    assert _try_build_embedded_webview("https://courses.ut.edu.vn/mod/quiz/view.php?id=1") is None

    monkeypatch.setattr(platform_utils, "IS_ANDROID", True)
    ctrl = _try_build_embedded_webview("https://courses.ut.edu.vn/mod/quiz/view.php?id=1")
    assert ctrl is not None

    monkeypatch.setattr(platform_utils, "IS_ANDROID", False)
    fake_android_page = SimpleNamespace(web=False, platform=ft.PagePlatform.ANDROID)
    ctrl_with_page = _try_build_embedded_webview(
        "https://courses.ut.edu.vn/mod/quiz/view.php?id=1",
        page=fake_android_page,  # type: ignore[arg-type]
    )
    assert ctrl_with_page is not None
    from core.courses.archive_service import ArchiveService
    from core.courses.cache_store import CourseCacheStore
    from core.courses.catalog_service import CourseCatalogService
    from core.courses.preview_cache import PreviewFileCache

    store = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|u")
    pcache = PreviewFileCache(tmp_path / "preview")
    server = LocalPreviewServer()
    fake_page = SimpleNamespace(
        overlay=[],
        web=False,
        platform="windows",
        title="UTHelper",
        width=390,
        height=844,
        update=lambda: None,
    )
    coord = CoursesCoordinator(
        page=fake_page,  # type: ignore[arg-type]
        catalog_service=CourseCatalogService(cache_store=store, preview_cache=pcache),
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=server,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        coord.open_in_app_webview("courses", "Moodle", "https://courses.ut.edu.vn/course/view.php?id=1")
        pane = coord.courses_view._overlays[-1]
        assert isinstance(pane, CoursesWebViewPane)
        assert pane._embedded_factory is None
        assert pane._pane_width <= 600.0
        assert coord.build_webview_host("javascript:alert(1)") is None

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(host_mod, "_webview2_runtime_available", lambda: False)
        assert coord.build_webview_host("https://courses.ut.edu.vn/") is None

        started_docks: list[WindowsWebView2Dock] = []

        class _FakeDock(WindowsWebView2Dock):
            def start(self) -> None:
                started_docks.append(self)

        monkeypatch.setattr(host_mod, "_webview2_runtime_available", lambda: True)
        monkeypatch.setattr(host_mod, "WindowsWebView2Dock", _FakeDock)
        wv_host = coord.build_webview_host("https://courses.ut.edu.vn/")
        assert isinstance(wv_host, EmbeddedWebViewHost)
        assert wv_host.backend == "webview2"
        assert len(started_docks) == 1
        assert started_docks[0]._hwnd_retries == 1
        wv_host.dispose()

        # CoursesCoordinator forwards self._page through _build_embedded_webview_for_page
        # even when platform_utils.IS_ANDROID/IS_IOS are False
        fake_page.platform = ft.PagePlatform.ANDROID
        forwarded_ctrl = coord._build_embedded_webview_for_page(
            "https://courses.ut.edu.vn/mod/quiz/view.php?id=7"
        )
        assert forwarded_ctrl is not None

        # And preserves 1-arg monkeypatch compatibility for _try_build_embedded_webview
        seen_urls: list[str] = []
        sentinel = ft.Container(expand=True)
        with monkeypatch.context() as mctx:
            mctx.setattr(
                "gui.controllers.courses_coordinator._try_build_embedded_webview",
                lambda u: (seen_urls.append(u), sentinel)[1],
            )
            compat_ctrl = coord._build_embedded_webview_for_page(
                "https://courses.ut.edu.vn/mod/quiz/view.php?id=8"
            )
        assert compat_ctrl is sentinel
        assert seen_urls == ["https://courses.ut.edu.vn/mod/quiz/view.php?id=8"]
    finally:
        coord.close()


# ---------------------------------------------------------------------------
# F3: LocalPreviewServer /viewer/<session_token>/<asset_path>
# ---------------------------------------------------------------------------


def test_f3_local_preview_server_viewer_assets_and_security(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assets_dir = tmp_path / "office_viewer"
    assets_dir.mkdir()
    (assets_dir / "vendor").mkdir()
    (assets_dir / "docx_host.html").write_text("<html><body>Docx</body></html>", encoding="utf-8")
    (assets_dir / "app.js").write_text("console.log('ok');", encoding="utf-8")
    (assets_dir / "styles.css").write_text("body { margin: 0; }", encoding="utf-8")
    (assets_dir / "font.woff2").write_bytes(b"wOF2\x00\x01")
    (assets_dir / "icon.svg").write_text("<svg></svg>", encoding="utf-8")
    (assets_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (assets_dir / "vendor" / "jszip.min.js").write_text("// zip", encoding="utf-8")
    (assets_dir / ".gitkeep").write_text("", encoding="utf-8")
    (assets_dir / "secret.py").write_text("x = 1", encoding="utf-8")
    (assets_dir / "data.json").write_text("{}", encoding="utf-8")
    (tmp_path / "outside.html").write_text("<h1>LEAK</h1>", encoding="utf-8")

    srv = LocalPreviewServer(viewer_assets_dir=assets_dir)
    try:
        base_url = srv.viewer_base_url()
        assert base_url.startswith(f"http://127.0.0.1:{srv.port}/viewer/")
        assert base_url.endswith("/")

        expected_mimes = {
            "docx_host.html": "text/html; charset=utf-8",
            "app.js": "application/javascript; charset=utf-8",
            "styles.css": "text/css; charset=utf-8",
            "font.woff2": "font/woff2",
            "icon.svg": "image/svg+xml",
            "logo.png": "image/png",
            "vendor/jszip.min.js": "application/javascript; charset=utf-8",
        }
        for rel, mime in expected_mimes.items():
            st, hdrs, body = _http_request(f"{base_url}{rel}")
            assert st == 200, rel
            assert hdrs["content-type"] == mime
            assert hdrs["cross-origin-resource-policy"] == "same-origin"
            assert hdrs["cache-control"] == "no-store, private"
            assert hdrs["x-content-type-options"] == "nosniff"
            assert hdrs["referrer-policy"] == "no-referrer"
            if rel.endswith(".html"):
                assert hdrs["content-security-policy"] == _VIEWER_APP_CSP
            elif rel.endswith(".svg"):
                assert hdrs["content-security-policy"] == _HTML_CSP
            else:
                assert "content-security-policy" not in hdrs
            assert len(body) > 0

        # HEAD support
        st_h, hdrs_h, body_h = _http_request(f"{base_url}app.js", method="HEAD")
        assert st_h == 200 and body_h == b""
        assert hdrs_h["content-length"] == str(len(b"console.log('ok');"))

        # Wrong token -> 403
        st_bad_tok, _, _ = _http_request(
            f"http://127.0.0.1:{srv.port}/viewer/wrongtoken/docx_host.html"
        )
        assert st_bad_tok == 403

        # Traversal & backslash & null byte defenses
        for evil in (
            "../outside.html",
            "%2e%2e/outside.html",
            "%2e%2e%2foutside.html",
            "vendor/..%2f..%2foutside.html",
            "%2e%2e%5coutside.html",
            "C:/outside.html",
            "app.js%00.html",
        ):
            st_evil, _, body_evil = _http_request(f"{base_url}{evil}")
            assert st_evil in (403, 404), evil
            assert b"LEAK" not in body_evil

        # Non-whitelisted extensions and directory requests -> 404
        for blocked in (".gitkeep", "secret.py", "data.json", "vendor/", ""):
            st_blk, _, _ = _http_request(f"{base_url}{blocked}")
            assert st_blk == 404, blocked

        # POST rejected with 405
        st_post, _, _ = _http_request(f"{base_url}docx_host.html", method="POST")
        assert st_post == 405

        # Existing /<16-hex-id>?token=... continues enforcing _HTML_CSP
        item_url = srv.register_html("<html><body>Item</body></html>")
        st_item, hdrs_item, _ = _http_request(item_url)
        assert st_item == 200
        assert hdrs_item["content-security-policy"] == _HTML_CSP
    finally:
        srv.stop()

    # Asset directory resolver priority
    assert _resolve_viewer_assets_dir(assets_dir) == assets_dir
    monkeypatch.setenv("FLET_ASSETS_DIR", str(tmp_path / "flet_assets"))
    assert _resolve_viewer_assets_dir() == tmp_path / "flet_assets" / "office_viewer"
    monkeypatch.delenv("FLET_ASSETS_DIR", raising=False)
    monkeypatch.setattr(preview_mod.sys, "frozen", True, raising=False)
    meipass_dir = tmp_path / "meipass"
    (meipass_dir / "assets" / "office_viewer").mkdir(parents=True)
    monkeypatch.setattr(preview_mod.sys, "_MEIPASS", str(meipass_dir), raising=False)
    assert _resolve_viewer_assets_dir() == meipass_dir / "assets" / "office_viewer"


# ---------------------------------------------------------------------------
# F4: CoursesWebViewPane Mobile Header, Nav Bar, Loopback Hide & handle_back
# ---------------------------------------------------------------------------


def test_f4_webview_pane_mobile_header_nav_loopback_and_handle_back():
    fake_host = MagicMock(spec=EmbeddedWebViewHost)
    fake_host.backend = "flet_webview"
    fake_host.dock = None
    fake_host.control.return_value = ft.Container(expand=True)
    can_back = [True]
    fake_host.can_go_back.side_effect = lambda: can_back[0]

    fake_page = SimpleNamespace(
        overlay=[],
        web=False,
        platform="android",
        title="UTHelper",
        width=390,
        height=844,
        update=lambda: None,
    )
    pane = CoursesWebViewPane(
        page=fake_page,  # type: ignore[arg-type]
        source="courses",
        title="Bai giang",
        url="https://courses.ut.edu.vn/mod/page/view.php?id=9",
        on_back=lambda: None,
        on_open_external=lambda _u: None,
        embedded_webview_factory=lambda _u: fake_host,  # type: ignore[arg-type]
        pane_width=390,
    )
    try:
        # Mobile <600dp: simplified 3-item top header + 44dp bottom nav bar
        header_controls = pane._header_container.content.controls
        assert header_controls == [pane._back_btn, pane._title_col, pane._more_menu_btn]
        assert pane._mobile_nav_bar.visible is True
        assert pane._mobile_nav_bar.height == 44
        assert pane._mobile_nav_row.controls == [
            pane._wv_back_btn,
            pane._wv_fwd_btn,
            pane._wv_reload_btn,
            pane._ext_btn,
        ]
        assert pane._ext_btn.visible is True
        assert pane._more_menu_item_ext.visible is True

        # Navigation buttons delegate to EmbeddedWebViewHost
        pane._wv_back_btn.on_click(None)
        fake_host.go_back.assert_called_once()
        pane._wv_fwd_btn.on_click(None)
        fake_host.go_forward.assert_called_once()
        pane._wv_reload_btn.on_click(None)
        fake_host.reload.assert_called_once()

        # handle_back() consumes browser history first, then returns False to pop overlay
        fake_host.go_back.reset_mock()
        assert pane.handle_back() is True
        fake_host.go_back.assert_called_once()
        can_back[0] = False
        assert pane.handle_back() is False

        # Responsive transition back to desktop (>=600dp)
        pane.sync_bounds(0, 0, 900, 700)
        assert pane._mobile_nav_bar.visible is False
        assert pane._header_container.content.controls == [
            pane._back_btn,
            pane._title_col,
            pane._wv_back_btn,
            pane._wv_fwd_btn,
            pane._wv_reload_btn,
            pane._expand_btn,
            pane._ext_btn,
        ]
        fake_host.sync_bounds.assert_called()
        pane.update_theme()
    finally:
        pane.dispose()

    # Loopback URLs hide external-open button and overflow menu item
    for loopback_url in (
        "http://127.0.0.1:5000/viewer/tok/docx_host.html",
        "http://localhost:8000/preview",
    ):
        assert _is_loopback_url(loopback_url) is True
        lb_pane = CoursesWebViewPane(
            page=fake_page,  # type: ignore[arg-type]
            source="courses",
            title="Local Doc",
            url=loopback_url,
            on_back=lambda: None,
            on_open_external=lambda _u: None,
            embedded_webview_factory=lambda _u: fake_host,  # type: ignore[arg-type]
            pane_width=390,
        )
        try:
            assert lb_pane._ext_btn.visible is False
            assert lb_pane._more_menu_item_ext.visible is False
            assert lb_pane._more_menu_item_ext not in lb_pane._more_menu_btn.items
        finally:
            lb_pane.dispose()


# ---------------------------------------------------------------------------
# F5: Cross-Platform External File Open / Save
# ---------------------------------------------------------------------------


def test_f5_open_external_mobile_uses_file_picker_without_file_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from core.courses.archive_service import ArchiveService
    from core.courses.cache_store import CourseCacheStore
    from core.courses.catalog_service import CourseCatalogService
    from core.courses.preview_cache import PreviewFileCache

    store = CourseCacheStore(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|u")
    pcache = PreviewFileCache(tmp_path / "preview")
    server = LocalPreviewServer()
    fake_page = SimpleNamespace(
        overlay=[],
        web=False,
        platform=ft.PagePlatform.ANDROID,
        title="UTHelper",
        width=390,
        height=844,
        update=lambda: None,
    )
    coord = CoursesCoordinator(
        page=fake_page,  # type: ignore[arg-type]
        catalog_service=CourseCatalogService(cache_store=store, preview_cache=pcache),
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=server,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        launch_mock = AsyncMock()
        save_mock = AsyncMock(return_value="saved/slide_1.pptx")
        monkeypatch.setattr(ft.UrlLauncher, "launch_url", launch_mock)
        monkeypatch.setattr(ft.FilePicker, "save_file", save_mock)

        # Mobile page.platform routes directly to FilePicker.save_file without file:// URI
        asyncio.run(coord._open_external_async("../slide 1.pptx", b"PK\x03\x04"))
        assert launch_mock.await_count == 0
        save_mock.assert_awaited_once()
        assert save_mock.await_args.kwargs["file_name"] == "slide_1.pptx"
        assert save_mock.await_args.kwargs["src_bytes"] == b"PK\x03\x04"

        # platform_utils.IS_ANDROID also enforces mobile save even on generic page
        fake_page.platform = ft.PagePlatform.WINDOWS
        monkeypatch.setattr(platform_utils, "IS_ANDROID", True)
        asyncio.run(coord._open_external_async("report.pdf", b"%PDF-1.4"))
        assert launch_mock.await_count == 0
        assert save_mock.await_count == 2

        # Empty bytes on mobile guarded cleanly without calling FilePicker.save_file
        asyncio.run(coord._open_external_async("empty.docx", b""))
        assert save_mock.await_count == 2
        snacks = [item for item in fake_page.overlay if isinstance(item, ft.SnackBar)]
        assert len(snacks) == 1
        assert "tệp trống" in snacks[0].content.value

        # Desktop path still uses UrlLauncher with file:// URI
        monkeypatch.setattr(platform_utils, "IS_ANDROID", False)
        monkeypatch.setattr(platform_utils, "IS_IOS", False)
        asyncio.run(coord._open_external_async("desktop.pdf", b"%PDF-1.4"))
        assert launch_mock.await_count == 1
        assert launch_mock.await_args.args[0].startswith("file:///")
    finally:
        coord.close()
