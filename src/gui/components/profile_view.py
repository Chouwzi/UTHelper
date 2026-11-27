"""Profile & Tuition View - student identity card and fee lookup tables.

Layout (single screen, minimal taps — no drill-down), mirrors the approved
desktop / phone mockups:

Desktop
  1. Page title "Hồ sơ & Học phí" + subtitle, refresh at the right.
  2. Student card: a full-width UTH campus banner behind the avatar, name,
     MSSV and "Hệ đào tạo" badge, followed by a 2-column info grid.
  3. Money summary: 4 equal-height tiles (tổng mức nộp · đã đóng · còn nợ ·
     status tile) — the status tile is the same height as the others.
  4. "Học kỳ" heading + one pill per semester (newest selected by default).
  5. Tabs: Học phí (per subject) / Khoản thu khác.
  6. Horizontally centered table with a totals row.

Phone (``UI_IS_MOBILE``)
  Compact header (back · centered title), compact profile card with a
  tappable avatar that opens student details in a popup, 2×2 summary tiles,
  a semester *dropdown* (scrollable bottom-sheet picker), segmented tabs,
  one tappable card per fee (detail bottom sheet) and a sticky "Tổng:" bar.

Data arrives from the controller through ``update_snapshot`` (cached first,
then fresh), so the screen renders instantly offline and refreshes itself.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
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


@lru_cache(maxsize=4)
def _asset_image_source(filename: str, mime_type: str) -> str:
    """Read an image as a data URL so Flet does not depend on asset URL routing."""
    candidates: list[Path] = []
    candidates.append(
        Path(__file__).resolve().parents[2] / "assets" / filename
    )
    active_assets = os.environ.get("FLET_ASSETS_DIR")
    if active_assets:
        candidates.append(Path(active_assets) / filename)
    for asset_path in candidates:
        try:
            if asset_path.is_file():
                encoded = base64.b64encode(asset_path.read_bytes()).decode("ascii")
                return f"data:{mime_type};base64,{encoded}"
        except OSError:
            logger.debug("Cannot read image asset: %s", asset_path, exc_info=True)
    # Keep the standard Flet asset route as a fallback for packaged builds.
    return filename


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


def _info_row(icon, label: str, value: str, *, desktop: bool = False) -> ft.Row:
    """One ``icon · Label: value`` line — the profile card's info grid row."""
    return ft.Row(
        controls=[
            ft.Icon(
                icon,
                size=16 if desktop else 15,
                color=C.ACCENT,
            ),
            ft.Text(
                f"{label}:",
                size=12.5,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
                # Desktop mockup: a wide label column so every value lines up
                # like the reference; the phone detail sheet stays compact.
                width=170 if desktop else 112,
                selectable=True,
            ),
            ft.Text(
                value or "—",
                size=12.5,
                color=C.TEXT_PRIMARY,
                expand=True,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                selectable=True,
            ),
        ],
        spacing=8,
        tight=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


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
        # Desktop mockup: big page title with a subtitle underneath.
        self._subtitle = ft.Text(
            "Thông tin sinh viên và tình trạng đóng học phí",
            size=13,
            color=C.TEXT_SECONDARY,
        )
        if platform_utils.UI_IS_MOBILE:
            # Phone mockup: back arrow left, title centered.
            self._title = ft.Text(
                "Hồ sơ & Học phí",
                size=16,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
                text_align=ft.TextAlign.CENTER,
                expand=True,
            )
            title_block: ft.Control = self._title
        else:
            self._title = ft.Text(
                "Hồ sơ & Học phí",
                size=22,
                weight=ft.FontWeight.W_800,
                color=C.TEXT_PRIMARY,
            )
            title_block = ft.Column(
                controls=[self._title, self._subtitle],
                spacing=2,
                tight=True,
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
        # Desktop hides "back" (the nav rail provides it) and must not wrap an
        # invisible control in ft.Semantics — Flet rejects that combination.
        # The button stays wired for tests but only joins the Row on phones.
        back_control: ft.Control | None = None
        if platform_utils.UI_IS_MOBILE:
            back_control = ft.Semantics(
                label="Quay lại", button=True, content=self._back_btn
            )
        else:
            self._back_btn.visible = False
        header_controls: list[ft.Control] = []
        if back_control is not None:
            header_controls.append(back_control)
        header_controls.extend(
            [
                title_block,
                ft.Semantics(
                    label="Làm mới từ Portal",
                    button=True,
                    content=self._refresh_btn,
                ),
            ]
        )
        self._header = ft.Row(
            controls=header_controls,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
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

        # ---- Student card + scrollable data host --------------------------
        # Keep identity outside the changing scroll content. This prevents
        # Flutter's scroll-column diff/layout pass from moving or reattaching
        # the avatar when the selected semester or fee tab changes.
        self._student_card_host = ft.Container(
            width=float("inf"), visible=False, key="profile-student-card-host"
        )
        self._data_host = ft.Column(
            controls=[],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            visible=False,
        )

        # Populated by _render_data(); kept as fields for tests/theme refresh.
        self._avatar: ft.Container | None = None
        self._avatar_text: ft.Text | None = None
        # Portal photo control is built once and reused: re-sending the ~200 KB
        # base64 payload on every pill/tab re-render would lag the screen.
        self._avatar_image: ft.Image | None = None
        self._avatar_src: str = ""
        self._student_card: ft.Container | None = None
        self._student_card_key: tuple[object, ...] | None = None
        self._name_text: ft.Text | None = None
        self._sid_text: ft.Text | None = None
        self._info_column: ft.Control | None = None
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
        self._table_host_width: float | None = None
        # Opened fee-detail bottom sheet (mobile); None when closed.
        self._detail_sheet: ft.BottomSheet | None = None
        # Mobile profile detail and semester picker sheets.
        self._semester_sheet: ft.BottomSheet | None = None
        self._profile_sheet: ft.BottomSheet | None = None
        self._updated_text: ft.Text | None = None
        # Sticky mobile total bar (mockup): "Tổng: …" under the fee cards.
        self._footer_text: ft.Text | None = None
        self._footer = self._build_footer()

        self.content = ft.Column(
            controls=[
                self._header,
                self._stale_banner,
                self._loading_row,
                self._error_state,
                self._empty_state,
                self._student_card_host,
                self._data_host,
                self._footer,
            ],
            spacing=10,
            expand=True,
        )

    # ------------------------------------------------------------------ #
    # Public state API (driven by ViewManager / controller)
    # ------------------------------------------------------------------ #
    def show_loading(self) -> None:
        """Show the spinner only when there is no snapshot to keep on screen."""
        if self._snapshot is None:
            self._loading_row.visible = True
            self._student_card_host.visible = False
            self._data_host.visible = False
            self._empty_state.visible = False
            self._error_state.visible = False
            self._stale_banner.visible = False
            self._sync_footer()
            self._update()

    def clear(self) -> None:
        """Remove all account-owned profile data from controls and dialogs."""
        self._snapshot = None
        self._from_cache = False
        self._selected_semester = ""
        self._student_card_host.content = None
        self._student_card_host.visible = False
        self._data_host.controls = []
        self._data_host.visible = False
        self._loading_row.visible = False
        self._error_state.visible = False
        self._empty_state.visible = False
        self._stale_banner.visible = False
        self._footer.visible = False
        self._avatar = None
        self._avatar_text = None
        self._avatar_image = None
        self._avatar_src = ""
        self._student_card = None
        self._student_card_key = None
        for attr in ("_profile_sheet", "_detail_sheet", "_semester_sheet"):
            sheet = getattr(self, attr, None)
            if sheet is not None and self._page_ref is not None:
                try:
                    self._page_ref.pop_dialog()
                except Exception:  # noqa: BLE001 - dialog may already be closed
                    logger.debug("Profile dialog already closed during clear")
            setattr(self, attr, None)
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
        if self._snapshot is None:
            self._student_card_host.visible = False
        self._data_host.visible = False
        self._empty_state.visible = False
        self._stale_banner.visible = False
        self._error_state.visible = True
        self._sync_footer()
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
        self._subtitle.color = C.TEXT_SECONDARY
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
        self._update_data_area()

    def select_tab(self, key: str) -> None:
        """Switch between the tuition and other-fee tables."""
        if key not in (TAB_TUITION, TAB_OTHER):
            return
        if key == self._active_tab:
            return
        self._active_tab = key
        self._render_data()
        self._update_data_area()

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
        student_card = self._build_student_card(snapshot)
        # Set the identity card only when its actual identity data changes.
        # Normal semester/tab changes leave both this host and the avatar
        # mounted in exactly the same place in the control tree.
        if self._student_card_host.content is not student_card:
            self._student_card_host.content = student_card
        self._student_card_host.visible = True

        data_controls: list[ft.Control] = [
            self._build_summary(),
            self._build_semester_block(snapshot),
            self._build_tabs(),
            self._build_table(),
            self._build_updated_line(snapshot),
        ]
        self._data_host.controls = data_controls
        self._data_host.visible = True
        self._update_footer()
        self._sync_footer()
        has_content = bool(snapshot.tuition_rows or snapshot.other_fees)
        # The identity card alone is still useful content; only an entirely
        # empty Portal response (no profile, no fees) shows the empty state.
        if not has_content and not snapshot.profile.student_id:
            self._empty_state.visible = True
            self._student_card_host.visible = False
            self._data_host.visible = False
            self._sync_footer()
        else:
            self._empty_state.visible = False
            self._student_card_host.visible = True

    def _build_student_card(self, snapshot: ProfileSnapshot) -> ft.Container:
        profile = snapshot.profile
        mobile = platform_utils.UI_IS_MOBILE
        avatar_src = (snapshot.avatar or "").strip()
        card_key = (profile, avatar_src, mobile)
        if self._student_card is not None and self._student_card_key == card_key:
            return self._student_card

        # Mockup proportion: the photo circle covers ~57% of the 188px banner
        # (148px made it nearly touch the banner edges).
        avatar_size = 52 if mobile else 112
        if avatar_src.startswith(("data:image/", "http://", "https://")):
            if self._avatar_image is None or self._avatar_src != avatar_src:
                self._avatar_image = ft.Image(
                    src=avatar_src,
                    width=avatar_size,
                    height=avatar_size,
                    fit=ft.BoxFit.COVER,
                    border_radius=avatar_size // 2,
                    anti_alias=True,
                    gapless_playback=True,
                    key="profile-student-avatar-image",
                )
                self._avatar_src = avatar_src
            avatar_content: ft.Control = self._avatar_image
            self._avatar_text = None
        else:
            self._avatar_image = None
            self._avatar_src = ""
            if self._avatar_text is None:
                self._avatar_text = ft.Text(
                    initials_of(profile.full_name, profile.student_id),
                    size=18 if mobile else 26,
                    weight=ft.FontWeight.W_700,
                    color=C.ON_ACCENT,
                )
            else:
                self._avatar_text.value = initials_of(
                    profile.full_name, profile.student_id
                )
                self._avatar_text.size = 18 if mobile else 26
                self._avatar_text.color = C.ON_ACCENT
            avatar_content = self._avatar_text
        # The mockup shows a white gap between the light ring and the photo.
        photo_mode = (
            self._avatar_image is not None and avatar_content is self._avatar_image
        )
        if self._avatar is None:
            self._avatar = ft.Container(
                content=avatar_content,
                width=avatar_size,
                height=avatar_size,
                border_radius=avatar_size // 2,
                bgcolor=(
                    C.SURFACE if (photo_mode and not mobile) else C.ACCENT
                ),
                padding=(
                    ft.Padding.all(3) if (photo_mode and not mobile) else None
                ),
                alignment=ft.Alignment(0, 0),
                key="profile-student-avatar",
                border=(
                    ft.Border.all(3, ft.Colors.with_opacity(0.30, C.ACCENT))
                    if not mobile
                    else None
                ),
                # No glow: the mockup keeps a clean light ring around the photo.
                shadow=None,
            )
        else:
            # Keep the same outer control mounted during semester/tab refreshes
            # so the avatar does not flash or jump as the profile is rebuilt.
            self._avatar.content = avatar_content
            self._avatar.width = avatar_size
            self._avatar.height = avatar_size
            self._avatar.border_radius = avatar_size // 2
            self._avatar.bgcolor = (
                C.SURFACE if (photo_mode and not mobile) else C.ACCENT
            )
            self._avatar.padding = (
                ft.Padding.all(3) if (photo_mode and not mobile) else None
            )
            self._avatar.alignment = ft.Alignment(0, 0)
            self._avatar.border = (
                ft.Border.all(3, ft.Colors.with_opacity(0.30, C.ACCENT))
                if not mobile
                else None
            )
            self._avatar.shadow = None
        self._name_text = ft.Text(
            profile.full_name or "Chưa có dữ liệu sinh viên",
            size=15 if mobile else 21,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._sid_text = ft.Text(
            f"MSSV: {profile.student_id or '—'}",
            size=12 if mobile else 13,
            color=C.TEXT_SECONDARY,
        )
        # "Hệ đào tạo" becomes the blue badge from the mockups.
        badge = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SCHOOL_ROUNDED, size=14, color=C.ACCENT),
                    ft.Text(
                        profile.education_type or "Chưa rõ hệ đào tạo",
                        size=12,
                        weight=ft.FontWeight.W_600,
                        color=C.ACCENT,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=5,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            border_radius=999,
            border=ft.Border.all(1, ft.Colors.with_opacity(0.40, C.ACCENT)),
            bgcolor=ft.Colors.with_opacity(0.10, C.ACCENT),
        )

        left_items = [
            # Icon set mirrors the desktop mockup: bank / layers / bookmark /
            # people (was briefcase / book / grid).
            (ft.Icons.ACCOUNT_BALANCE_ROUNDED, "Khoa", profile.faculty),
            (ft.Icons.LAYERS_ROUNDED, "Ngành", profile.major),
            (ft.Icons.BOOKMARK_ROUNDED, "Chuyên ngành", profile.specialization),
            (ft.Icons.GROUP_ROUNDED, "Lớp", profile.class_name),
        ]
        right_items = [
            (ft.Icons.CALENDAR_TODAY_ROUNDED, "Khóa", profile.cohort),
            (ft.Icons.CAKE_ROUNDED, "Ngày sinh", profile.birth_date),
            (ft.Icons.LOCATION_ON_ROUNDED, "Nơi sinh", profile.hometown),
            (ft.Icons.PHONE_ROUNDED, "Số điện thoại", profile.phone),
            (ft.Icons.EMAIL_ROUNDED, "Email", profile.email),
        ]
        if mobile:
            # Tap the avatar (or its trailing chevron) to see details in a
            # popup; keep the compact student card at a fixed height.
            self._avatar.ink = True
            self._avatar.on_click = self._open_student_details
            identity = ft.Row(
                controls=[
                    self._avatar,
                    ft.Column(
                        controls=[self._name_text, self._sid_text, badge],
                        spacing=5,
                        tight=True,
                        expand=True,
                    ),
                    ft.Container(
                        content=ft.Icon(
                            ft.Icons.CHEVRON_RIGHT_ROUNDED,
                            size=22,
                            color=C.TEXT_SECONDARY,
                        ),
                        ink=True,
                        on_click=self._open_student_details,
                    ),
                ],
                spacing=12,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            self._student_card = ft.Container(
                content=identity,
                padding=ft.Padding.symmetric(horizontal=14, vertical=12),
                border_radius=14,
                border=ft.Border.all(1, C.BORDER),
                bgcolor=C.SURFACE,
            )
            self._student_card_key = card_key
            return self._student_card

        # Desktop: run the campus panorama across the header behind the student
        # identity, matching the wide profile-card mockup.
        identity = ft.Row(
            controls=[
                self._avatar,
                ft.Column(
                    controls=[self._name_text, self._sid_text, badge],
                    spacing=8,
                    tight=True,
                    expand=True,
                ),
                ft.Container(expand=True),
            ],
            spacing=16,
            expand=True,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        # A slightly taller banner makes room for the whole campus photo while
        # extending it towards the student name without distorting its ratio.
        banner_height = 235
        campus_width = round(banner_height * 845 / 415)
        campus_height = banner_height
        banner = ft.Container(
            content=ft.Stack(
                controls=[
                    ft.Container(
                        content=ft.Image(
                            src=_asset_image_source(
                                "uth_profile_campus.jpg", "image/jpeg"
                            ),
                            width=campus_width,
                            height=campus_height,
                            fit=ft.BoxFit.CONTAIN,
                            filter_quality=ft.FilterQuality.HIGH,
                            anti_alias=True,
                            semantics_label="Khuôn viên UTH",
                            key="profile-uth-banner-image",
                        ),
                        width=campus_width,
                        height=campus_height,
                        right=0,
                        top=0,
                    ),
                    ft.Container(
                        width=campus_width,
                        right=0,
                        top=0,
                        bottom=0,
                        gradient=ft.LinearGradient(
                            begin=ft.Alignment(-1, 0),
                            end=ft.Alignment(1, 0),
                            colors=[
                                C.SURFACE,
                                ft.Colors.with_opacity(0.65, C.SURFACE),
                                ft.Colors.with_opacity(0, C.SURFACE),
                            ],
                            stops=[0, 0.16, 0.48],
                        ),
                    ),
                    ft.Container(
                        content=identity,
                        padding=ft.Padding.symmetric(horizontal=36, vertical=12),
                        alignment=ft.Alignment(-1, 0),
                        left=0,
                        right=0,
                        top=0,
                        bottom=0,
                    ),
                    ft.Container(
                        content=ft.Image(
                            src=_asset_image_source(
                                "uth_profile_wordmark.png", "image/png"
                            ),
                            fit=ft.BoxFit.CONTAIN,
                            semantics_label="UTH",
                        ),
                        width=168,
                        height=50,
                        right=10,
                        top=8,
                        border_radius=25,
                        bgcolor=C.SURFACE,
                        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                    ),
                ],
                height=banner_height,
                width=float("inf"),
                expand=True,
                fit=ft.StackFit.EXPAND,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            ),
            height=banner_height,
            width=float("inf"),
            bgcolor=C.SURFACE,
            expand=True,
            # Flush with the outer card: the parent's rounded clip shapes the
            # corners so the banner and the info grid read as one card.
            border_radius=0,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )
        self._info_column = ft.Row(
            controls=[
                ft.Column(
                    controls=[_info_row(*item, desktop=True) for item in left_items],
                    spacing=14,
                    tight=True,
                    expand=True,
                ),
                ft.Container(
                    width=2,
                    height=150,
                    # Soft slate hairline like the mockup, not a dark bar.
                    bgcolor=ft.Colors.with_opacity(0.65, C.BORDER_STRONG),
                    margin=ft.Margin.symmetric(horizontal=22),
                ),
                ft.Column(
                    controls=[_info_row(*item, desktop=True) for item in right_items],
                    spacing=14,
                    tight=True,
                    expand=True,
                ),
            ],
            spacing=12,
            tight=True,
        )
        self._student_card = ft.Container(
            content=ft.Column(
                controls=[
                    banner,
                    # Edge-to-edge hairline between the campus header and the
                    # info grid — one card, as in the desktop mockup.
                    ft.Container(
                        height=1,
                        width=float("inf"),
                        bgcolor=C.BORDER,
                    ),
                    ft.Container(
                        content=self._info_column,
                        padding=ft.Padding.only(left=36, right=36, top=20, bottom=24),
                    ),
                ],
                spacing=0,
                tight=True,
            ),
            border_radius=16,
            border=ft.Border.all(1, C.BORDER),
            bgcolor=C.SURFACE,
            shadow=ft.BoxShadow(
                blur_radius=16,
                spread_radius=1,
                color=ft.Colors.with_opacity(0.10, C.ACCENT),
                offset=ft.Offset(0, 4),
            ),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            width=float("inf"),
        )
        self._student_card_key = card_key
        return self._student_card

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

        value_size = 14 if platform_utils.UI_IS_MOBILE else 16
        self._stat_total = ft.Text(
            format_vnd(amount), size=value_size, weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._stat_paid = ft.Text(
            format_vnd(paid_amount), size=value_size, weight=ft.FontWeight.W_700,
            color=C.SAFE, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._stat_debt = ft.Text(
            format_vnd(debt), size=value_size, weight=ft.FontWeight.W_700,
            color=C.CRITICAL if debt > 0 else C.TEXT_SECONDARY,
            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
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
            padding=ft.Padding.symmetric(horizontal=12, vertical=7),
            border_radius=999,
            border=ft.Border.all(1, status_color),
            bgcolor=ft.Colors.with_opacity(0.10, status_color),
        )

        # Every tile — including the status tile — shares the same height so
        # the row reads as four even boxes like the mockups.
        tile_height = 76

        def tinted_tile(
            icon,
            icon_color: str,
            label: str,
            value_control: ft.Text,
            tint_color: str,
        ) -> ft.Container:
            return ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Container(
                            content=ft.Icon(icon, size=18, color=icon_color),
                            width=36,
                            height=36,
                            border_radius=18,
                            bgcolor=ft.Colors.with_opacity(0.12, icon_color),
                            alignment=ft.Alignment(0, 0),
                        ),
                        ft.Column(
                            controls=[
                                ft.Text(label, size=11.5, color=C.TEXT_SECONDARY),
                                value_control,
                            ],
                            spacing=2,
                            tight=True,
                            expand=True,
                        ),
                    ],
                    spacing=10,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                border_radius=12,
                bgcolor=ft.Colors.with_opacity(0.08, tint_color),
                border=ft.Border.all(1, C.BORDER) if platform_utils.UI_IS_MOBILE else None,
                expand=True,
                height=tile_height,
                alignment=ft.Alignment(0, 0),
            )

        status_tile = ft.Container(
            content=ft.Row(
                controls=[self._status_pill],
                tight=True,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=10),
            border_radius=12,
            bgcolor=ft.Colors.with_opacity(0.08, status_color),
            border=ft.Border.all(1, C.BORDER) if platform_utils.UI_IS_MOBILE else None,
            expand=True,
            height=tile_height,
            alignment=ft.Alignment(0, 0),
        )

        total_tile = tinted_tile(
            ft.Icons.ACCOUNT_BALANCE_WALLET_ROUNDED, C.ACCENT,
            "Tổng mức nộp", self._stat_total, C.ACCENT,
        )
        paid_tile = tinted_tile(
            ft.Icons.CHECK_CIRCLE_ROUNDED, C.SAFE,
            "Đã đóng", self._stat_paid, C.SAFE,
        )
        debt_tile = tinted_tile(
            ft.Icons.SCHEDULE_ROUNDED, C.TEXT_SECONDARY,
            "Còn nợ", self._stat_debt, C.TEXT_SECONDARY,
        )

        if platform_utils.UI_IS_MOBILE:
            # Phone mockup: 2×2 grid with the status box beside "Còn nợ".
            return ft.Column(
                controls=[
                    ft.Row(
                        controls=[total_tile, paid_tile],
                        spacing=10,
                        tight=True,
                    ),
                    ft.Row(
                        controls=[debt_tile, status_tile],
                        spacing=10,
                        tight=True,
                    ),
                ],
                spacing=10,
                tight=True,
            )
        return ft.Row(
            controls=[total_tile, paid_tile, debt_tile, status_tile],
            spacing=10,
            tight=True,
        )

    def _build_semester_block(self, snapshot: ProfileSnapshot) -> ft.Control:
        """Desktop: heading + pill row. Phone: heading + dropdown selector."""
        heading = ft.Row(
            controls=[
                ft.Icon(ft.Icons.SCHOOL_ROUNDED, size=18, color=C.ACCENT),
                ft.Text(
                    "Học kỳ",
                    size=14 if platform_utils.UI_IS_MOBILE else 16,
                    weight=ft.FontWeight.W_700,
                    color=C.TEXT_PRIMARY,
                ),
            ],
            spacing=8,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        if platform_utils.UI_IS_MOBILE:
            selected = short_semester(self._selected_semester) or "Chọn học kỳ"
            dropdown = ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Text(
                            selected,
                            size=13,
                            weight=ft.FontWeight.W_600,
                            color=C.TEXT_PRIMARY,
                            expand=True,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Icon(
                            ft.Icons.EXPAND_MORE_ROUNDED,
                            size=22,
                            color=C.ACCENT,
                        ),
                    ],
                    spacing=8,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.symmetric(horizontal=14, vertical=13),
                border_radius=10,
                border=ft.Border.all(1, C.ACCENT),
                bgcolor=C.SURFACE,
                ink=True,
                on_click=self._open_semester_picker,
            )
            return ft.Column(
                controls=[heading, dropdown], spacing=8, tight=True
            )

        rows = snapshot.tuition_rows
        ordered = self._ordered_semester_names(snapshot)
        pills: list[ft.Control] = [
            self._semester_pill(
                name, short_semester(name), name,
                tuple(r for r in rows if r.semester == name),
            )
            for name in ordered
        ]
        self._pills_row = ft.Row(
            controls=pills,
            spacing=8,
            tight=True,
            wrap=True,
            run_spacing=8,
        )
        return ft.Column(
            controls=[heading, self._pills_row], spacing=8, tight=True
        )

    def _semester_pill(
        self, key: str, label: str, semester_name: str, rows: tuple
    ) -> ft.Container:
        # Mockup style: the selected pill uses the light-blue badge look
        # ("Đại học - chính quy") instead of a heavy solid fill — same for
        # every platform the pills render on.
        active = self._selected_semester == key
        return ft.Container(
            content=ft.Text(
                label,
                size=12.5,
                weight=ft.FontWeight.W_700 if active else ft.FontWeight.W_500,
                color=C.ACCENT if active else C.TEXT_PRIMARY,
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=8),
            border_radius=999,
            border=None if active else ft.Border.all(1, C.BORDER),
            bgcolor=ft.Colors.with_opacity(0.12, C.ACCENT)
            if active
            else C.SURFACE,
            ink=True,
            tooltip=f"{semester_name} · {_pills_status_label(rows)}",
            on_click=lambda _e, pill_key=key: self.select_semester(pill_key),
        )

    def _build_tabs(self) -> ft.Row:
        expand = platform_utils.UI_IS_MOBILE
        self._tab_tuition = self._tab_button(
            "Học phí", TAB_TUITION, ft.Icons.RECEIPT_LONG_ROUNDED, expand=expand
        )
        self._tab_other = self._tab_button(
            "Khoản thu khác", TAB_OTHER, ft.Icons.PAYMENTS_ROUNDED, expand=expand
        )
        return ft.Row(
            controls=[self._tab_tuition, self._tab_other],
            spacing=8,
            tight=True,
        )

    def _tab_button(
        self, label: str, key: str, icon, *, expand: bool = False
    ) -> ft.Container:
        active = self._active_tab == key
        solid = active and platform_utils.UI_IS_MOBILE
        foreground = C.ON_ACCENT if solid else C.ACCENT if active else C.TEXT_SECONDARY
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(
                        icon,
                        size=15,
                        color=foreground,
                    ),
                    ft.Text(
                        label,
                        size=13,
                        weight=ft.FontWeight.W_700,
                        color=foreground,
                    ),
                ],
                spacing=6,
                tight=True,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=9),
            border_radius=10,
            bgcolor=C.ACCENT if solid else ft.Colors.with_opacity(0.12, C.ACCENT)
            if active
            else C.SURFACE,
            border=None if active else ft.Border.all(1, C.BORDER),
            ink=True,
            expand=expand,
            on_click=lambda _e, tab_key=key: self.select_tab(tab_key),
        )

    def _build_table(self) -> ft.Control:
        if platform_utils.UI_IS_MOBILE:
            # Phone: a vertical tappable list — one tap opens the fee detail
            # sheet instead of swiping a wide table sideways like the Portal.
            return self._build_mobile_list()
        if not self._filtered_rows():
            empty_label = (
                "Không có khoản thu khác."
                if self._active_tab == TAB_OTHER
                else "Không có học phí trong lựa chọn này."
            )
            self._table = None
            self._table_host = ft.Container(
                content=ft.Text(
                    empty_label,
                    size=13,
                    color=C.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                alignment=ft.Alignment(0, 0),
                padding=ft.Padding.symmetric(horizontal=16, vertical=24),
                border_radius=12,
                border=ft.Border.all(1, C.BORDER),
                bgcolor=C.SURFACE,
            )
            return self._table_host
        if self._active_tab == TAB_OTHER:
            table = self._build_other_fee_table()
        else:
            table = self._build_tuition_table()
        self._table = table
        if self._table_host_width is not None:
            table.width = max(0, self._table_host_width - 18)
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
            on_size_change=self._fit_table_to_host,
        )
        return self._table_host

    def _fit_table_to_host(self, event=None) -> None:
        """Stretch desktop fee columns to the available pane width on resize."""
        table = self._table
        try:
            host_width = float(getattr(event, "width", 0) or 0)
        except (TypeError, ValueError):
            return
        if table is None or host_width <= 0:
            return

        self._table_host_width = host_width
        # Account for the host's border and inner padding. Keeping the table's
        # width tied to the rendered host makes maximize/restore resize live.
        target_width = max(0, host_width - 18)
        if abs((table.width or 0) - target_width) < 1:
            return
        table.width = target_width
        try:
            table.update()
        except Exception:
            pass

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
                    border_radius=10,
                    border=ft.Border.all(1, C.BORDER),
                    bgcolor=C.SURFACE,
                )
            ]
        # Desktop-only DataTable stays None on phones; the sticky "Tổng:"
        # bar below shows the running total instead.
        self._table = None
        self._table_host = ft.Container(
            content=ft.Column(controls=cards, spacing=8, tight=True),
        )
        return self._table_host

    def _mobile_fee_card(self, row: TuitionRow | OtherFeeRow) -> ft.Container:
        """One fee as a tappable card: title · meta · price, status at right."""
        if isinstance(row, TuitionRow):
            title = row.subject or "—"
            meta_parts = []
            if row.course_code:
                meta_parts.append(f"Mã LHP: {row.course_code}")
            if row.credits:
                meta_parts.append(f"TC: {row.credits}")
            meta = " | ".join(meta_parts)
            amount = row.tuition
        else:
            title = row.name or row.code or "—"
            if row.year:
                meta = f"Năm học: {row.year}"
            elif row.code:
                meta = f"Mã: {row.code}"
            else:
                meta = ""
            amount = row.amount
        if row.status == "paid":
            amount_color = C.SAFE
        elif getattr(row, "debt", 0) > 0:
            amount_color = C.CRITICAL
        else:
            amount_color = C.TEXT_PRIMARY
        amount_text = ft.Text(
            format_vnd(amount),
            size=14,
            weight=ft.FontWeight.W_700,
            color=amount_color,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Column(
                        controls=[
                            ft.Text(
                                title,
                                size=13.5,
                                weight=ft.FontWeight.W_700,
                                color=C.TEXT_PRIMARY,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                meta or " ",
                                size=11.5,
                                color=C.TEXT_SECONDARY,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            amount_text,
                        ],
                        spacing=3,
                        tight=True,
                        expand=True,
                    ),
                    ft.Column(
                        controls=[
                            ft.Icon(
                                ft.Icons.CHEVRON_RIGHT_ROUNDED,
                                size=20,
                                color=C.TEXT_SECONDARY,
                            ),
                            self._status_chip(row.status),
                        ],
                        spacing=4,
                        tight=True,
                        horizontal_alignment=ft.CrossAxisAlignment.END,
                    ),
                ],
                spacing=8,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=12),
            border_radius=10,
            border=ft.Border.all(1, C.BORDER),
            bgcolor=C.SURFACE,
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

    def _open_student_details(self, _event=None) -> None:
        """Show the mobile student information in a dismissible popup sheet."""
        page = self._page_ref
        snapshot = self._snapshot
        if (
            page is None
            or snapshot is None
            or not callable(getattr(page, "show_dialog", None))
        ):
            return
        if self._profile_sheet is not None:
            try:
                page.pop_dialog()
            except Exception:  # noqa: BLE001 - already dismissed by the user
                logger.debug("Previous student detail sheet was already closed")
            self._profile_sheet = None
        profile = snapshot.profile
        pairs = [
            ("MSSV", profile.student_id),
            ("Hệ đào tạo", profile.education_type),
            ("Khoa", profile.faculty),
            ("Ngành", profile.major),
            ("Chuyên ngành", profile.specialization),
            ("Lớp", profile.class_name),
            ("Khóa", profile.cohort),
            ("Ngày sinh", profile.birth_date),
            ("Nơi sinh", profile.hometown),
            ("Số điện thoại", profile.phone),
            ("Email", profile.email),
        ]
        controls: list[ft.Control] = [
            ft.Text(
                profile.full_name or "Thông tin sinh viên",
                size=18,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
            )
        ]
        controls.extend(
            _info_row(ft.Icons.PERSON_ROUNDED, label, value or "—")
            for label, value in pairs
        )
        sheet = ft.BottomSheet(
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
            on_dismiss=lambda _event: setattr(self, "_profile_sheet", None),
        )
        self._profile_sheet = sheet
        try:
            page.show_dialog(sheet)
        except Exception as exc:  # noqa: BLE001 - never break the tap
            logger.warning("Cannot open student detail sheet: %s", exc)
            self._profile_sheet = None

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

    # ------------------------------------------------------------------ #
    # Mobile: collapsible info, semester picker, sticky total bar
    # ------------------------------------------------------------------ #
    def _build_footer(self) -> ft.Container:
        """Sticky "Tổng: …" bar pinned under the scroll area (phones only).

        Mockup: light-blue band, dark label, green amount (red while in debt).
        """
        self._footer_text = ft.Text(
            "0 ₫",
            size=16,
            weight=ft.FontWeight.W_700,
            color=C.SAFE,
        )
        return ft.Container(
            width=float("inf"),
            content=ft.Row(
                controls=[
                    ft.Text(
                        "Tổng:",
                        size=14,
                        weight=ft.FontWeight.W_700,
                        color=C.TEXT_PRIMARY,
                    ),
                    self._footer_text,
                ],
                spacing=8,
                tight=True,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.only(left=16, right=16, top=12, bottom=16),
            bgcolor=ft.Colors.with_opacity(0.10, C.ACCENT),
            border=ft.Border(top=ft.BorderSide(1, C.BORDER)),
            visible=False,
        )

    def _sync_footer(self) -> None:
        """Show the total bar only on phones that currently render data."""
        self._footer.visible = bool(
            platform_utils.UI_IS_MOBILE and self._data_host.visible
        )

    def _update_footer(self) -> None:
        """Refresh the sticky total for the active tab + semester filter."""
        if self._footer_text is None or self._snapshot is None:
            return
        rows = self._filtered_rows()
        if self._active_tab == TAB_OTHER:
            amount = sum(getattr(row, "amount", 0) for row in rows)
            debt = sum(getattr(row, "debt", 0) for row in rows)
        else:
            totals = summarize_tuition(rows)
            amount, debt = totals.pay_level, totals.debt
        self._footer_text.value = format_vnd(amount)
        # Mockup: green when everything is paid, red while debt remains.
        self._footer_text.color = C.CRITICAL if debt > 0 else C.SAFE

    def _open_semester_picker(self, _event=None) -> None:
        """Phone dropdown: choose the active semester from a bottom sheet."""
        page = self._page_ref
        if page is None or not callable(getattr(page, "show_dialog", None)):
            return
        snapshot = self._snapshot
        if snapshot is None:
            return
        if self._semester_sheet is not None:
            try:
                page.pop_dialog()
            except Exception:  # noqa: BLE001 - already dismissed by the user
                logger.debug("Previous semester sheet was already closed")
            self._semester_sheet = None
        ordered = self._ordered_semester_names(snapshot)
        options: list[ft.Control] = []
        for name in ordered:
            active = name == self._selected_semester
            rows = tuple(r for r in snapshot.tuition_rows if r.semester == name)
            options.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        short_semester(name),
                                        size=13.5,
                                        weight=ft.FontWeight.W_700
                                        if active
                                        else ft.FontWeight.W_500,
                                        color=C.ACCENT
                                        if active
                                        else C.TEXT_PRIMARY,
                                    ),
                                    ft.Text(
                                        _pills_status_label(rows),
                                        size=11.5,
                                        color=C.TEXT_SECONDARY,
                                    ),
                                ],
                                spacing=2,
                                tight=True,
                                expand=True,
                            ),
                            ft.Icon(
                                ft.Icons.CHECK_CIRCLE_ROUNDED,
                                size=18,
                                color=C.ACCENT,
                            )
                            if active
                            else ft.Container(width=18, height=18),
                        ],
                        spacing=8,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.symmetric(horizontal=14, vertical=12),
                    border_radius=10,
                    bgcolor=ft.Colors.with_opacity(0.12, C.ACCENT)
                    if active
                    else C.SURFACE,
                    ink=True,
                    on_click=lambda _e, key=name: self._pick_semester(key),
                )
            )
        if not options:
            options = [
                ft.Container(
                    content=ft.Text(
                        "Chưa có học kỳ.",
                        size=13,
                        color=C.TEXT_SECONDARY,
                    ),
                    padding=ft.Padding.symmetric(horizontal=14, vertical=12),
                )
            ]
        body = ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Icon(
                            ft.Icons.SCHOOL_ROUNDED, size=18, color=C.ACCENT
                        ),
                        ft.Text(
                            "Chọn học kỳ",
                            size=16,
                            weight=ft.FontWeight.W_700,
                            color=C.TEXT_PRIMARY,
                        ),
                    ],
                    spacing=8,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                *options,
            ],
            spacing=4,
            tight=True,
            scroll=ft.ScrollMode.AUTO,
        )
        sheet = ft.BottomSheet(
            content=ft.Container(
                content=body,
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
            on_dismiss=lambda _event: setattr(self, "_semester_sheet", None),
        )
        self._semester_sheet = sheet
        try:
            page.show_dialog(sheet)
        except Exception as exc:  # noqa: BLE001 - never break the tap
            logger.warning("Cannot open semester picker: %s", exc)
            self._semester_sheet = None

    def _pick_semester(self, key: str) -> None:
        page = self._page_ref
        self.select_semester(key)
        if self._semester_sheet is not None:
            if page is not None and callable(getattr(page, "pop_dialog", None)):
                try:
                    page.pop_dialog()
                except Exception:  # noqa: BLE001 - already dismissed
                    logger.debug("Semester sheet was already closed")
            self._semester_sheet = None

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
    def _status_style(status: str) -> tuple[str, str]:
        """(color, icon) for a fee status — shared by chips, cells and cards."""
        color = {
            "paid": C.SAFE,
            "debt": C.CRITICAL,
            "unpaid": C.TEXT_SECONDARY,
        }.get(status, C.TEXT_SECONDARY)
        icon = {
            "paid": ft.Icons.CHECK_CIRCLE_ROUNDED,
            "debt": ft.Icons.ERROR_ROUNDED,
            "unpaid": ft.Icons.SCHEDULE_ROUNDED,
        }.get(status, ft.Icons.SCHEDULE_ROUNDED)
        return color, icon

    @classmethod
    def _status_chip(cls, status: str) -> ft.Container:
        """Pill showing the fee status (shared by mobile cards and the sheet)."""
        color, icon = cls._status_style(status)
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(icon, size=13, color=color),
                    ft.Text(
                        _STATUS_LABELS.get(status, status),
                        size=11,
                        weight=ft.FontWeight.W_700,
                        color=color,
                    ),
                ],
                spacing=4,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border_radius=999,
            border=ft.Border.all(1, color),
            bgcolor=ft.Colors.with_opacity(0.10, color),
        )

    @classmethod
    def _status_cell(cls, status: str) -> ft.DataCell:
        return ft.DataCell(cls._status_chip(status))

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

        # Empty selections never reach this builder — ``_build_table`` shows a
        # dedicated notice frame instead — and a 1-cell DataRow would crash
        # Flet (row/column mismatch), so only emit the totals row when there
        # is real data.
        if rows:
            totals = summarize_tuition(rows)
            total_style = dict(size=12.5, weight=ft.FontWeight.W_700)
            total_cells = [
                ft.DataCell(ft.Text("Tổng", color=C.TEXT_PRIMARY, **total_style)),
                ft.DataCell(ft.Text("", size=12)),
                ft.DataCell(ft.Text("", size=12)),
                ft.DataCell(ft.Text(str(totals.credits), color=C.CRITICAL, **total_style)),
                ft.DataCell(
                    ft.Text(format_vnd(totals.tuition), color=C.TEXT_PRIMARY, **total_style)
                ),
                ft.DataCell(
                    ft.Text(format_vnd(totals.paid_amount), color=C.SAFE, **total_style)
                ),
                ft.DataCell(
                    ft.Text(
                        format_vnd(totals.debt),
                        color=C.CRITICAL if totals.debt > 0 else C.TEXT_PRIMARY,
                        **total_style,
                    )
                ),
                ft.DataCell(ft.Text("", size=12)),
            ]
            data_rows.append(
                ft.DataRow(
                    cells=total_cells,
                    color=ft.Colors.with_opacity(0.06, C.ACCENT),
                )
            )

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
            column_spacing=38,
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

        # Same guard as the tuition table: an empty selection is handled by
        # ``_build_table`` with a notice frame, and a short DataRow would
        # mismatch the 7 columns and crash the desktop layout.
        if rows:
            amount = sum(row.amount for row in rows)
            paid = sum(row.paid_amount for row in rows)
            debt = sum(row.debt for row in rows)
            total_style = dict(size=12.5, weight=ft.FontWeight.W_700)
            data_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text("Tổng", color=C.TEXT_PRIMARY, **total_style)),
                        ft.DataCell(ft.Text("", size=12)),
                        ft.DataCell(ft.Text("", size=12)),
                        ft.DataCell(
                            ft.Text(format_vnd(amount), color=C.TEXT_PRIMARY, **total_style)
                        ),
                        ft.DataCell(
                            ft.Text(format_vnd(paid), color=C.SAFE, **total_style)
                        ),
                        ft.DataCell(
                            ft.Text(
                                format_vnd(debt),
                                color=C.CRITICAL if debt > 0 else C.TEXT_PRIMARY,
                                **total_style,
                            )
                        ),
                        ft.DataCell(ft.Text("", size=12)),
                    ],
                    color=ft.Colors.with_opacity(0.06, C.ACCENT),
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
            column_spacing=38,
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

    def _update_data_area(self) -> None:
        """Update fee controls without diffing the static student identity.

        The profile card and avatar are deliberately outside ``_data_host``.
        Sending updates only for the changed data area prevents Flutter from
        reattaching/repainting the avatar when a semester or fee tab changes.
        """
        for control in (self._data_host, self._footer):
            try:
                control.update()
            except Exception:
                # Controls can be detached in previews and unit tests.
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
