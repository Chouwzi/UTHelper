"""Tests for the Hồ sơ & Học phí screen, its ViewManager wiring and navigation."""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import config
import flet as ft
from core.student_profile import (
    ProfileSnapshot,
    parse_other_fee_rows,
    parse_semesters,
    parse_summary_profile,
    parse_tuition_rows,
)
from gui.app_controller import AppController
from gui.components.profile_view import (
    TAB_OTHER,
    TAB_TUITION,
    ProfileView,
    format_vnd,
    initials_of,
    short_semester,
)
from gui.core.theme import C
from gui.view_manager import ViewManager

SEMESTER_1 = "Học kỳ 1 năm học 2025-2026"
SEMESTER_2 = "Học kỳ 2 năm học 2025-2026"


def _make_snapshot(*, empty: bool = False) -> ProfileSnapshot:
    profile = parse_summary_profile(
        {
            "maSinhVien": "SV001",
            "hoDem": "Nguyễn Văn",
            "ten": "A",
            "nganh": "Công nghệ thông tin",
            "khoa": "Ban CNTT",
            "email": "sv001@example.com",
        }
    )
    if empty:
        return ProfileSnapshot(profile=profile, credit="", account="sv001")
    tuition_rows = parse_tuition_rows(
        [
            {
                "tenDot": SEMESTER_1,
                "maLopHocPhan": "0120005105",
                "noiDung": "Triết học Mác - Lênin",
                "soTinChi": 3,
                "mucPhiBanDau": 4500000,
                "mucNop": 4500000,
                "soTienNop": 4500000,
                "congNo": 0,
                "daDongHocPhi": True,
            },
            {
                "tenDot": SEMESTER_2,
                "maLopHocPhan": "0120122010",
                "noiDung": "XD phần mềm hướng đối tượng",
                "soTinChi": 3,
                "mucPhiBanDau": 4500000,
                "mucNop": 4500000,
                "soTienNop": 1500000,
                "congNo": 3000000,
                "daDongHocPhi": False,
            },
        ]
    )
    other_fees = parse_other_fee_rows(
        [
            {
                "namHoc": "2025-2026",
                "tenKhoanThuKhac": "Bảo hiểm y tế",
                "mucNop": 450000,
                "isBatBuoc": True,
                "soTienNop": 450000,
                "congNo": 0,
            }
        ]
    )
    return ProfileSnapshot(
        profile=profile,
        credit="125000.00Đ (UTH)",
        semesters=parse_semesters(
            [{"id": 76, "tenDot": SEMESTER_2}, {"id": 75, "tenDot": SEMESTER_1}]
        ),
        tuition_rows=tuition_rows,
        other_fees=other_fees,
        account="sv001",
    )


def _make_view(**callbacks) -> ProfileView:
    return ProfileView(**callbacks)


# ---------------------------------------------------------------------------
# 1. Formatting helpers
# ---------------------------------------------------------------------------


def test_format_helpers_render_vietnamese_money_and_names():
    assert format_vnd(4500000) == "4.500.000 ₫"
    assert format_vnd(0) == "0 ₫"
    assert format_vnd("not-a-number") == "0 ₫"

    assert short_semester("Học kỳ 1 năm học 2025-2026") == "HK1 2025-2026"
    assert initials_of("Nguyễn Văn An") == "NA"
    assert initials_of("", fallback="SV001") == "SV"
    assert initials_of("") == "?"


# ---------------------------------------------------------------------------
# 2. ProfileView states
# ---------------------------------------------------------------------------


def test_profile_view_wires_header_callbacks():
    closed, refreshed = [], []
    view = _make_view(
        on_close=lambda: closed.append(True),
        on_refresh=lambda: refreshed.append(True),
    )

    view._handle_close()
    view._handle_refresh()
    assert closed == [True]
    assert refreshed == [True]


def test_profile_view_loading_only_shows_spinner_without_data():
    view = _make_view()

    view.show_loading()
    assert view._loading_row.visible is True
    assert view._data_host.visible is False

    view.update_snapshot(_make_snapshot())
    view.show_loading()
    # Đã có dữ liệu -> giữ bảng trên màn hình, không nháy spinner.
    assert view._loading_row.visible is False
    assert view._data_host.visible is True


