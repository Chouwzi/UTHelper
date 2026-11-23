"""Tests for the Hồ sơ & Học phí screen, its ViewManager wiring and navigation."""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import config
from core.student_profile import (
    ProfileSnapshot,
    parse_other_fee_rows,
    parse_semesters,
    parse_summary_profile,
    parse_tuition_rows,
)
from gui.app_controller import AppController
from gui.components.profile_view import (
    ALL_SEMESTERS_KEY,
    TAB_OTHER,
    TAB_TUITION,
    ProfileView,
    format_credit,
    format_vnd,
    initials_of,
    short_semester,
)
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

    assert format_credit("125000.00Đ (UTH)") == "125.000 ₫"
    assert format_credit("") == ""
    assert format_credit("bizarre text") == "bizarre text"

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
    assert "125.000 ₫" in view._credit_text.value
    # Kỳ 2 còn nợ 3.000.000 -> banner tổng thể là "Còn nợ".
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

    # "Tất cả": 2 dòng học phí + 1 dòng tổng.
    assert len(view._table.rows) == 3
    assert view._selected_semester == ALL_SEMESTERS_KEY

    view.select_semester(SEMESTER_1)
    # Chỉ còn 1 dòng của HK1 + dòng tổng.
    assert len(view._table.rows) == 2
    assert view._selected_semester == SEMESTER_1

    view.select_semester(ALL_SEMESTERS_KEY)
    assert len(view._table.rows) == 3


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

    # 1 pill "Tất cả" + 2 pill học kỳ, đúng thứ tự dropdown Portal (HK2 trước).
    pills = view._pills_row.controls
    assert len(pills) == 3
    assert pills[0].tooltip == "Tất cả học kỳ"
    assert "HK2" in pills[1].content.value
    assert "Còn nợ" in pills[1].tooltip
    assert "HK1" in pills[2].content.value
    assert "Đã đóng" in pills[2].tooltip


def test_profile_view_theme_refresh_does_not_crash():
    view = _make_view()
    view.update_snapshot(_make_snapshot())

    view.update_theme()
    assert view._title.color is not None


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
