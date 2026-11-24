"""Shared Flet UI helper builders for Courses panes."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Literal, Optional

import flet as ft

from gui.core.theme import C


def marshal_to_ui(control: ft.BaseControl, fn: Callable[[], None]) -> None:
    """Run ``fn`` on the control's Flet asyncio loop when invoked off-loop."""
    try:
        page = control.page
    except Exception:
        page = None

    if page is not None:
        try:
            loop = getattr(page, "loop", None)
        except Exception:
            # Flet 0.86+ raises RuntimeError("An attempt to fetch destroyed session.")
            # once the Page session weakref expires; drop stale callbacks safely.
            return

        if isinstance(loop, asyncio.AbstractEventLoop):
            if loop.is_closed():
                return
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is loop:
                fn()
                return
            if loop.is_running():
                try:
                    loop.call_soon_threadsafe(fn)
                except RuntimeError:
                    pass
            return

    fn()


def safe_control_update(control: ft.BaseControl) -> None:
    """Update a Flet control only if it is currently attached to a Page."""
    def _do_update() -> None:
        try:
            if control.page is not None:
                control.update()
        except Exception:
            pass

    marshal_to_ui(control, _do_update)


def build_filter_chip(
    label: str,
    *,
    selected: bool,
    on_select: Callable[[Any], None],
    bold_selected: bool = False,
    bgcolor: Optional[str] = None,
    selected_color: Optional[str] = None,
    text_color: Optional[str] = None,
    selected_text_color: Optional[str] = None,
    border_color: Optional[str] = None,
) -> ft.Chip:
    """Build a themed selection filter chip without mixing click/select handlers."""
    weight = (
        ft.FontWeight.W_600 if (bold_selected and selected) else ft.FontWeight.W_400
    )
    active_accent = selected_color or C.ACCENT
    active_fg = selected_text_color or C.ON_ACCENT
    idle_fg = text_color or C.TEXT_PRIMARY
    idle_bg = bgcolor or C.SURFACE
    idle_border = border_color or C.BORDER
    return ft.Chip(
        label=ft.Text(
            label,
            size=12,
            weight=weight,
            color=active_fg if selected else idle_fg,
        ),
        selected=selected,
        show_checkmark=False,
        selected_color=active_accent,
        bgcolor=idle_bg,
        border_side=ft.BorderSide(1, active_accent if selected else idle_border),
        label_padding=ft.Padding.symmetric(horizontal=4, vertical=0),
        on_select=on_select,
    )


def build_offline_banner(
    message: str,
    *,
    icon: Optional[ft.IconData] = None,
    on_retry: Optional[Callable[[], None]] = None,
    retry_label: str = "Thử lại",
) -> ft.Container:
    """Build the warning banner displayed when offline or a provider/archive call fails."""
    row_controls: list[ft.Control] = [
        ft.Icon(icon or ft.Icons.WIFI_OFF_ROUNDED, size=16, color=C.WARNING),
        ft.Text(message, size=12, color=C.TEXT_PRIMARY, expand=True),
    ]
    if callable(on_retry):
        row_controls.append(
            ft.TextButton(
                retry_label,
                style=ft.ButtonStyle(
                    color=C.ACCENT,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                ),
                on_click=lambda _e: on_retry(),
            )
        )
    return ft.Container(
        bgcolor=C.SURFACE,
        border=ft.Border.all(1, C.WARNING),
        border_radius=10,
        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        content=ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=row_controls,
        ),
    )


