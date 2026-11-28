"""ScheduleWeekView - Portal class timetable for the Calendar tab.

Shows the selected week (Portal ``lichhoc/lichTuan``) as a Ca 1-6 x Thứ 2-CN
timetable like the web Portal. Every class is rendered as a full card.

Weeks are cached on disk and in memory: the current week plus 4 weeks before
and 4 weeks after are loaded from the cache instantly and refreshed in the
background.
"""
from __future__ import annotations

import asyncio
import calendar
from collections.abc import Sequence
import inspect
import logging
import re
import threading
from datetime import date, datetime, timedelta
from typing import Any, Callable, Literal, Optional

import flet as ft

from config import settings
from core.demo_sandbox import is_demo_account
from core.today_schedule import (
    ClassSession,
    ScheduleAuthenticationError,
    SchedulePhase,
    WeekScheduleCache,
)
from core.url_validation import is_trusted_uth_lms_url, validate_https_url
from gui.components.in_app_webview import InAppWebViewDialog
from gui.core.theme import C
from gui.core.utils import DEMO_LMS_WEBVIEW_GUARD_MESSAGE, is_uth_lms_url
import platform_utils

logger = logging.getLogger(__name__)

FetchWeek = Callable[[date], "dict[date, tuple[ClassSession, ...]]"]
OpenUrlCallback = Callable[[str, str], None]
OpenExternalCallback = Callable[[str], None]

_WEEKDAYS_LONG = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
_PICKER_WEEKDAYS = ("T2", "T3", "T4", "T5", "T6", "T7", "CN")
_HTTPS_URL_RE = re.compile(r"https://[^\s<>()\"']+")
_ONLINE_KEYWORDS = ("zoom", "online", "e-learning", "elearning", "trực tuyến", "meet", "teams")
_NARROW_BREAKPOINT = 920
_WIDE_SHELL_BREAKPOINT = 980
_RAIL_WIDTH = 88
_DAY_COLUMN_MIN_W = 112
_DAY_COLUMN_MAX_W = 225
_DAY_COLUMN_GAP = 8
_WEEK_CONTENT_INSET = 28
_SWIPE_VELOCITY = 250.0
_TTL_CURRENT = timedelta(minutes=10)   # re-fetch the current week after this
_TTL_OTHER = timedelta(hours=6)        # ...and the other cached weeks after this
_PREFETCH_GAP_S = 0.3


def _text_action_style(color: str = C.ACCENT) -> ft.ButtonStyle:
    return ft.ButtonStyle(
        color=color,
        bgcolor=ft.Colors.TRANSPARENT,
        overlay_color=ft.Colors.with_opacity(0.14, color),
        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        shape=ft.RoundedRectangleBorder(radius=6),
    )


def _today_pill_style() -> ft.ButtonStyle:
    return ft.ButtonStyle(
        color=C.ACCENT,
        bgcolor=ft.Colors.with_opacity(0.10, C.ACCENT),
        overlay_color=ft.Colors.with_opacity(0.18, C.ACCENT),
        padding=ft.Padding.symmetric(horizontal=10, vertical=0),
        shape=ft.RoundedRectangleBorder(radius=10),
        side=ft.BorderSide(1, ft.Colors.with_opacity(0.28, C.ACCENT)),
        text_style=ft.TextStyle(size=12, weight=ft.FontWeight.W_600),
    )


def _week_picker_button_style() -> ft.ButtonStyle:
    return ft.ButtonStyle(
        color=C.TEXT_PRIMARY,
        bgcolor=ft.Colors.TRANSPARENT,
        overlay_color=ft.Colors.with_opacity(0.10, C.ACCENT),
        padding=ft.Padding.symmetric(horizontal=6, vertical=0),
        shape=ft.RoundedRectangleBorder(radius=8),
    )


def _extract_inline_https_urls(*texts: str) -> list[str]:
    found: list[str] = []
    for raw in texts:
        if not raw:
            continue
        for match in _HTTPS_URL_RE.findall(raw):
            candidate = match.rstrip(".,;:!?)]}")
            validated = validate_https_url(candidate)
            if validated and validated not in found:
                found.append(validated)
    return found


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


_PAREN_GROUP_RE = re.compile(r"\s*\([^)]*\)")
_UNCLOSED_PAREN_RE = re.compile(r"\s*\([^)]*$")
_SPACED_DASH_RE = re.compile(r"\s+[-–—]\s+")
_CO_SO_NUM_RE = re.compile(r"^cơ\s+sở\s*(\d+)$", re.IGNORECASE)


def classify_session_shift(
    session: ClassSession,
) -> Literal["morning", "afternoon", "evening"]:
    """Classify a class session into morning (<12:00), afternoon (12:00..17:29), or evening (>=17:30)."""
    hm = (session.start_at.hour, session.start_at.minute)
    if hm < (12, 0):
        return "morning"
    if hm < (17, 30):
        return "afternoon"
    return "evening"


def _strip_parenthetical_address(raw: str) -> str:
    if not raw:
        return ""
    cleaned = _PAREN_GROUP_RE.sub("", raw)
    cleaned = _UNCLOSED_PAREN_RE.sub("", cleaned)
    cleaned = _SPACED_DASH_RE.sub(" · ", cleaned)
    return " ".join(cleaned.split()).strip(" ·-–—")


def compact_room_badge_text(session: ClassSession) -> str:
    """Shorten verbose Portal room/campus text for compact timeline rows."""
    room_raw = (session.room or "").strip()
    campus_raw = (session.campus or "").strip()
    haystack = f"{room_raw} {campus_raw}".casefold()
    if any(kw in haystack for kw in _ONLINE_KEYWORDS):
        return "E-Learning"
    cleaned_room = _strip_parenthetical_address(room_raw)
    cleaned_campus = _strip_parenthetical_address(campus_raw)
    if cleaned_campus:
        match = _CO_SO_NUM_RE.match(cleaned_campus)
        if match:
            cleaned_campus = f"CS{match.group(1)}"
    if cleaned_room:
        folded_room = cleaned_room.casefold()
        if "·" in cleaned_room or "cs" in folded_room or "cơ sở" in folded_room:
            return cleaned_room
        if cleaned_campus and cleaned_campus.casefold() not in folded_room:
            return f"{cleaned_room} · {cleaned_campus}"
        return cleaned_room
    if cleaned_campus:
        return cleaned_campus
    return "Chưa cập nhật phòng"


