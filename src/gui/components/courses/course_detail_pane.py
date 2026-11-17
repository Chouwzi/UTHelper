"""Right-hand Detail pane displaying course metadata, sections, resources, and grades."""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

import flet as ft

from core.courses.models import (
    CourseAttachment,
    CourseGradeItem,
    CourseModuleItem,
    CourseSummary,
)
from gui.components.courses._shared import (
    build_expand_toggle_button,
    build_filter_chip,
    build_offline_banner,
    safe_control_update,
    sync_expand_toggle_button,
)
from gui.core.theme import C
from gui.view_models.courses_state import (
    CoursesState,
    compute_course_origin,
    flatten_course_resources,
    format_file_size,
    module_matches_type_filter,
    safe_http_url,
)

_MODULE_FILTERS: tuple[tuple[str, str], ...] = (
    ("all", "Tất cả"),
    ("materials", "Tài liệu"),
    ("tasks", "Bài tập"),
    ("attendance", "Điểm danh"),
    ("groups", "Nhóm"),
    ("links", "Liên kết"),
)

_RESOURCE_FILTERS: tuple[tuple[str, str], ...] = (
    ("all", "Tất cả"),
    ("pdf", "PDF"),
    ("office", "Office"),
    ("archive", "Nén"),
    ("link", "Liên kết"),
)


def _format_sync_timestamp(ts: int) -> str:
    if not ts or ts <= 0:
        return ""
    try:
        dt = datetime.fromtimestamp(ts)
    except (ValueError, OSError, OverflowError):
        return ""
    return f"Đồng bộ {dt.strftime('%H:%M %d/%m/%Y')}"


def _module_icon(mod: CourseModuleItem):
    if mod.modname == "assign":
        return ft.Icons.ASSIGNMENT_OUTLINED
    if mod.modname in {"quiz", "scorm"}:
        return ft.Icons.QUIZ_OUTLINED
    if mod.modname in {"attendance", "choice", "feedback"}:
        return ft.Icons.FACT_CHECK_OUTLINED
    if mod.modname == "googlemeet":
        return ft.Icons.VIDEO_CALL_ROUNDED
    if mod.modname == "url":
        return ft.Icons.LINK_ROUNDED
    if mod.modname == "folder":
        return ft.Icons.FOLDER_OUTLINED
    if mod.modname in {"page", "book", "label"}:
        return ft.Icons.MENU_BOOK_OUTLINED
    if mod.attachments:
        kind = mod.attachments[0].preview_kind
        if kind == "pdf":
            return ft.Icons.PICTURE_AS_PDF_OUTLINED
        if kind == "archive" or mod.attachments[0].is_archive:
            return ft.Icons.ARCHIVE_OUTLINED
    return ft.Icons.INSERT_DRIVE_FILE_OUTLINED


def _module_summary_text(mod: CourseModuleItem) -> str:
    if len(mod.attachments) > 1:
        return f"{len(mod.attachments)} tệp"
    if len(mod.attachments) == 1:
        att = mod.attachments[0]
        size_text = format_file_size(att.file_size)
        return f"{att.file_name} • {size_text}" if size_text else att.file_name
    if mod.modname in {"url", "googlemeet"}:
        return safe_http_url(mod.url) or safe_http_url(mod.web_url)
    return ""


