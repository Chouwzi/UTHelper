"""Profile & Tuition View - student identity card and fee lookup tables.

Layout (single screen, minimal taps — no drill-down):

1. Header: back / title / manual refresh.
2. Student card: avatar, họ tên, MSSV, khoa/ngành/lớp.
3. Money summary: tổng mức nộp · đã đóng · còn nợ + one status pill.
4. Semester pills: each pill *is* the per-semester paid/debt status and
   filters the table on a single tap; the newest semester is selected by
   default (there is no "Tất cả" pill).
5. Tabs: Học phí (per subject) / Khoản thu khác.
6. Horizontally scrollable table with a totals row.

Data arrives from the controller through ``update_snapshot`` (cached first,
then fresh), so the screen renders instantly offline and refreshes itself.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Callable, Optional

# Patch path for direct execution / Flet preview compatibility
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import flet as ft
import platform_utils
from core.student_profile import (
    OtherFeeRow,
    ProfileSnapshot,
    TuitionRow,
    summarize_tuition,
)
from gui.core.theme import C

logger = logging.getLogger(__name__)

TAB_TUITION = "tuition"
TAB_OTHER = "other"

_STATUS_LABELS = {
    "paid": "Đã đóng",
    "debt": "Còn nợ",
    "unpaid": "Chưa đóng",
}


def format_vnd(amount: int) -> str:
    """``4500000`` → ``4.500.000 ₫`` (Vietnamese thousand separators)."""
    try:
        value = int(amount)
    except (TypeError, ValueError):
        value = 0
    return f"{value:,}".replace(",", ".") + " ₫"


def short_semester(name: str) -> str:
    """``Học kỳ 1 năm học 2025-2026`` → ``HK1 2025-2026`` for compact pills."""
    text = (name or "").replace("năm học ", " ")
    text = re.sub(r"\s+", " ", text).strip()
    shortened = re.sub(r"Học kỳ\s*(\d)", r"HK\1", text)
    if shortened == text:
        shortened = text.replace("Học kỳ", "HK")
    return shortened.strip()


def initials_of(full_name: str, fallback: str = "") -> str:
    """Avatar letters: first letter of first + last word (``Nguyễn Văn An`` → ``NA``)."""
    words = (full_name or "").split()
    if not words:
        return (fallback or "?")[:2].upper()
    if len(words) == 1:
        return words[0][:1].upper()
    return (words[0][:1] + words[-1][:1]).upper()


class ProfileView(ft.Container):
    """Hồ sơ & Học phí screen; the controller pushes snapshots into it."""

    def __init__(
        self,
        *,
        on_close: Optional[Callable] = None,
        on_refresh: Optional[Callable] = None,
        page: Optional[ft.Page] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._on_close = on_close
        self._on_refresh = on_refresh
        self._page_ref = page

        self._snapshot: ProfileSnapshot | None = None
        self._from_cache = False
        # Newest semester once a snapshot arrives; ``""`` when there are none.
        self._selected_semester: str = ""
        self._active_tab = TAB_TUITION

        # ---- Header -------------------------------------------------------
        btn_constraints = ft.BoxConstraints(
            min_width=44, max_width=44, min_height=44, max_height=44
        )
        self._back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Quay lại",
            padding=0,
            size_constraints=btn_constraints,
            on_click=self._handle_close,
        )
        self._title = ft.Text(
            "Hồ sơ & Học phí",
            size=16,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY,
            text_align=ft.TextAlign.CENTER,
            expand=True,
        )
        self._refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Làm mới từ Portal",
            padding=0,
            size_constraints=btn_constraints,
            on_click=self._handle_refresh,
        )
        self._header = ft.Row(
            controls=[
                ft.Semantics(label="Quay lại", button=True, content=self._back_btn),
                self._title,
                ft.Semantics(
                    label="Làm mới từ Portal",
                    button=True,
                    content=self._refresh_btn,
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # ---- Offline / stale notice ---------------------------------------
        self._notice_text = ft.Text("", size=12, color=C.TEXT_SECONDARY)
        self._stale_banner = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.CLOUD_OFF_ROUNDED, size=15, color=C.CRITICAL),
                    self._notice_text,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border_radius=10,
            border=ft.Border.all(1, C.CRITICAL),
            bgcolor=ft.Colors.with_opacity(0.08, C.CRITICAL),
            visible=False,
        )

        # ---- Loading / error / empty states -------------------------------
        self._loading_text = ft.Text(
            "Đang tải hồ sơ & học phí...", size=13, color=C.TEXT_SECONDARY
        )
        self._loading_row = ft.Row(
            controls=[
                ft.ProgressRing(width=24, height=24, stroke_width=3, color=C.ACCENT),
                self._loading_text,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            visible=False,
        )
        self._error_text = ft.Text(
            "Không tải được hồ sơ từ Portal.\nKiểm tra kết nối/tài khoản rồi thử lại.",
            size=13,
            color=C.TEXT_SECONDARY,
            text_align=ft.TextAlign.CENTER,
        )
        self._error_retry_btn = ft.Button(
            "Thử lại",
            icon=ft.Icons.REFRESH_ROUNDED,
            bgcolor=C.ACCENT,
            color=C.ON_ACCENT,
            height=44,
            on_click=self._handle_refresh,
        )
        self._error_state = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Icon(ft.Icons.CLOUD_OFF_ROUNDED, size=36, color=C.CRITICAL),
                    self._error_text,
                    self._error_retry_btn,
                ],
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            ),
            alignment=ft.Alignment(0, 0),
            padding=32,
            visible=False,
        )
        self._empty_text = ft.Text(
            "Chưa có dữ liệu học phí.\nDữ liệu sẽ xuất hiện sau lần đồng bộ Portal đầu tiên.",
            size=13,
            color=C.TEXT_SECONDARY,
            text_align=ft.TextAlign.CENTER,
        )
        self._empty_state = ft.Container(
            content=self._empty_text,
            alignment=ft.Alignment(0, 0),
            padding=40,
            visible=False,
        )

        # ---- Data host (rebuilt from the snapshot) ------------------------
        self._data_host = ft.Container(content=None, visible=False)

        # Populated by _render_data(); kept as fields for tests/theme refresh.
        self._avatar: ft.Container | None = None
        self._avatar_text: ft.Text | None = None
        # Portal photo control is built once and reused: re-sending the ~200 KB
        # base64 payload on every pill/tab re-render would lag the screen.
        self._avatar_image: ft.Image | None = None
        self._avatar_src: str = ""
        self._name_text: ft.Text | None = None
        self._sid_text: ft.Text | None = None
        self._info_column: ft.Column | None = None
        self._stat_total: ft.Text | None = None
        self._stat_paid: ft.Text | None = None
        self._stat_debt: ft.Text | None = None
        self._status_pill: ft.Container | None = None
        self._status_text: ft.Text | None = None
        self._pills_row: ft.Row | None = None
        self._tab_tuition: ft.Container | None = None
        self._tab_other: ft.Container | None = None
        self._table: ft.DataTable | None = None
        self._table_host: ft.Container | None = None
        # Opened fee-detail bottom sheet (mobile); None when closed.
        self._detail_sheet: ft.BottomSheet | None = None
        self._updated_text: ft.Text | None = None

        self.content = ft.Column(
            controls=[
                self._header,
                self._stale_banner,
                self._loading_row,
                self._error_state,
                self._empty_state,
                self._data_host,
            ],
            spacing=10,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )

    # ------------------------------------------------------------------ #
    # Public state API (driven by ViewManager / controller)
    # ------------------------------------------------------------------ #
    def show_loading(self) -> None:
        """Show the spinner only when there is no snapshot to keep on screen."""
        if self._snapshot is None:
            self._loading_row.visible = True
            self._data_host.visible = False
            self._empty_state.visible = False
            self._error_state.visible = False
            self._stale_banner.visible = False
            self._update()

    def update_snapshot(
        self, snapshot: ProfileSnapshot, *, from_cache: bool = False
    ) -> None:
        """Render the given snapshot; a fresh fetch clears the stale notice."""
        self._snapshot = snapshot
        self._from_cache = from_cache
        self._loading_row.visible = False
        self._error_state.visible = False
        if not from_cache:
            self.set_stale_notice(None)
        self._render_data()
        self._update()

    def show_error(self, message: str = "") -> None:
        """Error state — only used when nothing (not even cache) is available."""
        if message:
            self._error_text.value = message
        self._loading_row.visible = False
        self._data_host.visible = False
        self._empty_state.visible = False
        self._stale_banner.visible = False
        self._error_state.visible = True
        self._update()

    def set_stale_notice(self, message: Optional[str]) -> None:
        """Show/hide the offline banner above the data."""
        if message:
            self._notice_text.value = message
            self._stale_banner.visible = True
        else:
            self._stale_banner.visible = False

    def update_theme(self) -> None:
        """Re-read C tokens (colors are captured when controls are built)."""
        self._back_btn.icon_color = C.TEXT_SECONDARY
        self._refresh_btn.icon_color = C.TEXT_SECONDARY
        self._title.color = C.TEXT_PRIMARY
        self._loading_text.color = C.TEXT_SECONDARY
        self._error_text.color = C.TEXT_SECONDARY
        self._error_retry_btn.bgcolor = C.ACCENT
        self._error_retry_btn.color = C.ON_ACCENT
        self._empty_text.color = C.TEXT_SECONDARY
        self._notice_text.color = C.TEXT_SECONDARY
        if self._snapshot is not None:
            self._render_data()
        self._update()

    # Interaction -------------------------------------------------------
    def select_semester(self, key: str) -> None:
        """Filter rows to one semester (a pill tap re-renders the tables)."""
        if key == self._selected_semester:
            return
        self._selected_semester = key
        self._render_data()
        self._update()

    def select_tab(self, key: str) -> None:
        """Switch between the tuition and other-fee tables."""
        if key not in (TAB_TUITION, TAB_OTHER):
            return
        if key == self._active_tab:
            return
        self._active_tab = key
        self._render_data()
        self._update()

    def _handle_close(self, _event=None) -> None:
        if callable(self._on_close):
            self._on_close()

    def _handle_refresh(self, _event=None) -> None:
        if callable(self._on_refresh):
            self._on_refresh()

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def _filtered_rows(self) -> tuple:
        if self._snapshot is None:
            return ()
        if self._active_tab == TAB_OTHER:
            # Other fees are not bound to a semester: keep them all visible.
            return self._snapshot.other_fees
        return tuple(
            row
            for row in self._snapshot.tuition_rows
            if row.semester == self._selected_semester
        )

    @staticmethod
    def _ordered_semester_names(snapshot: ProfileSnapshot) -> list[str]:
        """Semesters holding tuition rows, newest first (Portal dropdown order)."""
        seen: list[str] = []
        for row in snapshot.tuition_rows:
            if row.semester and row.semester not in seen:
                seen.append(row.semester)
        known = [ref.name for ref in snapshot.semesters]
        return [name for name in known if name in seen] + [
            name for name in seen if name not in known
        ]

    def _ensure_selected_semester(self, snapshot: ProfileSnapshot) -> None:
        """Default to the newest semester whenever the selection is invalid."""
        ordered = self._ordered_semester_names(snapshot)
        if self._selected_semester not in ordered:
            self._selected_semester = ordered[0] if ordered else ""

    def _render_data(self) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        self._ensure_selected_semester(snapshot)
        controls: list[ft.Control] = [
            self._build_student_card(snapshot),
            self._build_summary(),
            self._build_pills(snapshot),
            self._build_tabs(),
            self._build_table(),
            self._build_updated_line(snapshot),
        ]
        self._data_host.content = ft.Column(
            controls=controls,
            spacing=12,
            tight=True,
        )
        self._data_host.visible = True
        has_content = bool(snapshot.tuition_rows or snapshot.other_fees)
        # The identity card alone is still useful content; only an entirely
        # empty Portal response (no profile, no fees) shows the empty state.
        if not has_content and not snapshot.profile.student_id:
            self._empty_state.visible = True
            self._data_host.visible = False
        else:
            self._empty_state.visible = False

    def _build_student_card(self, snapshot: ProfileSnapshot) -> ft.Container:
        profile = snapshot.profile
        avatar_src = (snapshot.avatar or "").strip()
        if avatar_src.startswith(("data:image/", "http://", "https://")):
            if self._avatar_image is None or self._avatar_src != avatar_src:
                self._avatar_image = ft.Image(
                    src=avatar_src,
                    width=56,
                    height=56,
                    fit=ft.BoxFit.COVER,
                    border_radius=28,
                    anti_alias=True,
                )
                self._avatar_src = avatar_src
            avatar_content: ft.Control = self._avatar_image
            self._avatar_text = None
        else:
            self._avatar_image = None
            self._avatar_src = ""
            self._avatar_text = ft.Text(
                initials_of(profile.full_name, profile.student_id),
                size=20,
                weight=ft.FontWeight.W_700,
                color=C.ON_ACCENT,
            )
            avatar_content = self._avatar_text
        self._avatar = ft.Container(
            content=avatar_content,
            width=56,
            height=56,
            border_radius=28,
            bgcolor=C.ACCENT,
            alignment=ft.Alignment(0, 0),
        )
        self._name_text = ft.Text(
            profile.full_name or "Chưa có dữ liệu sinh viên",
            size=18,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY,
        )
        self._sid_text = ft.Text(
            f"MSSV: {profile.student_id or '—'}",
            size=12.5,
            color=C.TEXT_SECONDARY,
        )

        info_rows = [
            ("Khoa", profile.faculty),
            ("Ngành", profile.major),
            ("Chuyên ngành", profile.specialization),
            ("Lớp", profile.class_name),
            ("Hệ đào tạo", profile.education_type),
            ("Khóa", profile.cohort),
            ("Ngày sinh", profile.birth_date),
            ("Nơi sinh", profile.hometown),
            ("Số điện thoại", profile.phone),
            ("Email", profile.email),
        ]
        self._info_column = ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Text(
                            label,
                            size=12.5,
                            weight=ft.FontWeight.W_600,
                            color=C.TEXT_SECONDARY,
                            width=118,
                            selectable=True,
                        ),
                        ft.Text(
                            value or "—",
                            size=12.5,
                            color=C.TEXT_PRIMARY,
                            expand=True,
                            selectable=True,
                        ),
                    ],
                    spacing=8,
                    tight=True,
                )
                for label, value in info_rows
            ],
            spacing=5,
            tight=True,
        )

        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            self._avatar,
                            ft.Column(
                                controls=[self._name_text, self._sid_text],
                                spacing=3,
                                tight=True,
                                expand=True,
                            ),
                        ],
                        spacing=14,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(height=6),
                    self._info_column,
                ],
                spacing=4,
                tight=True,
            ),
            padding=16,
            border_radius=14,
            border=ft.Border.all(1, C.BORDER),
            bgcolor=C.SURFACE,
        )

    def _build_summary(self) -> ft.Container:
        rows = self._filtered_rows()
        if self._active_tab == TAB_OTHER:
            totals = summarize_tuition([])
            debt = sum(getattr(row, "debt", 0) for row in rows)
            paid_amount = sum(getattr(row, "paid_amount", 0) for row in rows)
            amount = sum(getattr(row, "amount", 0) for row in rows)
            unpaid = sum(1 for row in rows if getattr(row, "status", "") != "paid")
        else:
            totals = summarize_tuition(rows)
            debt, paid_amount, amount = totals.debt, totals.paid_amount, totals.pay_level
            unpaid = totals.unpaid_rows

        self._stat_total = ft.Text(
            format_vnd(amount), size=15, weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY,
        )
        self._stat_paid = ft.Text(
            format_vnd(paid_amount), size=15, weight=ft.FontWeight.W_700,
            color=C.SAFE,
        )
        self._stat_debt = ft.Text(
            format_vnd(debt), size=15, weight=ft.FontWeight.W_700,
            color=C.CRITICAL if debt > 0 else C.TEXT_SECONDARY,
        )

        if debt > 0:
            status_label, status_color = f"Còn nợ {format_vnd(debt)}", C.CRITICAL
        elif not rows:
            status_label, status_color = "Không phát sinh", C.TEXT_SECONDARY
        elif unpaid == 0:
            status_label, status_color = "Đã đóng đủ", C.SAFE
        else:
            status_label, status_color = "Chưa đóng đủ", C.TEXT_SECONDARY

        self._status_text = ft.Text(
            status_label, size=13, weight=ft.FontWeight.W_700, color=status_color
        )
        self._status_pill = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(
                        ft.Icons.CHECK_CIRCLE_ROUNDED
                        if status_color == C.SAFE
                        else ft.Icons.ERROR_ROUNDED,
                        size=15,
                        color=status_color,
                    ),
                    self._status_text,
                ],
                spacing=6,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            border_radius=999,
            border=ft.Border.all(1, status_color),
            bgcolor=ft.Colors.with_opacity(0.10, status_color),
        )

        def stat(label: str, value_control: ft.Text) -> ft.Container:
            return ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(label, size=11, color=C.TEXT_SECONDARY),
                        value_control,
                    ],
                    spacing=2,
                    tight=True,
                ),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                border_radius=10,
                border=ft.Border.all(1, C.BORDER),
                bgcolor=C.SURFACE,
                expand=True,
            )

        return ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        stat("Tổng mức nộp", self._stat_total),
                        stat("Đã đóng", self._stat_paid),
                        stat("Còn nợ", self._stat_debt),
                    ],
                    spacing=8,
                    tight=True,
                ),
                ft.Row(
                    controls=[self._status_pill],
                    tight=True,
                ),
            ],
            spacing=8,
            tight=True,
        )

    def _build_pills(self, snapshot: ProfileSnapshot) -> ft.Container:
        """One pill per semester — the pill itself shows that semester's status."""
        rows = snapshot.tuition_rows
        ordered = self._ordered_semester_names(snapshot)

        pills: list[ft.Control] = [
            self._semester_pill(name, short_semester(name), name,
                                tuple(r for r in rows if r.semester == name))
            for name in ordered
        ]

        self._pills_row = ft.Row(
            controls=pills, spacing=8, tight=True, scroll=ft.ScrollMode.AUTO
        )
        return ft.Column(
            controls=[
                ft.Text("Học kỳ", size=12, weight=ft.FontWeight.W_600,
                        color=C.TEXT_SECONDARY),
                self._pills_row,
            ],
            spacing=6,
            tight=True,
        )

    def _semester_pill(
        self, key: str, label: str, semester_name: str, rows: tuple
    ) -> ft.Container:
        active = self._selected_semester == key
        totals = summarize_tuition(rows)
        if totals.debt > 0:
            status_color = C.CRITICAL
        elif rows and totals.unpaid_rows == 0:
            status_color = C.SAFE
        else:
            status_color = C.TEXT_SECONDARY
        text_color = C.ON_ACCENT if active else C.TEXT_PRIMARY
        return ft.Container(
            content=ft.Text(
                label,
                size=12,
                weight=ft.FontWeight.W_700 if active else ft.FontWeight.W_500,
                color=text_color,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=7),
            border_radius=999,
            border=ft.Border.all(1, status_color),
            bgcolor=status_color if active else ft.Colors.with_opacity(0.10, status_color),
            ink=True,
            tooltip=f"{semester_name} · {_pills_status_label(rows)}",
            on_click=lambda _e, pill_key=key: self.select_semester(pill_key),
        )

    def _build_tabs(self) -> ft.Row:
        self._tab_tuition = self._tab_button("Học phí", TAB_TUITION)
        self._tab_other = self._tab_button("Khoản thu khác", TAB_OTHER)
        return ft.Row(
            controls=[self._tab_tuition, self._tab_other],
            spacing=8,
            tight=True,
        )

    def _tab_button(self, label: str, key: str) -> ft.Container:
        active = self._active_tab == key
        return ft.Container(
            content=ft.Text(
                label,
                size=13,
                weight=ft.FontWeight.W_700,
                color=C.ON_ACCENT if active else C.TEXT_SECONDARY,
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=8),
            border_radius=10,
            bgcolor=C.ACCENT if active else C.SURFACE,
            border=None if active else ft.Border.all(1, C.BORDER),
            ink=True,
            on_click=lambda _e, tab_key=key: self.select_tab(tab_key),
        )

    def _build_table(self) -> ft.Control:
        if platform_utils.UI_IS_MOBILE:
            # Phone: a vertical tappable list — one tap opens the fee detail
            # sheet instead of swiping a wide table sideways like the Portal.
            return self._build_mobile_list()
        if self._active_tab == TAB_OTHER:
            table = self._build_other_fee_table()
        else:
            table = self._build_tuition_table()
        self._table = table
        self._table_host = ft.Container(
            content=ft.Row(
                controls=[table],
                scroll=ft.ScrollMode.AUTO,
                tight=True,
                # Center the table when it is narrower than the pane so the
                # right side is not left with a big empty gap.
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            border_radius=12,
            border=ft.Border.all(1, C.BORDER),
            bgcolor=C.SURFACE,
            padding=8,
        )
        return self._table_host

    # ------------------------------------------------------------------ #
    # Mobile fee list + detail bottom sheet
    # ------------------------------------------------------------------ #
    def _build_mobile_list(self) -> ft.Container:
        """Stacked fee cards for phones; no horizontal scrolling anywhere."""
        rows: list = list(self._filtered_rows())
        if self._active_tab == TAB_OTHER:
            empty_label = "Không có khoản thu khác."
        else:
            empty_label = "Không có học phí trong lựa chọn này."

        cards: list[ft.Control] = [self._mobile_fee_card(row) for row in rows]
        if not cards:
            cards = [
                ft.Container(
                    content=ft.Text(
                        empty_label, size=12, color=C.TEXT_SECONDARY
                    ),
                    alignment=ft.Alignment(0, 0),
                    padding=ft.Padding.symmetric(horizontal=12, vertical=14),
                )
            ]
        # Desktop-only DataTable stays None on phones; the summary card above
        # already shows the totals, so no extra totals row is needed here.
        self._table = None
        self._table_host = ft.Container(
            content=ft.Column(controls=cards, spacing=8, tight=True),
            border_radius=12,
            border=ft.Border.all(1, C.BORDER),
            bgcolor=C.SURFACE,
            padding=8,
        )
        return self._table_host

    def _mobile_fee_card(self, row: TuitionRow | OtherFeeRow) -> ft.Container:
        """One fee as a tappable card: title + code on the left, money on the right."""
        if isinstance(row, TuitionRow):
            title = row.subject or "—"
            subtitle = " · ".join(
                part
                for part in (
                    row.course_code or "",
                    f"{row.credits} TC" if row.credits else "",
                )
                if part
            )
            amount = row.tuition
        else:
            title = row.name or row.code or "—"
            subtitle = row.year or ""
            amount = row.amount
        amount_text = ft.Text(
            format_vnd(amount),
            size=13,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY,
            text_align=ft.TextAlign.RIGHT,
        )
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Column(
                        controls=[
                            ft.Text(
                                title,
                                size=13.5,
                                weight=ft.FontWeight.W_600,
                                color=C.TEXT_PRIMARY,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                subtitle or " ",
                                size=11.5,
                                color=C.TEXT_SECONDARY,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                        spacing=2,
                        tight=True,
                        expand=True,
                    ),
                    ft.Column(
                        controls=[amount_text, self._status_chip(row.status)],
                        spacing=4,
                        tight=True,
                        horizontal_alignment=ft.CrossAxisAlignment.END,
                    ),
                ],
                spacing=10,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            border_radius=10,
            border=ft.Border.all(1, C.BORDER),
            bgcolor=C.SURFACE_ELEVATED,
            ink=True,
            on_click=lambda _event, fee=row: self._open_fee_detail(fee),
        )

    def _open_fee_detail(self, row: TuitionRow | OtherFeeRow) -> None:
        """One-tap detail sheet: Số tiền · Đã nộp · Còn nợ · Trạng thái."""
        page = self._page_ref
        if page is None or not callable(getattr(page, "show_dialog", None)):
            return
        if self._detail_sheet is not None:
            try:
                page.pop_dialog()
            except Exception:  # noqa: BLE001 - already dismissed by the user
                logger.debug("Previous fee detail sheet was already closed")
            self._detail_sheet = None
        if isinstance(row, TuitionRow):
            title = row.subject or "—"
        else:
            title = row.name or row.code or "—"
        sheet = self._build_fee_detail_sheet(title, row)
        self._detail_sheet = sheet
        try:
            page.show_dialog(sheet)
        except Exception as exc:  # noqa: BLE001 - never break the tap
            logger.warning("Cannot open fee detail sheet: %s", exc)
            self._detail_sheet = None

    def _build_fee_detail_sheet(
        self, title: str, row: TuitionRow | OtherFeeRow
    ) -> ft.BottomSheet:
        pairs: list[tuple[str, str, str | None]] = []
        if isinstance(row, TuitionRow):
            pairs.extend(
                [
                    ("Mã LHP", row.course_code or "—", None),
                    ("Số tín chỉ", str(row.credits), None),
                    ("Học kỳ", short_semester(row.semester), None),
                    ("Số tiền", format_vnd(row.tuition), None),
                    ("Đã nộp", format_vnd(row.paid_amount), C.SAFE),
                    (
                        "Còn nợ",
                        format_vnd(row.debt),
                        C.CRITICAL if row.debt > 0 else C.TEXT_SECONDARY,
                    ),
                ]
            )
        else:
            pairs.extend(
                [
                    ("Năm học", row.year or "—", None),
                    ("Số tiền", format_vnd(row.amount), None),
                    ("Đã nộp", format_vnd(row.paid_amount), C.SAFE),
                    (
                        "Còn nợ",
                        format_vnd(row.debt),
                        C.CRITICAL if row.debt > 0 else C.TEXT_SECONDARY,
                    ),
                ]
            )
        controls: list[ft.Control] = [
            ft.Row(
                controls=[
                    ft.Text(
                        title,
                        size=16,
                        weight=ft.FontWeight.W_700,
                        color=C.TEXT_PRIMARY,
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        expand=True,
                    ),
                    self._status_chip(row.status),
                ],
                spacing=10,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ]
        controls.extend(
            ft.Row(
                controls=[
                    ft.Text(
                        label,
                        size=13,
                        color=C.TEXT_SECONDARY,
                        width=110,
                    ),
                    ft.Text(
                        value,
                        size=13.5,
                        weight=ft.FontWeight.W_700,
                        color=color or C.TEXT_PRIMARY,
                        expand=True,
                        text_align=ft.TextAlign.RIGHT,
                    ),
                ],
                spacing=8,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            for label, value, color in pairs
        )
        return ft.BottomSheet(
            content=ft.Container(
                content=ft.Column(
                    controls=controls,
                    spacing=10,
                    tight=True,
                    scroll=ft.ScrollMode.AUTO,
                ),
                bgcolor=C.BG,
                padding=ft.Padding.only(left=16, right=16, top=8, bottom=20),
            ),
            bgcolor=C.BG,
            dismissible=True,
            draggable=True,
            show_drag_handle=True,
            use_safe_area=True,
            scrollable=True,
            maintain_bottom_view_insets_padding=True,
            elevation=12,
            barrier_color=C.SCRIM,
            shape=ft.RoundedRectangleBorder(radius=18),
            animation_style=ft.AnimationStyle(
                duration=220,
                reverse_duration=180,
                curve=ft.AnimationCurve.EASE_OUT_CUBIC,
                reverse_curve=ft.AnimationCurve.EASE_IN_CUBIC,
            ),
            on_dismiss=lambda _event: setattr(self, "_detail_sheet", None),
        )

    @staticmethod
    def _header_cell(label: str, numeric: bool = False) -> ft.DataColumn:
        return ft.DataColumn(
            label=ft.Text(
                label,
                size=12,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
                no_wrap=False,
            ),
            numeric=numeric,
        )

    @staticmethod
    def _text_cell(value: str, *, color: str | None = None, bold: bool = False) -> ft.DataCell:
        return ft.DataCell(
            ft.Text(
                value,
                size=12,
                color=color or C.TEXT_PRIMARY,
                weight=ft.FontWeight.W_700 if bold else ft.FontWeight.W_400,
                selectable=True,
            )
        )

    @staticmethod
    def _status_chip(status: str) -> ft.Container:
        """Pill showing the fee status (shared by mobile cards and the sheet)."""
        color = {
            "paid": C.SAFE,
            "debt": C.CRITICAL,
            "unpaid": C.TEXT_SECONDARY,
        }.get(status, C.TEXT_SECONDARY)
        return ft.Container(
            content=ft.Text(
                _STATUS_LABELS.get(status, status),
                size=11,
                weight=ft.FontWeight.W_700,
                color=color,
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
            border_radius=999,
            border=ft.Border.all(1, color),
            bgcolor=ft.Colors.with_opacity(0.10, color),
        )

    @staticmethod
    def _status_cell(status: str) -> ft.DataCell:
        color = {
            "paid": C.SAFE,
            "debt": C.CRITICAL,
            "unpaid": C.TEXT_SECONDARY,
        }.get(status, C.TEXT_SECONDARY)
        return ft.DataCell(
            ft.Container(
                content=ft.Text(
                    _STATUS_LABELS.get(status, status),
                    size=11,
                    weight=ft.FontWeight.W_700,
                    color=color,
                ),
                padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                border_radius=999,
                border=ft.Border.all(1, color),
                bgcolor=ft.Colors.with_opacity(0.10, color),
            )
        )

    def _build_tuition_table(self) -> ft.DataTable:
        rows: list[TuitionRow] = list(self._filtered_rows())

        headers = ["#", "Mã LHP", "Môn học", "TC", "Học phí", "Đã nộp", "Còn nợ", "Trạng thái"]

        data_rows: list[ft.DataRow] = []
        for index, row in enumerate(rows, start=1):
            cells = [
                self._text_cell(str(index), color=C.TEXT_SECONDARY),
                self._text_cell(row.course_code or "—"),
                self._text_cell(row.subject or "—"),
                self._text_cell(str(row.credits)),
                self._text_cell(format_vnd(row.tuition)),
                self._text_cell(format_vnd(row.paid_amount), color=C.SAFE),
                self._text_cell(
                    format_vnd(row.debt),
                    color=C.CRITICAL if row.debt > 0 else C.TEXT_SECONDARY,
                ),
                self._status_cell(row.status),
            ]
            data_rows.append(ft.DataRow(cells=cells))

        if not rows:
            data_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(
                            ft.Text(
                                "Không có học phí trong lựa chọn này.",
                                size=12,
                                color=C.TEXT_SECONDARY,
                            )
                        )
                    ]
                )
            )
        else:
            totals = summarize_tuition(rows)
            total_cells = [
                ft.DataCell(
                    ft.Text(
                        "Tổng",
                        size=12.5,
                        weight=ft.FontWeight.W_700,
                        color=C.CRITICAL,
                    )
                ),
                ft.DataCell(ft.Text("", size=12)),
                ft.DataCell(ft.Text("", size=12)),
            ]
            total_cells += [
                ft.DataCell(
                    ft.Text(
                        str(totals.credits),
                        size=12.5,
                        weight=ft.FontWeight.W_700,
                        color=C.CRITICAL,
                    )
                ),
                ft.DataCell(
                    ft.Text(
                        format_vnd(totals.tuition),
                        size=12.5,
                        weight=ft.FontWeight.W_700,
                        color=C.CRITICAL,
                    )
                ),
                ft.DataCell(
                    ft.Text(
                        format_vnd(totals.paid_amount),
                        size=12.5,
                        weight=ft.FontWeight.W_700,
                        color=C.CRITICAL,
                    )
                ),
                ft.DataCell(
                    ft.Text(
                        format_vnd(totals.debt),
                        size=12.5,
                        weight=ft.FontWeight.W_700,
                        color=C.CRITICAL,
                    )
                ),
                ft.DataCell(ft.Text("", size=12)),
            ]
            data_rows.append(ft.DataRow(cells=total_cells))

        return ft.DataTable(
            columns=[
                self._header_cell(label, numeric=label in {"TC", "Học phí", "Đã nộp", "Còn nợ"})
                for label in headers
            ],
            rows=data_rows,
            heading_row_color=C.SURFACE_ELEVATED,
            border=ft.Border.all(1, C.BORDER),
            border_radius=8,
            horizontal_lines=ft.BorderSide(1, C.BORDER),
            vertical_lines=ft.BorderSide(1, C.BORDER),
            column_spacing=16,
            show_checkbox_column=False,
        )

    def _build_other_fee_table(self) -> ft.DataTable:
        rows: list[OtherFeeRow] = list(self._filtered_rows())

        headers = ["#", "Năm học", "Khoản thu", "Mức nộp", "Đã nộp", "Còn nợ", "Trạng thái"]
        data_rows: list[ft.DataRow] = []
        for index, row in enumerate(rows, start=1):
            data_rows.append(
                ft.DataRow(
                    cells=[
                        self._text_cell(str(index), color=C.TEXT_SECONDARY),
                        self._text_cell(row.year or "—"),
                        self._text_cell(
                            row.name or row.code or "—"
                        ),
                        self._text_cell(format_vnd(row.amount)),
                        self._text_cell(format_vnd(row.paid_amount), color=C.SAFE),
                        self._text_cell(
                            format_vnd(row.debt),
                            color=C.CRITICAL if row.debt > 0 else C.TEXT_SECONDARY,
                        ),
                        self._status_cell(row.status),
                    ]
                )
            )

        if not rows:
            data_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(
                            ft.Text(
                                "Không có khoản thu khác.",
                                size=12,
                                color=C.TEXT_SECONDARY,
                            )
                        )
                    ]
                )
            )
        else:
            amount = sum(row.amount for row in rows)
            paid = sum(row.paid_amount for row in rows)
            debt = sum(row.debt for row in rows)
            data_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(
                            ft.Text(
                                "Tổng",
                                size=12.5,
                                weight=ft.FontWeight.W_700,
                                color=C.CRITICAL,
                            )
                        ),
                        ft.DataCell(ft.Text("", size=12)),
                        ft.DataCell(ft.Text("", size=12)),
                        ft.DataCell(
                            ft.Text(
                                format_vnd(amount),
                                size=12.5,
                                weight=ft.FontWeight.W_700,
                                color=C.CRITICAL,
                            )
                        ),
                        ft.DataCell(
                            ft.Text(
                                format_vnd(paid),
                                size=12.5,
                                weight=ft.FontWeight.W_700,
                                color=C.CRITICAL,
                            )
                        ),
                        ft.DataCell(
                            ft.Text(
                                format_vnd(debt),
                                size=12.5,
                                weight=ft.FontWeight.W_700,
                                color=C.CRITICAL,
                            )
                        ),
                        ft.DataCell(ft.Text("", size=12)),
                    ]
                )
            )

        return ft.DataTable(
            columns=[
                self._header_cell(
                    label, numeric=label in {"Mức nộp", "Đã nộp", "Còn nợ"}
                )
                for label in headers
            ],
            rows=data_rows,
            heading_row_color=C.SURFACE_ELEVATED,
            border=ft.Border.all(1, C.BORDER),
            border_radius=8,
            horizontal_lines=ft.BorderSide(1, C.BORDER),
            vertical_lines=ft.BorderSide(1, C.BORDER),
            column_spacing=16,
            show_checkbox_column=False,
        )

    def _build_updated_line(self, snapshot: ProfileSnapshot) -> ft.Text:
        fetched = snapshot.fetched_at
        origin = "bản lưu ngoại tuyến" if self._from_cache else "Portal"
        stamp = fetched.strftime("%H:%M %d/%m/%Y")
        self._updated_text = ft.Text(
            f"Cập nhật {stamp} · {origin}",
            size=11,
            color=C.TEXT_SECONDARY,
        )
        return self._updated_text

    def _update(self) -> None:
        try:
            self.update()
        except Exception:
            pass


def _pills_status_label(rows: tuple) -> str:
    """Tooltip status text for one semester's rows."""
    totals = summarize_tuition(rows)
    if not rows:
        return "Không phát sinh"
    if totals.debt > 0:
        return f"Còn nợ {format_vnd(totals.debt)}"
    if totals.unpaid_rows == 0:
        return "Đã đóng"
    return "Chưa đóng"