def test_profile_view_renders_identity_money_and_status():
    view = _make_view()

    view.update_snapshot(_make_snapshot())

    assert view._data_host.visible is True
    assert view._name_text.value == "Nguyễn Văn A"
    assert "SV001" in view._sid_text.value
    # Kỳ 2 (mới nhất, mặc định) còn nợ 3.000.000 -> banner là "Còn nợ".
    assert "Còn nợ" in view._status_text.value
    # Bản mạng -> ghi rõ nguồn "Portal".
    assert "Portal" in view._updated_text.value
    assert view._empty_state.visible is False


def test_profile_view_cached_snapshot_marks_offline_origin():
    view = _make_view()

    view.update_snapshot(_make_snapshot(), from_cache=True)
    view.set_stale_notice("Không thể làm mới · đang hiển thị dữ liệu đã lưu.")

    assert "bản lưu ngoại tuyến" in view._updated_text.value
    assert view._stale_banner.visible is True
    assert "dữ liệu đã lưu" in view._notice_text.value

    # Fetch thành công -> banner biến mất, nguồn về Portal.
    view.update_snapshot(_make_snapshot(), from_cache=False)
    assert view._stale_banner.visible is False
    assert "Portal" in view._updated_text.value


def test_profile_view_error_state_only_when_nothing_to_show():
    view = _make_view()

    view.show_error("Không tải được hồ sơ từ Portal.")
    assert view._error_state.visible is True
    assert view._data_host.visible is False
    assert "Không tải được" in view._error_text.value

    view.update_snapshot(_make_snapshot())
    assert view._error_state.visible is False
    assert view._data_host.visible is True


def test_profile_view_empty_state_when_portal_returns_nothing():
    view = _make_view()

    # Portal trả về trống hoàn toàn (không hồ sơ, không học phí).
    view.update_snapshot(ProfileSnapshot())
    assert view._empty_state.visible is True
    assert view._data_host.visible is False
    assert view._student_card_host.visible is False

    # Có hồ sơ dù chưa có học phí -> vẫn render thẻ sinh viên + bảng inline.
    view.update_snapshot(_make_snapshot(empty=True))
    assert view._empty_state.visible is False
    assert view._data_host.visible is True

    view.update_snapshot(_make_snapshot())
    assert view._empty_state.visible is False


# ---------------------------------------------------------------------------
# 3. Semester filter + tabs (ÍT NHẤN: pill = lọc, tab = đổi bảng)
# ---------------------------------------------------------------------------


def test_profile_view_semester_pill_filters_tuition_rows():
    view = _make_view()
    view.update_snapshot(_make_snapshot())

    # Mặc định là kỳ mới nhất (HK2 theo dropdown Portal): 1 dòng + dòng tổng.
    assert view._selected_semester == SEMESTER_2
    assert len(view._table.rows) == 2

    view.select_semester(SEMESTER_1)
    # Chỉ còn 1 dòng của HK1 + dòng tổng.
    assert len(view._table.rows) == 2
    assert view._selected_semester == SEMESTER_1

    view.select_semester(SEMESTER_2)
    assert len(view._table.rows) == 2
    # Bấm lại đúng kỳ đang chọn -> không render lại (không nháy).
    view.select_semester(SEMESTER_2)
    assert view._selected_semester == SEMESTER_2


def test_profile_view_defaults_to_newest_semester_and_resets_when_it_disappears():
    view = _make_view()
    view.update_snapshot(_make_snapshot())

    # Không còn pill "Tất cả": mặc định chọn kỳ mới nhất, bảng chỉ có kỳ đó.
    assert view._selected_semester == SEMESTER_2
    header_labels = [col.label.value for col in view._table.columns]
    assert "Học kỳ" not in header_labels

    view.select_semester(SEMESTER_1)

    # Snapshot mới không còn kỳ đang chọn -> tự quay về kỳ mới nhất còn lại.
    newer = _make_snapshot()
    newer = ProfileSnapshot(
        profile=newer.profile,
        credit=newer.credit,
        semesters=parse_semesters([{"id": 76, "tenDot": SEMESTER_2}]),
        tuition_rows=tuple(
            row for row in newer.tuition_rows if row.semester == SEMESTER_2
        ),
        other_fees=newer.other_fees,
        account=newer.account,
    )
    view.update_snapshot(newer)
    assert view._selected_semester == SEMESTER_2
    assert len(view._table.rows) == 2


