"""In-app path-safe archive tree explorer with breadcrumb navigation."""

from __future__ import annotations

from typing import Callable

import flet as ft

from core.courses.archive_service import (
    ArchiveNode,
    ArchiveSecurityError,
    ArchiveService,
    ArchiveTreeResult,
)
from core.courses.models import PreviewKind
from gui.components.courses._shared import (
    build_offline_banner,
    build_viewer_header,
    refresh_viewer_header,
    safe_control_update,
)
from gui.core.theme import C
from gui.view_models.courses_state import format_file_size

_KIND_ICONS: dict[PreviewKind, ft.IconData] = {
    "pdf": ft.Icons.PICTURE_AS_PDF_OUTLINED,
    "docx": ft.Icons.DESCRIPTION_OUTLINED,
    "xlsx": ft.Icons.TABLE_CHART_OUTLINED,
    "csv": ft.Icons.TABLE_CHART_OUTLINED,
    "pptx": ft.Icons.SLIDESHOW_OUTLINED,
    "image": ft.Icons.IMAGE_OUTLINED,
    "text": ft.Icons.DESCRIPTION_OUTLINED,
    "archive": ft.Icons.ARCHIVE_OUTLINED,
    "unsupported": ft.Icons.INSERT_DRIVE_FILE_OUTLINED,
}


def _inspect_safely(
    service: ArchiveService, archive_name: str, archive_bytes: bytes
) -> ArchiveTreeResult:
    try:
        return service.inspect_bytes(archive_name, archive_bytes)
    except (ArchiveSecurityError, ValueError, OSError, RuntimeError) as exc:
        return ArchiveTreeResult(
            file_name=archive_name or "archive",
            format_name="unknown",
            supported=False,
            root=ArchiveNode(name=archive_name or "archive", full_path="", is_dir=True),
            total_files=0,
            unsupported_reason=f"Không thể đọc tệp nén an toàn: {exc}",
        )


