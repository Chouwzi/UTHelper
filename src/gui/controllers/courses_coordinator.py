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

from core.courses.activity_service import CourseActivityService
from core.courses.archive_service import ArchiveService
from core.courses.catalog_service import CourseCatalogService
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.models import (
    CourseAttachment,
    CourseModuleItem,
    CourseSource,
    CourseSummary,
    PreviewKind,
    RecentMaterialEntry,
)
from core.courses.preview_cache import PreviewFileCache
from gui.components.courses._shared import (
    PaneErrorOverlay,
    PaneLoadingOverlay,
    build_expand_toggle_button,
    build_viewer_header,
    refresh_viewer_header,
    safe_control_update,
    sync_expand_toggle_button,
)
from gui.components.courses.activity_views import (
    AttendanceActivityView,
    ForumActivityView,
    GroupActivityView,
    PaneAssignmentView,
    QuizActivityView,
    RichContentActivityView,
)
from gui.components.courses.archive_explorer_view import ArchiveExplorerView
from gui.components.courses.courses_tab_view import CoursesTabView
from gui.components.courses.document_reader_view import DocumentReaderView
from gui.components.courses.webview_pane import CoursesWebViewPane
from gui.components.detail_view import DetailView
from gui.core.theme import C
from gui.view_models.courses_state import format_file_size, safe_http_url

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
        on_toggle_expand: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(expand=True, bgcolor=C.BG)
        self._expanded = False
        self._show_expand_toggle = False
        self._expand_btn = build_expand_toggle_button(
            expanded=False,
            on_toggle=lambda: on_toggle_expand() if callable(on_toggle_expand) else None,
            visible=False,
        )
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
            trailing_actions=[self._expand_btn, self._ext_btn],
        )
        self.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[self._header, webview],
        )

    def set_expanded(self, expanded: bool, *, visible: bool = True) -> None:
        self._expanded = bool(expanded)
        self._show_expand_toggle = bool(visible)
        sync_expand_toggle_button(
            self._expand_btn,
            expanded=self._expanded,
            visible=self._show_expand_toggle,
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
        sync_expand_toggle_button(
            self._expand_btn,
            expanded=self._expanded,
            visible=self._show_expand_toggle,
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
        activity_service: Optional[CourseActivityService] = None,
        courses_client: Any = None,
        thnn_client: Any = None,
        assignment_detail_fetcher: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
        on_assignment_status_changed: Optional[Callable[..., Any]] = None,
        submission_workflow_factory: Optional[Callable[..., Any]] = None,
        all_assignments_provider: Optional[Callable[[], list[dict[str, Any]]]] = None,
    ) -> None:
        self._page = page
        self.catalog_service = catalog_service
        self.preview_cache = preview_cache
        self.archive_service = archive_service
        self.preview_server = preview_server
        self._on_open_assignment = on_open_assignment_fallback
        self._on_launch_url = on_launch_url
        self._activity_service = activity_service
        self._courses_client = courses_client
        self._thnn_client = thnn_client
        self._assignment_detail_fetcher = assignment_detail_fetcher
        self._on_assignment_status_changed = on_assignment_status_changed
        self._submission_workflow_factory = submission_workflow_factory
        self._all_assignments_provider = all_assignments_provider
        self._initial_loaded = False
        self._attachment_load_seq = 0

        if hasattr(self.preview_server, "register_session_provider"):
            if courses_client is not None:
                self.preview_server.register_session_provider("courses", courses_client)
            if thnn_client is not None:
                self.preview_server.register_session_provider("thnn", thnn_client)

        self.courses_view = CoursesTabView(
            page=page,
            catalog_service=catalog_service,
            on_open_assignment=self._handle_open_assignment,
            on_open_webview_activity=self._handle_open_webview_activity,
            on_open_attachment_viewer=lambda c, mod, att: self._schedule(
                self.open_attachment_async, c, mod, att
            ),
            on_download_attachment=lambda c, att: self._schedule(
                self.save_attachment_async,
                att,
                getattr(c, "source", "courses"),
            ),
            on_launch_url=self._handle_course_url_action,
            on_open_custom_activity=self._handle_open_custom_activity,
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

    def _client_for(self, source: CourseSource) -> Any:
        return self._thnn_client if source == "thnn" else self._courses_client

    def _build_assignment_payload(
        self, course: CourseSummary, mod: CourseModuleItem
    ) -> Optional[dict[str, Any]]:
        url = safe_http_url(mod.web_url) or safe_http_url(mod.url)
        if not url:
            return None
        if callable(self._all_assignments_provider):
            try:
                for item in self._all_assignments_provider() or []:
                    if item.get("url") == url or (
                        mod.cmid
                        and item.get("cmid") == mod.cmid
                        and item.get("course_id") == course.id
                    ):
                        return dict(item)
            except Exception:
                pass
        return {
            "title": mod.name or "Bài tập",
            "course": course.display_name or course.fullname,
            "course_id": course.id,
            "cmid": mod.cmid,
            "type": "assignment" if mod.modname == "assign" else mod.modname,
            "url": url,
            "site_identity": course.source,
            "submission_status": "unknown",
            "details": {"description_html": mod.description_html or ""},
        }

    def _handle_open_assignment(
        self, course: CourseSummary, mod: CourseModuleItem
    ) -> None:
        if self._assignment_detail_fetcher is None:
            self._dispatch_cb(self._on_open_assignment, mod)
            return
        payload = self._build_assignment_payload(course, mod)
        if payload is None:
            self._dispatch_cb(self._on_open_assignment, mod)
            return

        detail_view = DetailView(
            self._page,
            on_close=self.courses_view.pop_overlay,
            get_client=lambda src=course.source: self._client_for(src),
            on_status_changed=self._on_assignment_status_changed,
            submission_workflow_factory=self._submission_workflow_factory,
            embedded_in_pane=True,
            on_open_url=self._handle_course_url_action,
        )
        pane_assign_holder: dict[str, PaneAssignmentView] = {}

        def _reload_assign() -> None:
            pa = pane_assign_holder.get("view")
            if pa is not None:
                pa.show_loading(payload)
                self._schedule(self._load_pane_assignment_async, pa, payload)

        pane_assign = PaneAssignmentView(
            detail_view=detail_view,
            title=mod.name or "Bài tập",
            on_close=self.courses_view.pop_overlay,
            on_refresh=_reload_assign,
            on_open_webview=lambda t=(mod.name or "Bài tập"), u=payload["url"], s=course.source, m=mod: self.open_in_app_webview(
                s, t, u, tag=f"webview:assign:{m.cmid}"
            ),
            on_toggle_expand=self.courses_view.toggle_pane3_expanded,
        )
        pane_assign_holder["view"] = pane_assign
        self.courses_view.push_overlay(pane_assign, tag=f"assign:{mod.cmid}")
        pane_assign.show_loading(payload)
        self._schedule(self._load_pane_assignment_async, pane_assign, payload)

    async def _load_pane_assignment_async(
        self, pane_assign: PaneAssignmentView, payload: dict[str, Any]
    ) -> None:
        fetcher = self._assignment_detail_fetcher
        if fetcher is None:
            return
        try:
            full_data = await asyncio.to_thread(fetcher, payload)
            if full_data and "details" in full_data:
                desc_html = full_data["details"].get("description_html", "")
                if desc_html:
                    from gui.core.utils import pre_cache_description_images

                    cached_html = await asyncio.to_thread(
                        pre_cache_description_images, desc_html
                    )
                    full_data = dict(full_data)
                    full_data["details"] = dict(full_data["details"])
                    full_data["details"]["description_html"] = cached_html
            pane_assign.update_detail(full_data)
        except Exception:
            logger.exception("Failed to load Pane-3 assignment details")
            pane_assign.show_error_banner()

    def open_in_app_webview(
        self,
        source: CourseSource,
        title: str,
        url: str,
        *,
        tag: str = "",
    ) -> None:
        """Open any safe HTTP(S) URL inside authenticated Pane-3 CoursesWebViewPane."""
        safe_url = safe_http_url(url)
        if not safe_url:
            return
        pane = CoursesWebViewPane(
            page=self._page,
            source=source,
            title=title or "Trình duyệt trong ứng dụng",
            url=safe_url,
            preview_server=self.preview_server,
            session_client=self._client_for(source),
            on_back=self.courses_view.pop_overlay,
            on_open_external=self._launch_url_safe,
            on_toggle_expand=self.courses_view.toggle_pane3_expanded,
            embedded_webview_factory=_try_build_embedded_webview,
        )
        self.courses_view.push_overlay(pane, tag=tag or f"webview:{abs(hash(safe_url))}")

    def _handle_course_url_action(self, url: str) -> None:
        safe_url = safe_http_url(url)
        if not safe_url:
            return
        if (
            self._activity_service is None
            and self._courses_client is None
            and self._thnn_client is None
        ):
            self._launch_url_safe(safe_url)
            return
        selected = None
        if hasattr(self.courses_view, "_state"):
            selected = self.courses_view._state.selected_course()
        source: CourseSource = (
            "thnn"
            if "thnn.ut.edu.vn" in safe_url.lower()
            else (selected.source if selected is not None else "courses")
        )
        self.open_in_app_webview(
            source,
            (selected.display_name or selected.fullname) if selected is not None else "Moodle Web",
            safe_url,
        )

    def _handle_open_webview_activity(
        self, course: CourseSummary, mod: CourseModuleItem
    ) -> None:
        if (
            self._activity_service is None
            and self._courses_client is None
            and self._thnn_client is None
        ):
            self._open_webview_activity(mod)
            return

        modname = (mod.modname or "").lower()
        if modname == "attendance" and self._activity_service is not None:
            view = AttendanceActivityView(
                source=course.source,
                module=mod,
                activity_service=self._activity_service,
                on_close=self.courses_view.pop_overlay,
                on_open_webview=lambda t, u, s=course.source, m=mod: self.open_in_app_webview(
                    s, t, u, tag=f"webview:att:{m.cmid}"
                ),
                on_toggle_expand=self.courses_view.toggle_pane3_expanded,
            )
            self.courses_view.push_overlay(view, tag=f"activity:attendance:{mod.cmid}")
            return

        if modname in {"groupselect", "choicegroup", "choice"} and self._activity_service is not None:
            view = GroupActivityView(
                source=course.source,
                course_id=course.id,
                module=mod,
                activity_service=self._activity_service,
                on_close=self.courses_view.pop_overlay,
                on_open_webview=lambda t, u, s=course.source, m=mod: self.open_in_app_webview(
                    s, t, u, tag=f"webview:grp:{m.cmid}"
                ),
                on_toggle_expand=self.courses_view.toggle_pane3_expanded,
            )
            self.courses_view.push_overlay(view, tag=f"activity:group:{mod.cmid}")
            return

        if modname == "quiz" and self._activity_service is not None:
            view = QuizActivityView(
                source=course.source,
                course_id=course.id,
                module=mod,
                activity_service=self._activity_service,
                on_close=self.courses_view.pop_overlay,
                on_open_webview=lambda t, u, s=course.source, m=mod: self.open_in_app_webview(
                    s, t, u, tag=f"webview:quiz:{m.cmid}"
                ),
                on_toggle_expand=self.courses_view.toggle_pane3_expanded,
            )
            self.courses_view.push_overlay(view, tag=f"activity:quiz:{mod.cmid}")
            return

        target_url = safe_http_url(mod.web_url) or safe_http_url(mod.url)
        if target_url:
            self.open_in_app_webview(
                course.source,
                mod.name or (mod.modname or "Hoạt động").upper(),
                target_url,
                tag=f"webview:{mod.cmid}",
            )

    def _handle_open_custom_activity(
        self, course: CourseSummary, mod: CourseModuleItem
    ) -> None:
        if self._activity_service is None:
            target = safe_http_url(mod.web_url) or safe_http_url(mod.url)
            if target:
                self._handle_course_url_action(target)
            return

        modname = (mod.modname or "").lower()
        if modname == "forum":
            view = ForumActivityView(
                source=course.source,
                course_id=course.id,
                module=mod,
                activity_service=self._activity_service,
                on_close=self.courses_view.pop_overlay,
                on_open_webview=lambda t, u, s=course.source, m=mod: self.open_in_app_webview(
                    s, t, u, tag=f"webview:forum:{m.cmid}"
                ),
                on_toggle_expand=self.courses_view.toggle_pane3_expanded,
            )
            self.courses_view.push_overlay(view, tag=f"activity:forum:{mod.cmid}")
            return

        if modname in {"book", "page"}:
            view = RichContentActivityView(
                source=course.source,
                course_id=course.id,
                module=mod,
                activity_service=self._activity_service,
                on_close=self.courses_view.pop_overlay,
                on_open_webview=lambda t, u, s=course.source, m=mod: self.open_in_app_webview(
                    s, t, u, tag=f"webview:rich:{m.cmid}"
                ),
                on_toggle_expand=self.courses_view.toggle_pane3_expanded,
            )
            self.courses_view.push_overlay(view, tag=f"activity:rich:{mod.cmid}")

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
                on_toggle_expand=self.courses_view.toggle_pane3_expanded,
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
                on_toggle_expand=self.courses_view.toggle_pane3_expanded,
            )
            self.courses_view.push_overlay(
                explorer, tag=f"archive:{mod.cmid}:{att.file_name}"
            )
            return
        self._open_entry_reader(att.file_name, raw_bytes, att.preview_kind)

    def _open_entry_reader(
        self, name: str, data: bytes, kind: PreviewKind
    ) -> None:
        if kind == "archive":
            nested = ArchiveExplorerView(
                archive_name=name,
                archive_bytes=data,
                archive_service=self.archive_service,
                on_close=self.courses_view.pop_overlay,
                on_open_entry=lambda n, d, k: self._open_entry_reader(n, d, k),
                on_save_raw=lambda n, d: self._schedule(self._save_bytes_async, n, d),
                on_toggle_expand=self.courses_view.toggle_pane3_expanded,
            )
            self.courses_view.push_overlay(nested, tag=f"archive:nested:{name}")
            return
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
            on_open_link=self._handle_course_url_action,
            webview_builder=_try_build_embedded_webview,
            pdf_cache_dir=getattr(self.preview_cache, "cache_dir", None),
            on_toggle_expand=self.courses_view.toggle_pane3_expanded,
        )
        self.courses_view.push_overlay(reader, tag=f"reader:{name}")

    def _dismiss_overlay_instance(self, target: ft.Control) -> None:
        dismiss_fn = getattr(self.courses_view, "_dismiss_overlay_instance", None)
        if callable(dismiss_fn):
            dismiss_fn(target)
            return
        overlays = getattr(self.courses_view, "_overlays", None)
        if isinstance(overlays, list) and overlays and overlays[-1] is target:
            self.courses_view.pop_overlay()

    async def open_attachment_async(
        self, course: CourseSummary, mod: CourseModuleItem, att: CourseAttachment
    ) -> None:
        """Show immediate loading state in Pane 3, fetch attachment (cache-first), and open viewer."""
        self._attachment_load_seq += 1
        req_seq = self._attachment_load_seq
        size_hint = format_file_size(att.file_size)
        subtitle = f"Đang tải xuống · {size_hint}" if size_hint else "Đang tải tài liệu từ Moodle..."
        loading_overlay = PaneLoadingOverlay(
            title=att.file_name or mod.name or "Tài liệu",
            subtitle=subtitle,
            status_message=f"Đang tải «{att.file_name}» vào bộ nhớ đệm...",
            preset="archive" if (att.is_archive or att.preview_kind == "archive") else "document",
            on_back=self.courses_view.pop_overlay,
            on_toggle_expand=self.courses_view.toggle_pane3_expanded,
        )
        self.courses_view.push_overlay(loading_overlay, tag=f"viewer:load:{req_seq}")
        try:
            await asyncio.sleep(0)
            raw = await self._fetch_attachment_bytes_async(course.source, att)
            if req_seq != self._attachment_load_seq or loading_overlay._disposed:
                return
            loading_overlay.set_status(
                f"Đang dựng trình xem cho «{att.file_name}»...",
                subtitle=f"Hoàn tất tải · {format_file_size(len(raw)) or 'Moodle'}",
            )
            await asyncio.sleep(0)
            if req_seq != self._attachment_load_seq or loading_overlay._disposed:
                return
            self._dismiss_overlay_instance(loading_overlay)
            self.open_attachment_bytes_in_overlay(course, mod, att, raw)
        except Exception as exc:
            logger.warning("Open attachment failed for %s: %s", att.file_name, exc)
            self._dismiss_overlay_instance(loading_overlay)
            safe_fallback = safe_http_url(mod.web_url) or safe_http_url(att.file_url)
            err_holder: dict[str, PaneErrorOverlay] = {}
            err_overlay = PaneErrorOverlay(
                title=att.file_name or mod.name or "Tài liệu",
                message=f"Không thể tải hoặc mở «{att.file_name}».\nChi tiết: {exc}",
                on_back=self.courses_view.pop_overlay,
                on_retry=lambda c=course, m=mod, a=att: (
                    self._dismiss_overlay_instance(err_holder["view"]),
                    self._schedule(self.open_attachment_async, c, m, a),
                ),
                on_open_webview=(
                    (lambda u=safe_fallback, s=course.source, t=att.file_name: self.open_in_app_webview(s, t, u))
                    if safe_fallback
                    else None
                ),
                on_toggle_expand=self.courses_view.toggle_pane3_expanded,
            )
            err_holder["view"] = err_overlay
            self.courses_view.push_overlay(err_overlay, tag=f"viewer:error:{mod.cmid}")

    async def save_attachment_async(
        self, att: CourseAttachment, source: str = "courses"
    ) -> None:
        """Fetch an attachment with user feedback and prompt to save it locally."""
        self._show_snack(f"Đang tải xuống «{att.file_name}»...")
        try:
            raw = await self._fetch_attachment_bytes_async(source, att)
            await self._save_bytes_async(att.file_name, raw)
        except Exception as exc:
            logger.warning("Save attachment failed for %s: %s", att.file_name, exc)
            self._show_snack(f"Lỗi tải file «{att.file_name}»: {exc}")

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
        """Stop the ephemeral preview HTTP server and dispose active overlays on shutdown."""
        clear_fn = getattr(self.courses_view, "clear_overlays", None)
        if callable(clear_fn):
            try:
                clear_fn(update=False)
            except Exception:
                pass
        try:
            self.preview_server.stop()
        except Exception:
            logger.debug("Preview server stop skipped", exc_info=True)
