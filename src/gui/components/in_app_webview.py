"""In-app WebView dialog with safe HTTPS validation and cross-platform fallback."""

from __future__ import annotations

import asyncio
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Optional

import flet as ft
from bs4 import BeautifulSoup

from core.url_validation import validate_https_url, validate_public_https_url
from gui.core.theme import C
from gui.core.utils import html_to_markdown

try:
    import flet_webview
except ImportError:  # pragma: no cover - optional on non-mobile CI environments
    flet_webview = None

logger = logging.getLogger(__name__)

_MAX_PREVIEW_BYTES = 2 * 1024 * 1024


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        if validate_public_https_url(redirected.full_url, resolve_dns=True) is None:
            raise urllib.error.HTTPError(
                redirected.full_url,
                code,
                "Blocked unsafe or non-HTTPS redirect",
                headers,
                fp,
            )
        return redirected


def supports_native_webview(page: Optional[ft.Page]) -> bool:
    """Return True when flet_webview.WebView is supported on ``page``."""
    if flet_webview is None or page is None:
        return False
    if bool(getattr(page, "web", False)):
        return False
    platform = getattr(page, "platform", None)
    return platform in (
        ft.PagePlatform.ANDROID,
        ft.PagePlatform.IOS,
        ft.PagePlatform.MACOS,
    )


def _fetch_https_preview(url: str) -> tuple[str, str]:
    """Fetch bounded HTTPS HTML and convert it into a readable preview."""
    validated = validate_public_https_url(url, resolve_dns=True)
    if validated is None:
        return "", ""
    opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler())
    req = urllib.request.Request(
        validated,
        headers={
            "User-Agent": "UTHelper/InAppViewer",
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )
    with opener.open(req, timeout=10) as resp:
        final_url = getattr(resp, "geturl", lambda: validated)()
        if validate_public_https_url(final_url, resolve_dns=True) is None:
            return "", ""
        raw = resp.read(_MAX_PREVIEW_BYTES + 1)
    if len(raw) > _MAX_PREVIEW_BYTES:
        raw = raw[:_MAX_PREVIEW_BYTES]
    html_text = raw.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_text, "html.parser")
    page_title = ""
    if soup.title and soup.title.string:
        page_title = soup.title.string.strip()
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    main_node = soup.find("main") or soup.find("article") or soup.body or soup
    markdown = html_to_markdown(str(main_node)).strip()
    return page_title, markdown[:8000]


