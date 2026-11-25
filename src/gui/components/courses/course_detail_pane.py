"""Right-hand Detail pane displaying course metadata, sections, resources, and grades."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Callable, Optional

import flet as ft

from core.courses.models import (
    CourseAttachment,
    CourseModuleItem,
    CourseSummary,
)
from gui.components.courses._shared import (
    build_course_tab_skeleton,
    build_expand_toggle_button,
    build_filter_chip,
    build_offline_banner,
    safe_control_update,
    sync_expand_toggle_button,
)
from gui.components.courses.grade_rows import (
    ParsedGradeRow as _ParsedGradeRow,
    analyze_grade_item as _analyze_grade_item,
    build_grade_row as _shared_build_grade_row,
    build_grouped_grade_card as _shared_build_grouped_grade_card,
    build_pending_grades_tile as _shared_build_pending_grades_tile,
    score_rank_label as _score_rank_label,
    score_semantic_color as _score_semantic_color,
    split_and_sort_grade_rows as _split_and_sort_grade_rows,
)
from gui.core.theme import C
from gui.view_models.courses_state import (
    CoursesState,
    compute_course_origin,
    extract_readable_html_and_links,
    flatten_course_resources,
    format_file_size,
    is_compact_height,
    module_matches_type_filter,
    real_module_attachments,
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


def _module_icon_and_tint(mod: CourseModuleItem) -> tuple[ft.IconData, str]:
    if mod.modname == "assign":
        return ft.Icons.ASSIGNMENT_OUTLINED, C.WARNING
    if mod.modname in {"quiz", "scorm", "vpl"}:
        return ft.Icons.QUIZ_OUTLINED, C.ACCENT
    if mod.modname in {"attendance", "choice", "feedback", "groupselect", "choicegroup"}:
        return ft.Icons.FACT_CHECK_OUTLINED, C.SAFE
    if mod.modname == "googlemeet":
        return ft.Icons.VIDEO_CALL_ROUNDED, C.SAFE
    if mod.modname == "url":
        return ft.Icons.LINK_ROUNDED, C.ACCENT
    if mod.modname == "folder":
        return ft.Icons.FOLDER_OUTLINED, C.ACCENT
    if mod.modname in {"page", "book", "label"}:
        return ft.Icons.MENU_BOOK_OUTLINED, C.ACCENT
    atts = real_module_attachments(mod)
    if atts:
        kind = atts[0].preview_kind
        if kind == "pdf":
            return ft.Icons.PICTURE_AS_PDF_OUTLINED, C.CRITICAL
        if kind == "archive" or atts[0].is_archive:
            return ft.Icons.ARCHIVE_OUTLINED, C.WARNING
    return ft.Icons.INSERT_DRIVE_FILE_OUTLINED, C.ACCENT


def _module_summary_text(mod: CourseModuleItem) -> str:
    atts = real_module_attachments(mod)
    if len(atts) > 1:
        return f"{len(atts)} tệp"
    if len(atts) == 1:
        att = atts[0]
        size_text = format_file_size(att.file_size)
        return f"{att.file_name} • {size_text}" if size_text else att.file_name
    if mod.modname in {"url", "googlemeet"}:
        return safe_http_url(mod.url) or safe_http_url(mod.web_url)
    return ""


class CourseDetailPane(ft.Container):
    """Detail pane showing selected course header and Apple-style Segmented Content/Resources/Grades."""

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
        self._meta_expanded: bool = False

        self._back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_ROUNDED,
            icon_color=C.TEXT_PRIMARY,
            icon_size=20,
            tooltip="Quay lại danh sách",
            visible=False,
            width=44,
            height=44,
            padding=ft.Padding.all(8),
            size_constraints=ft.BoxConstraints(min_width=44, min_height=44),
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
        self._content_tab_body = ft.Column(expand=True, spacing=10, scroll=ft.ScrollMode.AUTO)
        self._resources_tab_body = ft.Column(expand=True, spacing=8, scroll=ft.ScrollMode.AUTO)
        self._grades_tab_body = ft.Column(expand=True, spacing=12, scroll=ft.ScrollMode.AUTO)

        self._segment_labels: list[ft.Text] = [
            ft.Text(
                "Nội dung",
                size=12,
                weight=ft.FontWeight.W_600,
                color=C.TEXT_PRIMARY,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                text_align=ft.TextAlign.CENTER,
            ),
            ft.Text(
                "Tài liệu",
                size=12,
                weight=ft.FontWeight.W_500,
                color=C.TEXT_SECONDARY,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                text_align=ft.TextAlign.CENTER,
            ),
            ft.Text(
                "Điểm môn",
                size=12,
                weight=ft.FontWeight.W_500,
                color=C.TEXT_SECONDARY,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                text_align=ft.TextAlign.CENTER,
            ),
        ]
        _default_titles = ["Nội dung", "Tài liệu", "Điểm môn"]
        self._segment_semantics: list[ft.Semantics] = [
            ft.Semantics(
                label=_default_titles[i],
                button=True,
                selected=(i == 0),
                exclude_semantics=True,
                content=self._segment_labels[i],
            )
            for i in range(3)
        ]
        self._segment_pills: list[ft.Container] = [
            ft.Container(
                expand=True,
                height=36,
                padding=ft.Padding.symmetric(horizontal=4),
                alignment=ft.Alignment.CENTER,
                border_radius=8,
                animate=ft.Animation(170, ft.AnimationCurve.EASE_OUT_CUBIC),
                ink=True,
                on_click=lambda _e, idx=i: self._on_segment_tap(idx),
                content=self._segment_semantics[i],
            )
            for i in range(3)
        ]
        self._segmented_track = ft.Container(
            bgcolor=C.SURFACE_ELEVATED,
            border=ft.Border.all(1, C.BORDER),
            border_radius=11,
            padding=ft.Padding.all(3),
            content=ft.Row(spacing=4, controls=self._segment_pills),
        )
        self._tab_bar = ft.TabBar(
            tabs=[ft.Tab(label="Nội dung"), ft.Tab(label="Tài liệu"), ft.Tab(label="Điểm môn")],
            indicator_color=C.ACCENT,
            label_color=C.ACCENT,
            unselected_label_color=C.TEXT_SECONDARY,
            divider_color=C.BORDER,
            visible=False,
            height=0,
        )
        self._tabs = ft.Tabs(
            length=3,
            selected_index=0,
            expand=True,
            on_change=self._handle_tabs_change,
            content=ft.Column(
                expand=True,
                spacing=10,
                controls=[
                    self._segmented_track,
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
        self._sync_segment_visuals(0)

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
        self.padding = ft.Padding.all(16)
        self.content = ft.Column(
            expand=True, spacing=0, controls=[self._empty_placeholder, self._detail_column]
        )

    def _safe_update(self) -> None:
        safe_control_update(self)

    def _sync_segment_visuals(self, active_idx: int, *, state: Optional[CoursesState] = None) -> None:
        base_titles = ["Nội dung", "Tài liệu", "Điểm môn"]
        if state is not None:
            res_count = len(flatten_course_resources(state.sections))
            graded_count = sum(1 for g in state.grades if _analyze_grade_item(g).has_score)
            if res_count > 0:
                base_titles[1] = f"Tài liệu · {res_count}"
            if graded_count > 0:
                base_titles[2] = f"Điểm môn · {graded_count}"
            elif state.grades:
                base_titles[2] = f"Điểm môn · {len(state.grades)}"

        for i in range(3):
            is_active = i == active_idx
            pill = self._segment_pills[i]
            lbl = self._segment_labels[i]
            sem = self._segment_semantics[i]
            lbl.value = base_titles[i]
            sem.label = base_titles[i]
            sem.selected = is_active
            lbl.weight = ft.FontWeight.W_600 if is_active else ft.FontWeight.W_500
            lbl.color = C.TEXT_PRIMARY if is_active else C.TEXT_SECONDARY
            pill.bgcolor = C.SURFACE if is_active else None
            pill.border = ft.Border.all(1, C.BORDER) if is_active else None
            pill.shadow = (
                ft.BoxShadow(
                    blur_radius=6,
                    spread_radius=0,
                    color=ft.Colors.with_opacity(0.06, C.TEXT_PRIMARY),
                    offset=ft.Offset(0, 1),
                )
                if is_active
                else None
            )

    def _on_segment_tap(self, index: int) -> None:
        self._tabs.on_change(SimpleNamespace(control=self._tabs, data=str(index)))

    def sync_subtab(self, index: int, *, update: bool = True) -> None:
        """Sync selected tab index without rebuilding section ExpansionTile controls."""
        clamped = max(0, min(2, int(index)))
        self._tabs.selected_index = clamped
        if self._last_state is not None:
            self._last_state.active_subtab = clamped
        self._sync_segment_visuals(clamped, state=self._last_state)
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
        self._sync_segment_visuals(idx, state=self._last_state)
        if callable(self._on_subtab_change):
            self._on_subtab_change(idx)
        else:
            self._safe_update()

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

    def _toggle_meta_expanded(self) -> None:
        self._meta_expanded = not self._meta_expanded
        if self._last_state is not None:
            self.render(self._last_state, show_back=self._last_show_back)

    def _build_header(
        self, course: CourseSummary, state: CoursesState, *, show_back: bool
    ) -> list[ft.Control]:
        self._back_btn.visible = show_back
        is_mobile = state.layout_mode == "mobile"
        short_vp = is_compact_height(state.viewport_height)
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
                    size=17 if is_mobile else 18,
                    weight=ft.FontWeight.W_700,
                    color=C.TEXT_PRIMARY,
                    max_lines=1 if short_vp else 2,
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
                    icon=ft.Icons.LANGUAGE_ROUNDED,
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
                    padding=ft.Padding.symmetric(horizontal=9, vertical=4),
                    content=ft.Text(
                        course.semester_label,
                        size=11,
                        weight=ft.FontWeight.W_500,
                        color=C.TEXT_SECONDARY,
                    ),
                )
            )
        if course.credits > 0:
            meta_controls.append(
                ft.Container(
                    bgcolor=ft.Colors.with_opacity(0.10, C.ACCENT),
                    border_radius=8,
                    padding=ft.Padding.symmetric(horizontal=9, vertical=4),
                    content=ft.Text(
                        f"{course.credits} tín chỉ",
                        size=11,
                        weight=ft.FontWeight.W_600,
                        color=C.ACCENT,
                    ),
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
                    label=ft.Text(
                        teacher_label,
                        size=11,
                        color=C.TEXT_PRIMARY,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    width=290 if (is_mobile and len(teacher_label) > 34) else None,
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
                is_long_group = is_mobile and len(group_names) > 28
                meta_controls.append(
                    ft.Container(
                        width=290 if is_long_group else None,
                        bgcolor=ft.Colors.with_opacity(0.08, C.ACCENT),
                        border=ft.Border.all(1, ft.Colors.with_opacity(0.35, C.ACCENT)),
                        border_radius=8,
                        padding=ft.Padding.symmetric(horizontal=9, vertical=4),
                        content=ft.Row(
                            tight=not is_long_group,
                            spacing=5,
                            controls=[
                                ft.Icon(ft.Icons.GROUP_OUTLINED, size=14, color=C.ACCENT),
                                ft.Text(
                                    f"Nhóm: {group_names}",
                                    size=11,
                                    weight=ft.FontWeight.W_600,
                                    color=C.ACCENT,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    expand=is_long_group,
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
            if short_vp:
                controls.append(
                    ft.Container(
                        bgcolor=C.SURFACE,
                        border=ft.Border.all(1, C.BORDER),
                        border_radius=8,
                        padding=ft.Padding.symmetric(horizontal=9, vertical=4),
                        ink=True,
                        on_click=lambda _e: self._toggle_meta_expanded(),
                        content=ft.Row(
                            tight=True,
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Text(
                                    "Thông tin môn",
                                    size=11,
                                    weight=ft.FontWeight.W_600,
                                    color=C.TEXT_SECONDARY,
                                ),
                                ft.Icon(
                                    ft.Icons.EXPAND_LESS_ROUNDED
                                    if self._meta_expanded
                                    else ft.Icons.EXPAND_MORE_ROUNDED,
                                    size=15,
                                    color=C.TEXT_SECONDARY,
                                ),
                            ],
                        ),
                    )
                )
                controls.append(
                    ft.Container(
                        visible=self._meta_expanded,
                        content=ft.Row(
                            scroll=ft.ScrollMode.AUTO,
                            spacing=6,
                            controls=meta_controls,
                        ),
                    )
                )
            else:
                controls.append(ft.Row(controls=meta_controls, wrap=True, spacing=6, run_spacing=6))
        return controls

    def _module_row(self, mod: CourseModuleItem, *, section_label: str = "") -> ft.Control:
        subtitle_text = " • ".join(p for p in (section_label, _module_summary_text(mod)) if p)
        icon_data, icon_tint = _module_icon_and_tint(mod)
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
        real_atts = real_module_attachments(mod)
        if len(real_atts) == 1:
            first_att = real_atts[0]
            trailing.append(
                ft.IconButton(
                    icon=ft.Icons.DOWNLOAD_ROUNDED,
                    icon_size=18,
                    icon_color=C.TEXT_SECONDARY,
                    tooltip="Tải xuống",
                    width=44,
                    height=44,
                    padding=ft.Padding.all(8),
                    size_constraints=ft.BoxConstraints(min_width=44, min_height=44),
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
        else:
            trailing.append(
                ft.Icon(
                    ft.Icons.CHEVRON_RIGHT_ROUNDED,
                    size=18,
                    color=C.TEXT_SECONDARY,
                )
            )

        icon_squircle = ft.Container(
            width=34,
            height=34,
            border_radius=9,
            alignment=ft.Alignment.CENTER,
            bgcolor=ft.Colors.with_opacity(0.11, icon_tint),
            content=ft.Icon(icon_data, size=18, color=icon_tint),
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
            border_radius=11,
            padding=ft.Padding.symmetric(horizontal=11, vertical=9),
            ink=True,
            on_click=(
                (lambda _e, item=mod: self._on_open_module(item))
                if callable(self._on_open_module)
                else None
            ),
            content=ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[icon_squircle, text_col, *trailing],
            ),
        )

    def _label_inline_card(
        self, mod: CourseModuleItem, *, is_mobile: bool = False
    ) -> ft.Control:
        raw_html = mod.inline_html or mod.description_html
        body_text, links = extract_readable_html_and_links(raw_html)
        display_text = body_text or (mod.name or "").strip()
        needs_remote_open = not raw_html and bool(mod.attachments)

        card_items: list[ft.Control] = []
        if display_text:
            card_items.append(
                ft.Text(
                    display_text,
                    size=12.5,
                    color=C.TEXT_PRIMARY,
                    selectable=not needs_remote_open,
                )
            )
        if links:
            link_pills: list[ft.Control] = []
            max_chars = 32 if is_mobile else 56
            for label, href in links:
                is_meet = "meet.google.com" in href.lower()
                short_label = (
                    label if len(label) <= max_chars else f"{label[: max_chars - 3]}..."
                )
                link_pills.append(
                    ft.OutlinedButton(
                        content=short_label,
                        icon=(
                            ft.Icons.VIDEO_CALL_ROUNDED
                            if is_meet
                            else ft.Icons.OPEN_IN_NEW_ROUNDED
                        ),
                        tooltip=href,
                        width=248 if (is_mobile and len(short_label) > 24) else None,
                        style=ft.ButtonStyle(
                            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                            text_style=ft.TextStyle(
                                size=12, overflow=ft.TextOverflow.ELLIPSIS
                            ),
                        )
                        if is_mobile
                        else None,
                        on_click=lambda _e, u=href: (
                            self._on_launch_url(u)
                            if callable(self._on_launch_url)
                            else None
                        ),
                    )
                )
            card_items.append(
                ft.Row(wrap=True, spacing=6, run_spacing=6, controls=link_pills)
            )

        return ft.Container(
            bgcolor=C.BG,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=12, vertical=9),
            ink=needs_remote_open,
            on_click=(
                (lambda _e, item=mod: self._on_open_module(item))
                if (needs_remote_open and callable(self._on_open_module))
                else None
            ),
            content=ft.Row(
                spacing=9,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Container(
                        padding=ft.Padding.only(top=2),
                        content=ft.Icon(
                            ft.Icons.INFO_OUTLINE_ROUNDED,
                            size=16,
                            color=C.TEXT_SECONDARY,
                        ),
                    ),
                    ft.Column(expand=True, spacing=6, controls=card_items),
                ],
            ),
        )

    def _build_section_item(
        self, mod: CourseModuleItem, *, is_mobile: bool = False
    ) -> ft.Control:
        if mod.modname == "label" and not real_module_attachments(mod):
            return self._label_inline_card(mod, is_mobile=is_mobile)
        return self._module_row(mod)

    def _build_content_tab(self, state: CoursesState) -> list[ft.Control]:
        is_mobile = state.layout_mode == "mobile"
        controls: list[ft.Control] = [
            self._build_filter_chips(_MODULE_FILTERS, state.module_filter, "module_filter")
        ]
        tiles: list[ft.Control] = []
        for sec in state.sections:
            mods = [m for m in sec.modules if module_matches_type_filter(m, state.module_filter)]
            if (not mods and state.module_filter != "all") or (not mods and not sec.visible):
                continue
            tiles.append(
                ft.Container(
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=13,
                    bgcolor=C.SURFACE,
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                    content=ft.ExpansionTile(
                        title=ft.Text(
                            sec.name or f"Chủ đề {sec.section_num}",
                            size=14,
                            weight=ft.FontWeight.W_600,
                            color=C.TEXT_PRIMARY,
                        ),
                        subtitle=ft.Text(f"{len(mods)} mục", size=11, color=C.TEXT_SECONDARY),
                        expanded=(not tiles or any(m.is_new for m in mods)),
                        bgcolor=C.SURFACE,
                        collapsed_bgcolor=C.SURFACE,
                        text_color=C.TEXT_PRIMARY,
                        collapsed_text_color=C.TEXT_PRIMARY,
                        icon_color=C.ACCENT,
                        collapsed_icon_color=C.TEXT_SECONDARY,
                        shape=ft.RoundedRectangleBorder(
                            radius=13,
                            side=ft.BorderSide(0, C.SURFACE, style=ft.BorderStyle.NONE),
                        ),
                        collapsed_shape=ft.RoundedRectangleBorder(
                            radius=13,
                            side=ft.BorderSide(0, C.SURFACE, style=ft.BorderStyle.NONE),
                        ),
                        controls_padding=(
                            ft.Padding.only(left=8, right=8, bottom=8, top=2)
                            if is_mobile
                            else ft.Padding.only(left=10, right=10, bottom=10, top=2)
                        ),
                        controls=[
                            ft.Column(
                                spacing=6,
                                controls=[
                                    self._build_section_item(m, is_mobile=is_mobile)
                                    for m in mods
                                ],
                            )
                        ],
                    ),
                )
            )
        if tiles:
            controls.extend(tiles)
        elif state.loading_detail and not state.sections:
            controls.append(build_course_tab_skeleton("sections"))
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
        elif state.loading_detail and not state.sections:
            controls.append(build_course_tab_skeleton("resources"))
        else:
            controls.append(
                ft.Container(
                    padding=ft.Padding.symmetric(vertical=24),
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text("Chưa có tài liệu hoặc liên kết phù hợp.", size=13, color=C.TEXT_SECONDARY),
                )
            )
        return controls

    def _build_grade_row(
        self, parsed: _ParsedGradeRow, *, is_mobile: bool = False
    ) -> ft.Control:
        return _shared_build_grade_row(parsed, is_mobile=is_mobile)

    def _build_grouped_grade_card(
        self, rows: list[_ParsedGradeRow], *, is_mobile: bool = False
    ) -> ft.Container:
        return _shared_build_grouped_grade_card(rows, is_mobile=is_mobile)

    def _build_grades_summary_hero(
        self,
        all_rows: list[_ParsedGradeRow],
        graded_rows: list[_ParsedGradeRow],
        pending_rows: list[_ParsedGradeRow],
        active_filter: str,
        *,
        is_mobile: bool = False,
    ) -> ft.Control:
        total_row = next((r for r in graded_rows if r.is_course_total), None)
        regular_all = [r for r in all_rows if not r.is_course_total]
        regular_graded = [r for r in graded_rows if not r.is_course_total]

        hero_score_10: Optional[float] = None
        hero_display = "—"
        hero_caption = "Chưa có hạng mục nào được chấm điểm"

        if total_row is not None and total_row.score_on_10 is not None:
            hero_score_10 = total_row.score_on_10
            hero_display = total_row.item.grade_formatted
            hero_caption = "Điểm tổng kết môn trên Moodle"
        elif regular_graded:
            weighted_pairs = [
                (r.score_on_10, r.weight_pct)
                for r in regular_graded
                if r.score_on_10 is not None and r.weight_pct > 0
            ]
            if weighted_pairs:
                w_sum = sum(w for _, w in weighted_pairs)
                hero_score_10 = round(
                    sum((s or 0.0) * w for s, w in weighted_pairs) / max(w_sum, 1e-6), 2
                )
                hero_caption = f"Trung bình có trọng số ({w_sum:.0f}% đã chấm)"
            else:
                valid_scores = [r.score_on_10 for r in regular_graded if r.score_on_10 is not None]
                if valid_scores:
                    hero_score_10 = round(sum(valid_scores) / len(valid_scores), 2)
                    hero_caption = "Trung bình các hạng mục đã chấm"
            if hero_score_10 is not None:
                hero_display = f"{hero_score_10:.2f}".rstrip("0").rstrip(".")

        badge_color = _score_semantic_color(hero_score_10)
        rank_text = _score_rank_label(hero_score_10)
        denom_count = len(regular_all) or len(all_rows)
        done_count = len(regular_graded) if regular_all else len(graded_rows)
        best_val = max(
            (r.score_on_10 for r in regular_graded if r.score_on_10 is not None),
            default=None,
        )
        best_str = f"{best_val:.1f}" if best_val is not None else "—"

        kpi_pills = [
            ft.Container(
                bgcolor=C.BG,
                border=ft.Border.all(1, C.BORDER),
                border_radius=10,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                content=ft.Column(
                    spacing=2,
                    horizontal_alignment=ft.CrossAxisAlignment.START,
                    controls=[
                        ft.Text("ĐÃ CÓ ĐIỂM", size=10, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY),
                        ft.Text(
                            f"{done_count}/{denom_count} mục",
                            size=14,
                            weight=ft.FontWeight.W_700,
                            color=C.TEXT_PRIMARY,
                        ),
                    ],
                ),
            ),
            ft.Container(
                bgcolor=C.BG,
                border=ft.Border.all(1, C.BORDER),
                border_radius=10,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                content=ft.Column(
                    spacing=2,
                    horizontal_alignment=ft.CrossAxisAlignment.START,
                    controls=[
                        ft.Text("CAO NHẤT", size=10, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY),
                        ft.Text(
                            best_str,
                            size=14,
                            weight=ft.FontWeight.W_700,
                            color=C.TEXT_PRIMARY,
                        ),
                    ],
                ),
            ),
        ]

        filter_opts: list[tuple[str, str]] = [
            ("all", f"Tất cả ({len(all_rows)})"),
            ("graded", f"Đã chấm ({len(graded_rows)})"),
        ]
        if pending_rows:
            filter_opts.append(("pending", f"Chưa chấm ({len(pending_rows)})"))

        hero_controls: list[ft.Control] = [
            ft.Row(
                wrap=True,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
                run_spacing=10,
                controls=[
                    ft.Column(
                        spacing=4,
                        controls=[
                            ft.Row(
                                wrap=True,
                                spacing=8,
                                run_spacing=6,
                                vertical_alignment=ft.CrossAxisAlignment.END,
                                controls=[
                                    ft.Text(
                                        hero_display,
                                        size=30,
                                        weight=ft.FontWeight.W_700,
                                        color=C.TEXT_PRIMARY,
                                    ),
                                    *(
                                        [
                                            ft.Container(
                                                padding=ft.Padding.only(bottom=5),
                                                content=ft.Text(
                                                    "/ 10",
                                                    size=13,
                                                    weight=ft.FontWeight.W_500,
                                                    color=C.TEXT_SECONDARY,
                                                ),
                                            )
                                        ]
                                        if hero_score_10 is not None
                                        else []
                                    ),
                                    ft.Container(
                                        margin=ft.Margin.only(bottom=5),
                                        bgcolor=ft.Colors.with_opacity(0.11, badge_color),
                                        border_radius=7,
                                        padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                                        content=ft.Text(
                                            rank_text,
                                            size=11,
                                            weight=ft.FontWeight.W_600,
                                            color=badge_color,
                                        ),
                                    ),
                                ],
                            ),
                            ft.Text(hero_caption, size=12, color=C.TEXT_SECONDARY),
                        ],
                    ),
                    ft.Row(wrap=True, spacing=8, run_spacing=8, controls=kpi_pills),
                ],
            )
        ]
        if len(all_rows) > 1:
            hero_controls.append(
                ft.Divider(height=1, thickness=0.6, color=C.BORDER)
            )
            hero_controls.append(
                self._build_filter_chips(tuple(filter_opts), active_filter, "grade_filter")
            )

        return ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=14,
            padding=ft.Padding.all(12 if is_mobile else 16),
            content=ft.Column(spacing=12, controls=hero_controls),
        )

    def _build_grades_tab(self, course: CourseSummary, state: CoursesState) -> list[ft.Control]:
        is_mobile = state.layout_mode == "mobile"
        grades_url = f"{compute_course_origin(course)}/grade/report/user/index.php?id={course.id}"
        if state.grades:
            parsed_all, graded_rows, pending_rows = _split_and_sort_grade_rows(state.grades)
            g_filter = (state.grade_filter or "all").strip().lower()
            if g_filter not in {"all", "graded", "pending"}:
                g_filter = "all"

            controls: list[ft.Control] = [
                self._build_grades_summary_hero(
                    parsed_all,
                    graded_rows,
                    pending_rows,
                    g_filter,
                    is_mobile=is_mobile,
                )
            ]

            if g_filter == "graded":
                if graded_rows:
                    controls.append(self._build_grouped_grade_card(graded_rows, is_mobile=is_mobile))
                else:
                    controls.append(
                        ft.Container(
                            padding=ft.Padding.symmetric(vertical=20),
                            alignment=ft.Alignment.CENTER,
                            content=ft.Text(
                                "Chưa có cột điểm nào được giảng viên chấm.",
                                size=13,
                                color=C.TEXT_SECONDARY,
                            ),
                        )
                    )
            elif g_filter == "pending":
                if pending_rows:
                    controls.append(self._build_grouped_grade_card(pending_rows, is_mobile=is_mobile))
                else:
                    controls.append(
                        ft.Container(
                            padding=ft.Padding.symmetric(vertical=20),
                            alignment=ft.Alignment.CENTER,
                            content=ft.Text(
                                "Tất cả hạng mục của môn học đã có điểm.",
                                size=13,
                                color=C.TEXT_SECONDARY,
                            ),
                        )
                    )
            else:
                if graded_rows:
                    controls.append(
                        ft.Text(
                            "ĐIỂM ĐÃ CẬP NHẬT",
                            size=11,
                            weight=ft.FontWeight.W_600,
                            color=C.TEXT_SECONDARY,
                        )
                    )
                    controls.append(self._build_grouped_grade_card(graded_rows, is_mobile=is_mobile))
                if pending_rows and graded_rows:
                    controls.append(
                        _shared_build_pending_grades_tile(pending_rows, is_mobile=is_mobile)
                    )
                elif pending_rows:
                    controls.append(self._build_grouped_grade_card(pending_rows, is_mobile=is_mobile))

            controls.append(
                ft.Container(
                    alignment=ft.Alignment.CENTER_RIGHT,
                    padding=ft.Padding.only(top=2),
                    content=ft.TextButton(
                        content="Mở bảng điểm gốc trên Moodle",
                        icon=ft.Icons.LANGUAGE_ROUNDED,
                        on_click=lambda _e, url=grades_url: (
                            self._on_launch_url(url) if callable(self._on_launch_url) else None
                        ),
                    ),
                )
            )
            return controls

        if state.loading_detail:
            return [build_course_tab_skeleton("grades")]

        return [
            ft.Container(
                bgcolor=C.SURFACE,
                border=ft.Border.all(1, C.BORDER),
                border_radius=14,
                padding=ft.Padding.all(24),
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
                            icon=ft.Icons.LANGUAGE_ROUNDED,
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
        is_mobile = state.layout_mode == "mobile"
        short_vp = is_compact_height(state.viewport_height)
        self.padding = ft.Padding.symmetric(
            horizontal=12 if is_mobile else 16,
            vertical=8 if short_vp else (12 if is_mobile else 16),
        )
        self._detail_column.spacing = 6 if short_vp else 10
        if isinstance(self._tabs.content, ft.Column):
            self._tabs.content.spacing = 6 if short_vp else 10

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
            self._notice_slot.controls = [
                build_offline_banner(
                    state.offline_notice,
                    on_retry=self._on_refresh_course,
                )
            ]
            self._notice_slot.visible = True
        else:
            self._notice_slot.controls = []
            self._notice_slot.visible = False

        active_idx = max(0, min(2, int(state.active_subtab or 0)))
        self._tabs.selected_index = active_idx
        self._segmented_track.bgcolor = C.SURFACE_ELEVATED
        self._segmented_track.border = ft.Border.all(1, C.BORDER)
        self._sync_segment_visuals(active_idx, state=state)
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
        self._segmented_track.bgcolor = C.SURFACE_ELEVATED
        self._segmented_track.border = ft.Border.all(1, C.BORDER)
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
        else:
            self._sync_segment_visuals(self._tabs.selected_index, state=self._last_state)
            if update:
                self._safe_update()
