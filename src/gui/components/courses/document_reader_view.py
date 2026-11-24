"""In-app dual-tier document reader (Tier A WebView + Tier B native Flet)."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
import re
import threading
from typing import Any, Callable, Optional

import flet as ft

from core.courses.document_converter import (
    ConvertedDocument,
    DocxRichBlock,
    DocxTextRun,
    DocumentOutlineItem,
    PptxPlacedElement,
    PptxSlideRich,
    SpreadsheetSheet,
    convert_document_bytes,
)
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.models import PreviewKind
from core.courses.office_viewer_service import (
    OfficeViewerDescriptor,
    prepare_office_viewer,
    prepare_office_viewer_from_file,
    prepare_pdf_viewer,
)
from core.courses.pdf_renderer import PdfDocumentSession, PdfSearchHit
from gui.components.courses._shared import (
    build_expand_toggle_button,
    build_filter_chip,
    build_viewer_header,
    marshal_to_ui,
    refresh_viewer_header,
    safe_control_update,
    sync_expand_toggle_button,
)
from gui.core.theme import C
from gui.core.theme_presets import THEME_PRESETS
from gui.view_models.courses_state import format_file_size

_NIGHT_MODE_KINDS = frozenset({"docx", "xlsx", "csv", "pptx", "pdf"})
_MAX_NATIVE_TABLE_ROWS = 120
_MAX_NATIVE_TABLE_COLS = 40
_WHITESPACE_RE = re.compile(r"\s+")
_MONO_FONT = "Consolas, 'Courier New', monospace"
_SERIF_FONT = "Georgia, 'Times New Roman', Cambria, serif"


def _split_markdown_by_outline(
    markdown_text: str,
    outline: list[DocumentOutlineItem],
) -> list[tuple[Optional[str], str]]:
    """Split converted Markdown at outline headings so each heading has a ScrollKey."""
    if not outline or not markdown_text.strip():
        return [(None, markdown_text)]

    blocks = markdown_text.split("\n\n")
    sections: list[tuple[Optional[str], str]] = []
    current_anchor: Optional[str] = None
    current_blocks: list[str] = []
    outline_idx = 0

    for block in blocks:
        normalized_block = _WHITESPACE_RE.sub(" ", block.strip())
        matched = False
        for idx in range(outline_idx, len(outline)):
            item = outline[idx]
            prefix = f"{'#' * max(1, item.level)} "
            expected = prefix + _WHITESPACE_RE.sub(" ", item.title.strip())
            if normalized_block == expected:
                if current_blocks:
                    sections.append((current_anchor, "\n\n".join(current_blocks)))
                current_anchor = item.anchor_id
                current_blocks = [block]
                outline_idx = idx + 1
                matched = True
                break
        if not matched:
            current_blocks.append(block)

    if current_blocks:
        sections.append((current_anchor, "\n\n".join(current_blocks)))
    return sections or [(None, markdown_text)]


class DocumentReaderView(ft.Container):
    """Dual-tier in-app document viewer with dark/light mode and native fallbacks."""

    def __init__(
        self,
        *,
        file_name: str,
        data: bytes,
        kind: PreviewKind,
        preview_server: LocalPreviewServer,
        on_close: Callable[[], None],
        on_save: Callable[[str, bytes], None],
        on_open_external: Callable[[str, bytes], None],
        on_open_link: Optional[Callable[[str], None]] = None,
        webview_builder: Optional[Callable[[str], Optional[ft.Control]]] = None,
        pdf_cache_dir: Optional[Path] = None,
        preconverted: Optional[ConvertedDocument] = None,
        preopened_pdf: Optional[PdfDocumentSession] = None,
        rendered_pdf_bytes: Optional[bytes] = None,
        pdf_in_webview: bool = False,
        pane_width: float = 700.0,
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
        preprepared_office_viewer: Optional[OfficeViewerDescriptor] = None,
        cached_file_path: Optional[Path] = None,
        **kwargs,
    ) -> None:
        super().__init__(expand=True, bgcolor=C.BG, **kwargs)
        self._file_name = file_name or "Tài liệu"
        self._data = bytes(data or b"")
        self._kind: PreviewKind = kind
        self._preview_server = preview_server
        self._on_close = on_close
        self._on_save = on_save
        self._on_open_external = on_open_external
        self._on_open_link = on_open_link
        self._webview_builder = webview_builder
        self._pdf_cache_dir = pdf_cache_dir
        self._pane_width: float = max(320.0, float(pane_width or 700.0))
        self._expanded = bool(is_expanded)
        self._show_expand_toggle = bool(show_expand_toggle)
        self._on_toggle_expand = on_toggle_expand

        self._dark_reader: bool = False
        self._sheet_index: int = 0
        self._sheet_row_page: int = 0
        self._sheet_col_page: int = 0
        self._sheet_filter_query: str = ""
        self._doc_search_query: str = ""
        self._docx_scale: float = 1.0
        self._pptx_scale: float = 1.0
        self._pptx_page_index: int = 0
        self._pptx_search_query: str = ""
        self._pptx_outline_open: bool = False
        self._pdf_session: Optional[PdfDocumentSession] = preopened_pdf
        self._pdf_session_loaded: bool = preopened_pdf is not None
        self._rendered_pdf_bytes = rendered_pdf_bytes
        self._pdf_in_webview = pdf_in_webview
        self._pdf_page_index: int = 0
        self._pdf_scale: float = 1.6
        self._pdf_outline_open: bool = False
        self._pdf_outline_sheet: Optional[ft.BottomSheet] = None
        self._pdf_search_open: bool = False
        self._pdf_search_query: str = ""
        self._pdf_search_hits: list[PdfSearchHit] = []
        self._pdf_search_hit_pos: int = 0
        self._pdf_search_seq: int = 0
        self._pdf_render_seq: int = 0
        self._disposed: bool = False
        self._converted_cache: dict[bool, ConvertedDocument] = (
            {False: preconverted} if preconverted is not None else {}
        )
        self._html_urls: dict[bool, str] = {}
        self._pdf_url: Optional[str] = None
        self._active_webview_url: Optional[str] = None
        self._office_viewer: Optional[OfficeViewerDescriptor] = preprepared_office_viewer
        self._cached_file_path: Optional[Path] = (
            Path(cached_file_path) if cached_file_path is not None else None
        )
        self._office_webview: Optional[ft.Control] = None
        self._embedded_host = None
        self._webview_failed: bool = False
        self._viewer_watchdog: Optional[threading.Timer] = None
        self._viewer_watchdog_timeout: float = 14.0
        self._viewer_ready_received: bool = False
        self._webview_fallback_notice: str = ""

        size_text = format_file_size(len(self._data)) or "0 B"
        hide_night_for_wv_pdf = bool(
            (self._kind == "pdf" or self._rendered_pdf_bytes is not None)
            and self._pdf_in_webview
            and self._webview_builder is not None
        )
        self._night_btn = ft.IconButton(
            icon=ft.Icons.DARK_MODE_OUTLINED,
            tooltip="Chế độ đọc tối",
            icon_color=C.TEXT_SECONDARY,
            visible=(self._kind in _NIGHT_MODE_KINDS) and not hide_night_for_wv_pdf,
            on_click=self._toggle_reader_mode,
        )
        self._expand_btn = build_expand_toggle_button(
            expanded=self._expanded,
            on_toggle=lambda: self._on_toggle_expand() if callable(self._on_toggle_expand) else None,
            visible=self._show_expand_toggle,
        )
        self._save_btn = ft.IconButton(
            icon=ft.Icons.SAVE_ALT_ROUNDED,
            tooltip="Lưu tệp",
            icon_color=C.TEXT_PRIMARY,
            on_click=lambda _e: self._on_save(self._file_name, self._data),
        )
        self._ext_btn = ft.IconButton(
            icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
            tooltip="Mở bằng ứng dụng ngoài",
            icon_color=C.TEXT_PRIMARY,
            on_click=lambda _e: self._on_open_external(self._file_name, self._data),
        )
        kind_badge = (
            "HTML"
            if self._kind == "text" and self._file_name.lower().endswith((".html", ".htm"))
            else self._kind.upper()
        )
        (
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        ) = build_viewer_header(
            title=self._file_name,
            subtitle=f"{kind_badge} · {size_text}",
            back_tooltip="Đóng trình đọc",
            on_back=lambda _e: self._handle_close(),
            trailing_actions=[self._night_btn, self._expand_btn, self._save_btn, self._ext_btn],
        )

        self._body_switcher = ft.AnimatedSwitcher(
            content=ft.Container(),
            duration=150,
            reverse_duration=110,
            transition=ft.AnimatedSwitcherTransition.FADE,
            switch_in_curve=ft.AnimationCurve.EASE_OUT,
            switch_out_curve=ft.AnimationCurve.EASE_IN,
            expand=True,
        )
        self._body_host = ft.Container(expand=True, content=self._body_switcher)
        self._set_body_content(self._build_body())
        self.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[self._header_container, self._body_host],
        )

    def did_mount(self) -> None:
        self._schedule_alternate_theme_prewarm(force=True)

    def _set_body_content(self, ctrl: ft.Control) -> None:
        if (
            self._active_webview_url
            and getattr(self._embedded_host, "backend", None)
            in {"inappwebview", "flet_webview"}
        ):
            self._body_switcher.content = ft.Container()
            self._body_host.content = ctrl
            return
        self._body_switcher.content = ctrl
        if self._body_host.content is not self._body_switcher:
            self._body_host.content = self._body_switcher

    def _schedule_alternate_theme_prewarm(self, *, force: bool = False) -> None:
        """Warm the opposite dark/light conversion in a background thread so toggling is 0ms."""
        if not force and self._get_page() is None:
            return
        if self._kind not in _NIGHT_MODE_KINDS or self._kind == "pdf" or self._office_webview is not None:
            return
        alt_dark = not bool(self._dark_reader)
        if alt_dark in self._converted_cache:
            return

        def _warm() -> None:
            if self._disposed or alt_dark in self._converted_cache:
                return
            try:
                conv = convert_document_bytes(
                    self._file_name,
                    self._data,
                    self._kind,
                    dark_mode=alt_dark,
                )
                if not self._disposed:
                    self._converted_cache[alt_dark] = conv
            except Exception:
                pass

        threading.Thread(target=_warm, name="DocAltThemeWarm", daemon=True).start()

    def _get_page(self):
        try:
            return self.page
        except RuntimeError:
            return None

    def _reader_palette(self) -> tuple[str, str, str, str, str, str, str]:
        if self._dark_reader:
            preset = THEME_PRESETS["material_graphite"]
            return (
                preset["bg"],
                preset["surface"],
                preset["text_primary"],
                preset["text_secondary"],
                preset["border"],
                preset["accent"],
                preset["on_accent"],
            )
        return (
            C.BG,
            C.SURFACE,
            C.TEXT_PRIMARY,
            C.TEXT_SECONDARY,
            C.BORDER,
            C.ACCENT,
            C.ON_ACCENT,
        )

    def _get_converted(self) -> ConvertedDocument:
        key = bool(self._dark_reader)
        cached = self._converted_cache.get(key)
        if cached is None:
            cached = convert_document_bytes(
                self._file_name,
                self._data,
                self._kind,
                dark_mode=key,
            )
            self._converted_cache[key] = cached
        return cached

    def _try_register_pdf_url(self) -> Optional[str]:
        if self._pdf_url and self._preview_server.has_active_url(self._pdf_url):
            return self._pdf_url
        try:
            if (
                self._rendered_pdf_bytes is None
                and self._cached_file_path is not None
                and self._cached_file_path.is_file()
            ):
                url = self._preview_server.register_file(
                    self._cached_file_path, "application/pdf"
                )
            else:
                url = self._preview_server.register_bytes(
                    self._rendered_pdf_bytes or self._data, "application/pdf"
                )
        except (ValueError, OSError):
            self._pdf_url = None
            return None
        self._pdf_url = url
        return url

    def _try_register_html_url(
        self, dark_mode: bool, html_document: str
    ) -> Optional[str]:
        cached = self._html_urls.get(dark_mode)
        if cached and self._preview_server.has_active_url(cached):
            return cached
        try:
            url = self._preview_server.register_html(html_document)
        except ValueError:
            self._html_urls.pop(dark_mode, None)
            return None
        self._html_urls[dark_mode] = url
        return url

    def _cancel_viewer_watchdog(self) -> None:
        timer, self._viewer_watchdog = self._viewer_watchdog, None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _arm_viewer_watchdog(self, url: str, result: Any) -> None:
        self._cancel_viewer_watchdog()
        if self._disposed or self._viewer_ready_received:
            return
        if not any(
            host in url
            for host in ("docx_host.html", "pptx_host.html", "pdf_host.html")
        ):
            return
        if not (hasattr(result, "control") or hasattr(result, "eval_js")):
            return
        timeout_s = max(0.05, float(getattr(self, "_viewer_watchdog_timeout", 14.0)))
        timer = threading.Timer(
            timeout_s,
            lambda: self._fallback_from_webview("viewer_timeout"),
        )
        timer.daemon = True
        self._viewer_watchdog = timer
        timer.start()

    def _handle_viewer_message(self, raw_msg: str) -> None:
        if self._disposed:
            return
        try:
            payload = json.loads(raw_msg)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        msg_type = str(payload.get("type") or "").strip().lower()
        if msg_type == "ready":
            self._viewer_ready_received = True
            self._cancel_viewer_watchdog()
        elif msg_type == "error":
            self._cancel_viewer_watchdog()
            self._fallback_from_webview(str(payload.get("message") or "viewer_error"))

    def _fallback_from_webview(self, _reason: str = "") -> None:
        self._cancel_viewer_watchdog()

        def _apply() -> None:
            if self._disposed or self._webview_failed:
                return
            self._webview_failed = True
            self._office_webview = None
            self._active_webview_url = None
            if self._embedded_host is not None:
                try:
                    self._embedded_host.dispose()
                except Exception:
                    pass
                self._embedded_host = None
            self._night_btn.visible = self._kind in _NIGHT_MODE_KINDS
            if self._kind == "pptx" and _reason:
                self._webview_fallback_notice = (
                    "Đang dùng chế độ thẻ slide gọn nhẹ cho tệp lớn"
                )
            key = bool(self._dark_reader)
            page_live = self._get_page()
            if (
                self._kind in {"docx", "pptx"}
                and key not in self._converted_cache
                and page_live is not None
                and callable(getattr(page_live, "run_task", None))
            ):
                self._set_body_content(
                    ft.Container(
                        expand=True,
                        alignment=ft.Alignment.CENTER,
                        padding=24,
                        content=ft.Column(
                            tight=True,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=10,
                            controls=[
                                ft.ProgressRing(width=28, height=28, stroke_width=3, color=C.ACCENT),
                                ft.Text(
                                    "Đang chuyển sang chế độ đọc gọn nhẹ...",
                                    size=13,
                                    color=C.TEXT_SECONDARY,
                                ),
                            ],
                        ),
                    )
                )
                safe_control_update(self)

                async def _convert_and_mount() -> None:
                    if self._disposed:
                        return
                    conv = await asyncio.to_thread(
                        convert_document_bytes,
                        self._file_name,
                        self._data,
                        self._kind,
                        dark_mode=key,
                    )
                    if self._disposed:
                        return
                    self._converted_cache[key] = conv
                    self._set_body_content(self._build_body())
                    safe_control_update(self)

                try:
                    page_live.run_task(_convert_and_mount)
                    return
                except Exception:
                    pass
            self._set_body_content(self._build_body())
            safe_control_update(self)

        page = self._get_page()
        if page is not None and callable(getattr(page, "run_task", None)):
            try:
                asyncio.get_running_loop()
                _apply()
                return
            except RuntimeError:
                async def _run() -> None:
                    _apply()

                try:
                    page.run_task(_run)
                    return
                except Exception:
                    pass
        _apply()

    def _build_webview(self, url: str) -> Optional[ft.Control]:
        if self._webview_builder is None or self._webview_failed:
            return None
        try:
            self._viewer_ready_received = False
            try:
                result = self._webview_builder(
                    url,
                    on_error=self._fallback_from_webview,
                    on_message=self._handle_viewer_message,
                )
            except TypeError:
                try:
                    result = self._webview_builder(url, on_error=self._fallback_from_webview)
                except TypeError:
                    result = self._webview_builder(url)
            dock = getattr(result, "dock", None)
            if dock is not None and getattr(dock, "_failed", False) is True:
                dispose_fn = getattr(result, "dispose", None)
                if callable(dispose_fn):
                    dispose_fn()
                self._webview_failed = True
                return None
            control_fn = getattr(result, "control", None)
            if callable(control_fn):
                if self._embedded_host is not None and self._embedded_host is not result:
                    self._embedded_host.dispose()
                self._embedded_host = result
                self._arm_viewer_watchdog(url, result)
                return control_fn()
            if isinstance(result, ft.Control):
                self._arm_viewer_watchdog(url, result)
                return result
            return None
        except Exception:
            return None

    def _sync_night_button(self) -> None:
        self._night_btn.icon = (
            ft.Icons.LIGHT_MODE_OUTLINED
            if self._dark_reader
            else ft.Icons.DARK_MODE_OUTLINED
        )
        self._night_btn.tooltip = (
            "Chế độ đọc sáng" if self._dark_reader else "Chế độ đọc tối"
        )
        self._night_btn.icon_color = C.ACCENT if self._dark_reader else C.TEXT_SECONDARY

    def _toggle_reader_mode(self, _e=None) -> None:
        if (
            (self._kind == "pdf" or self._rendered_pdf_bytes is not None)
            and self._pdf_in_webview
            and not self._webview_failed
            and self._webview_builder is not None
        ):
            return
        self._dark_reader = not self._dark_reader
        self._sync_night_button()
        if self._office_webview is not None:
            evaluator = getattr(self._embedded_host or self._office_webview, "eval_js", None)
            if callable(evaluator):
                async def _set_office_theme() -> None:
                    mode = "dark" if self._dark_reader else "light"
                    result = evaluator(f"window.setTheme && window.setTheme('{mode}');")
                    if inspect.isawaitable(result):
                        await result

                page = self._get_page()
                if page is not None and callable(getattr(page, "run_task", None)):
                    page.run_task(_set_office_theme)
                else:
                    try:
                        asyncio.get_running_loop().create_task(_set_office_theme())
                    except RuntimeError:
                        asyncio.run(_set_office_theme())
                safe_control_update(self._night_btn)
                return
        target_mode = bool(self._dark_reader)
        # Offload uncached non-PDF conversion when mounted on a live page so UI never stutters
        live_page = self._get_page()
        if (
            self._kind != "pdf"
            and self._rendered_pdf_bytes is None
            and target_mode not in self._converted_cache
            and live_page is not None
        ):
            safe_control_update(self._night_btn)

            def _convert_async() -> None:
                if self._disposed:
                    return
                conv = convert_document_bytes(
                    self._file_name,
                    self._data,
                    self._kind,
                    dark_mode=target_mode,
                )

                def _apply_converted() -> None:
                    if self._disposed:
                        return
                    self._converted_cache[target_mode] = conv
                    if bool(self._dark_reader) == target_mode:
                        self._set_body_content(self._build_body())
                        safe_control_update(self)

                marshal_to_ui(self, _apply_converted, page=live_page)

            threading.Thread(target=_convert_async, name="DocThemeToggle", daemon=True).start()
            return

        self._set_body_content(self._build_body())
        safe_control_update(self)
        self._schedule_alternate_theme_prewarm()

    def _select_sheet(self, index: int) -> None:
        self._sheet_index = max(0, index)
        self._sheet_row_page = 0
        self._sheet_col_page = 0
        self._sheet_filter_query = ""
        self._set_body_content(self._build_body())
        safe_control_update(self)

    def _handle_close(self) -> None:
        self.dispose()
        self._on_close()

    def dispose(self) -> None:
        """Release underlying PDF session handles and cancel background pre-rendering."""
        self._disposed = True
        self._cancel_viewer_watchdog()
        self._close_pdf_outline_sheet()
        self._pdf_render_seq += 1
        self._pdf_search_seq += 1
        if self._embedded_host is not None:
            self._embedded_host.dispose()
            self._embedded_host = None
        if self._pdf_session is not None:
            self._pdf_session.close()
            self._pdf_session = None
        self._converted_cache.clear()
        self._rendered_pdf_bytes = None

    def set_expanded(self, expanded: bool, *, visible: bool = True) -> None:
        self._expanded = bool(expanded)
        self._show_expand_toggle = bool(visible)
        sync_expand_toggle_button(
            self._expand_btn,
            expanded=self._expanded,
            visible=self._show_expand_toggle,
        )

    def sync_bounds(
        self,
        _left: float,
        _top: float,
        width: float,
        _height: float,
        *,
        bottom_inset: float = 0.0,
    ) -> None:
        """Adapt document page width proportionally when Pane 3 resizes or expands."""
        if self._disposed:
            return
        if self._embedded_host is not None:
            inset = max(0.0, float(bottom_inset))
            if inset > 0.0:
                try:
                    self._embedded_host.sync_bounds(
                        _left,
                        _top + 55,
                        width,
                        max(80, _height - 55),
                        bottom_inset=inset,
                    )
                except TypeError:
                    self._embedded_host.sync_bounds(
                        _left, _top + 55, width, max(80, _height - 55)
                    )
            else:
                self._embedded_host.sync_bounds(
                    _left, _top + 55, width, max(80, _height - 55)
                )
        try:
            new_w = max(320.0, float(width))
        except (TypeError, ValueError):
            return
        if abs(new_w - self._pane_width) < 12.0:
            return
        self._pane_width = new_w
        if self._kind in {"docx", "doc", "pptx", "ppt", "pdf"} and not self._active_webview_url:
            self._set_body_content(self._build_body())
            safe_control_update(self)

    def suspend(self) -> None:
        if self._embedded_host is not None:
            self._embedded_host.suspend()

    def resume(self) -> None:
        if self._embedded_host is not None:
            self._embedded_host.resume()

    def _close_pdf_outline_sheet(self) -> None:
        sheet, self._pdf_outline_sheet = self._pdf_outline_sheet, None
        self._pdf_outline_open = False
        if sheet is not None:
            sheet.open = False
            safe_control_update(sheet)

    def handle_back(self) -> bool:
        if self._pdf_outline_open:
            self._close_pdf_outline_sheet()
        elif self._pdf_search_open:
            self._pdf_search_open = False
        else:
            return False
        self._set_body_content(self._build_body())
        safe_control_update(self)
        return True

    def handle_key_event(self, e: Any) -> bool:
        """Handle Esc-to-close and keyboard shortcuts inside the document reader."""
        if self._disposed:
            return False
        key = str(getattr(e, "key", "") or "")
        ctrl = bool(getattr(e, "ctrl", False) or getattr(e, "meta", False))
        if key == "Escape":
            if self.handle_back():
                return True
            self._handle_close()
            return True
        if ctrl and key.lower() == "f" and self._kind == "pdf" and not self._active_webview_url:
            self._pdf_search_open = not self._pdf_search_open
            self._set_body_content(self._build_body())
            safe_control_update(self)
            return True
        if key in ("ArrowLeft", "Page Up", "PageUp"):
            if self._kind == "pptx" and self._office_webview is None and self._pptx_page_index > 0:
                self._pptx_page_index -= 1
                self._set_body_content(self._build_body())
                safe_control_update(self)
                return True
            if self._kind == "pdf" and not self._active_webview_url and self._pdf_page_index > 0:
                self._pdf_page_index -= 1
                self._set_body_content(self._build_body())
                safe_control_update(self)
                return True
        if key in ("ArrowRight", "Page Down", "PageDown"):
            if self._kind == "pptx" and self._office_webview is None:
                converted = self._get_converted()
                if self._pptx_page_index + 1 < len(converted.slides):
                    self._pptx_page_index += 1
                    self._set_body_content(self._build_body())
                    safe_control_update(self)
                    return True
            if self._kind == "pdf" and not self._active_webview_url:
                sess = self._ensure_pdf_session()
                if sess.supported and self._pdf_page_index + 1 < sess.page_count:
                    self._pdf_page_index += 1
                    self._set_body_content(self._build_body())
                    safe_control_update(self)
                    return True
        return False

    def _jump_to_anchor(self, list_view: ft.ListView, anchor_id: str) -> None:
        async def _do_scroll() -> None:
            try:
                result = list_view.scroll_to(scroll_key=anchor_id, duration=200)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass

        page = self._get_page()
        run_task = getattr(page, "run_task", None) if page is not None else None
        if callable(run_task):
            run_task(_do_scroll)
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_scroll())
        except RuntimeError:
            pass

    def _build_outline_bar(
        self,
        outline: list[DocumentOutlineItem],
        list_view: ft.ListView,
        *,
        surface_color: str,
        text_primary: str,
        text_secondary: str,
        border_color: str,
    ) -> ft.Control:
        chips: list[ft.Control] = [
            ft.Chip(
                label=ft.Text(item.title, size=12, color=text_primary),
                bgcolor=surface_color,
                border_side=ft.BorderSide(1, border_color),
                on_click=lambda _e, aid=item.anchor_id: self._jump_to_anchor(
                    list_view, aid
                ),
            )
            for item in outline
        ]
        return ft.Container(
            bgcolor=surface_color,
            border=ft.Border.only(bottom=ft.BorderSide(1, border_color)),
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            content=ft.Row(
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text(
                        "Mục lục:",
                        size=12,
                        weight=ft.FontWeight.W_600,
                        color=text_secondary,
                    ),
                    ft.Row(
                        expand=True,
                        scroll=ft.ScrollMode.AUTO,
                        spacing=6,
                        controls=chips,
                    ),
                ],
            ),
        )

    def _build_fallback_card(
        self,
        *,
        icon: ft.IconData,
        title: str,
        subtitle: str,
        button_label: str,
        on_primary: Callable[[], None],
    ) -> ft.Control:
        card = ft.Container(
            width=min(420, max(240, self._pane_width - 32)),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=14,
            padding=24,
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
                tight=True,
                controls=[
                    ft.Icon(icon, size=44, color=C.ACCENT),
                    ft.Text(
                        title,
                        size=15,
                        weight=ft.FontWeight.W_600,
                        color=C.TEXT_PRIMARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        subtitle,
                        size=12,
                        color=C.TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=10,
                        wrap=True,
                        controls=[
                            ft.Button(
                                button_label,
                                icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                                bgcolor=C.ACCENT,
                                color=C.ON_ACCENT,
                                on_click=lambda _e: on_primary(),
                            ),
                            ft.OutlinedButton(
                                "Lưu tệp",
                                icon=ft.Icons.SAVE_ALT_ROUNDED,
                                on_click=lambda _e: self._on_save(
                                    self._file_name, self._data
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        )
        return ft.Column(
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            controls=[ft.Container(padding=24, content=card)],
        )

    def _build_spreadsheet_body(self, converted: ConvertedDocument) -> ft.Control:
        (
            bg_color,
            surface_color,
            text_primary,
            text_secondary,
            border_color,
            accent_color,
            on_accent_color,
        ) = self._reader_palette()
        self._body_host.bgcolor = bg_color
        sheets = converted.sheets
        self._sheet_index = max(0, min(self._sheet_index, len(sheets) - 1))
        sheet: SpreadsheetSheet = sheets[self._sheet_index]

        controls: list[ft.Control] = []
        if len(sheets) > 1:
            controls.append(
                ft.Container(
                    bgcolor=surface_color,
                    border=ft.Border.only(bottom=ft.BorderSide(1, border_color)),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                    content=ft.Row(
                        scroll=ft.ScrollMode.AUTO,
                        spacing=6,
                        controls=[
                            build_filter_chip(
                                sh.name,
                                selected=(i == self._sheet_index),
                                on_select=lambda _e, i=i: self._select_sheet(i),
                                bold_selected=True,
                                bgcolor=surface_color,
                                selected_color=accent_color,
                                text_color=text_primary,
                                selected_text_color=on_accent_color,
                                border_color=border_color,
                            )
                            for i, sh in enumerate(sheets)
                        ],
                    ),
                )
            )

        display_rows = sheet.rows
        raw_max_cols = max((len(r) for r in display_rows), default=0)
        if not display_rows or raw_max_cols == 0:
            controls.append(
                ft.Container(
                    expand=True,
                    alignment=ft.Alignment.CENTER,
                    padding=24,
                    content=ft.Container(
                        bgcolor=surface_color,
                        border=ft.Border.all(1, border_color),
                        border_radius=12,
                        padding=20,
                        content=ft.Text(
                            "Trang tính trống.",
                            size=13,
                            color=text_secondary,
                        ),
                    ),
                )
            )
            return ft.Column(
            expand=True,
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=controls,
        )

        raw_header = display_rows[0]
        data_rows = display_rows[1:]
        query_lower = self._sheet_filter_query.strip().lower()
        if query_lower:
            data_rows = [
                row
                for row in data_rows
                if any(query_lower in (cell or "").lower() for cell in row)
            ]

        rows_per_page = _MAX_NATIVE_TABLE_ROWS - 1  # 119 data rows + 1 header = 120
        total_row_pages = max(1, (len(data_rows) + rows_per_page - 1) // rows_per_page)
        self._sheet_row_page = max(0, min(self._sheet_row_page, total_row_pages - 1))
        r_start = self._sheet_row_page * rows_per_page
        r_end = min(len(data_rows), r_start + rows_per_page)
        visible_data_rows = data_rows[r_start:r_end]

        total_col_pages = max(
            1, (raw_max_cols + _MAX_NATIVE_TABLE_COLS - 1) // _MAX_NATIVE_TABLE_COLS
        )
        self._sheet_col_page = max(0, min(self._sheet_col_page, total_col_pages - 1))
        c_start = self._sheet_col_page * _MAX_NATIVE_TABLE_COLS
        c_end = min(raw_max_cols, c_start + _MAX_NATIVE_TABLE_COLS)

        def _set_sheet_filter(val: str) -> None:
            self._sheet_filter_query = val
            self._sheet_row_page = 0
            self._set_body_content(self._build_body())
            safe_control_update(self)

        def _step_row_page(delta: int) -> None:
            self._sheet_row_page = max(0, min(total_row_pages - 1, self._sheet_row_page + delta))
            self._set_body_content(self._build_body())
            safe_control_update(self)

        def _step_col_page(delta: int) -> None:
            self._sheet_col_page = max(0, min(total_col_pages - 1, self._sheet_col_page + delta))
            self._set_body_content(self._build_body())
            safe_control_update(self)

        toolbar_controls: list[ft.Control] = [
            ft.TextField(
                value=self._sheet_filter_query,
                hint_text="Lọc dòng...",
                dense=True,
                width=180,
                height=34,
                text_size=12,
                color=text_primary,
                bgcolor=bg_color,
                border_color=border_color,
                on_submit=lambda e: _set_sheet_filter(str(e.control.value or "")),
            ),
            ft.Text(
                f"Dòng {r_start + 1 if visible_data_rows else 0}–{r_end}/{len(data_rows)} · Cột {c_start + 1}–{c_end}/{raw_max_cols}",
                size=11,
                color=text_secondary,
            ),
        ]
        if total_row_pages > 1:
            toolbar_controls.extend(
                [
                    ft.IconButton(
                        icon=ft.Icons.CHEVRON_LEFT_ROUNDED,
                        icon_size=16,
                        icon_color=text_primary,
                        tooltip="Trang dòng trước",
                        disabled=self._sheet_row_page <= 0,
                        on_click=lambda _e: _step_row_page(-1),
                    ),
                    ft.Text(
                        f"Tr. {self._sheet_row_page + 1}/{total_row_pages}",
                        size=11,
                        color=text_primary,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CHEVRON_RIGHT_ROUNDED,
                        icon_size=16,
                        icon_color=text_primary,
                        tooltip="Trang dòng kế",
                        disabled=self._sheet_row_page >= total_row_pages - 1,
                        on_click=lambda _e: _step_row_page(1),
                    ),
                ]
            )
        if total_col_pages > 1:
            toolbar_controls.extend(
                [
                    ft.IconButton(
                        icon=ft.Icons.KEYBOARD_DOUBLE_ARROW_LEFT_ROUNDED,
                        icon_size=16,
                        icon_color=text_primary,
                        tooltip="Nhóm cột trước",
                        disabled=self._sheet_col_page <= 0,
                        on_click=lambda _e: _step_col_page(-1),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.KEYBOARD_DOUBLE_ARROW_RIGHT_ROUNDED,
                        icon_size=16,
                        icon_color=text_primary,
                        tooltip="Nhóm cột kế",
                        disabled=self._sheet_col_page >= total_col_pages - 1,
                        on_click=lambda _e: _step_col_page(1),
                    ),
                ]
            )
        controls.append(
            ft.Container(
                bgcolor=surface_color,
                border=ft.Border.only(bottom=ft.BorderSide(1, border_color)),
                padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                content=ft.Row(
                    wrap=True,
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=toolbar_controls,
                ),
            )
        )

        columns = [
            ft.DataColumn(
                ft.Text(
                    raw_header[c] if c < len(raw_header) else "",
                    size=12,
                    weight=ft.FontWeight.W_600,
                    color=text_primary,
                )
            )
            for c in range(c_start, c_end)
        ]
        rows = [
            ft.DataRow(
                cells=[
                    ft.DataCell(
                        ft.Text(
                            row[c] if c < len(row) else "",
                            size=12,
                            color=text_primary,
                            selectable=True,
                        )
                    )
                    for c in range(c_start, c_end)
                ]
            )
            for row in visible_data_rows
        ]
        table = ft.DataTable(
            columns=columns,
            rows=rows,
            bgcolor=bg_color,
            heading_row_color=surface_color,
            border=ft.Border.all(1, border_color),
            border_radius=8,
            horizontal_lines=ft.BorderSide(1, border_color),
            vertical_lines=ft.BorderSide(1, border_color),
            column_spacing=18,
        )

        list_items: list[ft.Control] = []
        if len(sheet.rows) > _MAX_NATIVE_TABLE_ROWS or raw_max_cols > _MAX_NATIVE_TABLE_COLS:
            list_items.append(
                ft.Text(
                    f"Đang hiển thị mỗi trang tối đa {_MAX_NATIVE_TABLE_ROWS} dòng × {_MAX_NATIVE_TABLE_COLS} cột để đảm bảo hiệu năng — dùng thanh phân trang phía trên để xem hết.",
                    size=11,
                    color=text_secondary,
                )
            )
        list_items.append(
            ft.Container(
                bgcolor=surface_color,
                border=ft.Border.all(1, border_color),
                border_radius=10,
                padding=8,
                content=ft.Row(scroll=ft.ScrollMode.AUTO, controls=[table]),
            )
        )
        controls.append(
            ft.ListView(
                expand=True,
                padding=12,
                spacing=8,
                controls=list_items,
            )
        )
        return ft.Column(
            expand=True,
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=controls,
        )

    def _handle_link_tap(self, url: str) -> None:
        cleaned = str(url or "").strip()
        if not cleaned or not callable(self._on_open_link):
            return
        self._on_open_link(cleaned)

    @staticmethod
    def _flet_text_align(align: str) -> ft.TextAlign:
        val = (align or "").strip().lower()
        if val == "center":
            return ft.TextAlign.CENTER
        if val == "right":
            return ft.TextAlign.RIGHT
        if val == "justify":
            return ft.TextAlign.JUSTIFY
        return ft.TextAlign.LEFT

    def _build_rich_runs_text(
        self,
        *,
        plain_text: str,
        runs: tuple[DocxTextRun, ...],
        size: int = 14,
        align: str = "left",
        text_primary: str,
        accent_color: str,
        base_bold: bool = False,
        base_italic: bool = False,
        scale: float = 1.0,
        first_line_px: int = 0,
        line_height: float = 0.0,
    ) -> ft.Control:
        """Build selectable Flet text control honoring inline styles, OOXML sizes, colors, fonts & indents."""
        t_align = self._flet_text_align(align)
        scaled_base = max(10, int(round(size * scale)))
        lead_spaces = (
            "\u00a0" * max(2, min(14, int(round((first_line_px * scale) / 4.2))))
            if first_line_px > 0
            else ""
        )
        eff_height = line_height if line_height >= 1.05 else None
        has_special = bool(lead_spaces) or any(
            r.bold
            or r.italic
            or r.underline
            or r.strike
            or r.code
            or r.superscript
            or r.subscript
            or r.href
            or r.font_size_pt > 0
            or r.color
            or r.bg_color
            or r.font_kind
            or "\t" in r.text
            for r in runs
        )
        if not runs or not has_special:
            return ft.Text(
                plain_text,
                size=scaled_base,
                weight=ft.FontWeight.W_700 if base_bold else ft.FontWeight.NORMAL,
                italic=base_italic,
                color=text_primary,
                text_align=t_align,
                selectable=True,
            )

        spans: list[ft.TextSpan] = []
        if lead_spaces:
            spans.append(ft.TextSpan(text=lead_spaces))
        for run in runs:
            decorations = []
            if run.underline or run.href:
                decorations.append(ft.TextDecoration.UNDERLINE)
            if run.strike:
                decorations.append(ft.TextDecoration.LINE_THROUGH)
            deco_val = (
                ft.TextDecoration.combine(decorations)
                if len(decorations) > 1
                else (decorations[0] if decorations else None)
            )
            # Convert OOXML pt -> logical px (~1.28x readability ratio), scaled by zoom
            run_base_px = (
                max(10, int(round(run.font_size_pt * 1.28 * scale)))
                if run.font_size_pt > 0
                else scaled_base
            )
            eff_size = (
                max(9, run_base_px - 3)
                if (run.superscript or run.subscript)
                else (max(10, run_base_px - 1) if run.code else run_base_px)
            )
            font_fam = (
                _MONO_FONT
                if (run.code or run.font_kind == "mono")
                else (_SERIF_FONT if run.font_kind == "serif" else None)
            )
            fg_col = accent_color if run.href else (run.color or text_primary)
            span_style = ft.TextStyle(
                color=fg_col,
                bgcolor=run.bg_color or None,
                size=eff_size,
                height=eff_height,
                weight=(
                    ft.FontWeight.W_700
                    if (run.bold or base_bold)
                    else ft.FontWeight.NORMAL
                ),
                italic=(run.italic or base_italic),
                font_family=font_fam,
                decoration=deco_val,
            )
            disp_text = run.text.replace("\t", "    ")
            if run.href and callable(self._on_open_link):
                spans.append(
                    ft.TextSpan(
                        text=disp_text,
                        style=span_style,
                        on_click=lambda _e, u=run.href: self._handle_link_tap(u),
                    )
                )
            else:
                spans.append(ft.TextSpan(text=disp_text, style=span_style))

        return ft.Text(
            spans=spans,
            size=scaled_base,
            color=text_primary,
            text_align=t_align,
            selectable=True,
        )

    def _build_grid_table_control(
        self,
        *,
        rich_rows: tuple[tuple[object, ...], ...] = (),
        plain_rows: tuple[tuple[str, ...], ...] = (),
        col_widths: tuple[float, ...] = (),
        has_borders: bool = True,
        scale: float = 1.0,
        bg_color: str,
        surface_color: str,
        text_primary: str,
        border_color: str,
        accent_color: str,
    ) -> ft.Control:
        """Render responsive Word/PPTX tables honoring column widths, vMerge, shading, and bottom rules."""
        grid_rows: list[ft.Control] = []
        source_rows = rich_rows if rich_rows else tuple(
            tuple(
                {
                    "text": c,
                    "runs": (),
                    "col_span": 1,
                    "align": "left",
                    "is_header": r_i == 0 and has_borders,
                    "images": (),
                }
                for c in row
            )
            for r_i, row in enumerate(plain_rows)
        )
        for r_idx, row in enumerate(source_rows):
            row_cells: list[ft.Control] = []
            logical_col = 0
            for cell in row:
                if isinstance(cell, dict):
                    c_text = str(cell["text"])
                    c_runs: tuple[DocxTextRun, ...] = ()
                    c_span = 1
                    c_vcont = False
                    c_align = str(cell["align"])
                    c_valign = "top"
                    c_header = bool(cell["is_header"])
                    c_bg = ""
                    c_brule = False
                    c_imgs: tuple[tuple[str, bytes], ...] = ()
                else:
                    c_text = str(getattr(cell, "text", ""))
                    c_runs = tuple(getattr(cell, "runs", ()))
                    c_span = max(1, int(getattr(cell, "col_span", 1)))
                    c_vcont = bool(getattr(cell, "is_vmerge_cont", False))
                    c_align = str(getattr(cell, "align", "left"))
                    c_valign = str(getattr(cell, "v_align", "top"))
                    c_header = bool(getattr(cell, "is_header", r_idx == 0 and has_borders))
                    c_bg = str(getattr(cell, "bg_color", "") or "")
                    c_brule = bool(getattr(cell, "has_bottom_rule", False))
                    c_imgs = tuple(getattr(cell, "images", ()))

                # Compute proportional flex weight from tblGrid col_widths when available
                if col_widths and logical_col < len(col_widths):
                    end_col = min(len(col_widths), logical_col + c_span)
                    weight_sum = sum(col_widths[logical_col:end_col]) or (0.1 * c_span)
                    flex_expand = max(1, int(round(weight_sum * 100)))
                else:
                    flex_expand = c_span * 10
                logical_col += c_span

                cell_children: list[ft.Control] = []
                if c_text or c_runs:
                    cell_children.append(
                        self._build_rich_runs_text(
                            plain_text=c_text,
                            runs=c_runs,
                            size=12,
                            align=c_align,
                            text_primary=text_primary,
                            accent_color=accent_color,
                            base_bold=(c_header and has_borders),
                            scale=scale,
                        )
                    )
                for _im_name, im_bytes in c_imgs:
                    if im_bytes:
                        cell_children.append(
                            ft.Image(
                                src=im_bytes,
                                width=max(80, int(round(180 * scale))),
                                fit=ft.BoxFit.CONTAIN,
                            )
                        )
                if not has_borders and c_brule:
                    # Iconic bottom rule (e.g., under "Độc lập - Tự do - Hạnh phúc")
                    cell_children.append(
                        ft.Container(
                            alignment=ft.Alignment.CENTER
                            if c_align == "center"
                            else ft.Alignment.CENTER_LEFT,
                            padding=ft.Padding.only(top=2),
                            content=ft.Container(
                                width=140 if c_align == "center" else None,
                                height=1,
                                bgcolor=text_primary,
                            ),
                        )
                    )

                if has_borders:
                    cell_border = ft.Border(
                        top=ft.BorderSide(0.0 if c_vcont else 0.5, border_color),
                        bottom=ft.BorderSide(0.5, border_color),
                        left=ft.BorderSide(0.5, border_color),
                        right=ft.BorderSide(0.5, border_color),
                    )
                else:
                    cell_border = None

                eff_cell_bg = c_bg or (
                    bg_color
                    if (c_header and has_borders)
                    else (surface_color if has_borders else None)
                )
                v_alignment = (
                    ft.MainAxisAlignment.CENTER
                    if c_valign == "center"
                    else (
                        ft.MainAxisAlignment.END
                        if c_valign == "bottom"
                        else ft.MainAxisAlignment.START
                    )
                )
                h_cell_pad = max(4, int(round((8 if has_borders else 6) * scale)))
                v_cell_pad = max(3, int(round((6 if has_borders else 4) * scale)))
                row_cells.append(
                    ft.Container(
                        expand=flex_expand,
                        padding=ft.Padding.symmetric(
                            horizontal=h_cell_pad,
                            vertical=v_cell_pad,
                        ),
                        bgcolor=eff_cell_bg,
                        border=cell_border,
                        content=ft.Column(
                            spacing=4,
                            alignment=v_alignment,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                            controls=cell_children,
                        ),
                    )
                )
            if row_cells:
                grid_rows.append(
                    ft.Row(
                        spacing=0 if has_borders else 10,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=row_cells,
                    )
                )
        table = ft.Container(
            border=ft.Border.all(1, border_color) if has_borders else None,
            border_radius=8 if has_borders else 0,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS if has_borders else ft.ClipBehavior.NONE,
            content=ft.Column(spacing=0 if has_borders else 4, controls=grid_rows),
        )
        column_count = max((sum(max(1, int(getattr(cell, "col_span", 1))) for cell in row) for row in source_rows), default=0)
        if self._pane_width < 600 and column_count >= 5:
            table.width = column_count * 100
            return ft.Row(scroll=ft.ScrollMode.AUTO, controls=[table])
        return table

    def _build_pptx_element_control(
        self,
        elem: PptxPlacedElement,
        *,
        slide_width: int,
        bg_color: str,
        surface_color: str,
        text_primary: str,
        border_color: str,
        accent_color: str,
    ) -> Optional[ft.Control]:
        if elem.kind == "text":
            p_controls: list[ft.Control] = []
            for p_obj in elem.paragraphs:
                if p_obj.is_title or p_obj.is_subtitle:
                    continue
                inner_ctrl = self._build_rich_runs_text(
                    plain_text=p_obj.text,
                    runs=p_obj.runs,
                    size=13,
                    align=p_obj.align,
                    text_primary=text_primary,
                    accent_color=accent_color,
                )
                if p_obj.bullet:
                    p_controls.append(
                        ft.Container(
                            padding=ft.Padding.only(left=p_obj.level * 14),
                            content=ft.Row(
                                spacing=6,
                                vertical_alignment=ft.CrossAxisAlignment.START,
                                controls=[
                                    ft.Text(
                                        p_obj.bullet,
                                        size=13,
                                        weight=ft.FontWeight.W_600,
                                        color=accent_color,
                                    ),
                                    ft.Container(expand=True, content=inner_ctrl),
                                ],
                            ),
                        )
                    )
                else:
                    p_controls.append(
                        ft.Container(
                            padding=ft.Padding.only(left=p_obj.level * 14),
                            content=inner_ctrl,
                        )
                    )
            if not p_controls:
                return None
            return ft.Column(spacing=4, controls=p_controls)

        if elem.kind == "table" and elem.table_rows:
            return self._build_grid_table_control(
                plain_rows=elem.table_rows,
                has_borders=True,
                bg_color=bg_color,
                surface_color=surface_color,
                text_primary=text_primary,
                border_color=border_color,
                accent_color=accent_color,
            )

        if elem.kind == "image" and elem.image_bytes:
            target_w = max(84, min(slide_width - 36, int(round(slide_width * elem.w_ratio))))
            return ft.Container(
                border=ft.Border.all(1, border_color),
                border_radius=8,
                padding=4,
                bgcolor=bg_color,
                alignment=ft.Alignment.CENTER,
                content=ft.Image(
                    src=elem.image_bytes,
                    width=target_w,
                    fit=ft.BoxFit.CONTAIN,
                ),
            )
        return None

    def _build_pptx_body(self, converted: ConvertedDocument) -> ft.Control:
        (
            bg_color,
            surface_color,
            text_primary,
            text_secondary,
            border_color,
            accent_color,
            _on_accent,
        ) = self._reader_palette()
        self._body_host.bgcolor = bg_color
        rich_map: dict[int, PptxSlideRich] = {
            item.slide_number: item for item in (converted.rich_slides or ())
        }
        total_slides = len(converted.slides)
        query_lower = self._pptx_search_query.strip().lower()
        avail_slide_w = max(300, int(self._pane_width) - 40)
        if self._pptx_scale <= 1.0:
            slide_width = max(280, min(avail_slide_w, int(round(min(avail_slide_w, 680) * self._pptx_scale))))
        else:
            slide_width = max(320, int(round(max(avail_slide_w, 680) * self._pptx_scale)))

        cards: list[ft.Control] = []
        matching_nums: list[int] = []
        for slide_num, body_text, notes_text in converted.slides:
            rich = rich_map.get(slide_num)
            haystack = "\n".join(
                p
                for p in (
                    rich.title_text if rich else "",
                    rich.subtitle_text if rich else "",
                    body_text,
                    notes_text,
                )
                if p
            ).lower()
            if query_lower and query_lower not in haystack:
                continue
            matching_nums.append(slide_num)

            ratio_label = ""
            if rich is not None:
                ratio_label = "4:3" if rich.aspect_ratio < 1.55 else "16:9"

            slide_badge_row = ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text(
                        f"Slide {slide_num}",
                        size=13,
                        weight=ft.FontWeight.W_700,
                        color=accent_color,
                    ),
                    *(
                        [ft.Text(ratio_label, size=10, color=text_secondary)]
                        if ratio_label
                        else []
                    ),
                ],
            )
            card_controls: list[ft.Control] = [slide_badge_row]

            rendered_rich_body = False
            if rich is not None:
                if rich.title_text:
                    card_controls.append(
                        ft.Text(
                            rich.title_text,
                            size=17,
                            weight=ft.FontWeight.W_700,
                            color=text_primary,
                            selectable=True,
                        )
                    )
                    rendered_rich_body = True
                if rich.subtitle_text:
                    card_controls.append(
                        ft.Text(
                            rich.subtitle_text,
                            size=14,
                            weight=ft.FontWeight.W_500,
                            color=text_secondary,
                            selectable=True,
                        )
                    )
                    rendered_rich_body = True

                placed_img_names: set[str] = set()
                elem_list = list(rich.elements or ())
                idx_el = 0
                while idx_el < len(elem_list):
                    cur = elem_list[idx_el]
                    if idx_el + 1 < len(elem_list):
                        nxt = elem_list[idx_el + 1]
                        same_band = abs(cur.y_ratio - nxt.y_ratio) <= 0.14
                        side_by_side = (
                            (cur.x_ratio + cur.w_ratio <= 0.58 and nxt.x_ratio >= 0.42)
                            or (nxt.x_ratio + nxt.w_ratio <= 0.58 and cur.x_ratio >= 0.42)
                        )
                        if same_band and side_by_side:
                            left_el, right_el = (cur, nxt) if cur.x_ratio <= nxt.x_ratio else (nxt, cur)
                            left_ctrl = self._build_pptx_element_control(
                                left_el,
                                slide_width=max(260, int(slide_width * 0.48)),
                                bg_color=bg_color,
                                surface_color=surface_color,
                                text_primary=text_primary,
                                border_color=border_color,
                                accent_color=accent_color,
                            )
                            right_ctrl = self._build_pptx_element_control(
                                right_el,
                                slide_width=max(260, int(slide_width * 0.48)),
                                bg_color=bg_color,
                                surface_color=surface_color,
                                text_primary=text_primary,
                                border_color=border_color,
                                accent_color=accent_color,
                            )
                            if left_el.kind == "image":
                                placed_img_names.add(left_el.image_name)
                            if right_el.kind == "image":
                                placed_img_names.add(right_el.image_name)
                            if left_ctrl is not None and right_ctrl is not None:
                                card_controls.append(
                                    ft.Row(
                                        spacing=14,
                                        vertical_alignment=ft.CrossAxisAlignment.START,
                                        controls=[
                                            ft.Container(expand=True, content=left_ctrl),
                                            ft.Container(expand=True, content=right_ctrl),
                                        ],
                                    )
                                )
                                rendered_rich_body = True
                                idx_el += 2
                                continue
                            elif left_ctrl is not None or right_ctrl is not None:
                                card_controls.append(left_ctrl or right_ctrl)  # type: ignore[arg-type]
                                rendered_rich_body = True
                                idx_el += 2
                                continue

                    built_ctrl = self._build_pptx_element_control(
                        cur,
                        slide_width=slide_width,
                        bg_color=bg_color,
                        surface_color=surface_color,
                        text_primary=text_primary,
                        border_color=border_color,
                        accent_color=accent_color,
                    )
                    if cur.kind == "image":
                        placed_img_names.add(cur.image_name)
                    if built_ctrl is not None:
                        card_controls.append(built_ctrl)
                        rendered_rich_body = True
                    idx_el += 1

                unplaced_images = [
                    (nm, bt) for nm, bt in (rich.images or ()) if nm not in placed_img_names and bt
                ]
                if unplaced_images:
                    card_controls.append(
                        ft.Row(
                            wrap=True,
                            spacing=8,
                            run_spacing=8,
                            controls=[
                                ft.Container(
                                    border=ft.Border.all(1, border_color),
                                    border_radius=8,
                                    padding=4,
                                    bgcolor=bg_color,
                                    content=ft.Image(
                                        src=img_bytes,
                                        width=min(360, slide_width - 40),
                                        fit=ft.BoxFit.CONTAIN,
                                    ),
                                )
                                for _img_name, img_bytes in unplaced_images
                            ],
                        )
                    )
                    rendered_rich_body = True

            if not rendered_rich_body:
                card_controls.append(
                    ft.Text(
                        body_text or "(Slide không có văn bản)",
                        size=13,
                        color=text_primary if body_text else text_secondary,
                        selectable=True,
                    )
                )
                if rich is not None and rich.tables:
                    for tbl_rows in rich.tables:
                        card_controls.append(
                            self._build_grid_table_control(
                                plain_rows=tbl_rows,
                                has_borders=True,
                                bg_color=bg_color,
                                surface_color=surface_color,
                                text_primary=text_primary,
                                border_color=border_color,
                                accent_color=accent_color,
                            )
                        )

            if notes_text:
                card_controls.extend(
                    [
                        ft.Divider(height=1, color=border_color),
                        ft.Text(
                            f"Ghi chú: {notes_text}",
                            size=12,
                            italic=True,
                            color=text_secondary,
                            selectable=True,
                        ),
                    ]
                )

            cards.append(
                ft.Container(
                    key=ft.ScrollKey(f"slide-{slide_num}"),
                    width=slide_width,
                    bgcolor=surface_color,
                    border=ft.Border.all(1, border_color),
                    border_radius=12,
                    padding=16,
                    content=ft.Column(spacing=10, controls=card_controls),
                )
            )

        if not cards:
            cards.append(
                ft.Container(
                    width=slide_width,
                    bgcolor=surface_color,
                    border=ft.Border.all(1, border_color),
                    border_radius=12,
                    padding=24,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text(
                        "Không tìm thấy slide phù hợp từ khóa tìm kiếm.",
                        size=13,
                        color=text_secondary,
                    ),
                )
            )

        list_view = ft.ListView(
            expand=True,
            build_controls_on_demand=False,
            padding=14,
            spacing=14,
            controls=[
                ft.Row(
                    scroll=ft.ScrollMode.AUTO,
                    alignment=ft.MainAxisAlignment.CENTER,
                    controls=[card],
                )
                if self._pptx_scale > 1.0
                else ft.Container(alignment=ft.Alignment.TOP_CENTER, content=card)
                for card in cards
            ],
        )

        def _jump_slide(target_idx: int) -> None:
            if not matching_nums:
                return
            self._pptx_page_index = max(0, min(len(matching_nums) - 1, int(target_idx)))
            target_slide_num = matching_nums[self._pptx_page_index]
            self._jump_to_anchor(list_view, f"slide-{target_slide_num}")

        def _adjust_pptx_zoom(delta: float) -> None:
            self._pptx_scale = max(0.7, min(1.5, round(self._pptx_scale + delta, 2)))
            self._set_body_content(self._build_pptx_body(converted))
            safe_control_update(self)

        def _reset_pptx_zoom() -> None:
            self._pptx_scale = 1.0
            self._set_body_content(self._build_pptx_body(converted))
            safe_control_update(self)

        def _set_pptx_search(q: str) -> None:
            self._pptx_search_query = str(q or "")
            self._pptx_page_index = 0
            self._set_body_content(self._build_pptx_body(converted))
            safe_control_update(self)

        toolbar = ft.Container(
            bgcolor=surface_color,
            border=ft.Border.only(bottom=ft.BorderSide(1, border_color)),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            content=ft.Row(
                wrap=True,
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.IconButton(
                        icon=ft.Icons.CHEVRON_LEFT_ROUNDED,
                        icon_size=18,
                        icon_color=text_primary,
                        tooltip="Slide trước",
                        disabled=len(matching_nums) <= 1,
                        on_click=lambda _e: _jump_slide(self._pptx_page_index - 1),
                    ),
                    ft.Text(
                        f"Slide {len(matching_nums)}/{total_slides}"
                        if query_lower
                        else f"{total_slides} slide",
                        size=12,
                        color=text_secondary,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CHEVRON_RIGHT_ROUNDED,
                        icon_size=18,
                        icon_color=text_primary,
                        tooltip="Slide tiếp",
                        disabled=len(matching_nums) <= 1,
                        on_click=lambda _e: _jump_slide(self._pptx_page_index + 1),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.ZOOM_OUT_ROUNDED,
                        icon_size=18,
                        icon_color=text_primary,
                        tooltip="Thu nhỏ slide",
                        on_click=lambda _e: _adjust_pptx_zoom(-0.15),
                    ),
                    ft.TextButton(
                        content=ft.Text(
                            f"{int(round(self._pptx_scale * 100))}%",
                            size=11,
                            color=text_primary,
                        ),
                        tooltip="Đặt lại 100%",
                        style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=6, vertical=0)),
                        on_click=lambda _e: _reset_pptx_zoom(),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.ZOOM_IN_ROUNDED,
                        icon_size=18,
                        icon_color=text_primary,
                        tooltip="Phóng to slide",
                        on_click=lambda _e: _adjust_pptx_zoom(0.15),
                    ),
                    ft.TextField(
                        value=self._pptx_search_query,
                        hint_text="Tìm trong slide...",
                        width=180,
                        height=32,
                        dense=True,
                        text_size=12,
                        color=text_primary,
                        bgcolor=bg_color,
                        border_color=border_color,
                        on_submit=lambda e: _set_pptx_search(str(e.control.value or "")),
                    ),
                ],
            ),
        )

        stack_parts: list[ft.Control] = [toolbar]
        if len(converted.outline) > 1:
            stack_parts.append(
                self._build_outline_bar(
                    converted.outline,
                    list_view,
                    surface_color=surface_color,
                    text_primary=text_primary,
                    text_secondary=text_secondary,
                    border_color=border_color,
                )
            )
        stack_parts.append(list_view)
        return ft.Column(
            expand=True,
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=stack_parts,
        )

    def _build_md_style_sheet(
        self,
        *,
        surface_color: str,
        text_primary: str,
        text_secondary: str,
        border_color: str,
        accent_color: str,
        scale: float = 1.0,
    ) -> ft.MarkdownStyleSheet:
        def _sz(pt: int) -> int:
            return max(10, int(round(pt * scale)))

        return ft.MarkdownStyleSheet(
            p_text_style=ft.TextStyle(color=text_primary, size=_sz(14)),
            h1_text_style=ft.TextStyle(
                color=text_primary, size=_sz(20), weight=ft.FontWeight.W_700
            ),
            h2_text_style=ft.TextStyle(
                color=text_primary, size=_sz(18), weight=ft.FontWeight.W_700
            ),
            h3_text_style=ft.TextStyle(
                color=text_primary, size=_sz(16), weight=ft.FontWeight.W_600
            ),
            h4_text_style=ft.TextStyle(
                color=text_primary, size=_sz(15), weight=ft.FontWeight.W_600
            ),
            h5_text_style=ft.TextStyle(
                color=text_primary, size=_sz(14), weight=ft.FontWeight.W_600
            ),
            h6_text_style=ft.TextStyle(
                color=text_secondary, size=_sz(13), weight=ft.FontWeight.W_600
            ),
            list_bullet_text_style=ft.TextStyle(color=text_primary, size=_sz(14)),
            table_head_text_style=ft.TextStyle(
                color=text_primary, size=_sz(13), weight=ft.FontWeight.W_600
            ),
            table_body_text_style=ft.TextStyle(color=text_primary, size=_sz(13)),
            table_cells_decoration=ft.BoxDecoration(
                border=ft.Border.all(1, border_color)
            ),
            blockquote_text_style=ft.TextStyle(
                color=text_secondary, size=_sz(13), italic=True
            ),
            blockquote_decoration=ft.BoxDecoration(
                bgcolor=surface_color,
                border=ft.Border.only(left=ft.BorderSide(3, accent_color)),
            ),
            blockquote_padding=10,
            code_text_style=ft.TextStyle(
                color=text_primary, size=_sz(12), font_family=_MONO_FONT
            ),
            codeblock_decoration=ft.BoxDecoration(
                bgcolor=surface_color,
                border=ft.Border.all(1, border_color),
                border_radius=8,
            ),
            codeblock_padding=10,
            horizontal_rule_decoration=ft.BoxDecoration(
                border=ft.Border.only(bottom=ft.BorderSide(1, border_color))
            ),
            a_text_style=ft.TextStyle(color=accent_color),
        )

    @staticmethod
    def _block_uses_markdown_plain(block: DocxRichBlock, *, scale: float = 1.0) -> bool:
        """Keep unstyled body paragraphs & left headings as Markdown scaled via md_style_sheet."""
        _ = scale
        if (
            block.section_role != "body"
            or block.align != "left"
            or block.indent_level > 0
            or block.indent_left_px > 0
            or block.indent_right_px > 0
            or block.first_line_px > 0
            or block.bg_color
            or block.has_bottom_border
            or block.has_box_border
        ):
            return False
        if block.kind == "list_item" and block.list_marker not in {"", "•"}:
            return False
        return not any(
            r.bold
            or r.italic
            or r.underline
            or r.strike
            or r.code
            or r.superscript
            or r.subscript
            or r.href
            or r.font_size_pt > 0
            or r.color
            or r.bg_color
            or r.font_kind
            for r in (block.runs or ())
        )

    def _open_print_html_in_webview(self, converted: ConvertedDocument) -> None:
        if not converted.html_document or not callable(self._on_open_link):
            return
        html_url = self._try_register_html_url(bool(self._dark_reader), converted.html_document)
        if html_url:
            self._on_open_link(html_url)

    def _build_docx_rich_body(self, converted: ConvertedDocument) -> ft.Control:
        """Render high-fidelity A4 Word pages (styles, theme colors, indents, tables, footnotes, zoom)."""
        (
            bg_color,
            surface_color,
            text_primary,
            text_secondary,
            border_color,
            accent_color,
            _on_accent,
        ) = self._reader_palette()
        self._body_host.bgcolor = bg_color
        user_zoom = self._docx_scale
        avail_w = max(300, int(self._pane_width) - 40)
        fit_ratio = min(1.0, avail_w / 794.0)
        scale = max(0.68, round(fit_ratio * user_zoom, 3))
        paper_h_pad = max(12, int(round(32 * scale)))
        paper_v_pad = max(14, int(round(24 * scale)))
        effective_card_w = (
            min(avail_w, int(round(min(avail_w, 794) * user_zoom)))
            if user_zoom <= 1.0
            else int(round(max(avail_w, 794) * user_zoom))
        )
        max_inner_img_w = max(140, effective_card_w - 2 * paper_h_pad - 20)
        md_style = self._build_md_style_sheet(
            surface_color=surface_color,
            text_primary=text_primary,
            text_secondary=text_secondary,
            border_color=border_color,
            accent_color=accent_color,
            scale=scale,
        )

        query_lower = self._doc_search_query.strip().lower()
        pages_controls: list[list[ft.Control]] = [[]]
        pending_md: list[str] = []
        pending_anchor: Optional[str] = None
        match_count = 0

        def _cur_page() -> list[ft.Control]:
            return pages_controls[-1]

        def _flush_md() -> None:
            nonlocal pending_anchor
            if not pending_md:
                return
            md_widget = ft.Markdown(
                "\n\n".join(pending_md),
                selectable=True,
                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                md_style_sheet=md_style,
                on_tap_link=lambda e: self._handle_link_tap(str(getattr(e, "data", "") or "")),
            )
            _cur_page().append(
                ft.Container(
                    key=ft.ScrollKey(pending_anchor) if pending_anchor else None,
                    content=md_widget,
                )
            )
            pending_md.clear()
            pending_anchor = None

        def _wrap_para_decoration(block: DocxRichBlock, inner: ft.Control) -> ft.Control:
            left_pad = int(round((max(block.indent_level * 20, block.indent_left_px)) * scale))
            right_pad = int(round(block.indent_right_px * scale))
            top_pad = int(round(block.space_before_px * scale * 0.6))
            bot_pad = int(round(block.space_after_px * scale * 0.6))
            bdr = None
            if block.has_box_border:
                bdr = ft.Border.all(1, border_color)
            elif block.has_bottom_border:
                bdr = ft.Border.only(bottom=ft.BorderSide(1, border_color))
            has_box_pad = bool(block.bg_color or block.has_box_border)
            return ft.Container(
                key=ft.ScrollKey(block.anchor_id) if block.anchor_id else None,
                margin=ft.Margin.only(top=top_pad, bottom=bot_pad) if (top_pad or bot_pad) else None,
                padding=ft.Padding(
                    left=left_pad + (8 if has_box_pad else 0),
                    right=right_pad + (8 if has_box_pad else 0),
                    top=6 if has_box_pad else 0,
                    bottom=6 if (has_box_pad or block.has_bottom_border) else 0,
                ),
                bgcolor=block.bg_color or None,
                border=bdr,
                border_radius=6 if has_box_pad else 0,
                content=inner,
            )

        for block in converted.docx_blocks:
            if block.kind == "page_break":
                if not query_lower and _cur_page():
                    _flush_md()
                    pages_controls.append([])
                continue

            if query_lower:
                if block.kind == "table":
                    hay = " ".join(cell for r in block.rows for cell in r).lower()
                else:
                    hay = (block.text or "").lower()
                if block.kind in {"image", "hr"} or query_lower not in hay:
                    continue
                match_count += 1

            # Soft-paginate very long unpaginated documents (~55 blocks/sheet) for smooth ListView virtualization
            if not query_lower and len(_cur_page()) >= 55:
                _flush_md()
                pages_controls.append([])

            if block.kind == "hr":
                _flush_md()
                _cur_page().append(ft.Divider(height=10, color=border_color))
            elif block.kind == "heading":
                _flush_md()
                if self._block_uses_markdown_plain(block, scale=scale):
                    pending_anchor = block.anchor_id or None
                    pending_md.append(f"{'#' * max(1, block.level)} {block.text}")
                    _flush_md()
                else:
                    h_size = max(14, 22 - max(1, block.level) * 2)
                    h_txt = self._build_rich_runs_text(
                        plain_text=block.text,
                        runs=block.runs,
                        size=h_size,
                        align=block.align,
                        text_primary=text_primary,
                        accent_color=accent_color,
                        base_bold=True,
                        scale=scale,
                    )
                    _cur_page().append(_wrap_para_decoration(block, h_txt))
            elif block.kind == "paragraph":
                if self._block_uses_markdown_plain(block, scale=scale):
                    pending_md.append(block.text)
                else:
                    _flush_md()
                    role_muted = block.section_role in {"header", "footer"}
                    para_ctrl = self._build_rich_runs_text(
                        plain_text=block.text,
                        runs=block.runs,
                        size=11 if role_muted else 14,
                        align=block.align,
                        text_primary=text_secondary if role_muted else text_primary,
                        accent_color=accent_color,
                        scale=scale,
                        first_line_px=0 if role_muted else block.first_line_px,
                        line_height=block.line_height,
                    )
                    if role_muted:
                        _cur_page().append(
                            ft.Container(
                                border=ft.Border.only(
                                    bottom=ft.BorderSide(0.5, border_color)
                                    if block.section_role == "header"
                                    else ft.BorderSide(0, border_color),
                                    top=ft.BorderSide(0.5, border_color)
                                    if block.section_role == "footer"
                                    else ft.BorderSide(0, border_color),
                                ),
                                padding=ft.Padding.symmetric(vertical=4),
                                content=para_ctrl,
                            )
                        )
                    else:
                        _cur_page().append(_wrap_para_decoration(block, para_ctrl))
            elif block.kind == "list_item":
                if self._block_uses_markdown_plain(block, scale=scale):
                    pending_md.append(f"- {block.text}")
                else:
                    _flush_md()
                    marker = block.list_marker or "•"
                    row_list = ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Text(
                                marker,
                                size=max(11, int(round(13 * scale))),
                                weight=ft.FontWeight.W_600,
                                color=accent_color,
                            ),
                            ft.Container(
                                expand=True,
                                content=self._build_rich_runs_text(
                                    plain_text=block.text,
                                    runs=block.runs,
                                    size=14,
                                    align=block.align,
                                    text_primary=text_primary,
                                    accent_color=accent_color,
                                    scale=scale,
                                    line_height=block.line_height,
                                ),
                            ),
                        ],
                    )
                    _cur_page().append(_wrap_para_decoration(block, row_list))
            elif block.kind == "image" and block.image_bytes:
                _flush_md()
                disp_w = (
                    max(48, min(max_inner_img_w, int(round(block.image_width_px * scale))))
                    if block.image_width_px > 0
                    else max_inner_img_w
                )
                img_align = (
                    ft.Alignment.CENTER
                    if block.align == "center"
                    else (
                        ft.Alignment.CENTER_RIGHT
                        if block.align == "right"
                        else ft.Alignment.CENTER
                    )
                )
                _cur_page().append(
                    ft.Container(
                        bgcolor=bg_color,
                        border=ft.Border.all(1, border_color),
                        border_radius=8,
                        padding=8,
                        alignment=img_align,
                        content=ft.Image(
                            src=block.image_bytes,
                            width=disp_w,
                            fit=ft.BoxFit.CONTAIN,
                        ),
                    )
                )
            elif block.kind == "table" and (block.rich_rows or block.rows):
                _flush_md()
                _cur_page().append(
                    self._build_grid_table_control(
                        rich_rows=block.rich_rows,
                        plain_rows=block.rows,
                        col_widths=block.col_widths,
                        has_borders=block.has_borders,
                        scale=scale,
                        bg_color=bg_color,
                        surface_color=surface_color,
                        text_primary=text_primary,
                        border_color=border_color,
                        accent_color=accent_color,
                    )
                )
        _flush_md()

        non_empty_pages = [p for p in pages_controls if p]
        if not non_empty_pages and not query_lower:
            return self._build_text_or_markdown_body(converted)
        if not non_empty_pages:
            non_empty_pages = [
                [
                    ft.Text(
                        "Không tìm thấy đoạn phù hợp từ khóa.",
                        size=13,
                        color=text_secondary,
                    )
                ]
            ]

        stretch_fluid = user_zoom <= 1.0 and avail_w <= 794 and abs(user_zoom - 1.0) < 0.01
        total_sheets = len(non_empty_pages)
        sheet_rows: list[ft.Control] = []
        for idx_p, p_ctrls in enumerate(non_empty_pages, start=1):
            sheet_footer = (
                [
                    ft.Container(
                        alignment=ft.Alignment.CENTER_RIGHT,
                        padding=ft.Padding.only(top=8),
                        content=ft.Text(
                            f"Trang {idx_p}/{total_sheets}",
                            size=10,
                            color=text_secondary,
                        ),
                    )
                ]
                if total_sheets > 1
                else []
            )
            page_card = ft.Container(
                width=None if stretch_fluid else effective_card_w,
                bgcolor=surface_color,
                border=ft.Border.all(1, border_color),
                border_radius=8,
                padding=ft.Padding.symmetric(horizontal=paper_h_pad, vertical=paper_v_pad),
                content=ft.Column(
                    spacing=8,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[*p_ctrls, *sheet_footer],
                ),
            )
            if stretch_fluid:
                sheet_rows.append(page_card)
            elif user_zoom > 1.0:
                sheet_rows.append(
                    ft.Row(
                        scroll=ft.ScrollMode.AUTO,
                        alignment=ft.MainAxisAlignment.CENTER,
                        controls=[page_card],
                    )
                )
            else:
                sheet_rows.append(
                    ft.Container(alignment=ft.Alignment.TOP_CENTER, content=page_card)
                )

        list_view = ft.ListView(
            expand=True,
            build_controls_on_demand=not bool(converted.outline),
            padding=14,
            spacing=14,
            controls=sheet_rows,
        )

        def _set_doc_search(q: str) -> None:
            self._doc_search_query = str(q or "")
            self._set_body_content(self._build_docx_rich_body(converted))
            safe_control_update(self)

        def _adjust_docx_zoom(delta: float) -> None:
            self._docx_scale = max(0.75, min(1.85, round(self._docx_scale + delta, 2)))
            self._set_body_content(self._build_docx_rich_body(converted))
            safe_control_update(self)

        def _reset_docx_zoom() -> None:
            self._docx_scale = 1.0
            self._set_body_content(self._build_docx_rich_body(converted))
            safe_control_update(self)

        meta_label = (
            f"Khớp: {match_count}"
            if query_lower
            else (
                f"{total_sheets} trang · {len(converted.outline)} đề mục"
                if converted.outline
                else f"Tài liệu Word · {total_sheets} trang"
            )
        )
        toolbar_items: list[ft.Control] = [
            ft.Text(meta_label, size=12, color=text_secondary),
            ft.IconButton(
                icon=ft.Icons.ZOOM_OUT_ROUNDED,
                icon_size=18,
                icon_color=text_primary,
                tooltip="Thu nhỏ trang Word",
                on_click=lambda _e: _adjust_docx_zoom(-0.15),
            ),
            ft.TextButton(
                content=ft.Text(
                    f"{int(round(user_zoom * 100))}%",
                    size=11,
                    color=text_primary,
                ),
                tooltip="Đặt lại 100%",
                style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=6, vertical=0)),
                on_click=lambda _e: _reset_docx_zoom(),
            ),
            ft.IconButton(
                icon=ft.Icons.ZOOM_IN_ROUNDED,
                icon_size=18,
                icon_color=text_primary,
                tooltip="Phóng to trang Word",
                on_click=lambda _e: _adjust_docx_zoom(0.15),
            ),
            ft.TextField(
                value=self._doc_search_query,
                hint_text="Tìm trong tài liệu Word...",
                width=200,
                height=32,
                dense=True,
                text_size=12,
                color=text_primary,
                bgcolor=bg_color,
                border_color=border_color,
                on_submit=lambda e: _set_doc_search(str(e.control.value or "")),
            ),
        ]
        if converted.html_document and callable(self._on_open_link):
            toolbar_items.append(
                ft.TextButton(
                    "Bản in A4 (WebView)",
                    icon=ft.Icons.PRINT_OUTLINED,
                    tooltip="Xem bố cục trang in HTML/CSS đầy đủ trong trình duyệt nội bộ",
                    on_click=lambda _e: self._open_print_html_in_webview(converted),
                )
            )

        doc_toolbar = ft.Container(
            bgcolor=surface_color,
            border=ft.Border.only(bottom=ft.BorderSide(1, border_color)),
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            content=ft.Row(
                wrap=True,
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=toolbar_items,
            ),
        )

        parts: list[ft.Control] = [doc_toolbar]
        if converted.outline:
            parts.append(
                self._build_outline_bar(
                    converted.outline,
                    list_view,
                    surface_color=surface_color,
                    text_primary=text_primary,
                    text_secondary=text_secondary,
                    border_color=border_color,
                )
            )
        parts.append(list_view)
        return ft.Column(
            expand=True,
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=parts,
        )

    def _ensure_pdf_session(self) -> PdfDocumentSession:
        if self._pdf_session is None and not self._pdf_session_loaded:
            self._pdf_session_loaded = True
            self._pdf_session = PdfDocumentSession(
                self._rendered_pdf_bytes or self._data, cache_dir=self._pdf_cache_dir
            )
        assert self._pdf_session is not None
        return self._pdf_session

    def _build_native_pdf_body(self, session: PdfDocumentSession) -> ft.Control:
        """Render interactive multi-page PDF reader inside Pane 3."""
        (
            bg_color,
            surface_color,
            text_primary,
            text_secondary,
            border_color,
            accent_color,
            _on_accent,
        ) = self._reader_palette()
        self._body_host.bgcolor = bg_color
        page_count = session.page_count
        self._pdf_page_index = max(0, min(self._pdf_page_index, page_count - 1))
        self._pdf_render_seq += 1
        render_seq = self._pdf_render_seq

        def _jump_pdf_page(target_idx: int) -> None:
            clamped = max(0, min(page_count - 1, int(target_idx)))
            self._close_pdf_outline_sheet()
            if clamped == self._pdf_page_index:
                return
            self._pdf_page_index = clamped
            self._set_body_content(self._build_native_pdf_body(session))
            safe_control_update(self)

        def _adjust_zoom(delta: float) -> None:
            self._pdf_scale = max(0.8, min(2.8, round(self._pdf_scale + delta, 2)))
            self._set_body_content(self._build_native_pdf_body(session))
            safe_control_update(self)

        def _reset_zoom() -> None:
            self._pdf_scale = 1.6
            self._set_body_content(self._build_native_pdf_body(session))
            safe_control_update(self)

        def _toggle_outline() -> None:
            if self._pane_width < 600:
                if self._pdf_outline_open:
                    self._close_pdf_outline_sheet()
                else:
                    self._pdf_outline_open = True

                    def _dismiss_outline(_e=None):
                        self._pdf_outline_open = False
                        self._pdf_outline_sheet = None

                    self._pdf_outline_sheet = ft.BottomSheet(
                        content=ft.Container(
                            height=360, padding=16, bgcolor=surface_color,
                            content=ft.ListView(controls=[
                                ft.TextButton(
                                    content=f"{entry.title} (tr.{entry.page_index + 1})",
                                    on_click=lambda _e, idx=entry.page_index: _jump_pdf_page(idx),
                                ) for entry in session.outline
                            ]),
                        ), on_dismiss=_dismiss_outline,
                    )
                    page = self._get_page()
                    if page is not None:
                        page.show_dialog(self._pdf_outline_sheet)
                return
            self._pdf_outline_open = not self._pdf_outline_open
            self._set_body_content(self._build_native_pdf_body(session))
            safe_control_update(self)

        def _run_search(query_str: str) -> None:
            clean_q = (query_str or "").strip()
            self._pdf_search_query = clean_q
            self._pdf_search_seq += 1
            search_seq = self._pdf_search_seq
            page = self._get_page()
            try:
                loop = getattr(page, "loop", None) if page is not None else None
            except Exception:
                return
            if (
                isinstance(loop, asyncio.AbstractEventLoop)
                and loop.is_running()
                and callable(getattr(page, "run_task", None))
            ):
                async def _search_async() -> None:
                    hits = await asyncio.to_thread(session.search, clean_q, max_hits=60)
                    if self._disposed or self._pdf_search_seq != search_seq:
                        return
                    self._pdf_search_hits = hits
                    self._pdf_search_hit_pos = 0
                    if self._pdf_search_hits:
                        self._pdf_page_index = self._pdf_search_hits[0].page_index
                    self._set_body_content(self._build_native_pdf_body(session))
                    safe_control_update(self)

                try:
                    page.run_task(_search_async)
                except Exception:
                    return
                return

            self._pdf_search_hits = session.search(clean_q, max_hits=60)
            self._pdf_search_hit_pos = 0
            if self._pdf_search_hits:
                self._pdf_page_index = self._pdf_search_hits[0].page_index
            self._set_body_content(self._build_native_pdf_body(session))
            safe_control_update(self)

        def _step_hit(delta: int) -> None:
            if not self._pdf_search_hits:
                return
            self._pdf_search_hit_pos = (self._pdf_search_hit_pos + delta) % len(
                self._pdf_search_hits
            )
            self._pdf_page_index = self._pdf_search_hits[self._pdf_search_hit_pos].page_index
            self._set_body_content(self._build_native_pdf_body(session))
            safe_control_update(self)

        hit_badge = (
            f"{self._pdf_search_hit_pos + 1}/{len(self._pdf_search_hits)}"
            if self._pdf_search_hits
            else ("0/0" if self._pdf_search_query else "")
        )
        is_compact = self._pane_width < 600
        compact_constraints = (
            ft.BoxConstraints(min_width=32, min_height=34, max_width=34, max_height=36)
            if is_compact
            else None
        )
        compact_style = (
            ft.ButtonStyle(padding=ft.Padding.all(2)) if is_compact else None
        )
        toolbar_controls: list[ft.Control] = []
        if session.outline:
            toolbar_controls.append(
                ft.IconButton(
                    icon=ft.Icons.LIST_ALT_ROUNDED,
                    icon_size=18,
                    icon_color=accent_color if self._pdf_outline_open else text_primary,
                    tooltip="Mục lục PDF",
                    size_constraints=compact_constraints,
                    style=compact_style,
                    on_click=lambda _e: _toggle_outline(),
                )
            )
        toolbar_controls.extend(
            [
                ft.IconButton(
                    icon=ft.Icons.CHEVRON_LEFT_ROUNDED,
                    icon_size=18,
                    icon_color=text_primary,
                    tooltip="Trang trước",
                    disabled=self._pdf_page_index <= 0,
                    size_constraints=compact_constraints,
                    style=compact_style,
                    on_click=lambda _e: _jump_pdf_page(self._pdf_page_index - 1),
                ),
                ft.TextField(
                    value=str(self._pdf_page_index + 1),
                    width=42 if is_compact else 54,
                    height=30 if is_compact else 32,
                    dense=True,
                    text_size=12,
                    text_align=ft.TextAlign.CENTER,
                    color=text_primary,
                    bgcolor=bg_color,
                    border_color=border_color,
                    on_submit=lambda e: _jump_pdf_page(
                        int(str(e.control.value or "1")) - 1
                        if str(e.control.value or "").strip().isdigit()
                        else self._pdf_page_index
                    ),
                ),
                ft.Text(f"/ {page_count}", size=11 if is_compact else 12, color=text_secondary),
                ft.IconButton(
                    icon=ft.Icons.CHEVRON_RIGHT_ROUNDED,
                    icon_size=18,
                    icon_color=text_primary,
                    tooltip="Trang tiếp",
                    disabled=self._pdf_page_index >= page_count - 1,
                    size_constraints=compact_constraints,
                    style=compact_style,
                    on_click=lambda _e: _jump_pdf_page(self._pdf_page_index + 1),
                ),
                ft.IconButton(
                    icon=ft.Icons.ZOOM_OUT_ROUNDED,
                    icon_size=18,
                    icon_color=text_primary,
                    tooltip="Thu nhỏ",
                    size_constraints=compact_constraints,
                    style=compact_style,
                    on_click=lambda _e: _adjust_zoom(-0.25),
                ),
                ft.TextButton(
                    content=ft.Text(
                        f"{int(round((self._pdf_scale / 1.6) * 100))}%",
                        size=11,
                        color=text_primary,
                    ),
                    tooltip="Đặt lại mức thu phóng 100%",
                    style=ft.ButtonStyle(
                        padding=ft.Padding.symmetric(
                            horizontal=4 if is_compact else 6, vertical=0
                        )
                    ),
                    on_click=lambda _e: _reset_zoom(),
                ),
                ft.IconButton(
                    icon=ft.Icons.ZOOM_IN_ROUNDED,
                    icon_size=18,
                    icon_color=text_primary,
                    tooltip="Phóng to",
                    size_constraints=compact_constraints,
                    style=compact_style,
                    on_click=lambda _e: _adjust_zoom(0.25),
                ),
            ]
        )
        search_field = ft.TextField(
            value=self._pdf_search_query,
            visible=(not is_compact) or self._pdf_search_open,
            hint_text="Tìm trong PDF...",
            width=None if is_compact else 170,
            expand=is_compact,
            height=32,
            dense=True,
            text_size=12,
            color=text_primary,
            bgcolor=bg_color,
            border_color=border_color,
            on_submit=lambda e: _run_search(str(e.control.value or "")),
        )
        hit_controls: list[ft.Control] = []
        if hit_badge:
            hit_controls.extend(
                [
                    ft.Text(hit_badge, size=11, color=text_secondary),
                    ft.IconButton(
                        icon=ft.Icons.ARROW_UPWARD_ROUNDED,
                        icon_size=15,
                        icon_color=text_primary,
                        tooltip="Kết quả trước",
                        disabled=not self._pdf_search_hits,
                        on_click=lambda _e: _step_hit(-1),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.ARROW_DOWNWARD_ROUNDED,
                        icon_size=15,
                        icon_color=text_primary,
                        tooltip="Kết quả tiếp",
                        disabled=not self._pdf_search_hits,
                        on_click=lambda _e: _step_hit(1),
                    ),
                ]
            )

        if is_compact:
            def _toggle_search(_e=None):
                self._pdf_search_open = not self._pdf_search_open
                self._set_body_content(self._build_native_pdf_body(session))
                safe_control_update(self)

            toolbar_controls.append(
                ft.IconButton(
                    icon=ft.Icons.SEARCH_ROUNDED,
                    icon_size=18,
                    tooltip="Tìm trong PDF",
                    icon_color=accent_color if self._pdf_search_open else text_primary,
                    size_constraints=compact_constraints,
                    style=compact_style,
                    on_click=_toggle_search,
                )
            )
            primary_row = ft.Row(
                wrap=False,
                spacing=2,
                scroll=ft.ScrollMode.AUTO,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=toolbar_controls,
            )
            search_subrow = ft.Row(
                visible=self._pdf_search_open or bool(hit_badge),
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[search_field, *hit_controls],
            )
            toolbar_content: ft.Control = ft.Column(
                spacing=4,
                tight=True,
                controls=[primary_row, search_subrow],
            )
        else:
            toolbar_controls.append(search_field)
            toolbar_controls.extend(hit_controls)
            toolbar_content = ft.Row(
                wrap=True,
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=toolbar_controls,
            )

        toolbar = ft.Container(
            bgcolor=surface_color,
            border=ft.Border.only(bottom=ft.BorderSide(1, border_color)),
            padding=ft.Padding.symmetric(horizontal=6 if is_compact else 10, vertical=4 if is_compact else 6),
            content=toolbar_content,
        )

        # Render 5-page batch centered on active page (active page rendered immediately, rest pre-warmed)
        batch_start = (self._pdf_page_index // 5) * 5
        batch_end = min(page_count, batch_start + 5)
        page_slots: dict[int, ft.Container] = {}
        page_cards: list[ft.Control] = []

        def _page_control_for(p_idx: int, rendered: Optional[object]) -> ft.Control:
            if rendered is not None and getattr(rendered, "png_bytes", b""):
                raw_disp_w = max(
                    280, int(round(getattr(rendered, "width", 800) * 0.68))
                )
                max_fit_pdf_w = max(280, int(self._pane_width) - 52)
                display_w = (
                    min(max_fit_pdf_w, raw_disp_w)
                    if self._pdf_scale <= 1.6
                    else raw_disp_w
                )
                img_ctrl = ft.Image(
                    src=rendered.png_bytes,  # type: ignore[attr-defined]
                    width=display_w,
                    fit=ft.BoxFit.CONTAIN,
                )
                body_item: ft.Control = (
                    ft.Row(
                        scroll=ft.ScrollMode.AUTO,
                        alignment=ft.MainAxisAlignment.CENTER,
                        controls=[img_ctrl],
                    )
                    if self._pdf_scale > 1.6
                    else img_ctrl
                )
                if self._pane_width < 600:
                    body_item = ft.Container(
                        width=display_w,
                        height=display_w * getattr(rendered, "height", 1000) / max(1, getattr(rendered, "width", 800)),
                        content=ft.InteractiveViewer(
                            min_scale=1, max_scale=4, content=img_ctrl,
                        ),
                    )
                return ft.Container(
                    bgcolor=surface_color,
                    border=ft.Border.all(1, border_color),
                    border_radius=10,
                    padding=6,
                    content=ft.Column(
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=4,
                        controls=[
                            body_item,
                            ft.Text(f"Trang {p_idx + 1}", size=11, color=text_secondary),
                        ],
                    ),
                )
            text_val = session.extract_page_text(p_idx)
            return ft.Container(
                bgcolor=surface_color,
                border=ft.Border.all(1, border_color),
                border_radius=10,
                padding=14,
                content=ft.Column(
                    spacing=6,
                    controls=[
                        ft.Text(
                            f"Trang {p_idx + 1}",
                            size=12,
                            weight=ft.FontWeight.W_600,
                            color=accent_color,
                        ),
                        ft.Text(
                            text_val or "(Trang này không có văn bản)",
                            size=13,
                            color=text_primary,
                            selectable=True,
                        ),
                    ],
                ),
            )

        remaining_indices: list[int] = []
        is_mounted_live = self._get_page() is not None
        cur_scale = self._pdf_scale
        cur_dark = bool(self._dark_reader)

        for p_idx in range(batch_start, batch_end):
            slot = ft.Container(
                key=ft.ScrollKey(f"pdf-page-{p_idx}"),
                alignment=ft.Alignment.CENTER,
            )
            if not session.can_render_images:
                slot.content = _page_control_for(p_idx, None)
            else:
                cached_now = session.peek_cached_page_png(
                    p_idx, scale=cur_scale, dark_mode=cur_dark
                )
                if cached_now is not None:
                    slot.content = _page_control_for(p_idx, cached_now)
                elif p_idx == self._pdf_page_index and not is_mounted_live:
                    rendered_sync = session.render_page_png(
                        p_idx, scale=cur_scale, dark_mode=cur_dark
                    )
                    slot.content = _page_control_for(p_idx, rendered_sync)
                else:
                    if p_idx == self._pdf_page_index:
                        remaining_indices.insert(0, p_idx)
                    else:
                        remaining_indices.append(p_idx)
                    slot.content = ft.Container(
                        bgcolor=surface_color,
                        border=ft.Border.all(1, border_color),
                        border_radius=10,
                        padding=24,
                        alignment=ft.Alignment.CENTER,
                        content=ft.Row(
                            tight=True,
                            spacing=8,
                            controls=[
                                ft.ProgressRing(width=18, height=18, stroke_width=2),
                                ft.Text(
                                    f"Đang kết xuất trang {p_idx + 1}...",
                                    size=12,
                                    color=text_secondary,
                                ),
                            ],
                        ),
                    )
            page_slots[p_idx] = slot
            page_cards.append(slot)

        if session.can_render_images:
            hydrate_page = self._get_page()
            all_batch_indices = [
                self._pdf_page_index,
                *[i for i in range(batch_start, batch_end) if i != self._pdf_page_index],
            ]

            def _hydrate_batch() -> None:
                for p_idx in remaining_indices:
                    if self._disposed or self._pdf_render_seq != render_seq:
                        return
                    rendered = session.render_page_png(
                        p_idx, scale=cur_scale, dark_mode=cur_dark
                    )
                    if self._disposed or self._pdf_render_seq != render_seq:
                        return
                    slot = page_slots.get(p_idx)
                    if slot is not None:
                        ctrl = _page_control_for(p_idx, rendered)

                        def _mount_slot(s=slot, c=ctrl) -> None:
                            if self._disposed or self._pdf_render_seq != render_seq:
                                return
                            s.content = c
                            safe_control_update(s)

                        marshal_to_ui(slot, _mount_slot, page=hydrate_page)
                # Pre-warm opposite dark/light mode in RAM so clicking Dark/Light is 0ms
                alt_dark = not cur_dark
                for p_idx in all_batch_indices:
                    if self._disposed or self._pdf_render_seq != render_seq:
                        return
                    if session.peek_cached_page_png(
                        p_idx, scale=cur_scale, dark_mode=alt_dark
                    ) is None:
                        session.render_page_png(
                            p_idx, scale=cur_scale, dark_mode=alt_dark
                        )

            threading.Thread(
                target=_hydrate_batch,
                name="PdfBatchHydrator",
                daemon=True,
            ).start()

        nav_footer: list[ft.Control] = []
        if batch_start > 0:
            nav_footer.append(
                ft.OutlinedButton(
                    "← 5 trang trước",
                    on_click=lambda _e, target=batch_start - 1: _jump_pdf_page(target),
                )
            )
        if batch_end < page_count:
            nav_footer.append(
                ft.FilledButton(
                    f"Xem tiếp trang {batch_end + 1}–{min(page_count, batch_end + 5)} →",
                    on_click=lambda _e, target=batch_end: _jump_pdf_page(target),
                )
            )
        if nav_footer:
            page_cards.append(
                ft.Row(
                    alignment=ft.MainAxisAlignment.CENTER,
                    wrap=True,
                    spacing=12,
                    controls=nav_footer,
                )
            )

        # Direct ListView ensures mouse wheel scrolling up/down never triggers unwanted scale zoom.
        pdf_viewer_col = ft.ListView(
            expand=True,
            padding=14,
            spacing=12,
            controls=page_cards,
        )

        content_area: ft.Control = pdf_viewer_col
        if self._pdf_outline_open and session.outline and self._pane_width >= 600:
            outline_panel = ft.Container(
                width=220,
                bgcolor=surface_color,
                border=ft.Border.only(right=ft.BorderSide(1, border_color)),
                padding=8,
                content=ft.ListView(
                    expand=True,
                    spacing=4,
                    controls=[
                        ft.TextButton(
                            content=f"{'  ' * min(3, entry.level)}{entry.title} (tr.{entry.page_index + 1})",
                            on_click=lambda _e, idx=entry.page_index: _jump_pdf_page(idx),
                        )
                        for entry in session.outline
                    ],
                ),
            )
            content_area = ft.Row(
                expand=True,
                spacing=0,
                controls=[outline_panel, ft.Container(expand=True, content=pdf_viewer_col)],
            )

        return ft.Column(
            expand=True,
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[toolbar, content_area],
        )

    def _build_text_or_markdown_body(self, converted: ConvertedDocument) -> ft.Control:
        (
            bg_color,
            surface_color,
            text_primary,
            text_secondary,
            border_color,
            accent_color,
            _on_accent,
        ) = self._reader_palette()
        self._body_host.bgcolor = bg_color
        is_plain_text = self._kind == "text" and not self._file_name.lower().endswith(
            (".md", ".markdown", ".html", ".htm")
        )
        if is_plain_text:
            code_box = ft.Container(
                bgcolor=surface_color,
                border=ft.Border.all(1, border_color),
                border_radius=10,
                padding=14,
                content=ft.Text(
                    converted.markdown_text or "(Tệp trống)",
                    size=13,
                    font_family=_MONO_FONT,
                    color=text_primary,
                    selectable=True,
                ),
            )
            return ft.ListView(expand=True, padding=12, controls=[code_box])

        md_style = ft.MarkdownStyleSheet(
            p_text_style=ft.TextStyle(color=text_primary, size=14),
            h1_text_style=ft.TextStyle(
                color=text_primary, size=20, weight=ft.FontWeight.W_700
            ),
            h2_text_style=ft.TextStyle(
                color=text_primary, size=18, weight=ft.FontWeight.W_700
            ),
            h3_text_style=ft.TextStyle(
                color=text_primary, size=16, weight=ft.FontWeight.W_600
            ),
            h4_text_style=ft.TextStyle(
                color=text_primary, size=15, weight=ft.FontWeight.W_600
            ),
            h5_text_style=ft.TextStyle(
                color=text_primary, size=14, weight=ft.FontWeight.W_600
            ),
            h6_text_style=ft.TextStyle(
                color=text_secondary, size=13, weight=ft.FontWeight.W_600
            ),
            list_bullet_text_style=ft.TextStyle(color=text_primary, size=14),
            table_head_text_style=ft.TextStyle(
                color=text_primary, size=13, weight=ft.FontWeight.W_600
            ),
            table_body_text_style=ft.TextStyle(color=text_primary, size=13),
            table_cells_decoration=ft.BoxDecoration(
                border=ft.Border.all(1, border_color)
            ),
            blockquote_text_style=ft.TextStyle(
                color=text_secondary, size=13, italic=True
            ),
            blockquote_decoration=ft.BoxDecoration(
                bgcolor=surface_color,
                border=ft.Border.only(left=ft.BorderSide(3, accent_color)),
            ),
            blockquote_padding=10,
            code_text_style=ft.TextStyle(
                color=text_primary, size=12, font_family=_MONO_FONT
            ),
            codeblock_decoration=ft.BoxDecoration(
                bgcolor=surface_color,
                border=ft.Border.all(1, border_color),
                border_radius=8,
            ),
            codeblock_padding=10,
            horizontal_rule_decoration=ft.BoxDecoration(
                border=ft.Border.only(bottom=ft.BorderSide(1, border_color))
            ),
            a_text_style=ft.TextStyle(color=accent_color),
        )
        sections = _split_markdown_by_outline(
            converted.markdown_text or "(Tài liệu trống)",
            converted.outline,
        )
        section_controls: list[ft.Control] = [
            ft.Container(
                key=ft.ScrollKey(anchor_id) if anchor_id else None,
                content=ft.Markdown(
                    md_chunk,
                    selectable=True,
                    extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                    md_style_sheet=md_style,
                    on_tap_link=lambda e: self._handle_link_tap(str(getattr(e, "data", "") or "")),
                ),
            )
            for anchor_id, md_chunk in sections
        ]
        list_view = ft.ListView(
            expand=True,
            build_controls_on_demand=False,
            spacing=10,
            controls=section_controls,
        )
        surface_card = ft.Container(
            expand=True,
            margin=12,
            bgcolor=surface_color,
            border=ft.Border.all(1, border_color),
            border_radius=12,
            padding=14,
            content=list_view,
        )
        if converted.outline:
            outline_bar = self._build_outline_bar(
                converted.outline,
                list_view,
                surface_color=surface_color,
                text_primary=text_primary,
                text_secondary=text_secondary,
                border_color=border_color,
            )
            return ft.Column(
                expand=True, spacing=0, controls=[outline_bar, surface_card]
            )
        return surface_card

    def _build_body(self) -> ft.Control:
        self._active_webview_url = None
        self._body_host.bgcolor = None

        if not self._data or self._kind == "unsupported":
            return self._build_fallback_card(
                icon=ft.Icons.INSERT_DRIVE_FILE_OUTLINED,
                title=self._file_name,
                subtitle=(
                    "Tệp trống."
                    if not self._data
                    else "Định dạng này chưa hỗ trợ đọc trực tiếp trong ứng dụng."
                ),
                button_label="Lưu & Mở bằng ứng dụng hệ thống",
                on_primary=lambda: self._on_open_external(self._file_name, self._data),
            )

        if self._kind == "image":
            image_error = ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=8,
                controls=[
                    ft.Icon(
                        ft.Icons.BROKEN_IMAGE_OUTLINED,
                        size=40,
                        color=C.TEXT_SECONDARY,
                    ),
                    ft.Text(
                        "Không thể hiển thị hình ảnh.",
                        size=13,
                        color=C.TEXT_SECONDARY,
                    ),
                ],
            )
            return ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                padding=8,
                content=ft.InteractiveViewer(
                    expand=True,
                    min_scale=0.5,
                    max_scale=5.0,
                    content=ft.Image(
                        src=self._data,
                        fit=ft.BoxFit.CONTAIN,
                        error_content=image_error,
                    ),
                ),
            )

        if self._kind == "pdf" or self._rendered_pdf_bytes is not None:
            if (
                self._pdf_in_webview
                and not self._webview_failed
                and self._webview_builder is not None
            ):
                pdf_url = self._try_register_pdf_url()
                webview = self._build_webview(pdf_url) if pdf_url else None
                if webview is not None:
                    self._night_btn.visible = False
                    self._active_webview_url = pdf_url
                    return webview
            if (
                self._office_viewer is not None
                and self._office_viewer.format == "pdf"
                and not self._webview_failed
                and self._webview_builder is not None
                and self._preview_server.has_active_url(self._office_viewer.blob_url)
            ):
                if self._office_webview is None:
                    self._office_webview = self._build_webview(
                        self._office_viewer.viewer_url
                    )
                if self._office_webview is not None:
                    self._night_btn.visible = True
                    self._active_webview_url = self._office_viewer.viewer_url
                    return self._office_webview
            session = self._ensure_pdf_session()
            if session.supported and session.page_count > 0 and session.can_render_images:
                size_text = format_file_size(len(self._data)) or "0 B"
                self._subtitle_text.value = (
                    f"{self._kind.upper()} · {session.page_count} trang · {size_text}"
                )
                self._night_btn.visible = True
                return self._build_native_pdf_body(session)
            if self._webview_builder is not None and not self._webview_failed:
                if (
                    self._office_viewer is None
                    or self._office_viewer.format != "pdf"
                    or not self._preview_server.has_active_url(self._office_viewer.blob_url)
                ):
                    pdf_payload = self._rendered_pdf_bytes or self._data
                    pdf_path = (
                        self._cached_file_path
                        if (self._rendered_pdf_bytes is None and self._kind == "pdf")
                        else None
                    )
                    self._office_viewer = prepare_pdf_viewer(
                        pdf_payload,
                        self._preview_server,
                        theme="dark" if self._dark_reader else "light",
                        file_path=pdf_path,
                    )
                    self._office_webview = None
                if self._office_viewer is not None:
                    if self._office_webview is None:
                        self._office_webview = self._build_webview(
                            self._office_viewer.viewer_url
                        )
                    if self._office_webview is not None:
                        self._night_btn.visible = True
                        self._active_webview_url = self._office_viewer.viewer_url
                        return self._office_webview
            if session.supported and session.page_count > 0:
                size_text = format_file_size(len(self._data)) or "0 B"
                self._subtitle_text.value = (
                    f"{self._kind.upper()} · {session.page_count} trang · {size_text}"
                )
                self._night_btn.visible = True
                return self._build_native_pdf_body(session)
            return self._build_fallback_card(
                icon=ft.Icons.PICTURE_AS_PDF_OUTLINED,
                title=self._file_name,
                subtitle=session.error_message
                or "Trình xem PDF nhúng chưa khả dụng trên nền tảng này.",
                button_label="Mở bằng ứng dụng PDF",
                on_primary=lambda: self._on_open_external(self._file_name, self._data),
            )

        if self._kind in {"docx", "xlsx", "csv", "pptx", "text"}:
            if (
                self._kind in {"docx", "pptx"}
                and not self._webview_failed
                and self._webview_builder is not None
            ):
                if self._office_viewer is None or not self._preview_server.has_active_url(self._office_viewer.blob_url):
                    if self._cached_file_path is not None and self._cached_file_path.is_file():
                        self._office_viewer = prepare_office_viewer_from_file(
                            self._cached_file_path,
                            self._file_name,
                            self._preview_server,
                            theme="dark" if self._dark_reader else "light",
                        )
                    else:
                        self._office_viewer = prepare_office_viewer(
                            self._data,
                            self._file_name,
                            self._preview_server,
                            theme="dark" if self._dark_reader else "light",
                        )
                    self._office_webview = None
                if self._office_viewer is not None:
                    if self._office_webview is None:
                        self._office_webview = self._build_webview(self._office_viewer.viewer_url)
                    if self._office_webview is not None:
                        self._active_webview_url = self._office_viewer.viewer_url
                        return self._office_webview
            converted = self._get_converted()
            if self._kind in {"xlsx", "csv"} and converted.sheets:
                return self._build_spreadsheet_body(converted)
            if (
                converted.html_document
                and not self._webview_failed
                and self._webview_builder is not None
            ):
                html_url = self._try_register_html_url(
                    bool(self._dark_reader), converted.html_document
                )
                if html_url:
                    wv = self._build_webview(html_url)
                    if wv is not None:
                        self._active_webview_url = html_url
                        return wv
            if self._kind == "pptx" and converted.slides:
                pptx_body = self._build_pptx_body(converted)
                if self._webview_fallback_notice:
                    banner = ft.Container(
                        bgcolor=C.SURFACE,
                        border=ft.Border.only(bottom=ft.BorderSide(1, C.BORDER)),
                        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                        content=ft.Row(
                            spacing=6,
                            controls=[
                                ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=15, color=C.ACCENT),
                                ft.Text(
                                    self._webview_fallback_notice,
                                    size=12,
                                    color=C.TEXT_SECONDARY,
                                    expand=True,
                                ),
                            ],
                        ),
                    )
                    return ft.Column(
                        expand=True,
                        spacing=0,
                        controls=[banner, ft.Container(expand=True, content=pptx_body)],
                    )
                return pptx_body
            if self._kind == "docx" and converted.docx_blocks:
                return self._build_docx_rich_body(converted)
            return self._build_text_or_markdown_body(converted)

        return self._build_fallback_card(
            icon=ft.Icons.INSERT_DRIVE_FILE_OUTLINED,
            title=self._file_name,
            subtitle="Định dạng này chưa hỗ trợ đọc trực tiếp trong ứng dụng.",
            button_label="Lưu & Mở bằng ứng dụng hệ thống",
            on_primary=lambda: self._on_open_external(self._file_name, self._data),
        )

    def update_theme(self) -> None:
        """Apply current global theme tokens while preserving reader mode & sheet state."""
        self.bgcolor = C.BG
        refresh_viewer_header(
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        )
        sync_expand_toggle_button(
            self._expand_btn,
            expanded=self._expanded,
            visible=self._show_expand_toggle,
        )
        self._save_btn.icon_color = C.TEXT_PRIMARY
        self._ext_btn.icon_color = C.TEXT_PRIMARY
        self._sync_night_button()

        preserve_webview = (
            self._office_webview is not None
            and self._office_viewer is not None
            and self._preview_server.has_active_url(self._office_viewer.blob_url)
        ) or (
            self._active_webview_url is not None
            and self._preview_server.has_active_url(self._active_webview_url)
            and (self._kind == "pdf" or self._dark_reader)
        )
        if not preserve_webview:
            self._set_body_content(self._build_body())
        safe_control_update(self)
