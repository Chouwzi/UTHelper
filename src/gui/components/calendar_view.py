"""CalendarView — Monthly calendar grid showing activity deadlines.

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
import calendar
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import flet as ft

from gui.core.theme import C
from gui.core.utils import (
    format_deadline,
    get_countdown,
    get_urgency_color,
    parse_datetime,
)

_WEEKDAYS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
_MONTHS_VI = {
    1: "Tháng 1", 2: "Tháng 2", 3: "Tháng 3", 4: "Tháng 4",
    5: "Tháng 5", 6: "Tháng 6", 7: "Tháng 7", 8: "Tháng 8",
    9: "Tháng 9", 10: "Tháng 10", 11: "Tháng 11", 12: "Tháng 12",
}


class CalendarView(ft.Container):
    """Full-screen calendar overlay for viewing activity deadlines by date."""

    def __init__(
        self,
        page: ft.Page,
        on_close: Optional[Callable] = None,
        on_open_detail: Optional[Callable] = None,
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

        self._build_ui()

    # ── Build UI ────────────────────────────────────────────────────────
    def _build_ui(self):
        # Month navigation header
        self._month_label = ft.Text(
            "", size=16, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY,
        )
        # Month summary (CV-05)
        self._month_summary = ft.Text(
            "", size=10, color=C.TEXT_SECONDARY,
        )

        self._header = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.IconButton(
                                ft.Icons.ARROW_BACK_ROUNDED,
                                icon_color=C.TEXT_SECONDARY, icon_size=18,
                                tooltip="Quay lại",
                                on_click=lambda _: self._on_close() if self._on_close else None,
                            ),
                            ft.Row(
                                controls=[
                                    ft.IconButton(
                                        ft.Icons.CHEVRON_LEFT_ROUNDED,
                                        icon_color=C.TEXT_SECONDARY, icon_size=20,
                                        tooltip="Trước",
                                        on_click=lambda _: self._navigate(-1),
                                    ),
                                    self._month_label,
                                    ft.IconButton(
                                        ft.Icons.CHEVRON_RIGHT_ROUNDED,
                                        icon_color=C.TEXT_SECONDARY, icon_size=20,
                                        tooltip="Sau",
                                        on_click=lambda _: self._navigate(1),
                                    ),
                                ],
                                spacing=0,
                                alignment=ft.MainAxisAlignment.CENTER,
                            ),
                            ft.IconButton(
                                ft.Icons.TODAY_ROUNDED,
                                icon_color=C.ACCENT, icon_size=18,
                                tooltip="Hôm nay",
                                on_click=lambda _: self._go_today(),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
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
            padding=ft.Padding.only(left=4, right=4, top=12, bottom=0),
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

        # ── Mode toggle (Month / Week) ──
        self._mode_month_btn = ft.Container(
            content=ft.Row(controls=[
                ft.Icon(ft.Icons.CALENDAR_MONTH_ROUNDED, size=14, color=C.TEXT_PRIMARY),
                ft.Text("Tháng", size=11, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY),
            ], spacing=4, tight=True),
            bgcolor=C.ACCENT + "30",
            border_radius=12,
            padding=ft.Padding(left=14, right=14, top=5, bottom=5),
            on_click=lambda _: self._toggle_view_mode("month"),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        )
        self._mode_week_btn = ft.Container(
            content=ft.Row(controls=[
                ft.Icon(ft.Icons.VIEW_WEEK_ROUNDED, size=14, color=C.TEXT_SECONDARY),
                ft.Text("Tuần", size=11, weight=ft.FontWeight.W_600, color=C.TEXT_SECONDARY),
            ], spacing=4, tight=True),
            bgcolor="transparent",
            border_radius=12,
            padding=ft.Padding(left=14, right=14, top=5, bottom=5),
            on_click=lambda _: self._toggle_view_mode("week"),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        )
        self._mode_toggle = ft.Container(
            content=ft.Row(
                [self._mode_month_btn, self._mode_week_btn],
                spacing=2,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            bgcolor=C.SURFACE,
            border_radius=14,
            padding=ft.Padding(left=3, right=3, top=3, bottom=3),
            border=ft.Border.all(1, C.BORDER),
            margin=ft.Margin(left=60, right=60, top=4, bottom=6),
        )

        # Calendar grid (6 rows × 7 cols) — CV-01: denser
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

        # ── Week strip (7 day-columns) ──
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
            padding=ft.Padding.only(top=4, bottom=4),
            visible=False,  # Hidden by default (month mode)
        )

        # Selected day detail panel
        self._day_title = ft.Text("", size=14, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY)
        self._day_count = ft.Text("", size=11, color=C.TEXT_SECONDARY)
        self._day_list = ft.ListView(spacing=6, expand=True)
        # CV-06: Better empty state
        self._day_empty = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Icon(ft.Icons.CALENDAR_TODAY_ROUNDED, size=28, color=C.BORDER),
                    ft.Text("Không có bài tập đến hạn", size=12, color=C.TEXT_SECONDARY),
                    ft.Text("Chọn ngày có chấm màu để xem", size=10, color=C.BORDER),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
            ),
            alignment=ft.Alignment(0, 0),
            expand=True,
            visible=False,
        )

        detail_panel = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Divider(height=1, color=C.BORDER),
                    ft.Container(
                        content=ft.Row(
                            controls=[self._day_title, self._day_count],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        padding=ft.Padding.only(left=16, right=16, top=8, bottom=4),
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

        # Assemble
        self.content = ft.Column(
            controls=[self._header, self._mode_toggle, self._grid, self._week_strip, detail_panel],
            spacing=0,
            expand=True,
        )

    def _make_day_cell(self) -> ft.Container:
        """Create a single day cell with today indicator dot."""
        day_num = ft.Text("", size=12, color=C.TEXT_PRIMARY, text_align=ft.TextAlign.CENTER)
        # Today indicator — small accent dot under the number (always visible for today)
        today_dot = ft.Container(
            width=5, height=5, border_radius=3, bgcolor=C.ACCENT, visible=False,
        )
        dots_row = ft.Row(controls=[], spacing=2, alignment=ft.MainAxisAlignment.CENTER)
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
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        cell.data = {"day_text": day_num, "dots_row": dots_row, "today_dot": today_dot, "day": 0}
        return cell

    def _make_week_cell(self, col_index: int) -> ft.Container:
        """Create a single week view cell — taller and wider than month cells."""
        weekday_labels = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
        wd_color = C.CRITICAL if col_index == 6 else C.TEXT_SECONDARY

        wd_label = ft.Text(
            weekday_labels[col_index], size=10, weight=ft.FontWeight.W_600,
            color=wd_color, text_align=ft.TextAlign.CENTER,
        )
        day_num = ft.Text(
            "", size=18, weight=ft.FontWeight.W_600,
            color=C.TEXT_PRIMARY, text_align=ft.TextAlign.CENTER,
        )
        dots_row = ft.Row(controls=[], spacing=2, alignment=ft.MainAxisAlignment.CENTER)
        count_text = ft.Text("", size=9, color=C.TEXT_SECONDARY, text_align=ft.TextAlign.CENTER)

        cell = ft.Container(
            content=ft.Column(
                controls=[wd_label, day_num, dots_row, count_text],
                spacing=1,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            width=52, height=80,
            border_radius=12,
            alignment=ft.Alignment(0, 0),
            on_click=lambda e, idx=col_index: self._on_week_cell_click(idx),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        cell.data = {
            "wd_label": wd_label, "day_text": day_num,
            "dots_row": dots_row, "count_text": count_text,
            "col": col_index, "date": None,
        }
        return cell

    def _on_week_cell_click(self, col_index: int):
        """Handle click on a week cell."""
        cell = self._week_cells[col_index]
        cell_date = cell.data.get("date")
        if cell_date:
            self._selected_date = cell_date
            self._selected_day = cell_date.day
            self._year = cell_date.year
            self._month = cell_date.month
            self._render_week()

    def _render_week(self):
        """Render the 7-day week strip for self._week_start."""
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

        # Summary for week
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

        if week_total > 0:
            done_text = f" · {week_submitted} đã nộp" if week_submitted > 0 else ""
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
            cell.data["date"] = d

            day_text.value = str(d.day)

            is_today = (d == today)
            is_selected = (d == self._selected_date)

            key = d.strftime("%Y-%m-%d")
            acts = self._deadline_map.get(key, [])
            has_deadline = len(acts) > 0

            # Most urgent color
            most_urgent_color = None
            if has_deadline:
                urgency_priority = {"overdue": 0, "critical": 1, "warning": 2, "safe": 3}
                sorted_acts = sorted(acts, key=lambda a: urgency_priority.get(a.get("urgency", "safe"), 3))
                most_urgent_color = get_urgency_color(sorted_acts[0].get("urgency", "safe"))

            # Styling
            wd_label: ft.Text = cell.data["wd_label"]
            if is_selected and is_today:
                cell.bgcolor = C.ACCENT + "20"
                cell.border = ft.Border.all(2, C.ACCENT)
                day_text.color = C.ACCENT
                day_text.weight = ft.FontWeight.W_700
                wd_label.color = C.ACCENT
            elif is_selected:
                cell.bgcolor = "#15889AAF"
                cell.border = ft.Border.all(1.5, "#99AABBCC")
                day_text.color = C.TEXT_PRIMARY
                day_text.weight = ft.FontWeight.W_700
                wd_label.color = C.TEXT_PRIMARY
            elif is_today:
                cell.bgcolor = C.ACCENT + "10"
                cell.border = ft.Border.all(1, C.ACCENT + "50")
                day_text.color = C.ACCENT
                day_text.weight = ft.FontWeight.W_700
                wd_label.color = C.ACCENT
            elif has_deadline:
                cell.bgcolor = (most_urgent_color or C.ACCENT) + "0D"
                cell.border = ft.Border.all(0.5, (most_urgent_color or C.BORDER) + "30")
                day_text.color = C.CRITICAL if i == 6 else C.TEXT_PRIMARY
                day_text.weight = ft.FontWeight.W_500
                wd_label.color = C.CRITICAL if i == 6 else C.TEXT_SECONDARY
            else:
                cell.bgcolor = None
                cell.border = None
                day_text.color = C.CRITICAL if i == 6 else C.TEXT_PRIMARY
                day_text.weight = ft.FontWeight.W_400
                wd_label.color = C.CRITICAL if i == 6 else C.TEXT_SECONDARY

            # Activity dots
            dots_row.controls.clear()
            if acts:
                if len(acts) >= 3:
                    dots_row.controls.append(
                        ft.Container(
                            content=ft.Text(str(len(acts)), size=8, color="#FFFFFF",
                                            weight=ft.FontWeight.W_700, text_align=ft.TextAlign.CENTER),
                            width=16, height=16, border_radius=8,
                            bgcolor=most_urgent_color or C.ACCENT,
                            alignment=ft.Alignment(0, 0),
                        )
                    )
                else:
                    seen = set()
                    for a in acts:
                        urg = a.get("urgency", "safe")
                        col = get_urgency_color(urg)
                        if col not in seen:
                            seen.add(col)
                            dots_row.controls.append(
                                ft.Container(width=6, height=6, border_radius=3, bgcolor=col)
                            )

            # Count text
            if len(acts) > 0:
                count_text.value = f"{len(acts)} bài"
                count_text.color = most_urgent_color or C.TEXT_SECONDARY
            else:
                count_text.value = ""

        self._update_day_panel()
        try:
            self._page_ref.update()
        except Exception:
            pass

    # ── Public API ──────────────────────────────────────────────────────
    def update_data(self, activities: List[Dict[str, Any]]):
        """Update calendar with new activity data and refresh grid."""
        self._all_data = activities
        self._build_deadline_map()
        if self._view_mode == "month":
            self._render_month()
        else:
            self._render_week()

    def show(self):
        self.visible = True
        # P4: Always anchor to today when opening — reduces cognitive orientation time
        today = date.today()
        if self._selected_date != today:
            self._year = today.year
            self._month = today.month
            self._selected_day = today.day
            self._selected_date = today
            self._week_start = today - timedelta(days=today.weekday())
        if self._view_mode == "month":
            self._render_month()
        else:
            self._render_week()

    def hide(self):
        self.visible = False

    # ── Internal Logic ──────────────────────────────────────────────────
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

    def _render_month(self):
        """Render the grid for self._year / self._month."""
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

        if month_total > 0:
            done_text = f" · {month_submitted} đã nộp" if month_submitted > 0 else ""
            self._month_summary.value = f"{month_total} bài tập{done_text}"
        else:
            self._month_summary.value = "Không có bài tập trong tháng này"

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
                    cell.bgcolor = None
                    cell.border = None
                    cell.on_click = None
                    continue

                day_text.value = str(day)
                cell.on_click = lambda e, d=day: self._on_day_click_by_day(d)

                is_today = (day == today.day and self._month == today.month and self._year == today.year)
                is_selected = (day == self._selected_day)

                # Deadline data for this day
                key = f"{self._year}-{self._month:02d}-{day:02d}"
                acts = self._deadline_map.get(key, [])
                has_deadline = len(acts) > 0

                # CV-07: Determine most urgent urgency for cell hint
                most_urgent_color = None
                if has_deadline:
                    urgency_priority = {"overdue": 0, "critical": 1, "warning": 2, "safe": 3}
                    sorted_acts = sorted(acts, key=lambda a: urgency_priority.get(a.get("urgency", "safe"), 3))
                    most_urgent_color = get_urgency_color(sorted_acts[0].get("urgency", "safe"))

                # ── Cell styling ──
                # Today  = accent dot indicator (always visible, unique landmark)
                # Selected = white/silver border (navigation state)
                # Today+Selected = both combined
                # Has deadline = subtle urgency-tinted bg

                today_dot: ft.Container = cell.data["today_dot"]
                today_dot.visible = is_today  # dot is ONLY for today

                if is_selected and is_today:
                    # Both: accent text + white selection border + today dot
                    cell.bgcolor = "#1A8899AA"  # subtle cool tint
                    cell.border = ft.Border.all(1.5, "#99AABBCC")
                    day_text.color = C.ACCENT
                    day_text.weight = ft.FontWeight.W_700
                elif is_selected:
                    # Selected only: white/silver border, neutral bg
                    cell.bgcolor = "#15889AAF"
                    cell.border = ft.Border.all(1.5, "#99AABBCC")
                    day_text.color = C.TEXT_PRIMARY
                    day_text.weight = ft.FontWeight.W_600
                elif is_today:
                    # Today only: accent text + dot, NO border
                    cell.bgcolor = None
                    cell.border = None
                    day_text.color = C.ACCENT
                    day_text.weight = ft.FontWeight.W_700
                elif has_deadline:
                    # Has deadline: subtle urgency bg
                    cell.bgcolor = (most_urgent_color or C.ACCENT) + "10"
                    cell.border = ft.Border.all(0.5, (most_urgent_color or C.BORDER) + "30")
                    day_text.color = C.CRITICAL if c == 6 else C.TEXT_PRIMARY
                    day_text.weight = ft.FontWeight.W_500
                else:
                    cell.bgcolor = None
                    cell.border = None
                    day_text.color = C.CRITICAL if c == 6 else C.TEXT_PRIMARY
                    day_text.weight = ft.FontWeight.W_400

                # CV-03: Larger dots (6px) with smarter display
                dots_row.controls.clear()
                if acts:
                    if len(acts) >= 3:
                        # Show count badge instead of many dots
                        dots_row.controls.append(
                            ft.Container(
                                content=ft.Text(str(len(acts)), size=7, color="#FFFFFF",
                                                weight=ft.FontWeight.W_700, text_align=ft.TextAlign.CENTER),
                                width=14, height=14, border_radius=7,
                                bgcolor=most_urgent_color or C.ACCENT,
                                alignment=ft.Alignment(0, 0),
                            )
                        )
                    else:
                        urgency_colors = []
                        seen = set()
                        for a in acts:
                            urg = a.get("urgency", "safe")
                            col = get_urgency_color(urg)
                            if col not in seen:
                                seen.add(col)
                                urgency_colors.append(col)
                        for color in urgency_colors[:3]:
                            dots_row.controls.append(
                                ft.Container(width=6, height=6, border_radius=3, bgcolor=color)
                            )

        self._update_day_panel()
        try:
            self._page_ref.update()
        except Exception:
            pass

    def _restyle_cell(self, cell, day: int, col: int, today):
        """Restyle a single cell — extracted for targeted updates."""
        day_text: ft.Text = cell.data["day_text"]
        dots_row: ft.Row = cell.data["dots_row"]

        is_today = (day == today.day and self._month == today.month and self._year == today.year)
        is_selected = (day == self._selected_day)

        key = f"{self._year}-{self._month:02d}-{day:02d}"
        acts = self._deadline_map.get(key, [])
        has_deadline = len(acts) > 0

        most_urgent_color = None
        if has_deadline:
            urgency_priority = {"overdue": 0, "critical": 1, "warning": 2, "safe": 3}
            sorted_acts = sorted(acts, key=lambda a: urgency_priority.get(a.get("urgency", "safe"), 3))
            most_urgent_color = get_urgency_color(sorted_acts[0].get("urgency", "safe"))

        today_dot: ft.Container = cell.data["today_dot"]
        today_dot.visible = is_today

        if is_selected and is_today:
            cell.bgcolor = "#1A8899AA"
            cell.border = ft.Border.all(1.5, "#99AABBCC")
            day_text.color = C.ACCENT
            day_text.weight = ft.FontWeight.W_700
        elif is_selected:
            cell.bgcolor = "#15889AAF"
            cell.border = ft.Border.all(1.5, "#99AABBCC")
            day_text.color = C.TEXT_PRIMARY
            day_text.weight = ft.FontWeight.W_600
        elif is_today:
            cell.bgcolor = None
            cell.border = None
            day_text.color = C.ACCENT
            day_text.weight = ft.FontWeight.W_700
        elif has_deadline:
            cell.bgcolor = (most_urgent_color or C.ACCENT) + "10"
            cell.border = ft.Border.all(0.5, (most_urgent_color or C.BORDER) + "30")
            day_text.color = C.CRITICAL if col == 6 else C.TEXT_PRIMARY
            day_text.weight = ft.FontWeight.W_500
        else:
            cell.bgcolor = None
            cell.border = None
            day_text.color = C.CRITICAL if col == 6 else C.TEXT_PRIMARY
            day_text.weight = ft.FontWeight.W_400

        dots_row.controls.clear()
        if acts:
            if len(acts) >= 3:
                dots_row.controls.append(
                    ft.Container(
                        content=ft.Text(str(len(acts)), size=7, color="#FFFFFF",
                                        weight=ft.FontWeight.W_700, text_align=ft.TextAlign.CENTER),
                        width=14, height=14, border_radius=7,
                        bgcolor=most_urgent_color or C.ACCENT,
                        alignment=ft.Alignment(0, 0),
                    )
                )
            else:
                urgency_colors = []
                seen = set()
                for a in acts:
                    urg = a.get("urgency", "safe")
                    col_c = get_urgency_color(urg)
                    if col_c not in seen:
                        seen.add(col_c)
                        urgency_colors.append(col_c)
                for color in urgency_colors[:3]:
                    dots_row.controls.append(
                        ft.Container(width=6, height=6, border_radius=3, bgcolor=color)
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
        """Move ±1 week."""
        self._week_start += timedelta(weeks=delta)
        self._selected_date = None
        self._selected_day = None
        self._render_week()

    def _go_today(self):
        today = date.today()
        self._year = today.year
        self._month = today.month
        self._selected_day = today.day
        self._selected_date = today
        self._week_start = today - timedelta(days=today.weekday())
        if self._view_mode == "month":
            self._render_month()
        else:
            self._render_week()

    def _toggle_view_mode(self, mode: str):
        """Switch between month and week view."""
        if self._view_mode == mode:
            return
        self._view_mode = mode

        if mode == "month":
            # Update toggle styling
            self._mode_month_btn.bgcolor = C.ACCENT + "30"
            self._mode_month_btn.content.controls[0].color = C.TEXT_PRIMARY
            self._mode_month_btn.content.controls[1].color = C.TEXT_PRIMARY
            self._mode_week_btn.bgcolor = "transparent"
            self._mode_week_btn.content.controls[0].color = C.TEXT_SECONDARY
            self._mode_week_btn.content.controls[1].color = C.TEXT_SECONDARY
            # Show/hide
            self._grid.visible = True
            self._week_strip.visible = False
            # Sync: if a date was selected in week view, jump to that month
            if self._selected_date:
                self._year = self._selected_date.year
                self._month = self._selected_date.month
                self._selected_day = self._selected_date.day
            self._render_month()
        else:
            # Update toggle styling
            self._mode_week_btn.bgcolor = C.ACCENT + "30"
            self._mode_week_btn.content.controls[0].color = C.TEXT_PRIMARY
            self._mode_week_btn.content.controls[1].color = C.TEXT_PRIMARY
            self._mode_month_btn.bgcolor = "transparent"
            self._mode_month_btn.content.controls[0].color = C.TEXT_SECONDARY
            self._mode_month_btn.content.controls[1].color = C.TEXT_SECONDARY
            # Show/hide
            self._grid.visible = False
            self._week_strip.visible = True
            # Sync: jump to week containing selected day
            if self._selected_day:
                try:
                    sel = date(self._year, self._month, self._selected_day)
                    self._week_start = sel - timedelta(days=sel.weekday())
                    self._selected_date = sel
                except ValueError:
                    pass
            self._render_week()

    def _on_day_click(self, e):
        cell = e.control
        day = cell.data.get("day", 0) if isinstance(cell.data, dict) else 0
        if day > 0:
            self._on_day_click_by_day(day)

    def _on_day_click_by_day(self, day: int):
        old_day = self._selected_day
        self._selected_day = day
        # Sync selected_date for week view
        try:
            self._selected_date = date(self._year, self._month, day)
        except ValueError:
            pass
        # H1: Only restyle the 2 affected cells instead of full 42-cell re-render
        today = date.today()
        for r in range(6):
            for c in range(7):
                cell = self._day_cells[r][c]
                cell_day = cell.data.get("day", 0) if isinstance(cell.data, dict) else 0
                if cell_day == old_day or cell_day == day:
                    if cell_day == 0:
                        continue
                    self._restyle_cell(cell, cell_day, c, today)
        self._update_day_panel()
        try:
            self._page_ref.update()
        except Exception:
            pass

    def _update_day_panel(self):
        """Update bottom panel — CV-06: better microcopy."""
        self._day_list.controls.clear()

        if self._selected_day is None:
            self._day_title.value = "Chọn một ngày"
            self._day_count.value = ""
            self._day_empty.visible = True
            return

        key = f"{self._year}-{self._month:02d}-{self._selected_day:02d}"
        acts = self._deadline_map.get(key, [])

        try:
            from gui.core.utils import get_vi_weekday
            wd = get_vi_weekday(datetime(self._year, self._month, self._selected_day))
            self._day_title.value = f"{wd}, {self._selected_day:02d}/{self._month:02d}"
        except Exception:
            self._day_title.value = f"{self._selected_day:02d}/{self._month:02d}/{self._year}"

        # CV-06: Better count text
        if acts:
            self._day_count.value = f"{len(acts)} bài tập"
        else:
            self._day_count.value = ""
        self._day_empty.visible = len(acts) == 0

        # Sort by urgency (most urgent first)
        urgency_priority = {"overdue": 0, "critical": 1, "warning": 2, "safe": 3}
        sorted_acts = sorted(acts, key=lambda a: urgency_priority.get(a.get("urgency", "safe"), 3))

        for act in sorted_acts:
            card = self._make_mini_card(act)
            self._day_list.controls.append(card)

    def _make_mini_card(self, act: Dict[str, Any]) -> ft.Container:
        """Create a compact activity card — CV-04, CV-08 fixes."""
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

        # Submission badge — persuasion: ✓ prefix for completion signal
        sub_controls = []
        if submission in ("submitted", "Đã nộp"):
            sub_controls.append(
                ft.Container(
                    content=ft.Text("✓ Đã nộp", size=9, weight=ft.FontWeight.W_600, color=C.SAFE),
                    padding=ft.Padding.symmetric(horizontal=5, vertical=1),
                    bgcolor=C.SAFE + "15",
                    border_radius=4,
                )
            )
        elif submission in ("graded", "Đã chấm"):
            sub_controls.append(
                ft.Container(
                    content=ft.Text("★ Đã chấm", size=9, weight=ft.FontWeight.W_600, color=C.SAFE),
                    padding=ft.Padding.symmetric(horizontal=5, vertical=1),
                    bgcolor=C.SAFE + "15",
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
        """Handle click on a mini card — open detail view."""
        if self._on_open_detail:
            self._on_open_detail(act)