def test_profile_view_keeps_selected_semester_across_refresh():
    view = _make_view()
    view.update_snapshot(_make_snapshot())
    view.select_semester(SEMESTER_1)

    # Làm mới Portal với cùng dữ liệu -> giữ nguyên kỳ người dùng đang xem.
    view.update_snapshot(_make_snapshot())
    assert view._selected_semester == SEMESTER_1


def test_profile_view_tab_switches_between_fee_tables():
    view = _make_view()
    view.update_snapshot(_make_snapshot())

    assert view._active_tab == TAB_TUITION
    # Bảng học phí có cột "Môn học".
    header_labels = [col.label.value for col in view._table.columns]
    assert "Môn học" in header_labels

    view.select_tab(TAB_OTHER)
    assert view._active_tab == TAB_OTHER
    header_labels = [col.label.value for col in view._table.columns]
    assert "Khoản thu" in header_labels
    # 1 khoản thu khác + 1 dòng tổng.
    assert len(view._table.rows) == 2

    view.select_tab(TAB_TUITION)
    assert view._active_tab == TAB_TUITION


def test_profile_view_semester_pills_cover_every_semester_with_status_tooltip():
    view = _make_view()
    view.update_snapshot(_make_snapshot())

    # Chỉ pill học kỳ (không còn "Tất cả"), đúng thứ tự dropdown Portal (HK2 trước).
    pills = view._pills_row.controls
    assert len(pills) == 2
    labels = [pill.content.value for pill in pills]
    assert all("Tất cả" not in label for label in labels)
    assert "HK2" in labels[0]
    assert "Còn nợ" in pills[0].tooltip
    assert "HK1" in labels[1]
    assert "Đã đóng" in pills[1].tooltip
    # Kỳ mới nhất (đang chọn) dùng màu badge "Đại học - chính quy": nền xanh
    # nhạt + chữ xanh dương (không còn tô đặc như trước); các pill còn lại
    # là nền surface viền nhạt (trạng thái nằm trong tooltip).
    assert pills[0].bgcolor == ft.Colors.with_opacity(0.12, C.ACCENT)
    assert pills[0].content.color == C.ACCENT
    assert pills[1].bgcolor == C.SURFACE
    assert pills[1].border is not None


def test_profile_view_theme_refresh_does_not_crash():
    view = _make_view()
    view.update_snapshot(_make_snapshot())

    view.update_theme()
    assert view._title.color is not None


# ---------------------------------------------------------------------------
# 3b. Portal avatar + mobile card list / detail sheet
# ---------------------------------------------------------------------------


def test_profile_view_uses_portal_avatar_and_falls_back_to_initials():
    view = _make_view()
    snapshot = _make_snapshot()
    view.update_snapshot(ProfileSnapshot(**{**snapshot.__dict__, "avatar": ""}))
    # No photo -> initials letter inside the accent circle.
    assert view._avatar_image is None
    assert view._avatar_text is not None
    assert view._avatar_text.value == "NA"

    view.update_snapshot(
        ProfileSnapshot(**{**snapshot.__dict__, "avatar": "data:image/png;base64,QUJD"})
    )
    # Photo replaces the letter, and re-rendering reuses the same control so
    # the ~200 KB base64 payload is not re-sent to the client.
    assert view._avatar_image is not None
    assert view._avatar_image.src == "data:image/png;base64,QUJD"
    first = view._avatar_image
    avatar_shell = view._avatar
    view.update_snapshot(
        ProfileSnapshot(**{**snapshot.__dict__, "avatar": "data:image/png;base64,QUJD"})
    )
    assert view._avatar_image is first
    assert view._avatar is avatar_shell


