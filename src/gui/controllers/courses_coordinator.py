"""Presentation coordinator for Courses workspace, caching, readers, and archive overlays."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import flet as ft

from core.courses.archive_service import ArchiveService
from core.courses.catalog_service import CourseCatalogService
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.models import (
    CourseAttachment,
    CourseModuleItem,
    CourseSummary,
    PreviewKind,
    RecentMaterialEntry,
)
from core.courses.preview_cache import PreviewFileCache
from gui.components.courses._shared import (
    build_viewer_header,
    refresh_viewer_header,
    safe_control_update,
)
from gui.components.courses.archive_explorer_view import ArchiveExplorerView
from gui.components.courses.courses_tab_view import CoursesTabView
from gui.components.courses.document_reader_view import DocumentReaderView
from gui.core.theme import C
from gui.view_models.courses_state import safe_http_url

logger = logging.getLogger(__name__)
_SAFE_TMP_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DANGEROUS_EXTERNAL_EXTS = frozenset(
    {".exe", ".bat", ".cmd", ".ps1", ".vbs", ".msi", ".scr", ".com", ".pif"}
)


def _try_build_embedded_webview(url: str) -> Optional[ft.Control]:
    """Use compiled UthQuizWebView extension if available for a safe HTTP(S) URL."""
    safe_url = safe_http_url(url)
    if not safe_url:
        return None
    try:
        from extensions.flet_uth_quiz import UthQuizWebView

        return UthQuizWebView(url=safe_url, expand=True)
    except Exception:
        return None


class _WebViewActivityOverlay(ft.Container):
    """Themed overlay hosting an embedded WebView activity with external fallback."""

    def __init__(
        self,
        *,
        title: str,
        subtitle: str,
        webview: ft.Control,
        on_close: Callable[[], Any],
        on_open_external: Callable[[], Any],
    ) -> None:
        super().__init__(expand=True, bgcolor=C.BG)
        self._ext_btn = ft.IconButton(
            icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
            tooltip="Mở bằng trình duyệt",
            icon_color=C.TEXT_PRIMARY,
            on_click=lambda _e: on_open_external(),
        )
        (
            self._header,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        ) = build_viewer_header(
            title=title or "Hoạt động",
            subtitle=subtitle,
            back_tooltip="Đóng",
            on_back=lambda _e: on_close(),
            trailing_actions=[self._ext_btn],
        )
        self.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[self._header, webview],
        )

    def update_theme(self) -> None:
        """Apply updated semantic theme tokens to the WebView overlay header."""
        self.bgcolor = C.BG
        refresh_viewer_header(
            self._header,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        )
        self._ext_btn.icon_color = C.TEXT_PRIMARY
        safe_control_update(self)


class CoursesCoordinator:
    """Thin coordinator decoupling AppController from Courses previews and file actions."""

    def __init__(
        self,
        *,
        page: ft.Page,
        catalog_service: CourseCatalogService,
        preview_cache: PreviewFileCache,
        archive_service: ArchiveService,
        preview_server: LocalPreviewServer,
        on_open_assignment_fallback: Callable[[CourseModuleItem], Any],
        on_launch_url: Callable[[str], Any],
    ) -> None:
        self._page = page
        self.catalog_service = catalog_service
        self.preview_cache = preview_cache
        self.archive_service = archive_service
        self.preview_server = preview_server
        self._on_open_assignment = on_open_assignment_fallback
        self._on_launch_url = on_launch_url
        self._initial_loaded = False

        self.courses_view = CoursesTabView(
            page=page,
            catalog_service=catalog_service,
            on_open_assignment=lambda _c, mod: self._dispatch_cb(
                self._on_open_assignment, mod
            ),
            on_open_webview_activity=lambda _c, mod: self._open_webview_activity(mod),
            on_open_attachment_viewer=lambda c, mod, att: self._schedule(
                self.open_attachment_async, c, mod, att
            ),
            on_download_attachment=lambda c, att: self._schedule(
                self.save_attachment_async,
                att,
                getattr(c, "source", "courses"),
            ),
            on_launch_url=self._launch_url_safe,
        )

    def _schedule(self, coro_fn: Callable[..., Any], *args: Any) -> None:
        run_task = getattr(self._page, "run_task", None)
        if callable(run_task):
            run_task(coro_fn, *args)
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro_fn(*args))
        except RuntimeError:
            pass

    def _dispatch_cb(self, fn: Callable[..., Any], *args: Any) -> None:
        if not callable(fn):
            return
        result = fn(*args)
        if inspect.isawaitable(result):
            async def _await_res() -> None:
                await result

            self._schedule(_await_res)

    def _launch_url_safe(self, url: str) -> None:
        safe_url = safe_http_url(url)
        if safe_url:
            self._dispatch_cb(self._on_launch_url, safe_url)

    async def ensure_loaded_async(self, *, background_sync_hours: int = 6) -> None:
        """Load cached courses on first visit; reset flag on failure for retry."""
        if self._initial_loaded:
            return
        self._initial_loaded = True
        try:
            if hasattr(self.courses_view, "CATALOG_STALE_HOURS"):
                try:
                    hours = max(1, int(background_sync_hours))
                except (TypeError, ValueError):
                    hours = 6
                self.courses_view.CATALOG_STALE_HOURS = hours
            sig = inspect.signature(self.courses_view.load_initial_async)
            if "background_sync_hours" in sig.parameters:
                await self.courses_view.load_initial_async(
                    background_sync_hours=background_sync_hours
                )
            else:
                await self.courses_view.load_initial_async()
        except Exception:
            self._initial_loaded = False
            raise

    def _open_webview_activity(self, mod: CourseModuleItem) -> None:
        safe_url = safe_http_url(mod.web_url)
        if not safe_url:
            if not mod.web_url:
                safe_url = safe_http_url(mod.url)
            if not safe_url:
                return

        wv = _try_build_embedded_webview(safe_url)
        if wv is not None:
            overlay = _WebViewActivityOverlay(
                title=mod.name,
                subtitle=(mod.modname or "Hoạt động").upper(),
                webview=wv,
                on_close=self.courses_view.pop_overlay,
                on_open_external=lambda u=safe_url: self._launch_url_safe(u),
            )
            self.courses_view.push_overlay(overlay, tag=f"webview:{mod.cmid}")
            return
        self._launch_url_safe(safe_url)

    async def _fetch_attachment_bytes_async(
        self, source: str, att: CourseAttachment
    ) -> bytes:
        try:
            cached_path = await asyncio.to_thread(
                self.preview_cache.get_cached_path,
                att.file_url,
                att.time_modified,
                att.file_name,
            )
            if cached_path is not None and cached_path.is_file():
                return await asyncio.to_thread(cached_path.read_bytes)
        except OSError as exc:
            logger.debug("Preview cache read failed for %s: %s", att.file_name, exc)

        raw = await asyncio.to_thread(
            self.catalog_service.fetch_attachment_bytes, source, att.file_url
        )
        try:
            await asyncio.to_thread(
                self.preview_cache.put_bytes,
                att.file_url,
                att.time_modified,
                att.file_name,
                raw,
            )
        except OSError as exc:
            logger.warning(
                "Failed to persist preview cache for %s: %s", att.file_name, exc
            )
        return raw

    def open_attachment_bytes_in_overlay(
        self,
        course: CourseSummary,
        mod: CourseModuleItem,
        att: CourseAttachment,
        raw_bytes: bytes,
    ) -> None:
        """Record recent access and open ArchiveExplorerView or DocumentReaderView overlay."""
        try:
            recorder = getattr(self.catalog_service, "record_recent", None)
            if callable(recorder):
                updated = recorder(
                    RecentMaterialEntry(
                        course_key=course.course_key,
                        course_name=course.display_name or course.fullname,
                        cmid=mod.cmid,
                        title=mod.name,
                        attachment_url=att.file_url,
                        file_name=att.file_name,
                        opened_at=int(time.time()),
                        source=course.source,
                        course_id=course.id,
                    )
                )
                if isinstance(updated, list) and hasattr(self.courses_view, "_state"):
                    self.courses_view._state.recent_items = list(updated)
        except Exception:
            logger.debug("Ignoring record_recent failure", exc_info=True)

        if att.is_archive or att.preview_kind == "archive":
            explorer = ArchiveExplorerView(
                archive_name=att.file_name,
                archive_bytes=raw_bytes,
                archive_service=self.archive_service,
                on_close=self.courses_view.pop_overlay,
                on_open_entry=lambda name, data, kind: self._open_entry_reader(
                    name, data, kind
                ),
                on_save_raw=lambda name, data: self._schedule(
                    self._save_bytes_async, name, data
                ),
            )
            self.courses_view.push_overlay(
                explorer, tag=f"archive:{mod.cmid}:{att.file_name}"
            )
            return
        self._open_entry_reader(att.file_name, raw_bytes, att.preview_kind)

    def _open_entry_reader(
        self, name: str, data: bytes, kind: PreviewKind
    ) -> None:
        reader = DocumentReaderView(
            file_name=name,
            data=data,
            kind=kind,
            preview_server=self.preview_server,
            on_close=self.courses_view.pop_overlay,
            on_save=lambda n, b: self._schedule(self._save_bytes_async, n, b),
            on_open_external=lambda n, b: self._schedule(
                self._open_external_async, n, b
            ),
            webview_builder=_try_build_embedded_webview,
        )
        self.courses_view.push_overlay(reader, tag=f"reader:{name}")

    async def open_attachment_async(
        self, course: CourseSummary, mod: CourseModuleItem, att: CourseAttachment
    ) -> None:
        """Fetch an attachment (using preview cache) and open its in-app viewer."""
        try:
            raw = await self._fetch_attachment_bytes_async(course.source, att)
            self.open_attachment_bytes_in_overlay(course, mod, att, raw)
        except Exception as exc:
            logger.warning("Open attachment failed for %s: %s", att.file_name, exc)
            self._show_snack(f"Không thể mở {att.file_name}: {exc}")

    async def save_attachment_async(
        self, att: CourseAttachment, source: str = "courses"
    ) -> None:
        """Fetch an attachment and prompt the user to save it locally."""
        try:
            raw = await self._fetch_attachment_bytes_async(source, att)
            await self._save_bytes_async(att.file_name, raw)
        except Exception as exc:
            logger.warning("Save attachment failed for %s: %s", att.file_name, exc)
            self._show_snack(f"Lỗi tải file: {exc}")

    async def _save_bytes_async(self, file_name: str, data: bytes) -> None:
        try:
            saved = await ft.FilePicker().save_file(
                dialog_title="Lưu tài liệu",
                file_name=file_name,
                src_bytes=data,
            )
        except Exception as exc:
            logger.warning("Save file failed for %s: %s", file_name, exc)
            self._show_snack(f"Không thể lưu {file_name}: {exc}")
            return
        if saved:
            self._show_snack(f"Đã lưu: {file_name}")

    async def _open_external_async(self, file_name: str, data: bytes) -> None:
        raw_leaf = Path(file_name or "document").name.strip()
        stripped_leaf = raw_leaf.rstrip(". ")
        raw_ext = Path(raw_leaf).suffix.lower()
        clean_ext = Path(stripped_leaf).suffix.lower()
        if raw_ext in _DANGEROUS_EXTERNAL_EXTS or clean_ext in _DANGEROUS_EXTERNAL_EXTS:
            await self._save_bytes_async(file_name, data)
            return

        safe_leaf = _SAFE_TMP_RE.sub("_", stripped_leaf).strip("._") or "document"
        stem = Path(safe_leaf).stem or "document"
        ext = Path(safe_leaf).suffix
        if ext.lower() in _DANGEROUS_EXTERNAL_EXTS:
            await self._save_bytes_async(file_name, data)
            return

        unique_name = f"{stem}_{uuid.uuid4().hex[:8]}{ext}"
        try:
            tmp_dir = self.preview_cache.cache_dir / "external"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            out = tmp_dir / unique_name
            await asyncio.to_thread(out.write_bytes, data)
            await ft.UrlLauncher().launch_url(out.as_uri())
        except Exception:
            await self._save_bytes_async(file_name, data)

    def _show_snack(self, text: str) -> None:
        sb = ft.SnackBar(
            content=ft.Text(text, size=13, color=C.TEXT_PRIMARY),
            bgcolor=C.SURFACE,
            duration=3000,
        )
        show_dlg = getattr(self._page, "show_dialog", None)
        if callable(show_dlg):
            try:
                show_dlg(sb)
                return
            except (AttributeError, TypeError):
                pass
        overlay = getattr(self._page, "overlay", None)
        if overlay is not None:
            overlay[:] = [item for item in overlay if not isinstance(item, ft.SnackBar)]
            overlay.append(sb)
            sb.open = True
            update = getattr(self._page, "update", None)
            if callable(update):
                try:
                    update()
                except Exception:
                    pass

    def update_theme(self) -> None:
        """Propagate semantic theme changes to CoursesTabView and active overlays."""
        self.courses_view.update_theme()
        for overlay in getattr(self.courses_view, "_overlays", ()):
            updater = getattr(overlay, "update_theme", None)
            if callable(updater):
                try:
                    updater()
                except Exception:
                    logger.debug("Overlay update_theme skipped", exc_info=True)

    def close(self) -> None:
        """Stop the ephemeral preview HTTP server on shutdown."""
        try:
            self.preview_server.stop()
        except Exception:
            logger.debug("Preview server stop skipped", exc_info=True)
