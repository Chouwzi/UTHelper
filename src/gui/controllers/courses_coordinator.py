"""Presentation coordinator for Courses workspace, caching, readers, and archive overlays."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import re
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import flet as ft

import platform_utils
from config import settings
from core.courses.activity_service import CourseActivityService
from core.demo_sandbox import is_demo_account
from core.courses.archive_service import ArchiveService
from core.courses.catalog_service import CourseCatalogService
from core.courses.document_converter import (
    ConvertedDocument,
    clear_converted_document_cache,
    convert_document_bytes,
)
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.models import (
    CourseAttachment,
    CourseModuleItem,
    CourseSection,
    CourseSource,
    CourseSummary,
    PreviewKind,
    RecentMaterialEntry,
)
from core.courses.pdf_renderer import PdfDocumentSession, clear_rendered_pdf_cache
from core.courses.office_viewer_service import (
    OfficeViewerDescriptor,
    prepare_office_viewer,
    prepare_office_viewer_from_file,
    prepare_pdf_viewer,
)
from core.courses.preview_cache import PreviewFileCache
from core.courses.windows_office_bridge import render_office_pdf
from core.url_validation import validate_https_url

try:
    from core.webview_auth import (
        classify_uth_webview_url as _core_classify_uth_webview_url,
        normalize_uth_webview_target_url as _core_normalize_uth_webview_target_url,
    )
except ImportError:
    _core_classify_uth_webview_url = None
    _core_normalize_uth_webview_target_url = None


def classify_uth_webview_url(url: str) -> str:
    fn = _core_classify_uth_webview_url
    if fn is None:
        wv_mod = sys.modules.get("core.webview_auth")
        fn = getattr(wv_mod, "classify_uth_webview_url", None)
    if callable(fn):
        return str(fn(url))
    validated = validate_https_url(url)
    if validated is None:
        return "external"
    parsed = urllib.parse.urlsplit(validated)
    if parsed.port is not None:
        return "external"
    host = (parsed.hostname or "").casefold()
    if host in ("courses.ut.edu.vn", "elearning.ut.edu.vn"):
        return "courses"
    if host == "thnn.ut.edu.vn":
        return "thnn"
    if host == "portal.ut.edu.vn":
        return "portal"
    return "external"


def normalize_uth_webview_target_url(url: str, *, has_credentials: bool = True) -> str:
    fn = _core_normalize_uth_webview_target_url
    if fn is None:
        wv_mod = sys.modules.get("core.webview_auth")
        fn = getattr(wv_mod, "normalize_uth_webview_target_url", None)
    if callable(fn):
        return str(fn(url, has_credentials=has_credentials))
    if not isinstance(url, str) or not url:
        return ""
    validated = validate_https_url(url)
    if validated is None:
        return url
    parsed = urllib.parse.urlsplit(validated)
    if parsed.port is not None:
        return url
    host = (parsed.hostname or "").casefold()
    if host == "elearning.ut.edu.vn":
        return urllib.parse.urlunsplit(
            ("https", "courses.ut.edu.vn", parsed.path, parsed.query, parsed.fragment)
        )
    if host == "portal.ut.edu.vn" and parsed.path in ("", "/", "/login") and has_credentials:
        return "https://portal.ut.edu.vn/dashboard"
    return validated
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
from gui.components.courses.embedded_webview_host import (
    EmbeddedWebViewHost,
    build_embedded_webview_host,
    is_embedded_webview_supported,
)
from gui.components.courses.webview_pane import CoursesWebViewPane
from gui.components.detail_view import DetailView
from gui.core.theme import C
from gui.core.utils import DEMO_LMS_WEBVIEW_GUARD_MESSAGE, is_uth_lms_url
from gui.view_models.courses_state import format_file_size, safe_http_url

logger = logging.getLogger(__name__)
_SAFE_TMP_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_EXTERNAL_OPEN_EXTS = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".ppt",
        ".xlsx",
        ".xls",
        ".csv",
        ".txt",
        ".md",
        ".rtf",
        ".odt",
        ".ods",
        ".odp",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".zip",
        ".rar",
        ".7z",
    }
)


def _supports_kwarg(fn: Callable[..., Any], kwarg: str) -> bool:
    """Return True when ``fn`` accepts keyword argument ``kwarg`` (directly or via **kwargs)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return kwarg in params or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def _try_build_embedded_webview(
    url: str, *, page: Optional[ft.Page] = None
) -> Optional[ft.Control]:
    """Build an embedded WebView control if the current platform supports self-managed WebViews."""
    safe_url = safe_http_url(url)
    if not safe_url:
        return None
    host = build_embedded_webview_host(safe_url, page=page)
    return host.control() if host is not None else None


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
        on_start_quiz: Optional[Callable[[CourseSummary, CourseModuleItem], Any]] = None,
        account_namespace: Optional[str] = None,
        webview_auth_service: Any = None,
        account_epoch_getter: Optional[Callable[[], int]] = None,
    ) -> None:
        self._page = page
        self._on_start_quiz = on_start_quiz
        self._account_namespace = account_namespace
        self._webview_auth_service = webview_auth_service
        self._account_epoch_getter = account_epoch_getter
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
        self._account_epoch = 0
        self._preview_register_lock = threading.RLock()

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
            on_course_loaded=lambda c, secs: self._schedule(
                self._prefetch_course_attachments_async, c, secs
            ),
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
        if not safe_url:
            return
        if is_demo_account(settings.UTH_USERNAME) and is_uth_lms_url(safe_url):
            self._show_snack(DEMO_LMS_WEBVIEW_GUARD_MESSAGE)
            return
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

    def reset_for_account_switch(self, namespace: Optional[str] = None) -> None:
        """Reset catalog load state, cache namespace, preview caches, and overlays on account change."""
        self._attachment_load_seq = getattr(self, "_attachment_load_seq", 0) + 1
        self._account_epoch = getattr(self, "_account_epoch", 0) + 1
        self._initial_loaded = False
        self._account_namespace = namespace
        effective_ns = namespace if namespace else "__logged_out__"
        cache_store = getattr(self.catalog_service, "_cache", None) or getattr(
            self.catalog_service, "_cache_store", None
        )
        set_ns = getattr(cache_store, "set_namespace", None)
        if callable(set_ns):
            set_ns(effective_ns)
        try:
            clear_converted_document_cache()
        except Exception:
            pass
        try:
            from gui.core.utils import clear_cached_description_images

            clear_cached_description_images()
        except Exception:
            pass
        preview_cache = getattr(self, "preview_cache", None)
        if preview_cache is not None:
            clear_pc = getattr(preview_cache, "clear", None)
            if callable(clear_pc):
                try:
                    clear_pc()
                except Exception:
                    pass
            cache_dir = getattr(preview_cache, "cache_dir", None)
            if isinstance(cache_dir, Path):
                try:
                    clear_rendered_pdf_cache(cache_dir)
                except Exception:
                    pass
                ext_dir = cache_dir / "external"
                if ext_dir.is_dir():
                    for entry in ext_dir.iterdir():
                        if entry.is_file():
                            try:
                                entry.unlink(missing_ok=True)
                            except OSError:
                                pass
        reset_srv = getattr(self.preview_server, "reset_for_account_switch", None)
        if callable(reset_srv):
            reg_lock = getattr(self, "_preview_register_lock", None)
            ctx = reg_lock if reg_lock is not None else contextlib.nullcontext()
            with ctx:
                try:
                    reset_srv()
                except Exception:
                    pass
        overlays = getattr(getattr(self, "courses_view", None), "_overlays", None)
        if isinstance(overlays, list):
            for ov in list(overlays):
                dispose_fn = getattr(ov, "dispose", None)
                if callable(dispose_fn) and _supports_kwarg(
                    dispose_fn, "clear_browser_storage"
                ):
                    try:
                        dispose_fn(clear_browser_storage=True)
                    except Exception:
                        pass
        if hasattr(self.courses_view, "reset_for_account_switch"):
            self.courses_view.reset_for_account_switch()

    def _resolve_account_epoch(self) -> int:
        getter = getattr(self, "_account_epoch_getter", None)
        if callable(getter):
            try:
                val = getter()
                if val is not None:
                    return int(val)
            except Exception:
                pass
        svc = getattr(self, "_webview_auth_service", None)
        svc_getter = getattr(svc, "_account_epoch_getter", None)
        if callable(svc_getter):
            try:
                val = svc_getter()
                if val is not None:
                    return int(val)
            except Exception:
                pass
        return int(getattr(self, "_account_epoch", 0))

    def _client_for(self, source: CourseSource) -> Any:
        return self._thnn_client if source == "thnn" else self._courses_client

    def _build_assignment_payload(
        self, course: CourseSummary, mod: CourseModuleItem
    ) -> Optional[dict[str, Any]]:
        url = safe_http_url(mod.web_url) or safe_http_url(mod.url)
        if not url:
            return None
        if classify_uth_webview_url(url) == "courses":
            url = normalize_uth_webview_target_url(url, has_credentials=False)
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
        start_epoch = getattr(self, "_account_epoch", 0)
        try:
            full_data = await asyncio.to_thread(fetcher, payload)
            if getattr(self, "_account_epoch", 0) != start_epoch:
                return
            if full_data and "details" in full_data:
                desc_html = full_data["details"].get("description_html", "")
                if desc_html:
                    from gui.core.utils import (
                        clear_cached_description_images,
                        pre_cache_description_images,
                    )

                    cached_html = await asyncio.to_thread(
                        pre_cache_description_images, desc_html
                    )
                    if getattr(self, "_account_epoch", 0) != start_epoch:
                        try:
                            clear_cached_description_images()
                        except Exception:
                            pass
                        return
                    full_data = dict(full_data)
                    full_data["details"] = dict(full_data["details"])
                    full_data["details"]["description_html"] = cached_html
            if getattr(self, "_account_epoch", 0) != start_epoch:
                return
            pane_assign.update_detail(full_data)
        except Exception:
            if getattr(self, "_account_epoch", 0) != start_epoch:
                return
            logger.exception("Failed to load Pane-3 assignment details")
            pane_assign.show_error_banner()

    def build_webview_host(
        self,
        url: str = "",
        *,
        html: Optional[str] = None,
        on_ready: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_message: Optional[Callable[[str], None]] = None,
        on_url_changed: Optional[Callable[[str], None]] = None,
        on_title_changed: Optional[Callable[[str], None]] = None,
        on_loading_changed: Optional[Callable[[bool], None]] = None,
        on_history_changed: Optional[Callable[[bool, bool], None]] = None,
    ) -> Optional[EmbeddedWebViewHost]:
        """Build a cross-platform EmbeddedWebViewHost bound to the active Flet Page."""
        return build_embedded_webview_host(
            url,
            html=html,
            page=self._page,
            bgcolor=C.BG,
            account_namespace=self._account_namespace,
            on_ready=on_ready,
            on_error=on_error,
            on_message=on_message,
            on_url_changed=on_url_changed,
            on_title_changed=on_title_changed,
            on_loading_changed=on_loading_changed,
            on_history_changed=on_history_changed,
        )

    def ensure_preview_server(self) -> LocalPreviewServer:
        """Return the active LocalPreviewServer, ensuring Moodle session providers are registered."""
        register_fn = getattr(self.preview_server, "register_session_provider", None)
        if callable(register_fn):
            reg_lock = getattr(self, "_preview_register_lock", None)
            ctx = reg_lock if reg_lock is not None else contextlib.nullcontext()
            with ctx:
                providers = getattr(self.preview_server, "_session_providers", None)
                if self._courses_client is not None and (
                    not isinstance(providers, dict) or "courses" not in providers
                ):
                    register_fn("courses", self._courses_client)
                if self._thnn_client is not None and (
                    not isinstance(providers, dict) or "thnn" not in providers
                ):
                    register_fn("thnn", self._thnn_client)
        return self.preview_server

    def _has_webview_credentials(self, site_kind: str) -> bool:
        if is_demo_account(settings.UTH_USERNAME):
            return False
        svc = getattr(self, "_webview_auth_service", None)
        if svc is not None:
            public_has_fn = getattr(svc, "has_credentials", None)
            if callable(public_has_fn):
                try:
                    return bool(public_has_fn(site_kind))
                except TypeError:
                    return bool(public_has_fn())
            client_for_site = getattr(svc, "client_for_site", None)
            if callable(client_for_site) and client_for_site(site_kind) is None:
                return False
            return True
        if site_kind == "thnn":
            return getattr(self, "_thnn_client", None) is not None
        if site_kind == "courses":
            return getattr(self, "_courses_client", None) is not None
        return False

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
        if is_demo_account(settings.UTH_USERNAME) and is_uth_lms_url(safe_url):
            self._show_snack(DEMO_LMS_WEBVIEW_GUARD_MESSAGE)
            return
        site_kind = classify_uth_webview_url(safe_url)
        resolved_source: CourseSource = (
            "thnn"
            if site_kind == "thnn"
            else ("courses" if site_kind == "courses" else source)
        )
        if site_kind in {"courses", "thnn", "portal"}:
            safe_url = normalize_uth_webview_target_url(
                safe_url,
                has_credentials=self._has_webview_credentials(site_kind),
            )
        initial_pane_w: Optional[float] = None
        bounds_fn = getattr(self.courses_view, "_compute_pane3_bounds", None)
        if callable(bounds_fn):
            try:
                initial_pane_w = float(bounds_fn()[2])
            except Exception:
                initial_pane_w = None
        pane_kwargs: dict[str, Any] = {
            "page": getattr(self, "_page", None),
            "source": resolved_source,
            "title": title or "Trình duyệt trong ứng dụng",
            "url": safe_url,
            "preview_server": getattr(self, "preview_server", None),
            "session_client": self._client_for(resolved_source),
            "webview_auth_service": getattr(self, "_webview_auth_service", None),
            "on_back": self.courses_view.pop_overlay,
            "on_open_external": self._launch_url_safe,
            "on_toggle_expand": self.courses_view.toggle_pane3_expanded,
            "pane_width": initial_pane_w,
            "account_namespace": getattr(self, "_account_namespace", None),
        }
        if _supports_kwarg(CoursesWebViewPane, "account_epoch_getter"):
            pane_kwargs["account_epoch_getter"] = self._resolve_account_epoch
        pane = CoursesWebViewPane(**pane_kwargs)
        self.courses_view.push_overlay(pane, tag=tag or f"webview:{abs(hash(safe_url))}")

    def _handle_course_url_action(self, url: str, title: str = "") -> None:
        safe_url = safe_http_url(url)
        if not safe_url:
            return
        selected = None
        if hasattr(self.courses_view, "_state"):
            selected = self.courses_view._state.selected_course()
        site_kind = classify_uth_webview_url(safe_url)
        if site_kind == "thnn":
            source: CourseSource = "thnn"
        elif site_kind == "courses":
            source = "courses"
        else:
            source = selected.source if selected is not None else "courses"
        if site_kind in {"courses", "thnn", "portal"}:
            safe_url = normalize_uth_webview_target_url(
                safe_url,
                has_credentials=self._has_webview_credentials(site_kind),
            )
        pane_title = str(title or "").strip() or (
            (selected.display_name or selected.fullname) if selected is not None else "Moodle Web"
        )
        self.open_in_app_webview(
            source,
            pane_title,
            safe_url,
        )

    def _handle_open_webview_activity(
        self, course: CourseSummary, mod: CourseModuleItem
    ) -> None:
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
            status_cb = self._on_assignment_status_changed
            opened_epoch = self._resolve_account_epoch()

            def _forward_quiz_status_with_epoch(
                quiz_url: str,
                new_status: str,
                _cb: Optional[Callable[..., Any]] = status_cb,
                _epoch: int = opened_epoch,
            ) -> None:
                if _cb is None or self._resolve_account_epoch() != _epoch:
                    return
                try:
                    _cb(quiz_url, new_status, _epoch)
                except TypeError:
                    _cb(quiz_url, new_status)

            view = QuizActivityView(
                source=course.source,
                course_id=course.id,
                module=mod,
                activity_service=self._activity_service,
                on_close=self.courses_view.pop_overlay,
                on_open_webview=lambda t, u, s=course.source, m=mod: self.open_in_app_webview(
                    s, t, u, tag=f"webview:quiz:{m.cmid}"
                ),
                on_start_quiz=(lambda: self._dispatch_cb(self._on_start_quiz, course, mod)) if self._on_start_quiz is not None else None,
                on_status_changed=(
                    _forward_quiz_status_with_epoch
                    if status_cb is not None
                    else None
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

        if modname in {"book", "page", "label"}:
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

    def _build_embedded_webview_for_page(self, url: str) -> Optional[ft.Control]:
        if "page" in inspect.signature(_try_build_embedded_webview).parameters:
            return _try_build_embedded_webview(url, page=self._page)
        return _try_build_embedded_webview(url)

    def _open_webview_activity(self, mod: CourseModuleItem) -> None:
        safe_url = safe_http_url(mod.web_url)
        if not safe_url:
            if not mod.web_url:
                safe_url = safe_http_url(mod.url)
            if not safe_url:
                return

        site_kind = classify_uth_webview_url(safe_url)
        source: CourseSource = "thnn" if site_kind == "thnn" else "courses"
        if site_kind in {"courses", "thnn", "portal"}:
            safe_url = normalize_uth_webview_target_url(
                safe_url,
                has_credentials=self._has_webview_credentials(site_kind),
            )
        self.open_in_app_webview(source, mod.name, safe_url, tag=f"webview:{mod.cmid}")

    async def _prefetch_course_attachments_async(
        self, course: CourseSummary, sections: list[CourseSection]
    ) -> None:
        """Concurrently prefetch up to 4 uncached previewable documents (<= 5 MiB) in background."""
        if course.source != "courses":
            return
        fetch_cache_fn = getattr(self.catalog_service, "fetch_or_cache_preview_file", None)
        if not callable(fetch_cache_fn):
            return
        candidates: list[tuple[int, CourseAttachment]] = []
        for sec in sections:
            for mod in sec.modules:
                for att in mod.attachments:
                    if att.file_name.strip().lower() in {"index.html", "index.htm", "structure"}:
                        continue
                    if (
                        att.preview_kind in {"pdf", "docx", "pptx", "xlsx", "csv", "text"}
                        and 0 < int(att.file_size or 0) <= 5 * 1024 * 1024
                        and att.file_url
                    ):
                        prio = 0 if mod.is_new else 1
                        candidates.append((prio, att))
        if not candidates:
            return
        candidates.sort(key=lambda item: (item[0], item[1].file_size))
        to_fetch: list[CourseAttachment] = []
        for _, att in candidates:
            if len(to_fetch) >= 4:
                break
            if getattr(self.courses_view._state, "selected_course_key", None) != course.course_key:
                return
            try:
                existing = await asyncio.to_thread(
                    self.preview_cache.get_cached_path,
                    att.file_url,
                    att.time_modified,
                    att.file_name,
                )
                if existing is not None:
                    continue
                to_fetch.append(att)
            except Exception:
                logger.debug("Idle attachment prefetch skipped for %s", att.file_name)

        if not to_fetch:
            return
        await asyncio.sleep(0.05)
        if getattr(self.courses_view._state, "selected_course_key", None) != course.course_key:
            return

        is_mobile = bool(platform_utils.IS_ANDROID or platform_utils.IS_IOS)
        sem = asyncio.Semaphore(2 if is_mobile else 3)

        async def _prefetch_one(att: CourseAttachment) -> None:
            async with sem:
                if getattr(self.courses_view._state, "selected_course_key", None) != course.course_key:
                    return
                try:
                    await asyncio.to_thread(
                        fetch_cache_fn,
                        "courses",
                        att.file_url,
                        att.time_modified,
                        att.file_name,
                    )
                except Exception:
                    logger.debug("Idle attachment prefetch skipped for %s", att.file_name)

        await asyncio.gather(*(_prefetch_one(att) for att in to_fetch), return_exceptions=True)

    async def _fetch_attachment_payload_async(
        self,
        source: str,
        att: CourseAttachment,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_commit: Optional[Callable[[], bool]] = None,
    ) -> tuple[bytes, Optional[Path]]:
        try:
            cached_path = await asyncio.to_thread(
                self.preview_cache.get_cached_path,
                att.file_url,
                att.time_modified,
                att.file_name,
            )
            if cached_path is not None and cached_path.is_file():
                data_cached = await asyncio.to_thread(cached_path.read_bytes)
                if callable(progress_callback):
                    try:
                        progress_callback(len(data_cached), len(data_cached))
                    except Exception:
                        pass
                return data_cached, cached_path
        except OSError as exc:
            logger.debug("Preview cache read failed for %s: %s", att.file_name, exc)

        def _invoke_fetch() -> bytes:
            if progress_callback is not None:
                try:
                    return self.catalog_service.fetch_attachment_bytes(
                        source,  # type: ignore[arg-type]
                        att.file_url,
                        progress_callback=progress_callback,
                    )
                except TypeError:
                    pass
            return self.catalog_service.fetch_attachment_bytes(
                source,  # type: ignore[arg-type]
                att.file_url,
            )

        raw = await asyncio.to_thread(_invoke_fetch)
        if should_commit is not None and not should_commit():
            return raw, None
        saved_path: Optional[Path] = None
        try:
            put_bytes = self.preview_cache.put_bytes
            if should_commit is not None and _supports_kwarg(put_bytes, "should_commit"):
                saved_path = await asyncio.to_thread(
                    put_bytes,
                    att.file_url,
                    att.time_modified,
                    att.file_name,
                    raw,
                    should_commit=should_commit,
                )
            else:
                saved_path = await asyncio.to_thread(
                    put_bytes,
                    att.file_url,
                    att.time_modified,
                    att.file_name,
                    raw,
                )
        except OSError as exc:
            logger.warning(
                "Failed to persist preview cache for %s: %s", att.file_name, exc
            )
        return raw, saved_path

    async def _fetch_attachment_bytes_async(
        self,
        source: str,
        att: CourseAttachment,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bytes:
        start_epoch = getattr(self, "_account_epoch", 0)
        if _supports_kwarg(self._fetch_attachment_payload_async, "should_commit"):
            raw, _ = await self._fetch_attachment_payload_async(
                source,
                att,
                progress_callback=progress_callback,
                should_commit=lambda: getattr(self, "_account_epoch", 0) == start_epoch,
            )
            return raw
        raw, _ = await self._fetch_attachment_payload_async(
            source, att, progress_callback=progress_callback
        )
        return raw

    def open_attachment_bytes_in_overlay(
        self,
        course: CourseSummary,
        mod: CourseModuleItem,
        att: CourseAttachment,
        raw_bytes: bytes,
        *,
        preconverted: Optional[ConvertedDocument] = None,
        preopened_pdf: Optional[PdfDocumentSession] = None,
        rendered_pdf_bytes: Optional[bytes] = None,
        preprepared_office_viewer: Optional[OfficeViewerDescriptor] = None,
        cached_file_path: Optional[Path] = None,
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
        self._open_entry_reader(
            att.file_name,
            raw_bytes,
            att.preview_kind,
            preconverted=preconverted,
            preopened_pdf=preopened_pdf,
            rendered_pdf_bytes=rendered_pdf_bytes,
            preprepared_office_viewer=preprepared_office_viewer,
            cached_file_path=cached_file_path,
        )

    def _open_entry_reader(
        self,
        name: str,
        data: bytes,
        kind: PreviewKind,
        *,
        preconverted: Optional[ConvertedDocument] = None,
        preopened_pdf: Optional[PdfDocumentSession] = None,
        rendered_pdf_bytes: Optional[bytes] = None,
        preprepared_office_viewer: Optional[OfficeViewerDescriptor] = None,
        cached_file_path: Optional[Path] = None,
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
        initial_pane_w = 700.0
        bounds_fn = getattr(self.courses_view, "_compute_pane3_bounds", None)
        if callable(bounds_fn):
            try:
                initial_pane_w = float(bounds_fn()[2])
            except Exception:
                initial_pane_w = 700.0
        if (
            (kind == "pdf" or rendered_pdf_bytes is not None)
            and preprepared_office_viewer is None
            and is_embedded_webview_supported(self._page)
        ):
            reg_lock = getattr(self, "_preview_register_lock", None)
            ctx = reg_lock if reg_lock is not None else contextlib.nullcontext()
            pdf_payload = rendered_pdf_bytes if rendered_pdf_bytes is not None else data
            entry_pdf_path = (
                cached_file_path
                if (rendered_pdf_bytes is None and kind == "pdf")
                else None
            )
            with ctx:
                preprepared_office_viewer = prepare_pdf_viewer(
                    pdf_payload,
                    self.preview_server,
                    theme="light",
                    file_path=entry_pdf_path,
                )
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
            webview_builder=self.build_webview_host,
            pdf_cache_dir=getattr(self.preview_cache, "cache_dir", None),
            preconverted=preconverted,
            preopened_pdf=preopened_pdf,
            rendered_pdf_bytes=rendered_pdf_bytes,
            pane_width=initial_pane_w,
            pdf_in_webview=False,
            on_toggle_expand=self.courses_view.toggle_pane3_expanded,
            preprepared_office_viewer=preprepared_office_viewer,
            cached_file_path=cached_file_path,
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
        """Show live progress in Pane 3, fetch attachment, pre-render off-thread, and open viewer."""
        self._attachment_load_seq = getattr(self, "_attachment_load_seq", 0) + 1
        req_seq = self._attachment_load_seq
        start_epoch = getattr(self, "_account_epoch", 0)
        raw_size = getattr(att, "file_size", 0)
        expected_total = (
            max(0, int(raw_size))
            if isinstance(raw_size, (int, float)) and not isinstance(raw_size, bool)
            else 0
        )
        size_hint = format_file_size(expected_total)
        subtitle = f"Đang tải xuống · {size_hint}" if size_hint else "Đang tải tài liệu từ Moodle..."
        loading_overlay = PaneLoadingOverlay(
            title=att.file_name or mod.name or "Tài liệu",
            subtitle=subtitle,
            status_message=f"Đang tải «{att.file_name}» vào bộ nhớ đệm...",
            preset="archive" if (att.is_archive or att.preview_kind == "archive") else "document",
            on_back=self.courses_view.pop_overlay,
            on_toggle_expand=self.courses_view.toggle_pane3_expanded,
            page=self._page,
        )
        self.courses_view.push_overlay(loading_overlay, tag=f"viewer:load:{req_seq}")
        preopened_pdf: Optional[PdfDocumentSession] = None

        def _is_still_current() -> bool:
            return (
                req_seq == self._attachment_load_seq
                and not loading_overlay._disposed
                and getattr(self, "_account_epoch", 0) == start_epoch
            )

        def _should_commit_seq() -> bool:
            return (
                req_seq == self._attachment_load_seq
                and getattr(self, "_account_epoch", 0) == start_epoch
            )

        try:
            await asyncio.sleep(0)
            t0 = time.monotonic()

            def _on_dl_progress(done_b: int, total_b: int) -> None:
                if not _is_still_current():
                    return
                eff_total = total_b if total_b > 0 else expected_total
                elapsed = max(0.001, time.monotonic() - t0)
                speed_bps = done_b / elapsed if elapsed >= 0.08 else 0.0
                speed_txt = f" · {format_file_size(int(speed_bps))}/s" if speed_bps >= 32768 else ""
                done_txt = format_file_size(done_b) or f"{done_b} B"
                if eff_total > 0 and done_b <= eff_total:
                    frac = done_b / eff_total
                    pct = int(round(frac * 100))
                    tot_txt = format_file_size(eff_total)
                    loading_overlay.set_progress(
                        frac,
                        message=f"Đang tải «{att.file_name}» ({pct}%)...",
                        subtitle=f"{done_txt} / {tot_txt}{speed_txt}",
                    )
                else:
                    loading_overlay.set_progress(
                        None,
                        message=f"Đang tải «{att.file_name}» ({done_txt})...",
                        subtitle=f"Đã nhận {done_txt}{speed_txt}",
                    )

            if _supports_kwarg(self._fetch_attachment_payload_async, "should_commit"):
                raw, cached_file_path = await self._fetch_attachment_payload_async(
                    course.source,
                    att,
                    progress_callback=_on_dl_progress,
                    should_commit=_should_commit_seq,
                )
            else:
                raw, cached_file_path = await self._fetch_attachment_payload_async(
                    course.source, att, progress_callback=_on_dl_progress
                )
            if not _is_still_current():
                return
            loading_overlay.set_progress(
                1.0,
                message=f"Đang dựng bố cục hiển thị cho «{att.file_name}»...",
                subtitle=f"Hoàn tất tải · {format_file_size(len(raw)) or 'Moodle'}",
            )

            preconverted: Optional[ConvertedDocument] = None
            rendered_pdf_bytes: Optional[bytes] = None
            preprepared_office_viewer: Optional[OfficeViewerDescriptor] = None
            plat_val = getattr(self._page, "platform", None)
            is_windows_desktop = (
                (plat_val == ft.PagePlatform.WINDOWS or str(plat_val or "").lower() == "windows")
                and not bool(getattr(self._page, "web", False))
            )
            webview_supported = is_embedded_webview_supported(self._page)
            if not _is_still_current():
                return
            reg_lock = getattr(self, "_preview_register_lock", None)
            if not (att.is_archive or att.preview_kind == "archive"):
                if att.preview_kind in {"docx", "pptx"}:
                    if is_windows_desktop:
                        get_cached_pdf = getattr(
                            self.preview_cache, "get_cached_converted_pdf", None
                        )
                        put_cached_pdf = getattr(
                            self.preview_cache, "put_converted_pdf", None
                        )
                        if callable(get_cached_pdf):
                            rendered_pdf_bytes = await asyncio.to_thread(
                                get_cached_pdf, raw, att.file_name
                            )
                            if not _is_still_current():
                                return
                        if rendered_pdf_bytes is None:
                            rendered_pdf_bytes = await asyncio.to_thread(
                                render_office_pdf, raw, att.file_name
                            )
                            if not _is_still_current():
                                return
                            if rendered_pdf_bytes is not None and callable(put_cached_pdf):
                                if _supports_kwarg(put_cached_pdf, "should_commit"):
                                    await asyncio.to_thread(
                                        put_cached_pdf,
                                        raw,
                                        att.file_name,
                                        rendered_pdf_bytes,
                                        should_commit=_should_commit_seq,
                                    )
                                else:
                                    await asyncio.to_thread(
                                        put_cached_pdf,
                                        raw,
                                        att.file_name,
                                        rendered_pdf_bytes,
                                    )
                                if not _is_still_current():
                                    return
                    if rendered_pdf_bytes is not None:
                        if webview_supported:
                            if not _is_still_current():
                                return

                            def _prepare_rendered_pdf() -> Optional[OfficeViewerDescriptor]:
                                ctx = (
                                    reg_lock
                                    if reg_lock is not None
                                    else contextlib.nullcontext()
                                )
                                with ctx:
                                    if not _is_still_current():
                                        return None
                                    return prepare_pdf_viewer(
                                        rendered_pdf_bytes,
                                        self.preview_server,
                                        theme="light",
                                    )

                            preprepared_office_viewer = await asyncio.to_thread(
                                _prepare_rendered_pdf
                            )
                            if not _is_still_current():
                                return
                        if preprepared_office_viewer is None:
                            if not _is_still_current():
                                return
                            preopened_pdf = await asyncio.to_thread(
                                PdfDocumentSession,
                                rendered_pdf_bytes,
                                cache_dir=getattr(self.preview_cache, "cache_dir", None),
                            )
                    else:
                        is_ooxml_office = att.file_name.lower().endswith(
                            (".docx", ".pptx")
                        )
                        if is_ooxml_office and webview_supported:
                            if not _is_still_current():
                                return

                            def _prepare_office() -> Optional[OfficeViewerDescriptor]:
                                ctx = (
                                    reg_lock
                                    if reg_lock is not None
                                    else contextlib.nullcontext()
                                )
                                with ctx:
                                    if not _is_still_current():
                                        return None
                                    if (
                                        cached_file_path is not None
                                        and cached_file_path.is_file()
                                    ):
                                        desc = prepare_office_viewer_from_file(
                                            cached_file_path,
                                            att.file_name,
                                            self.preview_server,
                                            theme="light",
                                        )
                                        if desc is not None:
                                            return desc
                                    return prepare_office_viewer(
                                        raw,
                                        att.file_name,
                                        self.preview_server,
                                        theme="light",
                                    )

                            preprepared_office_viewer = await asyncio.to_thread(
                                _prepare_office
                            )
                            if not _is_still_current():
                                return
                        if preprepared_office_viewer is None:
                            if not _is_still_current():
                                return
                            preconverted = await asyncio.to_thread(
                                convert_document_bytes,
                                att.file_name,
                                raw,
                                att.preview_kind,
                                dark_mode=False,
                            )
                            if getattr(self, "_account_epoch", 0) != start_epoch:
                                try:
                                    clear_converted_document_cache()
                                except Exception:
                                    pass
                                return
                elif att.preview_kind in {"xlsx", "csv", "text"}:
                    if not _is_still_current():
                        return
                    preconverted = await asyncio.to_thread(
                        convert_document_bytes,
                        att.file_name,
                        raw,
                        att.preview_kind,
                        dark_mode=False,
                    )
                    if getattr(self, "_account_epoch", 0) != start_epoch:
                        try:
                            clear_converted_document_cache()
                        except Exception:
                            pass
                        return
                elif att.preview_kind == "pdf":
                    if webview_supported:
                        if not _is_still_current():
                            return

                        def _prepare_pdf() -> Optional[OfficeViewerDescriptor]:
                            ctx = (
                                reg_lock
                                if reg_lock is not None
                                else contextlib.nullcontext()
                            )
                            with ctx:
                                if not _is_still_current():
                                    return None
                                return prepare_pdf_viewer(
                                    raw,
                                    self.preview_server,
                                    theme="light",
                                    file_path=cached_file_path,
                                )

                        preprepared_office_viewer = await asyncio.to_thread(
                            _prepare_pdf
                        )
                        if not _is_still_current():
                            return
                    if preprepared_office_viewer is None:
                        if not _is_still_current():
                            return
                        pdf_dir = getattr(self.preview_cache, "cache_dir", None)

                        def _warm_pdf() -> Optional[PdfDocumentSession]:
                            if not _is_still_current():
                                return None
                            sess = PdfDocumentSession(raw, cache_dir=pdf_dir)
                            if not _is_still_current():
                                sess.close()
                                return None
                            pcount = getattr(sess, "page_count", 0)
                            if (
                                bool(getattr(sess, "supported", False))
                                and bool(getattr(sess, "can_render_images", False))
                                and isinstance(pcount, int)
                                and not isinstance(pcount, bool)
                                and pcount > 0
                            ):
                                sess.render_page_png(0, scale=1.6, dark_mode=False)
                                if getattr(self, "_account_epoch", 0) != start_epoch:
                                    doc_hash = getattr(sess, "doc_hash", "")
                                    sess.close()
                                    if isinstance(pdf_dir, Path) and doc_hash:
                                        rdir = pdf_dir / "rendered_pdf"
                                        if rdir.is_dir():
                                            for entry in rdir.glob(f"{doc_hash}_*"):
                                                try:
                                                    entry.unlink(missing_ok=True)
                                                except OSError:
                                                    pass
                                    return None
                            return sess

                        preopened_pdf = await asyncio.to_thread(_warm_pdf)

            if not _is_still_current():
                if preopened_pdf is not None:
                    preopened_pdf.close()
                return
            self._dismiss_overlay_instance(loading_overlay)
            self.open_attachment_bytes_in_overlay(
                course,
                mod,
                att,
                raw,
                preconverted=preconverted,
                preopened_pdf=preopened_pdf,
                rendered_pdf_bytes=rendered_pdf_bytes,
                preprepared_office_viewer=preprepared_office_viewer,
                cached_file_path=cached_file_path,
            )
            preopened_pdf = None
        except Exception as exc:
            if preopened_pdf is not None:
                preopened_pdf.close()
            was_current = _is_still_current()
            logger.warning("Open attachment failed for %s: %s", att.file_name, exc)
            self._dismiss_overlay_instance(loading_overlay)
            if not was_current:
                return
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
        start_epoch = getattr(self, "_account_epoch", 0)
        self._show_snack(f"Đang tải xuống «{att.file_name}»...")
        try:
            raw = await self._fetch_attachment_bytes_async(source, att)
            if getattr(self, "_account_epoch", 0) != start_epoch:
                return
            await self._save_bytes_async(att.file_name, raw)
        except Exception as exc:
            if getattr(self, "_account_epoch", 0) != start_epoch:
                return
            logger.warning("Save attachment failed for %s: %s", att.file_name, exc)
            self._show_snack(f"Lỗi tải file «{att.file_name}»: {exc}")

    def _should_use_mobile_file_save(self) -> bool:
        if platform_utils.IS_ANDROID or platform_utils.IS_IOS:
            return True
        if bool(getattr(self._page, "web", False)):
            return True
        plat = getattr(self._page, "platform", None)
        if plat in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS):
            return True
        plat_str = str(plat or "").lower()
        return "android" in plat_str or "ios" in plat_str

    async def _save_bytes_async(self, file_name: str, data: bytes) -> None:
        safe_name = Path(file_name or "document").name.strip() or "document"
        if not data and self._should_use_mobile_file_save():
            self._show_snack(f"Không thể lưu {safe_name}: tệp trống.")
            return
        try:
            saved = await ft.FilePicker().save_file(
                dialog_title="Lưu tài liệu",
                file_name=safe_name,
                src_bytes=data,
            )
        except Exception as exc:
            logger.warning("Save file failed for %s: %s", safe_name, exc)
            self._show_snack(f"Không thể lưu {safe_name}: {exc}")
            return
        if saved:
            self._show_snack(f"Đã lưu: {safe_name}")

    @staticmethod
    def _write_external_file_with_motw(out: Path, data: bytes) -> None:
        out.write_bytes(data)
        if os.name == "nt" or platform_utils.IS_WINDOWS:
            try:
                with open(f"{out}:Zone.Identifier", "wb") as ads:
                    ads.write(b"[ZoneTransfer]\r\nZoneId=3\r\n")
            except OSError:
                pass

    async def _open_external_async(self, file_name: str, data: bytes) -> None:
        raw_input = str(file_name or "").strip()
        effective_input = raw_input if raw_input else "document.txt"
        raw_leaf = Path(effective_input).name.strip()
        stripped_leaf = raw_leaf.rstrip(". ")
        if not stripped_leaf:
            stripped_leaf = "document.txt"
            raw_leaf = "document.txt"
        raw_ext = Path(raw_leaf).suffix.lower()
        clean_ext = Path(stripped_leaf).suffix.lower()
        if (
            not clean_ext
            or raw_ext != clean_ext
            or clean_ext not in _SAFE_EXTERNAL_OPEN_EXTS
        ):
            await self._save_bytes_async(file_name, data)
            return

        safe_leaf = _SAFE_TMP_RE.sub("_", stripped_leaf).strip("._") or f"document{clean_ext}"
        stem = Path(safe_leaf).stem or "document"
        ext = Path(safe_leaf).suffix.lower()
        if ext not in _SAFE_EXTERNAL_OPEN_EXTS:
            await self._save_bytes_async(file_name, data)
            return

        if self._should_use_mobile_file_save():
            await self._save_bytes_async(safe_leaf, data)
            return

        start_epoch = getattr(self, "_account_epoch", 0)
        unique_name = f"{stem}_{uuid.uuid4().hex[:8]}{ext}"
        out: Optional[Path] = None
        try:
            if getattr(self, "_account_epoch", 0) != start_epoch:
                return
            tmp_dir = self.preview_cache.cache_dir / "external"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            out = tmp_dir / unique_name

            def _write_if_current() -> bool:
                if getattr(self, "_account_epoch", 0) != start_epoch:
                    return False
                self._write_external_file_with_motw(out, data)
                if getattr(self, "_account_epoch", 0) != start_epoch:
                    try:
                        out.unlink(missing_ok=True)
                    except OSError:
                        pass
                    return False
                return True

            wrote = await asyncio.to_thread(_write_if_current)
            if not wrote or getattr(self, "_account_epoch", 0) != start_epoch:
                try:
                    out.unlink(missing_ok=True)
                except OSError:
                    pass
                return
            await ft.UrlLauncher().launch_url(out.as_uri())
        except Exception:
            if getattr(self, "_account_epoch", 0) != start_epoch:
                if out is not None:
                    try:
                        out.unlink(missing_ok=True)
                    except OSError:
                        pass
                return
            await self._save_bytes_async(file_name, data)

    def _show_snack(self, text: str) -> None:
        sb = ft.SnackBar(
            content=ft.Text(text, size=13, color=C.TEXT_PRIMARY),
            bgcolor=C.SURFACE,
            duration=3000,
        )
        overlay = getattr(self._page, "overlay", None)
        if isinstance(overlay, list):
            overlay[:] = [item for item in overlay if not isinstance(item, ft.SnackBar)]
            overlay.append(sb)
            sb.open = True
            update = getattr(self._page, "update", None)
            if callable(update):
                try:
                    update()
                except Exception:
                    pass
            return
        show_dlg = getattr(self._page, "show_dialog", None)
        if callable(show_dlg):
            try:
                show_dlg(sb)
            except (AttributeError, TypeError):
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
