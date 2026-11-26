"""Left-hand Master catalog pane for searching, filtering, and picking courses."""

from __future__ import annotations

import math
from typing import Callable, Optional

import flet as ft

from core.courses.models import CourseSummary, RecentMaterialEntry
from gui.components.courses._shared import (
    build_filter_chip,
    build_offline_banner,
    safe_control_update,
)
from gui.core.theme import C
from gui.view_models.courses_state import CoursesState, is_compact_height
import platform_utils


def _search_hint_text() -> str:
    if platform_utils.UI_IS_MOBILE:
        return "Tìm tên môn, mã HP, giảng viên..."
    return "Tìm tên môn, mã HP, giảng viên... (Ctrl+K)"


def _safe_progress(course: CourseSummary) -> Optional[float]:
    if course.progress is not None and not isinstance(course.progress, bool):
        try:
            val = float(course.progress)
        except (TypeError, ValueError):
            val = None
        else:
            if math.isfinite(val):
                return min(1.0, max(0.0, val))
    if (
        not isinstance(course.total_trackable, bool)
        and not isinstance(course.completed_count, bool)
        and course.total_trackable > 0
    ):
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
        self._card_map: dict[str, ft.Container] = {}
        self._prev_selected_key: Optional[str] = None
        self._last_cards_sig: Optional[tuple] = None
        self._recent_expanded: bool = False

        self._title_text = ft.Text(
            "Môn học", size=18, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY, expand=True
        )
        self._refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Làm mới danh sách môn học",
            width=44,
            height=44,
            padding=ft.Padding.all(8),
            size_constraints=ft.BoxConstraints(min_width=44, min_height=44),
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
            hint_text=_search_hint_text(),
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
        self._chips_row = ft.Row(wrap=True, spacing=6, run_spacing=6)
        self._progress_strip = ft.ProgressBar(
            value=None, bar_height=2, color=C.ACCENT, bgcolor=C.BORDER, visible=False
        )
        self._sync_status_note = ft.Text(
            "Đang đồng bộ danh sách môn học...",
            size=11,
            color=C.ACCENT,
            visible=False,
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
                self._sync_status_note,
                self._notice_slot,
                self._recent_slot,
                self._cards_list,
            ],
        )

    def _safe_update(self) -> None:
        safe_control_update(self)

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
        semesters = state.available_semesters()
        active_raw = (state.semester_filter or "all").strip()
        active_lower = active_raw.lower()
        has_current_option = (
            any(c.is_current_term for c in state.courses)
            or active_lower == "current"
            or len([s for s in semesters if s != "Khác"]) >= 2
        )
        options: list[tuple[str, str]] = []
        if has_current_option:
            cur_count = len(state.current_term_courses())
            cur_label = f"Kỳ hiện tại ({cur_count})" if cur_count > 0 else "Kỳ hiện tại"
            options.append(("current", cur_label))
        options.append(("all", "Tất cả"))
        options.extend((sem, sem) for sem in semesters)
        if any(c.source == "thnn" for c in state.courses) and not any(
            sem.lower() == "thnn" for sem in semesters
        ):
            options.append(("thnn", "THNN"))

        return [
            build_filter_chip(
                label,
                selected=(
                    active_lower == "current"
                    if key == "current"
                    else (
                        active_lower == "thnn"
                        if key.lower() == "thnn"
                        else active_raw == key
                    )
                ),
                on_select=self._handle_semester_select(key),
                bold_selected=True,
            )
            for key, label in options
        ]

    def _toggle_recent_expanded(self) -> None:
        self._recent_expanded = not self._recent_expanded
        if self._last_state is not None:
            self.render(self._last_state)

    @staticmethod
    def _recent_subtitle(entry: RecentMaterialEntry) -> str:
        fname = (entry.file_name or "").strip()
        if fname.lower() in {"index.html", "index.htm", "structure"}:
            fname = ""
        return fname or entry.title or "Tài liệu vừa xem"

    def _build_recent_banner(self, state: CoursesState) -> Optional[ft.Control]:
        if not state.recent_items:
            return None
        items = list(state.recent_items[:5])
        recent = items[0]
        subtitle = self._recent_subtitle(recent)
        primary_row = ft.Container(
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
                    *(
                        [
                            ft.IconButton(
                                icon=(
                                    ft.Icons.EXPAND_LESS_ROUNDED
                                    if self._recent_expanded
                                    else ft.Icons.EXPAND_MORE_ROUNDED
                                ),
                                icon_size=18,
                                icon_color=C.TEXT_SECONDARY,
                                tooltip=(
                                    "Thu gọn"
                                    if self._recent_expanded
                                    else f"Xem thêm {len(items) - 1} mục gần đây"
                                ),
                                width=44,
                                height=44,
                                padding=ft.Padding.all(8),
                                size_constraints=ft.BoxConstraints(min_width=44, min_height=44),
                                on_click=lambda _e: self._toggle_recent_expanded(),
                            )
                        ]
                        if len(items) > 1
                        else [
                            ft.Icon(
                                ft.Icons.PLAY_CIRCLE_OUTLINE_ROUNDED,
                                size=18,
                                color=C.TEXT_SECONDARY,
                            )
                        ]
                    ),
                ],
            ),
        )
        banner_controls: list[ft.Control] = [primary_row]
        if self._recent_expanded and len(items) > 1:
            for extra in items[1:]:
                extra_sub = self._recent_subtitle(extra)
                banner_controls.append(ft.Divider(height=1, thickness=0.6, color=C.BORDER))
                banner_controls.append(
                    ft.Container(
                        ink=True,
                        padding=ft.Padding.symmetric(vertical=4),
                        on_click=(
                            (lambda _e, entry=extra: self._on_open_recent(entry))
                            if callable(self._on_open_recent)
                            else None
                        ),
                        content=ft.Row(
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Icon(
                                    ft.Icons.ARTICLE_OUTLINED,
                                    size=15,
                                    color=C.TEXT_SECONDARY,
                                ),
                                ft.Text(
                                    f"{extra.course_name} • {extra_sub}",
                                    size=12,
                                    color=C.TEXT_PRIMARY,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    expand=True,
                                ),
                            ],
                        ),
                    )
                )
        return ft.Container(
            bgcolor=C.SURFACE_ELEVATED,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            content=ft.Column(spacing=4, controls=banner_controls),
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

    @staticmethod
    def _card_selection_Visuals(selected: bool) -> tuple[str, ft.Border]:
        if selected:
            return (
                ft.Colors.with_opacity(0.09, C.ACCENT),
                ft.Border.all(1.5, C.ACCENT),
            )
        return (C.SURFACE, ft.Border.all(1, C.BORDER))

    def _build_course_card(
        self, course: CourseSummary, *, selected: bool, is_mobile: bool = False
    ) -> ft.Container:
        badges: list[ft.Control] = []
        if course.source == "thnn":
            badges.append(_badge_pill("THNN", bgcolor=C.ACCENT, text_color=C.ON_ACCENT))
        elif course.program_tag:
            badges.append(_badge_pill(course.program_tag, bgcolor=C.SURFACE_ELEVATED, text_color=C.TEXT_SECONDARY))
        if course.semester_label:
            badges.append(_badge_pill(course.semester_label, bgcolor=C.SURFACE_ELEVATED, text_color=C.TEXT_SECONDARY))
        if course.credits > 0:
            badges.append(
                _badge_pill(
                    f"{course.credits} TC",
                    bgcolor=ft.Colors.with_opacity(0.10, C.ACCENT),
                    text_color=C.ACCENT,
                )
            )
        if course.has_unseen_updates:
            badges.append(_badge_pill("Mới", bgcolor=C.UPDATE, text_color=C.ON_UPDATE))

        subtitle_parts = [p for p in (course.course_code, course.teacher_name) if p]
        top_controls: list[ft.Control] = [
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
                width=44,
                height=44,
                padding=ft.Padding.all(8),
                size_constraints=ft.BoxConstraints(min_width=44, min_height=44),
                on_click=lambda _e, key=course.course_key: (
                    self._on_toggle_pin(key) if callable(self._on_toggle_pin) else None
                ),
            ),
        ]
        if is_mobile:
            top_controls.append(
                ft.Icon(
                    ft.Icons.CHEVRON_RIGHT_ROUNDED,
                    size=18,
                    color=C.TEXT_SECONDARY,
                )
            )
        card_controls: list[ft.Control] = [
            ft.Row(
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=top_controls,
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
            if pct == 0 and course.total_trackable > 0:
                label = f"{course.total_trackable} mục học tập"
            elif course.total_trackable > 0:
                label = f"{course.completed_count}/{course.total_trackable} hoàn thành ({pct}%)"
            else:
                label = f"Tiến độ {pct}%"
            bar_color = C.SAFE if progress_val >= 0.999 else C.ACCENT
            card_controls.append(
                ft.Column(
                    spacing=4,
                    controls=[
                        ft.ProgressBar(
                            value=progress_val,
                            bar_height=3,
                            border_radius=2,
                            color=bar_color,
                            bgcolor=C.BORDER,
                        ),
                        ft.Text(label, size=11, color=C.TEXT_SECONDARY),
                    ],
                )
            )

        card_bg, card_border = self._card_selection_Visuals(selected)
        return ft.Container(
            bgcolor=card_bg,
            border=card_border,
            border_radius=14,
            padding=ft.Padding.symmetric(horizontal=13, vertical=11),
            animate=ft.Animation(160, ft.AnimationCurve.EASE_OUT_CUBIC),
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

    def render(self, state: CoursesState, *, update: bool = True) -> None:
        """Render search bar, chips, notices, recent resume, and filtered courses."""
        self._last_state = state
        self._search_field.hint_text = _search_hint_text()
        if (self._search_field.value or "") != state.search_query:
            self._search_field.value = state.search_query
        self._clear_search_btn.visible = bool(state.search_query)

        is_mobile = state.layout_mode == "mobile"
        short_vp = is_compact_height(state.viewport_height)
        scroll_banners = is_mobile or short_vp
        self.padding = ft.Padding.symmetric(horizontal=12, vertical=6 if short_vp else 12)
        if isinstance(self.content, ft.Column):
            self.content.spacing = 6 if short_vp else 10

        self._chips_row.wrap = not short_vp
        self._chips_row.scroll = ft.ScrollMode.AUTO if short_vp else None
        self._chips_row.controls = self._build_semester_chips(state)
        self._progress_strip.visible = bool(state.loading_catalog)
        self._sync_status_note.visible = bool(state.loading_catalog)

        offline_banner = (
            build_offline_banner(
                state.offline_notice,
                on_retry=self._on_refresh_catalog,
            )
            if state.offline_notice
            else None
        )
        recent_banner = self._build_recent_banner(state)

        if scroll_banners:
            self._notice_slot.controls = []
            self._notice_slot.visible = False
            self._recent_slot.controls = []
            self._recent_slot.visible = False
            top_banners = [b for b in (offline_banner, recent_banner) if b is not None]
        else:
            self._notice_slot.controls = [offline_banner] if offline_banner is not None else []
            self._notice_slot.visible = offline_banner is not None
            self._recent_slot.controls = [recent_banner] if recent_banner is not None else []
            self._recent_slot.visible = recent_banner is not None
            top_banners = []

        effective_selected_key = (
            state.selected_course_key
            if (not is_mobile or state.in_detail_mobile)
            else None
        )

        filtered = state.filtered_courses()
        if state.loading_catalog and not state.courses:
            self._card_map.clear()
            self._last_cards_sig = None
            self._cards_list.controls = [*top_banners, *self._build_skeleton_cards()]
        elif not filtered:
            self._card_map.clear()
            self._last_cards_sig = None
            self._cards_list.controls = [*top_banners, self._build_empty_state(state)]
        else:
            banner_sig = (
                (
                    state.offline_notice,
                    tuple(
                        (r.course_key, r.cmid, r.file_name, r.opened_at)
                        for r in state.recent_items[:5]
                    ),
                    self._recent_expanded,
                )
                if scroll_banners
                else None
            )
            cur_sig = (
                C.BG,
                C.SURFACE,
                C.ACCENT,
                C.BORDER,
                is_mobile,
                scroll_banners,
                banner_sig,
                tuple(
                    (
                        c.course_key,
                        c.display_name,
                        c.is_pinned,
                        c.has_unseen_updates,
                        c.progress,
                        c.completed_count,
                        c.total_trackable,
                        c.teacher_name,
                        c.credits,
                    )
                    for c in filtered
                ),
            )
            if cur_sig == self._last_cards_sig and len(self._card_map) == len(filtered):
                # O(1) in-place selection update: zero widget tree rebuild & preserves scroll!
                if self._prev_selected_key != effective_selected_key:
                    for key_change, is_sel in (
                        (self._prev_selected_key, False),
                        (effective_selected_key, True),
                    ):
                        if key_change and key_change in self._card_map:
                            bg, bdr = self._card_selection_Visuals(is_sel)
                            card_ref = self._card_map[key_change]
                            card_ref.bgcolor = bg
                            card_ref.border = bdr
                    self._prev_selected_key = effective_selected_key
            else:
                new_map: dict[str, ft.Container] = {}
                new_controls: list[ft.Control] = list(top_banners)
                for course in filtered:
                    card_ctrl = self._build_course_card(
                        course,
                        selected=(course.course_key == effective_selected_key),
                        is_mobile=is_mobile,
                    )
                    new_map[course.course_key] = card_ctrl
                    new_controls.append(card_ctrl)
                self._card_map = new_map
                self._prev_selected_key = effective_selected_key
                self._last_cards_sig = cur_sig
                self._cards_list.controls = new_controls
        if update:
            self._safe_update()

    def update_theme(self, *, update: bool = True, rerender: bool = True) -> None:
        """Apply current global theme palette C to persistent controls and re-render."""
        self.bgcolor = C.BG
        self._title_text.color = C.TEXT_PRIMARY
        self._refresh_btn.icon_color = C.TEXT_SECONDARY
        self._clear_search_btn.icon_color = C.TEXT_SECONDARY
        self._search_field.hint_text = _search_hint_text()
        self._search_field.bgcolor = C.SURFACE
        self._search_field.color = C.TEXT_PRIMARY
        self._search_field.border_color = C.BORDER
        self._search_field.focused_border_color = C.ACCENT
        self._search_field.hint_style = ft.TextStyle(color=C.TEXT_SECONDARY, size=12)
        self._progress_strip.color = C.ACCENT
        self._progress_strip.bgcolor = C.BORDER
        self._sync_status_note.color = C.ACCENT
        if rerender and self._last_state is not None:
            self.render(self._last_state, update=update)
        elif update:
            self._safe_update()
