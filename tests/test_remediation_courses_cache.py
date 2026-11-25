"""TDD remediation tests for Slice 4: D-04, DEMO-03, is_uth_lms_url, DEMO-09, D-05."""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft
import pytest

import config
from core.courses.archive_service import ArchiveService
from core.courses.cache_store import CourseCacheStore
from core.courses.catalog_service import CourseCatalogService
from core.courses.document_converter import (
    _DOC_LRU_CACHE,
    ConvertedDocument,
    clear_converted_document_cache,
)
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.models import (
    CourseAttachment,
    CourseModuleItem,
    CourseSummary,
)
from core.courses.preview_cache import PreviewFileCache
from gui.controllers.courses_coordinator import CoursesCoordinator
from gui.core import utils


def _minimal_pdf_bytes(text: str = "UTHelper") -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\n"
        b"trailer << /Root 1 0 R >>\n%%EOF\n"
    ) + f"% {text}\n".encode("ascii")


def _minimal_docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


def _make_fake_page(platform: ft.PagePlatform = ft.PagePlatform.WINDOWS) -> SimpleNamespace:
    return SimpleNamespace(
        overlay=[],
        web=False,
        platform=platform,
        title="UTHelper",
        width=1200,
        height=800,
        update=lambda: None,
        run_task=lambda *_a, **_kw: None,
    )


def _make_course_and_mod(
    file_name: str,
    preview_kind: str,
    file_url: str = "https://courses.ut.edu.vn/pluginfile.php/10/mod_resource/content/1/doc",
) -> tuple[CourseSummary, CourseModuleItem, CourseAttachment]:
    course = CourseSummary(
        id=10,
        source="courses",
        shortname="CS101",
        fullname="CS101",
        display_name="CS101",
    )
    att = CourseAttachment(
        file_name=file_name,
        file_url=file_url,
        time_modified=100,
        file_size=512,
        preview_kind=preview_kind,  # type: ignore[arg-type]
    )
    mod = CourseModuleItem(
        cmid=101,
        instance_id=101,
        modname="resource",
        name=file_name,
        section_id=1,
        url=file_url,
        web_url=file_url,
        attachments=[att],
    )
    return course, mod, att


# ---------------------------------------------------------------------------
# D-04: PreviewFileCache.put_converted_pdf should_commit guard
# ---------------------------------------------------------------------------


def test_d04_put_converted_pdf_aborts_when_should_commit_returns_false(tmp_path: Path):
    pcache = PreviewFileCache(tmp_path / "pcache")
    src = b"pptx-source-bytes"
    pdf = _minimal_pdf_bytes("converted")

    result = pcache.put_converted_pdf(
        src,
        "slides.pptx",
        pdf,
        should_commit=lambda: False,
    )

    assert result is None
    assert pcache.get_cached_converted_pdf(src, "slides.pptx") is None
    assert list((tmp_path / "pcache").iterdir()) == []


def test_d04_put_converted_pdf_cleans_staged_temp_when_should_commit_flips_false(
    tmp_path: Path,
):
    pcache = PreviewFileCache(tmp_path / "pcache")
    src = b"docx-source-bytes"
    pdf = _minimal_pdf_bytes("mid-write-stale")
    decisions = iter([True, False])

    result = pcache.put_converted_pdf(
        src,
        "report.docx",
        pdf,
        should_commit=lambda: next(decisions, False),
    )

    assert result is None
    assert pcache.get_cached_converted_pdf(src, "report.docx") is None
    # Neither final PDF nor staged .tmp.* artifact may survive on disk
    assert list((tmp_path / "pcache").iterdir()) == []


def test_d04_put_converted_pdf_commits_when_should_commit_is_true_or_none(tmp_path: Path):
    pcache = PreviewFileCache(tmp_path / "pcache")
    src = b"deck-bytes"
    pdf = _minimal_pdf_bytes("ok")

    committed = pcache.put_converted_pdf(
        src,
        "deck.pptx",
        pdf,
        should_commit=lambda: True,
    )
    assert committed is not None and committed.is_file()
    assert pcache.get_cached_converted_pdf(src, "deck.pptx") == pdf


# ---------------------------------------------------------------------------
# D-04: CoursesCoordinator.open_attachment_async post-logout race guards
# ---------------------------------------------------------------------------


