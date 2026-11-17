"""In-app dual-tier document reader (Tier A WebView + Tier B native Flet)."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
import re
import threading
from typing import Callable, Optional

import flet as ft

from core.courses.document_converter import (
    ConvertedDocument,
    DocxRichBlock,
    DocumentOutlineItem,
    PptxSlideRich,
    SpreadsheetSheet,
    convert_document_bytes,
)
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.models import PreviewKind
from core.courses.pdf_renderer import PdfDocumentSession, PdfSearchHit
from gui.components.courses._shared import (
    build_expand_toggle_button,
    build_filter_chip,
    build_viewer_header,
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
        webview_builder: Optional[Callable[[str], Optional[ft.Control]]] = None,
        pdf_cache_dir: Optional[Path] = None,
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
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
        self._webview_builder = webview_builder
        self._pdf_cache_dir = pdf_cache_dir
        self._expanded = bool(is_expanded)
        self._show_expand_toggle = bool(show_expand_toggle)
        self._on_toggle_expand = on_toggle_expand

        self._dark_reader: bool = False
        self._sheet_index: int = 0
        self._sheet_row_page: int = 0
        self._sheet_col_page: int = 0
        self._sheet_filter_query: str = ""
        self._pdf_session: Optional[PdfDocumentSession] = None
        self._pdf_session_loaded: bool = False
        self._pdf_page_index: int = 0
        self._pdf_scale: float = 1.6
        self._pdf_outline_open: bool = False
        self._pdf_search_query: str = ""
        self._pdf_search_hits: list[PdfSearchHit] = []
        self._pdf_search_hit_pos: int = 0
        self._pdf_render_seq: int = 0
        self._disposed: bool = False
        self._converted_cache: dict[bool, ConvertedDocument] = {}
        self._html_urls: dict[bool, str] = {}
        self._pdf_url: Optional[str] = None
        self._active_webview_url: Optional[str] = None

        size_text = format_file_size(len(self._data)) or "0 B"
        self._night_btn = ft.IconButton(
            icon=ft.Icons.DARK_MODE_OUTLINED,
            tooltip="Chế độ đọc tối",
            icon_color=C.TEXT_SECONDARY,
            visible=self._kind in _NIGHT_MODE_KINDS,
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
        (
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        ) = build_viewer_header(
            title=self._file_name,
            subtitle=f"{self._kind.upper()} · {size_text}",
            back_tooltip="Đóng trình đọc",
            on_back=lambda _e: self._handle_close(),
            trailing_actions=[self._night_btn, self._expand_btn, self._save_btn, self._ext_btn],
        )

        self._body_host = ft.Container(expand=True)
        self._body_host.content = self._build_body()
        self.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[self._header_container, self._body_host],
        )

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
            url = self._preview_server.register_bytes(self._data, "application/pdf")
        except ValueError:
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

    def _build_webview(self, url: str) -> Optional[ft.Control]:
        if self._webview_builder is None:
            return None
        try:
            return self._webview_builder(url)
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
        self._dark_reader = not self._dark_reader
        self._sync_night_button()
        self._body_host.content = self._build_body()
        safe_control_update(self)

    def _select_sheet(self, index: int) -> None:
        self._sheet_index = max(0, index)
        self._sheet_row_page = 0
        self._sheet_col_page = 0
        self._sheet_filter_query = ""
        self._body_host.content = self._build_body()
        safe_control_update(self)

    def _handle_close(self) -> None:
        self.dispose()
        self._on_close()

    def dispose(self) -> None:
        """Release underlying PDF session handles and cancel background pre-rendering."""
        self._disposed = True
        self._pdf_render_seq += 1
        if self._pdf_session is not None:
            self._pdf_session.close()
            self._pdf_session = None

    def set_expanded(self, expanded: bool, *, visible: bool = True) -> None:
        self._expanded = bool(expanded)
        self._show_expand_toggle = bool(visible)
        sync_expand_toggle_button(
            self._expand_btn,
            expanded=self._expanded,
            visible=self._show_expand_toggle,
        )

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
            width=420,
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
            return ft.Column(expand=True, spacing=0, controls=controls)

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
            self._body_host.content = self._build_body()
            safe_control_update(self)

        def _step_row_page(delta: int) -> None:
            self._sheet_row_page = max(0, min(total_row_pages - 1, self._sheet_row_page + delta))
            self._body_host.content = self._build_body()
            safe_control_update(self)

        def _step_col_page(delta: int) -> None:
            self._sheet_col_page = max(0, min(total_col_pages - 1, self._sheet_col_page + delta))
            self._body_host.content = self._build_body()
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
        return ft.Column(expand=True, spacing=0, controls=controls)

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
        cards: list[ft.Control] = []
        for slide_num, body_text, notes_text in converted.slides:
            rich = rich_map.get(slide_num)
            card_controls: list[ft.Control] = [
                ft.Text(
                    f"Slide {slide_num}",
                    size=14,
                    weight=ft.FontWeight.W_700,
                    color=accent_color,
                ),
                ft.Text(
                    body_text or "(Slide không có văn bản)",
                    size=13,
                    color=text_primary if body_text else text_secondary,
                    selectable=True,
                ),
            ]
            if rich is not None and rich.tables:
                for tbl_rows in rich.tables:
                    if not tbl_rows:
                        continue
                    max_c = max(len(r) for r in tbl_rows)
                    if max_c <= 0:
                        continue
                    head_row = tbl_rows[0]
                    tbl_ctrl = ft.DataTable(
                        columns=[
                            ft.DataColumn(
                                ft.Text(
                                    head_row[c] if c < len(head_row) else "",
                                    size=12,
                                    weight=ft.FontWeight.W_600,
                                    color=text_primary,
                                )
                            )
                            for c in range(max_c)
                        ],
                        rows=[
                            ft.DataRow(
                                cells=[
                                    ft.DataCell(
                                        ft.Text(
                                            r[c] if c < len(r) else "",
                                            size=12,
                                            color=text_primary,
                                            selectable=True,
                                        )
                                    )
                                    for c in range(max_c)
                                ]
                            )
                            for r in tbl_rows[1:]
                        ],
                        bgcolor=bg_color,
                        heading_row_color=surface_color,
                        border=ft.Border.all(1, border_color),
                        border_radius=8,
                    )
                    card_controls.append(
                        ft.Row(scroll=ft.ScrollMode.AUTO, controls=[tbl_ctrl])
                    )
            if rich is not None and rich.images:
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
                                    width=360,
                                    fit=ft.BoxFit.CONTAIN,
                                ),
                            )
                            for _img_name, img_bytes in rich.images
                        ],
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
                    bgcolor=surface_color,
                    border=ft.Border.all(1, border_color),
                    border_radius=12,
                    padding=14,
                    content=ft.Column(spacing=8, controls=card_controls),
                )
            )
        list_view = ft.ListView(
            expand=True,
            build_controls_on_demand=False,
            padding=12,
            spacing=12,
            controls=cards,
        )
        if len(converted.outline) > 1:
            outline_bar = self._build_outline_bar(
                converted.outline,
                list_view,
                surface_color=surface_color,
                text_primary=text_primary,
                text_secondary=text_secondary,
                border_color=border_color,
            )
            return ft.Column(expand=True, spacing=0, controls=[outline_bar, list_view])
        return list_view

    def _build_md_style_sheet(
        self,
        *,
        surface_color: str,
        text_primary: str,
        text_secondary: str,
        border_color: str,
        accent_color: str,
    ) -> ft.MarkdownStyleSheet:
        return ft.MarkdownStyleSheet(
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

    def _build_docx_rich_body(self, converted: ConvertedDocument) -> ft.Control:
        """Render rich DOCX flow (headings, paragraphs, inline images, and tables)."""
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
        md_style = self._build_md_style_sheet(
            surface_color=surface_color,
            text_primary=text_primary,
            text_secondary=text_secondary,
            border_color=border_color,
            accent_color=accent_color,
        )

        controls: list[ft.Control] = []
        pending_md: list[str] = []
        pending_anchor: Optional[str] = None

        def _flush_md() -> None:
            nonlocal pending_anchor
            if not pending_md:
                return
            md_widget = ft.Markdown(
                "\n\n".join(pending_md),
                selectable=True,
                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                md_style_sheet=md_style,
            )
            controls.append(
                ft.Container(
                    key=ft.ScrollKey(pending_anchor) if pending_anchor else None,
                    content=md_widget,
                )
            )
            pending_md.clear()
            pending_anchor = None

        block: DocxRichBlock
        for block in converted.docx_blocks:
            if block.kind == "heading":
                _flush_md()
                pending_anchor = block.anchor_id or None
                pending_md.append(f"{'#' * max(1, block.level)} {block.text}")
                _flush_md()
            elif block.kind == "paragraph":
                pending_md.append(block.text)
            elif block.kind == "list_item":
                pending_md.append(f"- {block.text}")
            elif block.kind == "image" and block.image_bytes:
                _flush_md()
                controls.append(
                    ft.Container(
                        bgcolor=bg_color,
                        border=ft.Border.all(1, border_color),
                        border_radius=8,
                        padding=8,
                        alignment=ft.Alignment.CENTER,
                        content=ft.Image(
                            src=block.image_bytes,
                            fit=ft.BoxFit.CONTAIN,
                        ),
                    )
                )
            elif block.kind == "table" and block.rows:
                _flush_md()
                max_c = max(len(r) for r in block.rows)
                if max_c > 0:
                    head_row = block.rows[0]
                    tbl = ft.DataTable(
                        columns=[
                            ft.DataColumn(
                                ft.Text(
                                    head_row[c] if c < len(head_row) else "",
                                    size=12,
                                    weight=ft.FontWeight.W_600,
                                    color=text_primary,
                                )
                            )
                            for c in range(max_c)
                        ],
                        rows=[
                            ft.DataRow(
                                cells=[
                                    ft.DataCell(
                                        ft.Text(
                                            r[c] if c < len(r) else "",
                                            size=12,
                                            color=text_primary,
                                            selectable=True,
                                        )
                                    )
                                    for c in range(max_c)
                                ]
                            )
                            for r in block.rows[1:]
                        ],
                        bgcolor=bg_color,
                        heading_row_color=surface_color,
                        border=ft.Border.all(1, border_color),
                        border_radius=8,
                    )
                    controls.append(ft.Row(scroll=ft.ScrollMode.AUTO, controls=[tbl]))
        _flush_md()

        if not controls:
            return self._build_text_or_markdown_body(converted)

        list_view = ft.ListView(
            expand=True,
            build_controls_on_demand=False,
            spacing=10,
            controls=controls,
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
            return ft.Column(expand=True, spacing=0, controls=[outline_bar, surface_card])
        return surface_card

    def _ensure_pdf_session(self) -> PdfDocumentSession:
        if self._pdf_session is None and not self._pdf_session_loaded:
            self._pdf_session_loaded = True
            self._pdf_session = PdfDocumentSession(
                self._data, cache_dir=self._pdf_cache_dir
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
            if clamped == self._pdf_page_index:
                return
            self._pdf_page_index = clamped
            self._body_host.content = self._build_native_pdf_body(session)
            safe_control_update(self)

        def _adjust_zoom(delta: float) -> None:
            self._pdf_scale = max(0.8, min(2.8, round(self._pdf_scale + delta, 2)))
            self._body_host.content = self._build_native_pdf_body(session)
            safe_control_update(self)

        def _toggle_outline() -> None:
            self._pdf_outline_open = not self._pdf_outline_open
            self._body_host.content = self._build_native_pdf_body(session)
            safe_control_update(self)

        def _run_search(query_str: str) -> None:
            self._pdf_search_query = (query_str or "").strip()
            self._pdf_search_hits = session.search(self._pdf_search_query, max_hits=60)
            self._pdf_search_hit_pos = 0
            if self._pdf_search_hits:
                self._pdf_page_index = self._pdf_search_hits[0].page_index
            self._body_host.content = self._build_native_pdf_body(session)
            safe_control_update(self)

        def _step_hit(delta: int) -> None:
            if not self._pdf_search_hits:
                return
            self._pdf_search_hit_pos = (self._pdf_search_hit_pos + delta) % len(
                self._pdf_search_hits
            )
            self._pdf_page_index = self._pdf_search_hits[self._pdf_search_hit_pos].page_index
            self._body_host.content = self._build_native_pdf_body(session)
            safe_control_update(self)

        hit_badge = (
            f"{self._pdf_search_hit_pos + 1}/{len(self._pdf_search_hits)}"
            if self._pdf_search_hits
            else ("0/0" if self._pdf_search_query else "")
        )
        toolbar_controls: list[ft.Control] = []
        if session.outline:
            toolbar_controls.append(
                ft.IconButton(
                    icon=ft.Icons.LIST_ALT_ROUNDED,
                    icon_size=18,
                    icon_color=accent_color if self._pdf_outline_open else text_primary,
                    tooltip="Mục lục PDF",
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
                    on_click=lambda _e: _jump_pdf_page(self._pdf_page_index - 1),
                ),
                ft.TextField(
                    value=str(self._pdf_page_index + 1),
                    width=54,
                    height=32,
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
                ft.Text(f"/ {page_count}", size=12, color=text_secondary),
                ft.IconButton(
                    icon=ft.Icons.CHEVRON_RIGHT_ROUNDED,
                    icon_size=18,
                    icon_color=text_primary,
                    tooltip="Trang tiếp",
                    disabled=self._pdf_page_index >= page_count - 1,
                    on_click=lambda _e: _jump_pdf_page(self._pdf_page_index + 1),
                ),
                ft.IconButton(
                    icon=ft.Icons.ZOOM_OUT_ROUNDED,
                    icon_size=18,
                    icon_color=text_primary,
                    tooltip="Thu nhỏ",
                    on_click=lambda _e: _adjust_zoom(-0.25),
                ),
                ft.Text(f"{int(round((self._pdf_scale / 1.6) * 100))}%", size=11, color=text_secondary),
                ft.IconButton(
                    icon=ft.Icons.ZOOM_IN_ROUNDED,
                    icon_size=18,
                    icon_color=text_primary,
                    tooltip="Phóng to",
                    on_click=lambda _e: _adjust_zoom(0.25),
                ),
                ft.TextField(
                    value=self._pdf_search_query,
                    hint_text="Tìm trong PDF...",
                    width=170,
                    height=32,
                    dense=True,
                    text_size=12,
                    color=text_primary,
                    bgcolor=bg_color,
                    border_color=border_color,
                    on_submit=lambda e: _run_search(str(e.control.value or "")),
                ),
            ]
        )
        if hit_badge:
            toolbar_controls.extend(
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

        toolbar = ft.Container(
            bgcolor=surface_color,
            border=ft.Border.only(bottom=ft.BorderSide(1, border_color)),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            content=ft.Row(
                wrap=True,
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=toolbar_controls,
            ),
        )

        # Render 5-page batch centered on active page (active page rendered immediately, rest pre-warmed)
        batch_start = (self._pdf_page_index // 5) * 5
        batch_end = min(page_count, batch_start + 5)
        page_slots: dict[int, ft.Container] = {}
        page_cards: list[ft.Control] = []

        def _page_control_for(p_idx: int, rendered: Optional[object]) -> ft.Control:
            if rendered is not None and getattr(rendered, "png_bytes", b""):
                return ft.Container(
                    bgcolor=surface_color,
                    border=ft.Border.all(1, border_color),
                    border_radius=10,
                    padding=6,
                    content=ft.Column(
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=4,
                        controls=[
                            ft.Image(
                                src=rendered.png_bytes,  # type: ignore[attr-defined]
                                fit=ft.BoxFit.CONTAIN,
                            ),
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

        for p_idx in range(batch_start, batch_end):
            slot = ft.Container(
                key=ft.ScrollKey(f"pdf-page-{p_idx}"),
                alignment=ft.Alignment.CENTER,
            )
            if p_idx == self._pdf_page_index or not session.can_render_images:
                rendered = (
                    session.render_page_png(
                        p_idx, scale=self._pdf_scale, dark_mode=self._dark_reader
                    )
                    if session.can_render_images
                    else None
                )
                slot.content = _page_control_for(p_idx, rendered)
            else:
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
                            ft.Text(f"Đang tải trang {p_idx + 1}...", size=12, color=text_secondary),
                        ],
                    ),
                )
            page_slots[p_idx] = slot
            page_cards.append(slot)

        if session.can_render_images and (batch_end - batch_start > 1):
            remaining_indices = [i for i in range(batch_start, batch_end) if i != self._pdf_page_index]

            def _hydrate_batch() -> None:
                for p_idx in remaining_indices:
                    if self._disposed or self._pdf_render_seq != render_seq:
                        return
                    rendered = session.render_page_png(
                        p_idx, scale=self._pdf_scale, dark_mode=self._dark_reader
                    )
                    if self._disposed or self._pdf_render_seq != render_seq:
                        return
                    slot = page_slots.get(p_idx)
                    if slot is not None:
                        slot.content = _page_control_for(p_idx, rendered)
                        safe_control_update(slot)

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
                    spacing=12,
                    controls=nav_footer,
                )
            )

        pdf_viewer_col = ft.InteractiveViewer(
            expand=True,
            min_scale=0.6,
            max_scale=3.0,
            content=ft.ListView(
                expand=True,
                padding=14,
                spacing=12,
                controls=page_cards,
            ),
        )

        content_area: ft.Control = pdf_viewer_col
        if self._pdf_outline_open and session.outline:
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

        return ft.Column(expand=True, spacing=0, controls=[toolbar, content_area])

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
            (".md", ".markdown")
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

        if self._kind == "pdf":
            session = self._ensure_pdf_session()
            if session.supported and session.page_count > 0:
                size_text = format_file_size(len(self._data)) or "0 B"
                self._subtitle_text.value = (
                    f"PDF · {session.page_count} trang · {size_text}"
                )
                return self._build_native_pdf_body(session)
            if self._webview_builder is not None:
                pdf_url = self._try_register_pdf_url()
                if pdf_url:
                    wv = self._build_webview(pdf_url)
                    if wv is not None:
                        self._active_webview_url = pdf_url
                        return wv
            return self._build_fallback_card(
                icon=ft.Icons.PICTURE_AS_PDF_OUTLINED,
                title=self._file_name,
                subtitle=session.error_message
                or "Trình xem PDF nhúng chưa khả dụng trên nền tảng này.",
                button_label="Mở bằng ứng dụng PDF",
                on_primary=lambda: self._on_open_external(self._file_name, self._data),
            )

        if self._kind in {"docx", "xlsx", "csv", "pptx", "text"}:
            converted = self._get_converted()
            if converted.html_document and self._webview_builder is not None:
                html_url = self._try_register_html_url(
                    bool(self._dark_reader), converted.html_document
                )
                if html_url:
                    wv = self._build_webview(html_url)
                    if wv is not None:
                        self._active_webview_url = html_url
                        return wv

            if self._kind in {"xlsx", "csv"} and converted.sheets:
                return self._build_spreadsheet_body(converted)
            if self._kind == "pptx" and converted.slides:
                return self._build_pptx_body(converted)
            if self._kind == "docx" and any(
                b.kind in {"image", "table"} for b in (converted.docx_blocks or ())
            ):
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
            self._active_webview_url is not None
            and self._preview_server.has_active_url(self._active_webview_url)
            and (self._kind == "pdf" or self._dark_reader)
        )
        if not preserve_webview:
            self._body_host.content = self._build_body()
        safe_control_update(self)