def build_viewer_header(
    *,
    title: str,
    subtitle: str,
    back_tooltip: str,
    on_back: Callable[[Any], None],
    trailing_actions: list[ft.Control],
) -> tuple[ft.Container, ft.IconButton, ft.Text, ft.Text]:
    """Build a shared top header bar for in-app Courses reader/explorer overlays."""
    back_btn = ft.IconButton(
        icon=ft.Icons.ARROW_BACK_ROUNDED,
        tooltip=back_tooltip,
        icon_color=C.TEXT_PRIMARY,
        icon_size=20,
        padding=ft.Padding.all(4),
        size_constraints=ft.BoxConstraints(min_width=44, min_height=44),
        on_click=on_back,
    )
    title_text = ft.Text(
        title,
        size=15,
        weight=ft.FontWeight.W_600,
        color=C.TEXT_PRIMARY,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    subtitle_text = ft.Text(
        subtitle,
        size=11,
        color=C.TEXT_SECONDARY,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    header_controls: list[ft.Control] = [
        back_btn,
        ft.Column(
            expand=True,
            spacing=1,
            controls=[title_text, subtitle_text],
        ),
    ]
    if trailing_actions:
        header_controls.append(
            ft.Row(
                spacing=0,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=list(trailing_actions),
            )
        )
    container = ft.Container(
        bgcolor=C.SURFACE,
        border=ft.Border.only(bottom=ft.BorderSide(1, C.BORDER)),
        padding=ft.Padding.symmetric(horizontal=6, vertical=4),
        content=ft.Row(
            spacing=2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=header_controls,
        ),
    )
    return container, back_btn, title_text, subtitle_text


def build_expand_toggle_button(
    *,
    expanded: bool = False,
    on_toggle: Optional[Callable[[], None]] = None,
    visible: bool = True,
) -> ft.IconButton:
    """Create a Pane-3 expand/collapse icon button (`3` <-> `2+3`)."""
    return ft.IconButton(
        icon=ft.Icons.CLOSE_FULLSCREEN_ROUNDED
        if expanded
        else ft.Icons.OPEN_IN_FULL_ROUNDED,
        icon_size=18,
        icon_color=C.TEXT_PRIMARY,
        tooltip="Thu về khung nội dung (Ctrl+\\)"
        if expanded
        else "Mở rộng toàn vùng môn học (Ctrl+\\)",
        visible=bool(visible),
        on_click=lambda _e: on_toggle() if callable(on_toggle) else None,
    )


def sync_expand_toggle_button(
    btn: Optional[ft.IconButton],
    *,
    expanded: bool,
    visible: bool = True,
) -> None:
    """Sync icon, tooltip, visibility, and theme color of an expand toggle button."""
    if btn is None:
        return
    btn.icon = (
        ft.Icons.CLOSE_FULLSCREEN_ROUNDED
        if expanded
        else ft.Icons.OPEN_IN_FULL_ROUNDED
    )
    btn.tooltip = (
        "Thu về khung nội dung (Ctrl+\\)"
        if expanded
        else "Mở rộng toàn vùng môn học (Ctrl+\\)"
    )
    btn.icon_color = C.TEXT_PRIMARY
    btn.visible = bool(visible)
    safe_control_update(btn)


def refresh_viewer_header(
    header_container: ft.Container,
    back_btn: ft.IconButton,
    title_text: ft.Text,
    subtitle_text: ft.Text,
) -> None:
    """Apply active global theme tokens C to a viewer header bar."""
    header_container.bgcolor = C.SURFACE
    header_container.border = ft.Border.only(bottom=ft.BorderSide(1, C.BORDER))
    back_btn.icon_color = C.TEXT_PRIMARY
    title_text.color = C.TEXT_PRIMARY
    subtitle_text.color = C.TEXT_SECONDARY


def build_skeleton_block(
    *,
    width: Optional[float] = None,
    height: float = 12,
    radius: float = 6,
    subtle: bool = False,
) -> ft.Container:
    """Build a themed placeholder skeleton bar using semantic C tokens."""
    return ft.Container(
        width=width,
        height=height,
        bgcolor=C.SURFACE_ELEVATED if subtle else C.SURFACE_HOVER,
        border_radius=radius,
    )


def build_course_tab_skeleton(
    kind: Literal["sections", "resources", "grades"] = "sections",
) -> ft.Column:
    """Build layout-matching skeleton cards + status message for CourseDetailPane tabs."""
    status_label = {
        "sections": "Đang đồng bộ nội dung và hoạt động môn học từ Moodle...",
        "resources": "Đang tổng hợp tài liệu & liên kết học tập...",
        "grades": "Đang tải bảng điểm thành phần từ Moodle...",
    }.get(kind, "Đang tải dữ liệu...")

    cards: list[ft.Control] = [
        ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.ProgressRing(width=15, height=15, stroke_width=2, color=C.ACCENT),
                ft.Text(status_label, size=12, color=C.TEXT_SECONDARY, expand=True),
            ],
        )
    ]
    if kind == "grades":
        for _ in range(4):
            cards.append(
                ft.Container(
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=10,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=12),
                    content=ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Column(
                                spacing=6,
                                expand=True,
                                controls=[
                                    build_skeleton_block(width=190, height=13),
                                    build_skeleton_block(width=120, height=10, subtle=True),
                                ],
                            ),
                            build_skeleton_block(width=46, height=20, radius=8),
                        ],
                    ),
                )
            )
    else:
        row_count = 3 if kind == "sections" else 4
        for _ in range(row_count):
            cards.append(
                ft.Container(
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=12,
                    padding=14,
                    content=ft.Column(
                        spacing=10,
                        controls=[
                            build_skeleton_block(width=220, height=14),
                            build_skeleton_block(height=11, subtle=True),
                            build_skeleton_block(width=210, height=11, subtle=True),
                        ],
                    ),
                )
            )
    return ft.Column(spacing=10, controls=cards)