def test_d04_logout_mid_office_conversion_never_writes_converted_pdf_to_cache(
    tmp_path: Path, monkeypatch
):
    pcache = PreviewFileCache(tmp_path / "pcache")
    store = CourseCacheStore(cache_dir=tmp_path / "store", namespace="acct-a")
    srv = LocalPreviewServer()
    docx_bytes = _minimal_docx_bytes()
    pdf_bytes = _minimal_pdf_bytes("secret-acct-a")

    cat = CourseCatalogService(cache_store=store, preview_cache=pcache)
    cat.fetch_attachment_bytes = lambda *_a, **_kw: docx_bytes  # type: ignore[method-assign]
    coord = CoursesCoordinator(
        page=_make_fake_page(ft.PagePlatform.WINDOWS),  # type: ignore[arg-type]
        catalog_service=cat,
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
        account_namespace="https://courses.ut.edu.vn|acct_a",
    )
    try:
        course, mod, att = _make_course_and_mod("secret.docx", "docx")
        put_spy = Mock(wraps=pcache.put_converted_pdf)
        monkeypatch.setattr(pcache, "put_converted_pdf", put_spy)

        def _render_with_logout(_raw: bytes, _fname: str) -> bytes:
            # Simulate logout / account switch mid-conversion
            coord.reset_for_account_switch(None)
            return pdf_bytes

        monkeypatch.setattr(
            "gui.controllers.courses_coordinator.render_office_pdf",
            _render_with_logout,
        )

        asyncio.run(coord.open_attachment_async(course, mod, att))

        put_spy.assert_not_called()
        assert pcache.get_cached_converted_pdf(docx_bytes, "secret.docx") is None
        assert list(pcache.cache_dir.iterdir()) == []
    finally:
        coord.close()


def test_d04_logout_mid_download_never_repopulates_preview_cache(
    tmp_path: Path,
):
    pcache = PreviewFileCache(tmp_path / "pcache")
    store = CourseCacheStore(cache_dir=tmp_path / "store", namespace="acct-a")
    srv = LocalPreviewServer()
    pdf_bytes = _minimal_pdf_bytes("secret-download")

    cat = CourseCatalogService(cache_store=store, preview_cache=pcache)
    coord = CoursesCoordinator(
        page=_make_fake_page(ft.PagePlatform.WINDOWS),  # type: ignore[arg-type]
        catalog_service=cat,
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )

    def _download_with_logout(*_a, **_kw) -> bytes:
        coord.reset_for_account_switch(None)
        return pdf_bytes

    cat.fetch_attachment_bytes = _download_with_logout  # type: ignore[method-assign]
    try:
        course, mod, att = _make_course_and_mod("private.pdf", "pdf")
        asyncio.run(coord.open_attachment_async(course, mod, att))

        assert pcache.get_cached_path(att.file_url, att.time_modified, att.file_name) is None
        assert list(pcache.cache_dir.iterdir()) == []
    finally:
        coord.close()


