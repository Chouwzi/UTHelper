"""Authenticated in-app WebView pane (Windows WebView2 + flet-webview + HTML reader fallback)."""

from __future__ import annotations

import asyncio
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
    host = (urlsplit(str(url or "").strip()).hostname or "").lower()
    return host in _LOOPBACK_HOSTS


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
        on_back: Callable[[], None],
        on_open_external: Callable[[str], None],
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
        embedded_webview_factory: Optional[Callable[[str], Optional[ft.Control]]] = None,
        pane_width: Optional[float] = None,
    ) -> None:
        super().__init__(expand=True, bgcolor=C.BG)
        self._page = page
        self._source = source
        self._title = title or "Trình duyệt học tập"
        self._url = url
        self._preview_server = preview_server
        self._session_client = session_client
        self._on_back = on_back
        self._on_open_external = on_open_external
        self._expanded = bool(is_expanded)
        self._show_expand_toggle = bool(show_expand_toggle)
        self._on_toggle_expand = on_toggle_expand
        self._embedded_factory = embedded_webview_factory

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

    def _build_bridge_or_target_url(self) -> str:
        if self._preview_server is not None and hasattr(
            self._preview_server, "build_session_bridge_url"
        ):
            bridged = self._preview_server.build_session_bridge_url(self._source, self._url)
            if bridged:
                return bridged
        return self._url

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
        is_uth_site = target_host == "ut.edu.vn" or target_host.endswith(".ut.edu.vn")
        cookie_provider: Optional[Callable[[], list[dict[str, Any]]]] = None
        if (
            is_uth_site
            and self._session_client is not None
            and hasattr(self._session_client, "get_session_cookies")
        ):
            client_ref = self._session_client

            def _provide_cookies() -> list[dict[str, Any]]:
                return client_ref.get_session_cookies(ensure_login=True)

            cookie_provider = _provide_cookies

        host = build_embedded_webview_host(
            load_url,
            page=self._page,
            bgcolor=C.BG,
            cookie_provider=cookie_provider,
            on_title_changed=self._on_native_title,
            on_url_changed=self._on_native_url_or_bridge,
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
            return

        self._show_html_fallback_or_card()

    def _marshal_ui(self, fn: Callable[[], None]) -> None:
        if self._disposed:
            return
        page = _safe_control_page(self)
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
        if not url or "/session/" in url:
            return

        def _apply() -> None:
            self._url = url
            self._subtitle_text.value = self._short_subtitle(url)
            self._sync_external_visibility()
            safe_control_update(self._subtitle_text)
            safe_control_update(self._ext_btn)

        self._marshal_ui(_apply)

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
            if not self._is_allowed_navigation_url(self._url):
                error_text = "Liên kết không hợp lệ hoặc không được phép mở."
            elif (
                is_uth_site
                and self._session_client is not None
                and hasattr(self._session_client, "open_session_url")
            ):
                _final_url, html_text, redirected = self._session_client.open_session_url(
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
        self._url = clean
        self._subtitle_text.value = self._short_subtitle(clean)
        self._sync_external_visibility()
        safe_control_update(self._subtitle_text)
        if self._host is not None:
            self._set_nav_loading(True)
            nav_url = (
                self._build_bridge_or_target_url()
                if self._host.backend == "flet_webview"
                else clean
            )
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
