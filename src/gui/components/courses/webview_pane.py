"""Authenticated in-app WebView pane (Windows WebView2 + flet-webview + HTML reader fallback)."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
from typing import Any, Callable, Optional
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import flet as ft
from bs4 import BeautifulSoup

from core.courses.models import CourseSource
from core.html_compat import BS4_PARSER
from core.security import HTMLSanitizer
from core.webview_auth import (
    UthWebViewAuthService,
    WebViewAuthBootstrap,
    WebViewRecoveryTracker,
    classify_uth_webview_url,
    is_uth_login_url,
    normalize_uth_webview_target_url,
)
from gui.components.courses._shared import (
    build_viewer_header,
    refresh_viewer_header,
    safe_control_update,
)
from gui.components.courses.embedded_webview_host import (
    EmbeddedWebViewHost,
    WindowsWebView2Dock,
    _is_flet_webview_platform,
    _safe_control_page,
    _webview2_runtime_available,
    build_embedded_webview_host,
)
from gui.core.theme import C
from gui.view_models.courses_state import safe_http_url
from platform_utils import UI_IS_MOBILE

logger = logging.getLogger(__name__)

__all__ = [
    "CoursesWebViewPane",
    "WindowsWebView2Dock",
    "_is_flet_webview_platform",
    "_is_loopback_url",
    "_webview2_runtime_available",
    "is_external_meeting_url",
]

_EXTERNAL_MEETING_HOSTS = (
    "meet.google.com",
    "zoom.us",
    "teams.microsoft.com",
    "zalo.me",
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_NON_READABLE_HTML_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "head",
    "title",
    "meta",
    "link",
    "iframe",
    "object",
    "embed",
)


def is_external_meeting_url(url: str) -> bool:
    """Return True only for external video/chat tools better opened in native apps."""
    host = (urlsplit(str(url or "").strip()).hostname or "").lower()
    if not host:
        return False
    return any(host == m or host.endswith(f".{m}") for m in _EXTERNAL_MEETING_HOSTS)


def _is_loopback_url(url: str) -> bool:
    """Return True for ephemeral local loopback URLs that should not open externally."""
    try:
        host = (urlsplit(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOOPBACK_HOSTS


def _extract_bridged_uth_url(url: str) -> str:
    """Map a local loopback ``/session/<token>/<site>/<path>`` URL back to its canonical HTTPS UTH URL."""
    if not _is_loopback_url(url):
        return ""
    try:
        parsed = urlsplit(str(url or ""))
    except ValueError:
        return ""
    if parsed.scheme.lower() != "http":
        return ""
    parts = [seg for seg in parsed.path.split("/") if seg]
    if len(parts) >= 3 and parts[0] == "session" and parts[2] in ("courses", "thnn"):
        site = parts[2]
        rest = "/".join(parts[3:])
        query = f"?{parsed.query}" if parsed.query else ""
        return f"https://{site}.ut.edu.vn/{rest}{query}"
    return ""


def _call_with_supported_kwargs(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke ``fn`` passing only kwargs it accepts (preserves test double compatibility)."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(*args, **kwargs)
    filtered = {
        key: val
        for key, val in kwargs.items()
        if key in params
        and params[key].kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    return fn(*args, **filtered)


def _build_flet_portal_eval_script(portal_init_script: str, target_url: str) -> str:
    """Wrap an origin-gated Portal localStorage script with a loop-safe reload guard."""
    script = str(portal_init_script or "").strip()
    if not script:
        return ""
    safe_target = normalize_uth_webview_target_url(target_url, has_credentials=True)
    if classify_uth_webview_url(safe_target) != "portal":
        safe_target = "https://portal.ut.edu.vn/dashboard"
    target_json = (
        json.dumps(safe_target)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return (
        "(function () {"
        "try {"
        'if (!window.location || window.location.origin !== "https://portal.ut.edu.vn") { return; }'
        'var __before = window.localStorage.getItem("account");'
        f"{script}"
        'var __after = window.localStorage.getItem("account");'
        "if (__after && __after !== __before) {"
        f"window.location.replace({target_json});"
        "}"
        "} catch (_e) {}"
        "})();"
    )


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Pin fallback loopback fetches to the originating active preview origin."""

    def __init__(
        self,
        *,
        expected_scheme: str,
        expected_netloc: str,
        origin_validator: Callable[[str], bool],
    ) -> None:
        super().__init__()
        self._expected_scheme = expected_scheme.lower()
        self._expected_netloc = expected_netloc.lower()
        self._origin_validator = origin_validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = str(newurl or "").strip()
        try:
            parsed = urlsplit(target)
        except ValueError as exc:
            raise urllib.error.URLError("Invalid redirect URL") from exc
        if (
            parsed.scheme.lower() != self._expected_scheme
            or parsed.netloc.lower() != self._expected_netloc
            or not self._origin_validator(target)
        ):
            raise urllib.error.URLError(
                "Blocked redirect outside active preview origin"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class CoursesWebViewPane(ft.Container):
    """Pane-3 interactive authenticated browser workspace for Moodle activities & pages."""

    def __init__(
        self,
        *,
        page: Optional[ft.Page],
        source: CourseSource,
        title: str,
        url: str,
        preview_server: Any = None,
        session_client: Any = None,
        webview_auth_service: Any = None,
        on_back: Callable[[], None],
        on_open_external: Callable[[str], None],
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
        embedded_webview_factory: Optional[Callable[[str], Optional[ft.Control]]] = None,
        pane_width: Optional[float] = None,
        account_namespace: Optional[str] = None,
    ) -> None:
        super().__init__(expand=True, bgcolor=C.BG)
        self._page = page
        self._source = source
        self._title = title or "Trình duyệt học tập"
        self._preview_server = preview_server
        self._session_client = session_client
        self._webview_auth_service = webview_auth_service
        self._recovery_tracker = WebViewRecoveryTracker()
        self._recovery_lock = threading.Lock()
        self._recovery_in_flight = False
        self._recovery_awaiting_commit: set[str] = set()
        self._portal_rearm_grace_seconds: float = 5.0
        self._portal_recovery_pending_rearm = False
        self._portal_rearm_generation: int = 0
        self._portal_rearm_timer: Optional[threading.Timer] = None
        self._portal_mobile_lock = threading.Lock()
        self._portal_mobile_bootstrap_started = False
        self._flet_portal_bootstrap_in_flight = False
        self._deferred_portal_login_url = ""
        self._portal_mobile_script_applied = False
        self._flet_page_loaded = False
        self._pending_flet_portal_script = ""
        self._last_rewritten_bridge_target = ""

        raw_url = str(url or "")
        initial_site = classify_uth_webview_url(raw_url)
        if initial_site in {"courses", "thnn", "portal"}:
            self._url = normalize_uth_webview_target_url(
                raw_url,
                has_credentials=self._has_auth_credentials(initial_site),
            )
            self._recovery_tracker.record_navigation(self._url)
        else:
            self._url = raw_url
        self._current_url = self._url

        self._on_back = on_back
        self._on_open_external = on_open_external
        self._expanded = bool(is_expanded)
        self._show_expand_toggle = bool(show_expand_toggle)
        self._on_toggle_expand = on_toggle_expand
        self._embedded_factory = embedded_webview_factory
        self._account_namespace = account_namespace

        if pane_width is not None:
            resolved_width = float(pane_width)
        elif page is not None and getattr(page, "width", None):
            resolved_width = float(getattr(page, "width"))
        elif page is not None and getattr(getattr(page, "window", None), "width", None):
            resolved_width = float(getattr(page.window, "width"))
        else:
            resolved_width = 400.0 if UI_IS_MOBILE else 1100.0
        self._pane_width = max(240.0, resolved_width)
        self._compact_mode: Optional[bool] = None

        self._host: Optional[EmbeddedWebViewHost] = None
        self._dock: Optional[WindowsWebView2Dock] = None
        self._fallback_html_loaded = False
        self._disposed = False

        touch_constraints = ft.BoxConstraints(min_width=44, min_height=44)
        self._wv_back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_IOS_NEW_ROUNDED,
            icon_size=16,
            tooltip="Trang trước",
            icon_color=C.TEXT_PRIMARY,
            disabled_color=C.TEXT_SECONDARY,
            size_constraints=touch_constraints,
            on_click=self._on_webview_back,
        )
        self._wv_fwd_btn = ft.IconButton(
            icon=ft.Icons.ARROW_FORWARD_IOS_ROUNDED,
            icon_size=16,
            tooltip="Trang kế",
            icon_color=C.TEXT_PRIMARY,
            disabled_color=C.TEXT_SECONDARY,
            size_constraints=touch_constraints,
            on_click=self._on_webview_forward,
        )
        self._wv_reload_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_size=18,
            tooltip="Tải lại trang",
            icon_color=C.TEXT_PRIMARY,
            size_constraints=touch_constraints,
            on_click=self._on_webview_reload,
        )
        self._ext_btn = ft.IconButton(
            icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
            icon_size=18,
            tooltip="Mở bằng trình duyệt ngoài",
            icon_color=C.TEXT_PRIMARY,
            size_constraints=touch_constraints,
            on_click=lambda _e: self._on_open_external(self._url),
        )
        self._expand_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_FULLSCREEN_ROUNDED
            if self._expanded
            else ft.Icons.OPEN_IN_FULL_ROUNDED,
            icon_size=18,
            tooltip="Thu về khung nội dung (Ctrl+\\)"
            if self._expanded
            else "Mở rộng toàn vùng môn học (Ctrl+\\)",
            icon_color=C.TEXT_PRIMARY,
            size_constraints=touch_constraints,
            visible=self._show_expand_toggle,
            on_click=lambda _e: self._on_toggle_expand()
            if self._on_toggle_expand is not None
            else None,
        )
        self._more_menu_item_reload = ft.PopupMenuItem(
            content="Tải lại trang",
            icon=ft.Icons.REFRESH_ROUNDED,
            on_click=self._on_webview_reload,
        )
        self._more_menu_item_ext = ft.PopupMenuItem(
            content="Mở bằng trình duyệt ngoài",
            icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
            on_click=lambda _e: self._on_open_external(self._url),
        )
        self._more_menu_btn = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT_ROUNDED,
            icon_color=C.TEXT_PRIMARY,
            bgcolor=C.SURFACE,
            tooltip="Tùy chọn",
            size_constraints=touch_constraints,
            items=[self._more_menu_item_reload, self._more_menu_item_ext],
        )

        self._header_container, self._back_btn, self._title_text, self._subtitle_text = (
            build_viewer_header(
                title=self._title,
                subtitle=self._short_subtitle(self._url),
                back_tooltip="Đóng trình xem",
                on_back=lambda _e: self._handle_back(),
                trailing_actions=[
                    self._wv_back_btn,
                    self._wv_fwd_btn,
                    self._wv_reload_btn,
                    self._expand_btn,
                    self._ext_btn,
                ],
            )
        )
        self._back_btn.size_constraints = touch_constraints
        self._title_col = self._header_container.content.controls[1]

        self._mobile_nav_row = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_AROUND,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[],
        )
        self._mobile_nav_bar = ft.Container(
            height=44,
            bgcolor=C.SURFACE,
            border=ft.Border.only(top=ft.BorderSide(1, C.BORDER)),
            content=self._mobile_nav_row,
            visible=False,
        )

        self._nav_progress = ft.ProgressBar(
            value=None,
            bar_height=2,
            color=C.ACCENT,
            bgcolor=C.BORDER,
            visible=True,
        )
        self._body_host = ft.Container(expand=True, bgcolor=C.BG)
        self.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                self._header_container,
                self._nav_progress,
                self._body_host,
                self._mobile_nav_bar,
            ],
        )
        self._apply_responsive_chrome()
        self._mount_engine()

    def _sync_external_visibility(self) -> None:
        show_ext = not _is_loopback_url(self._url)
        self._ext_btn.visible = show_ext
        self._more_menu_item_ext.visible = show_ext
        self._more_menu_btn.items = (
            [self._more_menu_item_reload, self._more_menu_item_ext]
            if show_ext
            else [self._more_menu_item_reload]
        )

    def _apply_responsive_chrome(self) -> bool:
        compact = self._pane_width < 600.0
        changed = self._compact_mode != compact
        self._compact_mode = compact
        header_row = self._header_container.content
        if compact:
            header_row.controls = [
                self._back_btn,
                self._title_col,
                self._more_menu_btn,
            ]
            self._mobile_nav_row.controls = [
                self._wv_back_btn,
                self._wv_fwd_btn,
                self._wv_reload_btn,
                self._ext_btn,
            ]
            self._mobile_nav_bar.visible = True
        else:
            header_row.controls = [
                self._back_btn,
                self._title_col,
                self._wv_back_btn,
                self._wv_fwd_btn,
                self._wv_reload_btn,
                self._expand_btn,
                self._ext_btn,
            ]
            self._mobile_nav_row.controls = []
            self._mobile_nav_bar.visible = False
        self._sync_external_visibility()
        return changed

    @staticmethod
    def _short_subtitle(url: str) -> str:
        parsed = urlsplit(url or "")
        return f"{parsed.netloc}{parsed.path}" if parsed.netloc else (url or "")

    def _has_auth_credentials(self, site: str) -> bool:
        svc = self._webview_auth_service
        if svc is not None:
            public_has_fn = getattr(svc, "has_credentials", None)
            if callable(public_has_fn):
                try:
                    return bool(public_has_fn(site))
                except TypeError:
                    return bool(public_has_fn())
            client_for_site = getattr(svc, "client_for_site", None)
            if callable(client_for_site) and client_for_site(site) is None:
                return False
            return True
        if site in {"courses", "thnn"} and self._session_client is not None:
            return True
        return False

    def _session_client_for_site(self, site: str) -> Any:
        svc = self._webview_auth_service
        if svc is not None:
            getter = getattr(svc, "client_for_site", None)
            if callable(getter):
                client = getter(site)
                if client is not None:
                    return client
        if site == self._source:
            return self._session_client
        return None

    def _resolve_auth_service(self) -> Optional[Any]:
        if self._webview_auth_service is not None:
            return self._webview_auth_service
        if self._session_client is None:
            return None
        if self._source == "thnn":
            return UthWebViewAuthService(thnn_client=self._session_client)
        return UthWebViewAuthService(courses_client=self._session_client)

    def _allow_preview_port_on_host(self, url: str) -> None:
        host = self._host
        if host is None or not self._is_allowed_preview_url(url):
            return
        try:
            port = urlsplit(url).port
        except ValueError:
            port = None
        allow_fn = getattr(host, "allow_loopback_port", None)
        if port is not None and callable(allow_fn):
            allow_fn(int(port))

    def _build_bridge_or_target_url(self, target_url: Optional[str] = None) -> str:
        target = str(target_url if target_url is not None else self._url)
        if self._preview_server is not None and hasattr(
            self._preview_server, "build_session_bridge_url"
        ):
            site = classify_uth_webview_url(target)
            if site in ("courses", "thnn"):
                bridge_target = normalize_uth_webview_target_url(
                    target, has_credentials=self._has_auth_credentials(site)
                )
                bridged = self._preview_server.build_session_bridge_url(
                    site, bridge_target
                )
                if not bridged:
                    register_fn = getattr(
                        self._preview_server, "register_session_provider", None
                    )
                    site_client = self._session_client_for_site(site)
                    if callable(register_fn) and site_client is not None:
                        register_fn(site, site_client)
                        bridged = self._preview_server.build_session_bridge_url(
                            site, bridge_target
                        )
                if bridged:
                    return bridged
            elif site == "external":
                bridged = self._preview_server.build_session_bridge_url(
                    self._source, target
                )
                if bridged:
                    return bridged
        return target

    def _mount_engine(self) -> None:
        use_bridge = _is_flet_webview_platform(self._page)
        load_url = self._build_bridge_or_target_url() if use_bridge else self._url

        if self._embedded_factory is not None:
            custom = self._embedded_factory(load_url)
            if custom is not None:
                if isinstance(custom, EmbeddedWebViewHost):
                    self._host = custom
                    self._dock = custom.dock
                    self._nav_progress.visible = False
                    self._body_host.content = ft.Container(
                        expand=True, content=custom.control()
                    )
                    return
                self._nav_progress.visible = False
                self._body_host.content = ft.Container(expand=True, content=custom)
                return

        target_host = (urlsplit(self._url or "").hostname or "").lower()
        site_kind = classify_uth_webview_url(self._url)
        is_uth_site = target_host == "ut.edu.vn" or target_host.endswith(".ut.edu.vn")
        cookie_provider: Optional[Callable[[], list[dict[str, Any]]]] = None
        init_script_provider: Optional[Callable[[], str]] = None

        if not use_bridge:
            if (
                site_kind in {"courses", "thnn", "portal"}
                and self._webview_auth_service is not None
            ):
                auth_svc = self._webview_auth_service
                initial_target = self._url
                bootstrap_lock = threading.Lock()
                bootstrap_cache: dict[str, Optional[WebViewAuthBootstrap]] = {
                    "value": None
                }

                def _load_initial_bootstrap() -> WebViewAuthBootstrap:
                    with bootstrap_lock:
                        cached = bootstrap_cache["value"]
                        if cached is None:
                            cached = auth_svc.prepare_initial_auth(initial_target)
                            bootstrap_cache["value"] = cached
                        return cached

                def _provide_cookies() -> list[dict[str, Any]]:
                    return list(_load_initial_bootstrap().cookies)

                def _provide_init_script() -> str:
                    return str(_load_initial_bootstrap().portal_init_script or "")

                cookie_provider = _provide_cookies
                init_script_provider = _provide_init_script
            elif (
                is_uth_site
                and self._session_client is not None
                and hasattr(self._session_client, "get_session_cookies")
            ):
                client_ref = self._session_client

                def _provide_legacy_cookies() -> list[dict[str, Any]]:
                    raw = _call_with_supported_kwargs(
                        client_ref.get_session_cookies,
                        ensure_login=True,
                        verify_live=True,
                    )
                    if isinstance(raw, dict):
                        return [
                            {
                                "name": str(k),
                                "value": str(v),
                                "domain": target_host,
                                "path": "/",
                            }
                            for k, v in raw.items()
                        ]
                    return list(raw or ())

                cookie_provider = _provide_legacy_cookies

        host = _call_with_supported_kwargs(
            build_embedded_webview_host,
            load_url,
            page=self._page,
            bgcolor=C.BG,
            account_namespace=self._account_namespace,
            cookie_provider=cookie_provider,
            init_script_provider=init_script_provider,
            on_ready=self._on_host_ready,
            on_title_changed=self._on_native_title,
            on_url_changed=self._on_native_url_or_bridge,
            on_navigation_starting=self._on_host_navigation_starting,
            on_loading_changed=self._on_host_loading_changed,
            on_history_changed=self._on_history_changed,
            on_error=self._on_host_error,
            on_external_url=self._on_open_external,
        )
        if host is not None:
            self._host = host
            self._dock = host.dock
            if (
                host.backend == "webview2"
                and host.dock is not None
                and getattr(host.dock, "_failed", False)
            ):
                self._host = None
                self._dock = None
                host.dispose()
                self._set_nav_loading(False)
                self._show_html_fallback_or_card()
                return
            self._nav_progress.visible = True
            self._body_host.content = host.control()
            if (
                host.backend == "flet_webview"
                and site_kind == "portal"
                and self._webview_auth_service is not None
            ):
                self._start_flet_portal_bootstrap()
            return

        self._show_html_fallback_or_card()

    def _schedule_host_eval_js(self, script: str) -> None:
        host = self._host
        if self._disposed or host is None or not script:
            return
        eval_fn = getattr(host, "eval_js", None)
        if not callable(eval_fn):
            return

        async def _run() -> None:
            if self._disposed:
                return
            try:
                res = eval_fn(script)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                logger.debug("WebView eval_js skipped: %s", exc)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(_run())
            return
        page = _safe_control_page(self) or self._page
        run_task = getattr(page, "run_task", None) if page is not None else None
        if callable(run_task):
            try:
                res = run_task(_run)
                if inspect.isawaitable(res):
                    asyncio.run(res)  # type: ignore[arg-type]
                return
            except Exception:
                pass
        try:
            asyncio.run(_run())
        except Exception:
            pass

    def _is_bridge_session_url(self, url: str) -> bool:
        if "/session/" not in url or not _is_loopback_url(url):
            return False
        if self._preview_server is not None and callable(
            getattr(self._preview_server, "is_active_loopback_origin", None)
        ):
            return self._is_allowed_preview_url(url)
        try:
            return urlsplit(str(url or "")).scheme.lower() == "http"
        except ValueError:
            return False

    def _resolved_current_url(self) -> str:
        host = self._host
        if host is not None:
            host_url = getattr(host, "current_url", "")
            if isinstance(host_url, str) and host_url:
                if self._is_bridge_session_url(host_url):
                    bridged = _extract_bridged_uth_url(host_url)
                    if bridged:
                        return bridged
                return host_url
        return self._current_url

    def _clear_pending_flet_portal_script(self) -> None:
        with self._portal_mobile_lock:
            self._pending_flet_portal_script = ""
            self._deferred_portal_login_url = ""

    def _cancel_portal_rearm_timer_locked(self) -> None:
        self._portal_rearm_generation += 1
        timer = self._portal_rearm_timer
        self._portal_rearm_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _cancel_portal_rearm_timer(self) -> None:
        with self._recovery_lock:
            self._cancel_portal_rearm_timer_locked()

    def _complete_portal_rearm_if_stable(self, expected_gen: int) -> None:
        with self._recovery_lock:
            if expected_gen != self._portal_rearm_generation:
                return
            self._portal_rearm_timer = None
            if (
                self._disposed
                or not self._portal_recovery_pending_rearm
                or "portal" in self._recovery_awaiting_commit
            ):
                return
            current = self._resolved_current_url()
            if (
                classify_uth_webview_url(current) == "portal"
                and is_uth_login_url(current) is None
            ):
                self._portal_recovery_pending_rearm = False
                self._recovery_tracker.note_authenticated_landing(current)

    def _start_flet_portal_bootstrap(self) -> None:
        with self._portal_mobile_lock:
            if self._portal_mobile_bootstrap_started or self._disposed:
                return
            self._portal_mobile_bootstrap_started = True
            self._flet_portal_bootstrap_in_flight = True
        auth_svc = self._webview_auth_service
        target_url = self._url
        if auth_svc is None:
            with self._portal_mobile_lock:
                self._flet_portal_bootstrap_in_flight = False
            return

        def _worker() -> None:
            mobile_script = ""
            try:
                bootstrap = auth_svc.prepare_initial_auth(target_url)
                if not self._disposed and bootstrap.portal_init_script:
                    mobile_script = _build_flet_portal_eval_script(
                        bootstrap.portal_init_script, bootstrap.target_url
                    )
            except Exception as exc:
                logger.debug("Mobile Portal bootstrap failed: %s", type(exc).__name__)

            should_eval_now = False
            deferred_login_url = ""
            with self._portal_mobile_lock:
                self._flet_portal_bootstrap_in_flight = False
                current_url = self._resolved_current_url()
                current_is_portal = classify_uth_webview_url(current_url) == "portal"
                if mobile_script and not self._disposed:
                    self._deferred_portal_login_url = ""
                    if current_is_portal and not self._portal_mobile_script_applied:
                        if self._flet_page_loaded:
                            self._portal_mobile_script_applied = True
                            should_eval_now = True
                        else:
                            self._pending_flet_portal_script = mobile_script
                else:
                    deferred_login_url = self._deferred_portal_login_url
                    self._deferred_portal_login_url = ""

            if should_eval_now:

                def _eval_if_still_portal(js: str = mobile_script) -> None:
                    cur = self._resolved_current_url()
                    if (
                        not self._disposed
                        and classify_uth_webview_url(cur) == "portal"
                        and WindowsWebView2Dock._url_origin(cur)
                        == "https://portal.ut.edu.vn"
                    ):
                        self._schedule_host_eval_js(js)

                self._marshal_ui(_eval_if_still_portal)
            elif (
                deferred_login_url
                and not self._disposed
                and is_uth_login_url(self._resolved_current_url()) == "portal"
            ):
                self._trigger_login_recovery(deferred_login_url)

        try:
            threading.Thread(
                target=_worker,
                name="CoursesWebViewMobilePortalAuth",
                daemon=True,
            ).start()
        except Exception:
            with self._portal_mobile_lock:
                self._flet_portal_bootstrap_in_flight = False

    def _on_host_navigation_starting(self, url: str) -> None:
        if not url or self._disposed:
            return
        if self._is_bridge_session_url(url):
            self._clear_pending_flet_portal_script()
            self._cancel_portal_rearm_timer()
            return
        site = classify_uth_webview_url(url)
        if site != "portal":
            self._clear_pending_flet_portal_script()
            self._cancel_portal_rearm_timer()
        if site in {"courses", "thnn", "portal"} and is_uth_login_url(url) is None:
            self._recovery_tracker.record_navigation(url)

    def _on_host_ready(self) -> None:
        if self._disposed:
            return
        current = self._resolved_current_url()
        site = classify_uth_webview_url(current)
        is_login = is_uth_login_url(current) is not None

        script_to_eval = ""
        with self._portal_mobile_lock:
            self._flet_page_loaded = True
            if (
                self._pending_flet_portal_script
                and not self._portal_mobile_script_applied
            ):
                if (
                    site == "portal"
                    and WindowsWebView2Dock._url_origin(current)
                    == "https://portal.ut.edu.vn"
                ):
                    script_to_eval = self._pending_flet_portal_script
                    self._pending_flet_portal_script = ""
                    self._portal_mobile_script_applied = True
                else:
                    self._pending_flet_portal_script = ""
        if script_to_eval:
            self._marshal_ui(lambda js=script_to_eval: self._schedule_host_eval_js(js))

        if site in {"courses", "thnn"} and not is_login:
            with self._recovery_lock:
                if site in self._recovery_awaiting_commit:
                    return
            self._recovery_tracker.note_authenticated_landing(current)
        elif site == "portal" and not is_login:
            with self._recovery_lock:
                if "portal" in self._recovery_awaiting_commit:
                    return
                if not self._portal_recovery_pending_rearm:
                    self._recovery_tracker.note_authenticated_landing(current)
                else:
                    grace = max(0.0, float(self._portal_rearm_grace_seconds))
                    self._cancel_portal_rearm_timer_locked()
                    if grace <= 0.0:
                        self._portal_recovery_pending_rearm = False
                        self._recovery_tracker.note_authenticated_landing(current)
                    else:
                        gen = self._portal_rearm_generation
                        timer = threading.Timer(
                            grace,
                            self._complete_portal_rearm_if_stable,
                            args=(gen,),
                        )
                        timer.daemon = True
                        self._portal_rearm_timer = timer
                        timer.start()

    def _marshal_ui(self, fn: Callable[[], None]) -> None:
        if self._disposed:
            return
        page = _safe_control_page(self) or self._page
        if page is None or not callable(getattr(page, "run_task", None)):
            fn()
            return
        try:
            asyncio.get_running_loop()
            fn()
            return
        except RuntimeError:
            pass

        async def _apply() -> None:
            if not self._disposed:
                fn()

        try:
            page.run_task(_apply)
        except Exception:
            fn()

    def _set_nav_loading(self, loading: bool) -> None:
        if self._disposed:
            return
        self._nav_progress.visible = bool(loading)
        safe_control_update(self._nav_progress)

    def _on_host_loading_changed(self, loading: bool) -> None:
        def _apply() -> None:
            self._nav_progress.visible = bool(loading)
            safe_control_update(self._nav_progress)

        self._marshal_ui(_apply)

    def _on_history_changed(self, can_back: bool, can_forward: bool) -> None:
        def _apply() -> None:
            self._wv_back_btn.disabled = not bool(can_back)
            self._wv_fwd_btn.disabled = not bool(can_forward)
            safe_control_update(self._wv_back_btn)
            safe_control_update(self._wv_fwd_btn)

        self._marshal_ui(_apply)

    def _on_native_title(self, title: str) -> None:
        if not title:
            return

        def _apply() -> None:
            self._title_text.value = title
            safe_control_update(self._title_text)

        self._marshal_ui(_apply)

    def _on_native_url_or_bridge(self, url: str) -> None:
        if not url:
            return
        if self._is_bridge_session_url(url):
            self._last_rewritten_bridge_target = ""
            bridged_uth = _extract_bridged_uth_url(url)
            if bridged_uth:
                self._current_url = bridged_uth
                if is_uth_login_url(bridged_uth) is None:
                    bridged_site = classify_uth_webview_url(bridged_uth)
                    with self._recovery_lock:
                        self._recovery_awaiting_commit.discard(bridged_site)
            return

        self._current_url = url
        login_site = is_uth_login_url(url)
        if login_site is not None:
            if login_site == "portal":
                self._cancel_portal_rearm_timer()
            self._trigger_login_recovery(url)
        else:
            site = classify_uth_webview_url(url)
            if site != "portal":
                self._clear_pending_flet_portal_script()
                self._cancel_portal_rearm_timer()
            if site in {"courses", "thnn", "portal"}:
                with self._recovery_lock:
                    self._recovery_awaiting_commit.discard(site)
                self._recovery_tracker.record_navigation(url)
                if (
                    site in {"courses", "thnn"}
                    and self._host is not None
                    and self._host.backend == "flet_webview"
                    and self._preview_server is not None
                ):
                    bridged = self._build_bridge_or_target_url(url)
                    if bridged != url and bridged != self._last_rewritten_bridge_target:
                        self._last_rewritten_bridge_target = bridged
                        self._source = site
                        self._allow_preview_port_on_host(bridged)
                        self._host.navigate(bridged)

        def _apply() -> None:
            self._url = url
            self._subtitle_text.value = self._short_subtitle(url)
            self._sync_external_visibility()
            safe_control_update(self._subtitle_text)
            safe_control_update(self._ext_btn)

        self._marshal_ui(_apply)

    def _trigger_login_recovery(self, login_url: str) -> None:
        if self._disposed or self._host is None:
            return
        if is_uth_login_url(login_url) == "portal":
            with self._portal_mobile_lock:
                if self._flet_portal_bootstrap_in_flight:
                    self._deferred_portal_login_url = login_url
                    return
        with self._recovery_lock:
            if self._recovery_in_flight:
                return
            self._recovery_in_flight = True
        try:
            threading.Thread(
                target=self._attempt_login_recovery,
                args=(login_url,),
                name="CoursesWebViewLoginRecovery",
                daemon=True,
            ).start()
        except Exception:
            with self._recovery_lock:
                self._recovery_in_flight = False

    def _attempt_login_recovery(self, login_url: str) -> None:
        try:
            if self._disposed:
                return
            auth_service = self._resolve_auth_service()
            if auth_service is None:
                return
            bootstrap = auth_service.recover_from_login_redirect(
                login_url, self._recovery_tracker
            )
            if bootstrap is None or self._disposed:
                return
            self._marshal_ui(lambda b=bootstrap: self._apply_recovery_bootstrap(b))
        except Exception as exc:
            logger.debug("WebView login recovery failed: %s", type(exc).__name__)
        finally:
            with self._recovery_lock:
                self._recovery_in_flight = False

    def _apply_recovery_bootstrap(self, bootstrap: WebViewAuthBootstrap) -> None:
        if self._disposed or self._host is None:
            return
        host = self._host
        target_url = bootstrap.target_url
        if not target_url:
            return
        with self._recovery_lock:
            self._recovery_awaiting_commit.add(bootstrap.site)
            if bootstrap.site == "portal":
                self._cancel_portal_rearm_timer_locked()
                self._portal_recovery_pending_rearm = True
        nav_url = target_url
        init_script = bootstrap.portal_init_script
        if host.backend == "flet_webview":
            if bootstrap.site in ("courses", "thnn") and self._preview_server is not None:
                self._source = bootstrap.site
                nav_url = self._build_bridge_or_target_url(target_url)
                self._allow_preview_port_on_host(nav_url)
            elif bootstrap.site == "portal" and init_script:
                with self._portal_mobile_lock:
                    self._portal_mobile_script_applied = True
                    self._pending_flet_portal_script = ""
                    self._deferred_portal_login_url = ""
                init_script = _build_flet_portal_eval_script(init_script, target_url)

        self._url = target_url
        self._subtitle_text.value = self._short_subtitle(target_url)
        self._sync_external_visibility()
        safe_control_update(self._subtitle_text)
        safe_control_update(self._ext_btn)
        self._set_nav_loading(True)
        apply_fn = getattr(host, "apply_auth_and_navigate", None)
        if callable(apply_fn):
            apply_fn(list(bootstrap.cookies), nav_url, init_script=init_script)
        else:
            host.navigate(nav_url)

    def _is_allowed_preview_url(self, url: str) -> bool:
        if self._preview_server is None:
            return False
        checker = getattr(self._preview_server, "is_active_loopback_origin", None)
        if not callable(checker):
            return False
        return bool(safe_http_url(url, allow_loopback=True)) and bool(checker(url))

    def _is_allowed_navigation_url(self, url: str) -> bool:
        if bool(safe_http_url(url)):
            return True
        return self._is_allowed_preview_url(url)

    def _show_blocked_url_card(self) -> None:
        self._body_host.content = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            padding=24,
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=12,
                controls=[
                    ft.Icon(ft.Icons.BLOCK_ROUNDED, size=44, color=C.CRITICAL),
                    ft.Text(
                        self._title,
                        size=16,
                        weight=ft.FontWeight.W_600,
                        color=C.TEXT_PRIMARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        "Đã chặn điều hướng tới liên kết không được phép vì lý do bảo mật.",
                        size=13,
                        color=C.TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.FilledButton(
                        "Quay lại",
                        icon=ft.Icons.ARROW_BACK_ROUNDED,
                        on_click=lambda _e: self._handle_back(),
                    ),
                ],
            ),
        )
        safe_control_update(self._body_host)

    def _on_host_error(self, reason: str) -> None:
        self._cancel_portal_rearm_timer()

        def _apply() -> None:
            self._set_nav_loading(False)
            host = self._host
            reason_str = str(reason or "")
            if reason_str.startswith("Blocked disallowed WebView URL:"):
                if host is not None:
                    self._host = None
                    self._dock = None
                    host.dispose()
                if not self._disposed:
                    self._show_blocked_url_card()
                    safe_control_update(self)
                logger.warning(
                    "Blocked disallowed WebView URL in CoursesWebViewPane: %s",
                    reason_str,
                )
                return
            if host is not None and host.backend == "webview2":
                dock = host.dock
                if dock is None or not getattr(dock, "_ready", False):
                    self._host = None
                    self._dock = None
                    host.dispose()
                    if not self._disposed:
                        self._show_html_fallback_or_card()
                        safe_control_update(self)
                    return
            logger.debug("EmbeddedWebViewHost error: %s", reason)

        self._marshal_ui(_apply)

    def _show_html_fallback_or_card(self) -> None:
        self._body_host.content = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            padding=24,
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=10,
                controls=[
                    ft.ProgressRing(width=24, height=24, stroke_width=2.5, color=C.ACCENT),
                    ft.Text(
                        "Đang tải nội dung hoạt động qua phiên Moodle...",
                        size=13,
                        color=C.TEXT_SECONDARY,
                    ),
                ],
            ),
        )
        if not self._fallback_html_loaded:
            self._fallback_html_loaded = True
            threading.Thread(
                target=self._load_authenticated_html_fallback,
                name="CoursesWebViewHtmlFallback",
                daemon=True,
            ).start()

    def _load_authenticated_html_fallback(self) -> None:
        markdown_text = ""
        extracted_links: list[tuple[str, str]] = []
        error_text = ""
        parsed_target = urlsplit(self._url or "")
        target_host = (parsed_target.hostname or "").lower()
        is_uth_site = target_host == "ut.edu.vn" or target_host.endswith(".ut.edu.vn")
        is_local_preview = self._is_allowed_preview_url(self._url)
        html_text = ""
        try:
            fallback_client = (
                self._session_client_for_site(classify_uth_webview_url(self._url))
                or self._session_client
            )
            if not self._is_allowed_navigation_url(self._url):
                error_text = "Liên kết không hợp lệ hoặc không được phép mở."
            elif (
                is_uth_site
                and fallback_client is not None
                and hasattr(fallback_client, "open_session_url")
            ):
                _final_url, html_text, redirected = fallback_client.open_session_url(
                    self._url
                )
                if redirected:
                    error_text = "Liên kết điều hướng ra ngoài hệ thống UTH."
            elif is_local_preview:
                opener = urllib.request.build_opener(
                    _SameOriginRedirectHandler(
                        expected_scheme=parsed_target.scheme,
                        expected_netloc=parsed_target.netloc,
                        origin_validator=self._is_allowed_preview_url,
                    )
                )
                req = urllib.request.Request(
                    self._url,
                    headers={"User-Agent": "UTHelper/2.5"},
                )
                with opener.open(req, timeout=5) as resp:  # nosec B310
                    raw_bytes = resp.read(512 * 1024)
                    html_text = raw_bytes.decode("utf-8", errors="replace")
            elif not is_uth_site:
                error_text = (
                    "Liên kết ngoài hệ thống yêu cầu trình duyệt tương tác trực tiếp (WebView2 / trình duyệt)."
                )

            if html_text and not error_text:
                raw_soup = BeautifulSoup(html_text, BS4_PARSER)
                for junk_tag in list(raw_soup.find_all(_NON_READABLE_HTML_TAGS)):
                    if not getattr(junk_tag, "decomposed", False):
                        junk_tag.decompose()
                HTMLSanitizer.sanitize_soup(raw_soup)
                main = (
                    raw_soup.select_one("#region-main")
                    or raw_soup.select_one("main")
                    or raw_soup.body
                    or raw_soup
                )
                lines: list[str] = []
                for el in main.find_all(["h1", "h2", "h3", "p", "li"]):
                    txt = " ".join(el.get_text(" ", strip=True).split())
                    if txt:
                        prefix = (
                            "## "
                            if el.name in ("h1", "h2", "h3")
                            else ("- " if el.name == "li" else "")
                        )
                        lines.append(f"{prefix}{txt}")
                markdown_text = "\n\n".join(lines[:140])
                for anchor in main.select("a[href]"):
                    href = str(anchor.get("href") or "").strip()
                    label = " ".join(anchor.get_text(" ", strip=True).split())
                    if (
                        label
                        and self._is_allowed_navigation_url(href)
                        and len(extracted_links) < 12
                    ):
                        extracted_links.append((label, href))
        except Exception as exc:
            error_text = str(exc)

        if self._disposed:
            return

        self._nav_progress.visible = False
        safe_control_update(self._nav_progress)

        if error_text or not markdown_text:
            card_controls: list[ft.Control] = [
                ft.Icon(ft.Icons.LANGUAGE_ROUNDED, size=44, color=C.ACCENT),
                ft.Text(
                    self._title,
                    size=16,
                    weight=ft.FontWeight.W_600,
                    color=C.TEXT_PRIMARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Text(
                    error_text
                    or "Hoạt động này yêu cầu trình duyệt tương tác trực tiếp trên thiết bị.",
                    size=13,
                    color=C.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
            ]
            if safe_http_url(self._url) and not _is_loopback_url(self._url):
                card_controls.append(
                    ft.FilledButton(
                        "Mở bằng trình duyệt ngoài",
                        icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                        on_click=lambda _e: self._on_open_external(self._url),
                    )
                )
            self._body_host.content = ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                padding=24,
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=12,
                    controls=card_controls,
                ),
            )
            safe_control_update(self._body_host)
            return

        controls: list[ft.Control] = [
            ft.Markdown(
                value=markdown_text,
                selectable=True,
                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                on_tap_link=lambda e: self._navigate_link(
                    str(getattr(e, "data", "") or "")
                ),
            )
        ]
        if extracted_links:
            controls.append(ft.Divider(height=16, color=C.BORDER))
            controls.append(
                ft.Text(
                    "Liên kết nhanh trong trang",
                    size=11,
                    weight=ft.FontWeight.W_600,
                    color=C.TEXT_SECONDARY,
                )
            )
            controls.append(
                ft.Row(
                    wrap=True,
                    spacing=8,
                    run_spacing=8,
                    controls=[
                        ft.OutlinedButton(
                            label[:40],
                            on_click=lambda _e, target=href: self._navigate_link(target),
                        )
                        for label, href in extracted_links
                    ],
                )
            )
        self._body_host.content = ft.Container(
            expand=True,
            padding=ft.Padding.symmetric(horizontal=20, vertical=16),
            content=ft.ListView(expand=True, spacing=8, controls=controls),
        )
        safe_control_update(self._body_host)

    def _navigate_link(self, target_url: str) -> None:
        clean = str(target_url or "").strip()
        if not clean or not self._is_allowed_navigation_url(clean):
            logger.warning("Blocked disallowed navigation link in CoursesWebViewPane")
            return
        site = classify_uth_webview_url(clean)
        if site != "portal":
            self._clear_pending_flet_portal_script()
            self._cancel_portal_rearm_timer()
        if site in {"courses", "thnn", "portal"}:
            clean = normalize_uth_webview_target_url(
                clean, has_credentials=self._has_auth_credentials(site)
            )
            self._recovery_tracker.record_navigation(clean)
            if site in {"courses", "thnn"}:
                self._source = site
        self._url = clean
        self._current_url = clean
        self._subtitle_text.value = self._short_subtitle(clean)
        self._sync_external_visibility()
        safe_control_update(self._subtitle_text)
        if self._host is not None:
            self._set_nav_loading(True)
            nav_url = (
                self._build_bridge_or_target_url(clean)
                if self._host.backend == "flet_webview"
                else clean
            )
            if self._host.backend == "flet_webview":
                self._allow_preview_port_on_host(nav_url)
            self._host.navigate(nav_url)
        elif self._dock is not None:
            self._set_nav_loading(True)
            self._dock.navigate(clean)
        else:
            self._fallback_html_loaded = False
            self._show_html_fallback_or_card()
            safe_control_update(self._body_host)

    def _on_webview_back(self, _e: Any) -> None:
        if self._host is not None:
            self._host.go_back()
        elif self._dock is not None:
            self._dock.go_back()

    def _on_webview_forward(self, _e: Any) -> None:
        if self._host is not None:
            self._host.go_forward()
        elif self._dock is not None:
            self._dock.go_forward()

    def _on_webview_reload(self, _e: Any) -> None:
        if self._host is not None:
            self._set_nav_loading(True)
            self._host.reload()
        elif self._dock is not None:
            self._dock.reload()
        else:
            self._fallback_html_loaded = False
            self._show_html_fallback_or_card()
            safe_control_update(self._body_host)

    def handle_back(self) -> bool:
        """Step back within WebView history if possible; otherwise allow overlay pop."""
        if self._host is not None and self._host.can_go_back():
            self._host.go_back()
            return True
        if self._dock is not None and self._dock.can_go_back():
            self._dock.go_back()
            return True
        return False

    def _handle_back(self) -> None:
        self.dispose()
        self._on_back()

    def set_expanded(self, expanded: bool, *, visible: bool = True) -> None:
        self._expanded = bool(expanded)
        self._show_expand_toggle = bool(visible)
        self._expand_btn.icon = (
            ft.Icons.CLOSE_FULLSCREEN_ROUNDED
            if self._expanded
            else ft.Icons.OPEN_IN_FULL_ROUNDED
        )
        self._expand_btn.tooltip = (
            "Thu về khung nội dung (Ctrl+\\)"
            if self._expanded
            else "Mở rộng toàn vùng môn học (Ctrl+\\)"
        )
        self._expand_btn.visible = self._show_expand_toggle
        safe_control_update(self._expand_btn)

    def sync_bounds(
        self,
        left: float,
        top: float,
        width: float,
        height: float,
        *,
        nav_bottom_inset: float = 0.0,
    ) -> None:
        self._pane_width = max(240.0, float(width))
        changed = self._apply_responsive_chrome()
        if changed:
            safe_control_update(self)
        if self._disposed:
            return
        header_h = 55.0
        mobile_bar_h = 44.0 if self._mobile_nav_bar.visible else 0.0
        bottom_inset = max(0.0, float(nav_bottom_inset)) + mobile_bar_h
        if self._host is not None:
            self._host.sync_bounds(
                left,
                top + header_h,
                width,
                max(160.0, height - header_h - mobile_bar_h),
                bottom_inset=bottom_inset,
            )
        elif self._dock is not None:
            self._dock.set_bounds(
                left,
                top + header_h,
                width,
                max(160.0, height - header_h - mobile_bar_h),
                bottom_inset=bottom_inset,
            )

    def suspend(self) -> None:
        if self._host is not None:
            self._host.suspend()
        elif self._dock is not None:
            self._dock.set_visible(False)

    def resume(self) -> None:
        if self._disposed:
            return
        if self._host is not None:
            self._host.resume()
        elif self._dock is not None:
            self._dock.set_visible(True)

    def dispose(self) -> None:
        self._disposed = True
        self._cancel_portal_rearm_timer()
        with self._portal_mobile_lock:
            self._pending_flet_portal_script = ""
            self._deferred_portal_login_url = ""
            self._flet_portal_bootstrap_in_flight = False
        if self._host is not None:
            self._host.dispose()
            self._host = None
            self._dock = None
        elif self._dock is not None:
            self._dock.dispose()
            self._dock = None

    def update_theme(self) -> None:
        self.bgcolor = C.BG
        self._body_host.bgcolor = C.BG
        self._nav_progress.color = C.ACCENT
        self._nav_progress.bgcolor = C.BORDER
        self._mobile_nav_bar.bgcolor = C.SURFACE
        self._mobile_nav_bar.border = ft.Border.only(top=ft.BorderSide(1, C.BORDER))
        self._more_menu_btn.icon_color = C.TEXT_PRIMARY
        self._more_menu_btn.bgcolor = C.SURFACE
        refresh_viewer_header(
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        )
        for btn in (
            self._wv_back_btn,
            self._wv_fwd_btn,
            self._wv_reload_btn,
            self._expand_btn,
            self._ext_btn,
        ):
            btn.icon_color = C.TEXT_PRIMARY
        self._wv_back_btn.disabled_color = C.TEXT_SECONDARY
        self._wv_fwd_btn.disabled_color = C.TEXT_SECONDARY
        if self._host is not None:
            self._host.update_theme()
        safe_control_update(self)