class ArchiveExplorerView(ft.Container):
    """Interactive breadcrumb explorer for ZIP, TAR.*, and optional desktop RAR."""

    def __init__(
        self,
        *,
        archive_name: str,
        archive_bytes: bytes,
        archive_service: ArchiveService,
        on_close: Callable[[], None],
        on_open_entry: Callable[[str, bytes, PreviewKind], None],
        on_save_raw: Callable[[str, bytes], None],
        **kwargs,
    ) -> None:
        super().__init__(expand=True, bgcolor=C.BG, **kwargs)
        self._name = archive_name or "archive"
        self._bytes = bytes(archive_bytes or b"")
        self._svc = archive_service
        self._on_close = on_close
        self._on_open_entry = on_open_entry
        self._on_save_raw = on_save_raw

        self._tree = _inspect_safely(self._svc, self._name, self._bytes)
        self._stack: list[ArchiveNode] = [self._tree.root]
        self._error_message: str = ""

        size_text = format_file_size(len(self._bytes)) or "0 B"
        subtitle_text = (
            f"{self._tree.total_files} tệp · {size_text}"
            if self._tree.supported
            else f"{self._tree.format_name.upper()} · {size_text}"
        )

        self._save_btn = ft.IconButton(
            icon=ft.Icons.SAVE_ALT_ROUNDED,
            tooltip="Lưu tệp nén gốc",
            icon_color=C.TEXT_PRIMARY,
            on_click=lambda _e: self._on_save_raw(self._name, self._bytes),
        )
        (
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        ) = build_viewer_header(
            title=self._name,
            subtitle=subtitle_text,
            back_tooltip="Quay lại",
            on_back=self._handle_back_btn,
            trailing_actions=[self._save_btn],
        )

        self._breadcrumb_row = ft.Row(scroll=ft.ScrollMode.AUTO, spacing=2)
        self._breadcrumb_bar = ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.only(bottom=ft.BorderSide(1, C.BORDER)),
            padding=ft.Padding.symmetric(horizontal=10, vertical=4),
            visible=self._tree.supported,
            content=self._breadcrumb_row,
        )
        self._banner_host = ft.Container(
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            visible=False,
        )
        self._body_host = ft.Container(expand=True)
        self.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                self._header_container,
                self._breadcrumb_bar,
                self._banner_host,
                self._body_host,
            ],
        )
        self._render()

    def _handle_back_btn(self, _e=None) -> None:
        if not self.handle_back():
            self._on_close()

    def handle_back(self) -> bool:
        """Pop the inner folder stack; return False if already at archive root."""
        if len(self._stack) > 1:
            self._stack.pop()
            self._error_message = ""
            self._render()
            safe_control_update(self)
            return True
        return False

    def _jump_to(self, idx: int) -> None:
        if 0 <= idx < len(self._stack):
            self._stack = self._stack[: idx + 1]
            self._error_message = ""
            self._render()
            safe_control_update(self)

    def _open_node(self, node: ArchiveNode) -> None:
        if node.is_dir:
            self._error_message = ""
            self._stack.append(node)
            self._render()
            safe_control_update(self)
            return

        try:
            extracted = self._svc.extract_entry_bytes(
                self._name, self._bytes, node.full_path
            )
        except Exception as exc:
            self._error_message = f"Không thể mở '{node.name}': {exc}"
            self._render()
            safe_control_update(self)
            return

        if self._error_message:
            self._error_message = ""
            self._render()
            safe_control_update(self)
        self._on_open_entry(node.name, extracted, node.preview_kind)

    def _build_breadcrumbs(self) -> list[ft.Control]:
        controls: list[ft.Control] = []
        last_idx = len(self._stack) - 1
        for idx, node in enumerate(self._stack):
            if idx > 0:
                controls.append(
                    ft.Icon(
                        ft.Icons.CHEVRON_RIGHT_ROUNDED,
                        size=15,
                        color=C.TEXT_SECONDARY,
                    )
                )
            label = "Gốc" if idx == 0 else node.name
            is_current = idx == last_idx
            controls.append(
                ft.TextButton(
                    label,
                    style=ft.ButtonStyle(
                        color=C.ACCENT if is_current else C.TEXT_SECONDARY,
                        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                    ),
                    on_click=lambda _e, target=idx: self._jump_to(target),
                )
            )
        return controls

    def _build_node_row(self, node: ArchiveNode) -> ft.Container:
        if node.is_dir:
            icon = ft.Icons.FOLDER_ROUNDED
            icon_color = C.ACCENT
            subtitle = f"{len(node.children)} mục"
            trailing: list[ft.Control] = [
                ft.Icon(
                    ft.Icons.CHEVRON_RIGHT_ROUNDED,
                    size=18,
                    color=C.TEXT_SECONDARY,
                )
            ]
        else:
            icon = _KIND_ICONS.get(node.preview_kind, ft.Icons.INSERT_DRIVE_FILE_OUTLINED)
            icon_color = C.TEXT_SECONDARY
            size_label = format_file_size(node.size) or "0 B"
            subtitle = f"{node.preview_kind.upper()} · {size_label}"
            trailing = []

        return ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            ink=True,
            on_click=lambda _e, target=node: self._open_node(target),
            content=ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(icon, size=20, color=icon_color),
                    ft.Column(
                        expand=True,
                        spacing=2,
                        controls=[
                            ft.Text(
                                node.name,
                                size=13,
                                weight=ft.FontWeight.W_500,
                                color=C.TEXT_PRIMARY,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                subtitle,
                                size=11,
                                color=C.TEXT_SECONDARY,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                    ),
                    *trailing,
                ],
            ),
        )

    def _build_unsupported_view(self) -> ft.Control:
        reason = (
            self._tree.unsupported_reason
            or "Định dạng nén chưa được hỗ trợ xem trực tiếp."
        )
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
                    ft.Icon(
                        ft.Icons.ARCHIVE_OUTLINED,
                        size=42,
                        color=C.WARNING,
                    ),
                    ft.Text(
                        self._name,
                        size=15,
                        weight=ft.FontWeight.W_600,
                        color=C.TEXT_PRIMARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        reason,
                        size=12,
                        color=C.TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Button(
                        "Lưu & Mở bằng ZArchiver / Files",
                        icon=ft.Icons.SAVE_ALT_ROUNDED,
                        bgcolor=C.ACCENT,
                        color=C.ON_ACCENT,
                        on_click=lambda _e: self._on_save_raw(
                            self._name, self._bytes
                        ),
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

    def _render(self) -> None:
        self._breadcrumb_bar.visible = self._tree.supported
        self._breadcrumb_row.controls = (
            self._build_breadcrumbs() if self._tree.supported else []
        )

        if self._error_message:
            self._banner_host.visible = True
            self._banner_host.content = build_offline_banner(
                self._error_message,
                icon=ft.Icons.ERROR_OUTLINE_ROUNDED,
            )
        else:
            self._banner_host.visible = False
            self._banner_host.content = None

        if not self._tree.supported:
            self._body_host.content = self._build_unsupported_view()
            return

        current = self._stack[-1]
        if not current.children:
            self._body_host.content = ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                padding=24,
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=8,
                    tight=True,
                    controls=[
                        ft.Icon(
                            ft.Icons.FOLDER_OPEN_OUTLINED,
                            size=38,
                            color=C.TEXT_SECONDARY,
                        ),
                        ft.Text(
                            "Thư mục trống",
                            size=13,
                            color=C.TEXT_SECONDARY,
                        ),
                    ],
                ),
            )
            return

        self._body_host.content = ft.ListView(
            expand=True,
            padding=12,
            spacing=8,
            controls=[self._build_node_row(child) for child in current.children],
        )

    def update_theme(self) -> None:
        """Apply current global theme tokens C and re-render rows."""
        self.bgcolor = C.BG
        refresh_viewer_header(
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        )
        self._save_btn.icon_color = C.TEXT_PRIMARY
        self._breadcrumb_bar.bgcolor = C.SURFACE
        self._breadcrumb_bar.border = ft.Border.only(
            bottom=ft.BorderSide(1, C.BORDER)
        )
        self._render()
        safe_control_update(self)