def test_profile_view_photo_avatar_keeps_white_gap_inside_light_ring():
    """The mockup shows ring → white gap → photo, not photo touching the ring."""
    view = _make_view()
    snapshot = _make_snapshot()
    view.update_snapshot(
        ProfileSnapshot(**{**snapshot.__dict__, "avatar": "data:image/png;base64,QUJD"})
    )
    assert view._avatar_image is not None
    assert view._avatar.padding is not None
    assert view._avatar.padding.left == 3
    assert view._avatar.bgcolor == C.SURFACE

    # Initials fallback keeps the solid accent circle (white letter, no gap).
    view.update_snapshot(ProfileSnapshot(**{**snapshot.__dict__, "avatar": ""}))
    assert view._avatar_text is not None
    assert view._avatar.padding is None
    assert view._avatar.bgcolor == C.ACCENT


def test_profile_view_mobile_renders_tappable_cards_instead_of_table(
    monkeypatch,
):
    from gui.components import profile_view as profile_view_module

    monkeypatch.setattr(profile_view_module.platform_utils, "UI_IS_MOBILE", True)
    view = _make_view()
    view.update_snapshot(_make_snapshot())
    avatar = view._avatar

    # No DataTable / no horizontal scroll on phones: one card per fee.
    assert view._table is None
    cards = view._table_host.content.controls
    assert len(cards) == 1  # newest semester has a single tuition row
    assert cards[0].on_click is not None

    view.select_tab(TAB_OTHER)
    assert view._avatar is avatar
    other_cards = view._table_host.content.controls
    assert len(other_cards) == 1
    assert other_cards[0].on_click is not None


def test_profile_view_desktop_empty_fee_states_do_not_build_malformed_rows():
    view = _make_view()
    view.update_snapshot(_make_snapshot(empty=True))

    empty_tuition = view._table_host
    assert view._table is None
    assert isinstance(empty_tuition.content, ft.Text)
    assert "Không có học phí" in empty_tuition.content.value

    view.select_tab(TAB_OTHER)
    empty_other = view._table_host
    assert view._table is None
    assert isinstance(empty_other.content, ft.Text)
    assert "Không có khoản thu khác" in empty_other.content.value


def test_profile_view_fee_builders_never_emit_short_rows_when_empty():
    """Review #129: a 1-cell DataRow mismatches the columns and crashes Flet."""
    view = _make_view()
    view.update_snapshot(_make_snapshot(empty=True))

    tuition = view._build_tuition_table()
    assert isinstance(tuition, ft.DataTable)
    assert len(tuition.columns) == 8
    assert tuition.rows == []

    other = view._build_other_fee_table()
    assert isinstance(other, ft.DataTable)
    assert len(other.columns) == 7
    assert other.rows == []


def test_profile_view_mobile_detail_sheet_shows_money_and_status(monkeypatch):
    from gui.components import profile_view as profile_view_module

    monkeypatch.setattr(profile_view_module.platform_utils, "UI_IS_MOBILE", True)
    shown: list = []
    page = SimpleNamespace(
        show_dialog=lambda sheet: shown.append(sheet),
        pop_dialog=lambda: None,
    )
    view = _make_view(page=page)
    view.update_snapshot(_make_snapshot())

    debt_row = next(
        row
        for row in view._snapshot.tuition_rows
        if row.semester == SEMESTER_2
    )
    view._open_fee_detail(debt_row)

    assert len(shown) == 1
    sheet = shown[0]
    assert view._detail_sheet is sheet
    labels = [
        control.controls[0].value
        for control in sheet.content.content.controls[1:]
    ]
    assert "Số tiền" in labels
    assert "Còn nợ" in labels
    values = {
        control.controls[0].value: control.controls[1].value
        for control in sheet.content.content.controls[1:]
    }
    assert values["Còn nợ"] == format_vnd(3000000)
    assert values["Số tiền"] == format_vnd(4500000)


def test_profile_view_mobile_detail_sheet_is_skipped_without_a_page(monkeypatch):
    from gui.components import profile_view as profile_view_module

    monkeypatch.setattr(profile_view_module.platform_utils, "UI_IS_MOBILE", True)
    view = _make_view()  # no page wired
    view.update_snapshot(_make_snapshot())

    view._open_fee_detail(view._snapshot.tuition_rows[0])
    assert view._detail_sheet is None


