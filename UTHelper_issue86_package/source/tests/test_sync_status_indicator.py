import os
import sys

import flet as ft

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))


def test_sync_status_indicator_uses_accessible_client_side_busy_animation():
    from gui.components.sync_status_indicator import SyncStatusIndicator

    indicator = SyncStatusIndicator("Đang cập nhật dữ liệu...")

    assert isinstance(indicator.control, ft.Semantics)
    assert indicator.control.live_region is True
    assert indicator.control.label == "Đang cập nhật dữ liệu"
    assert indicator.state == "busy"
    assert indicator.busy_shimmer.visible is True
    assert indicator.static_dot.visible is False
    assert indicator.busy_shimmer.loop == 0
    assert indicator.busy_shimmer.period >= 1500


def test_sync_status_indicator_switches_to_static_success_and_error_states():
    from gui.components.sync_status_indicator import SyncStatusIndicator
    from gui.core.theme import C

    indicator = SyncStatusIndicator("Đang cập nhật dữ liệu...")

    indicator.set_state("success", "Cập nhật lúc 22:06 · 45 hoạt động")
    assert indicator.state == "success"
    assert indicator.text.value == "Cập nhật lúc 22:06 · 45 hoạt động"
    assert indicator.control.label == "Đã cập nhật dữ liệu"
    assert indicator.busy_shimmer.visible is False
    assert indicator.static_dot.visible is True
    assert indicator.static_dot.bgcolor == C.SAFE

    indicator.set_state("error", "Lỗi cập nhật · đang dùng dữ liệu đã lưu")
    assert indicator.state == "error"
    assert indicator.control.label == "Lỗi cập nhật dữ liệu"
    assert indicator.busy_shimmer.visible is False
    assert indicator.static_dot.bgcolor == C.CRITICAL


def test_sync_status_indicator_rejects_unknown_state():
    import pytest

    from gui.components.sync_status_indicator import SyncStatusIndicator

    indicator = SyncStatusIndicator("Đang cập nhật dữ liệu...")

    with pytest.raises(ValueError, match="Unsupported sync status"):
        indicator.set_state("purple", "Không hợp lệ")
