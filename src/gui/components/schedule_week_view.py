"""ScheduleWeekView - Portal class timetable for the Calendar tab.

Shows the selected week (Portal ``lichhoc/lichTuan``) as a Ca 1-6 x Thứ 2-CN
timetable like the web Portal. Every class is rendered as a full card.

Weeks are cached on disk and in memory: the current week plus 4 weeks before
and 4 weeks after are loaded from the cache instantly and refreshed in the
background.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import flet as ft

from core.today_schedule import (
    ClassSession,
    ScheduleAuthenticationError,
    SchedulePhase,
    WeekScheduleCache,
)
from core.url_validation import is_trusted_uth_lms_url, validate_https_url
from gui.components.in_app_webview import InAppWebViewDialog
from gui.core.theme import C
import platform_utils

logger = logging.getLogger(__name__)

FetchWeek = Callable[[date], "dict[date, tuple[ClassSession, ...]]"]
OpenUrlCallback = Callable[[str, str], None]
OpenExternalCallback = Callable[[str], None]

_WEEKDAYS_LONG = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
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


class ScheduleWeekView(ft.Container):
    def __init__(
        self,
        page: ft.Page,
        fetch_week: Optional[FetchWeek] = None,
        cache: Optional[WeekScheduleCache] = None,
        on_open_url: Optional[OpenUrlCallback] = None,
        on_open_external: Optional[OpenExternalCallback] = None,
    ) -> None:
        super().__init__(expand=True, visible=False)
        self._page_ref = page
        self._fetch_week = fetch_week
        self._cache = cache
        self._on_open_url = on_open_url
        self._on_open_external = on_open_external
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
        self._auth_failed = False
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
        self._week_label_box = ft.Container(
            content=self._week_label,
            padding=ft.Padding.symmetric(horizontal=6),
            alignment=ft.Alignment(0, 0),
        )
        self._stepper_box = ft.Container(
            content=ft.Row(
                [
                    self._prev_semantics,
                    self._week_label_box,
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
        task = self._prefetch_task
        self._prefetch_task = None
        if task is not None and not getattr(task, "done", lambda: True)():
            try:
                task.cancel()
            except Exception:
                pass
        self._prefetching = False
        self._prefetch_generation = None
        self._weeks.clear()
        self._live_at.clear()
        self._errors.clear()
        self._loading.clear()
        self._forced.clear()
        self._hydrated = False
        self._auth_failed = False
        if self.visible:
            self.show()

    def update_theme(self) -> None:
        self.bgcolor = C.BG
        self._week_label.color = C.TEXT_PRIMARY
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

    def _reload(self) -> None:
        monday = _monday(self._selected)
        self._errors.pop(monday, None)
        self._auth_failed = False
        self._ensure_loaded(monday, force=True)

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
        for monday, entry in entries.items():
            self._weeks.setdefault(monday, entry.days)

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
            return
        if not force and not self._needs_fetch(monday):
            return
        if self._fetch_week is None:
            if monday not in self._weeks:
                self._errors[monday] = "Chưa cấu hình nguồn lịch học."
                self._render()
            return
        self._loading.add(monday)
        if force:
            self._forced.add(monday)
        self._errors.pop(monday, None)
        self._render()
        try:
            self._page_ref.run_task(self._load_async, monday)
        except (AttributeError, RuntimeError, TypeError):
            self._loading.discard(monday)
            self._forced.discard(monday)
            logger.debug("Could not schedule timetable load", exc_info=True)

    def _start_prefetch(self) -> None:
        if self._fetch_week is None or self._auth_failed:
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
                if generation != self._generation or self._auth_failed:
                    break
                if monday in self._loading or not self._needs_fetch(monday):
                    continue
                self._loading.add(monday)
                await self._load_async(monday)
                if generation != self._generation or self._auth_failed:
                    break
                await asyncio.sleep(_PREFETCH_GAP_S)
                if generation != self._generation or self._auth_failed:
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
        data: dict[date, tuple[ClassSession, ...]] | None = None
        try:
            data = await asyncio.to_thread(self._fetch_week, monday)  # type: ignore[arg-type]
        except ScheduleAuthenticationError:
            auth_error = True
            error = "Chưa đăng nhập Portal. Vào Cài đặt để nhập MSSV và mật khẩu."
        except Exception:
            logger.warning("Portal week schedule request failed", exc_info=True)
            error = "Không tải được lịch học. Kiểm tra kết nối rồi thử lại."
        if generation != self._generation:
            return  # account changed while fetching: drop the stale result
        self._loading.discard(monday)
        forced = monday in self._forced
        self._forced.discard(monday)
        if auth_error:
            self._auth_failed = True
            self._weeks.clear()
            self._live_at.clear()
            self._errors[monday] = error
        elif data is not None:
            fetched_at = datetime.now()
            self._weeks[monday] = data
            self._live_at[monday] = fetched_at
            self._errors.pop(monday, None)
            await asyncio.to_thread(self._save_cache, monday, data, fetched_at)
        elif monday not in self._weeks or forced:
            # Cached data stays on screen; only surface the error when there
            # is nothing to show or the person asked for a reload.
            self._errors[monday] = error
        if monday == _monday(self._selected) or auth_error:
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
            controls = [
                ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, size=16, color=C.CRITICAL),
                ft.Text(
                    self._errors[monday], size=11, color=C.CRITICAL, expand=True
                ),
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
            max_lines=2,
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
            max_lines=3,
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
            summary_rows: list[ft.Control] = [
                ft.Text(
                    time_text,
                    size=11,
                    weight=ft.FontWeight.W_600,
                    color=C.ACCENT,
                ),
                title,
                class_label,
                room_label,
            ]
            if session.cancelled:
                summary_rows.append(
                    ft.Text(
                        "Tạm ngưng",
                        size=10,
                        weight=ft.FontWeight.W_600,
                        color=C.CRITICAL,
                    )
                )
            summary = ft.Column(summary_rows, spacing=4)

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
            content=ft.Column([summary, details_button], spacing=4),
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
            if not skip_focus_restore["skip"] and return_focus is not None:
                await return_focus.focus()

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
            if not skip_focus_restore["skip"] and return_focus is not None:
                await return_focus.focus()

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

        async def _launch() -> None:
            try:
                await ft.UrlLauncher().launch_url(validated)
            except Exception:
                logger.debug("Could not open schedule link", exc_info=True)

        try:
            self._page_ref.run_task(_launch)
        except (AttributeError, RuntimeError, TypeError):
            logger.debug("Could not schedule link launch", exc_info=True)
