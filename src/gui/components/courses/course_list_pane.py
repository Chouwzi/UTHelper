"""Left-hand Master catalog pane for searching, filtering, and picking courses."""

from __future__ import annotations

import math
from typing import Callable, Optional

import flet as ft

from core.courses.models import CourseSummary, RecentMaterialEntry
from gui.core.theme import C
from gui.view_models.courses_state import CoursesState


def _safe_progress(course: CourseSummary) -> Optional[float]:
    if course.progress is not None:
        try:
            val = float(course.progress)
        except (TypeError, ValueError):
            val = None
        else:
            if math.isfinite(val):
                return min(1.0, max(0.0, val))
    if course.total_trackable > 0:
        return min(1.0, max(0.0, course.completed_count / course.total_trackable))
    return None


def _badge_pill(text: str, *, bgcolor: str, text_color: str) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=10, weight=ft.FontWeight.W_600, color=text_color),
        bgcolor=bgcolor,
        border_radius=6,
        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
    )


class CourseListPane(ft.Container):
    """Master catalog pane showing search, semester chips, recent resume, and courses."""

    def __init__(
        self,
        *,
        on_select_course: Optional[Callable[[str], None]] = None,
        on_toggle_pin: Optional[Callable[[str], None]] = None,
        on_open_recent: Optional[Callable[[RecentMaterialEntry], None]] = None,
        on_filter_change: Optional[Callable[[str, str], None]] = None,
        on_refresh_catalog: Optional[Callable[[], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(expand=True, **kwargs)
        self._on_select_course = on_select_course
        self._on_toggle_pin = on_toggle_pin
        self._on_open_recent = on_open_recent
        self._on_filter_change = on_filter_change
        self._on_refresh_catalog = on_refresh_catalog
        self._last_state: Optional[CoursesState] = None

        self._title_text = ft.Text(
            "Môn học", size=18, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY, expand=True
        )
        self._refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Làm mới danh sách môn học",
            on_click=lambda _e: (
                self._on_refresh_catalog() if callable(self._on_refresh_catalog) else None
            ),
        )
        self._clear_search_btn = ft.IconButton(
            icon=ft.Icons.CLEAR_ROUNDED,
            icon_size=16,
            icon_color=C.TEXT_SECONDARY,
            tooltip="Xóa tìm kiếm",
            visible=False,
            on_click=self._handle_clear_search,
        )
        self._search_field = ft.TextField(
            hint_text="Tìm tên môn, mã HP, giảng viên... (Ctrl+K)",
            prefix_icon=ft.Icons.SEARCH_ROUNDED,
            suffix=self._clear_search_btn,
            dense=True,
            text_size=13,
            border_radius=10,
            bgcolor=C.SURFACE,
            color=C.TEXT_PRIMARY,
            border_color=C.BORDER,
            focused_border_color=C.ACCENT,
            hint_style=ft.TextStyle(color=C.TEXT_SECONDARY, size=12),
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            on_change=self._handle_search_change,
        )
        self._chips_row = ft.Row(scroll=ft.ScrollMode.AUTO, spacing=6)
        self._progress_strip = ft.ProgressBar(
            value=None, bar_height=2, color=C.ACCENT, bgcolor=C.BORDER, visible=False
        )
        self._notice_slot = ft.Column(spacing=6, visible=False)
        self._recent_slot = ft.Column(spacing=6, visible=False)
        self._cards_list = ft.Column(expand=True, spacing=8, scroll=ft.ScrollMode.AUTO)

        self.bgcolor = C.BG
        self.padding = ft.Padding.all(12)
        self.content = ft.Column(
            expand=True,
            spacing=10,
            controls=[
                ft.Row(
                    controls=[self._title_text, self._refresh_btn],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self._search_field,
                self._chips_row,
                self._progress_strip,
                self._notice_slot,
                self._recent_slot,
                self._cards_list,
            ],
        )

    def _safe_update(self) -> None:
        try:
            if self.page is not None:
                self.update()
        except Exception:
            pass

    def _handle_search_change(self, e) -> None:
        query = str(getattr(e.control, "value", "") or "")
        self._clear_search_btn.visible = bool(query)
        sem = self._last_state.semester_filter if self._last_state else "all"
        if callable(self._on_filter_change):
            self._on_filter_change(sem, query)

    def _handle_clear_search(self, _e) -> None:
        self._search_field.value = ""
        self._clear_search_btn.visible = False
        sem = self._last_state.semester_filter if self._last_state else "all"
        if callable(self._on_filter_change):
            self._on_filter_change(sem, "")

    def _handle_semester_select(self, semester_key: str):
        def _handler(_e) -> None:
            query = self._search_field.value or ""
            if callable(self._on_filter_change):
                self._on_filter_change(semester_key, query)

        return _handler

    async def focus_search(self) -> None:
        """Focus the catalog search field cleanly."""
        try:
            await self._search_field.focus()
        except Exception:
            pass

    def _build_semester_chips(self, state: CoursesState) -> list[ft.Control]:
        options: list[tuple[str, str]] = [("all", "Tất cả")]
        semesters = state.available_semesters()
        options.extend((sem, sem) for sem in semesters)
        if any(c.source == "thnn" for c in state.courses) and "THNN" not in semesters:
            options.append(("THNN", "THNN"))

        chips: list[ft.Control] = []
        for key, label in options:
            selected = state.semester_filter == key
            chips.append(
                ft.Chip(
                    label=ft.Text(
                        label,
                        size=12,
                        weight=ft.FontWeight.W_600 if selected else ft.FontWeight.W_400,
                        color=C.ON_ACCENT if selected else C.TEXT_PRIMARY,
                    ),
                    selected=selected,
                    show_checkmark=False,
                    selected_color=C.ACCENT,
                    bgcolor=C.SURFACE,
                    border_side=ft.BorderSide(1, C.ACCENT if selected else C.BORDER),
                    on_select=self._handle_semester_select(key),
                )
            )
        return chips

    def _build_recent_banner(self, state: CoursesState) -> Optional[ft.Control]:
        if not state.recent_items:
            return None
        recent = state.recent_items[0]
        subtitle = recent.file_name or recent.title or "Tài liệu vừa xem"
        return ft.Container(
            bgcolor=C.SURFACE_ELEVATED,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            ink=True,
            on_click=(
                (lambda _e, entry=recent: self._on_open_recent(entry))
                if callable(self._on_open_recent)
                else None
            ),
            content=ft.Row(
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.HISTORY_ROUNDED, size=18, color=C.ACCENT),
                    ft.Column(
                        expand=True,
                        spacing=1,
                        controls=[
                            ft.Text("Tiếp tục nhanh", size=11, weight=ft.FontWeight.W_700, color=C.ACCENT),
                            ft.Text(
                                f"{recent.course_name} • {subtitle}",
                                size=12,
                                color=C.TEXT_PRIMARY,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                    ),
                    ft.Icon(ft.Icons.PLAY_CIRCLE_OUTLINE_ROUNDED, size=18, color=C.TEXT_SECONDARY),
                ],
            ),
        )

    def _build_skeleton_cards(self) -> list[ft.Control]:
        return [
            ft.Container(
                bgcolor=C.SURFACE,
                border=ft.Border.all(1, C.BORDER),
                border_radius=12,
                padding=ft.Padding.all(12),
                content=ft.Column(
                    spacing=8,
                    controls=[
                        ft.Container(height=14, width=200, bgcolor=C.SURFACE_HOVER, border_radius=4),
                        ft.Container(height=10, width=130, bgcolor=C.SURFACE_HOVER, border_radius=4),
                        ft.Container(height=3, bgcolor=C.BORDER, border_radius=2),
                    ],
                ),
            )
            for _ in range(4)
        ]

    def _build_course_card(self, course: CourseSummary, *, selected: bool) -> ft.Control:
        badges: list[ft.Control] = []
        if course.source == "thnn":
            badges.append(_badge_pill("THNN", bgcolor=C.ACCENT, text_color=C.ON_ACCENT))
        elif course.program_tag:
            badges.append(_badge_pill(course.program_tag, bgcolor=C.SURFACE_ELEVATED, text_color=C.TEXT_SECONDARY))
        if course.semester_label:
            badges.append(_badge_pill(course.semester_label, bgcolor=C.SURFACE_ELEVATED, text_color=C.TEXT_SECONDARY))
        if course.has_unseen_updates:
            badges.append(_badge_pill("Mới", bgcolor=C.UPDATE, text_color=C.ON_UPDATE))

        subtitle_parts = [p for p in (course.course_code, course.teacher_name) if p]
        card_controls: list[ft.Control] = [
            ft.Row(
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Text(
                        course.display_name or course.fullname,
                        size=14,
                        weight=ft.FontWeight.W_600,
                        color=C.TEXT_PRIMARY,
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        expand=True,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.STAR_ROUNDED if course.is_pinned else ft.Icons.STAR_BORDER_ROUNDED,
                        icon_color=C.WARNING if course.is_pinned else C.TEXT_SECONDARY,
                        icon_size=18,
                        tooltip="Bỏ ghim" if course.is_pinned else "Ghim môn học",
                        on_click=lambda _e, key=course.course_key: (
                            self._on_toggle_pin(key) if callable(self._on_toggle_pin) else None
                        ),
                    ),
                ],
            )
        ]
        if badges:
            card_controls.append(ft.Row(controls=badges, wrap=True, spacing=4, run_spacing=4))
        if subtitle_parts:
            card_controls.append(
                ft.Text(
                    " • ".join(subtitle_parts),
                    size=12,
                    color=C.TEXT_SECONDARY,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                )
            )

        progress_val = _safe_progress(course)
        if progress_val is not None:
            pct = int(round(progress_val * 100))
            label = (
                f"{course.completed_count}/{course.total_trackable} hoàn thành ({pct}%)"
                if course.total_trackable > 0
                else f"Tiến độ {pct}%"
            )
            card_controls.append(
                ft.Column(
                    spacing=4,
                    controls=[
                        ft.ProgressBar(value=progress_val, bar_height=3, color=C.ACCENT, bgcolor=C.BORDER),
                        ft.Text(label, size=11, color=C.TEXT_SECONDARY),
                    ],
                )
            )

        return ft.Container(
            bgcolor=C.SURFACE_HOVER if selected else C.SURFACE,
            border=ft.Border.all(1.5 if selected else 1, C.ACCENT if selected else C.BORDER),
            border_radius=12,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            ink=True,
            on_click=(
                (lambda _e, key=course.course_key: self._on_select_course(key))
                if callable(self._on_select_course)
                else None
            ),
            content=ft.Column(spacing=6, controls=card_controls),
        )

    def _build_empty_state(self, state: CoursesState) -> ft.Control:
        has_filters = bool(state.courses) and (
            state.semester_filter != "all" or bool(state.search_query.strip())
        )
        msg = (
            "Không tìm thấy môn học phù hợp bộ lọc."
            if has_filters
            else "Chưa có môn học nào. Nhấn làm mới để tải danh sách."
        )
        controls: list[ft.Control] = [
            ft.Icon(ft.Icons.SCHOOL_OUTLINED, size=36, color=C.TEXT_SECONDARY),
            ft.Text(msg, size=13, color=C.TEXT_SECONDARY, text_align=ft.TextAlign.CENTER),
        ]
        if has_filters:
            controls.append(
                ft.TextButton(
                    content="Xóa bộ lọc",
                    on_click=lambda _e: (
                        self._on_filter_change("all", "") if callable(self._on_filter_change) else None
                    ),
                )
            )
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=32, horizontal=16),
            alignment=ft.Alignment.CENTER,
            content=ft.Column(spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=controls),
        )

    def render(self, state: CoursesState) -> None:
        """Render search bar, chips, notices, recent resume, and filtered courses."""
        self._last_state = state
        if (self._search_field.value or "") != state.search_query:
            self._search_field.value = state.search_query
        self._clear_search_btn.visible = bool(state.search_query)

        self._chips_row.controls = self._build_semester_chips(state)
        self._progress_strip.visible = bool(state.loading_catalog and state.courses)

        if state.offline_notice:
            self._notice_slot.controls = [
                ft.Container(
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.WARNING),
                    border_radius=10,
                    padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                    content=ft.Row(
                        spacing=8,
                        controls=[
                            ft.Icon(ft.Icons.WIFI_OFF_ROUNDED, size=16, color=C.WARNING),
                            ft.Text(state.offline_notice, size=12, color=C.TEXT_PRIMARY, expand=True),
                        ],
                    ),
                )
            ]
            self._notice_slot.visible = True
        else:
            self._notice_slot.controls = []
            self._notice_slot.visible = False

        recent_banner = self._build_recent_banner(state)
        self._recent_slot.controls = [recent_banner] if recent_banner is not None else []
        self._recent_slot.visible = recent_banner is not None

        filtered = state.filtered_courses()
        if state.loading_catalog and not state.courses:
            self._cards_list.controls = self._build_skeleton_cards()
        elif not filtered:
            self._cards_list.controls = [self._build_empty_state(state)]
        else:
            self._cards_list.controls = [
                self._build_course_card(course, selected=(course.course_key == state.selected_course_key))
                for course in filtered
            ]
        self._safe_update()

    def update_theme(self) -> None:
        """Apply current global theme palette C to persistent controls and re-render."""
        self.bgcolor = C.BG
        self._title_text.color = C.TEXT_PRIMARY
        self._refresh_btn.icon_color = C.TEXT_SECONDARY
        self._clear_search_btn.icon_color = C.TEXT_SECONDARY
        self._search_field.bgcolor = C.SURFACE
        self._search_field.color = C.TEXT_PRIMARY
        self._search_field.border_color = C.BORDER
        self._search_field.focused_border_color = C.ACCENT
        self._search_field.hint_style = ft.TextStyle(color=C.TEXT_SECONDARY, size=12)
        self._progress_strip.color = C.ACCENT
        self._progress_strip.bgcolor = C.BORDER
        if self._last_state is not None:
            self.render(self._last_state)
        else:
            self._safe_update()