def test_d04_logout_before_office_viewer_registration_never_registers_preview_server_blob(
    tmp_path: Path, monkeypatch
):
    pcache = PreviewFileCache(tmp_path / "pcache")
    store = CourseCacheStore(cache_dir=tmp_path / "store", namespace="acct-a")
    srv = LocalPreviewServer()
    docx_bytes = _minimal_docx_bytes()

    cat = CourseCatalogService(cache_store=store, preview_cache=pcache)
    cat.fetch_attachment_bytes = lambda *_a, **_kw: docx_bytes  # type: ignore[method-assign]
    coord = CoursesCoordinator(
        page=_make_fake_page(ft.PagePlatform.WINDOWS),  # type: ignore[arg-type]
        catalog_service=cat,
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        course, mod, att = _make_course_and_mod("handout.docx", "docx")
        monkeypatch.setattr(
            "gui.controllers.courses_coordinator.is_embedded_webview_supported",
            lambda _p: True,
        )

        def _render_returns_none_after_logout(_raw: bytes, _fname: str):
            coord.reset_for_account_switch(None)
            return None

        prepare_mock = Mock()
        prepare_file_mock = Mock()
        monkeypatch.setattr(
            "gui.controllers.courses_coordinator.render_office_pdf",
            _render_returns_none_after_logout,
        )
        monkeypatch.setattr(
            "gui.controllers.courses_coordinator.prepare_office_viewer",
            prepare_mock,
        )
        monkeypatch.setattr(
            "gui.controllers.courses_coordinator.prepare_office_viewer_from_file",
            prepare_file_mock,
        )

        asyncio.run(coord.open_attachment_async(course, mod, att))

        prepare_mock.assert_not_called()
        prepare_file_mock.assert_not_called()
        assert len(srv._items) == 0
    finally:
        coord.close()


def test_d04_logout_before_mobile_pdf_viewer_never_registers_blob(
    tmp_path: Path, monkeypatch
):
    pcache = PreviewFileCache(tmp_path / "pcache")
    store = CourseCacheStore(cache_dir=tmp_path / "store", namespace="acct-a")
    srv = LocalPreviewServer()
    pdf_bytes = _minimal_pdf_bytes("mobile-secret")

    cat = CourseCatalogService(cache_store=store, preview_cache=pcache)
    coord = CoursesCoordinator(
        page=_make_fake_page(ft.PagePlatform.ANDROID),  # type: ignore[arg-type]
        catalog_service=cat,
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        course, mod, att = _make_course_and_mod("syllabus.pdf", "pdf")

        async def _fetch_then_logout(_src, _att, **_kw):
            return pdf_bytes, None

        monkeypatch.setattr(coord, "_fetch_attachment_payload_async", _fetch_then_logout)
        monkeypatch.setattr(
            "gui.controllers.courses_coordinator.is_embedded_webview_supported",
            lambda _p: (coord.reset_for_account_switch(None), True)[1],
        )
        prepare_pdf_mock = Mock()
        monkeypatch.setattr(
            "gui.controllers.courses_coordinator.prepare_pdf_viewer",
            prepare_pdf_mock,
        )

        asyncio.run(coord.open_attachment_async(course, mod, att))

        prepare_pdf_mock.assert_not_called()
        assert len(srv._items) == 0
    finally:
        coord.close()


def test_d04_logout_before_and_during_warm_pdf_never_leaves_rendered_pngs(
    tmp_path: Path, monkeypatch
):
    pcache = PreviewFileCache(tmp_path / "pcache")
    store = CourseCacheStore(cache_dir=tmp_path / "store", namespace="acct-a")
    srv = LocalPreviewServer()
    pdf_bytes = _minimal_pdf_bytes("linux-secret")

    cat = CourseCatalogService(cache_store=store, preview_cache=pcache)
    coord = CoursesCoordinator(
        page=_make_fake_page(ft.PagePlatform.LINUX),  # type: ignore[arg-type]
        catalog_service=cat,
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        course, mod, att = _make_course_and_mod("lecture.pdf", "pdf")
        rendered_dir = pcache.cache_dir / "rendered_pdf"
        doc_hash = hashlib.sha256(pdf_bytes).hexdigest()[:24]

        class _FakePdfSession:
            supported = True
            can_render_images = True
            page_count = 1

            def __init__(self, data: bytes, *, cache_dir: Path | None = None) -> None:
                self.doc_hash = doc_hash
                self._cache_dir = cache_dir
                self.closed = False

            def render_page_png(self, page_index: int, **_kw):
                # Logout occurs while page 0 is rendering!
                coord.reset_for_account_switch(None)
                if self._cache_dir is not None:
                    rdir = self._cache_dir / "rendered_pdf"
                    rdir.mkdir(parents=True, exist_ok=True)
                    (rdir / f"{self.doc_hash}_p{page_index}_160_light.png").write_bytes(
                        b"\x89PNG\r\n\x1a\n"
                    )
                return SimpleNamespace(page_index=page_index)

            def close(self) -> None:
                self.closed = True

        async def _fake_fetch(_src, _att, **_kw):
            return pdf_bytes, None

        monkeypatch.setattr(coord, "_fetch_attachment_payload_async", _fake_fetch)
        monkeypatch.setattr(
            "gui.controllers.courses_coordinator.is_embedded_webview_supported",
            lambda _p: False,
        )
        monkeypatch.setattr(
            "gui.controllers.courses_coordinator.PdfDocumentSession",
            _FakePdfSession,
        )

        asyncio.run(coord.open_attachment_async(course, mod, att))

        remaining = list(rendered_dir.glob("*.png")) if rendered_dir.exists() else []
        assert remaining == []
    finally:
        coord.close()


def test_d04_logout_during_convert_document_bytes_clears_ram_cache(
    tmp_path: Path, monkeypatch
):
    clear_converted_document_cache()
    pcache = PreviewFileCache(tmp_path / "pcache")
    store = CourseCacheStore(cache_dir=tmp_path / "store", namespace="acct-a")
    srv = LocalPreviewServer()
    csv_bytes = b"col1,col2\nsecret,42\n"

    cat = CourseCatalogService(cache_store=store, preview_cache=pcache)
    coord = CoursesCoordinator(
        page=_make_fake_page(ft.PagePlatform.WINDOWS),  # type: ignore[arg-type]
        catalog_service=cat,
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        course, mod, att = _make_course_and_mod("grades.csv", "csv")

        async def _fake_fetch(_src, _att, **_kw):
            return csv_bytes, None

        def _convert_with_mid_logout(fname, data, kind, **_kw):
            coord.reset_for_account_switch(None)
            doc = ConvertedDocument(
                kind=kind,
                title=fname,
                markdown_text="secret",
            )
            _DOC_LRU_CACHE[("stale-key", fname, kind, False)] = (len(data), doc)
            return doc

        monkeypatch.setattr(coord, "_fetch_attachment_payload_async", _fake_fetch)
        monkeypatch.setattr(
            "gui.controllers.courses_coordinator.convert_document_bytes",
            _convert_with_mid_logout,
        )

        asyncio.run(coord.open_attachment_async(course, mod, att))

        assert len(_DOC_LRU_CACHE) == 0
    finally:
        clear_converted_document_cache()
        coord.close()


# ---------------------------------------------------------------------------
# D-04: clear_cached_description_images & reset_for_account_switch purge
# ---------------------------------------------------------------------------


def test_d04_clear_cached_description_images_purges_safely_and_handles_errors(
    tmp_path: Path, monkeypatch
):
    user_dir = tmp_path / "userdata"
    assets_dir = tmp_path / "assets"
    monkeypatch.setattr(config, "_USER_DATA_DIR", user_dir)
    monkeypatch.setattr(utils, "get_active_assets_dir", lambda: assets_dir)

    # Missing directory returns 0 without creating it
    assert utils.clear_cached_description_images() == 0
    assert not (user_dir / "cache" / "images").exists()

    img_dir = user_dir / "cache" / "images"
    img_dir.mkdir(parents=True)
    mirrored_dir = assets_dir / "cache" / "images"
    mirrored_dir.mkdir(parents=True)

    outside_file = user_dir / "cache" / "keep_me.txt"
    outside_file.write_text("do not delete", encoding="utf-8")
    asset_keep = assets_dir / "icon.png"
    asset_keep.write_bytes(b"icon")
    nested_dir = img_dir / "nested"
    nested_dir.mkdir()
    nested_file = nested_dir / "keep_nested.txt"
    nested_file.write_text("subdir untouched", encoding="utf-8")

    f_locked = img_dir / "locked.png"
    f_ok = img_dir / "ok.png"
    f_mirrored = mirrored_dir / "ok.png"
    f_locked.write_bytes(b"locked")
    f_ok.write_bytes(b"ok")
    f_mirrored.write_bytes(b"ok")

    orig_unlink = Path.unlink

    def _flaky_unlink(self: Path, missing_ok: bool = False):
        if self.name == "locked.png":
            raise PermissionError("file in use")
        return orig_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _flaky_unlink)

    removed = utils.clear_cached_description_images()

    assert removed == 2
    assert not f_ok.exists()
    assert not f_mirrored.exists()
    # Locked file error swallowed gracefully without aborting other deletions
    assert f_locked.exists()
    # Nothing outside cache/images (or nested subdirs) is deleted
    assert outside_file.exists()
    assert asset_keep.exists()
    assert nested_file.exists()


def test_d04_reset_for_account_switch_purges_description_image_cache(
    tmp_path: Path, monkeypatch
):
    user_dir = tmp_path / "userdata"
    assets_dir = tmp_path / "assets"
    monkeypatch.setattr(config, "_USER_DATA_DIR", user_dir)
    monkeypatch.setattr(utils, "get_active_assets_dir", lambda: assets_dir)

    img_dir = user_dir / "cache" / "images"
    img_dir.mkdir(parents=True)
    cached_img = img_dir / ("a" * 64 + ".png")
    cached_img.write_bytes(b"\x89PNG\r\n\x1a\n")

    pcache = PreviewFileCache(tmp_path / "pcache")
    store = CourseCacheStore(cache_dir=tmp_path / "store", namespace="acct-a")
    srv = LocalPreviewServer()
    coord = CoursesCoordinator(
        page=_make_fake_page(),  # type: ignore[arg-type]
        catalog_service=CourseCatalogService(cache_store=store, preview_cache=pcache),
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        coord.reset_for_account_switch(None)
        assert not cached_img.exists()
    finally:
        coord.close()


# ---------------------------------------------------------------------------
# DEMO-03: pre_cache_description_images Demo Mode short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("demo_user", ["uthelper_demo", "  UTHelper_Demo  ", "UTHELPER_DEMO"])
def test_demo03_pre_cache_description_images_returns_early_without_network_or_disk_io(
    tmp_path: Path, monkeypatch, demo_user: str
):
    user_dir = tmp_path / "demo_user_data"
    monkeypatch.setattr(config, "_USER_DATA_DIR", user_dir)
    monkeypatch.setattr(config.settings, "UTH_USERNAME", demo_user)
    monkeypatch.setattr(config.settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(config.settings, "MOODLE_WS_TOKEN", "should-never-be-used")

    def _forbid_network(*_a, **_kw):
        raise AssertionError("Demo Mode must NEVER trigger live HTTP requests")

    monkeypatch.setattr(utils.urllib.request, "urlopen", _forbid_network)
    monkeypatch.setattr(utils.urllib.request, "build_opener", _forbid_network)

    html = (
        '<p>Demo intro</p>'
        '<img src="https://courses.ut.edu.vn/pluginfile.php/42/mod_assign/intro/demo.png" alt="img">'
    )

    result = utils.pre_cache_description_images(html)

    assert result == html
    assert not (user_dir / "cache" / "images").exists()


# ---------------------------------------------------------------------------
# is_uth_lms_url edge-case hardening
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://courses.ut.edu.vn/course/view.php?id=10",
        "https://thnn.ut.edu.vn/mod/quiz/view.php?id=5",
        "https://portal.ut.edu.vn/home",
        "https://ut.edu.vn/",
        "  https://COURSES.UT.EDU.VN/my/  ",
        "https://sub.courses.ut.edu.vn/login",
        "https://courses.ut.edu.vn./course/view.php?id=1",
        "https://courses.ut.edu.vn:443/course/view.php?id=1",
        "https://courses.ut.edu.vn:8443/course/view.php?id=1",
        "http://courses.ut.edu.vn/course/view.php?id=1",
        "http://thnn.ut.edu.vn:80/my/",
        "https://courses.ut.edu.vn/pluginfile.php/123/mod_assign/intro/Bai tap 1.pdf",
        "https://courses.ut.edu.vn/course/view.php?name=Lap trinh",
    ],
)
def test_is_uth_lms_url_accepts_valid_uth_http_and_https_hosts(url: str):
    assert utils.is_uth_lms_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "   ",
        123,
        ["https://courses.ut.edu.vn/"],
        "https://courses.ut.edu.vn@evil.com/login",
        "https://user:pass@courses.ut.edu.vn/login",
        "https://evil.com@courses.ut.edu.vn/login",
        "https://evil-ut.edu.vn/login",
        "https://notut.edu.vn/login",
        "https://courses.ut.edu.vn.evil.com/login",
        "https://ut.edu.vn.attacker.com/login",
        "https://evil.com/courses.ut.edu.vn",
        "https://evil.com/?next=https://courses.ut.edu.vn",
        "mailto:test@ut.edu.vn",
        "javascript:alert(1)",
        "javascript://courses.ut.edu.vn/%0aalert(1)",
        "data:text/html,<script>alert(1)</script>",
        "data://courses.ut.edu.vn/x",
        "file://courses.ut.edu.vn/share",
        "ftp://courses.ut.edu.vn/file.pdf",
        "//courses.ut.edu.vn/path",
        r"https://courses.ut.edu.vn\@evil.com/x",
        r"https://evil.com\@courses.ut.edu.vn/x",
        "https://courses.ut.edu.vn/path\nHeader: injected",
        "https://courses.ut.edu.vn/path\r\nEvil: 1",
        "https://courses .ut.edu.vn/path",
        "https://courses.ut.edu.vn /path",
        "https://courses.ut.edu.vn:notaport/x",
        "https://courses.ut.edu.vn:99999/x",
        "https://.ut.edu.vn/",
        "https://courses..ut.edu.vn/",
        "https://courses.ut.edu.vn../",
        "http://127.0.0.1:54123/pdf-viewer/token123/",
        "https://docs.python.org/3/",
        "https://play.google.com/store/apps/details?id=io.github.chouwzi.uthelper",
    ],
)
def test_is_uth_lms_url_rejects_spoofed_non_http_and_malformed_urls(url: object):
    assert utils.is_uth_lms_url(url) is False


# ---------------------------------------------------------------------------
# DEMO-09: webview2_account_profile_name case/whitespace normalization
# ---------------------------------------------------------------------------


def test_demo09_webview2_account_profile_name_normalizes_case_and_whitespace(
    monkeypatch,
):
    from gui.components.courses.embedded_webview_host import (
        webview2_account_profile_name,
    )

    # Lowercase hash must stay byte-for-byte identical to existing persistent profiles
    expected_legacy_hash = hashlib.sha256(
        b"uth-webview2-profile-v1\0https://courses.ut.edu.vn|studenta"
    ).hexdigest()[:24]
    canonical = webview2_account_profile_name("https://courses.ut.edu.vn|studenta")
    assert canonical == f"acct_{expected_legacy_hash}"

    # Case & whitespace variations map to the exact same profile
    assert (
        webview2_account_profile_name("https://courses.ut.edu.vn|StudentA")
        == canonical
    )
    assert (
        webview2_account_profile_name("https://courses.ut.edu.vn|  StudentA  ")
        == canonical
    )
    assert (
        webview2_account_profile_name("  HTTPS://COURSES.UT.EDU.VN | StudentA  ")
        == canonical
    )
    assert webview2_account_profile_name("StudentA") == webview2_account_profile_name(
        "  studenta  "
    )
    assert webview2_account_profile_name(
        "https://courses.ut.edu.vn|UTHelper_Demo"
    ) == webview2_account_profile_name("https://courses.ut.edu.vn|uthelper_demo")

    # Fallback from global settings also normalizes case/whitespace identically
    monkeypatch.setattr(
        config.settings, "MOODLE_BASE_URL", "  HTTPS://COURSES.UT.EDU.VN  "
    )
    monkeypatch.setattr(config.settings, "UTH_USERNAME", "  StudentA  ")
    assert webview2_account_profile_name(None) == canonical
    assert webview2_account_profile_name("") == canonical

    # Distinct accounts remain strictly isolated
    assert (
        webview2_account_profile_name("https://courses.ut.edu.vn|studentb")
        != canonical
    )


# ---------------------------------------------------------------------------
# D-05: Stacked modal dialog WebView2 occlusion tracking
# ---------------------------------------------------------------------------


def test_d05_stacked_modal_dialogs_keep_dock_occluded_until_outer_dialog_closes():
    from gui.components.courses.embedded_webview_host import (
        _ACTIVE_DOCKS,
        _OCCLUSION_LOCK,
        WindowsWebView2Dock,
        clear_global_webview_occlusion,
        is_globally_occluded,
        set_global_webview_occlusion,
    )

    clear_global_webview_occlusion()
    dock = WindowsWebView2Dock(page_title="UTHelper", url="https://courses.ut.edu.vn/")
    notifications: list[bool] = []
    dock._notify_occlusion_changed = lambda: notifications.append(  # type: ignore[method-assign]
        is_globally_occluded()
    )
    try:
        with _OCCLUSION_LOCK:
            _ACTIVE_DOCKS.add(dock)

        # Open outer modal dialog -> occludes dock
        set_global_webview_occlusion("modal_dialog", True)
        assert is_globally_occluded() is True
        assert dock._effective_visible() is False
        assert notifications == [True]

        # Open inner modal dialog on top of outer modal dialog
        set_global_webview_occlusion("modal_dialog", True)
        assert is_globally_occluded() is True
        assert dock._effective_visible() is False
        assert notifications == [True]

        # Close inner modal dialog -> outer dialog is STILL open, dock MUST stay occluded!
        set_global_webview_occlusion("modal_dialog", False)
        assert is_globally_occluded() is True
        assert dock._effective_visible() is False
        assert notifications == [True]

        # Close outer modal dialog -> NOW dock un-occludes!
        set_global_webview_occlusion("modal_dialog", False)
        assert is_globally_occluded() is False
        assert dock._effective_visible() is True
        assert notifications == [True, False]
    finally:
        clear_global_webview_occlusion()
        dock.dispose()


def test_d05_unmatched_false_occlusion_calls_remain_idempotent_and_non_negative():
    from gui.components.courses.embedded_webview_host import (
        clear_global_webview_occlusion,
        is_globally_occluded,
        set_global_webview_occlusion,
    )

    try:
        clear_global_webview_occlusion()
        # Repeated unmatched False calls never drive refcount negative
        for _ in range(5):
            set_global_webview_occlusion("modal_dialog", False)
            set_global_webview_occlusion("webview_menu", False)
        assert is_globally_occluded() is False

        # Subsequent single True must occlude immediately, and single False must clear
        set_global_webview_occlusion("modal_dialog", True)
        assert is_globally_occluded() is True
        set_global_webview_occlusion("modal_dialog", False)
        assert is_globally_occluded() is False

        # Extra False after clearing is still a no-op
        set_global_webview_occlusion("modal_dialog", False)
        assert is_globally_occluded() is False

        # Mixed stacked reasons + clear_global_webview_occlusion resets everything
        set_global_webview_occlusion("modal_dialog", True)
        set_global_webview_occlusion("modal_dialog", True)
        set_global_webview_occlusion("detail_view", True)
        set_global_webview_occlusion("detail_view", False)
        assert is_globally_occluded() is True
        set_global_webview_occlusion("modal_dialog", False)
        assert is_globally_occluded() is True
        clear_global_webview_occlusion()
        assert is_globally_occluded() is False
    finally:
        clear_global_webview_occlusion()


def test_d04_open_attachment_passes_should_commit_to_put_converted_pdf_under_concurrent_logout(
    tmp_path: Path, monkeypatch
):
    pcache = PreviewFileCache(tmp_path / "pcache")
    store = CourseCacheStore(cache_dir=tmp_path / "store", namespace="acct-a")
    srv = LocalPreviewServer()
    docx_bytes = _minimal_docx_bytes()
    pdf_bytes = _minimal_pdf_bytes("race-inside-put")

    cat = CourseCatalogService(cache_store=store, preview_cache=pcache)
    cat.fetch_attachment_bytes = lambda *_a, **_kw: docx_bytes  # type: ignore[method-assign]
    coord = CoursesCoordinator(
        page=_make_fake_page(ft.PagePlatform.WINDOWS),  # type: ignore[arg-type]
        catalog_service=cat,
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    try:
        course, mod, att = _make_course_and_mod("race.docx", "docx")
        monkeypatch.setattr(
            "gui.controllers.courses_coordinator.render_office_pdf",
            lambda _raw, _fname: pdf_bytes,
        )

        orig_put = pcache.put_converted_pdf
        captured_should_commit: list[object] = []

        def _racing_put(data, fname, pdf, *, should_commit=None):
            captured_should_commit.append(should_commit)
            # Logout occurs AFTER open_attachment_async entered put_converted_pdf
            coord.reset_for_account_switch(None)
            return orig_put(data, fname, pdf, should_commit=should_commit)

        monkeypatch.setattr(pcache, "put_converted_pdf", _racing_put)

        asyncio.run(coord.open_attachment_async(course, mod, att))

        assert len(captured_should_commit) == 1
        assert callable(captured_should_commit[0])
        assert captured_should_commit[0]() is False
        assert pcache.get_cached_converted_pdf(docx_bytes, "race.docx") is None
        assert list(pcache.cache_dir.iterdir()) == []
    finally:
        coord.close()


def test_d04_load_pane_assignment_logout_mid_fetch_skips_image_pre_cache(
    tmp_path: Path, monkeypatch
):
    pcache = PreviewFileCache(tmp_path / "pcache")
    store = CourseCacheStore(cache_dir=tmp_path / "store", namespace="acct-a")
    srv = LocalPreviewServer()
    pre_cache_mock = Mock()
    monkeypatch.setattr(utils, "pre_cache_description_images", pre_cache_mock)

    coord = CoursesCoordinator(
        page=_make_fake_page(),  # type: ignore[arg-type]
        catalog_service=CourseCatalogService(cache_store=store, preview_cache=pcache),
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )

    def _fetch_with_logout(_payload):
        coord.reset_for_account_switch(None)
        return {
            "details": {
                "description_html": '<img src="https://courses.ut.edu.vn/pluginfile.php/1/a.png">'
            }
        }

    coord._assignment_detail_fetcher = _fetch_with_logout
    pane_assign = SimpleNamespace(update_detail=Mock(), show_error_banner=Mock())
    try:
        asyncio.run(coord._load_pane_assignment_async(pane_assign, {"id": "1"}))  # type: ignore[arg-type]
        pre_cache_mock.assert_not_called()
        pane_assign.update_detail.assert_not_called()
    finally:
        coord.close()


def test_d04_clear_cached_description_images_skips_symlinked_cache_root(
    tmp_path: Path, monkeypatch
):
    user_dir = tmp_path / "userdata"
    assets_dir = tmp_path / "assets"
    img_dir = user_dir / "cache" / "images"
    img_dir.mkdir(parents=True)
    victim = img_dir / "victim.png"
    victim.write_bytes(b"precious")

    monkeypatch.setattr(config, "_USER_DATA_DIR", user_dir)
    monkeypatch.setattr(utils, "get_active_assets_dir", lambda: assets_dir)

    orig_is_symlink = Path.is_symlink

    def _fake_symlink(self: Path) -> bool:
        if self == img_dir:
            return True
        return orig_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", _fake_symlink)

    assert utils.clear_cached_description_images() == 0
    assert victim.exists()


def test_is_uth_lms_url_rejects_port_zero_and_supports_configured_custom_https_host(
    monkeypatch,
):
    assert utils.is_uth_lms_url("https://courses.ut.edu.vn:0/course/view.php?id=1") is False
    monkeypatch.setattr(
        config.settings, "MOODLE_BASE_URL", "https://custom-lms.example.edu"
    )
    monkeypatch.setattr(
        config.settings, "PORTAL_API_BASE", "https://custom-portal.example.edu"
    )
    assert utils.is_uth_lms_url("https://custom-lms.example.edu/course/view.php?id=1") is True
    assert utils.is_uth_lms_url("https://custom-lms.example.edu./course/view.php?id=1") is True
    assert utils.is_uth_lms_url("http://custom-lms.example.edu/course/view.php?id=1") is True
    assert utils.is_uth_lms_url("https://custom-portal.example.edu./home") is True
    assert utils.is_uth_lms_url("http://custom-portal.example.edu/home") is True
    assert utils.is_uth_lms_url("https://custom-lms.example.edu@evil.com/") is False


def test_d05_singleton_surface_double_true_does_not_strand_webview_occluded():
    from gui.components.courses.embedded_webview_host import (
        clear_global_webview_occlusion,
        is_globally_occluded,
        set_global_webview_occlusion,
    )

    try:
        clear_global_webview_occlusion()
        # Singleton surfaces (detail_view, quiz_player, webview_menu) may fire True
        # idempotently on rebuild/re-open and False only once on close.
        for reason in ("detail_view", "quiz_player", "webview_menu"):
            set_global_webview_occlusion(reason, True)
            set_global_webview_occlusion(reason, True)
            assert is_globally_occluded() is True
            set_global_webview_occlusion(reason, False)
            assert is_globally_occluded() is False

        # Optional per-instance token also supports idempotent double-False on stacked modals
        set_global_webview_occlusion("modal_dialog", True, token="outer")
        set_global_webview_occlusion("modal_dialog", True, token="inner")
        set_global_webview_occlusion("modal_dialog", False, token="inner")
        set_global_webview_occlusion("modal_dialog", False, token="inner")
        assert is_globally_occluded() is True
        set_global_webview_occlusion("modal_dialog", False, token="outer")
        assert is_globally_occluded() is False
    finally:
        clear_global_webview_occlusion()


def test_d04_clear_cached_description_images_blocks_windows_ntfs_junctions_and_parent_junctions(
    tmp_path: Path, monkeypatch
):
    import subprocess
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows NTFS junction test requires win32")

    # 1. Leaf junction: userdata/cache/images -> victim_docs
    user_dir = tmp_path / "userdata_leaf"
    assets_dir = tmp_path / "assets_leaf"
    victim_docs = tmp_path / "victim_docs"
    victim_docs.mkdir(parents=True)
    thesis = victim_docs / "thesis.docx"
    thesis.write_bytes(b"important-thesis")
    (user_dir / "cache").mkdir(parents=True)
    leaf_junction = user_dir / "cache" / "images"
    res = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(leaf_junction), str(victim_docs)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"mklink /J failed: {res.stderr}"

    monkeypatch.setattr(config, "_USER_DATA_DIR", user_dir)
    monkeypatch.setattr(utils, "get_active_assets_dir", lambda: assets_dir)

    assert utils.clear_cached_description_images() == 0
    assert thesis.exists()
    assert thesis.read_bytes() == b"important-thesis"

    # 2. Parent junction: userdata/cache -> victim_parent (containing images/secret.png)
    user_dir_parent = tmp_path / "userdata_parent"
    user_dir_parent.mkdir(parents=True)
    victim_parent = tmp_path / "victim_parent"
    (victim_parent / "images").mkdir(parents=True)
    secret_img = victim_parent / "images" / "secret.png"
    secret_img.write_bytes(b"outside-secret")
    parent_junction = user_dir_parent / "cache"
    res2 = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(parent_junction), str(victim_parent)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res2.returncode == 0, f"mklink /J parent failed: {res2.stderr}"

    monkeypatch.setattr(config, "_USER_DATA_DIR", user_dir_parent)
    assert utils.clear_cached_description_images() == 0
    assert secret_img.exists()
    assert secret_img.read_bytes() == b"outside-secret"


def test_d04_open_external_async_logout_mid_write_unlinks_and_never_launches(
    tmp_path: Path, monkeypatch
):
    from unittest.mock import AsyncMock

    pcache = PreviewFileCache(tmp_path / "pcache")
    store = CourseCacheStore(cache_dir=tmp_path / "store", namespace="acct-a")
    srv = LocalPreviewServer()
    coord = CoursesCoordinator(
        page=_make_fake_page(ft.PagePlatform.WINDOWS),  # type: ignore[arg-type]
        catalog_service=CourseCatalogService(cache_store=store, preview_cache=pcache),
        preview_cache=pcache,
        archive_service=ArchiveService(),
        preview_server=srv,
        on_open_assignment_fallback=lambda _m: None,
        on_launch_url=lambda _u: None,
    )
    launch_mock = AsyncMock()
    save_mock = AsyncMock()
    monkeypatch.setattr(ft.UrlLauncher, "launch_url", launch_mock)
    monkeypatch.setattr(coord, "_save_bytes_async", save_mock)

    orig_write = CoursesCoordinator._write_external_file_with_motw

    def _write_with_mid_logout(out: Path, data: bytes) -> None:
        # Simulate logout happening right before/during disk write!
        coord.reset_for_account_switch(None)
        orig_write(out, data)

    monkeypatch.setattr(
        coord,
        "_write_external_file_with_motw",
        _write_with_mid_logout,
    )
    try:
        asyncio.run(coord._open_external_async("confidential.pdf", b"%PDF-1.4\nsecret"))
        ext_dir = pcache.cache_dir / "external"
        remaining = list(ext_dir.iterdir()) if ext_dir.exists() else []
        assert remaining == []
        launch_mock.assert_not_awaited()
        save_mock.assert_not_awaited()
    finally:
        coord.close()