class ScheduleWeekView(ft.Container):
    def __init__(
        self,
        page: ft.Page,
        fetch_week: Optional[FetchWeek] = None,
        cache: Optional[WeekScheduleCache] = None,
        on_open_url: Optional[OpenUrlCallback] = None,
        on_open_external: Optional[OpenExternalCallback] = None,
        on_weeks_changed: Optional[Callable[[], None]] = None,
        on_maintenance: Optional[Callable[[Any, bool], None]] = None,
    ) -> None:
        super().__init__(expand=True, visible=False)
        self._page_ref = page
        self._fetch_week = fetch_week
        self._cache = cache
        self._on_open_url = on_open_url
        self._on_open_external = on_open_external
        self.on_weeks_changed: Callable[[], None] | None = on_weeks_changed
        self.on_maintenance: Callable[[Any, bool], None] | None = on_maintenance
        self._cache_lock = threading.Lock()
        self._layout_width = self._initial_layout_width()
        self.on_size_change = self._on_size_change
        self._selected: date = date.today()
        self._weeks: dict[date, dict[date, tuple[ClassSession, ...]]] = {}
        self._live_at: dict[date, datetime] = {}   # weeks fetched live (carry links)
        self._errors: dict[date, str] = {}
        self._loading: set[date] = set()
        self._forced: set[date] = set()            # manual reloads show a spinner
        self._hydrated = False
        self._prefetching = False
        self._prefetch_generation: int | None = None
        self._prefetch_task = None
        self._ensure_weeks_task: Any = None
        self._ensure_weeks_seq = 0
        self._ensure_weeks_targets: tuple[date, ...] = ()
        self._ensure_weeks_debouncing = False
        self._auth_failed = False
        self._maintenance_active = False
        self._maintenance_weeks: set[date] = set()
        self._generation = 0                       # bumped by reset()

        self._week_label = ft.Text(
            "",
            size=13,
            weight=ft.FontWeight.W_600,
            color=C.TEXT_PRIMARY,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            text_align=ft.TextAlign.CENTER,
        )
        nav_btn_constraints = ft.BoxConstraints(
            min_width=44, max_width=44, min_height=44, max_height=44
        )
        self._prev_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_LEFT_ROUNDED,
            icon_color=C.TEXT_PRIMARY,
            icon_size=20,
            tooltip="Tuần trước",
            padding=0,
            size_constraints=nav_btn_constraints,
            on_click=lambda _: self._navigate(-1),
        )
        self._prev_semantics = ft.Semantics(
            label="Tuần trước",
            button=True,
            content=self._prev_btn,
        )
        self._next_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_RIGHT_ROUNDED,
            icon_color=C.TEXT_PRIMARY,
            icon_size=20,
            tooltip="Tuần sau",
            padding=0,
            size_constraints=nav_btn_constraints,
            on_click=lambda _: self._navigate(1),
        )
        self._next_semantics = ft.Semantics(
            label="Tuần sau",
            button=True,
            content=self._next_btn,
        )
        self._picker_chevron_icon = ft.Icon(
            ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED,
            size=16,
            color=C.TEXT_SECONDARY,
        )
        self._week_label_box = ft.TextButton(
            content=ft.Row(
                [self._week_label, self._picker_chevron_icon],
                spacing=2,
                tight=True,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            height=44,
            tooltip="Chọn ngày trong tháng",
            style=_week_picker_button_style(),
            on_click=lambda _e: self._show_month_date_picker(
                return_focus=self._week_label_box
            ),
        )
        self._week_label_semantics = ft.Semantics(
            label="Chọn ngày trong tháng",
            button=True,
            content=self._week_label_box,
        )
        self._stepper_box = ft.Container(
            content=ft.Row(
                [
                    self._prev_semantics,
                    self._week_label_semantics,
                    self._next_semantics,
                ],
                spacing=0,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
        )
        self._today_btn = ft.TextButton(
            "Hôm nay",
            width=88,
            height=44,
            tooltip="Về hôm nay",
            style=_today_pill_style(),
            on_click=lambda _: self._go(date.today()),
        )
        self._refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=18,
            tooltip="Tải lại lịch học",
            padding=0,
            size_constraints=nav_btn_constraints,
            on_click=lambda _: self._reload(),
        )
        self._refresh_semantics = ft.Semantics(
            label="Tải lại lịch học",
            button=True,
            content=self._refresh_btn,
        )
        self._refresh_action_box = ft.Container(
            content=self._refresh_semantics,
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
        )
        self._nav_row = ft.Row(
            [
                ft.Row(
                    [self._stepper_box, self._today_btn],
                    spacing=8,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self._refresh_action_box,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        )

        self._status = ft.Row(spacing=8, visible=False)
        self._body = ft.Column(spacing=6, expand=True, scroll=ft.ScrollMode.AUTO)

        column = ft.Column(
            [
                ft.Container(
                    self._nav_row,
                    padding=ft.Padding.only(left=14, right=14, top=4, bottom=4),
                ),
                ft.Container(
                    self._status,
                    padding=ft.Padding.symmetric(horizontal=16, vertical=2),
                ),
                ft.Container(
                    self._body,
                    padding=ft.Padding.only(left=14, right=14, bottom=8, top=4),
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
        )
        # Swipe left/right = next/previous day on narrow screens, week on desktop.
        self.content = ft.GestureDetector(
            content=column,
            on_horizontal_drag_end=self._on_swipe,
            expand=True,
        )
        self._render()

    def _on_swipe(self, event) -> None:
        velocity = getattr(event, "primary_velocity", None)
        if velocity is None:
            velocity = getattr(getattr(event, "velocity", None), "x", 0.0) or 0.0
        if velocity <= -_SWIPE_VELOCITY:
            self._navigate(1)
        elif velocity >= _SWIPE_VELOCITY:
            self._navigate(-1)

    def _initial_layout_width(self) -> float:
        for source in (
            getattr(self._page_ref, "width", None),
            getattr(getattr(self._page_ref, "window", None), "width", None),
        ):
            try:
                width = float(source)
            except (TypeError, ValueError):
                continue
            if width > 0:
                if not platform_utils.UI_IS_MOBILE and width >= _WIDE_SHELL_BREAKPOINT:
                    return max(0.0, width - _RAIL_WIDTH)
                return width
        return 1120.0 - _RAIL_WIDTH

    def _is_narrow_layout(self) -> bool:
        return self._layout_width < _NARROW_BREAKPOINT

    def _on_size_change(self, event=None) -> None:
        try:
            width = float(getattr(event, "width", 0) or 0)
        except (TypeError, ValueError):
            return
        if width <= 0:
            return
        previous_width = self._layout_width
        self._layout_width = width
        crossed_breakpoint = (
            previous_width < _NARROW_BREAKPOINT
        ) != (width < _NARROW_BREAKPOINT)
        shrunk_in_wide = (width < previous_width) and (width >= _NARROW_BREAKPOINT)
        if self.visible and (
            crossed_breakpoint
            or shrunk_in_wide
            or abs(width - previous_width) >= 8
        ):
            self._render()

    # ------------------------------------------------------------ public API
    def show(self) -> None:
        """Called when the tab becomes visible: anchor to this week, render the
        cached weeks right away and refresh stale ones in the background."""
        self.visible = True
        self._selected = date.today()
        self._hydrate()
        self._render()
        self._ensure_loaded(_monday(self._selected))
        self._start_prefetch()

    def hide(self) -> None:
        self.visible = False

    def reset(self) -> None:
        """Forget everything in memory (e.g. another account logged in).

        The caller switches the cache namespace first.
        """
        self._generation += 1
        self._ensure_weeks_seq += 1
        for task_attr in ("_prefetch_task", "_ensure_weeks_task"):
            task = getattr(self, task_attr, None)
            setattr(self, task_attr, None)
            if task is not None and not getattr(task, "done", lambda: True)():
                try:
                    task.cancel()
                except Exception:
                    pass
        self._prefetching = False
        self._prefetch_generation = None
        self._ensure_weeks_targets = ()
        self._ensure_weeks_debouncing = False
        self._weeks.clear()
        self._live_at.clear()
        self._errors.clear()
        self._loading.clear()
        self._forced.clear()
        self._hydrated = False
        self._auth_failed = False
        self._maintenance_active = False
        self._maintenance_weeks.clear()
        self._notify_weeks_changed()
        if self.visible:
            self.show()

    def sessions_for_day(self, day: date) -> tuple[ClassSession, ...]:
        """Return cached/loaded class sessions for ``day``."""
        return self._sessions_on(day)

    def sessions_for_week(
        self, monday: date
    ) -> dict[date, tuple[ClassSession, ...]]:
        """Return cached/loaded day->sessions mapping for the week of ``monday``."""
        target = _monday(monday)
        return dict(self._weeks.get(target, {}))

    def week_status(self, monday: date) -> tuple[bool, str]:
        """Return ``(is_loading_spinner, error_message)`` for the week of ``monday``."""
        target = _monday(monday)
        spinner = target in self._loading and (
            target not in self._weeks or target in self._forced
        )
        return spinner, self._errors.get(target, "")

    def reload_week(self, monday: date) -> None:
        """Force-reload the timetable for the week containing ``monday``."""
        target = _monday(monday)
        self._errors.pop(target, None)
        self._maintenance_weeks.discard(target)
        self._auth_failed = False
        self._maintenance_active = False
        if self._fetch_week is None:
            self._notify_weeks_changed()
            return
        self._ensure_loaded(target, force=True)

    def open_session_details(
        self, session: ClassSession, *, return_focus: ft.Control | None = None
    ) -> None:
        """Open the standard class session detail dialog."""
        self._show_session_details(session, return_focus=return_focus)

    def hydrate_and_ensure_weeks(
        self,
        mondays: Sequence[date],
        *,
        start_prefetch: bool = False,
        debounce_ms: int = 0,
    ) -> None:
        """Hydrate disk cache in 0ms and ensure ``mondays`` are fresh with optional debounce."""
        normalized: list[date] = []
        for item in mondays:
            m = _monday(item)
            if m not in normalized:
                normalized.append(m)
        target_tuple = tuple(normalized)

        self._hydrate()

        prev_task = self._ensure_weeks_task
        prev_running = prev_task is not None and not getattr(
            prev_task, "done", lambda: True
        )()

        if (
            self._fetch_week is None
            or self._auth_failed
            or self._maintenance_active
        ):
            if prev_running:
                self._ensure_weeks_seq += 1
                self._ensure_weeks_task = None
                self._ensure_weeks_targets = ()
                self._ensure_weeks_debouncing = False
                try:
                    prev_task.cancel()  # type: ignore[union-attr]
                except Exception:
                    pass
            return

        if start_prefetch:
            self._start_prefetch()

        if (
            prev_running
            and self._ensure_weeks_targets == target_tuple
            and (not self._ensure_weeks_debouncing or debounce_ms > 0)
        ):
            return

        cancelled_previous = False
        if prev_running:
            cancelled_previous = True
            self._ensure_weeks_task = None
            try:
                prev_task.cancel()  # type: ignore[union-attr]
            except Exception:
                pass

        unfresh = [m for m in target_tuple if not self._is_fresh(m)]
        if not unfresh:
            self._ensure_weeks_targets = ()
            self._ensure_weeks_debouncing = False
            return

        self._ensure_weeks_seq += 1
        seq = self._ensure_weeks_seq
        generation = self._generation
        self._ensure_weeks_targets = target_tuple
        self._ensure_weeks_debouncing = debounce_ms > 0

        premarked: set[date] = set()
        if debounce_ms <= 0:
            first_unloaded = next(
                (m for m in unfresh if m not in self._loading), None
            )
            if first_unloaded is not None:
                self._loading.add(first_unloaded)
                self._errors.pop(first_unloaded, None)
                premarked.add(first_unloaded)

        async def _runner() -> None:
            try:
                if debounce_ms > 0:
                    await asyncio.sleep(debounce_ms / 1000.0)
                elif cancelled_previous:
                    await asyncio.sleep(0)
                if seq != self._ensure_weeks_seq or generation != self._generation:
                    return
                self._ensure_weeks_debouncing = False
                for m in target_tuple:
                    if (
                        seq != self._ensure_weeks_seq
                        or generation != self._generation
                        or self._auth_failed
                        or self._maintenance_active
                    ):
                        break
                    if m in premarked:
                        premarked.discard(m)
                    else:
                        if m in self._loading or self._is_fresh(m):
                            continue
                        self._loading.add(m)
                        self._errors.pop(m, None)
                        self._notify_weeks_changed()
                    await self._load_async(m)
            except asyncio.CancelledError:
                return
            finally:
                for leftover in list(premarked):
                    self._loading.discard(leftover)
                if self._ensure_weeks_seq == seq:
                    self._ensure_weeks_task = None
                    self._ensure_weeks_targets = ()
                    self._ensure_weeks_debouncing = False

        try:
            self._ensure_weeks_task = self._page_ref.run_task(_runner)
        except (AttributeError, RuntimeError, TypeError):
            for leftover in list(premarked):
                self._loading.discard(leftover)
            self._ensure_weeks_task = None
            self._ensure_weeks_targets = ()
            self._ensure_weeks_debouncing = False
            logger.debug("Could not schedule ensure_weeks task", exc_info=True)

    def update_theme(self) -> None:
        self.bgcolor = C.BG
        self._week_label.color = C.TEXT_PRIMARY
        self._picker_chevron_icon.color = C.TEXT_SECONDARY
        self._week_label_box.style = _week_picker_button_style()
        self._prev_btn.icon_color = C.TEXT_PRIMARY
        self._next_btn.icon_color = C.TEXT_PRIMARY
        self._stepper_box.bgcolor = C.SURFACE
        self._stepper_box.border = ft.Border.all(1, C.BORDER)
        self._today_btn.style = _today_pill_style()
        self._refresh_btn.icon_color = C.TEXT_SECONDARY
        self._refresh_action_box.bgcolor = C.SURFACE
        self._refresh_action_box.border = ft.Border.all(1, C.BORDER)
        self._render()

    # ------------------------------------------------------------ navigation
    def _shift(self, days: int) -> None:
        self._go(self._selected + timedelta(days=days))

    def _navigate(self, direction: int) -> None:
        days = 1 if self._is_narrow_layout() else 7
        self._shift(direction * days)

    def _go(self, target: date) -> None:
        self._selected = target
        self._render()
        self._ensure_loaded(_monday(target))

    def _show_month_date_picker(
        self, *, return_focus: ft.Control | None = None
    ) -> None:
        """Open a Monday-first month calendar dialog to jump directly to any date."""
        self._hydrate()
        width = max(280.0, min(344.0, self._layout_width - 32))
        today = date.today()
        selected_monday = _monday(self._selected)
        selected_sunday = selected_monday + timedelta(days=6)
        picker_state = {
            "year": self._selected.year,
            "month": self._selected.month,
        }
        skip_focus_restore = {"skip": False}

        def _on_key_down(event) -> None:
            if getattr(event, "key", "") == "Escape":
                self._safe_pop_dialog()

        async def _restore_focus(_event=None) -> None:
            if not skip_focus_restore["skip"]:
                await self._restore_control_focus(return_focus)

        def _pick_date(target: date) -> None:
            self._safe_pop_dialog()
            self._go(target)

        month_title = ft.Text(
            "",
            size=14,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY,
            text_align=ft.TextAlign.CENTER,
            expand=True,
        )
        grid_col = ft.Column(spacing=4, tight=True)

        def _rebuild_grid() -> None:
            year = picker_state["year"]
            month = picker_state["month"]
            month_title.value = f"Tháng {month}, {year}"
            weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(
                year, month
            )
            rows: list[ft.Control] = []
            for week in weeks:
                cells: list[ft.Control] = []
                for col_idx, cell_date in enumerate(week):
                    in_month = cell_date.month == month
                    is_selected = cell_date == self._selected
                    is_today = cell_date == today
                    in_selected_week = (
                        selected_monday <= cell_date <= selected_sunday
                    )
                    has_classes = bool(self._sessions_on(cell_date))

                    if is_selected:
                        bg = C.ACCENT
                        border = None
                        fg = C.ON_ACCENT
                        weight = ft.FontWeight.W_700
                    else:
                        bg = (
                            ft.Colors.with_opacity(0.11, C.ACCENT)
                            if in_selected_week
                            else None
                        )
                        border = (
                            ft.Border.all(1.5, C.ACCENT) if is_today else None
                        )
                        if not in_month:
                            fg = ft.Colors.with_opacity(0.38, C.TEXT_SECONDARY)
                            weight = ft.FontWeight.W_400
                        elif is_today:
                            fg = C.ACCENT
                            weight = ft.FontWeight.W_700
                        elif col_idx == 6:
                            fg = C.CRITICAL
                            weight = (
                                ft.FontWeight.W_600
                                if in_selected_week
                                else ft.FontWeight.W_500
                            )
                        else:
                            fg = C.TEXT_PRIMARY
                            weight = (
                                ft.FontWeight.W_600
                                if in_selected_week
                                else ft.FontWeight.W_500
                            )

                    dot_color = (
                        C.ON_ACCENT
                        if is_selected
                        else (
                            ft.Colors.with_opacity(0.45, C.ACCENT)
                            if not in_month
                            else C.ACCENT
                        )
                    )
                    cells.append(
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Text(
                                        str(cell_date.day),
                                        size=12,
                                        weight=weight,
                                        color=fg,
                                        text_align=ft.TextAlign.CENTER,
                                    ),
                                    ft.Container(
                                        width=4,
                                        height=4,
                                        border_radius=2,
                                        bgcolor=dot_color if has_classes else None,
                                    ),
                                ],
                                spacing=2,
                                tight=True,
                                alignment=ft.MainAxisAlignment.CENTER,
                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            expand=True,
                            height=40,
                            bgcolor=bg,
                            border=border,
                            border_radius=8,
                            alignment=ft.Alignment(0, 0),
                            ink=True,
                            data={
                                "picker_date": cell_date.isoformat(),
                                "in_month": in_month,
                                "is_selected": is_selected,
                                "is_today": is_today,
                                "in_selected_week": in_selected_week,
                                "has_classes": has_classes,
                            },
                            on_click=lambda _e, d=cell_date: _pick_date(d),
                        )
                    )
                rows.append(ft.Row(cells, spacing=4))
            grid_col.controls = rows

        def _shift_picker_month(delta: int) -> None:
            month = picker_state["month"] + delta
            year = picker_state["year"]
            if month < 1:
                month = 12
                year -= 1
            elif month > 12:
                month = 1
                year += 1
            picker_state["year"] = year
            picker_state["month"] = month
            _rebuild_grid()
            for ctrl in (month_title, grid_col):
                try:
                    ctrl.update()
                except Exception:
                    pass

        _rebuild_grid()

        btn_constraints = ft.BoxConstraints(
            min_width=44, max_width=44, min_height=44, max_height=44
        )
        header_row = ft.Row(
            [
                ft.Text(
                    "CHỌN NGÀY TRONG THÁNG",
                    size=11,
                    weight=ft.FontWeight.W_700,
                    color=C.TEXT_SECONDARY,
                    expand=True,
                ),
                ft.Semantics(
                    label="Đóng",
                    button=True,
                    content=ft.IconButton(
                        icon=ft.Icons.CLOSE_ROUNDED,
                        icon_color=C.TEXT_SECONDARY,
                        icon_size=20,
                        tooltip="Đóng",
                        padding=0,
                        size_constraints=btn_constraints,
                        on_click=lambda _e: self._safe_pop_dialog(),
                    ),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        month_nav = ft.Container(
            content=ft.Row(
                [
                    ft.Semantics(
                        label="Tháng trước",
                        button=True,
                        content=ft.IconButton(
                            icon=ft.Icons.CHEVRON_LEFT_ROUNDED,
                            icon_color=C.TEXT_PRIMARY,
                            icon_size=20,
                            tooltip="Tháng trước",
                            padding=0,
                            size_constraints=btn_constraints,
                            on_click=lambda _e: _shift_picker_month(-1),
                        ),
                    ),
                    month_title,
                    ft.Semantics(
                        label="Tháng sau",
                        button=True,
                        content=ft.IconButton(
                            icon=ft.Icons.CHEVRON_RIGHT_ROUNDED,
                            icon_color=C.TEXT_PRIMARY,
                            icon_size=20,
                            tooltip="Tháng sau",
                            padding=0,
                            size_constraints=btn_constraints,
                            on_click=lambda _e: _shift_picker_month(1),
                        ),
                    ),
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
        )
        weekday_header = ft.Row(
            [
                ft.Container(
                    content=ft.Text(
                        wd,
                        size=11,
                        weight=ft.FontWeight.W_600,
                        color=C.CRITICAL if wd == "CN" else C.TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    expand=True,
                    alignment=ft.Alignment(0, 0),
                )
                for wd in _PICKER_WEEKDAYS
            ],
            spacing=4,
        )
        footer_row = ft.Row(
            [
                ft.TextButton(
                    "Hôm nay",
                    height=40,
                    tooltip="Về hôm nay",
                    style=_today_pill_style(),
                    on_click=lambda _e: _pick_date(date.today()),
                ),
                ft.TextButton(
                    "Đóng",
                    height=40,
                    style=_text_action_style(C.TEXT_SECONDARY),
                    on_click=lambda _e: self._safe_pop_dialog(),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        dialog = ft.AlertDialog(
            modal=True,
            scrollable=True,
            semantics_label="Chọn ngày trong tháng",
            content_padding=0,
            inset_padding=ft.Padding.symmetric(horizontal=16, vertical=24),
            content=ft.KeyboardListener(
                autofocus=True,
                on_key_down=_on_key_down,
                content=ft.Container(
                    width=width,
                    padding=ft.Padding.only(left=14, right=14, top=8, bottom=12),
                    content=ft.Column(
                        [
                            header_row,
                            month_nav,
                            weekday_header,
                            grid_col,
                            ft.Divider(height=6, color=C.BORDER),
                            footer_row,
                        ],
                        spacing=8,
                        tight=True,
                    ),
                ),
            ),
            bgcolor=C.BG,
            shape=ft.RoundedRectangleBorder(radius=16),
            on_dismiss=_restore_focus,
        )
        try:
            self._page_ref.show_dialog(dialog)
        except Exception:
            logger.debug("Could not show month date picker", exc_info=True)

    def _reload(self) -> None:
        monday = _monday(self._selected)
        self._errors.pop(monday, None)
        self._maintenance_weeks.discard(monday)
        self._auth_failed = False
        self._maintenance_active = False
        self._ensure_loaded(monday, force=True)

    def _notify_weeks_changed(self) -> None:
        cb = self.on_weeks_changed
        if callable(cb):
            try:
                cb()
            except Exception:
                logger.debug("on_weeks_changed callback failed", exc_info=True)

    def _notify_maintenance(self, notice: Any, forced: bool) -> None:
        cb = self.on_maintenance
        if not callable(cb):
            return
        try:
            sig = inspect.signature(cb)
            params = list(sig.parameters.values())
            positional = [
                p
                for p in params
                if p.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            has_varargs = any(
                p.kind == inspect.Parameter.VAR_POSITIONAL for p in params
            )
            has_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params
            )
            kw_names = {
                p.name
                for p in params
                if p.kind
                in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
            }
            if len(positional) >= 2 or has_varargs:
                cb(notice, forced)
            elif "user_initiated" in kw_names:
                cb(notice, user_initiated=forced)
            elif "force" in kw_names:
                cb(notice, force=forced)
            elif has_kwargs:
                cb(notice, user_initiated=forced)
            else:
                cb(notice)
        except Exception:
            logger.debug("on_maintenance callback failed", exc_info=True)

    @staticmethod
    def _is_maintenance_exception(exc: BaseException) -> bool:
        if getattr(exc, "notice", None) is not None:
            return True
        return any(
            cls.__name__ in {"UthMaintenanceError", "PortalMaintenanceError"}
            for cls in type(exc).__mro__
        )

    @staticmethod
    def _format_maintenance_error(exc: BaseException) -> str:
        notice = getattr(exc, "notice", None)
        window_text = str(getattr(notice, "window_text", "") or "").strip()
        reopen_text = str(getattr(notice, "reopen_text", "") or "").strip()
        if window_text and reopen_text:
            return f"Hệ thống UTH đang bảo trì ({window_text} · Mở lại: {reopen_text})."
        if window_text:
            return f"Hệ thống UTH đang bảo trì ({window_text})."
        if reopen_text:
            return f"Hệ thống UTH đang bảo trì (Mở lại: {reopen_text})."
        msg = str(exc).strip()
        if msg and "bảo trì" in msg.casefold():
            return msg
        return "Hệ thống UTH đang tạm ngưng bảo trì."

    # ----------------------------------------------------------------- cache
    def _hydrate(self) -> None:
        """Load the ±4 week window from disk once (instant, no network)."""
        if self._hydrated or self._cache is None:
            return
        self._hydrated = True
        try:
            with self._cache_lock:
                entries = self._cache.load_window()
        except Exception:
            logger.debug("Could not read week schedule cache", exc_info=True)
            return
        added = False
        for monday, entry in entries.items():
            if monday not in self._weeks:
                self._weeks[monday] = entry.days
                added = True
        if added:
            self._notify_weeks_changed()

    def _save_cache(
        self,
        monday: date,
        data: dict[date, tuple[ClassSession, ...]],
        fetched_at: datetime,
    ) -> None:
        if self._cache is None:
            return
        try:
            with self._cache_lock:
                self._cache.save_week(monday, data, fetched_at)
        except Exception:
            logger.debug("Could not write week schedule cache", exc_info=True)

    def _is_fresh(self, monday: date) -> bool:
        return not self._needs_fetch(monday)

    def _needs_fetch(self, monday: date) -> bool:
        if monday not in self._weeks:
            return True
        live = self._live_at.get(monday)
        if live is None:  # only known from disk: refresh once to restore links
            return True
        ttl = _TTL_CURRENT if monday == _monday(date.today()) else _TTL_OTHER
        return datetime.now() - live > ttl

    # --------------------------------------------------------------- loading
    def _ensure_loaded(self, monday: date, *, force: bool = False) -> None:
        if monday in self._loading:
            if force:
                self._forced.add(monday)
                self._notify_weeks_changed()
                self._render()
            return
        if not force and not self._needs_fetch(monday):
            return
        if self._fetch_week is None:
            if monday not in self._weeks:
                self._errors[monday] = "Chưa cấu hình nguồn lịch học."
                self._notify_weeks_changed()
                self._render()
            return
        self._loading.add(monday)
        if force:
            self._forced.add(monday)
        self._errors.pop(monday, None)
        self._notify_weeks_changed()
        self._render()
        try:
            self._page_ref.run_task(self._load_async, monday)
        except (AttributeError, RuntimeError, TypeError):
            self._loading.discard(monday)
            self._forced.discard(monday)
            logger.debug("Could not schedule timetable load", exc_info=True)

    def _start_prefetch(self) -> None:
        if self._fetch_week is None or self._auth_failed or self._maintenance_active:
            return
        if self._prefetching and self._prefetch_generation == self._generation:
            return
        generation = self._generation
        self._prefetching = True
        self._prefetch_generation = generation
        try:
            self._prefetch_task = self._page_ref.run_task(
                self._prefetch_async, generation
            )
        except (AttributeError, RuntimeError, TypeError):
            if self._prefetch_generation == generation:
                self._prefetching = False
                self._prefetch_generation = None
                self._prefetch_task = None
            logger.debug("Could not schedule timetable prefetch", exc_info=True)

    async def _prefetch_async(self, generation: int | None = None) -> None:
        """Keep the current week and 4 weeks each side warm, nearest first."""
        if generation is None:
            generation = self._generation
        try:
            for monday in WeekScheduleCache.window():
                if (
                    generation != self._generation
                    or self._auth_failed
                    or self._maintenance_active
                ):
                    break
                if monday in self._loading or not self._needs_fetch(monday):
                    continue
                self._loading.add(monday)
                try:
                    await self._load_async(monday)
                except asyncio.CancelledError:
                    if generation == self._generation:
                        self._loading.discard(monday)
                    raise
                if (
                    generation != self._generation
                    or self._auth_failed
                    or self._maintenance_active
                ):
                    break
                await asyncio.sleep(_PREFETCH_GAP_S)
                if (
                    generation != self._generation
                    or self._auth_failed
                    or self._maintenance_active
                ):
                    break
        finally:
            if self._prefetch_generation in (None, generation):
                self._prefetching = False
                self._prefetch_generation = None
                self._prefetch_task = None

    async def _load_async(self, monday: date) -> None:
        generation = self._generation
        error = ""
        auth_error = False
        maintenance_exc: BaseException | None = None
        data: dict[date, tuple[ClassSession, ...]] | None = None
        try:
            data = await asyncio.to_thread(self._fetch_week, monday)  # type: ignore[arg-type]
        except asyncio.CancelledError:
            if generation == self._generation:
                self._loading.discard(monday)
                self._forced.discard(monday)
                self._notify_weeks_changed()
                if monday == _monday(self._selected):
                    self._render()
            raise
        except Exception as exc:
            if self._is_maintenance_exception(exc):
                maintenance_exc = exc
                error = self._format_maintenance_error(exc)
            elif isinstance(exc, ScheduleAuthenticationError):
                auth_error = True
                error = "Chưa đăng nhập Portal. Vào Cài đặt để nhập MSSV và mật khẩu."
            else:
                logger.warning("Portal week schedule request failed", exc_info=True)
                error = "Không tải được lịch học. Kiểm tra kết nối rồi thử lại."
        if generation != self._generation:
            return  # account changed while fetching: drop the stale result
        self._loading.discard(monday)
        forced = monday in self._forced
        self._forced.discard(monday)
        if maintenance_exc is not None:
            self._maintenance_active = True
            self._maintenance_weeks.add(monday)
            self._errors[monday] = error
            notice_payload = getattr(maintenance_exc, "notice", None)
            if notice_payload is None:
                notice_payload = maintenance_exc
            self._notify_maintenance(notice_payload, forced)
        elif auth_error:
            self._auth_failed = True
            self._weeks.clear()
            self._live_at.clear()
            self._maintenance_weeks.clear()
            self._errors[monday] = error
        elif data is not None:
            self._maintenance_active = False
            self._maintenance_weeks.discard(monday)
            fetched_at = datetime.now()
            self._weeks[monday] = data
            self._live_at[monday] = fetched_at
            self._errors.pop(monday, None)
            try:
                await asyncio.to_thread(self._save_cache, monday, data, fetched_at)
            except asyncio.CancelledError:
                if generation == self._generation:
                    self._notify_weeks_changed()
                    if monday == _monday(self._selected):
                        self._render()
                raise
        elif monday not in self._weeks or forced:
            # Cached data stays on screen; only surface the error when there
            # is nothing to show or the person asked for a reload.
            self._maintenance_weeks.discard(monday)
            self._errors[monday] = error
        self._notify_weeks_changed()
        if (
            monday == _monday(self._selected)
            or auth_error
            or maintenance_exc is not None
        ):
            self._render()

    # ---------------------------------------------------------------- render
    def _sessions_on(self, day: date) -> tuple[ClassSession, ...]:
        return self._weeks.get(_monday(day), {}).get(day, ())

    def _render(self) -> None:
        monday = _monday(self._selected)
        sunday = monday + timedelta(days=6)
        narrow = self._is_narrow_layout()
        if narrow:
            self._week_label.value = (
                f"{_WEEKDAYS_LONG[self._selected.weekday()]}, "
                f"{self._selected:%d/%m/%Y}"
            )
            self._week_label.size = 13
            self._prev_btn.tooltip = "Ngày trước"
            self._prev_semantics.label = "Ngày trước"
            self._next_btn.tooltip = "Ngày sau"
            self._next_semantics.label = "Ngày sau"
        else:
            self._week_label.value = f"{monday:%d/%m} – {sunday:%d/%m/%Y}"
            self._week_label.size = 14
            self._prev_btn.tooltip = "Tuần trước"
            self._prev_semantics.label = "Tuần trước"
            self._next_btn.tooltip = "Tuần sau"
            self._next_semantics.label = "Tuần sau"
        self._render_status(monday)
        if monday in self._weeks:
            if narrow:
                self._render_day_body()
            else:
                self._render_week_body(monday)
        else:
            self._body.controls = []
        try:
            self.update()
        except Exception:
            logger.debug("Schedule view update skipped", exc_info=True)

    def _render_status(self, monday: date) -> None:
        controls: list[ft.Control] = []
        spinner = monday in self._loading and (
            monday not in self._weeks or monday in self._forced
        )
        if spinner:
            controls = [
                ft.ProgressRing(width=14, height=14, stroke_width=2, color=C.ACCENT),
                ft.Text("Đang tải lịch học…", size=11, color=C.TEXT_SECONDARY),
            ]
        elif monday in self._errors:
            is_maint = monday in self._maintenance_weeks
            icon_tone = C.WARNING if is_maint else C.CRITICAL
            text_tone = C.TEXT_PRIMARY if is_maint else C.CRITICAL
            icon = (
                ft.Icons.WARNING_AMBER_ROUNDED
                if is_maint
                else ft.Icons.ERROR_OUTLINE_ROUNDED
            )
            controls = [
                ft.Icon(icon, size=16, color=icon_tone),
                ft.Text(self._errors[monday], size=11, color=text_tone, expand=True),
                ft.TextButton(
                    "Thử lại",
                    height=44,
                    style=_text_action_style(C.ACCENT),
                    on_click=lambda _: self._reload(),
                ),
            ]
        self._status.controls = controls
        self._status.visible = bool(controls)

    def _render_day_body(self) -> None:
        sessions = self._sessions_on(self._selected)
        if not sessions:
            self._body.controls = [
                ft.Container(
                    content=ft.Text(
                        "Không có lịch học ngày này",
                        size=13,
                        color=C.TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    alignment=ft.Alignment(0, 0),
                    padding=ft.Padding.symmetric(vertical=28, horizontal=12),
                )
            ]
            return
        self._body.controls = [
            self._session_summary_card(session, narrow=True)
            for session in sessions
        ]

    # ----- session card & custom detail modal
    @staticmethod
    def _phase_badge(session: ClassSession, now: datetime) -> ft.Container:
        phase = session.phase_at(now)
        label, color = {
            SchedulePhase.UPCOMING: ("Sắp diễn ra", C.ACCENT),
            SchedulePhase.IN_PROGRESS: ("Đang học", C.SAFE),
            SchedulePhase.FINISHED: ("Đã kết thúc", C.TEXT_SECONDARY),
            SchedulePhase.CANCELLED: ("Tạm ngưng", C.CRITICAL),
        }[phase]
        return ft.Container(
            content=ft.Text(
                label,
                size=10,
                color=color,
                weight=ft.FontWeight.W_600,
            ),
            bgcolor=ft.Colors.with_opacity(0.12, color),
            border_radius=999,
            padding=ft.Padding.symmetric(horizontal=9, vertical=3),
        )

    @staticmethod
    def _detail_field_row(
        icon,
        label: str,
        primary_value: str,
        *,
        secondary_value: str = "",
        icon_color: str | None = None,
        value_color: str | None = None,
        extra_control: ft.Control | None = None,
    ) -> ft.Row:
        resolved_icon_color = icon_color or C.ACCENT
        text_items: list[ft.Control] = [
            ft.Text(
                label.upper(),
                size=10,
                color=C.TEXT_SECONDARY,
                weight=ft.FontWeight.W_600,
            ),
            ft.Text(
                primary_value,
                size=13,
                color=value_color or C.TEXT_PRIMARY,
                weight=ft.FontWeight.W_500,
                selectable=True,
            ),
        ]
        if secondary_value:
            text_items.append(
                ft.Text(
                    secondary_value,
                    size=11,
                    color=C.TEXT_SECONDARY,
                    selectable=True,
                )
            )
        if extra_control is not None:
            text_items.append(extra_control)
        return ft.Row(
            [
                ft.Container(
                    content=ft.Icon(icon, size=17, color=resolved_icon_color),
                    width=34,
                    height=34,
                    border_radius=9,
                    bgcolor=ft.Colors.with_opacity(0.11, resolved_icon_color),
                    alignment=ft.Alignment(0, 0),
                ),
                ft.Column(
                    text_items,
                    spacing=2,
                    tight=True,
                    expand=True,
                ),
            ],
            spacing=11,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    def _safe_pop_dialog(self) -> None:
        pop_fn = getattr(self._page_ref, "pop_dialog", None)
        if callable(pop_fn):
            try:
                pop_fn()
            except Exception:
                logger.debug("Could not pop active schedule dialog", exc_info=True)

    @staticmethod
    async def _restore_control_focus(control: Any) -> None:
        if (
            control is None
            or not getattr(control, "visible", True)
            or getattr(control, "disabled", False)
        ):
            return
        focus_fn = getattr(control, "focus", None)
        if not callable(focus_fn):
            return
        try:
            result = focus_fn()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("Focus restore skipped", exc_info=True)

    def _build_action_link_row(
        self,
        *,
        label: str,
        icon,
        url: str,
        title: str,
        filled: bool,
        require_uth_lms: bool = False,
        on_before_open: Callable[[], None] | None = None,
    ) -> ft.Row:
        def _handle_in_app(_e=None) -> None:
            if callable(on_before_open):
                on_before_open()
            self._open_url(url, title, require_uth_lms=require_uth_lms)

        def _handle_external(_e=None) -> None:
            self._open_external(url, require_uth_lms=require_uth_lms)

        fg_color = C.ON_ACCENT if filled else C.ACCENT
        bg_color = C.ACCENT if filled else ft.Colors.with_opacity(0.11, C.ACCENT)
        border = (
            None
            if filled
            else ft.Border.all(1, ft.Colors.with_opacity(0.28, C.ACCENT))
        )

        primary_btn = ft.Container(
            content=ft.Semantics(
                label=label,
                button=True,
                content=ft.Row(
                    [
                        ft.Icon(icon, size=17, color=fg_color),
                        ft.Text(
                            label,
                            size=13,
                            weight=ft.FontWeight.W_600,
                            color=fg_color,
                        ),
                    ],
                    spacing=8,
                    alignment=ft.MainAxisAlignment.CENTER,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
            height=44,
            expand=True,
            bgcolor=bg_color,
            border=border,
            border_radius=10,
            alignment=ft.Alignment(0, 0),
            ink=True,
            tooltip=f"{label} (mở trong ứng dụng)",
            on_click=_handle_in_app,
        )
        external_box = ft.Container(
            content=ft.Semantics(
                label=f"Mở {label} bằng trình duyệt ngoài",
                button=True,
                content=ft.IconButton(
                    icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                    icon_color=C.TEXT_SECONDARY,
                    icon_size=17,
                    tooltip=f"Mở {label} bằng trình duyệt ngoài",
                    padding=0,
                    size_constraints=ft.BoxConstraints(
                        min_width=44, max_width=44, min_height=44, max_height=44
                    ),
                    on_click=_handle_external,
                ),
            ),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
        )
        return ft.Row(
            [primary_btn, external_box],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _session_card(
        self,
        session: ClassSession,
        now: datetime,
        *,
        on_before_open_url: Callable[[], None] | None = None,
        on_open_outline: Callable[[], None] | None = None,
    ) -> ft.Control:
        code_chips: list[ft.Control] = []
        if session.course_code:
            code_chips.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.BADGE_OUTLINED, size=13, color=C.ACCENT),
                            ft.Text(
                                f"Mã lớp: {session.course_code}",
                                size=11,
                                weight=ft.FontWeight.W_600,
                                color=C.TEXT_PRIMARY,
                                selectable=True,
                            ),
                        ],
                        spacing=5,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=7,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                )
            )
        if session.subject_code:
            code_chips.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.TAG_ROUNDED, size=13, color=C.TEXT_SECONDARY),
                            ft.Text(
                                f"Mã HP: {session.subject_code}",
                                size=11,
                                weight=ft.FontWeight.W_500,
                                color=C.TEXT_SECONDARY,
                                selectable=True,
                            ),
                        ],
                        spacing=4,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=7,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                )
            )

        hero_items: list[ft.Control] = [
            ft.Text(
                session.subject,
                size=16,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
                selectable=True,
            ),
        ]
        if code_chips:
            hero_items.append(
                ft.Row(code_chips, spacing=6, run_spacing=6, wrap=True)
            )

        period_part = (
            f"  ·  Tiết {session.period_start}–{session.period_end}"
            if session.period_start is not None and session.period_end is not None
            else ""
        )
        time_primary = f"{session.start_at:%H:%M} – {session.end_at:%H:%M}{period_part}"
        time_secondary = (
            f"{_WEEKDAYS_LONG[session.start_at.weekday()]}, "
            f"{session.start_at:%d/%m/%Y}"
        )

        room_haystack = f"{session.room} {session.campus}".casefold()
        is_online_room = any(kw in room_haystack for kw in _ONLINE_KEYWORDS)
        room_icon = (
            ft.Icons.VIDEOCAM_OUTLINED
            if is_online_room
            else ft.Icons.MEETING_ROOM_OUTLINED
        )
        room_primary = session.room or "Chưa cập nhật phòng"
        room_secondary = f"Cơ sở: {session.campus}" if session.campus else ""

        lecturer_present = bool((session.lecturer or "").strip())
        lecturer_value = session.lecturer if lecturer_present else "Chưa cập nhật"

        info_rows: list[ft.Control] = [
            self._detail_field_row(
                ft.Icons.ACCESS_TIME_ROUNDED,
                "Thời gian",
                time_primary,
                secondary_value=time_secondary,
            ),
            ft.Divider(height=1, thickness=1, color=C.BORDER),
            self._detail_field_row(
                room_icon,
                "Phòng học & Cơ sở",
                room_primary,
                secondary_value=room_secondary,
            ),
            ft.Divider(height=1, thickness=1, color=C.BORDER),
            self._detail_field_row(
                ft.Icons.PERSON_OUTLINE_ROUNDED,
                "Giảng viên",
                lecturer_value,
                value_color=C.TEXT_PRIMARY if lecturer_present else C.TEXT_SECONDARY,
            ),
        ]
        if session.note:
            note_color = C.CRITICAL if session.cancelled else C.WARNING
            info_rows.extend(
                [
                    ft.Divider(height=1, thickness=1, color=C.BORDER),
                    self._detail_field_row(
                        ft.Icons.INFO_OUTLINE_ROUNDED,
                        "Ghi chú",
                        session.note,
                        icon_color=note_color,
                        value_color=C.CRITICAL if session.cancelled else C.TEXT_PRIMARY,
                    ),
                ]
            )

        info_card = ft.Container(
            content=ft.Column(info_rows, spacing=11, tight=True),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=12,
            padding=ft.Padding.all(14),
        )

        actions_col: list[ft.Control] = []
        if session.has_outline:
            def _trigger_outline(_e=None) -> None:
                if callable(on_open_outline):
                    on_open_outline()
                else:
                    self._show_outline(session)

            outline_card = ft.Container(
                content=ft.Semantics(
                    label="Đề cương chi tiết học phần",
                    button=True,
                    content=ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.AUTO_STORIES_ROUNDED,
                                size=17,
                                color=C.ACCENT,
                            ),
                            ft.Text(
                                "Đề cương chi tiết học phần",
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color=C.TEXT_PRIMARY,
                                expand=True,
                            ),
                            ft.Icon(
                                ft.Icons.CHEVRON_RIGHT_ROUNDED,
                                size=18,
                                color=C.TEXT_SECONDARY,
                            ),
                        ],
                        spacing=9,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
                height=44,
                padding=ft.Padding.symmetric(horizontal=12),
                bgcolor=C.SURFACE,
                border=ft.Border.all(1, C.BORDER),
                border_radius=10,
                ink=True,
                on_click=_trigger_outline,
            )
            actions_col.append(outline_card)

        known_urls = {
            u for u in (session.link, session.meeting_url) if u
        }
        for inline_url in _extract_inline_https_urls(session.room, session.note):
            if inline_url in known_urls:
                continue
            known_urls.add(inline_url)
            actions_col.append(
                self._build_action_link_row(
                    label="Mở liên kết buổi học",
                    icon=ft.Icons.LINK_ROUNDED,
                    url=inline_url,
                    title=session.subject,
                    filled=not bool(session.link or session.meeting_url),
                    require_uth_lms=False,
                    on_before_open=on_before_open_url,
                )
            )

        if session.meeting_url:
            actions_col.append(
                self._build_action_link_row(
                    label="Truy cập Zoom",
                    icon=ft.Icons.VIDEOCAM_OUTLINED,
                    url=session.meeting_url,
                    title=f"Zoom · {session.subject}",
                    filled=not bool(session.link),
                    require_uth_lms=False,
                    on_before_open=on_before_open_url,
                )
            )

        if session.link:
            actions_col.append(
                self._build_action_link_row(
                    label="Mở khóa học LMS",
                    icon=ft.Icons.SCHOOL_ROUNDED,
                    url=session.link,
                    title=f"LMS · {session.subject}",
                    filled=True,
                    require_uth_lms=True,
                    on_before_open=on_before_open_url,
                )
            )

        sections: list[ft.Control] = [
            ft.Column(hero_items, spacing=6, tight=True),
            info_card,
        ]
        if actions_col:
            sections.append(ft.Column(actions_col, spacing=8, tight=True))

        return ft.Column(sections, spacing=12, tight=True)

    def _session_summary_card(
        self, session: ClassSession, *, narrow: bool
    ) -> ft.Container:
        time_text = f"{session.start_at:%H:%M} – {session.end_at:%H:%M}"
        class_text = session.course_code or "Mã lớp chưa cập nhật"
        room_text = session.room or "Chưa cập nhật phòng"
        class_label = ft.Text(
            class_text,
            size=11,
            color=C.TEXT_SECONDARY,
            max_lines=2 if narrow else 1,
            overflow=ft.TextOverflow.ELLIPSIS,
            selectable=True,
        )
        room_label = ft.Text(
            room_text,
            size=12 if narrow else 11,
            color=C.TEXT_PRIMARY,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        title = ft.Text(
            session.subject,
            size=14 if narrow else 12,
            weight=ft.FontWeight.W_600,
            color=C.TEXT_PRIMARY,
            max_lines=3 if narrow else 2,
            overflow=ft.TextOverflow.ELLIPSIS,
            selectable=True,
        )
        if narrow:
            summary = ft.Row(
                [
                    ft.Container(
                        content=ft.Text(
                            time_text,
                            size=12,
                            weight=ft.FontWeight.W_600,
                            color=C.ACCENT,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        width=82,
                        alignment=ft.Alignment(0, -1),
                    ),
                    ft.Container(width=1, height=70, bgcolor=C.BORDER),
                    ft.Column(
                        [title, class_label, room_label],
                        spacing=3,
                        expand=True,
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        else:
            time_header_controls: list[ft.Control] = [
                ft.Text(
                    time_text,
                    size=11,
                    weight=ft.FontWeight.W_600,
                    color=C.ACCENT,
                ),
            ]
            if session.cancelled:
                time_header_controls.append(
                    ft.Container(
                        content=ft.Text(
                            "Tạm ngưng",
                            size=9.5,
                            weight=ft.FontWeight.W_600,
                            color=C.CRITICAL,
                        ),
                        bgcolor=ft.Colors.with_opacity(0.12, C.CRITICAL),
                        border_radius=999,
                        padding=ft.Padding.symmetric(horizontal=6, vertical=1),
                    )
                )
            time_header = ft.Row(
                time_header_controls,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
                wrap=True,
                run_spacing=0,
            )
            summary = ft.Column(
                [time_header, title, class_label, room_label],
                spacing=2,
                tight=True,
            )

        details_button = ft.TextButton(
            "Chi tiết",
            height=44,
            width=88 if narrow else 80,
            tooltip="Chi tiết buổi học",
            style=_text_action_style(),
            on_click=lambda _event, item=session: self._show_session_details(
                item, return_focus=details_button
            ),
        )
        return ft.Container(
            height=None if narrow else 172,
            content=ft.Column(
                [summary, details_button],
                alignment=(
                    ft.MainAxisAlignment.START
                    if narrow
                    else ft.MainAxisAlignment.SPACE_BETWEEN
                ),
                spacing=4,
            ),
            padding=ft.Padding.all(8 if not narrow else 10),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=8,
        )

    def _show_session_details(
        self, session: ClassSession, *, return_focus: ft.Control | None = None
    ) -> None:
        width = max(280.0, min(460.0, self._layout_width - 48))
        now = datetime.now()
        skip_focus_restore = {"skip": False}

        def _dismiss_for_transition() -> None:
            skip_focus_restore["skip"] = True
            self._safe_pop_dialog()

        def _open_outline_from_detail() -> None:
            _dismiss_for_transition()
            self._show_outline(
                session,
                return_focus=return_focus,
                on_back=lambda: self._show_session_details(
                    session, return_focus=return_focus
                ),
            )

        def _on_key_down(event) -> None:
            if getattr(event, "key", "") == "Escape":
                self._safe_pop_dialog()

        async def _restore_focus(_event=None) -> None:
            if not skip_focus_restore["skip"]:
                await self._restore_control_focus(return_focus)

        close_btn = ft.Semantics(
            label="Đóng",
            button=True,
            content=ft.IconButton(
                icon=ft.Icons.CLOSE_ROUNDED,
                icon_color=C.TEXT_SECONDARY,
                icon_size=20,
                tooltip="Đóng",
                padding=0,
                size_constraints=ft.BoxConstraints(
                    min_width=44, max_width=44, min_height=44, max_height=44
                ),
                on_click=lambda _e: self._safe_pop_dialog(),
            ),
        )
        header_row = ft.Row(
            [
                ft.Row(
                    [
                        ft.Text(
                            "CHI TIẾT BUỔI HỌC",
                            size=11,
                            weight=ft.FontWeight.W_700,
                            color=C.TEXT_SECONDARY,
                        ),
                        self._phase_badge(session, now),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    expand=True,
                ),
                close_btn,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        body = self._session_card(
            session,
            now,
            on_before_open_url=_dismiss_for_transition,
            on_open_outline=_open_outline_from_detail,
        )

        dialog = ft.AlertDialog(
            modal=True,
            scrollable=True,
            semantics_label="Chi tiết buổi học",
            content_padding=0,
            inset_padding=ft.Padding.symmetric(horizontal=16, vertical=24),
            content=ft.KeyboardListener(
                autofocus=True,
                on_key_down=_on_key_down,
                content=ft.Container(
                    width=width,
                    padding=ft.Padding.only(left=16, right=16, top=10, bottom=16),
                    content=ft.Column(
                        [header_row, body],
                        spacing=8,
                        tight=True,
                    ),
                ),
            ),
            bgcolor=C.BG,
            shape=ft.RoundedRectangleBorder(radius=16),
            on_dismiss=_restore_focus,
        )
        try:
            self._page_ref.show_dialog(dialog)
        except Exception:
            logger.debug("Could not show class details", exc_info=True)

    def _show_outline(
        self,
        session: ClassSession,
        *,
        return_focus: ft.Control | None = None,
        on_back: Callable[[], None] | None = None,
    ) -> None:
        body = session.outline or "Môn học này chưa có mô tả đề cương."
        width = max(280.0, min(520.0, self._layout_width - 48))
        try:
            page_h = float(getattr(self._page_ref, "height", 0) or 0)
        except (TypeError, ValueError):
            page_h = 0.0
        max_h = min(420.0, max(220.0, page_h * 0.55)) if page_h > 0 else 420.0
        skip_focus_restore = {"skip": False}

        def _on_key_down(event) -> None:
            if getattr(event, "key", "") == "Escape":
                self._safe_pop_dialog()

        async def _restore_focus(_event=None) -> None:
            if not skip_focus_restore["skip"]:
                await self._restore_control_focus(return_focus)

        def _handle_back_to_detail(_e=None) -> None:
            if callable(on_back):
                skip_focus_restore["skip"] = True
                self._safe_pop_dialog()
                on_back()

        def _handle_outline_link(event) -> None:
            target = str(getattr(event, "data", "") or "").strip()
            if not target:
                return
            validated = validate_https_url(target)
            if validated is None:
                return
            skip_focus_restore["skip"] = True
            self._safe_pop_dialog()
            self._open_url(
                validated,
                f"Đề cương · {session.subject}",
                require_uth_lms=False,
            )

        btn_constraints = ft.BoxConstraints(
            min_width=44, max_width=44, min_height=44, max_height=44
        )
        leading_controls: list[ft.Control] = []
        if callable(on_back):
            leading_controls.append(
                ft.Semantics(
                    label="Quay lại chi tiết buổi học",
                    button=True,
                    content=ft.IconButton(
                        icon=ft.Icons.ARROW_BACK_ROUNDED,
                        icon_color=C.TEXT_SECONDARY,
                        icon_size=19,
                        tooltip="Quay lại",
                        padding=0,
                        size_constraints=btn_constraints,
                        on_click=_handle_back_to_detail,
                    ),
                )
            )
        leading_controls.append(
            ft.Text(
                "Đề cương chi tiết học phần",
                size=15,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
                expand=True,
                semantics_label="Đề cương chi tiết học phần",
            )
        )
        leading_controls.append(
            ft.Semantics(
                label="Đóng",
                button=True,
                content=ft.IconButton(
                    icon=ft.Icons.CLOSE_ROUNDED,
                    icon_color=C.TEXT_SECONDARY,
                    icon_size=20,
                    tooltip="Đóng",
                    padding=0,
                    size_constraints=btn_constraints,
                    on_click=lambda _e: self._safe_pop_dialog(),
                ),
            )
        )

        dialog = ft.AlertDialog(
            modal=True,
            semantics_label="Đề cương chi tiết học phần",
            title_padding=ft.Padding.only(left=12 if callable(on_back) else 16, right=8, top=10, bottom=4),
            content_padding=ft.Padding.only(left=16, right=16, top=4, bottom=16),
            title=ft.Row(
                leading_controls,
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            content=ft.KeyboardListener(
                autofocus=True,
                on_key_down=_on_key_down,
                content=ft.Container(
                    width=width,
                    height=max_h,
                    padding=ft.Padding.all(14),
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=12,
                    content=ft.Column(
                        [
                            ft.Text(
                                session.subject,
                                size=14,
                                weight=ft.FontWeight.W_700,
                                color=C.TEXT_PRIMARY,
                            ),
                            ft.Text(
                                f"Mã môn học: {session.subject_code or '—'}  ·  Mã lớp: {session.course_code or '—'}",
                                size=11,
                                color=C.TEXT_SECONDARY,
                            ),
                            ft.Divider(height=8, color=C.BORDER),
                            ft.Markdown(
                                value=body,
                                selectable=True,
                                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                                on_tap_link=_handle_outline_link,
                            ),
                        ],
                        tight=True,
                        scroll=ft.ScrollMode.AUTO,
                        spacing=6,
                    ),
                ),
            ),
            bgcolor=C.BG,
            shape=ft.RoundedRectangleBorder(radius=16),
            on_dismiss=_restore_focus,
        )
        try:
            self._page_ref.show_dialog(dialog)
        except Exception:
            logger.debug("Could not show outline dialog", exc_info=True)

    # ----- desktop week view
    def _render_week_body(self, monday: date) -> None:
        today = date.today()
        days = [monday + timedelta(days=i) for i in range(7)]
        if not any(self._sessions_on(day) for day in days):
            self._body.controls = [
                ft.Container(
                    content=ft.Text(
                        "Tuần này không có lịch học",
                        size=13,
                        color=C.TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    alignment=ft.Alignment(0, 0),
                    padding=ft.Padding.symmetric(vertical=28),
                )
            ]
            return

        gaps = _DAY_COLUMN_GAP * (len(days) - 1)
        available = max(0.0, self._layout_width - _WEEK_CONTENT_INSET - gaps)
        column_width = min(
            _DAY_COLUMN_MAX_W,
            max(_DAY_COLUMN_MIN_W, available / len(days)),
        )
        day_columns: list[ft.Control] = []
        for day in days:
            is_today = day == today
            day_header = ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            _WEEKDAYS_LONG[day.weekday()],
                            size=11,
                            weight=(
                                ft.FontWeight.W_700
                                if is_today
                                else ft.FontWeight.W_600
                            ),
                            color=C.ON_ACCENT if is_today else C.TEXT_PRIMARY,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ft.Text(
                            f"{day:%d/%m}",
                            size=11,
                            weight=(
                                ft.FontWeight.W_700
                                if is_today
                                else ft.FontWeight.W_400
                            ),
                            color=C.ON_ACCENT if is_today else C.TEXT_SECONDARY,
                            text_align=ft.TextAlign.CENTER,
                        ),
                    ],
                    spacing=2,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                height=54,
                alignment=ft.Alignment(0, 0),
                border_radius=6,
                bgcolor=C.ACCENT if is_today else C.SURFACE_ELEVATED,
                border=ft.Border.all(1, C.ACCENT if is_today else C.BORDER),
            )
            sessions = [
                self._session_summary_card(session, narrow=False)
                for session in self._sessions_on(day)
            ]
            day_columns.append(
                ft.Container(
                    content=ft.Column(
                        [day_header, *sessions],
                        spacing=6,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    width=column_width,
                    padding=4,
                    bgcolor=(
                        ft.Colors.with_opacity(0.08, C.ACCENT)
                        if is_today
                        else C.SURFACE
                    ),
                    border=(
                        ft.Border.all(1.5, C.ACCENT)
                        if is_today
                        else ft.Border.all(1, C.BORDER)
                    ),
                    border_radius=8,
                    data={
                        "schedule_day": day.isoformat(),
                        "is_today": is_today,
                    },
                )
            )

        self._body.controls = [
            ft.Row(
                day_columns,
                spacing=_DAY_COLUMN_GAP,
                wrap=False,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        ]

    # ------------------------------------------------------------------ misc
    @staticmethod
    def _validated_url(url: str, *, require_uth_lms: bool) -> str | None:
        if require_uth_lms:
            return url if is_trusted_uth_lms_url(url) else None
        return validate_https_url(url)

    def _show_demo_lms_guard_snackbar(self) -> None:
        sb = ft.SnackBar(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, color=C.WARNING, size=16),
                    ft.Text(
                        DEMO_LMS_WEBVIEW_GUARD_MESSAGE,
                        size=13,
                        color=C.TEXT_PRIMARY,
                        expand=True,
                    ),
                ],
                spacing=8,
            ),
            bgcolor=C.SURFACE,
            duration=3000,
        )
        overlay = getattr(self._page_ref, "overlay", None)
        if overlay is not None:
            overlay[:] = [
                item for item in overlay if not isinstance(item, ft.SnackBar)
            ]
            overlay.append(sb)
            sb.open = True
            update_fn = getattr(self._page_ref, "update", None)
            if callable(update_fn):
                try:
                    update_fn()
                except Exception:
                    pass
            return
        show_dlg = getattr(self._page_ref, "show_dialog", None)
        if callable(show_dlg):
            try:
                show_dlg(sb)
            except (AttributeError, TypeError):
                pass

    def _open_url(
        self, url: str, title: str = "", *, require_uth_lms: bool = False
    ) -> None:
        validated = self._validated_url(url, require_uth_lms=require_uth_lms)
        if validated is None:
            logger.warning("Blocked invalid or untrusted schedule URL")
            return
        if callable(self._on_open_url):
            self._on_open_url(validated, title)
            return
        if is_demo_account(getattr(settings, "UTH_USERNAME", "")) and is_uth_lms_url(
            validated
        ):
            self._show_demo_lms_guard_snackbar()
            return
        InAppWebViewDialog(
            self._page_ref,
            url=validated,
            title=title,
            on_open_external=lambda target: self._open_external(
                target, require_uth_lms=require_uth_lms
            ),
        ).show()

    def _open_external(
        self, url: str, *, require_uth_lms: bool = False
    ) -> None:
        validated = self._validated_url(url, require_uth_lms=require_uth_lms)
        if validated is None:
            logger.warning("Blocked invalid or untrusted external schedule URL")
            return
        if callable(self._on_open_external):
            self._on_open_external(validated)
            return
        if is_demo_account(getattr(settings, "UTH_USERNAME", "")) and is_uth_lms_url(
            validated
        ):
            self._show_demo_lms_guard_snackbar()
            return

        async def _launch() -> None:
            try:
                await ft.UrlLauncher().launch_url(validated)
            except Exception:
                logger.debug("Could not open schedule link", exc_info=True)

        try:
            self._page_ref.run_task(_launch)
        except (AttributeError, RuntimeError, TypeError):
            logger.debug("Could not schedule link launch", exc_info=True)