# ---------------------------------------------------------------------------
# 3c. Mockup-faithful redesign (desktop card + phone layout)
# ---------------------------------------------------------------------------


def test_profile_view_desktop_card_matches_wide_banner_mockup():
    view = _make_view()
    view.update_snapshot(_make_snapshot())

    assert view._title.size == 22
    assert view._subtitle.value

    assert view._student_card_host.visible is True
    assert view._student_card_host.key == "profile-student-card-host"
    card = view._student_card_host.content
    banner = card.content.controls[0]
    stack = banner.content
    assert isinstance(stack, ft.Stack)
    campus_layer = stack.controls[0]
    campus_image = campus_layer.content
    assert campus_image.src.startswith("data:image/jpeg;base64,")
    assert campus_image.fit == ft.BoxFit.CONTAIN
    assert abs(campus_image.width / campus_image.height - 845 / 415) < 0.01
    assert campus_layer.right == 0
    assert campus_layer.top == 0
    assert campus_image.height == banner.height
    identity = stack.controls[2].content
    assert len(identity.controls) == 3
    assert identity.controls[0] is view._avatar
    name_block = identity.controls[1]
    assert name_block.controls[0] is view._name_text
    assert name_block.controls[1] is view._sid_text
    assert name_block.controls[2].content.controls[1].value
    assert campus_image.semantics_label == "Khuôn viên UTH"
    assert view._avatar.width == 112
    assert view._avatar.key == "profile-student-avatar"
    assert view._name_text.size == 21
    assert view._sid_text.size == 13
    assert name_block.controls[2].content.controls[1].size == 12

    # One card: banner, edge-to-edge hairline, then the info grid.
    hairline = card.content.controls[1]
    assert isinstance(hairline, ft.Container)
    assert hairline.height == 1
    assert hairline.bgcolor == C.BORDER
    assert card.border_radius == 16
    assert not card.padding

    info_grid = card.content.controls[2].content
    assert len(info_grid.controls) == 3
    divider = info_grid.controls[1]
    assert isinstance(divider, ft.Container)
    assert divider.width == 2
    assert divider.height == 150
    assert divider.bgcolor == ft.Colors.with_opacity(0.65, C.BORDER_STRONG)
    assert len(info_grid.controls[0].controls) == 4
    assert len(info_grid.controls[2].controls) == 5
    first_row = info_grid.controls[0].controls[0].controls
    assert first_row[0].color == C.ACCENT
    assert first_row[1].value == "Khoa:"
    assert first_row[1].weight == ft.FontWeight.W_700
    assert first_row[1].color == C.TEXT_PRIMARY
    assert info_grid.controls[2].controls[4].controls[1].value == "Email:"
    # Left-column icons follow the mockup: bank / layers / bookmark / people.
    left_icons = [row.controls[0].icon for row in info_grid.controls[0].controls]
    assert left_icons == [
        ft.Icons.ACCOUNT_BALANCE_ROUNDED,
        ft.Icons.LAYERS_ROUNDED,
        ft.Icons.BOOKMARK_ROUNDED,
        ft.Icons.GROUP_ROUNDED,
    ]

    assert view._tab_tuition.bgcolor == ft.Colors.with_opacity(0.12, C.ACCENT)
    assert view._tab_tuition.content.controls[1].color == C.ACCENT
    tiles = view._data_host.controls[0].controls
    assert len(tiles) == 4
    assert len({tile.height for tile in tiles}) == 1


def test_profile_view_keeps_student_card_and_avatar_mounted_when_switching_sections(
    monkeypatch,
):
    from gui.components import profile_view as profile_view_module

    snapshot = _make_snapshot()
    snapshot = ProfileSnapshot(
        **{**snapshot.__dict__, "avatar": "data:image/png;base64,QUJD"}
    )
    for mobile in (False, True):
        monkeypatch.setattr(profile_view_module.platform_utils, "UI_IS_MOBILE", mobile)
        view = _make_view()
        view.update_snapshot(snapshot)
        student_card = view._student_card_host.content
        student_card_host = view._student_card_host
        avatar = view._avatar
        avatar_image = view._avatar_image

        view.select_semester(SEMESTER_1)
        view.select_tab(TAB_OTHER)
        view.select_tab(TAB_TUITION)
        view.select_semester(SEMESTER_2)

        assert view._student_card_host.content is student_card
        assert any(control is student_card_host for control in view.content.controls)
        assert student_card not in view._data_host.controls
        assert view._student_card is student_card
        assert view._avatar is avatar
        assert view._avatar_image is avatar_image
        assert view._avatar_image.src == "data:image/png;base64,QUJD"
        assert view._avatar_image.gapless_playback is True


