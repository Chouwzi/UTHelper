"""In-app dual-tier document reader (Tier A WebView + Tier B native Flet)."""

from __future__ import annotations

import asyncio
import inspect
import re
from typing import Callable, Optional

import flet as ft

from core.courses.document_converter import (
    ConvertedDocument,
    DocumentOutlineItem,
    SpreadsheetSheet,
    convert_document_bytes,
)
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.models import PreviewKind
from gui.components.courses._shared import build_filter_chip, safe_control_update
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
        if outline_idx < len(outline):
            item = outline[outline_idx]
            prefix = f"{'#' * max(1, item.level)} "
            expected = prefix + _WHITESPACE_RE.sub(" ", item.title.strip())
            normalized_block = _WHITESPACE_RE.sub(" ", block.strip())
            if normalized_block == expected:
                if current_blocks:
                    sections.append((current_anchor, "\n\n".join(current_blocks)))
                current_anchor = item.anchor_id
                current_blocks = [block]
                outline_idx += 1
                continue
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

        self._dark_reader: bool = False
        self._sheet_index: int = 0
        self._converted_cache: dict[bool, ConvertedDocument] = {}
        self._html_urls: dict[bool, str] = {}
        self._pdf_url: Optional[str] = None

        size_text = format_file_size(len(self._data)) or "0 B"
        self._back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_ROUNDED,
            tooltip="Đóng trình đọc",
            icon_color=C.TEXT_PRIMARY,
            on_click=lambda _e: self._on_close(),
        )
        self._title_text = ft.Text(
            self._file_name,
            size=15,
            weight=ft.FontWeight.W_600,
            color=C.TEXT_PRIMARY,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._subtitle_text = ft.Text(
            f"{self._kind.upper()} · {size_text}",
            size=11,
            color=C.TEXT_SECONDARY,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._night_btn = ft.IconButton(
            icon=ft.Icons.DARK_MODE_OUTLINED,
            tooltip="Chế độ đọc tối",
            icon_color=C.TEXT_SECONDARY,
            visible=self._kind in _NIGHT_MODE_KINDS,
            on_click=self._toggle_reader_mode,
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
        self._header_container = ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.only(bottom=ft.BorderSide(1, C.BORDER)),
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
            content=ft.Row(
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    self._back_btn,
                    ft.Column(
                        expand=True,
                        spacing=1,
                        controls=[self._title_text, self._subtitle_text],
                    ),
                    self._night_btn,
                    self._save_btn,
                    self._ext_btn,
                ],
            ),
        )
        self._body_host = ft.Container(expand=True, content=self._build_body())
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

    def _reader_palette(self) -> tuple[str, str, str, str, str]:
        if self._dark_reader:
            preset = THEME_PRESETS["material_graphite"]
            return (
                preset["bg"],
                preset["surface"],
                preset["text_primary"],
                preset["text_secondary"],
                preset["border"],
            )
        return C.BG, C.SURFACE, C.TEXT_PRIMARY, C.TEXT_SECONDARY, C.BORDER

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

    def _register_pdf_url(self) -> str:
        if self._pdf_url and self._preview_server.port > 0:
            return self._pdf_url
        url = self._preview_server.register_bytes(self._data, "application/pdf")
        self._pdf_url = url
        return url

    def _register_html_url(self, dark_mode: bool, html_document: str) -> str:
        cached = self._html_urls.get(dark_mode)
        if cached and self._preview_server.port > 0:
            return cached
        url = self._preview_server.register_html(html_document)
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
        self._body_host.content = self._build_body()
        safe_control_update(self)

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
        return ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            padding=24,
            content=ft.Container(
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
            ),
        )

    def _build_spreadsheet_body(self, converted: ConvertedDocument) -> ft.Control:
        bg_color, surface_color, text_primary, text_secondary, border_color = (
            self._reader_palette()
        )
        sheets = converted.sheets
        idx = min(self._sheet_index, len(sheets) - 1)
        self._sheet_index = max(0, idx)
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
                            )
                            for i, sh in enumerate(sheets)
                        ],
                    ),
                )
            )

        display_rows = sheet.rows[:_MAX_NATIVE_TABLE_ROWS]
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

        col_count = min(raw_max_cols, _MAX_NATIVE_TABLE_COLS)
        raw_header = display_rows[0]
        columns = [
            ft.DataColumn(
                ft.Text(
                    raw_header[c] if c < len(raw_header) else "",
                    size=12,
                    weight=ft.FontWeight.W_600,
                    color=text_primary,
                )
            )
            for c in range(col_count)
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
                    for c in range(col_count)
                ]
            )
            for row in display_rows[1:]
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
                    f"Đang hiển thị tối đa {_MAX_NATIVE_TABLE_ROWS} dòng × {_MAX_NATIVE_TABLE_COLS} cột để đảm bảo hiệu năng.",
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
        _bg, surface_color, text_primary, text_secondary, border_color = (
            self._reader_palette()
        )
        cards: list[ft.Control] = []
        for slide_num, body_text, notes_text in converted.slides:
            card_controls: list[ft.Control] = [
                ft.Text(
                    f"Slide {slide_num}",
                    size=14,
                    weight=ft.FontWeight.W_700,
                    color=C.ACCENT,
                ),
                ft.Text(
                    body_text or "(Slide không có văn bản)",
                    size=13,
                    color=text_primary if body_text else text_secondary,
                    selectable=True,
                ),
            ]
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

    def _build_text_or_markdown_body(self, converted: ConvertedDocument) -> ft.Control:
        _bg, surface_color, text_primary, text_secondary, border_color = (
            self._reader_palette()
        )
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
            blockquote_text_style=ft.TextStyle(color=text_secondary, size=13, italic=True),
            code_text_style=ft.TextStyle(
                color=text_primary, size=12, font_family=_MONO_FONT
            ),
            a_text_style=ft.TextStyle(color=C.ACCENT),
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
            return ft.Column(expand=True, spacing=0, controls=[outline_bar, surface_card])
        return surface_card

    def _build_body(self) -> ft.Control:
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
            return ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                padding=8,
                content=ft.InteractiveViewer(
                    expand=True,
                    min_scale=0.5,
                    max_scale=5.0,
                    content=ft.Image(src=self._data, fit=ft.BoxFit.CONTAIN),
                ),
            )

        if self._kind == "pdf":
            pdf_url = self._register_pdf_url()
            wv = self._build_webview(pdf_url)
            if wv is not None:
                return wv
            return self._build_fallback_card(
                icon=ft.Icons.PICTURE_AS_PDF_OUTLINED,
                title=self._file_name,
                subtitle="Trình xem PDF nhúng chưa khả dụng trên nền tảng này.",
                button_label="Mở bằng ứng dụng PDF",
                on_primary=lambda: self._on_open_external(self._file_name, self._data),
            )

        if self._kind in {"docx", "xlsx", "csv", "pptx", "text"}:
            converted = self._get_converted()
            if converted.html_document and self._webview_builder is not None:
                html_url = self._register_html_url(
                    bool(self._dark_reader), converted.html_document
                )
                wv = self._build_webview(html_url)
                if wv is not None:
                    return wv

            if self._kind in {"xlsx", "csv"} and converted.sheets:
                return self._build_spreadsheet_body(converted)
            if self._kind == "pptx" and converted.slides:
                return self._build_pptx_body(converted)
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
        self._header_container.bgcolor = C.SURFACE
        self._header_container.border = ft.Border.only(
            bottom=ft.BorderSide(1, C.BORDER)
        )
        self._back_btn.icon_color = C.TEXT_PRIMARY
        self._title_text.color = C.TEXT_PRIMARY
        self._subtitle_text.color = C.TEXT_SECONDARY
        self._save_btn.icon_color = C.TEXT_PRIMARY
        self._ext_btn.icon_color = C.TEXT_PRIMARY
        self._sync_night_button()
        self._body_host.content = self._build_body()
        safe_control_update(self)