class CourseDetailPane(ft.Container):
    """Detail pane showing selected course header and Content/Resources/Grades tabs."""

    def __init__(
        self,
        *,
        on_back_mobile: Optional[Callable[[], None]] = None,
        on_refresh_course: Optional[Callable[[], None]] = None,
        on_open_module: Optional[Callable[[CourseModuleItem], None]] = None,
        on_download_attachment: Optional[Callable[[CourseAttachment], None]] = None,
        on_toggle_completion: Optional[Callable[[CourseModuleItem, bool], None]] = None,
        on_copy_text: Optional[Callable[[str], None]] = None,
        on_launch_url: Optional[Callable[[str], None]] = None,
        on_subtab_change: Optional[Callable[[int], None]] = None,
        on_toggle_expand: Optional[Callable[[], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(expand=True, **kwargs)
        self._on_back_mobile = on_back_mobile
        self._on_refresh_course = on_refresh_course
        self._on_open_module = on_open_module
        self._on_download_attachment = on_download_attachment
        self._on_toggle_completion = on_toggle_completion
        self._on_copy_text = on_copy_text
        self._on_launch_url = on_launch_url
        self._on_subtab_change = on_subtab_change
        self._on_toggle_expand = on_toggle_expand
        self._last_state: Optional[CoursesState] = None
        self._last_show_back = False

        self._back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_ROUNDED,
            icon_color=C.TEXT_PRIMARY,
            icon_size=20,
            tooltip="Quay lại danh sách",
            visible=False,
            on_click=lambda _e: self._on_back_mobile() if callable(self._on_back_mobile) else None,
        )
        self._expand_btn = build_expand_toggle_button(
            expanded=False,
            on_toggle=lambda: self._on_toggle_expand() if callable(self._on_toggle_expand) else None,
            visible=False,
        )
        self._header_slot = ft.Column(spacing=8)
        self._loading_bar = ft.ProgressBar(
            value=None, bar_height=2, color=C.ACCENT, bgcolor=C.BORDER, visible=False
        )
        self._notice_slot = ft.Column(spacing=6, visible=False)
        self._content_tab_body = ft.Column(expand=True, spacing=8, scroll=ft.ScrollMode.AUTO)
        self._resources_tab_body = ft.Column(expand=True, spacing=8, scroll=ft.ScrollMode.AUTO)
        self._grades_tab_body = ft.Column(expand=True, spacing=8, scroll=ft.ScrollMode.AUTO)
        self._tab_bar = ft.TabBar(
            tabs=[ft.Tab(label="Nội dung"), ft.Tab(label="Tài liệu"), ft.Tab(label="Điểm môn")],
            indicator_color=C.ACCENT,
            label_color=C.ACCENT,
            unselected_label_color=C.TEXT_SECONDARY,
            divider_color=C.BORDER,
        )
        self._tabs = ft.Tabs(
            length=3,
            selected_index=0,
            expand=True,
            on_change=self._handle_tabs_change,
            content=ft.Column(
                expand=True,
                spacing=8,
                controls=[
                    self._tab_bar,
                    ft.TabBarView(
                        expand=True,
                        controls=[
                            self._content_tab_body,
                            self._resources_tab_body,
                            self._grades_tab_body,
                        ],
                    ),
                ],
            ),
        )
        self._empty_placeholder = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=ft.Column(
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.SCHOOL_OUTLINED, size=44, color=C.TEXT_SECONDARY),
                    ft.Text(
                        "Chọn một môn học để xem nội dung, tài liệu và điểm",
                        size=14,
                        color=C.TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
            ),
        )
        self._detail_column = ft.Column(
            expand=True,
            spacing=10,
            visible=False,
            controls=[self._header_slot, self._loading_bar, self._notice_slot, self._tabs],
        )

        self.bgcolor = C.BG
        self.padding = ft.Padding.all(14)
        self.content = ft.Column(
            expand=True, spacing=0, controls=[self._empty_placeholder, self._detail_column]
        )

    def _safe_update(self) -> None:
        safe_control_update(self)

    def sync_subtab(self, index: int, *, update: bool = True) -> None:
        """Sync selected tab index without rebuilding section ExpansionTile controls."""
        clamped = max(0, min(2, int(index)))
        self._tabs.selected_index = clamped
        if self._last_state is not None:
            self._last_state.active_subtab = clamped
        if update:
            self._safe_update()

    def _handle_tabs_change(self, e) -> None:
        raw = getattr(e, "data", None)
        if raw is None:
            raw = getattr(getattr(e, "control", None), "selected_index", 0)
        if isinstance(raw, bool):
            idx = 0
        else:
            try:
                idx = int(raw)
            except (TypeError, ValueError):
                idx = 0
        if not (0 <= idx < 3):
            idx = 0
        self._tabs.selected_index = idx
        if self._last_state is not None:
            self._last_state.active_subtab = idx
        if callable(self._on_subtab_change):
            self._on_subtab_change(idx)

    def _set_filter(self, attr: str, key: str) -> None:
        if self._last_state is None:
            return
        setattr(self._last_state, attr, key)
        self.render(self._last_state, show_back=self._last_show_back)

    def _build_filter_chips(
        self, options: tuple[tuple[str, str], ...], active_key: str, attr: str
    ) -> ft.Row:
        return ft.Row(
            scroll=ft.ScrollMode.AUTO,
            spacing=6,
            controls=[
                build_filter_chip(
                    label,
                    selected=(active_key == key),
                    on_select=lambda _e, k=key: self._set_filter(attr, k),
                )
                for key, label in options
            ],
        )

    def _build_header(
        self, course: CourseSummary, state: CoursesState, *, show_back: bool
    ) -> list[ft.Control]:
        self._back_btn.visible = show_back
        sync_label = _format_sync_timestamp(state.last_synced_at or course.last_synced_at)
        course_web = (
            safe_http_url(course.web_url)
            or f"{compute_course_origin(course)}/course/view.php?id={course.id}"
        )

        title_col = ft.Column(
            expand=True,
            spacing=2,
            controls=[
                ft.Text(
                    course.display_name or course.fullname,
                    size=17,
                    weight=ft.FontWeight.W_700,
                    color=C.TEXT_PRIMARY,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                *([ft.Text(sync_label, size=11, color=C.TEXT_SECONDARY)] if sync_label else []),
            ],
        )
        top_row = ft.Row(
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self._back_btn,
                title_col,
                ft.IconButton(
                    icon=ft.Icons.REFRESH_ROUNDED,
                    icon_color=C.TEXT_SECONDARY,
                    icon_size=20,
                    tooltip="Đồng bộ môn học",
                    on_click=lambda _e: (
                        self._on_refresh_course() if callable(self._on_refresh_course) else None
                    ),
                ),
                self._expand_btn,
                ft.IconButton(
                    icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                    icon_color=C.TEXT_SECONDARY,
                    icon_size=20,
                    tooltip="Mở khóa học trên Moodle Web",
                    on_click=lambda _e, url=course_web: (
                        self._on_launch_url(url) if callable(self._on_launch_url) else None
                    ),
                ),
            ],
        )

        sync_expand_toggle_button(
            self._expand_btn,
            expanded=bool(state.pane3_expanded),
            visible=(not show_back and callable(self._on_toggle_expand)),
        )

        meta_controls: list[ft.Control] = []
        if course.semester_label:
            meta_controls.append(
                ft.Container(
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=8,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                    content=ft.Text(course.semester_label, size=11, color=C.TEXT_SECONDARY),
                )
            )
        if course.source == "thnn":
            meta_controls.append(
                ft.Container(
                    bgcolor=C.ACCENT,
                    border_radius=8,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                    content=ft.Text("THNN", size=11, weight=ft.FontWeight.W_600, color=C.ON_ACCENT),
                )
            )

        copy_value = (course.teacher_email or course.teacher_name).strip()
        if copy_value:
            teacher_label = (
                f"{course.teacher_name} ({course.teacher_email})"
                if course.teacher_name and course.teacher_email
                else copy_value
            )
            meta_controls.append(
                ft.Chip(
                    leading=ft.Icon(ft.Icons.PERSON_OUTLINE_ROUNDED, size=15, color=C.ACCENT),
                    label=ft.Text(teacher_label, size=11, color=C.TEXT_PRIMARY),
                    bgcolor=C.SURFACE,
                    border_side=ft.BorderSide(1, C.BORDER),
                    tooltip="Nhấn để sao chép liên hệ giảng viên",
                    on_click=lambda _e, val=copy_value: (
                        self._on_copy_text(val) if callable(self._on_copy_text) else None
                    ),
                )
            )

        if state.user_groups:
            group_names = ", ".join(
                str(g.get("name") or "").strip()
                for g in state.user_groups
                if isinstance(g, dict) and str(g.get("name") or "").strip()
            )
            if group_names:
                meta_controls.append(
                    ft.Container(
                        bgcolor=C.SURFACE,
                        border=ft.Border.all(1, C.ACCENT),
                        border_radius=8,
                        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                        content=ft.Row(
                            tight=True,
                            spacing=4,
                            controls=[
                                ft.Icon(ft.Icons.GROUP_OUTLINED, size=14, color=C.ACCENT),
                                ft.Text(
                                    f"Nhóm: {group_names}",
                                    size=11,
                                    weight=ft.FontWeight.W_600,
                                    color=C.ACCENT,
                                ),
                            ],
                        ),
                    )
                )

        safe_meet = safe_http_url(course.meet_url)
        if safe_meet:
            meta_controls.append(
                ft.OutlinedButton(
                    content="Vào phòng học",
                    icon=ft.Icons.VIDEO_CALL_ROUNDED,
                    on_click=lambda _e, url=safe_meet: (
                        self._on_launch_url(url) if callable(self._on_launch_url) else None
                    ),
                )
            )

        controls: list[ft.Control] = [top_row]
        if meta_controls:
            controls.append(ft.Row(controls=meta_controls, wrap=True, spacing=6, run_spacing=6))
        return controls

    def _module_row(self, mod: CourseModuleItem, *, section_label: str = "") -> ft.Control:
        subtitle_text = " • ".join(p for p in (section_label, _module_summary_text(mod)) if p)
        trailing: list[ft.Control] = []
        if mod.is_new:
            trailing.append(
                ft.Container(
                    bgcolor=C.UPDATE,
                    border_radius=6,
                    padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                    content=ft.Text("Mới", size=10, weight=ft.FontWeight.W_700, color=C.ON_UPDATE),
                )
            )
        if len(mod.attachments) == 1:
            first_att = mod.attachments[0]
            trailing.append(
                ft.IconButton(
                    icon=ft.Icons.DOWNLOAD_ROUNDED,
                    icon_size=18,
                    icon_color=C.TEXT_SECONDARY,
                    tooltip="Tải xuống",
                    on_click=lambda _e, att=first_att: (
                        self._on_download_attachment(att) if callable(self._on_download_attachment) else None
                    ),
                )
            )
        if mod.completion_mode == 1:
            trailing.append(
                ft.Checkbox(
                    value=mod.completion_state > 0,
                    active_color=C.ACCENT,
                    check_color=C.ON_ACCENT,
                    on_change=lambda e, item=mod: (
                        self._on_toggle_completion(item, bool(getattr(e.control, "value", False)))
                        if callable(self._on_toggle_completion)
                        else None
                    ),
                )
            )

        text_col = ft.Column(
            expand=True,
            spacing=2,
            controls=[
                ft.Text(
                    mod.name or "Hoạt động",
                    size=13,
                    weight=ft.FontWeight.W_500,
                    color=C.TEXT_PRIMARY,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                *(
                    [
                        ft.Text(
                            subtitle_text,
                            size=11,
                            color=C.TEXT_SECONDARY,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        )
                    ]
                    if subtitle_text
                    else []
                ),
            ],
        )

        return ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            ink=True,
            on_click=(
                (lambda _e, item=mod: self._on_open_module(item))
                if callable(self._on_open_module)
                else None
            ),
            content=ft.Row(
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[ft.Icon(_module_icon(mod), size=20, color=C.ACCENT), text_col, *trailing],
            ),
        )

    def _build_content_tab(self, state: CoursesState) -> list[ft.Control]:
        controls: list[ft.Control] = [
            self._build_filter_chips(_MODULE_FILTERS, state.module_filter, "module_filter")
        ]
        tiles: list[ft.Control] = []
        for sec in state.sections:
            mods = [m for m in sec.modules if module_matches_type_filter(m, state.module_filter)]
            if (not mods and state.module_filter != "all") or (not mods and not sec.visible):
                continue
            tiles.append(
                ft.ExpansionTile(
                    title=ft.Text(sec.name or f"Chủ đề {sec.section_num}", size=14, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY),
                    subtitle=ft.Text(f"{len(mods)} mục", size=11, color=C.TEXT_SECONDARY),
                    expanded=(not tiles or any(m.is_new for m in mods)),
                    bgcolor=C.SURFACE,
                    collapsed_bgcolor=C.SURFACE,
                    text_color=C.TEXT_PRIMARY,
                    collapsed_text_color=C.TEXT_PRIMARY,
                    icon_color=C.ACCENT,
                    collapsed_icon_color=C.TEXT_SECONDARY,
                    controls_padding=ft.Padding.symmetric(horizontal=8, vertical=6),
                    controls=[self._module_row(m) for m in mods],
                )
            )
        if tiles:
            controls.extend(tiles)
        else:
            controls.append(
                ft.Container(
                    padding=ft.Padding.symmetric(vertical=24),
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text("Không có nội dung phù hợp bộ lọc.", size=13, color=C.TEXT_SECONDARY),
                )
            )
        return controls

    def _build_resources_tab(self, state: CoursesState) -> list[ft.Control]:
        controls: list[ft.Control] = [
            self._build_filter_chips(_RESOURCE_FILTERS, state.resource_kind_filter, "resource_kind_filter")
        ]
        items = flatten_course_resources(state.sections, kind_filter=state.resource_kind_filter)
        if items:
            controls.extend(self._module_row(mod, section_label=sec) for sec, mod in items)
        else:
            controls.append(
                ft.Container(
                    padding=ft.Padding.symmetric(vertical=24),
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text("Chưa có tài liệu hoặc liên kết phù hợp.", size=13, color=C.TEXT_SECONDARY),
                )
            )
        return controls

    def _build_grade_card(self, item: CourseGradeItem) -> ft.Control:
        meta_parts = [
            p
            for p in (
                f"Trọng số: {item.weight}" if item.weight else "",
                f"Thang: {item.range_formatted}" if item.range_formatted else "",
                item.percentage,
            )
            if p
        ]
        return ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            content=ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Column(
                        expand=True,
                        spacing=2,
                        controls=[
                            ft.Text(item.item_name or "Điểm thành phần", size=13, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY),
                            *([ft.Text(" • ".join(meta_parts), size=11, color=C.TEXT_SECONDARY)] if meta_parts else []),
                        ],
                    ),
                    ft.Text(item.grade_formatted or "-", size=15, weight=ft.FontWeight.W_700, color=C.ACCENT),
                ],
            ),
        )

    def _build_grades_tab(self, course: CourseSummary, state: CoursesState) -> list[ft.Control]:
        if state.grades:
            return [self._build_grade_card(item) for item in state.grades]
        grades_url = f"{compute_course_origin(course)}/grade/report/user/index.php?id={course.id}"
        return [
            ft.Container(
                bgcolor=C.SURFACE,
                border=ft.Border.all(1, C.BORDER),
                border_radius=12,
                padding=ft.Padding.all(20),
                alignment=ft.Alignment.CENTER,
                content=ft.Column(
                    spacing=10,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Icon(ft.Icons.GRADE_OUTLINED, size=36, color=C.TEXT_SECONDARY),
                        ft.Text(
                            "Chưa có dữ liệu điểm trên Moodle cho môn học này.",
                            size=13,
                            color=C.TEXT_SECONDARY,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ft.OutlinedButton(
                            content="Xem điểm trên Moodle",
                            icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                            on_click=lambda _e, url=grades_url: (
                                self._on_launch_url(url) if callable(self._on_launch_url) else None
                            ),
                        ),
                    ],
                ),
            )
        ]

    def render(self, state: CoursesState, *, show_back: bool, update: bool = True) -> None:
        """Render the selected course header, warnings, and 3 workspace sub-tabs."""
        self._last_state = state
        self._last_show_back = show_back
        course = state.selected_course()
        if course is None:
            self._empty_placeholder.visible = True
            self._detail_column.visible = False
            if update:
                self._safe_update()
            return

        self._empty_placeholder.visible = False
        self._detail_column.visible = True
        self._loading_bar.visible = bool(state.loading_detail)
        self._header_slot.controls = self._build_header(course, state, show_back=show_back)

        if state.offline_notice:
            self._notice_slot.controls = [build_offline_banner(state.offline_notice)]
            self._notice_slot.visible = True
        else:
            self._notice_slot.controls = []
            self._notice_slot.visible = False

        self._tabs.selected_index = max(0, min(2, int(state.active_subtab or 0)))
        self._content_tab_body.controls = self._build_content_tab(state)
        self._resources_tab_body.controls = self._build_resources_tab(state)
        self._grades_tab_body.controls = self._build_grades_tab(course, state)
        if update:
            self._safe_update()

    def update_theme(self, *, update: bool = True, rerender: bool = True) -> None:
        """Apply current global theme tokens C to persistent controls and re-render."""
        self.bgcolor = C.BG
        self._back_btn.icon_color = C.TEXT_PRIMARY
        self._loading_bar.color = C.ACCENT
        self._loading_bar.bgcolor = C.BORDER
        self._tab_bar.indicator_color = C.ACCENT
        self._tab_bar.label_color = C.ACCENT
        self._tab_bar.unselected_label_color = C.TEXT_SECONDARY
        self._tab_bar.divider_color = C.BORDER
        placeholder_col = self._empty_placeholder.content
        if isinstance(placeholder_col, ft.Column) and len(placeholder_col.controls) >= 2:
            placeholder_col.controls[0].color = C.TEXT_SECONDARY
            placeholder_col.controls[1].color = C.TEXT_SECONDARY
        if rerender and self._last_state is not None:
            self.render(self._last_state, show_back=self._last_show_back, update=update)
        elif update:
            self._safe_update()