def test_profile_view_mobile_footer_and_avatar_opens_student_popup(monkeypatch):
    from gui.components import profile_view as profile_view_module

    monkeypatch.setattr(profile_view_module.platform_utils, "UI_IS_MOBILE", True)
    shown: list = []
    page = SimpleNamespace(show_dialog=lambda sheet: shown.append(sheet))
    view = _make_view(page=page)
    view.update_snapshot(_make_snapshot())

    # Sticky "Tổng:" bar under the cards, filtered by semester + tab.
    assert view._footer.visible is True
    assert view._footer.width == float("inf")
    assert view._footer.content.alignment == ft.MainAxisAlignment.CENTER
    assert view._footer_text.value == format_vnd(4500000)
    # Mockup: light-blue band; red while the selected semester owes money…
    assert view._footer_text.color == C.CRITICAL
    # …and green once everything is paid.
    view.select_semester(SEMESTER_1)
    assert view._footer_text.value == format_vnd(4500000)
    assert view._footer_text.color == C.SAFE

    # The compact card stays closed; tapping the avatar opens a popup.
    card = view._student_card_host.content
    identity = card.content
    avatar = identity.controls[0]
    chevron = identity.controls[2]
    assert avatar.on_click is not None
    assert chevron.on_click is not None
    assert len(identity.controls) == 3
    avatar.on_click(None)
    assert len(shown) == 1
    assert shown[0] is view._profile_sheet
    popup_rows = shown[0].content.content.controls
    assert popup_rows[0].value == "Nguyễn Văn A"
    assert len(popup_rows) == 12  # Name + all 11 student fields.
    assert popup_rows[1].controls[1].value == "MSSV:"
    assert popup_rows[-1].controls[2].value == "sv001@example.com"

    # No data on screen -> the total bar hides with it.
    view.show_error("Không tải được hồ sơ từ Portal.")
    assert view._footer.visible is False


def test_profile_view_clear_discards_snapshot_and_rendered_account_data():
    view = _make_view()
    view.update_snapshot(_make_snapshot())

    view.clear()

    assert view._snapshot is None
    assert view._data_host.controls == []
    assert view._data_host.visible is False
    assert view._student_card_host.content is None
    assert view._student_card_host.visible is False
    assert view._avatar is None
    assert view._footer.visible is False


def test_profile_view_mobile_avatar_popup_is_skipped_without_a_page(monkeypatch):
    from gui.components import profile_view as profile_view_module

    monkeypatch.setattr(profile_view_module.platform_utils, "UI_IS_MOBILE", True)
    view = _make_view()
    view.update_snapshot(_make_snapshot())
    avatar = view._student_card_host.content.content.controls[0]
    view._open_student_details()
    assert avatar.on_click is not None
    assert view._profile_sheet is None


def test_profile_view_mobile_semester_dropdown_opens_picker(monkeypatch):
    from gui.components import profile_view as profile_view_module

    monkeypatch.setattr(profile_view_module.platform_utils, "UI_IS_MOBILE", True)
    shown: list = []
    popped: list = []
    page = SimpleNamespace(
        show_dialog=lambda sheet: shown.append(sheet),
        pop_dialog=lambda: popped.append(True),
    )
    view = _make_view(page=page)
    view.update_snapshot(_make_snapshot())

    # Mockup dropdown: selected semester + chevron, tap opens the picker.
    semester_block = view._data_host.controls[1]
    dropdown = semester_block.controls[1]
    assert dropdown.on_click is not None

    dropdown.on_click(None)
    assert len(shown) == 1
    assert view._semester_sheet is shown[0]
    assert shown[0].scrollable is True
    assert shown[0].content.content.scroll == ft.ScrollMode.AUTO
    # Header row + one option per semester.
    assert len(shown[0].content.content.controls[1:]) == 2

    # Choosing the older semester re-renders and closes the sheet.
    view._pick_semester(SEMESTER_1)
    assert view._selected_semester == SEMESTER_1
    assert popped == [True]
    assert view._semester_sheet is None