def build_assignment_loading_skeleton(
    status_text: str = "Đang tải đề bài, hạn nộp và trạng thái nộp bài từ Moodle...",
    *,
    title: str = "",
) -> ft.Container:
    """Build layout-matching skeleton state shown while assignment details load."""
    header_lines: list[ft.Control] = [
        ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.ProgressRing(width=16, height=16, stroke_width=2.5, color=C.ACCENT),
                ft.Text(
                    status_text,
                    size=12,
                    weight=ft.FontWeight.W_600,
                    color=C.ACCENT,
                    expand=True,
                ),
            ],
        ),
    ]
    if title:
        header_lines.append(
            ft.Text(
                title,
                size=17,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        )
    else:
        header_lines.append(build_skeleton_block(width=260, height=18))
    header_lines.append(build_skeleton_block(width=180, height=12, subtle=True))

    cards = [
        ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.ACCENT),
            border_radius=10,
            padding=14,
            content=ft.Column(spacing=8, controls=header_lines),
        ),
        ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
            padding=14,
            content=ft.Column(
                spacing=10,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            build_skeleton_block(width=76, height=11, subtle=True),
                            build_skeleton_block(width=140, height=12),
                        ],
                    ),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            build_skeleton_block(width=64, height=11, subtle=True),
                            build_skeleton_block(width=120, height=12),
                        ],
                    ),
                ],
            ),
        ),
        ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
            padding=14,
            content=ft.Column(
                spacing=8,
                controls=[
                    build_skeleton_block(width=110, height=11),
                    build_skeleton_block(height=12, subtle=True),
                    build_skeleton_block(height=12, subtle=True),
                    build_skeleton_block(width=220, height=12, subtle=True),
                ],
            ),
        ),
    ]
    return ft.Container(
        expand=True,
        padding=16,
        content=ft.Column(
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                ft.ProgressBar(color=C.ACCENT, bgcolor=C.BORDER, bar_height=3),
                *cards,
            ],
        ),
    )


def _build_preset_skeleton(preset: Literal["document", "archive", "assignment", "activity"]) -> list[ft.Control]:
    if preset == "archive":
        return [
            ft.Container(
                bgcolor=C.SURFACE,
                border=ft.Border.all(1, C.BORDER),
                border_radius=10,
                padding=12,
                content=ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        build_skeleton_block(width=24, height=24, radius=6),
                        ft.Column(
                            spacing=6,
                            expand=True,
                            controls=[
                                build_skeleton_block(width=200, height=13),
                                build_skeleton_block(width=90, height=10, subtle=True),
                            ],
                        ),
                    ],
                ),
            )
            for _ in range(4)
        ]
    if preset == "assignment":
        return [build_assignment_loading_skeleton("")]
    if preset == "activity":
        return [
            ft.Row(
                spacing=10,
                controls=[
                    ft.Container(
                        expand=True,
                        bgcolor=C.SURFACE,
                        border=ft.Border.all(1, C.BORDER),
                        border_radius=10,
                        padding=14,
                        content=ft.Column(
                            spacing=8,
                            controls=[
                                build_skeleton_block(width=90, height=10, subtle=True),
                                build_skeleton_block(width=70, height=18),
                            ],
                        ),
                    )
                    for _ in range(2)
                ],
            ),
            ft.Container(
                bgcolor=C.SURFACE,
                border=ft.Border.all(1, C.BORDER),
                border_radius=10,
                padding=16,
                content=ft.Column(
                    spacing=10,
                    controls=[
                        build_skeleton_block(width=180, height=14),
                        build_skeleton_block(height=12, subtle=True),
                        build_skeleton_block(width=240, height=12, subtle=True),
                    ],
                ),
            ),
        ]
    # document preset
    return [
        ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=12,
            padding=16,
            content=ft.Column(
                spacing=12,
                controls=[
                    build_skeleton_block(width=200, height=18),
                    build_skeleton_block(height=12, subtle=True),
                    build_skeleton_block(height=12, subtle=True),
                    build_skeleton_block(width=220, height=12, subtle=True),
                    ft.Container(height=8),
                    build_skeleton_block(height=140, radius=10, subtle=True),
                    build_skeleton_block(height=12, subtle=True),
                    build_skeleton_block(width=180, height=12, subtle=True),
                ],
            ),
        )
    ]


