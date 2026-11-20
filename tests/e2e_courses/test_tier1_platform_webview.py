"""Tier 1 E2E Tests — Platform Packaging, WebView Host, Loopback Viewer Route, & File Open/Save (F1–F5)."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import sys
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import flet as ft
import pytest

from core.courses.archive_service import ArchiveService
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.preview_cache import PreviewFileCache
from tests.e2e_courses.builders import (
    FakeCatalogService,
    build_docx_bytes,
    build_pdf_bytes,
    build_pptx_bytes,
    make_attachment,
    make_course,
    make_module,
    tiny_png_bytes,
)
from tests.e2e_courses.conftest import (
    FakePage,
    assert_min_touch_target,
    assert_no_fixed_width_overflow,
    assert_valid_flet_mro,
    bind_control_page,
    collect_texts,
    http_request,
    walk_controls,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _dep_names(specs: list[str]) -> list[str]:
    names: list[str] = []
    for raw in specs:
        head = raw.split(";", 1)[0].strip()
        for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "["):
            if sep in head:
                head = head.split(sep, 1)[0].strip()
        names.append(head.lower())
    return names


# ---------------------------------------------------------------------------
# F1: Store-Safe Packaging & PDF/Imaging Deps (>=5 tests)
# ---------------------------------------------------------------------------


def test_f1_pyproject_excludes_pypdfium2_and_includes_pillow_for_android() -> None:
    """F1: Android/cross-platform deps exclude 404 pypdfium2 and include pillow + flet-webview==0.86.5."""
    cfg = _load_pyproject()
    project_deps = cfg["project"]["dependencies"]
    android_deps = cfg["tool"]["flet"]["android"]["dependencies"]
    ios_deps = cfg["tool"]["flet"]["ios"]["dependencies"]

    assert "pypdfium2" not in _dep_names(project_deps)
    assert "pypdfium2" not in _dep_names(android_deps)
    assert "pillow" in (_dep_names(project_deps) + _dep_names(android_deps))
    assert "flet-webview==0.86.5" in android_deps
    assert "flet-webview==0.86.5" in ios_deps


def test_f1_pyproject_isolates_windows_only_dependencies() -> None:
    """F1: Windows-only deps live exclusively in windows optional/flet groups, never [project].dependencies."""
    cfg = _load_pyproject()
    project_names = set(_dep_names(cfg["project"]["dependencies"]))
    opt_win_names = set(_dep_names(cfg["project"]["optional-dependencies"]["windows"]))
    flet_win_names = set(_dep_names(cfg["tool"]["flet"]["windows"]["dependencies"]))

    windows_only = {
        "pywin32",
        "webview2-microsoft.web.webview2.core",
        "winrt-windows.applicationmodel",
        "windows-toasts",
        "pystray",
        "keyring",
        "pypdfium2",
    }
    assert project_names.isdisjoint(windows_only)
    assert windows_only <= opt_win_names
    assert windows_only <= flet_win_names


def test_f1_ch_play_loopback_network_security_config() -> None:
    """F1: Android disables backups/global cleartext and allows loopback 127.0.0.1/localhost only."""
    cfg = _load_pyproject()
    manifest_app = cfg["tool"]["flet"]["android"].get("manifest_application", {})

    assert str(manifest_app.get("allowBackup", "")).lower() == "false"
    assert manifest_app.get("networkSecurityConfig") == "@xml/network_security_config"
    assert str(manifest_app.get("usesCleartextTraffic", "")).lower() != "true"

    xml_candidates = list(REPO_ROOT.rglob("network_security_config.xml"))
    assert xml_candidates, "Expected network_security_config.xml in Android resources"

    tree = ET.fromstring(xml_candidates[0].read_text(encoding="utf-8"))
    base_cfg = tree.find("base-config")
    assert base_cfg is not None
    assert base_cfg.attrib.get("cleartextTrafficPermitted") == "false"

    domain_cfgs = tree.findall("domain-config")
    loopback_domains: set[str] = set()
    for dc in domain_cfgs:
        if dc.attrib.get("cleartextTrafficPermitted") == "true":
            for dom in dc.findall("domain"):
                if dom.text:
                    loopback_domains.add(dom.text.strip())
    assert {"127.0.0.1", "localhost"} <= loopback_domains


def test_f1_android_build_isolation_and_strict_loopback_cleartext() -> None:
    """F1: Android optional-deps isolate cleanly, allowBackup=false, and cleartext is restricted strictly to loopback."""
    cfg = _load_pyproject()
    opt_deps = cfg["project"]["optional-dependencies"]
    assert "android-build" in opt_deps
    android_build_names = set(_dep_names(opt_deps["android-build"]))
    assert "flet-android-notifications" in android_build_names

    windows_only = {
        "pywin32",
        "webview2-microsoft.web.webview2.core",
        "winrt-windows.applicationmodel",
        "windows-toasts",
        "pystray",
        "keyring",
        "pypdfium2",
    }
    assert android_build_names.isdisjoint(windows_only)

    manifest_app = cfg["tool"]["flet"]["android"].get("manifest_application", {})
    assert str(manifest_app.get("allowBackup", "")).lower() == "false"
    assert manifest_app.get("networkSecurityConfig") == "@xml/network_security_config"
    assert str(manifest_app.get("usesCleartextTraffic", "")).lower() != "true"

    xml_candidates = list(REPO_ROOT.rglob("network_security_config.xml"))
    assert xml_candidates, "Expected network_security_config.xml in Android resources"
    tree = ET.fromstring(xml_candidates[0].read_text(encoding="utf-8"))
    base_cfg = tree.find("base-config")
    assert base_cfg is not None
    assert base_cfg.attrib.get("cleartextTrafficPermitted") == "false"

    for dc in tree.findall("domain-config"):
        if str(dc.attrib.get("cleartextTrafficPermitted", "")).lower() == "true":
            domains = {dom.text.strip() for dom in dc.findall("domain") if dom.text}
            assert domains, "cleartext domain-config must declare explicit loopback domains"
            assert domains <= {"127.0.0.1", "localhost"}, (
                f"Non-loopback cleartext domain(s) forbidden: {domains - {'127.0.0.1', 'localhost'}}"
            )


def test_f1_pdf_session_pure_python_pypdf_fallback_without_native_renderers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1: PdfDocumentSession opens, extracts text, outlines, and searches via pypdf when pypdfium2/PIL are absent."""
    import core.courses.pdf_renderer as pdf_mod

    monkeypatch.setattr(pdf_mod, "_pdfium", None)
    monkeypatch.setattr(pdf_mod, "_pymupdf", None)
    monkeypatch.setattr(pdf_mod, "_PilImage", None)

    pdf_bytes = build_pdf_bytes(
        ["Trang 1: Tong quan he thong", "Trang 2: Thuat toan Dijkstra", "Trang 3: Ket luan"],
        outline_titles=[("Tong quan", 0), ("Thuat toan", 1)],
    )
    with pdf_mod.PdfDocumentSession(pdf_bytes) as session:
        assert session.supported is True
        assert session.can_render_images is False
        assert session.page_count == 3
        assert "Dijkstra" in session.extract_page_text(1)
        hits = session.search("dijkstra")
        assert len(hits) == 1
        assert hits[0].page_index == 1
        assert len(session.outline) == 2
        assert session.render_page_png(0) is None