def test_profile_view_mobile_fee_card_shows_mockup_lines(monkeypatch):
    from gui.components import profile_view as profile_view_module

    monkeypatch.setattr(profile_view_module.platform_utils, "UI_IS_MOBILE", True)
    view = _make_view()
    view.update_snapshot(_make_snapshot())

    # Newest semester (debt) is selected: one card with mockup lines.
    cards = view._table_host.content.controls
    assert len(cards) == 1
    lines = cards[0].content.controls[0].controls
    title, meta, price = lines
    assert title.value == "XD phần mềm hướng đối tượng"
    assert "Mã LHP: 0120122010" in meta.value
    assert "TC: 3" in meta.value
    assert price.value == format_vnd(4500000)
    # Unpaid fee: red price, "Còn nợ" pill at the right edge.
    assert price.color == C.CRITICAL
    pill = cards[0].content.controls[1].controls[1]
    assert pill.content.controls[1].value == "Còn nợ"


# ---------------------------------------------------------------------------
# 4. ViewManager wiring
# ---------------------------------------------------------------------------


class _FakePage:
    def __init__(self):
        self.update_calls = 0

    def update(self):
        self.update_calls += 1


class _FakeControl:
    def __init__(self, *, visible: bool = False):
        self.visible = visible
        self.opacity = 1.0 if visible else 0.0
        self.offset = None
        self.content = None
        self.theme_updates = 0

    def hide(self):
        self.visible = False

    def show_loading(self, *_args):
        self.visible = True

    def update_theme(self):
        self.theme_updates += 1


class _FakeController:
    def __init__(self):
        self.calendar_btn = SimpleNamespace(icon_color=None)
        self.grades_btn = SimpleNamespace(icon_color=None)
        self.courses_btn = SimpleNamespace(icon_color=None)

    def _dismiss_desktop_filter_menu(self):
        pass


class _FakeProfile:
    """Ghi lại các method state mà ViewManager gọi vào."""

    def __init__(self):
        self.visible = False
        self.opacity = 1.0
        self.offset = None
        self.loading_calls = 0
        self.snapshots: list[tuple[object, bool]] = []
        self.errors: list[str] = []
        self.notices: list[str] = []
        self.theme_updates = 0

    def show_loading(self):
        self.loading_calls += 1

    def update_snapshot(self, snapshot, *, from_cache: bool = False):
        self.snapshots.append((snapshot, from_cache))

    def show_error(self, message: str = ""):
        self.errors.append(message)

    def set_stale_notice(self, message):
        self.notices.append(message)

    def update_theme(self):
        self.theme_updates += 1


def _make_view_manager(profile):
    page = _FakePage()
    dash = _FakeControl(visible=True)
    destinations: list[str] = []
    vm = ViewManager(
        page,
        dash,
        _FakeControl(),
        _FakeControl(),
        _FakeControl(),
        _FakeControl(),
        _FakeController(),
        profile_view=profile,
        tab_switcher=_FakeControl(),
        on_destination_change=destinations.append,
    )
    return vm, dash, destinations


def test_view_manager_shows_profile_and_notifies_destination():
    profile = _FakeProfile()
    vm, dash, destinations = _make_view_manager(profile)

    vm.show_profile_loading()

    assert profile.visible is True
    assert dash.visible is False
    assert profile.loading_calls == 1
    assert profile.theme_updates == 1
    assert destinations[-1] == "profile"


def test_view_manager_pushes_data_error_and_stale_notice_into_profile():
    profile = _FakeProfile()
    vm, _dash, _destinations = _make_view_manager(profile)
    snapshot = _make_snapshot()

    vm.show_profile_data(snapshot, from_cache=True)
    vm.show_profile_stale_notice("đang hiển thị dữ liệu đã lưu")
    vm.show_profile_error("lỗi rồi")

    assert profile.snapshots == [(snapshot, True)]
    assert profile.notices == ["đang hiển thị dữ liệu đã lưu"]
    assert profile.errors == ["lỗi rồi"]


