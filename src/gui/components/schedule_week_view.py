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
from gui.core.theme import C

logger = logging.getLogger(__name__)

FetchWeek = Callable[[date], "dict[date, tuple[ClassSession, ...]]"]

_WEEKDAYS_LONG = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
_CA_LABELS = {0: "Khác", 1: "Ca 1", 2: "Ca 2", 3: "Ca 3", 4: "Ca 4", 5: "Ca 5", 6: "Ca 6"}
_CA_GROUP = {0: "", 1: "Sáng", 2: "Sáng", 3: "Chiều", 4: "Chiều", 5: "Tối", 6: "Tối"}
_COL_W = 250          # a full card needs a wide column (grid scrolls sideways)
_LABEL_W = 52
_CELL_MIN_H = 56      # empty cell height so every Ca row keeps a visible band
_TODAY_TINT = "#38BDF8"   # light aqua used to highlight today's column
_SWIPE_VELOCITY = 250.0
_TTL_CURRENT = timedelta(minutes=10)   # re-fetch the current week after this
_TTL_OTHER = timedelta(hours=6)        # ...and the other cached weeks after this
_PREFETCH_GAP_S = 0.3


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _ca_of(period: int | None) -> int | None:
    """Tiết 1-3 -> Ca 1, 4-6 -> Ca 2, ... 16-18 -> Ca 6 (same as the web)."""
    if period is None or period < 1 or period > 18:
        return None
    return (period - 1) // 3 + 1


def _ca_for(session: ClassSession) -> int:
    """Ca the session starts in (0 when the Portal gave no period)."""
    return _ca_of(session.period_start) or 0


