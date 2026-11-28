"""CalendarView - Monthly calendar grid showing activity deadlines.

UX Audit Fixes Applied:
- CV-01: Denser grid (44px cells, 2px spacing)
- CV-02: Today highlight more prominent (ring + bold)
- CV-03: Larger dots (6px) + count badge for 3+ activities
- CV-04: Clean course name display
- CV-05: Month summary counter in header area
- CV-06: Better empty state microcopy
- CV-07: Days with deadlines have subtle bg hint
- CV-08: Color bar full height
"""
import os
import sys

# Patch path for direct execution / Flet preview compatibility
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import asyncio
import calendar
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Literal, Optional

import flet as ft
import platform_utils

from core.today_schedule import ClassSession
from gui.components.calendar_navigation import (
    MonthAnchor,
    month_slot_anchors,
    shift_month,
    shift_week,
    week_slot_anchors,
)
from gui.components.schedule_week_view import (
    ScheduleWeekView,
    classify_session_shift,
    compact_room_badge_text,
)
from gui.core.theme import (
    C,
    on_color,
    refresh_semantic_control_colors,
    semantic_palette,
)
from gui.core.utils import (
    get_countdown,
    get_urgency_color,
    parse_datetime,
)

_COMPACT_SHELL_BREAKPOINT = 600
_WEEKDAYS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
_WEEKDAYS_LONG_VI = (
    "Thứ 2",
    "Thứ 3",
    "Thứ 4",
    "Thứ 5",
    "Thứ 6",
    "Thứ 7",
    "Chủ nhật",
)
_MONTHS_VI = {
    1: "Tháng 1", 2: "Tháng 2", 3: "Tháng 3", 4: "Tháng 4",
    5: "Tháng 5", 6: "Tháng 6", 7: "Tháng 7", 8: "Tháng 8",
    9: "Tháng 9", 10: "Tháng 10", 11: "Tháng 11", 12: "Tháng 12",
}


def _mondays_overlapping_month(year: int, month: int) -> list[date]:
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    cursor = first_day - timedelta(days=first_day.weekday())
    end_monday = last_day - timedelta(days=last_day.weekday())
    mondays: list[date] = []
    while cursor <= end_monday:
        mondays.append(cursor)
        cursor += timedelta(days=7)
    return mondays


@dataclass(slots=True)
class _CalendarPeriodPage:
    anchor: MonthAnchor | date
    control: ft.Control
    weekday_labels: list[ft.Text] = field(default_factory=list)
    day_cells: list[list[ft.Container]] = field(default_factory=list)
    week_cells: list[ft.Container] = field(default_factory=list)