def test_f1_pdf_session_dark_lut_and_disk_cache_roundtrip(tmp_path: Path) -> None:
    """F1: PdfDocumentSession reports page dimensions and handles rendering/cache cleanly."""
    from core.courses.pdf_renderer import PdfDocumentSession

    pdf_bytes = build_pdf_bytes(["First page alpha", "Second page beta"])
    with PdfDocumentSession(pdf_bytes, cache_dir=tmp_path) as session:
        assert session.supported is True
        assert session.page_count == 2
        w, h = session.get_page_size(0)
        assert w > 0 and h > 0
        assert "beta" in session.extract_page_text(1).lower()
        if session.can_render_images:
            light = session.render_page_png(0, scale=1.0, dark_mode=False)
            dark = session.render_page_png(0, scale=1.0, dark_mode=True)
            assert light is not None and light.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
            assert dark is not None and dark.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
            assert light.png_bytes != dark.png_bytes

    if session.can_render_images:
        with PdfDocumentSession(pdf_bytes, cache_dir=tmp_path) as reopened:
            cached = reopened.render_page_png(0, scale=1.0, dark_mode=False)
            assert cached is not None
            assert cached.from_cache is True


# ---------------------------------------------------------------------------
# F2: Unified EmbeddedWebViewHost (>=5 tests)
# ---------------------------------------------------------------------------