def test_view_manager_close_profile_returns_to_dashboard_and_hides_view():
    profile = _FakeProfile()
    vm, dash, destinations = _make_view_manager(profile)
    vm.show_profile_loading()

    asyncio.run(vm.close_profile())

    assert profile.visible is False
    assert dash.visible is True
    assert destinations[-1] == "home"


def test_view_manager_hides_profile_when_switching_to_other_screens():
    profile = _FakeProfile()
    vm, dash, _destinations = _make_view_manager(profile)
    vm.show_profile_loading()
    assert profile.visible is True

    vm.show_dashboard()
    assert profile.visible is False
    assert dash.visible is True

    vm.show_profile_loading()
    assert profile.visible is True
    vm.show_grades_loading()
    assert profile.visible is False


def test_view_manager_without_profile_view_still_works():
    page = _FakePage()
    vm = ViewManager(
        page,
        _FakeControl(visible=True),
        _FakeControl(),
        _FakeControl(),
        _FakeControl(),
        _FakeControl(),
        _FakeController(),
    )

    vm.show_profile_loading()
    assert page.update_calls >= 1

    asyncio.run(vm.close_profile())
    assert page.update_calls >= 2


# ---------------------------------------------------------------------------
# 5. AppController navigation wiring
# ---------------------------------------------------------------------------


def _controller(active: str) -> AppController:
    controller = AppController.__new__(AppController)
    controller.detail_view = SimpleNamespace(visible=False)
    controller.settings_view = SimpleNamespace(
        visible=active == "settings", has_changes=lambda: False
    )
    controller.calendar_view = SimpleNamespace(visible=active == "calendar")
    controller.grade_overview_view = SimpleNamespace(visible=active == "grades")
    controller.course_grades_view = SimpleNamespace(visible=False)
    controller.courses_view = SimpleNamespace(visible=active == "courses")
    controller.profile_view = SimpleNamespace(visible=active == "profile")
    controller._dismiss_desktop_filter_menu = lambda: None
    controller._sync_rail_active = lambda *a, **k: None
    for name in (
        "_close_detail", "_close_settings", "_close_calendar", "_close_grades",
        "_close_profile", "_close_courses", "_show_calendar", "_show_grades",
        "_show_profile", "_show_courses", "_show_settings",
    ):
        setattr(controller, name, AsyncMock())
    return controller


def test_current_section_detects_profile_screen():
    assert _controller("profile")._current_section() == "profile"
    assert _controller("grades")._current_section() == "grades"
    assert _controller("home")._current_section() == "home"


def test_rail_navigate_opens_profile_from_another_section():
    controller = _controller("grades")

    asyncio.run(controller._rail_navigate("profile"))

    controller._close_grades.assert_awaited_once()
    controller._show_profile.assert_awaited_once()


def test_rail_navigate_from_profile_closes_profile_first():
    controller = _controller("profile")

    asyncio.run(controller._rail_navigate("home"))

    controller._close_profile.assert_awaited_once()


def test_clicking_profile_again_while_open_does_nothing():
    controller = _controller("profile")

    asyncio.run(controller._rail_navigate("profile"))

    controller._close_profile.assert_not_awaited()
    controller._show_profile.assert_not_awaited()


def test_perform_destination_navigation_opens_profile():
    controller = _controller("home")

    asyncio.run(controller._perform_destination_navigation("profile"))

    controller._show_profile.assert_awaited_once()


def test_profile_service_is_cached_per_account(monkeypatch):
    monkeypatch.setattr(config.settings, "UTH_USERNAME", "sv001")
    controller = AppController.__new__(AppController)
    controller._portal_client = object()

    first = controller._get_profile_service()
    second = controller._get_profile_service()
    assert first is second

    # Đổi tài khoản -> service/namespace mới (không dùng nhầm cache).
    monkeypatch.setattr(config.settings, "UTH_USERNAME", "sv002")
    third = controller._get_profile_service()
    assert third is not first
    assert third.cache.path != first.cache.path