class _NativeWebViewHost(ft.Container):
    """Enables JavaScript on fallback flet_webview once mounted on a Page."""

    def __init__(self, webview: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._webview = webview

    def did_mount(self) -> None:
        wv = self._webview
        js_enum = getattr(flet_webview, "JavaScriptMode", None)
        set_js = getattr(wv, "set_javascript_mode", None)
        if wv is None or js_enum is None or not callable(set_js):
            return
        try:
            page = self.page
        except RuntimeError:
            return
        run_task = getattr(page, "run_task", None)
        if not callable(run_task):
            return

        async def _enable_js() -> None:
            try:
                await set_js(js_enum.UNRESTRICTED)
                reload_fn = getattr(wv, "reload", None)
                if callable(reload_fn):
                    await reload_fn()
            except Exception:
                pass

        try:
            run_task(_enable_js)
        except Exception:
            pass


class InAppWebViewDialog:
    """Modal in-app WebView pane with explicit external-browser opt-out."""

    def __init__(
        self,
        page: ft.Page,
        *,
        url: str,
        title: str = "",
        on_open_external: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._page = page
        self._url = validate_public_https_url(url, resolve_dns=False) or ""
        self._title = (title or "").strip() or "Trình duyệt trong ứng dụng"
        self._on_open_external = on_open_external
        self._dialog: Optional[ft.AlertDialog] = None
        self._webview = None
        self._embedded_host = None
        self._closed = False

    def show(self) -> bool:
        if not self._url or self._page is None:
            return False

        host = urllib.parse.urlsplit(self._url).hostname or self._url
        title_label = ft.Text(
            self._title,
            size=14,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        subtitle_label = ft.Text(
            host,
            size=11,
            color=C.TEXT_SECONDARY,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        btn_constraints = ft.BoxConstraints(
            min_width=44,
            max_width=44,
            min_height=44,
            max_height=44,
        )

        external_btn = ft.Semantics(
            label="Mở bằng trình duyệt ngoài",
            button=True,
            content=ft.IconButton(
                icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                icon_color=C.ACCENT,
                icon_size=19,
                tooltip="Mở bằng trình duyệt ngoài",
                padding=0,
                size_constraints=btn_constraints,
                on_click=self._handle_open_external,
            ),
        )
        close_btn = ft.Semantics(
            label="Đóng trình xem web",
            button=True,
            content=ft.IconButton(
                icon=ft.Icons.CLOSE_ROUNDED,
                icon_color=C.TEXT_SECONDARY,
                icon_size=20,
                tooltip="Đóng",
                padding=0,
                size_constraints=btn_constraints,
                on_click=lambda _e: self.close(),
            ),
        )

        from gui.components.courses.embedded_webview_host import (
            _is_inappwebview_mobile_platform,
            build_embedded_webview_host,
        )

        is_inapp_mobile = _is_inappwebview_mobile_platform(self._page)
        back_icon_btn: Optional[ft.IconButton] = None
        forward_icon_btn: Optional[ft.IconButton] = None
        toolbar_actions: list[ft.Control] = []
        if is_inapp_mobile or supports_native_webview(self._page):
            back_icon_btn = ft.IconButton(
                icon=ft.Icons.ARROW_BACK_ROUNDED,
                icon_color=C.TEXT_SECONDARY,
                disabled_color=C.TEXT_SECONDARY,
                disabled=is_inapp_mobile,
                icon_size=19,
                tooltip="Trang trước",
                padding=0,
                size_constraints=btn_constraints,
                on_click=self._go_back,
            )
            back_btn = ft.Semantics(
                label="Trang trước",
                button=True,
                content=back_icon_btn,
            )
            forward_icon_btn = ft.IconButton(
                icon=ft.Icons.ARROW_FORWARD_ROUNDED,
                icon_color=C.TEXT_SECONDARY,
                disabled_color=C.TEXT_SECONDARY,
                disabled=is_inapp_mobile,
                icon_size=19,
                tooltip="Trang sau",
                padding=0,
                size_constraints=btn_constraints,
                on_click=self._go_forward,
            )
            forward_btn = ft.Semantics(
                label="Trang sau",
                button=True,
                content=forward_icon_btn,
            )
            reload_btn = ft.Semantics(
                label="Tải lại trang",
                button=True,
                content=ft.IconButton(
                    icon=ft.Icons.REFRESH_ROUNDED,
                    icon_color=C.TEXT_SECONDARY,
                    icon_size=19,
                    tooltip="Tải lại",
                    padding=0,
                    size_constraints=btn_constraints,
                    on_click=self._reload,
                ),
            )
            toolbar_actions.extend([back_btn, forward_btn, reload_btn])

        toolbar_actions.extend([external_btn, close_btn])

        header = ft.Row(
            controls=[
                ft.Column(
                    controls=[title_label, subtitle_label],
                    spacing=1,
                    tight=True,
                    expand=True,
                ),
                ft.Row(controls=toolbar_actions, spacing=0, tight=True),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        body_host = ft.Container(expand=True, bgcolor=C.BG, border_radius=10)
        committed_state = {"done": False}

        def _on_embedded_url_changed(nav_url: str) -> None:
            validated_nav = validate_public_https_url(nav_url, resolve_dns=False)
            if validated_nav:
                self._url = validated_nav
                subtitle_label.value = (
                    urllib.parse.urlsplit(validated_nav).hostname or host
                )
                self._safe_update(subtitle_label)

        def _on_embedded_history_changed(can_back: bool, can_fwd: bool) -> None:
            if back_icon_btn is not None:
                back_icon_btn.disabled = not bool(can_back)
                self._safe_update(back_icon_btn)
            if forward_icon_btn is not None:
                forward_icon_btn.disabled = not bool(can_fwd)
                self._safe_update(forward_icon_btn)

        def _on_embedded_error(reason: str) -> None:
            if self._closed or committed_state["done"]:
                return
            host_ref = self._embedded_host
            if host_ref is not None:
                self._embedded_host = None
                self._webview = None
                try:
                    host_ref.dispose()
                except Exception:
                    pass
            reason_str = str(reason or "")
            if reason_str.startswith("__UTH_EXTERNAL_APP_LAUNCHED__:"):
                msg = "Đã mở liên kết trong ứng dụng ngoài trên thiết bị."
            elif reason_str.startswith("__UTH_EXTERNAL_APP_MISSING__:"):
                msg = "Thiết bị chưa cài đặt ứng dụng hỗ trợ trực tiếp liên kết này. Bạn có thể mở bằng trình duyệt ngoài."
            else:
                msg = "Không thể hiển thị trực tiếp liên kết này trong ứng dụng. Bạn có thể mở bằng trình duyệt ngoài."
            body_host.padding = ft.Padding.all(14)
            body_host.border = ft.Border.all(1, C.BORDER)
            body_host.content = ft.Column(
                controls=[
                    ft.Text(self._url, size=11, color=C.ACCENT, selectable=True),
                    ft.Text(msg, size=12, color=C.TEXT_SECONDARY),
                    ft.Button(
                        "Mở bằng trình duyệt ngoài",
                        icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                        bgcolor=C.ACCENT,
                        color=C.ON_ACCENT,
                        height=44,
                        on_click=self._handle_open_external,
                    ),
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            )
            self._safe_update(body_host)

        embedded_host = (
            build_embedded_webview_host(
                self._url,
                page=self._page,
                bgcolor=C.BG,
                on_ready=lambda: committed_state.__setitem__("done", True),
                on_error=_on_embedded_error,
                on_title_changed=lambda title: (
                    committed_state.__setitem__("done", True),
                    setattr(title_label, "value", title),
                    self._safe_update(title_label),
                )
                if title
                else None,
                on_url_changed=_on_embedded_url_changed,
                on_history_changed=_on_embedded_history_changed,
                on_external_url=lambda ext: self._on_open_external(ext)
                if callable(self._on_open_external)
                else None,
            )
            if is_inapp_mobile
            else None
        )
        if embedded_host is not None:
            self._embedded_host = embedded_host
            slot_ctrl = embedded_host.control()
            self._webview = getattr(slot_ctrl, "content", slot_ctrl)
            status_bar = ft.ProgressBar(
                value=None, bar_height=2, color=C.ACCENT, bgcolor=C.BORDER, visible=True
            )
            status_host = ft.Container(
                left=0,
                top=0,
                right=0,
                height=2,
                opacity=1.0,
                ignore_interactions=True,
                content=status_bar,
            )

            def _on_inapp_loading(loading: bool) -> None:
                is_loading = bool(loading)
                status_bar.visible = is_loading
                status_host.opacity = 1.0 if is_loading else 0.0
                self._safe_update(status_host)

            embedded_host._on_loading_changed = _on_inapp_loading
            body_host.content = ft.Stack(
                expand=True,
                fit=ft.StackFit.EXPAND,
                controls=[slot_ctrl, status_host],
            )
        elif supports_native_webview(self._page):
            status_bar = ft.ProgressBar(
                value=0, bar_height=2, color=C.ACCENT, bgcolor=C.BORDER, visible=True
            )
            status_host = ft.Container(
                left=0,
                top=0,
                right=0,
                height=2,
                opacity=1.0,
                ignore_interactions=True,
                content=status_bar,
            )

            def _extract_nav_url(e) -> str:
                raw = getattr(e, "data", None)
                if raw is None:
                    raw = getattr(e, "url", "")
                return str(raw or "").strip()

            def _is_safe_native_url(nav_url: str) -> bool:
                if not nav_url:
                    return True
                # Case-insensitive validation: block non-HTTPS schemes and local/private targets.
                return (
                    validate_https_url(nav_url) is not None
                    and validate_public_https_url(nav_url, resolve_dns=False) is not None
                )

            def _on_started(e):
                nav_url = _extract_nav_url(e)
                if nav_url and not _is_safe_native_url(nav_url):
                    self.close()
                    return
                status_bar.value = None
                status_bar.visible = True
                status_host.opacity = 1.0
                self._safe_update(status_host)

            def _on_url_change(e):
                nav_url = _extract_nav_url(e)
                if nav_url and not _is_safe_native_url(nav_url):
                    self.close()
                    return
                if validate_https_url(nav_url):
                    subtitle_label.value = urllib.parse.urlsplit(nav_url).hostname or host
                    self._safe_update(subtitle_label)

            def _on_ended(e):
                status_bar.visible = False
                status_host.opacity = 0.0
                nav_url = _extract_nav_url(e)
                if nav_url and not _is_safe_native_url(nav_url):
                    self.close()
                    return
                if validate_https_url(nav_url):
                    subtitle_label.value = urllib.parse.urlsplit(nav_url).hostname or host
                self._safe_update(status_host)
                self._safe_update(subtitle_label)

            self._webview = flet_webview.WebView(
                url=self._url,
                bgcolor=C.BG,
                expand=True,
                prevent_links=["http://", "javascript:", "file:", "data:", "intent:"],
                on_page_started=_on_started,
                on_url_change=_on_url_change,
                on_page_ended=_on_ended,
            )
            body_host = _NativeWebViewHost(
                self._webview,
                expand=True,
                bgcolor=C.BG,
                border_radius=10,
                content=ft.Stack(
                    expand=True,
                    fit=ft.StackFit.EXPAND,
                    controls=[self._webview, status_host],
                ),
            )
        else:
            loading_row = ft.Row(
                controls=[
                    ft.ProgressRing(width=16, height=16, stroke_width=2, color=C.ACCENT),
                    ft.Text("Đang mở trong ứng dụng...", size=12, color=C.TEXT_SECONDARY),
                ],
                spacing=8,
            )
            reader_col = ft.Column(
                controls=[
                    loading_row,
                    ft.Text(
                        self._url,
                        size=11,
                        color=C.ACCENT,
                        selectable=True,
                    ),
                    ft.Text(
                        "Trình xem web nhúng trên máy tính chỉ hiển thị bản đọc nhanh "
                        "hoặc cần trình duyệt ngoài cho phiên đăng nhập/Zoom.",
                        size=12,
                        color=C.TEXT_SECONDARY,
                    ),
                    ft.Button(
                        "Mở bằng trình duyệt ngoài",
                        icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                        bgcolor=C.ACCENT,
                        color=C.ON_ACCENT,
                        height=44,
                        on_click=self._handle_open_external,
                    ),
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            )
            body_host.padding = ft.Padding.all(14)
            body_host.border = ft.Border.all(1, C.BORDER)
            body_host.content = reader_col
            self._schedule_preview_fetch(reader_col, title_label)

        try:
            page_w = float(getattr(self._page, "width", 0) or 0)
        except (TypeError, ValueError):
            page_w = 0.0
        try:
            page_h = float(getattr(self._page, "height", 0) or 0)
        except (TypeError, ValueError):
            page_h = 0.0
        dialog_w = max(280.0, min(900.0, page_w - 32.0)) if page_w > 0 else 640.0
        dialog_h = max(320.0, min(680.0, page_h * 0.82)) if page_h > 0 else 520.0

        def _on_key_down(event) -> None:
            if getattr(event, "key", "") == "Escape":
                self.close()

        self._dialog = ft.AlertDialog(
            modal=True,
            semantics_label=self._title,
            title=header,
            content=ft.KeyboardListener(
                autofocus=True,
                on_key_down=_on_key_down,
                content=ft.Container(
                    content=body_host,
                    width=dialog_w,
                    height=dialog_h,
                ),
            ),
            content_padding=ft.Padding.only(left=12, right=12, top=4, bottom=12),
            title_padding=ft.Padding.only(left=16, right=8, top=10, bottom=4),
            bgcolor=C.SURFACE,
            shape=ft.RoundedRectangleBorder(radius=14),
            on_dismiss=lambda _e: setattr(self, "_closed", True),
        )
        try:
            self._page.show_dialog(self._dialog)
            return True
        except Exception:
            logger.debug("Could not present InAppWebViewDialog", exc_info=True)
            return False

    def close(self) -> None:
        self._closed = True
        if self._embedded_host is not None:
            try:
                self._embedded_host.dispose()
            except Exception:
                pass
            self._embedded_host = None
            self._webview = None
        try:
            self._page.pop_dialog()
        except Exception:
            if self._dialog is not None:
                self._dialog.open = False
                self._safe_update(self._dialog)

    def _safe_update(self, control: ft.Control) -> None:
        try:
            control.update()
        except Exception:
            pass

    def _schedule_preview_fetch(
        self, reader_col: ft.Column, title_label: ft.Text
    ) -> None:
        async def _load() -> None:
            page_title = ""
            markdown = ""
            error_note = ""
            try:
                page_title, markdown = await asyncio.to_thread(
                    _fetch_https_preview, self._url
                )
            except Exception:
                error_note = (
                    "Trang này yêu cầu trình duyệt đầy đủ hoặc phiên đăng nhập trực tiếp."
                )
            if self._closed:
                return
            if page_title and self._title == "Trình duyệt trong ứng dụng":
                title_label.value = page_title
                self._safe_update(title_label)
            controls: list[ft.Control] = [
                ft.Text(self._url, size=11, color=C.ACCENT, selectable=True),
                ft.Button(
                    "Mở bằng trình duyệt ngoài",
                    icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                    bgcolor=C.ACCENT,
                    color=C.ON_ACCENT,
                    height=44,
                    on_click=self._handle_open_external,
                ),
            ]
            if markdown:
                controls.append(ft.Divider(height=1, color=C.BORDER))
                controls.append(
                    ft.Markdown(
                        markdown,
                        selectable=True,
                        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                    )
                )
            else:
                controls.append(
                    ft.Text(
                        error_note
                        or "Không có nội dung HTML tĩnh để hiển thị. Bạn có thể mở bằng trình duyệt ngoài.",
                        size=12,
                        color=C.TEXT_SECONDARY,
                    )
                )
            reader_col.controls = controls
            self._safe_update(reader_col)

        try:
            self._page.run_task(_load)
        except (AttributeError, RuntimeError, TypeError):
            logger.debug("Could not schedule in-app HTML preview", exc_info=True)

    def _handle_open_external(self, _event=None) -> None:
        if callable(self._on_open_external):
            self._on_open_external(self._url)
            return
        async def _launch() -> None:
            try:
                await ft.UrlLauncher().launch_url(self._url)
            except Exception:
                logger.debug("Could not launch external browser", exc_info=True)

        try:
            self._page.run_task(_launch)
        except (AttributeError, RuntimeError, TypeError):
            logger.debug("Could not schedule external browser launch", exc_info=True)

    async def _go_back(self, _event=None) -> None:
        if self._embedded_host is not None:
            self._embedded_host.go_back()
            return
        if self._webview is not None and await self._webview.can_go_back():
            await self._webview.go_back()

    async def _go_forward(self, _event=None) -> None:
        if self._embedded_host is not None:
            self._embedded_host.go_forward()
            return
        if self._webview is not None and await self._webview.can_go_forward():
            await self._webview.go_forward()

    async def _reload(self, _event=None) -> None:
        if self._embedded_host is not None:
            self._embedded_host.reload()
            return
        if self._webview is not None:
            await self._webview.reload()