def test_f2_host_api_contract_methods_present() -> None:
    """F2: EmbeddedWebViewHost module exposes factory and full lifecycle contract."""
    host_mod = importlib.import_module("gui.components.courses.embedded_webview_host")
    host_cls = host_mod.EmbeddedWebViewHost
    factory = host_mod.build_embedded_webview_host

    assert callable(factory)
    for method_name in (
        "control",
        "eval_js",
        "navigate",
        "suspend",
        "resume",
        "sync_bounds",
        "dispose",
    ):
        assert hasattr(host_cls, method_name)
    assert inspect.iscoroutinefunction(host_cls.eval_js)


@pytest.mark.parametrize("profile", ["android", "ios"])
def test_f2_mobile_dispatch_constructs_flet_webview(
    profile: str,
    fake_page: FakePage,
    platform_profile: Any,
) -> None:
    """F2: Android and iOS build_embedded_webview_host wrap a flet_webview.WebView."""
    import flet_webview as fwv

    host_mod = importlib.import_module("gui.components.courses.embedded_webview_host")
    platform_profile(profile, page=fake_page)

    target_url = "https://courses.ut.edu.vn/mod/quiz/view.php?id=10"
    host = host_mod.build_embedded_webview_host(target_url, page=fake_page)
    assert host is not None
    assert host.backend == "flet_webview"

    ctrl = host.control()
    assert isinstance(ctrl, ft.Control)
    assert_valid_flet_mro(ctrl)
    webviews = [c for c in walk_controls(ctrl) if isinstance(c, fwv.WebView)]
    assert len(webviews) == 1
    assert webviews[0].url == target_url
    host.dispose()