class PaneLoadingOverlay(ft.Container):
    """Animated Pane-3 loading state shown immediately while downloading or resolving content."""

    def __init__(
        self,
        *,
        title: str,
        subtitle: str = "Đang tải từ Moodle...",
        status_message: str = "Đang tải dữ liệu vào ứng dụng, vui lòng chờ trong giây lát...",
        preset: Literal["document", "archive", "assignment", "activity"] = "document",
        on_back: Callable[[], None],
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(expand=True, bgcolor=C.BG)
        self._on_back = on_back
        self._expanded = bool(is_expanded)
        self._show_expand_toggle = bool(show_expand_toggle)
        self._on_toggle_expand = on_toggle_expand
        self._disposed = False

        self._expand_btn = build_expand_toggle_button(
            expanded=self._expanded,
            on_toggle=lambda: self._on_toggle_expand()
            if callable(self._on_toggle_expand)
            else None,
            visible=self._show_expand_toggle,
        )
        (
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        ) = build_viewer_header(
            title=title or "Đang tải...",
            subtitle=subtitle,
            back_tooltip="Hủy & quay lại",
            on_back=lambda _e: self._handle_back(),
            trailing_actions=[self._expand_btn],
        )
        self._progress_bar = ft.ProgressBar(
            value=None, color=C.ACCENT, bgcolor=C.BORDER, bar_height=3
        )
        self._status_label = ft.Text(
            status_message,
            size=13,
            weight=ft.FontWeight.W_600,
            color=C.TEXT_PRIMARY,
            expand=True,
        )
        self._status_banner = ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.ACCENT),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            content=ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=C.ACCENT),
                    self._status_label,
                ],
            ),
        )
        self._skeleton_host = ft.Column(
            spacing=12,
            opacity=1.0,
            animate_opacity=ft.Animation(650, ft.AnimationCurve.EASE_IN_OUT),
            controls=_build_preset_skeleton(preset),
        )
        self.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                self._header_container,
                self._progress_bar,
                ft.Container(
                    expand=True,
                    padding=16,
                    content=ft.ListView(
                        expand=True,
                        spacing=12,
                        controls=[self._status_banner, self._skeleton_host],
                    ),
                ),
            ],
        )

    def did_mount(self) -> None:
        try:
            page = self.page
        except RuntimeError:
            page = None
        run_task = getattr(page, "run_task", None) if page is not None else None
        if callable(run_task):
            run_task(self._pulse_loop_async)

    async def _pulse_loop_async(self) -> None:
        while not self._disposed:
            try:
                self._skeleton_host.opacity = (
                    0.55 if (self._skeleton_host.opacity or 1.0) > 0.8 else 1.0
                )
                safe_control_update(self._skeleton_host)
            except Exception:
                break
            await asyncio.sleep(0.68)

    def set_status(self, message: str, *, subtitle: Optional[str] = None) -> None:
        self._status_label.value = message
        if subtitle is not None:
            self._subtitle_text.value = subtitle
        safe_control_update(self)

    def set_progress(
        self,
        fraction: Optional[float],
        *,
        message: Optional[str] = None,
        subtitle: Optional[str] = None,
    ) -> None:
        if fraction is None:
            self._progress_bar.value = None
        else:
            self._progress_bar.value = max(0.03, min(1.0, float(fraction)))
        if message is not None:
            self._status_label.value = message
        if subtitle is not None:
            self._subtitle_text.value = subtitle
        safe_control_update(self)

    def set_expanded(self, expanded: bool, *, visible: bool = True) -> None:
        self._expanded = bool(expanded)
        self._show_expand_toggle = bool(visible)
        sync_expand_toggle_button(
            self._expand_btn,
            expanded=self._expanded,
            visible=self._show_expand_toggle,
        )

    def _handle_back(self) -> None:
        self.dispose()
        self._on_back()

    def dispose(self) -> None:
        self._disposed = True

    def update_theme(self) -> None:
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
        self._progress_bar.color = C.ACCENT
        self._progress_bar.bgcolor = C.BORDER
        self._status_banner.bgcolor = C.SURFACE
        self._status_banner.border = ft.Border.all(1, C.ACCENT)
        self._status_label.color = C.TEXT_PRIMARY
        safe_control_update(self)


