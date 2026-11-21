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
        shape=ft.RoundedRectangleBorder(radius=4),
    )


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
        self._auth_failed = False
        self._generation = 0                       # bumped by reset()

        self._week_label = ft.Text(
            "", size=14, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY
        )
        self._prev_btn = ft.TextButton(
            "Tuần trước",
            width=94,
            height=48,
            style=_text_action_style(C.TEXT_SECONDARY),
            on_click=lambda _: self._navigate(-1),
        )
        self._next_btn = ft.TextButton(
            "Tuần sau",
            width=86,
            height=48,
            style=_text_action_style(C.TEXT_SECONDARY),
            on_click=lambda _: self._navigate(1),
        )
        self._today_btn = ft.TextButton(
            "Hôm nay",
            width=82,
            height=48,
            style=_text_action_style(C.ACCENT),
            on_click=lambda _: self._go(date.today()),
        )
        self._refresh_btn = ft.TextButton(
            "Tải lại lịch học",
            width=126,
            height=48,
            style=_text_action_style(C.TEXT_SECONDARY),
            on_click=lambda _: self._reload(),
        )
        self._nav_row = ft.Row(
            [
                self._prev_btn,
                self._week_label,
                self._next_btn,
                self._today_btn,
                self._refresh_btn,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=0,
            run_spacing=4,
            wrap=True,
        )

        self._status = ft.Row(spacing=8, visible=False)
        self._body = ft.Column(spacing=6, expand=True, scroll=ft.ScrollMode.AUTO)

        column = ft.Column(
            [
                ft.Container(
                    self._nav_row,
                    padding=ft.Padding.only(left=4, right=4, top=2),
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
        if self.visible and (crossed_breakpoint or abs(width - previous_width) >= 24):
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
        self._prev_btn.style = _text_action_style(C.TEXT_SECONDARY)
        self._next_btn.style = _text_action_style(C.TEXT_SECONDARY)
        self._today_btn.style = _text_action_style(C.ACCENT)
        self._refresh_btn.style = _text_action_style(C.TEXT_SECONDARY)
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
        if self._fetch_week is None or self._prefetching or self._auth_failed:
            return
        self._prefetching = True
        try:
            self._page_ref.run_task(self._prefetch_async)
        except (AttributeError, RuntimeError, TypeError):
            self._prefetching = False
            logger.debug("Could not schedule timetable prefetch", exc_info=True)

    async def _prefetch_async(self) -> None:
        """Keep the current week and 4 weeks each side warm, nearest first."""
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
            self._prefetching = False

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
            self._prev_btn.content = "Ngày trước"
            self._next_btn.content = "Ngày sau"
        else:
            self._week_label.value = f"{monday:%d/%m} – {sunday:%d/%m/%Y}"
            self._prev_btn.content = "Tuần trước"
            self._next_btn.content = "Tuần sau"
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

    # ----- session card (full detail)
    @staticmethod
    def _phase_label(session: ClassSession, now: datetime) -> ft.Control | None:
        if session.start_at.date() != now.date() and not session.cancelled:
            return None
        phase = session.phase_at(now)
        label, color = {
            SchedulePhase.UPCOMING: ("Sắp tới", C.ACCENT),
            SchedulePhase.IN_PROGRESS: ("Đang học", C.SAFE),
            SchedulePhase.FINISHED: ("Đã xong", C.TEXT_SECONDARY),
            SchedulePhase.CANCELLED: ("Tạm ngưng", C.CRITICAL),
        }[phase]
        return ft.Text(
            label,
            size=10,
            color=color,
            weight=ft.FontWeight.W_600,
        )

    def _meta_row(self, icon, text: str, *, color: str | None = None) -> ft.Row:
        return ft.Row(
            [
                ft.Icon(icon, size=15, color=C.ACCENT),
                ft.Text(
                    text,
                    size=12,
                    color=color or C.TEXT_PRIMARY,
                    expand=True,
                    selectable=True,
                ),
            ],
            spacing=7,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    def _link_button(self, label: str, icon, color: str, on_click) -> ft.TextButton:
        return ft.TextButton(
            label,
            icon=icon,
            icon_color=color,
            height=44,
            tooltip=label,
            style=_text_action_style(color),
            on_click=on_click,
        )

    def _link_with_external_button(
        self,
        label: str,
        icon,
        color: str,
        url: str,
        title: str,
        *,
        require_uth_lms: bool = False,
    ) -> ft.Row:
        primary = self._link_button(
            label,
            icon,
            color,
            lambda _, u=url, t=title, req=require_uth_lms: self._open_url(
                u, t, require_uth_lms=req
            ),
        )
        external_btn = ft.Semantics(
            label=f"Mở {label} bằng trình duyệt ngoài",
            button=True,
            content=ft.IconButton(
                icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                icon_color=color,
                icon_size=18,
                tooltip=f"Mở {label} bằng trình duyệt ngoài",
                padding=0,
                size_constraints=ft.BoxConstraints(
                    min_width=44, max_width=44, min_height=44, max_height=44
                ),
                on_click=lambda _, u=url, req=require_uth_lms: self._open_external(
                    u, require_uth_lms=req
                ),
            ),
        )
        return ft.Row(
            [primary, external_btn],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=4,
        )

    def _session_card(self, session: ClassSession, now: datetime) -> ft.Control:
        accent = C.CRITICAL if session.cancelled else C.ACCENT
        phase_label = self._phase_label(session, now)
        tiet = (
            f"Tiết: {session.period_start} - {session.period_end}"
            if session.period_start is not None and session.period_end is not None
            else "Tiết: chưa cập nhật"
        )
        rows: list[ft.Control] = [
            ft.Row(
                [
                    ft.Text(
                        session.subject,
                        size=14,
                        weight=ft.FontWeight.W_700,
                        color=C.ACCENT,
                        expand=True,
                    ),
                    *([phase_label] if phase_label else []),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            ft.Text(
                session.course_code or "—",
                size=13,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
                selectable=True,
            ),
            self._meta_row(ft.Icons.TAG_ROUNDED, f"Mã môn học: {session.subject_code or 'Chưa cập nhật'}"),
            self._meta_row(ft.Icons.MENU_BOOK_ROUNDED, tiet),
            self._meta_row(
                ft.Icons.ACCESS_TIME_ROUNDED,
                f"Thời gian: {session.start_at:%H:%M} - {session.end_at:%H:%M}",
            ),
            self._meta_row(
                ft.Icons.MEETING_ROOM_OUTLINED,
                f"Phòng: {session.room or 'Chưa cập nhật'}",
            ),
        ]
        if session.campus:
            rows.append(self._meta_row(ft.Icons.LOCATION_ON_OUTLINED, f"Cơ sở: {session.campus}"))
        rows.append(
            self._meta_row(
                ft.Icons.PERSON_OUTLINE_ROUNDED,
                f"Giảng viên: {session.lecturer or 'Chưa cập nhật'}",
            )
        )
        if session.note:
            rows.append(
                self._meta_row(
                    ft.Icons.INFO_OUTLINE_ROUNDED,
                    f"Ghi chú: {session.note}",
                    color=C.CRITICAL,
                )
            )
        if session.meeting_url:
            rows.append(
                self._link_with_external_button(
                    "Truy cập Zoom",
                    ft.Icons.VIDEOCAM_OUTLINED,
                    C.ACCENT,
                    session.meeting_url,
                    f"Zoom · {session.subject}",
                    require_uth_lms=False,
                )
            )
        if session.has_outline:
            outline_btn = self._link_button(
                "Đề cương chi tiết học phần",
                ft.Icons.AUTO_STORIES_ROUNDED,
                C.WARNING,
                None,
            )
            outline_btn.on_click = lambda _, item=session, btn=outline_btn: self._show_outline(
                item, return_focus=btn
            )
            rows.append(outline_btn)
        if session.link:
            rows.append(
                self._link_with_external_button(
                    "LMS",
                    ft.Icons.SCHOOL_ROUNDED,
                    C.CRITICAL,
                    session.link,
                    f"LMS · {session.subject}",
                    require_uth_lms=True,
                )
            )
        return ft.Container(
            content=ft.Column(rows, spacing=6),
            padding=ft.Padding.all(14),
            bgcolor=ft.Colors.with_opacity(0.08, accent),
            border_radius=12,
            border=ft.Border(
                left=ft.BorderSide(4, accent),
                top=ft.BorderSide(1, C.BORDER),
                right=ft.BorderSide(1, C.BORDER),
                bottom=ft.BorderSide(1, C.BORDER),
            ),
        )

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
        width = max(280.0, min(520.0, self._layout_width - 48))
        try:
            page_h = float(getattr(self._page_ref, "height", 0) or 0)
        except (TypeError, ValueError):
            page_h = 0.0
        height = min(460.0, max(240.0, page_h * 0.65)) if page_h > 0 else 460.0

        def _on_key_down(event) -> None:
            if event.key == "Escape":
                self._page_ref.pop_dialog()

        async def _restore_focus(_event=None) -> None:
            if return_focus is not None:
                await return_focus.focus()

        dialog = ft.AlertDialog(
            modal=True,
            semantics_label="Chi tiết buổi học",
            title=ft.Text(
                "Chi tiết buổi học",
                size=16,
                weight=ft.FontWeight.W_600,
                color=C.TEXT_PRIMARY,
                semantics_label="Chi tiết buổi học",
            ),
            content=ft.KeyboardListener(
                content=ft.Column(
                    [self._session_card(session, datetime.now())],
                    width=width,
                    height=height,
                    spacing=0,
                    scroll=ft.ScrollMode.AUTO,
                ),
                autofocus=True,
                on_key_down=_on_key_down,
            ),
            actions=[
                ft.TextButton(
                    "Đóng",
                    height=44,
                    style=_text_action_style(),
                    on_click=lambda _event: self._page_ref.pop_dialog(),
                )
            ],
            bgcolor=C.SURFACE,
            shape=ft.RoundedRectangleBorder(radius=14),
            on_dismiss=_restore_focus,
        )
        try:
            self._page_ref.show_dialog(dialog)
        except Exception:
            logger.debug("Could not show class details", exc_info=True)

    def _show_outline(
        self, session: ClassSession, *, return_focus: ft.Control | None = None
    ) -> None:
        body = session.outline or "Môn học này chưa có mô tả đề cương."
        width = max(280.0, min(520.0, self._layout_width - 48))
        try:
            page_h = float(getattr(self._page_ref, "height", 0) or 0)
        except (TypeError, ValueError):
            page_h = 0.0
        max_h = min(420.0, max(220.0, page_h * 0.55)) if page_h > 0 else 420.0

        def _on_key_down(event) -> None:
            if getattr(event, "key", "") == "Escape":
                self._page_ref.pop_dialog()

        async def _restore_focus(_event=None) -> None:
            if return_focus is not None:
                await return_focus.focus()

        dialog = ft.AlertDialog(
            modal=True,
            semantics_label="Đề cương chi tiết học phần",
            title=ft.Text(
                "Đề cương chi tiết học phần",
                size=16,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
                semantics_label="Đề cương chi tiết học phần",
            ),
            content=ft.KeyboardListener(
                autofocus=True,
                on_key_down=_on_key_down,
                content=ft.Container(
                    width=width,
                    height=max_h,
                    content=ft.Column(
                        [
                            ft.Text(
                                session.subject,
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color=C.ACCENT,
                            ),
                            ft.Text(
                                f"Mã môn học: {session.subject_code or '—'}  ·  Mã lớp: {session.course_code or '—'}",
                                size=11,
                                color=C.TEXT_SECONDARY,
                            ),
                            ft.Divider(height=8, color=C.BORDER),
                            ft.Text(body, size=12, color=C.TEXT_PRIMARY, selectable=True),
                        ],
                        tight=True,
                        scroll=ft.ScrollMode.AUTO,
                        spacing=6,
                    ),
                ),
            ),
            actions=[
                ft.TextButton(
                    "Đóng",
                    height=44,
                    style=_text_action_style(),
                    on_click=lambda _: self._page_ref.pop_dialog(),
                )
            ],
            bgcolor=C.SURFACE,
            shape=ft.RoundedRectangleBorder(radius=14),
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
                            weight=ft.FontWeight.W_600,
                            color=C.TEXT_PRIMARY,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ft.Text(
                            f"{day:%d/%m}",
                            size=11,
                            color=C.TEXT_SECONDARY,
                            text_align=ft.TextAlign.CENTER,
                        ),
                    ],
                    spacing=2,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                height=54,
                alignment=ft.Alignment(0, 0),
                bgcolor=(
                    ft.Colors.with_opacity(0.14, C.ACCENT)
                    if is_today
                    else C.SURFACE_ELEVATED
                ),
                border=ft.Border.all(1, C.BORDER),
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
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=8,
                    data={"schedule_day": day.isoformat()},
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