class ScheduleWeekView(ft.Container):
    def __init__(
        self,
        page: ft.Page,
        fetch_week: Optional[FetchWeek] = None,
        cache: Optional[WeekScheduleCache] = None,
    ) -> None:
        super().__init__(expand=True, visible=False)
        self._page_ref = page
        self._fetch_week = fetch_week
        self._cache = cache
        self._cache_lock = threading.Lock()
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
        self._prev_btn = ft.IconButton(
            ft.Icons.CHEVRON_LEFT_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Tuần trước",
            on_click=lambda _: self._shift(-7),
        )
        self._next_btn = ft.IconButton(
            ft.Icons.CHEVRON_RIGHT_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Tuần sau",
            on_click=lambda _: self._shift(7),
        )
        self._today_btn = ft.IconButton(
            ft.Icons.TODAY_ROUNDED,
            icon_color=C.ACCENT,
            icon_size=18,
            tooltip="Tuần này",
            on_click=lambda _: self._go(date.today()),
        )
        self._refresh_btn = ft.IconButton(
            ft.Icons.REFRESH_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=18,
            tooltip="Tải lại tuần này",
            on_click=lambda _: self._reload(),
        )
        self._nav_row = ft.Row(
            [
                ft.Row(
                    [self._prev_btn, self._week_label, self._next_btn],
                    spacing=0,
                ),
                ft.Row([self._today_btn, self._refresh_btn], spacing=0),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
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
        # Swipe left/right = next/previous week (one page per week).
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
            self._shift(7)
        elif velocity >= _SWIPE_VELOCITY:
            self._shift(-7)

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
        self._prev_btn.icon_color = C.TEXT_SECONDARY
        self._next_btn.icon_color = C.TEXT_SECONDARY
        self._today_btn.icon_color = C.ACCENT
        self._refresh_btn.icon_color = C.TEXT_SECONDARY
        self._render()

    # ------------------------------------------------------------ navigation
    def _shift(self, days: int) -> None:
        self._go(self._selected + timedelta(days=days))

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
        try:
            for monday in WeekScheduleCache.window():
                if self._auth_failed:
                    break
                if monday in self._loading or not self._needs_fetch(monday):
                    continue
                self._loading.add(monday)
                await self._load_async(monday)
                await asyncio.sleep(_PREFETCH_GAP_S)
        finally:
            self._prefetching = False

    async def _load_async(self, monday: date) -> None:
        generation = self._generation
        error = ""
        data: dict[date, tuple[ClassSession, ...]] | None = None
        try:
            data = await asyncio.to_thread(self._fetch_week, monday)  # type: ignore[arg-type]
        except ScheduleAuthenticationError:
            self._auth_failed = True
            error = "Chưa đăng nhập Portal. Vào Cài đặt để nhập MSSV và mật khẩu."
        except Exception:
            logger.warning("Portal week schedule request failed", exc_info=True)
            error = "Không tải được lịch học. Kiểm tra kết nối rồi thử lại."
        if generation != self._generation:
            return  # account changed while fetching: drop the stale result
        self._loading.discard(monday)
        forced = monday in self._forced
        self._forced.discard(monday)
        if data is not None:
            fetched_at = datetime.now()
            self._weeks[monday] = data
            self._live_at[monday] = fetched_at
            self._errors.pop(monday, None)
            await asyncio.to_thread(self._save_cache, monday, data, fetched_at)
        elif monday not in self._weeks or forced:
            # Cached data stays on screen; only surface the error when there
            # is nothing to show or the person asked for a reload.
            self._errors[monday] = error
        if monday == _monday(self._selected):
            self._render()

    # ---------------------------------------------------------------- render
    def _sessions_on(self, day: date) -> tuple[ClassSession, ...]:
        return self._weeks.get(_monday(day), {}).get(day, ())

    def _render(self) -> None:
        monday = _monday(self._selected)
        sunday = monday + timedelta(days=6)
        self._week_label.value = f"{monday:%d/%m} – {sunday:%d/%m/%Y}"
        self._render_status(monday)
        if monday in self._weeks:
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
                ft.TextButton("Thử lại", on_click=lambda _: self._reload()),
            ]
        self._status.controls = controls
        self._status.visible = bool(controls)

    # ----- session card (full detail)
    @staticmethod
    def _phase_chip(session: ClassSession, now: datetime) -> ft.Control | None:
        if session.start_at.date() != now.date() and not session.cancelled:
            return None
        phase = session.phase_at(now)
        label, color = {
            SchedulePhase.UPCOMING: ("Sắp tới", C.ACCENT),
            SchedulePhase.IN_PROGRESS: ("Đang học", C.SAFE),
            SchedulePhase.FINISHED: ("Đã xong", C.TEXT_SECONDARY),
            SchedulePhase.CANCELLED: ("Tạm ngưng", C.CRITICAL),
        }[phase]
        return ft.Container(
            content=ft.Text(label, size=10, color=color, weight=ft.FontWeight.W_600),
            padding=ft.Padding.symmetric(horizontal=7, vertical=3),
            border=ft.Border.all(1, color),
            border_radius=999,
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

    def _link_button(self, label: str, icon, color: str, on_click) -> ft.Control:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon, size=16, color=color),
                    ft.Text(label, size=12, weight=ft.FontWeight.W_600, color=color),
                ],
                spacing=6,
                tight=True,
            ),
            padding=ft.Padding.symmetric(horizontal=2, vertical=3),
            on_click=on_click,
            ink=True,
            border_radius=6,
        )

    def _session_card(self, session: ClassSession, now: datetime) -> ft.Control:
        accent = C.CRITICAL if session.cancelled else C.ACCENT
        chip = self._phase_chip(session, now)
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
                    *([chip] if chip else []),
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
                self._link_button(
                    "Truy cập Zoom",
                    ft.Icons.VIDEOCAM_OUTLINED,
                    C.ACCENT,
                    lambda _, u=session.meeting_url: self._open_url(u),
                )
            )
        if session.has_outline:
            rows.append(
                self._link_button(
                    "Đề cương chi tiết học phần",
                    ft.Icons.AUTO_STORIES_ROUNDED,
                    C.WARNING,
                    lambda _, item=session: self._show_outline(item),
                )
            )
        if session.link:
            rows.append(
                self._link_button(
                    "LMS",
                    ft.Icons.SCHOOL_ROUNDED,
                    C.CRITICAL,
                    lambda _, u=session.link: self._open_url(u),
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

    def _show_outline(self, session: ClassSession) -> None:
        body = session.outline or "Môn học này chưa có mô tả đề cương."
        dialog = ft.AlertDialog(
            title=ft.Text(
                "Đề cương chi tiết học phần",
                size=16,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
            ),
            content=ft.Column(
                [
                    ft.Text(session.subject, size=13, weight=ft.FontWeight.W_600, color=C.ACCENT),
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
                width=460,
                spacing=6,
            ),
            actions=[
                ft.TextButton("Đóng", on_click=lambda _: self._page_ref.pop_dialog())
            ],
            bgcolor=C.SURFACE,
            shape=ft.RoundedRectangleBorder(radius=14),
        )
        try:
            self._page_ref.show_dialog(dialog)
        except Exception:
            logger.debug("Could not show outline dialog", exc_info=True)


    # ----- week grid
    def _render_week_body(self, monday: date) -> None:
        today = date.today()
        now = datetime.now()
        days = [monday + timedelta(days=i) for i in range(7)]
        grid: dict[tuple[int, int], list[ClassSession]] = {}
        for col, day in enumerate(days):
            for session in self._sessions_on(day):
                grid.setdefault((_ca_for(session), col), []).append(session)
        used_ca = sorted({ca for ca, _ in grid}, key=lambda ca: ca or 99)
        if not used_ca:
            self._body.controls = [
                ft.Container(
                    ft.Text(
                        "Tuần này không có lịch học",
                        size=12,
                        color=C.TEXT_SECONDARY,
                    ),
                    alignment=ft.Alignment(0, 0),
                    padding=ft.Padding.symmetric(vertical=28),
                )
            ]
            return

        def col_size(control: ft.Container) -> ft.Container:
            # Fixed widths: the today highlight below is positioned by x offset.
            control.width = _COL_W
            return control

        def head(text: str, *, today_col: bool = False) -> ft.Container:
            return ft.Container(
                content=ft.Text(
                    text,
                    size=11,
                    weight=ft.FontWeight.W_700 if today_col else ft.FontWeight.W_600,
                    color=C.TEXT_PRIMARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                height=40,
                alignment=ft.Alignment(0, 0),
                bgcolor=(
                    ft.Colors.with_opacity(0.45, _TODAY_TINT)
                    if today_col
                    else C.SURFACE_ELEVATED
                ),
                border=ft.Border.all(1, C.BORDER),
            )

        corner = ft.Container(
            content=ft.Text("Ca", size=11, weight=ft.FontWeight.W_600,
                            color=C.TEXT_PRIMARY),
            width=_LABEL_W,
            height=40,
            alignment=ft.Alignment(0, 0),
            bgcolor=C.SURFACE_ELEVATED,
            border=ft.Border.all(1, C.BORDER),
        )
        rows: list[ft.Control] = [
            ft.Row(
                [corner]
                + [
                    col_size(
                        head(
                            f"{_WEEKDAYS_LONG[i]}\n{day:%d/%m}",
                            today_col=day == today,
                        )
                    )
                    for i, day in enumerate(days)
                ],
                spacing=0,
            )
        ]
        for ca in used_ca:
            label_text = _CA_LABELS[ca] + (f"\n{_CA_GROUP[ca]}" if _CA_GROUP[ca] else "")
            cells: list[ft.Control] = [
                ft.Container(
                    content=ft.Text(
                        label_text,
                        size=10,
                        weight=ft.FontWeight.W_600,
                        color=C.TEXT_PRIMARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    width=_LABEL_W,
                    height=_CELL_MIN_H,
                    alignment=ft.Alignment(0, -1),
                    padding=ft.Padding.only(top=8),
                )
            ]
            for col, day in enumerate(days):
                items = grid.get((ca, col), [])
                cells.append(
                    col_size(
                        ft.Container(
                            content=ft.Column(
                                [self._session_card(s, now) for s in items],
                                spacing=6,
                            ),
                            # A card sizes the cell; empty cells keep a min band.
                            height=None if items else _CELL_MIN_H,
                            padding=4,
                            bgcolor=None if day == today else C.SURFACE,
                        )
                    )
                )
            total_w = _LABEL_W + 7 * _COL_W
            layers: list[ft.Control] = [
                # Ca label background spans the whole band height.
                ft.Container(
                    left=0, width=_LABEL_W, top=0, bottom=0,
                    bgcolor=C.SURFACE_ELEVATED,
                )
            ]
            today_col = (today - monday).days if monday <= today < monday + timedelta(days=7) else None
            if today_col is not None:
                # Full-height aqua strip: it fills the whole band, so the
                # today column stays one unbroken colour from header to bottom.
                layers.append(
                    ft.Container(
                        left=_LABEL_W + today_col * _COL_W,
                        width=_COL_W,
                        top=0,
                        bottom=0,
                        bgcolor=ft.Colors.with_opacity(0.18, _TODAY_TINT),
                    )
                )
            # Band divider, skipping the today column so the strip is not cut.
            x0 = _LABEL_W + today_col * _COL_W if today_col is not None else total_w
            x1 = x0 + _COL_W if today_col is not None else total_w
            for left, width in ((0, x0), (x1, total_w - x1)):
                if width > 0:
                    layers.append(
                        ft.Container(
                            left=left,
                            width=width,
                            height=1,
                            bottom=0,
                            bgcolor=C.BORDER,
                        )
                    )
            rows.append(
                ft.Stack(
                    [
                        *layers,
                        ft.Row(
                            cells,
                            spacing=0,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        ),
                    ],
                    width=total_w,
                )
            )
        table = ft.Column(rows, spacing=0)
        self._body.controls = [
            ft.Row(
                [table],
                scroll=ft.ScrollMode.AUTO,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        ]

    # ------------------------------------------------------------------ misc
    def _open_url(self, url: str) -> None:
        async def _launch() -> None:
            try:
                await ft.UrlLauncher().launch_url(url)
            except Exception:
                logger.debug("Could not open schedule link", exc_info=True)

        try:
            self._page_ref.run_task(_launch)
        except (AttributeError, RuntimeError, TypeError):
            logger.debug("Could not schedule link launch", exc_info=True)