def test_f2_windows_dispatch_uses_webview2_when_runtime_available(
    fake_page: FakePage,
    platform_profile: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: Desktop Windows selects WindowsWebView2Dock backend when Edge WebView2 is available."""
    host_mod = importlib.import_module("gui.components.courses.embedded_webview_host")
    platform_profile("desktop", page=fake_page, is_windows=True)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(host_mod, "_webview2_runtime_available", lambda: True)
    monkeypatch.setattr(host_mod.WindowsWebView2Dock, "start", lambda self: None)

    host = host_mod.build_embedded_webview_host(
        "http://127.0.0.1:9000/viewer/tok/docx_host.html",
        page=fake_page,
    )
    assert host is not None
    assert host.backend == "webview2"
    assert_valid_flet_mro(host.control())
    host.sync_bounds(0, 56, 800, 600)
    host.suspend()
    host.resume()
    host.dispose()


def test_f2_headless_unsupported_and_unsafe_urls_return_none(
    fake_page: FakePage,
    platform_profile: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: Unsupported/web/headless platforms and non-HTTP(S) schemes return None cleanly."""
    host_mod = importlib.import_module("gui.components.courses.embedded_webview_host")

    platform_profile("desktop", page=fake_page, is_windows=False)
    monkeypatch.setattr(host_mod, "_webview2_runtime_available", lambda: False)
    assert host_mod.build_embedded_webview_host("https://courses.ut.edu.vn", page=fake_page) is None

    web_page = FakePage(web=True)
    assert host_mod.build_embedded_webview_host("https://courses.ut.edu.vn", page=web_page) is None

    platform_profile("android", page=fake_page)
    for bad_url in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,<script>"):
        assert host_mod.build_embedded_webview_host(bad_url, page=fake_page) is None


def test_f2_host_lifecycle_eval_js_navigate_suspend_resume_dispose(
    fake_page: FakePage,
    platform_profile: Any,
) -> None:
    """F2: Lifecycle methods eval_js, navigate, suspend, resume, sync_bounds, dispose work cleanly."""
    import flet_webview as fwv

    host_mod = importlib.import_module("gui.components.courses.embedded_webview_host")
    platform_profile("android", page=fake_page)

    host = host_mod.build_embedded_webview_host(
        "https://courses.ut.edu.vn/course/view.php?id=101",
        page=fake_page,
    )
    assert host is not None
    ctrl = host.control()
    assert ctrl.visible is True

    host.suspend()
    assert ctrl.visible is False
    host.resume()
    assert ctrl.visible is True

    host.navigate("https://courses.ut.edu.vn/mod/forum/view.php?id=55")
    webviews = [c for c in walk_controls(ctrl) if isinstance(c, fwv.WebView)]
    assert webviews[0].url == "https://courses.ut.edu.vn/mod/forum/view.php?id=55"

    host.navigate("javascript:alert(1)")
    assert webviews[0].url == "https://courses.ut.edu.vn/mod/forum/view.php?id=55"

    asyncio.run(host.eval_js("window.setTheme && window.setTheme('dark')"))
    host.sync_bounds(0, 56, 360, 640)
    host.dispose()
    asyncio.run(host.eval_js("console.log('after dispose')"))


def test_f2_courses_coordinator_uses_unified_host_instead_of_flet_uth_quiz(
    fake_page: FakePage,
    platform_profile: Any,
    preview_server: LocalPreviewServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: CoursesCoordinator mounts unified EmbeddedWebViewHost via public workflows on Android and Windows."""
    import flet_webview as fwv
    from gui.components.courses.webview_pane import CoursesWebViewPane

    host_mod = importlib.import_module("gui.components.courses.embedded_webview_host")
    importlib.import_module("gui.controllers.courses_coordinator")
    assert "extensions.flet_uth_quiz" not in sys.modules

    quiz_url = "https://courses.ut.edu.vn/mod/quiz/view.php?id=5"

    # Android: open_in_app_webview and open_attachment_bytes_in_overlay mount flet_webview.WebView
    platform_profile("android", page=fake_page)
    host_android = host_mod.build_embedded_webview_host(quiz_url, page=fake_page)
    assert host_android is not None
    assert host_android.backend == "flet_webview"
    host_android.dispose()

    coord_android = _build_coordinator(fake_page, tmp_path / "android", preview_server)
    try:
        coord_android.open_in_app_webview("courses", "Moodle Quiz", quiz_url)
        assert coord_android.courses_view._overlays
        top_wv = coord_android.courses_view._overlays[-1]
        assert isinstance(top_wv, CoursesWebViewPane)
        assert any(isinstance(c, fwv.WebView) for c in walk_controls(top_wv))

        course = make_course(101)
        mod = make_module(202, modname="resource", name="HTML Note")
        att = make_attachment("note.html", preview_kind="text")
        coord_android.open_attachment_bytes_in_overlay(
            course,
            mod,
            att,
            b"<html><body><h1>UTH Note</h1></body></html>",
        )
        top_reader = coord_android.courses_view._overlays[-1]
        assert any(isinstance(c, fwv.WebView) for c in walk_controls(top_reader))
    finally:
        coord_android.close()

    # Windows: build_embedded_webview_host + open_in_app_webview use WebView2 without external launch
    win_page = FakePage()
    platform_profile("desktop", page=win_page, is_windows=True)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(host_mod, "_webview2_runtime_available", lambda: True)
    monkeypatch.setattr(host_mod.WindowsWebView2Dock, "start", lambda self: None)

    host_win = host_mod.build_embedded_webview_host(quiz_url, page=win_page)
    assert host_win is not None
    assert host_win.backend == "webview2"
    host_win.dispose()

    coord_win = _build_coordinator(win_page, tmp_path / "windows", preview_server)
    try:
        coord_win.open_in_app_webview("courses", "Moodle Quiz", quiz_url)
        assert coord_win.courses_view._overlays
        assert isinstance(coord_win.courses_view._overlays[-1], CoursesWebViewPane)
    finally:
        coord_win.close()


# ---------------------------------------------------------------------------
# F3: LocalPreviewServer Viewer App Route (>=5 tests)
# ---------------------------------------------------------------------------


def test_f3_viewer_base_url_returns_authenticated_loopback_prefix(
    preview_server: LocalPreviewServer,
) -> None:
    """F3: viewer_base_url() starts the loopback server and returns /viewer/<token>/."""
    base = preview_server.viewer_base_url()
    parsed = urlsplit(base)
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == preview_server.port
    parts = [seg for seg in parsed.path.split("/") if seg]
    assert len(parts) == 2 and parts[0] == "viewer" and len(parts[1]) >= 16
    assert base.endswith("/")


def test_f3_viewer_serves_docx_and_pptx_hosts_with_viewer_app_csp(tmp_path: Path) -> None:
    """F3: GET /viewer/<token>/docx_host.html and pptx_host.html return 200 with _VIEWER_APP_CSP."""
    (tmp_path / "docx_host.html").write_text(
        "<!doctype html><html><body><script src='docx_host.js'></script></body></html>",
        encoding="utf-8",
    )
    (tmp_path / "pptx_host.html").write_text(
        "<!doctype html><html><body><script src='pptx_host.js'></script></body></html>",
        encoding="utf-8",
    )
    srv = LocalPreviewServer(viewer_assets_dir=tmp_path)
    try:
        base = srv.viewer_base_url()
        for name in ("docx_host.html", "pptx_host.html"):
            status, hdrs, body = http_request(f"{base}{name}")
            assert status == 200
            assert "text/html" in hdrs.get("content-type", "")
            csp = hdrs.get("content-security-policy", "")
            assert "default-src 'self'" in csp
            assert "script-src 'self'" in csp
            assert "object-src 'none'" in csp
            assert b"<!doctype html>" in body
    finally:
        srv.stop()


def test_f3_viewer_mime_types_for_static_bundle_assets(tmp_path: Path) -> None:
    """F3: /viewer/<token>/ serves .js, .css, .svg, .png, and .woff2 with accurate MIME types."""
    (tmp_path / "viewer.js").write_text("console.log('ok');", encoding="utf-8")
    (tmp_path / "viewer.css").write_text("body { margin: 0; }", encoding="utf-8")
    (tmp_path / "icon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (tmp_path / "thumb.png").write_bytes(tiny_png_bytes())
    (tmp_path / "font.woff2").write_bytes(b"wOF2dummyfontbytes")

    srv = LocalPreviewServer(viewer_assets_dir=tmp_path)
    try:
        base = srv.viewer_base_url()
        expected = {
            "viewer.js": "javascript",
            "viewer.css": "text/css",
            "icon.svg": "image/svg+xml",
            "thumb.png": "image/png",
            "font.woff2": "font/woff2",
        }
        for filename, mime_needle in expected.items():
            status, hdrs, _body = http_request(f"{base}{filename}")
            assert status == 200
            assert mime_needle in hdrs.get("content-type", "")
            assert hdrs.get("x-content-type-options") == "nosniff"
            if filename == "icon.svg":
                assert "default-src 'none'" in hdrs.get("content-security-policy", "")
    finally:
        srv.stop()


def test_f3_viewer_rejects_bad_token_and_path_traversal(tmp_path: Path) -> None:
    """F3: /viewer/ rejects wrong tokens, parent-dir traversal, and non-whitelisted extensions."""
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "docx_host.html").write_text("<html>ok</html>", encoding="utf-8")
    (assets_dir / "secret.py").write_text("SECRET = 1", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("TOP_SECRET", encoding="utf-8")

    srv = LocalPreviewServer(viewer_assets_dir=assets_dir)
    try:
        base = srv.viewer_base_url()
        port = srv.port

        status_bad_tok, _, _ = http_request(
            f"http://127.0.0.1:{port}/viewer/wrong-token-value/docx_host.html"
        )
        assert status_bad_tok == 403

        for traversal in ("../outside.txt", "%2e%2e/outside.txt", "sub/../../outside.txt"):
            st, _, body = http_request(f"{base}{traversal}")
            assert st in (403, 404)
            assert b"TOP_SECRET" not in body

        st_py, _, _ = http_request(f"{base}secret.py")
        assert st_py in (403, 404)
    finally:
        srv.stop()


def test_f3_raw_html_and_svg_items_retain_strict_html_csp(
    preview_server: LocalPreviewServer,
) -> None:
    """F3: Raw /<16-hex-id>?token=... HTML & SVG payloads continue enforcing default-src 'none'."""
    html_url = preview_server.register_html("<html><body><p>Raw preview</p></body></html>")
    st_html, hdrs_html, _ = http_request(html_url)
    assert st_html == 200
    assert "default-src 'none'" in hdrs_html.get("content-security-policy", "")

    svg_url = preview_server.register_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")
    st_svg, hdrs_svg, _ = http_request(svg_url)
    assert st_svg == 200
    assert "default-src 'none'" in hdrs_svg.get("content-security-policy", "")


def test_f3_raw_office_blobs_and_range_requests_served_cleanly(
    preview_server: LocalPreviewServer,
) -> None:
    """F3: Raw .docx/.pptx bytes served over loopback support full GET and Range 206 fetch."""
    docx_bytes = build_docx_bytes(paragraphs=["Loopback fetch check"])
    pptx_bytes = build_pptx_bytes(slides=[("Slide 1", "Body")])

    docx_url = preview_server.register_bytes(
        docx_bytes,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    pptx_url = preview_server.register_bytes(
        pptx_bytes,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )

    st_d, hdrs_d, body_d = http_request(docx_url)
    assert st_d == 200
    assert body_d == docx_bytes
    assert hdrs_d.get("accept-ranges") == "bytes"

    st_r, hdrs_r, chunk_r = http_request(pptx_url, headers={"Range": "bytes=0-31"})
    assert st_r == 206
    assert chunk_r == pptx_bytes[:32]
    assert hdrs_r.get("content-range") == f"bytes 0-31/{len(pptx_bytes)}"


# ---------------------------------------------------------------------------
# F4: CoursesWebViewPane Mobile Navigation & Header (>=5 tests)
# ---------------------------------------------------------------------------


def test_f4_mobile_360dp_renders_compact_header_and_overflow_menu(
    fake_page: FakePage,
    platform_profile: Any,
    preview_server: LocalPreviewServer,
) -> None:
    """F4: On <600dp phone width, CoursesWebViewPane renders compact header + overflow menu with zero overflow."""
    from gui.components.courses.webview_pane import CoursesWebViewPane

    fake_page.resize(360, 780)
    platform_profile("android", page=fake_page)

    pane = CoursesWebViewPane(
        page=fake_page,
        source="courses",
        title="Hoạt động trắc nghiệm trực tuyến có tiêu đề rất dài trên điện thoại",
        url="https://courses.ut.edu.vn/mod/quiz/view.php?id=42",
        preview_server=preview_server,
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        assert_valid_flet_mro(pane)
        assert_no_fixed_width_overflow(pane, 360.0)
        assert_min_touch_target(pane._back_btn, 44.0)

        overflow_menus = [
            c for c in walk_controls(pane._header_container) if isinstance(c, ft.PopupMenuButton)
        ]
        assert overflow_menus, "Expected PopupMenuButton overflow menu in compact mobile WebView header"
    finally:
        pane.dispose()


def test_f4_flet_webview_back_forward_reload_wired_on_android(
    fake_page: FakePage,
    platform_profile: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F4: Back, forward, and reload controls invoke flet_webview.WebView navigation methods on Android."""
    import flet_webview as fwv
    from gui.components.courses.webview_pane import CoursesWebViewPane

    calls: list[str] = []

    async def _go_back(self: Any) -> None:
        calls.append("back")

    async def _go_forward(self: Any) -> None:
        calls.append("forward")

    async def _reload(self: Any) -> None:
        calls.append("reload")

    monkeypatch.setattr(fwv.WebView, "go_back", _go_back)
    monkeypatch.setattr(fwv.WebView, "go_forward", _go_forward)
    monkeypatch.setattr(fwv.WebView, "reload", _reload)

    fake_page.resize(390, 844)
    platform_profile("android", page=fake_page)

    pane = CoursesWebViewPane(
        page=fake_page,
        source="courses",
        title="Moodle Quiz",
        url="https://courses.ut.edu.vn/mod/quiz/view.php?id=77",
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        pane._on_webview_back(None)
        pane._on_webview_forward(None)
        pane._on_webview_reload(None)
        assert "back" in calls
        assert "forward" in calls
        assert "reload" in calls
    finally:
        pane.dispose()


def test_f4_external_open_hidden_for_loopback_preview_urls(
    fake_page: FakePage,
    platform_profile: Any,
    preview_server: LocalPreviewServer,
) -> None:
    """F4: External browser button/action is hidden when displaying a 127.0.0.1 loopback preview URL."""
    from gui.components.courses.webview_pane import CoursesWebViewPane

    loopback_url = preview_server.register_html("<html><body><p>Offline Preview</p></body></html>")

    for profile, width in (("desktop", 1024), ("android", 360)):
        fake_page.resize(width, 780)
        platform_profile(profile, page=fake_page, is_windows=False)
        pane = CoursesWebViewPane(
            page=fake_page,
            source="courses",
            title="Local Preview",
            url=loopback_url,
            preview_server=preview_server,
            on_back=lambda: None,
            on_open_external=lambda _u: None,
        )
        try:
            assert pane._ext_btn.visible is False
            menu_texts = [
                getattr(item, "content", "")
                for btn in walk_controls(pane._header_container)
                if isinstance(btn, ft.PopupMenuButton)
                for item in (btn.items or [])
            ]
            assert not any("trình duyệt ngoài" in str(t).lower() for t in menu_texts)
        finally:
            pane.dispose()


def test_f4_external_open_shown_for_external_https_urls(
    fake_page: FakePage,
    platform_profile: Any,
) -> None:
    """F4: External-open action is visible and dispatches target URL for external https:// URLs."""
    from gui.components.courses.webview_pane import CoursesWebViewPane

    opened: list[str] = []
    target_url = "https://courses.ut.edu.vn/mod/quiz/view.php?id=99"
    fake_page.resize(1120, 760)
    platform_profile("desktop", page=fake_page, is_windows=False)

    pane = CoursesWebViewPane(
        page=fake_page,
        source="courses",
        title="Quiz Online",
        url=target_url,
        on_back=lambda: None,
        on_open_external=opened.append,
    )
    try:
        assert pane._ext_btn.visible is True
        pane._ext_btn.on_click(None)
        assert opened == [target_url]
    finally:
        pane.dispose()


def test_f4_desktop_header_shows_inline_navigation_buttons(
    fake_page: FakePage,
    platform_profile: Any,
) -> None:
    """F4: Desktop >=600dp header preserves inline back/forward/reload/external icon buttons."""
    from gui.components.courses.webview_pane import CoursesWebViewPane

    fake_page.resize(1120, 760)
    platform_profile("desktop", page=fake_page, is_windows=False)

    pane = CoursesWebViewPane(
        page=fake_page,
        source="courses",
        title="Course Page",
        url="https://courses.ut.edu.vn/course/view.php?id=101",
        on_back=lambda: None,
        on_open_external=lambda _u: None,
    )
    try:
        assert_valid_flet_mro(pane)
        assert_no_fixed_width_overflow(pane, 1120.0)
        texts = collect_texts(pane._header_container)
        assert "Trang trước" in texts
        assert "Trang kế" in texts
        assert "Tải lại trang" in texts
        assert "Mở bằng trình duyệt ngoài" in texts
    finally:
        pane.dispose()


# ---------------------------------------------------------------------------
# F5: Cross-Platform External File Open / Save (>=5 tests)
# ---------------------------------------------------------------------------


def _build_coordinator(
    page: FakePage,
    tmp_path: Path,
    preview_server: LocalPreviewServer,
    catalog: FakeCatalogService | None = None,
) -> Any:
    from gui.controllers.courses_coordinator import CoursesCoordinator

    return CoursesCoordinator(
        page=page,  # type: ignore[arg-type]
        catalog_service=catalog or FakeCatalogService(),  # type: ignore[arg-type]
        preview_cache=PreviewFileCache(tmp_path / "cache"),
        archive_service=ArchiveService(),
        preview_server=preview_server,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )


def test_f5_android_open_external_never_launches_file_uri(
    fake_page: FakePage,
    platform_profile: Any,
    preview_server: LocalPreviewServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F5: On Android, _open_external_async avoids file:// URI launch (FileUriExposedException) and uses safe save."""
    platform_profile("android", page=fake_page)
    launched: list[str] = []
    saved_calls: list[tuple[str | None, bytes | None]] = []

    async def _fake_launch(self: Any, url: str, **_kwargs: Any) -> None:
        launched.append(str(url))

    async def _fake_save(self: Any, **kwargs: Any) -> str:
        saved_calls.append((kwargs.get("file_name"), kwargs.get("src_bytes")))
        return "/storage/emulated/0/Download/bai_giang.pdf"

    monkeypatch.setattr(ft.UrlLauncher, "launch_url", _fake_launch)
    monkeypatch.setattr(ft.FilePicker, "save_file", _fake_save)

    coord = _build_coordinator(fake_page, tmp_path, preview_server)
    bind_control_page(monkeypatch, type(coord.courses_view), fake_page)
    try:
        asyncio.run(coord._open_external_async("bai_giang.pdf", b"%PDF-1.4 sample"))
        assert not any(u.startswith("file:") for u in launched)
        assert saved_calls == [("bai_giang.pdf", b"%PDF-1.4 sample")]
    finally:
        coord.close()


def test_f5_ios_open_external_never_launches_file_uri(
    fake_page: FakePage,
    platform_profile: Any,
    preview_server: LocalPreviewServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F5: On iOS, _open_external_async routes safely via FilePicker.save_file instead of file:// URI."""
    platform_profile("ios", page=fake_page)
    launched: list[str] = []
    saved_calls: list[tuple[str | None, bytes | None]] = []

    async def _fake_launch(self: Any, url: str, **_kwargs: Any) -> None:
        launched.append(str(url))

    async def _fake_save(self: Any, **kwargs: Any) -> str:
        saved_calls.append((kwargs.get("file_name"), kwargs.get("src_bytes")))
        return "/var/mobile/Documents/slides.pptx"

    monkeypatch.setattr(ft.UrlLauncher, "launch_url", _fake_launch)
    monkeypatch.setattr(ft.FilePicker, "save_file", _fake_save)

    coord = _build_coordinator(fake_page, tmp_path, preview_server)
    try:
        payload = build_pptx_bytes()
        asyncio.run(coord._open_external_async("slides.pptx", payload))
        assert launched == []
        assert saved_calls == [("slides.pptx", payload)]
    finally:
        coord.close()


def test_f5_windows_open_external_writes_sanitized_temp_file_and_launches(
    fake_page: FakePage,
    platform_profile: Any,
    preview_server: LocalPreviewServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F5: Desktop Windows writes sanitized temp file and launches its file:// URI."""
    platform_profile("desktop", page=fake_page, is_windows=True)
    launched: list[str] = []

    async def _fake_launch(self: Any, url: str, **_kwargs: Any) -> None:
        launched.append(str(url))

    monkeypatch.setattr(ft.UrlLauncher, "launch_url", _fake_launch)

    coord = _build_coordinator(fake_page, tmp_path, preview_server)
    try:
        asyncio.run(coord._open_external_async("../../bài giảng (2026)!.pdf", b"%PDF-1.4 content"))
        assert len(launched) == 1
        assert launched[0].startswith("file:///")
        assert launched[0].endswith(".pdf")
        ext_files = list((tmp_path / "cache" / "external").glob("*.pdf"))
        assert len(ext_files) == 1
        assert ext_files[0].read_bytes() == b"%PDF-1.4 content"
        assert "!" not in ext_files[0].name
        assert ".." not in ext_files[0].name
    finally:
        coord.close()


@pytest.mark.parametrize(
    "bad_name",
    ["setup.exe", "run.bat", "script.ps1", "installer.msi", "bypass.exe. "],
)
def test_f5_dangerous_extensions_blocked_to_save_only_fallback(
    bad_name: str,
    fake_page: FakePage,
    platform_profile: Any,
    preview_server: LocalPreviewServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F5: Executable/script extensions never auto-launch and fall back to save-only FilePicker."""
    platform_profile("desktop", page=fake_page, is_windows=True)
    launched: list[str] = []
    saved_calls: list[str | None] = []

    async def _fake_launch(self: Any, url: str, **_kwargs: Any) -> None:
        launched.append(str(url))

    async def _fake_save(self: Any, **kwargs: Any) -> str:
        saved_calls.append(kwargs.get("file_name"))
        return "saved"

    monkeypatch.setattr(ft.UrlLauncher, "launch_url", _fake_launch)
    monkeypatch.setattr(ft.FilePicker, "save_file", _fake_save)

    coord = _build_coordinator(fake_page, tmp_path, preview_server)
    try:
        asyncio.run(coord._open_external_async(bad_name, b"MZ-test"))
        assert launched == []
        assert saved_calls == [bad_name.strip()]
    finally:
        coord.close()


def test_f5_save_attachment_async_saves_via_file_picker_with_feedback(
    fake_page: FakePage,
    platform_profile: Any,
    preview_server: LocalPreviewServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F5: save_attachment_async fetches bytes, invokes FilePicker.save_file with src_bytes, and notifies user."""
    platform_profile("android", page=fake_page)
    docx_bytes = build_docx_bytes(paragraphs=["Tài liệu thực hành"])
    att = make_attachment(
        "lab1.docx",
        "https://courses.ut.edu.vn/pluginfile.php/1/lab1.docx",
        file_size=len(docx_bytes),
    )
    catalog = FakeCatalogService(attachment_bytes_map={att.file_url: docx_bytes})
    saved_calls: list[tuple[str | None, bytes | None]] = []

    async def _fake_save(self: Any, **kwargs: Any) -> str:
        saved_calls.append((kwargs.get("file_name"), kwargs.get("src_bytes")))
        return "/storage/emulated/0/Download/lab1.docx"

    monkeypatch.setattr(ft.FilePicker, "save_file", _fake_save)

    coord = _build_coordinator(fake_page, tmp_path, preview_server, catalog=catalog)
    try:
        asyncio.run(coord.save_attachment_async(att, "courses"))
        assert saved_calls == [("lab1.docx", docx_bytes)]
        snack_texts = [
            txt
            for dlg in (*fake_page.dialogs, *fake_page.overlay)
            for txt in collect_texts(dlg)
        ]
        assert any("Đã lưu: lab1.docx" in t for t in snack_texts)
    finally:
        coord.close()