class PaneErrorOverlay(ft.Container):
    """Recoverable Pane-3 error card offering retry and in-app WebView fallback."""

    def __init__(
        self,
        *,
        title: str,
        message: str,
        on_back: Callable[[], None],
        on_retry: Optional[Callable[[], None]] = None,
        on_open_webview: Optional[Callable[[], None]] = None,
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(expand=True, bgcolor=C.BG)
        self._on_back = on_back
        self._expanded = bool(is_expanded)
        self._show_expand_toggle = bool(show_expand_toggle)
        self._expand_btn = build_expand_toggle_button(
            expanded=self._expanded,
            on_toggle=lambda: on_toggle_expand() if callable(on_toggle_expand) else None,
            visible=self._show_expand_toggle,
        )
        (
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        ) = build_viewer_header(
            title=title or "Không thể tải nội dung",
            subtitle="Đã xảy ra sự cố khi tải dữ liệu",
            back_tooltip="Quay lại",
            on_back=lambda _e: self._on_back(),
            trailing_actions=[self._expand_btn],
        )

        actions: list[ft.Control] = []
        if callable(on_retry):
            actions.append(
                ft.FilledButton(
                    "Thử tải lại",
                    icon=ft.Icons.REFRESH_ROUNDED,
                    on_click=lambda _e: on_retry(),
                )
            )
        if callable(on_open_webview):
            actions.append(
                ft.OutlinedButton(
                    "Mở bằng trình duyệt trong ứng dụng",
                    icon=ft.Icons.WEB_ROUNDED,
                    on_click=lambda _e: on_open_webview(),
                )
            )
        actions.append(
            ft.TextButton(
                "Quay lại",
                on_click=lambda _e: self._on_back(),
            )
        )

        self._card = ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.WARNING),
            border_radius=12,
            padding=18,
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
                tight=True,
                controls=[
                    ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, size=40, color=C.WARNING),
                    ft.Text(
                        "Không thể hiển thị nội dung ngay lúc này",
                        size=15,
                        weight=ft.FontWeight.W_600,
                        color=C.TEXT_PRIMARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        message,
                        size=13,
                        color=C.TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                        selectable=True,
                    ),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        wrap=True,
                        spacing=8,
                        run_spacing=8,
                        controls=actions,
                    ),
                ],
            ),
        )

        def _on_body_resize(e) -> None:
            w = getattr(e, "width", None)
            if w is None or isinstance(w, bool):
                return
            try:
                numeric = float(w)
            except (TypeError, ValueError):
                return
            self._card.width = min(460.0, max(240.0, numeric - 32.0)) if numeric > 500 else None
            safe_control_update(self._card)

        self.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                self._header_container,
                ft.Container(
                    expand=True,
                    on_size_change=_on_body_resize,
                    content=ft.Column(
                        expand=True,
                        scroll=ft.ScrollMode.AUTO,
                        alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[ft.Container(padding=16, content=self._card)],
                    ),
                ),
            ],
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
        self._card.bgcolor = C.SURFACE
        self._card.border = ft.Border.all(1, C.WARNING)
        safe_control_update(self)