class CalendarView(ft.Container):
    """Full-screen calendar overlay for viewing activity deadlines by date."""

    def __init__(
        self,
        page: ft.Page,
        on_close: Optional[Callable] = None,
        on_open_detail: Optional[Callable] = None,
        fetch_schedule_week: Optional[Callable] = None,
        schedule_cache: Optional[Any] = None,
        on_open_url: Optional[Callable[[str, str], None]] = None,
        on_open_external: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(expand=True, visible=False, bgcolor=C.BG)
        self._page_ref = page
        self._on_close = on_close
        self._on_open_detail = on_open_detail

        today = date.today()
        self._year = today.year
        self._month = today.month
        self._selected_day: Optional[int] = today.day
        self._all_data: List[Dict[str, Any]] = []
        self._deadline_map: Dict[str, List[Dict[str, Any]]] = {}
        # Week view state
        self._view_mode: str = "month"  # "month" or "week"
        self._week_start: date = today - timedelta(days=today.weekday())  # Monday
        self._selected_date: Optional[date] = today  # full date for week view
        self._detail_filter_mode: Literal["day", "week_classes", "tasks"] = "day"
        self._last_measured_width: float = 0.0
        self._rendering_calendar: bool = False

        self._schedule_view = ScheduleWeekView(
            page,
            fetch_schedule_week,
            schedule_cache,
            on_open_url=on_open_url,
            on_open_external=on_open_external,
            on_weeks_changed=self._on_schedule_weeks_changed,
        )

        self._force_period_rebuild = False
        self._defer_page_update = False

        self._build_ui()
        self.on_size_change = self._on_size_change
        self._rendered_palette = semantic_palette()

    # Build UI
    def _build_ui(self):
        # Month navigation header
        self._month_label = ft.Text(
            "",
            size=15,
            weight=ft.FontWeight.W_600,
            color=C.TEXT_PRIMARY,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            text_align=ft.TextAlign.CENTER,
        )
        # Month summary (CV-05)
        self._month_summary = ft.Text(
            "", size=10, color=C.TEXT_SECONDARY,
        )
        icon_constraints = ft.BoxConstraints(
            min_width=44, max_width=44, min_height=44, max_height=44
        )
        self._back_btn = ft.IconButton(
            ft.Icons.ARROW_BACK_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=18,
            tooltip="Quay lại",
            padding=0,
            size_constraints=icon_constraints,
            on_click=lambda _: self._on_close() if self._on_close else None,
        )
        self._previous_btn = ft.IconButton(
            ft.Icons.CHEVRON_LEFT_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Trước",
            padding=0,
            size_constraints=icon_constraints,
            on_click=self._on_previous_period,
        )
        self._next_btn = ft.IconButton(
            ft.Icons.CHEVRON_RIGHT_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Sau",
            padding=0,
            size_constraints=icon_constraints,
            on_click=self._on_next_period,
        )
        self._today_btn = ft.IconButton(
            ft.Icons.TODAY_ROUNDED,
            icon_color=C.ACCENT,
            icon_size=18,
            tooltip="Hôm nay",
            padding=0,
            size_constraints=icon_constraints,
            on_click=lambda _: self._go_today(),
        )

        self._header = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Semantics(
                                label="Quay lại",
                                button=True,
                                content=self._back_btn,
                            ),
                            ft.Row(
                                controls=[
                                    ft.Semantics(
                                        label="Kỳ trước",
                                        button=True,
                                        content=self._previous_btn,
                                    ),
                                    ft.Container(
                                        content=self._month_label,
                                        expand=True,
                                        alignment=ft.Alignment(0, 0),
                                    ),
                                    ft.Semantics(
                                        label="Kỳ sau",
                                        button=True,
                                        content=self._next_btn,
                                    ),
                                ],
                                spacing=0,
                                expand=True,
                                alignment=ft.MainAxisAlignment.CENTER,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Semantics(
                                label="Hôm nay",
                                button=True,
                                content=self._today_btn,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    # Month summary row (CV-05)
                    ft.Container(
                        content=self._month_summary,
                        alignment=ft.Alignment(0, 0),
                        padding=ft.Padding.only(bottom=2),
                    ),
                ],
                spacing=0,
            ),
            padding=ft.Padding.only(left=4, right=4, top=20, bottom=0),
            bgcolor=C.BG,
        )

        # Weekday labels row
        self._weekday_row = ft.Row(
            controls=[
                ft.Container(
                    content=ft.Text(
                        wd, size=10, weight=ft.FontWeight.W_600,
                        color=C.CRITICAL if wd == "CN" else C.TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    width=44, alignment=ft.Alignment(0, 0),
                )
                for wd in _WEEKDAYS
            ],
            spacing=2,
            alignment=ft.MainAxisAlignment.CENTER,
        )

        # Mode toggle (Month / Week)
        self._mode_month_icon = ft.Icon(
            ft.Icons.CALENDAR_MONTH_ROUNDED, size=14, color=C.TEXT_PRIMARY
        )
        self._mode_month_text = ft.Text(
            "Tháng", size=11, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY
        )
        self._mode_month_btn = ft.Container(
            content=ft.Row(controls=[
                self._mode_month_icon,
                self._mode_month_text,
            ], spacing=4, tight=True),
            bgcolor=ft.Colors.with_opacity(0.19, C.ACCENT),
            border_radius=12,
            height=44,
            alignment=ft.Alignment(0, 0),
            padding=ft.Padding(left=12, right=12, top=0, bottom=0),
            on_click=lambda _: self._toggle_view_mode("month"),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        )
        self._mode_week_icon = ft.Icon(
            ft.Icons.VIEW_WEEK_ROUNDED, size=14, color=C.TEXT_SECONDARY
        )
        self._mode_week_text = ft.Text(
            "Tuần", size=11, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY
        )
        self._mode_week_btn = ft.Container(
            content=ft.Row(controls=[
                self._mode_week_icon,
                self._mode_week_text,
            ], spacing=4, tight=True),
            bgcolor="transparent",
            border_radius=12,
            height=44,
            alignment=ft.Alignment(0, 0),
            padding=ft.Padding(left=12, right=12, top=0, bottom=0),
            on_click=lambda _: self._toggle_view_mode("week"),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        )
        self._mode_schedule_icon = ft.Icon(
            ft.Icons.SCHOOL_ROUNDED, size=14, color=C.TEXT_SECONDARY
        )
        self._mode_schedule_text = ft.Text(
            "Lịch học", size=11, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY
        )
        self._mode_schedule_btn = ft.Container(
            content=ft.Row(controls=[
                self._mode_schedule_icon,
                self._mode_schedule_text,
            ], spacing=4, tight=True),
            bgcolor="transparent",
            border_radius=12,
            height=44,
            visible=not self._should_hide_separate_schedule_tab(),
            alignment=ft.Alignment(0, 0),
            padding=ft.Padding(left=12, right=12, top=0, bottom=0),
            on_click=lambda _: self._toggle_view_mode("schedule"),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        )
        self._mode_toggle = ft.Container(
            content=ft.Row(
                [self._mode_month_btn, self._mode_week_btn, self._mode_schedule_btn],
                spacing=2,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            bgcolor=C.SURFACE,
            border_radius=14,
            padding=ft.Padding(left=3, right=3, top=3, bottom=3),
            border=ft.Border.all(1, C.BORDER),
            margin=ft.Margin(left=12, right=12, top=4, bottom=6),
        )

        # Calendar grid (6 rows × 7 cols) - CV-01: denser
        self._grid_rows: List[ft.Row] = []
        self._day_cells: List[List[ft.Container]] = []
        for _ in range(6):
            row_cells = []
            row_controls = []
            for _ in range(7):
                cell = self._make_day_cell()
                row_cells.append(cell)
                row_controls.append(cell)
            self._day_cells.append(row_cells)
            row = ft.Row(controls=row_controls, spacing=2, alignment=ft.MainAxisAlignment.CENTER)
            self._grid_rows.append(row)

        self._grid = ft.Column(
            controls=[self._weekday_row] + self._grid_rows,
            spacing=2,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Week strip (7 day-columns)
        self._week_cells: List[ft.Container] = []
        week_cell_controls = []
        for i in range(7):
            cell = self._make_week_cell(i)
            self._week_cells.append(cell)
            week_cell_controls.append(cell)

        self._week_strip = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=week_cell_controls,
                        spacing=3,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ],
                spacing=2,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            visible=False,  # Hidden by default (month mode)
        )

        # Selected day detail panel
        self._filter_day_text = ft.Text(
            "Theo ngày", size=11, weight=ft.FontWeight.W_600, color=C.ACCENT
        )
        self._filter_day_chip = ft.Container(
            content=self._filter_day_text,
            height=30,
            padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            border_radius=999,
            alignment=ft.Alignment(0, 0),
            bgcolor=ft.Colors.with_opacity(0.16, C.ACCENT),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.35, C.ACCENT)),
            ink=True,
            data={"filter_mode": "day"},
            on_click=lambda _: self._set_detail_filter_mode("day"),
        )
        self._filter_week_text = ft.Text(
            "Cả tuần (0 ca)",
            size=11,
            weight=ft.FontWeight.W_500,
            color=C.TEXT_SECONDARY,
        )
        self._filter_week_chip = ft.Container(
            content=self._filter_week_text,
            height=30,
            padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            border_radius=999,
            alignment=ft.Alignment(0, 0),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            ink=True,
            data={"filter_mode": "week_classes"},
            on_click=lambda _: self._set_detail_filter_mode("week_classes"),
        )
        self._filter_tasks_text = ft.Text(
            "Bài tập (0)",
            size=11,
            weight=ft.FontWeight.W_500,
            color=C.TEXT_SECONDARY,
        )
        self._filter_tasks_chip = ft.Container(
            content=self._filter_tasks_text,
            height=30,
            padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            border_radius=999,
            alignment=ft.Alignment(0, 0),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            ink=True,
            data={"filter_mode": "tasks"},
            on_click=lambda _: self._set_detail_filter_mode("tasks"),
        )
        self._detail_refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=17,
            tooltip="Tải lại lịch học",
            padding=0,
            size_constraints=icon_constraints,
            on_click=lambda _: self._reload_active_schedule_week(),
        )
        self._filter_bar_row = ft.Row(
            controls=[
                ft.Row(
                    controls=[
                        self._filter_day_chip,
                        self._filter_week_chip,
                        self._filter_tasks_chip,
                    ],
                    spacing=6,
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                ),
                ft.Semantics(
                    label="Tải lại lịch học",
                    button=True,
                    content=self._detail_refresh_btn,
                ),
            ],
            spacing=4,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._portal_status_row = ft.Row(
            controls=[],
            spacing=8,
            visible=False,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self._day_title = ft.Text("", size=14, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY)
        self._day_count = ft.Text("", size=11, color=C.TEXT_SECONDARY)
        self._day_list = ft.ListView(spacing=6, expand=True)
        # CV-06: Better empty state
        self._day_empty_icon = ft.Icon(
            ft.Icons.CALENDAR_TODAY_ROUNDED, size=28, color=C.BORDER
        )
        self._day_empty_title = ft.Text(
            "Không có bài tập đến hạn", size=12, color=C.TEXT_SECONDARY
        )
        self._day_empty_hint = ft.Text(
            "Chọn ngày có chấm màu để xem", size=10, color=C.BORDER
        )
        self._day_empty = ft.Container(
            content=ft.Column(
                controls=[
                    self._day_empty_icon,
                    self._day_empty_title,
                    self._day_empty_hint,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
            ),
            alignment=ft.Alignment(0, 0),
            expand=True,
            visible=False,
        )

        self._detail_divider = ft.Divider(height=1, color=C.BORDER)
        detail_panel = ft.Container(
            content=ft.Column(
                controls=[
                    self._detail_divider,
                    ft.Container(
                        content=self._filter_bar_row,
                        padding=ft.Padding.only(left=14, right=8, top=4, bottom=0),
                    ),
                    ft.Container(
                        content=self._portal_status_row,
                        padding=ft.Padding.only(left=16, right=14, top=0, bottom=2),
                    ),
                    ft.Container(
                        content=ft.Row(
                            controls=[self._day_title, self._day_count],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        padding=ft.Padding.only(left=16, right=16, top=2, bottom=4),
                    ),
                    ft.Container(
                        content=ft.Stack(controls=[self._day_list, self._day_empty], expand=True),
                        padding=ft.Padding.only(left=14, right=14, bottom=8),
                        expand=True,
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            expand=True,
        )
        self._detail_panel = detail_panel

        self._period_transition_active = False
        self._period_request_active = False
        self._pager_recentering = False
        self._pending_desktop_delta: int | None = None
        self._pending_desktop_control: ft.Dismissible | None = None
        self._native_desktop_period_drag = platform_utils.UI_IS_DESKTOP
        self._period_pager = ft.PageView(
            controls=[],
            height=316,
            selected_index=1,
            horizontal=True,
            snap=True,
            implicit_scrolling=True,
            keep_page=False,
            pad_ends=False,
            viewport_fraction=1.0,
            on_change=self._on_period_page_change,
        )
        # Dismissible keeps drag animation entirely in Flutter. Its child is a
        # three-page strip whose neighbours overflow on either side; this host
        # is the stationary viewport that clips that overflow.
        self._desktop_period_host = ft.Container(
            width=float("inf"),
            height=316,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )
        # The slot itself is positioned in the outer stage. It must be a
        # Stack so every replacement Dismissible can use absolute constraints;
        # a Container child has no bounded size here and leaves only the
        # lightweight release-frame underlay visible after navigation.
        self._desktop_period_slot = ft.Stack(
            controls=[],
            left=0,
            right=0,
            top=0,
            bottom=0,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )
        self._desktop_period_gesture = ft.GestureDetector(
            content=self._desktop_period_host,
            allowed_devices=[ft.PointerDeviceType.MOUSE],
            mouse_cursor=ft.MouseCursor.GRAB,
            exclude_from_semantics=True,
        )
        self._period_dismiss_generation = 0
        self._period_dismissible: ft.Dismissible | None = None
        self._desktop_period_stage: ft.Stack | None = None
        self._period_background_pages: list[_CalendarPeriodPage] = []
        self._period_surface = (
            self._desktop_period_gesture
            if self._native_desktop_period_drag
            else self._period_pager
        )

        # Assemble
        self.content = ft.Column(
            controls=[
                self._header,
                self._mode_toggle,
                self._period_surface,
                detail_panel,
                self._schedule_view,
            ],
            spacing=0,
            expand=True,
        )
        self._seed_period_pages()

    def _build_month_period_page(
        self, anchor: MonthAnchor
    ) -> _CalendarPeriodPage:
        weekday_labels = [
            ft.Text(
                weekday,
                size=10,
                weight=ft.FontWeight.W_600,
                color=C.CRITICAL if weekday == "CN" else C.TEXT_SECONDARY,
                text_align=ft.TextAlign.CENTER,
            )
            for weekday in _WEEKDAYS
        ]
        weekday_row = ft.Row(
            controls=[
                ft.Container(
                    content=label,
                    width=44,
                    alignment=ft.Alignment(0, 0),
                )
                for label in weekday_labels
            ],
            spacing=2,
            alignment=ft.MainAxisAlignment.CENTER,
        )
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(
            anchor.year, anchor.month
        )
        while len(weeks) < 6:
            weeks.append([0] * 7)
        day_cells: list[list[ft.Container]] = []
        rows = []
        for week in weeks:
            cells = []
            for day_number in week:
                cell = self._make_day_cell()
                cell_date = (
                    date(anchor.year, anchor.month, day_number)
                    if day_number
                    else None
                )
                cell.data["date"] = cell_date
                cell.data["day"] = day_number
                cell.data["day_text"].value = str(day_number) if day_number else ""
                cell.on_click = (
                    (lambda _, selected=cell_date: self._on_absolute_date_click(selected))
                    if cell_date
                    else None
                )
                cells.append(cell)
            day_cells.append(cells)
            rows.append(
                ft.Row(
                    controls=cells,
                    spacing=2,
                    alignment=ft.MainAxisAlignment.CENTER,
                )
            )
        control = ft.Column(
            controls=[weekday_row, *rows],
            spacing=2,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        period = _CalendarPeriodPage(
            anchor,
            control,
            weekday_labels=weekday_labels,
            day_cells=day_cells,
        )
        self._render_month_period_page(period)
        return period

    def _build_week_period_page(self, week_start: date) -> _CalendarPeriodPage:
        cells = [self._make_week_cell(index) for index in range(7)]
        for index, cell in enumerate(cells):
            cell_date = week_start + timedelta(days=index)
            cell.data["date"] = cell_date
            cell.data["day_text"].value = str(cell_date.day)
            cell.on_click = (
                lambda _, selected=cell_date: self._on_absolute_date_click(selected)
            )
        control = ft.Container(
            content=ft.Row(
                controls=cells,
                spacing=3,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        )
        period = _CalendarPeriodPage(week_start, control, week_cells=cells)
        self._render_week_period_page(period)
        return period

    def _build_month_period_preview(
        self, anchor: MonthAnchor
    ) -> _CalendarPeriodPage:
        """Build a non-interactive background with exact live-grid geometry."""
        period = self._build_month_period_page(anchor)
        for row in period.day_cells:
            for cell in row:
                cell.on_click = None
                cell.animate = None
        return period

    def _seed_period_pages(self, *, delta: int | None = None) -> None:
        self._schedule_view._hydrate()
        if self._view_mode == "month":
            anchors = month_slot_anchors(MonthAnchor(self._year, self._month))
            build_period = self._build_month_period_page
        else:
            anchors = week_slot_anchors(self._week_start)
            build_period = self._build_week_period_page

        old_pages = getattr(self, "_period_pages", [])
        if (
            delta is None
            and not getattr(self, "_force_period_rebuild", False)
            and len(old_pages) == 3
            and [page.anchor for page in old_pages] == list(anchors)
        ):
            self._period_pages = old_pages
            pager = getattr(self, "_period_pager", None)
            if pager is not None:
                pager.selected_index = 1
                desired_controls = (
                    []
                    if self._native_desktop_period_drag
                    else [period.control for period in self._period_pages]
                )
                if list(getattr(pager, "controls", [])) != desired_controls:
                    pager.controls = desired_controls
            if not self._native_desktop_period_drag:
                self._render_period_pages()
                return
            if (
                self._period_dismissible is not None
                and list(getattr(self._desktop_period_slot, "controls", []))
                == [self._period_dismissible]
                and self._desktop_period_stage is not None
                and len(getattr(self, "_period_background_pages", [])) == 2
                and [p.anchor for p in self._period_background_pages]
                == [anchors[0], anchors[2]]
            ):
                height = 316 if self._view_mode == "month" else 88
                if self._desktop_period_underlay is not None:
                    self._desktop_period_underlay.height = height
                self._desktop_period_stage.height = height
                self._render_period_pages()
                return

        if (
            delta == 1
            and len(old_pages) == 3
            and old_pages[1].anchor == anchors[0]
            and old_pages[2].anchor == anchors[1]
        ):
            self._period_pages = [
                old_pages[1],
                old_pages[2],
                build_period(anchors[2]),
            ]
        elif (
            delta == -1
            and len(old_pages) == 3
            and old_pages[0].anchor == anchors[1]
            and old_pages[1].anchor == anchors[2]
        ):
            self._period_pages = [
                build_period(anchors[0]),
                old_pages[0],
                old_pages[1],
            ]
        else:
            self._period_pages = [build_period(anchor) for anchor in anchors]

        # Reused pages keep their control identity (and therefore their
        # already-mounted Flutter subtrees), but selection/deadline styling
        # must follow the newly active period.
        if delta in (-1, 1):
            for period in self._period_pages:
                if isinstance(period.anchor, MonthAnchor):
                    self._render_month_period_page(period)
                else:
                    self._render_week_period_page(period)
        pager = getattr(self, "_period_pager", None)
        if pager is not None:
            pager.selected_index = 1
            pager.controls = (
                []
                if self._native_desktop_period_drag
                else [period.control for period in self._period_pages]
            )
        if self._native_desktop_period_drag:
            self._period_dismiss_generation += 1
            height = 316 if self._view_mode == "month" else 88

            def strip_period(control: ft.Control, offset_x: int) -> ft.Container:
                return ft.Container(
                    content=control,
                    bgcolor=C.BG,
                    left=0,
                    right=0,
                    top=0,
                    bottom=0,
                    offset=ft.Offset(offset_x, 0),
                )

            previous, current, following = self._period_pages
            if self._view_mode == "month":
                self._period_background_pages = [
                    self._build_month_period_preview(previous.anchor),
                    self._build_month_period_preview(following.anchor),
                ]
                current_underlay = self._grid
            else:
                self._period_background_pages = [
                    self._build_week_period_page(previous.anchor),
                    self._build_week_period_page(following.anchor),
                ]
                current_underlay = self._week_strip
            previous_background, following_background = (
                self._period_background_pages
            )

            def background_period(control: ft.Control) -> ft.Container:
                return ft.Container(
                    content=control,
                    bgcolor=C.BG,
                    width=float("inf"),
                    height=height,
                )

            period_strip = ft.Stack(
                controls=[
                    strip_period(previous.control, -1),
                    strip_period(current.control, 0),
                    strip_period(following.control, 1),
                ],
                width=float("inf"),
                height=height,
                clip_behavior=ft.ClipBehavior.NONE,
            )
            self._period_dismissible = ft.Dismissible(
                key=f"calendar-period-{self._period_dismiss_generation}",
                content=period_strip,
                background=background_period(previous_background.control),
                secondary_background=background_period(
                    following_background.control
                ),
                dismiss_direction=ft.DismissDirection.HORIZONTAL,
                dismiss_thresholds={
                    ft.DismissDirection.START_TO_END: 0.14,
                    ft.DismissDirection.END_TO_START: 0.14,
                },
                movement_duration=ft.Duration(milliseconds=30),
                # Flet treats a missing/None value as its 300 ms default,
                # which runs a visible vertical SizeTransition after this
                # horizontal swipe. Flutter skips onDismissed for a zero
                # duration here, so use the smallest positive duration: it
                # completes the lifecycle below one display frame while the
                # exact-geometry underlay remains painted.
                resize_duration=ft.Duration(milliseconds=1),
                on_confirm_dismiss=self._on_desktop_period_confirm,
                on_dismiss=self._on_desktop_period_dismiss,
                left=0,
                right=0,
                top=0,
                bottom=0,
            )
            self._desktop_period_slot.controls = [self._period_dismissible]
            if self._desktop_period_stage is None:
                self._desktop_period_underlay = background_period(
                    current_underlay
                )
                self._desktop_period_stage = ft.Stack(
                    controls=[
                        self._desktop_period_underlay,
                        self._desktop_period_slot,
                    ],
                    width=float("inf"),
                    height=height,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                )
                self._desktop_period_host.content = (
                    self._desktop_period_stage
                )
            else:
                # A confirmed swipe has already installed a lightweight copy
                # of the destination below Dismissible. Keep it mounted until
                # the new full strip paints; replacing it here would expose
                # the old month's compatibility grid for one bridge frame.
                if self._pending_desktop_delta is None:
                    self._desktop_period_underlay.content = current_underlay
                self._desktop_period_underlay.height = height
                self._desktop_period_stage.height = height

    def _update_period_surface(self) -> None:
        try:
            # A completed desktop swipe changes only these three mounted
            # subtrees. Updating CalendarView itself makes Flet diff and
            # serialize the whole detail screen, which keeps the replacement
            # Dismissible unavailable long enough to reject the next swipe.
            self._page_ref.update(
                self._header,
                self._desktop_period_slot,
                self._detail_panel,
            )
        except (AttributeError, RuntimeError, TypeError):
            # Unit tests and construction before mounting have no Flet page.
            pass

    def _render_period_pages(self) -> None:
        for period in self._period_pages + self._period_background_pages:
            if isinstance(period.anchor, MonthAnchor):
                self._render_month_period_page(period)
            else:
                self._render_week_period_page(period)

    def _reset_period_pager(self) -> None:
        self._period_transition_active = False
        self._period_request_active = False
        self._pager_recentering = False
        # A mode switch can replace a Dismissible before its delayed dismiss
        # event reaches Python. Do not let that old transaction reject the
        # first swipe in the newly mounted mode.
        self._pending_desktop_delta = None
        self._pending_desktop_control = None
        height = 316 if self._view_mode == "month" else 88
        self._period_pager.height = height
        self._desktop_period_host.height = height
        self._seed_period_pages()

    def _advance_week_selection(
        self, target_week_start: date, delta: int
    ) -> None:
        today = date.today()
        if self._selected_date is not None:
            next_selected = self._selected_date + timedelta(days=7 * delta)
            if not (
                target_week_start
                <= next_selected
                <= target_week_start + timedelta(days=6)
            ):
                next_selected = target_week_start + timedelta(
                    days=self._selected_date.weekday()
                )
        elif target_week_start <= today <= target_week_start + timedelta(days=6):
            next_selected = today
        else:
            next_selected = target_week_start
        self._week_start = target_week_start
        self._selected_date = next_selected
        self._year = next_selected.year
        self._month = next_selected.month
        self._selected_day = next_selected.day

    def _apply_period_delta(self, delta: int) -> bool:
        if delta not in (-1, 1):
            return False
        if self._view_mode == "month":
            current = MonthAnchor(self._year, self._month)
            target = shift_month(current, delta)
            if target == current:
                return False
            self._year, self._month = target.year, target.month
            self._selected_day = None
        else:
            target = shift_week(self._week_start, delta)
            if target == self._week_start:
                return False
            self._advance_week_selection(target, delta)
        return True

    async def _on_period_page_change(self, event) -> None:
        try:
            selected_index = int(event.data)
        except (TypeError, ValueError):
            return
        if self._pager_recentering and selected_index == 1:
            return
        if self._period_transition_active or selected_index not in (0, 2):
            return
        self._period_transition_active = True
        delta = -1 if selected_index == 0 else 1
        try:
            if not self._apply_period_delta(delta):
                return
            self._seed_period_pages()
            if self._view_mode == "month":
                self._render_month()
            else:
                self._render_week()
            self._pager_recentering = True
            await self._period_pager.jump_to_page(1)
        finally:
            self._pager_recentering = False
            self._period_transition_active = False

    async def _on_desktop_period_dismiss(self, event) -> None:
        source = getattr(event, "control", None) or self._period_dismissible
        if source is not self._period_dismissible:
            # Late event from a Dismissible replaced by another swipe or mode
            # switch. Its logical delta was already handled (or cancelled).
            return
        direction = getattr(event, "direction", None)
        if direction == ft.DismissDirection.START_TO_END:
            delta = -1
        elif direction == ft.DismissDirection.END_TO_START:
            delta = 1
        else:
            return
        self._finish_desktop_period_dismiss(source, delta)

    def _finish_desktop_period_dismiss(
        self, source: ft.Dismissible, delta: int
    ) -> bool:
        """Idempotently replace one dismissed strip and release its state."""
        if source is not self._period_dismissible or delta not in (-1, 1):
            return False

        pending_matches = (
            self._pending_desktop_control is source
            and self._pending_desktop_delta == delta
        )
        if self._pending_desktop_control is source and not pending_matches:
            # The source is definitely being removed, so it still needs a
            # replacement. Keep the already-rendered logical period rather
            # than applying a contradictory second delta.
            reuse_delta = None
        elif pending_matches:
            reuse_delta = delta
        else:
            # Compatibility path for direct on_dismiss delivery without a
            # preceding confirmation event.
            self._period_transition_active = True
            if not self._apply_period_delta(delta):
                reuse_delta = None
            else:
                reuse_delta = delta
        try:
            # Flutter removes a dismissed control, so replace it with a new
            # keyed instance. Reuse the two still-relevant period pages and
            # build only the newly exposed far edge.
            self._seed_period_pages(delta=reuse_delta)
            if self._view_mode == "month":
                self._render_month(update_page=False)
            else:
                self._render_week(update_page=False)
            self._update_period_surface()
        finally:
            if self._pending_desktop_control is source:
                self._pending_desktop_delta = None
                self._pending_desktop_control = None
            self._period_transition_active = False
        return True

    async def _recover_desktop_period_dismiss(
        self, source: ft.Dismissible, delta: int
    ) -> None:
        """Re-arm a strip if Flutter omits/delays its dismiss event."""
        # movement_duration (30 ms) + resize_duration (1 ms) should normally
        # emit on_dismiss first. Two extra display frames cover bridge jitter
        # without introducing the old half-second dead interval.
        await asyncio.sleep(0.065)
        if (
            source is self._period_dismissible
            and self._pending_desktop_control is source
            and self._pending_desktop_delta == delta
        ):
            self._finish_desktop_period_dismiss(source, delta)

    async def _resolve_desktop_period_dismiss(self, event, dismiss: bool) -> None:
        """Resolve Flet's pending Dismissible decision on its source control."""
        dismissible = getattr(event, "control", None) or self._period_dismissible
        if dismissible is not None:
            await dismissible.confirm_dismiss(dismiss)

    async def _on_desktop_period_confirm(self, event) -> None:
        """Install the destination underlay, then release Flet's drag lock."""
        source = getattr(event, "control", None) or self._period_dismissible
        if source is not self._period_dismissible:
            await self._resolve_desktop_period_dismiss(event, False)
            return
        if (
            self._pending_desktop_control is not None
            and self._pending_desktop_control is not source
        ):
            # Heal an orphaned transaction defensively. A pending decision is
            # meaningful only for the control that created it; it must never
            # veto a swipe on the current replacement.
            self._pending_desktop_delta = None
            self._pending_desktop_control = None
            self._period_transition_active = False
        direction = getattr(event, "direction", None)
        if direction == ft.DismissDirection.START_TO_END:
            delta = -1
        elif direction == ft.DismissDirection.END_TO_START:
            delta = 1
        else:
            await self._resolve_desktop_period_dismiss(event, False)
            return
        if self._period_transition_active or self._pending_desktop_delta is not None:
            await self._resolve_desktop_period_dismiss(event, False)
            return
        if not self._apply_period_delta(delta):
            await self._resolve_desktop_period_dismiss(event, False)
            return

        self._period_transition_active = True
        self._pending_desktop_delta = delta
        self._pending_desktop_control = source
        if self._view_mode == "month":
            self._render_month(update_page=False)
            target_underlay = self._build_month_period_preview(
                MonthAnchor(self._year, self._month)
            )
        else:
            self._render_week(update_page=False)
            target_underlay = self._build_week_period_page(self._week_start)
        self._desktop_period_underlay.content = target_underlay.control
        try:
            self._page_ref.update(
                self._header,
                self._desktop_period_underlay,
                self._detail_panel,
            )
        except (AttributeError, RuntimeError, TypeError):
            pass
        # Flet 0.86+ does not use the event handler's return value here. The
        # pending drag remains locked until confirm_dismiss() is invoked on
        # the exact Dismissible that raised the event.
        await self._resolve_desktop_period_dismiss(event, True)
        try:
            self._page_ref.run_task(
                self._recover_desktop_period_dismiss, source, delta
            )
        except (AttributeError, RuntimeError, TypeError):
            pass

    async def _on_previous_period(self, _event) -> None:
        await self._request_period_navigation(-1)

    async def _on_next_period(self, _event) -> None:
        await self._request_period_navigation(1)

    async def _request_period_navigation(self, delta: int) -> None:
        if (
            self._period_transition_active
            or self._period_request_active
            or delta not in (-1, 1)
        ):
            return
        try:
            self._period_pager.page
            pager_is_mounted = True
        except RuntimeError:
            pager_is_mounted = False
        if not pager_is_mounted:
            if not self._apply_period_delta(delta):
                return
            self._seed_period_pages()
            if self._view_mode == "month":
                self._render_month()
            else:
                self._render_week()
            return
        self._period_request_active = True
        try:
            await self._period_pager.go_to_page(
                0 if delta < 0 else 2,
                animation_duration=ft.Duration(milliseconds=220),
                animation_curve=ft.AnimationCurve.EASE_OUT_CUBIC,
            )
        finally:
            self._period_request_active = False

    def _render_month_period_page(self, period: _CalendarPeriodPage) -> None:
        anchor = period.anchor
        if not isinstance(anchor, MonthAnchor):
            return
        for label in period.weekday_labels:
            label.color = C.CRITICAL if label.value == "CN" else C.TEXT_SECONDARY
        today = date.today()
        for column, cell in (
            (column, cell)
            for row in period.day_cells
            for column, cell in enumerate(row)
        ):
            cell_date = cell.data["date"]
            if cell_date is None:
                today_dot = cell.data["today_dot"]
                today_dot.visible = False
                today_dot.bgcolor = C.ACCENT
                class_bar = cell.data.get("class_bar")
                if class_bar is not None:
                    class_bar.visible = False
                cell.data["dots_row"].controls.clear()
                continue
            self._style_period_cell(
                cell,
                cell_date,
                column,
                today,
                selected=(
                    anchor.year == self._year
                    and anchor.month == self._month
                    and cell_date.day == self._selected_day
                ),
            )

    def _render_shift_bars(
        self, shift_bars: ft.Column, cell_date: Optional[date]
    ) -> int:
        sessions = (
            self._schedule_view.sessions_for_day(cell_date)
            if cell_date is not None
            else ()
        )
        buckets: dict[str, list[ClassSession]] = {
            "morning": [],
            "afternoon": [],
            "evening": [],
        }
        for sess in sessions:
            buckets[classify_session_shift(sess)].append(sess)
        for shift_key, bar in zip(
            ("morning", "afternoon", "evening"), shift_bars.controls
        ):
            group = buckets[shift_key]
            if not group:
                bar.bgcolor = C.BORDER
                bar.opacity = 0.25
            elif any(not s.cancelled for s in group):
                bar.bgcolor = C.ACCENT
                bar.opacity = 1.0
            else:
                bar.bgcolor = C.CRITICAL
                bar.opacity = 0.45
        return len(sessions)

    def _render_week_period_page(self, period: _CalendarPeriodPage) -> None:
        today = date.today()
        urgency_priority = {"overdue": 0, "critical": 1, "warning": 2, "safe": 3}
        for index, cell in enumerate(period.week_cells):
            cell_date = cell.data["date"]
            acts = self._deadline_map.get(cell_date.isoformat(), [])
            selected = cell_date == self._selected_date
            day_text = cell.data["day_text"]
            weekday_label = cell.data["wd_label"]
            count_text = cell.data["count_text"]
            shift_bars = cell.data["shift_bars"]
            is_today = cell_date == today
            most_urgent_color = None
            if acts:
                most_urgent = min(
                    acts,
                    key=lambda activity: urgency_priority.get(
                        activity.get("urgency", "safe"), 3
                    ),
                )
                most_urgent_color = get_urgency_color(
                    most_urgent.get("urgency", "safe")
                )

            if selected and is_today:
                cell.bgcolor = ft.Colors.with_opacity(0.13, C.ACCENT)
                cell.border = ft.Border.all(2, C.ACCENT)
                day_text.color = weekday_label.color = C.ACCENT
                day_text.weight = ft.FontWeight.W_700
            elif selected:
                cell.bgcolor = C.SURFACE_HOVER
                cell.border = ft.Border.all(1.5, C.BORDER_STRONG)
                day_text.color = weekday_label.color = C.TEXT_PRIMARY
                day_text.weight = ft.FontWeight.W_700
            elif is_today:
                cell.bgcolor = ft.Colors.with_opacity(0.06, C.ACCENT)
                cell.border = ft.Border.all(
                    1, ft.Colors.with_opacity(0.31, C.ACCENT)
                )
                day_text.color = weekday_label.color = C.ACCENT
                day_text.weight = ft.FontWeight.W_700
            elif acts:
                cell.bgcolor = ft.Colors.with_opacity(
                    0.05, most_urgent_color or C.ACCENT
                )
                cell.border = ft.Border.all(
                    0.5,
                    ft.Colors.with_opacity(0.19, most_urgent_color or C.BORDER),
                )
                day_text.color = C.CRITICAL if index == 6 else C.TEXT_PRIMARY
                weekday_label.color = (
                    C.CRITICAL if index == 6 else C.TEXT_SECONDARY
                )
                day_text.weight = ft.FontWeight.W_500
            else:
                cell.bgcolor = None
                cell.border = None
                day_text.color = C.CRITICAL if index == 6 else C.TEXT_PRIMARY
                weekday_label.color = (
                    C.CRITICAL if index == 6 else C.TEXT_SECONDARY
                )
                day_text.weight = ft.FontWeight.W_400

            self._render_shift_bars(shift_bars, cell_date)
            self._render_activity_markers(
                cell.data["dots_row"], acts, most_urgent_color
            )
            count_text.value = f"{len(acts)} bài" if acts else ""
            count_text.color = most_urgent_color or C.TEXT_SECONDARY

    def _style_period_cell(
        self,
        cell: ft.Container,
        cell_date: date,
        column: int,
        today: date,
        *,
        selected: bool,
    ) -> None:
        day_text = cell.data["day_text"]
        dots_row = cell.data["dots_row"]
        acts = self._deadline_map.get(cell_date.isoformat(), [])
        has_classes = bool(self._schedule_view.sessions_for_day(cell_date))
        is_today = cell_date == today
        today_dot = cell.data["today_dot"]
        today_dot.visible = is_today
        today_dot.bgcolor = C.ACCENT
        if selected:
            cell.bgcolor = C.SURFACE_HOVER
            cell.border = ft.Border.all(1.5, C.BORDER_STRONG)
            day_text.color = C.ACCENT if is_today else C.TEXT_PRIMARY
            day_text.weight = ft.FontWeight.W_700 if is_today else ft.FontWeight.W_600
        elif is_today:
            cell.bgcolor = None
            cell.border = None
            day_text.color = C.ACCENT
            day_text.weight = ft.FontWeight.W_700
        elif acts:
            urgency_priority = {"overdue": 0, "critical": 1, "warning": 2, "safe": 3}
            most_urgent = min(
                acts,
                key=lambda activity: urgency_priority.get(
                    activity.get("urgency", "safe"), 3
                ),
            )
            urgency_color = get_urgency_color(most_urgent.get("urgency", "safe"))
            cell.bgcolor = ft.Colors.with_opacity(0.06, urgency_color)
            cell.border = ft.Border.all(
                0.5, ft.Colors.with_opacity(0.19, urgency_color)
            )
            day_text.color = C.CRITICAL if column == 6 else C.TEXT_PRIMARY
            day_text.weight = ft.FontWeight.W_500
        else:
            cell.bgcolor = None
            cell.border = None
            day_text.color = C.CRITICAL if column == 6 else C.TEXT_PRIMARY
            day_text.weight = ft.FontWeight.W_400
        most_urgent_color = None
        if acts:
            urgency_priority = {"overdue": 0, "critical": 1, "warning": 2, "safe": 3}
            most_urgent = min(
                acts,
                key=lambda activity: urgency_priority.get(
                    activity.get("urgency", "safe"), 3
                ),
            )
            most_urgent_color = get_urgency_color(
                most_urgent.get("urgency", "safe")
            )
        self._render_activity_markers(
            dots_row,
            acts,
            most_urgent_color,
            class_bar=cell.data.get("class_bar"),
            has_classes=has_classes,
        )

    def _render_activity_markers(
        self,
        dots_row: ft.Row,
        activities: list[dict[str, Any]],
        most_urgent_color: Optional[str],
        *,
        class_bar: Optional[ft.Container] = None,
        has_classes: bool = False,
        badge_size: int = 16,
        badge_font_size: int = 8,
    ) -> None:
        dots_row.controls.clear()
        if len(activities) >= 3:
            color = most_urgent_color or C.ACCENT
            dots_row.controls.append(
                ft.Container(
                    content=ft.Text(
                        str(len(activities)),
                        size=badge_font_size,
                        color=on_color(color),
                        weight=ft.FontWeight.W_700,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    width=badge_size,
                    height=badge_size,
                    border_radius=badge_size // 2,
                    bgcolor=color,
                    alignment=ft.Alignment(0, 0),
                )
            )
        else:
            seen = set()
            for activity in activities:
                color = get_urgency_color(activity.get("urgency", "safe"))
                if color in seen:
                    continue
                seen.add(color)
                if len(dots_row.controls) < 3:
                    dots_row.controls.append(
                        ft.Container(width=6, height=6, border_radius=3, bgcolor=color)
                    )
        if has_classes:
            bar = class_bar or ft.Container(
                width=10, height=2.5, border_radius=1.5, bgcolor=C.ACCENT
            )
            bar.bgcolor = C.ACCENT
            bar.visible = True
            dots_row.controls.append(bar)
        elif class_bar is not None:
            class_bar.visible = False

    def _on_absolute_date_click(self, selected: date) -> None:
        self._year, self._month = selected.year, selected.month
        self._selected_day = selected.day
        self._selected_date = selected
        if self._view_mode == "week":
            if self._detail_filter_mode in ("week_classes", "tasks"):
                self._detail_filter_mode = "day"
            self._week_start = selected - timedelta(days=selected.weekday())
            self._render_period_pages()
            self._render_week()
        else:
            if self._detail_filter_mode == "week_classes":
                self._week_start = selected - timedelta(days=selected.weekday())
            self._render_period_pages()
            self._render_month()

    def _make_day_cell(self) -> ft.Container:
        """Create a single day cell with today indicator dot."""
        day_num = ft.Text("", size=12, color=C.TEXT_PRIMARY, text_align=ft.TextAlign.CENTER)
        # Today indicator - small accent dot under the number (always visible for today)
        today_dot = ft.Container(
            width=5, height=5, border_radius=3, bgcolor=C.ACCENT, visible=False,
        )
        class_bar = ft.Container(
            width=10, height=2.5, border_radius=1.5, bgcolor=C.ACCENT, visible=False,
        )
        dots_row = ft.Row(
            controls=[],
            spacing=2,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        cell = ft.Container(
            content=ft.Column(
                controls=[
                    day_num,
                    today_dot,
                    dots_row,
                ],
                spacing=0,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            width=44, height=48,
            border_radius=8,
            alignment=ft.Alignment(0, -0.1),
            on_click=lambda e, c=None: self._on_day_click(e),
        )
        cell.data = {
            "day_text": day_num,
            "dots_row": dots_row,
            "today_dot": today_dot,
            "class_bar": class_bar,
            "day": 0,
        }
        return cell

    def _make_week_cell(self, col_index: int) -> ft.Container:
        """Create a single week view cell - taller and wider than month cells."""
        weekday_labels = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
        wd_color = C.CRITICAL if col_index == 6 else C.TEXT_SECONDARY

        wd_label = ft.Text(
            weekday_labels[col_index], size=10, weight=ft.FontWeight.W_600,
            color=wd_color, text_align=ft.TextAlign.CENTER,
        )
        day_num = ft.Text(
            "", size=16, weight=ft.FontWeight.W_600,
            color=C.TEXT_PRIMARY, text_align=ft.TextAlign.CENTER,
        )
        shift_bars = ft.Column(
            controls=[
                ft.Container(
                    width=16,
                    height=1.8,
                    border_radius=1,
                    bgcolor=C.BORDER,
                    opacity=0.25,
                )
                for _ in range(3)
            ],
            spacing=1,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        dots_row = ft.Row(controls=[], spacing=2, alignment=ft.MainAxisAlignment.CENTER)
        count_text = ft.Text("", size=9, color=C.TEXT_SECONDARY, text_align=ft.TextAlign.CENTER)

        cell = ft.Container(
            content=ft.Column(
                controls=[wd_label, day_num, shift_bars, dots_row, count_text],
                spacing=1,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            expand=True,
            height=80,
            border_radius=12,
            alignment=ft.Alignment(0, 0),
            on_click=lambda e, idx=col_index: self._on_week_cell_click(idx),
        )
        cell.data = {
            "wd_label": wd_label,
            "day_text": day_num,
            "shift_bars": shift_bars,
            "dots_row": dots_row,
            "count_text": count_text,
            "col": col_index,
            "date": None,
        }
        return cell

    def _on_week_cell_click(self, col_index: int):
        """Handle click on a week cell."""
        cell = self._week_cells[col_index]
        cell_date = cell.data.get("date")
        if cell_date:
            if self._detail_filter_mode in ("week_classes", "tasks"):
                self._detail_filter_mode = "day"
            self._selected_date = cell_date
            self._selected_day = cell_date.day
            self._year = cell_date.year
            self._month = cell_date.month
            self._render_period_pages()
            self._render_week()

    def _render_week(
        self, *, update_page: bool = True, ensure_schedule: bool = True
    ):
        """Render the 7-day week strip for self._week_start."""
        was_rendering = self._rendering_calendar
        self._rendering_calendar = True
        try:
            if ensure_schedule:
                self._schedule_view.hydrate_and_ensure_weeks(
                    [self._week_start], debounce_ms=0
                )
            else:
                self._schedule_view._hydrate()
            today = date.today()
            week_num = self._week_start.isocalendar()[1]
            month_name = _MONTHS_VI[self._week_start.month]

            # Determine if week spans 2 months
            week_end = self._week_start + timedelta(days=6)
            if self._week_start.month != week_end.month:
                m2 = _MONTHS_VI[week_end.month]
                self._month_label.value = f"Tuần {week_num} · {month_name} – {m2}"
            else:
                self._month_label.value = f"Tuần {week_num} · {month_name} {self._week_start.year}"

            # Summary for week (combining classes and assignments when classes exist)
            week_total = 0
            week_submitted = 0
            for i in range(7):
                d = self._week_start + timedelta(days=i)
                key = d.strftime("%Y-%m-%d")
                acts = self._deadline_map.get(key, [])
                week_total += len(acts)
                for a in acts:
                    sub = a.get("submission_status", "")
                    if sub in ("submitted", "Đã nộp", "graded", "Đã chấm"):
                        week_submitted += 1

            week_sessions_map = self._schedule_view.sessions_for_week(
                self._week_start
            )
            week_classes = sum(len(v) for v in week_sessions_map.values())
            done_text = f" · {week_submitted} đã nộp" if week_submitted > 0 else ""
            if week_classes > 0 and week_total > 0:
                self._month_summary.value = (
                    f"{week_classes} ca học · {week_total} bài tập{done_text}"
                )
            elif week_classes > 0:
                self._month_summary.value = f"{week_classes} ca học trong tuần này"
            elif week_total > 0:
                self._month_summary.value = f"{week_total} bài tập{done_text}"
            else:
                self._month_summary.value = "Không có bài tập trong tuần này"

            # Render 7 cells
            for i in range(7):
                d = self._week_start + timedelta(days=i)
                cell = self._week_cells[i]
                day_text: ft.Text = cell.data["day_text"]
                dots_row: ft.Row = cell.data["dots_row"]
                count_text: ft.Text = cell.data["count_text"]
                shift_bars: ft.Column = cell.data["shift_bars"]
                cell.data["date"] = d

                day_text.value = str(d.day)
                count_text.color = C.TEXT_SECONDARY

                is_today = (d == today)
                is_selected = (d == self._selected_date)

                key = d.strftime("%Y-%m-%d")
                acts = self._deadline_map.get(key, [])
                has_deadline = len(acts) > 0

                most_urgent_color = None
                if has_deadline:
                    urgency_priority = {"overdue": 0, "critical": 1, "warning": 2, "safe": 3}
                    sorted_acts = sorted(acts, key=lambda a: urgency_priority.get(a.get("urgency", "safe"), 3))
                    most_urgent_color = get_urgency_color(sorted_acts[0].get("urgency", "safe"))

                wd_label: ft.Text = cell.data["wd_label"]
                if is_selected and is_today:
                    cell.bgcolor = ft.Colors.with_opacity(0.13, C.ACCENT)
                    cell.border = ft.Border.all(2, C.ACCENT)
                    day_text.color = C.ACCENT
                    day_text.weight = ft.FontWeight.W_700
                    wd_label.color = C.ACCENT
                elif is_selected:
                    cell.bgcolor = C.SURFACE_HOVER
                    cell.border = ft.Border.all(1.5, C.BORDER_STRONG)
                    day_text.color = C.TEXT_PRIMARY
                    day_text.weight = ft.FontWeight.W_700
                    wd_label.color = C.TEXT_PRIMARY
                elif is_today:
                    cell.bgcolor = ft.Colors.with_opacity(0.06, C.ACCENT)
                    cell.border = ft.Border.all(
                        1, ft.Colors.with_opacity(0.31, C.ACCENT)
                    )
                    day_text.color = C.ACCENT
                    day_text.weight = ft.FontWeight.W_700
                    wd_label.color = C.ACCENT
                elif has_deadline:
                    cell.bgcolor = ft.Colors.with_opacity(
                        0.05, most_urgent_color or C.ACCENT
                    )
                    cell.border = ft.Border.all(
                        0.5,
                        ft.Colors.with_opacity(0.19, most_urgent_color or C.BORDER),
                    )
                    day_text.color = C.CRITICAL if i == 6 else C.TEXT_PRIMARY
                    day_text.weight = ft.FontWeight.W_500
                    wd_label.color = C.CRITICAL if i == 6 else C.TEXT_SECONDARY
                else:
                    cell.bgcolor = None
                    cell.border = None
                    day_text.color = C.CRITICAL if i == 6 else C.TEXT_PRIMARY
                    day_text.weight = ft.FontWeight.W_400
                    wd_label.color = C.CRITICAL if i == 6 else C.TEXT_SECONDARY

                self._render_shift_bars(shift_bars, d)
                self._render_activity_markers(dots_row, acts, most_urgent_color)

                if len(acts) > 0:
                    count_text.value = f"{len(acts)} bài"
                    count_text.color = most_urgent_color or C.TEXT_SECONDARY
                else:
                    count_text.value = ""

            if hasattr(self, "_period_pages"):
                self._render_period_pages()
            self._update_day_panel()
        finally:
            self._rendering_calendar = was_rendering

        if update_page and not getattr(self, "_defer_page_update", False):
            try:
                self._page_ref.update()
            except Exception:
                import logging as _fb_log
                _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

    # Public API
    def update_data(self, activities: List[Dict[str, Any]]):
        """Update calendar with new activity data and refresh grid."""
        self._all_data = activities
        self._build_deadline_map()
        self._force_period_rebuild = True
        try:
            self._reset_period_pager()
        finally:
            self._force_period_rebuild = False
        if self._view_mode == "month":
            self._render_month()
        elif self._view_mode == "week":
            self._render_week()
        else:
            self._apply_schedule_header()

    def _resolve_page_width(
        self, explicit_width: Optional[float] = None
    ) -> float:
        for candidate in (
            explicit_width,
            getattr(self._page_ref, "width", None),
            getattr(getattr(self._page_ref, "window", None), "width", None),
            self._last_measured_width if self._last_measured_width > 0 else None,
        ):
            if candidate is None or isinstance(candidate, bool):
                continue
            try:
                numeric = float(candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(numeric) and numeric > 0:
                return numeric
        return 0.0

    def _should_hide_separate_schedule_tab(
        self, width: Optional[float] = None
    ) -> bool:
        if platform_utils.UI_IS_MOBILE:
            return True
        page_width = self._resolve_page_width(width)
        return 0 < page_width < _COMPACT_SHELL_BREAKPOINT

    def handle_viewport_resize(self, width: Optional[float] = None) -> None:
        hide_schedule = self._should_hide_separate_schedule_tab(width)
        visibility_changed = self._mode_schedule_btn.visible != (
            not hide_schedule
        )
        self._mode_schedule_btn.visible = not hide_schedule
        if hide_schedule and self._view_mode == "schedule":
            preserved = self._schedule_view._selected
            self._leave_schedule_mode()
            self._view_mode = "week"
            self._selected_date = preserved
            self._year = preserved.year
            self._month = preserved.month
            self._selected_day = preserved.day
            self._week_start = preserved - timedelta(days=preserved.weekday())
            self._grid.visible = False
            self._week_strip.visible = True
            self._style_mode_buttons()
            self._reset_period_pager()
            self._render_week()
            return
        if (
            visibility_changed
            and self.visible
            and not getattr(self, "_defer_page_update", False)
        ):
            try:
                self._page_ref.update()
            except Exception:
                pass

    def _on_size_change(self, event=None) -> None:
        try:
            measured = float(getattr(event, "width", 0) or 0)
        except (TypeError, ValueError):
            measured = 0.0
        if measured > 0:
            self._last_measured_width = measured
        self.handle_viewport_resize()

    def _on_schedule_weeks_changed(self) -> None:
        if self._rendering_calendar:
            return
        if self._view_mode == "schedule":
            return
        if self._view_mode == "week":
            self._render_week(update_page=self.visible, ensure_schedule=False)
        elif self._view_mode == "month":
            self._render_month(update_page=self.visible, ensure_schedule=False)

    def show(self, activities: Optional[List[Dict[str, Any]]] = None):
        if activities is not None:
            self._all_data = activities
            self._build_deadline_map()
        self.visible = True
        hide_schedule = self._should_hide_separate_schedule_tab()
        self._mode_schedule_btn.visible = not hide_schedule
        # Always anchor to today when opening - reduces cognitive orientation time
        today = date.today()
        self._year = today.year
        self._month = today.month
        self._selected_day = today.day
        self._selected_date = today
        self._week_start = today - timedelta(days=today.weekday())
        if hide_schedule and self._view_mode == "schedule":
            self._leave_schedule_mode()
            self._view_mode = "week"
            self._grid.visible = False
            self._week_strip.visible = True
            self._style_mode_buttons()
        self._reset_period_pager()
        if self._view_mode == "month":
            self._render_month()
        elif self._view_mode == "week":
            self._render_week()
        else:
            self._apply_schedule_header()
            self._schedule_view.show()

    def hide(self):
        self.visible = False

    # Internal Logic
    def _build_deadline_map(self):
        """Group activities by deadline date string."""
        self._deadline_map.clear()
        for act in self._all_data:
            dl_str = act.get("deadline", "")
            dt = parse_datetime(dl_str)
            if not dt or dt.year >= 2099:
                continue
            key = dt.strftime("%Y-%m-%d")
            self._deadline_map.setdefault(key, []).append(act)

    def _render_month(
        self, *, update_page: bool = True, ensure_schedule: bool = True
    ):
        """Render the grid for self._year / self._month."""
        was_rendering = self._rendering_calendar
        self._rendering_calendar = True
        try:
            if ensure_schedule:
                self._schedule_view.hydrate_and_ensure_weeks(
                    _mondays_overlapping_month(self._year, self._month),
                    debounce_ms=250,
                )
            else:
                self._schedule_view._hydrate()
            self._month_label.value = f"{_MONTHS_VI[self._month]} {self._year}"

            today = date.today()
            cal = calendar.Calendar(firstweekday=0)
            month_days = cal.monthdayscalendar(self._year, self._month)
            while len(month_days) < 6:
                month_days.append([0] * 7)

            # CV-05: Month summary
            month_total = 0
            month_submitted = 0
            for key, acts in self._deadline_map.items():
                try:
                    d = datetime.strptime(key, "%Y-%m-%d")
                    if d.year == self._year and d.month == self._month:
                        month_total += len(acts)
                        for a in acts:
                            sub = a.get("submission_status", "")
                            if sub in ("submitted", "Đã nộp", "graded", "Đã chấm"):
                                month_submitted += 1
                except ValueError:
                    pass

            days_in_month = calendar.monthrange(self._year, self._month)[1]
            month_classes = sum(
                len(
                    self._schedule_view.sessions_for_day(
                        date(self._year, self._month, day_num)
                    )
                )
                for day_num in range(1, days_in_month + 1)
            )
            done_text = f" · {month_submitted} đã nộp" if month_submitted > 0 else ""
            if month_classes > 0 and month_total > 0:
                self._month_summary.value = (
                    f"{month_classes} ca học · {month_total} bài tập{done_text}"
                )
            elif month_classes > 0:
                self._month_summary.value = f"{month_classes} ca học trong tháng này"
            elif month_total > 0:
                self._month_summary.value = f"{month_total} bài tập{done_text}"
            else:
                self._month_summary.value = "Không có bài tập trong tháng này"

            if self._native_desktop_period_drag:
                for r, week in enumerate(month_days):
                    for c, day in enumerate(week):
                        cell = self._day_cells[r][c]
                        day_text: ft.Text = cell.data["day_text"]
                        dots_row: ft.Row = cell.data["dots_row"]
                        cell.data["day"] = day

                        if day == 0:
                            day_text.value = ""
                            dots_row.controls.clear()
                            cell.data["today_dot"].visible = False
                            if cell.data.get("class_bar") is not None:
                                cell.data["class_bar"].visible = False
                            cell.bgcolor = None
                            cell.border = None
                            cell.on_click = None
                            continue

                        day_text.value = str(day)
                        cell.on_click = lambda e, d=day: self._on_day_click_by_day(d)

                        cell_date = date(self._year, self._month, day)
                        is_today = (cell_date == today)
                        is_selected = (day == self._selected_day)

                        key = f"{self._year}-{self._month:02d}-{day:02d}"
                        acts = self._deadline_map.get(key, [])
                        has_deadline = len(acts) > 0
                        has_classes = bool(
                            self._schedule_view.sessions_for_day(cell_date)
                        )

                        most_urgent_color = None
                        if has_deadline:
                            urgency_priority = {"overdue": 0, "critical": 1, "warning": 2, "safe": 3}
                            sorted_acts = sorted(acts, key=lambda a: urgency_priority.get(a.get("urgency", "safe"), 3))
                            most_urgent_color = get_urgency_color(sorted_acts[0].get("urgency", "safe"))

                        today_dot: ft.Container = cell.data["today_dot"]
                        today_dot.visible = is_today
                        today_dot.bgcolor = C.ACCENT

                        if is_selected and is_today:
                            cell.bgcolor = C.SURFACE_HOVER
                            cell.border = ft.Border.all(1.5, C.BORDER_STRONG)
                            day_text.color = C.ACCENT
                            day_text.weight = ft.FontWeight.W_700
                        elif is_selected:
                            cell.bgcolor = C.SURFACE_HOVER
                            cell.border = ft.Border.all(1.5, C.BORDER_STRONG)
                            day_text.color = C.TEXT_PRIMARY
                            day_text.weight = ft.FontWeight.W_600
                        elif is_today:
                            cell.bgcolor = None
                            cell.border = None
                            day_text.color = C.ACCENT
                            day_text.weight = ft.FontWeight.W_700
                        elif has_deadline:
                            cell.bgcolor = ft.Colors.with_opacity(
                                0.06, most_urgent_color or C.ACCENT
                            )
                            cell.border = ft.Border.all(
                                0.5,
                                ft.Colors.with_opacity(
                                    0.19, most_urgent_color or C.BORDER
                                ),
                            )
                            day_text.color = C.CRITICAL if c == 6 else C.TEXT_PRIMARY
                            day_text.weight = ft.FontWeight.W_500
                        else:
                            cell.bgcolor = None
                            cell.border = None
                            day_text.color = C.CRITICAL if c == 6 else C.TEXT_PRIMARY
                            day_text.weight = ft.FontWeight.W_400

                        self._render_activity_markers(
                            dots_row,
                            acts,
                            most_urgent_color,
                            class_bar=cell.data.get("class_bar"),
                            has_classes=has_classes,
                            badge_size=14,
                            badge_font_size=7,
                        )

            if hasattr(self, "_period_pages"):
                self._render_period_pages()
            self._update_day_panel()
        finally:
            self._rendering_calendar = was_rendering

        if update_page and not getattr(self, "_defer_page_update", False):
            try:
                self._page_ref.update()
            except Exception:
                import logging as _fb_log
                _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _restyle_cell(self, cell, day: int, col: int, today):
        """Restyle a single cell - extracted for targeted updates."""
        day_text: ft.Text = cell.data["day_text"]
        dots_row: ft.Row = cell.data["dots_row"]

        cell_date = date(self._year, self._month, day)
        is_today = (cell_date == today)
        is_selected = (day == self._selected_day)

        key = f"{self._year}-{self._month:02d}-{day:02d}"
        acts = self._deadline_map.get(key, [])
        has_deadline = len(acts) > 0
        has_classes = bool(self._schedule_view.sessions_for_day(cell_date))

        most_urgent_color = None
        if has_deadline:
            urgency_priority = {"overdue": 0, "critical": 1, "warning": 2, "safe": 3}
            sorted_acts = sorted(acts, key=lambda a: urgency_priority.get(a.get("urgency", "safe"), 3))
            most_urgent_color = get_urgency_color(sorted_acts[0].get("urgency", "safe"))

        today_dot: ft.Container = cell.data["today_dot"]
        today_dot.visible = is_today
        today_dot.bgcolor = C.ACCENT

        if is_selected and is_today:
            cell.bgcolor = C.SURFACE_HOVER
            cell.border = ft.Border.all(1.5, C.BORDER_STRONG)
            day_text.color = C.ACCENT
            day_text.weight = ft.FontWeight.W_700
        elif is_selected:
            cell.bgcolor = C.SURFACE_HOVER
            cell.border = ft.Border.all(1.5, C.BORDER_STRONG)
            day_text.color = C.TEXT_PRIMARY
            day_text.weight = ft.FontWeight.W_600
        elif is_today:
            cell.bgcolor = None
            cell.border = None
            day_text.color = C.ACCENT
            day_text.weight = ft.FontWeight.W_700
        elif has_deadline:
            cell.bgcolor = ft.Colors.with_opacity(
                0.06, most_urgent_color or C.ACCENT
            )
            cell.border = ft.Border.all(
                0.5,
                ft.Colors.with_opacity(0.19, most_urgent_color or C.BORDER),
            )
            day_text.color = C.CRITICAL if col == 6 else C.TEXT_PRIMARY
            day_text.weight = ft.FontWeight.W_500
        else:
            cell.bgcolor = None
            cell.border = None
            day_text.color = C.CRITICAL if col == 6 else C.TEXT_PRIMARY
            day_text.weight = ft.FontWeight.W_400

        self._render_activity_markers(
            dots_row,
            acts,
            most_urgent_color,
            class_bar=cell.data.get("class_bar"),
            has_classes=has_classes,
            badge_size=14,
            badge_font_size=7,
        )

    def _navigate(self, delta: int):
        """Navigate based on current view mode."""
        if self._view_mode == "month":
            self._change_month(delta)
        else:
            self._change_week(delta)

    def _change_month(self, delta: int):
        m = self._month + delta
        if m < 1:
            self._month = 12
            self._year -= 1
        elif m > 12:
            self._month = 1
            self._year += 1
        else:
            self._month = m
        self._selected_day = None
        self._render_month()

    def _change_week(self, delta: int):
        """Move ±1 week while advancing the selected weekday."""
        target = self._week_start + timedelta(weeks=delta)
        self._advance_week_selection(target, delta)
        self._render_week()

    def _go_today(self):
        today = date.today()
        self._year = today.year
        self._month = today.month
        self._selected_day = today.day
        self._selected_date = today
        self._week_start = today - timedelta(days=today.weekday())
        self._reset_period_pager()
        if self._view_mode == "month":
            self._render_month()
        else:
            self._render_week()

    def _toggle_view_mode(self, mode: str):
        """Switch between month, week, and schedule view."""
        if self._view_mode == mode:
            return
        previous_mode = self._view_mode
        self._view_mode = mode
        if mode == "schedule":
            self._enter_schedule_mode()
            return
        if previous_mode == "schedule":
            self._leave_schedule_mode()

        if mode == "month":
            self._grid.visible = True
            self._week_strip.visible = False
            if self._selected_date:
                self._year = self._selected_date.year
                self._month = self._selected_date.month
                self._selected_day = self._selected_date.day
        else:
            self._grid.visible = False
            self._week_strip.visible = True
            if self._selected_day:
                try:
                    sel = date(self._year, self._month, self._selected_day)
                    self._week_start = sel - timedelta(days=sel.weekday())
                    self._selected_date = sel
                except ValueError:
                    pass
        # Update tab colors before rendering because the render methods can
        # call page.update() immediately.
        self._style_mode_buttons()
        self._reset_period_pager()
        if self._view_mode == "month":
            self._render_month()
        else:
            self._render_week()

    # Schedule (Portal timetable) mode
    def _style_mode_buttons(self) -> None:
        for mode, btn, icon, text in (
            ("month", self._mode_month_btn, self._mode_month_icon, self._mode_month_text),
            ("week", self._mode_week_btn, self._mode_week_icon, self._mode_week_text),
            ("schedule", self._mode_schedule_btn, self._mode_schedule_icon, self._mode_schedule_text),
        ):
            active = self._view_mode == mode
            btn.bgcolor = (
                ft.Colors.with_opacity(0.19, C.ACCENT) if active else "transparent"
            )
            icon.color = C.TEXT_PRIMARY if active else C.TEXT_SECONDARY
            text.color = C.TEXT_PRIMARY if active else C.TEXT_SECONDARY

    def _apply_schedule_header(self) -> None:
        self._month_label.value = "Lịch học"
        self._month_summary.value = "Thời khóa biểu từ Portal"

    def _set_deadline_surfaces_visible(self, visible: bool) -> None:
        self._period_surface.visible = visible
        self._detail_panel.visible = visible
        # Keep the buttons in the layout (just invisible) so the centered
        # title is not pushed against the right edge.
        for button in (self._previous_btn, self._next_btn, self._today_btn):
            button.opacity = 1.0 if visible else 0.0
            button.disabled = not visible

    def _enter_schedule_mode(self) -> None:
        self._set_deadline_surfaces_visible(False)
        self._apply_schedule_header()
        self._style_mode_buttons()
        self._schedule_view.show()
        try:
            self.update()
        except Exception:
            pass

    def reset_schedule(self) -> None:
        """Drop in-memory timetable data (account/cache namespace changed)."""
        self._detail_filter_mode = "day"
        self._schedule_view.reset()

    def _leave_schedule_mode(self) -> None:
        self._schedule_view.hide()
        self._set_deadline_surfaces_visible(True)

    def _on_day_click(self, e):
        cell = e.control
        day = cell.data.get("day", 0) if isinstance(cell.data, dict) else 0
        if day > 0:
            self._on_day_click_by_day(day)

    def _on_day_click_by_day(self, day: int):
        old_day = self._selected_day
        self._selected_day = day
        try:
            self._selected_date = date(self._year, self._month, day)
            if self._detail_filter_mode == "week_classes":
                self._week_start = self._selected_date - timedelta(
                    days=self._selected_date.weekday()
                )
        except ValueError:
            pass
        today = date.today()
        for r in range(6):
            for c in range(7):
                cell = self._day_cells[r][c]
                cell_day = cell.data.get("day", 0) if isinstance(cell.data, dict) else 0
                if cell_day == old_day or cell_day == day:
                    if cell_day == 0:
                        continue
                    self._restyle_cell(cell, cell_day, c, today)
        if hasattr(self, "_period_pages"):
            self._render_period_pages()
        self._update_day_panel()
        try:
            self._page_ref.update()
        except Exception:
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _active_schedule_monday(self) -> date:
        if self._view_mode == "week":
            return self._week_start
        if self._selected_day is not None:
            try:
                sel = date(self._year, self._month, self._selected_day)
                return sel - timedelta(days=sel.weekday())
            except ValueError:
                pass
        today = date.today()
        if today.year == self._year and today.month == self._month:
            return today - timedelta(days=today.weekday())
        first_day = date(self._year, self._month, 1)
        return first_day - timedelta(days=first_day.weekday())

    def _set_detail_filter_mode(
        self, mode: Literal["day", "week_classes", "tasks"]
    ) -> None:
        if self._detail_filter_mode == mode:
            return
        self._detail_filter_mode = mode
        if mode == "week_classes":
            monday = self._active_schedule_monday()
            self._schedule_view.hydrate_and_ensure_weeks([monday], debounce_ms=0)
        self._update_day_panel()
        try:
            self._page_ref.update()
        except Exception:
            pass

    def _reload_active_schedule_week(self) -> None:
        monday = self._active_schedule_monday()
        self._schedule_view.reload_week(monday)
        self._update_day_panel()
        try:
            self._page_ref.update()
        except Exception:
            pass

    def _style_filter_chips(
        self, week_class_count: int, tasks_count: int
    ) -> None:
        self._filter_week_text.value = f"Cả tuần ({week_class_count} ca)"
        self._filter_tasks_text.value = f"Bài tập ({tasks_count})"
        for mode, chip, txt in (
            ("day", self._filter_day_chip, self._filter_day_text),
            ("week_classes", self._filter_week_chip, self._filter_week_text),
            ("tasks", self._filter_tasks_chip, self._filter_tasks_text),
        ):
            active = self._detail_filter_mode == mode
            chip.bgcolor = (
                ft.Colors.with_opacity(0.16, C.ACCENT) if active else C.SURFACE
            )
            chip.border = ft.Border.all(
                1,
                ft.Colors.with_opacity(0.35, C.ACCENT) if active else C.BORDER,
            )
            txt.color = C.ACCENT if active else C.TEXT_SECONDARY
            txt.weight = (
                ft.FontWeight.W_600 if active else ft.FontWeight.W_500
            )

    def _render_portal_status_row(self, monday: date) -> None:
        is_loading, error_msg = self._schedule_view.week_status(monday)
        controls: list[ft.Control] = []
        if is_loading:
            controls = [
                ft.ProgressRing(
                    width=13, height=13, stroke_width=2, color=C.ACCENT
                ),
                ft.Text("Đang tải lịch học…", size=11, color=C.TEXT_SECONDARY),
            ]
        elif error_msg:
            is_maint = "bảo trì" in error_msg.casefold()
            icon_tone = C.WARNING if is_maint else C.CRITICAL
            text_tone = C.TEXT_PRIMARY if is_maint else C.CRITICAL
            icon = (
                ft.Icons.WARNING_AMBER_ROUNDED
                if is_maint
                else ft.Icons.ERROR_OUTLINE_ROUNDED
            )
            controls = [
                ft.Icon(icon, size=15, color=icon_tone),
                ft.Text(
                    error_msg,
                    size=11,
                    color=text_tone,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
                ft.TextButton(
                    "Thử lại",
                    height=34,
                    on_click=lambda _: self._reload_active_schedule_week(),
                ),
            ]
        self._portal_status_row.controls = controls
        self._portal_status_row.visible = bool(controls)

    def _make_compact_session_row(self, session: ClassSession) -> ft.Container:
        room_badge = compact_room_badge_text(session)
        is_online = room_badge == "E-Learning"
        room_fg = C.SAFE if is_online else C.TEXT_SECONDARY
        room_bg = (
            ft.Colors.with_opacity(0.10, C.SAFE)
            if is_online
            else C.SURFACE_ELEVATED
        )

        meta_controls: list[ft.Control] = [
            ft.Container(
                content=ft.Text(
                    room_badge,
                    size=10,
                    weight=ft.FontWeight.W_600 if is_online else ft.FontWeight.W_500,
                    color=room_fg,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                bgcolor=room_bg,
                border_radius=5,
                padding=ft.Padding.symmetric(horizontal=6, vertical=1.5),
            )
        ]
        if session.cancelled:
            meta_controls.append(
                ft.Container(
                    content=ft.Text(
                        "Tạm ngưng",
                        size=9.5,
                        weight=ft.FontWeight.W_600,
                        color=C.CRITICAL,
                    ),
                    bgcolor=ft.Colors.with_opacity(0.12, C.CRITICAL),
                    border_radius=5,
                    padding=ft.Padding.symmetric(horizontal=6, vertical=1.5),
                )
            )

        time_col = ft.Container(
            width=44,
            alignment=ft.Alignment(-1, 0),
            content=ft.Column(
                controls=[
                    ft.Text(
                        f"{session.start_at:%H:%M}",
                        size=11,
                        weight=ft.FontWeight.W_700,
                        color=C.ACCENT,
                        max_lines=1,
                    ),
                    ft.Text(
                        f"{session.end_at:%H:%M}",
                        size=10,
                        color=C.TEXT_SECONDARY,
                        max_lines=1,
                    ),
                ],
                spacing=1,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
        )
        accent_bar = ft.Container(
            width=3,
            height=32,
            border_radius=2,
            bgcolor=C.CRITICAL if session.cancelled else C.ACCENT,
        )
        middle_col = ft.Column(
            controls=[
                ft.Text(
                    session.subject,
                    size=12.5,
                    weight=ft.FontWeight.W_600,
                    color=C.TEXT_PRIMARY,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Row(
                    controls=meta_controls,
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=2,
            tight=True,
            expand=True,
        )
        row_container = ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=7),
            bgcolor=C.SURFACE,
            border_radius=8,
            border=ft.Border.all(1, C.BORDER),
            ink=True,
            tooltip="Xem chi tiết buổi học",
            data={"compact_session_row": True, "min_height": 46},
            content=ft.Row(
                controls=[
                    ft.Container(width=0, height=32),
                    time_col,
                    accent_bar,
                    middle_col,
                    ft.Icon(
                        ft.Icons.CHEVRON_RIGHT_ROUNDED,
                        size=18,
                        color=C.TEXT_SECONDARY,
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_click=lambda _e, item=session: self._schedule_view.open_session_details(
                item
            ),
        )
        return row_container

    def _update_day_panel(self):
        """Update bottom panel with filter chips, compact class rows, and activity mini-cards."""
        self._day_list.controls.clear()
        urgency_priority = {"overdue": 0, "critical": 1, "warning": 2, "safe": 3}

        active_monday = self._active_schedule_monday()
        week_sessions_map = self._schedule_view.sessions_for_week(active_monday)
        week_class_count = sum(len(v) for v in week_sessions_map.values())

        if self._view_mode == "week":
            day_date = self._selected_date
            tasks_scope_count = sum(
                len(
                    self._deadline_map.get(
                        (self._week_start + timedelta(days=i)).isoformat(), []
                    )
                )
                for i in range(7)
            )
        else:
            if self._selected_day is not None:
                try:
                    day_date = date(self._year, self._month, self._selected_day)
                except ValueError:
                    day_date = None
            else:
                day_date = None
            tasks_scope_count = (
                len(self._deadline_map.get(day_date.isoformat(), []))
                if day_date is not None
                else 0
            )

        self._style_filter_chips(week_class_count, tasks_scope_count)
        self._render_portal_status_row(active_monday)

        today = date.today()

        if self._detail_filter_mode == "week_classes":
            week_end = active_monday + timedelta(days=6)
            self._day_title.value = (
                f"Lịch học tuần {active_monday:%d/%m} - {week_end:%d/%m}"
            )
            self._day_count.value = (
                f"{week_class_count} ca học" if week_class_count else ""
            )
            for i in range(7):
                d = active_monday + timedelta(days=i)
                sessions = week_sessions_map.get(d, ())
                if not sessions:
                    continue
                self._day_list.controls.append(
                    ft.Container(
                        padding=ft.Padding.only(top=4 if self._day_list.controls else 0, bottom=1),
                        content=ft.Text(
                            f"{_WEEKDAYS_LONG_VI[d.weekday()]}, {d:%d/%m}",
                            size=11,
                            weight=ft.FontWeight.W_600,
                            color=C.ACCENT if d == today else C.TEXT_SECONDARY,
                        ),
                    )
                )
                for sess in sessions:
                    self._day_list.controls.append(
                        self._make_compact_session_row(sess)
                    )
            self._day_empty_title.value = "Tuần này không có lịch học"
            self._day_empty_hint.value = "Chuyển tuần khác hoặc bấm tải lại để kiểm tra"
            self._day_empty.visible = week_class_count == 0
            return

        if self._detail_filter_mode == "tasks" and self._view_mode == "week":
            week_end = self._week_start + timedelta(days=6)
            self._day_title.value = (
                f"Bài tập tuần {self._week_start:%d/%m} - {week_end:%d/%m}"
            )
            self._day_count.value = (
                f"{tasks_scope_count} bài tập" if tasks_scope_count else ""
            )
            for i in range(7):
                d = self._week_start + timedelta(days=i)
                acts = self._deadline_map.get(d.isoformat(), [])
                if not acts:
                    continue
                sorted_acts = sorted(
                    acts,
                    key=lambda a: urgency_priority.get(
                        a.get("urgency", "safe"), 3
                    ),
                )
                self._day_list.controls.append(
                    ft.Container(
                        padding=ft.Padding.only(top=4 if self._day_list.controls else 0, bottom=1),
                        content=ft.Text(
                            f"{_WEEKDAYS_LONG_VI[d.weekday()]}, {d:%d/%m}",
                            size=11,
                            weight=ft.FontWeight.W_600,
                            color=C.ACCENT if d == today else C.TEXT_SECONDARY,
                        ),
                    )
                )
                for act in sorted_acts:
                    self._day_list.controls.append(self._make_mini_card(act))
            self._day_empty_title.value = "Không có bài tập trong tuần này"
            self._day_empty_hint.value = "Chuyển sang tuần khác để xem tiếp"
            self._day_empty.visible = tasks_scope_count == 0
            return

        if day_date is None:
            self._day_title.value = "Chọn một ngày"
            self._day_count.value = ""
            self._day_empty_title.value = "Không có bài tập đến hạn"
            self._day_empty_hint.value = "Chọn ngày có chấm màu để xem"
            self._day_empty.visible = True
            return

        try:
            from gui.core.utils import get_vi_weekday
            wd = get_vi_weekday(
                datetime(day_date.year, day_date.month, day_date.day)
            )
            self._day_title.value = f"{wd}, {day_date.day:02d}/{day_date.month:02d}"
        except Exception:
            self._day_title.value = (
                f"{day_date.day:02d}/{day_date.month:02d}/{day_date.year}"
            )

        acts = self._deadline_map.get(day_date.isoformat(), [])
        sorted_acts = sorted(
            acts,
            key=lambda a: urgency_priority.get(a.get("urgency", "safe"), 3),
        )
        day_sessions = (
            ()
            if self._detail_filter_mode == "tasks"
            else self._schedule_view.sessions_for_day(day_date)
        )

        if day_sessions and sorted_acts:
            self._day_count.value = (
                f"{len(day_sessions)} ca học · {len(sorted_acts)} bài tập"
            )
        elif day_sessions:
            self._day_count.value = f"{len(day_sessions)} ca học"
        elif sorted_acts:
            self._day_count.value = f"{len(sorted_acts)} bài tập"
        else:
            self._day_count.value = ""

        for sess in day_sessions:
            self._day_list.controls.append(self._make_compact_session_row(sess))
        for act in sorted_acts:
            self._day_list.controls.append(self._make_mini_card(act))

        has_items = bool(day_sessions or sorted_acts)
        self._day_empty_title.value = "Không có bài tập đến hạn"
        self._day_empty_hint.value = "Chọn ngày có chấm màu để xem"
        self._day_empty.visible = not has_items

    def _make_mini_card(self, act: Dict[str, Any]) -> ft.Container:
        """Create a compact activity card - CV-04, CV-08 fixes."""
        title = act.get("title", "Không rõ")
        course = act.get("course", "")
        act_type = act.get("type", "other")
        urgency = act.get("urgency", "safe")
        deadline_str = act.get("deadline", "")
        submission = act.get("submission_status", "")

        urg_color = get_urgency_color(urgency)
        countdown_text, is_overdue = get_countdown(deadline_str, act_type)

        # Type label
        from gui.core.theme import _TYPE_LABELS, _TYPE_COLORS
        type_label = _TYPE_LABELS.get(act_type, "SỰ KIỆN")
        type_color = _TYPE_COLORS.get(act_type, C.TEXT_SECONDARY)

        # CV-04: Clean course name
        clean_course = course
        if clean_course.startswith("[") and "]" in clean_course:
            # Remove "[CODE]" prefix, e.g. "[UTH2026] Name" → "Name"
            idx = clean_course.index("]")
            after = clean_course[idx + 1:].strip()
            if after:
                clean_course = after
            # If nothing after ], keep original but remove brackets
            else:
                clean_course = clean_course.strip("[]")

        # Submission badge - persuasion: ✓ prefix for completion signal
        sub_controls = []
        if submission in ("submitted", "Đã nộp"):
            sub_controls.append(
                ft.Container(
                    content=ft.Text("✓ Đã nộp", size=9, weight=ft.FontWeight.W_600, color=C.SAFE),
                    padding=ft.Padding.symmetric(horizontal=5, vertical=1),
                    bgcolor=ft.Colors.with_opacity(0.08, C.SAFE),
                    border_radius=4,
                )
            )
        elif submission in ("graded", "Đã chấm"):
            sub_controls.append(
                ft.Container(
                    content=ft.Text("★ Đã chấm", size=9, weight=ft.FontWeight.W_600, color=C.SAFE),
                    padding=ft.Padding.symmetric(horizontal=5, vertical=1),
                    bgcolor=ft.Colors.with_opacity(0.08, C.SAFE),
                    border_radius=4,
                )
            )

        # Course row
        course_row_controls = [
            ft.Text(
                clean_course, size=10, color=C.TEXT_SECONDARY,
                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True,
            ),
        ] + sub_controls

        card = ft.Container(
            content=ft.Row(
                controls=[
                    # CV-08: Left color bar full height
                    ft.Container(
                        width=3, bgcolor=urg_color,
                        border_radius=ft.BorderRadius.only(top_left=8, bottom_left=8),
                        height=60,
                    ),
                    # Content
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Row(
                                    controls=[
                                        ft.Container(
                                            content=ft.Text(type_label, size=9, weight=ft.FontWeight.W_600, color=type_color),
                                            padding=ft.Padding.symmetric(horizontal=6, vertical=1),
                                            border=ft.Border.all(1, type_color),
                                            border_radius=4,
                                        ),
                                        ft.Text(
                                            countdown_text, size=10,
                                            color=C.CRITICAL if is_overdue else urg_color,
                                            weight=ft.FontWeight.W_600,
                                            italic=is_overdue,
                                        ),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                ft.Text(
                                    title, size=13, weight=ft.FontWeight.W_600,
                                    color=C.TEXT_PRIMARY, max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                                ft.Row(
                                    controls=course_row_controls,
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                            ],
                            spacing=3,
                        ),
                        padding=ft.Padding.only(left=10, right=10, top=8, bottom=8),
                        expand=True,
                    ),
                ],
                spacing=0,
            ),
            bgcolor=C.SURFACE,
            border_radius=8,
            border=ft.Border.all(1, C.BORDER),
            on_click=lambda _, a=act: self._on_card_click(a),
            ink=True,
        )
        return card

    def _on_card_click(self, act: Dict[str, Any]):
        """Handle click on a mini card - open detail view."""
        if self._on_open_detail:
            self._on_open_detail(act)

    def update_theme(self):
        """Update colors of all calendar controls dynamically on theme switch."""
        from gui.core.theme import C
        current_palette = semantic_palette()
        refresh_semantic_control_colors(
            [self], self._rendered_palette, current_palette
        )
        self._rendered_palette = current_palette
        self.bgcolor = C.BG
        self._header.bgcolor = C.BG

        self._back_btn.icon_color = C.TEXT_SECONDARY
        self._previous_btn.icon_color = C.TEXT_SECONDARY
        self._month_label.color = C.TEXT_PRIMARY
        self._next_btn.icon_color = C.TEXT_SECONDARY
        self._today_btn.icon_color = C.ACCENT
        self._month_summary.color = C.TEXT_SECONDARY

        # Weekdays Row Controls
        try:
            for wd_c in self._weekday_row.controls:
                txt = wd_c.content
                txt.color = C.CRITICAL if txt.value == "CN" else C.TEXT_SECONDARY
        except Exception:
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

        # Mode Toggle Colors
        try:
            self._mode_toggle.bgcolor = C.SURFACE
            self._mode_toggle.border = ft.Border.all(1, C.BORDER)
            self._style_mode_buttons()
        except Exception:
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

        # Day Panel Static Info
        self._day_title.color = C.TEXT_PRIMARY
        self._day_count.color = C.TEXT_SECONDARY
        self._detail_refresh_btn.icon_color = C.TEXT_SECONDARY

        # Day Empty State
        self._day_empty_icon.color = C.BORDER
        self._day_empty_title.color = C.TEXT_SECONDARY
        self._day_empty_hint.color = C.BORDER
        self._detail_divider.color = C.BORDER

        # Compatibility controls are detached once the pager is mounted but are
        # still exercised by existing lifecycle paths and tests.
        for row in self._day_cells:
            for cell in row:
                cell.data["today_dot"].bgcolor = C.ACCENT
                if cell.data.get("class_bar") is not None:
                    cell.data["class_bar"].bgcolor = C.ACCENT
        for cell in self._week_cells:
            cell.data["count_text"].color = C.TEXT_SECONDARY
            self._render_shift_bars(
                cell.data["shift_bars"], cell.data.get("date")
            )

        # Re-render Cells (applies new colors to grid cells)
        if self._view_mode == "month":
            self._render_month(
                update_page=False, ensure_schedule=self.visible
            )
        elif self._view_mode == "week":
            self._render_week(
                update_page=False, ensure_schedule=self.visible
            )
        self._render_period_pages()

        # Re-render selected day details list (re-creates mini-cards with correct theme)
        self._update_day_panel()
        self._schedule_view.update_theme()
        if self._view_mode == "schedule":
            self._apply_schedule_header()

        try:
            self.update()
        except Exception:
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

def main(page: ft.Page):
    """Stub main function to support Flet Preview on this file directly."""
    # Apply compatibility shims if running directly
    try:
        from gui.flet_compat import patch_flet
        patch_flet()
    except Exception:
        import logging as _fb_log
        _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)
    from gui.app_controller import AppController
    AppController(page)

if __name__ == "__main__":
    ft.run(main=main, assets_dir=os.path.join(_project_root, "assets"))
