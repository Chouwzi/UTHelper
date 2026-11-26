import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.components.color_picker import ColorPicker


def _make_picker(hex_value="#DC2626", *, dialog_open=True, on_change=None):
    picker = ColorPicker.__new__(ColorPicker)
    picker.hex_inp = SimpleNamespace(value=hex_value, error=None, update=Mock())
    picker.container_box = SimpleNamespace(bgcolor="#DC2626", update=Mock())
    picker.tb_field = SimpleNamespace(
        value="#DC2626",
        on_change=on_change if on_change is not None else Mock(),
        update=Mock(),
    )
    picker.dlg = SimpleNamespace(open=dialog_open)
    picker.page = SimpleNamespace(pop_dialog=Mock())
    return picker


def test_apply_rejects_invalid_hex_and_keeps_dialog_open():
    picker = _make_picker("#12GG56")

    picker._apply(object())

    assert picker.hex_inp.error == "Mã màu không hợp lệ. Nhập theo dạng #RRGGBB."
    picker.hex_inp.update.assert_called_once_with()
    assert picker.tb_field.value == "#DC2626"
    picker.tb_field.on_change.assert_not_called()
    picker.page.pop_dialog.assert_not_called()


def test_apply_normalizes_hex_notifies_settings_and_closes_dialog():
    on_change = Mock()
    event = object()
    picker = _make_picker("  #a1b2c3  ", on_change=on_change)

    picker._apply(event)

    assert picker.hex_inp.value == "#A1B2C3"
    assert picker.hex_inp.error is None
    assert picker.container_box.bgcolor == "#A1B2C3"
    assert picker.tb_field.value == "#A1B2C3"
    on_change.assert_called_once_with(event)
    picker.page.pop_dialog.assert_called_once_with()


def test_update_from_hex_clears_error_and_updates_input():
    picker = _make_picker("a1b2c3")
    picker.hex_inp.error = "Mã màu không hợp lệ"
    picker.r_sl = SimpleNamespace(value=0, update=Mock())
    picker.g_sl = SimpleNamespace(value=0, update=Mock())
    picker.b_sl = SimpleNamespace(value=0, update=Mock())
    picker.prv = SimpleNamespace(bgcolor=None, update=Mock())

    picker._update_from_hex(object())

    assert (picker.r_sl.value, picker.g_sl.value, picker.b_sl.value) == (
        161,
        178,
        195,
    )
    assert picker.prv.bgcolor == "#A1B2C3"
    assert picker.hex_inp.error is None
    picker.hex_inp.update.assert_called_once_with()


def test_cancel_does_not_pop_a_closed_dialog():
    picker = _make_picker(dialog_open=False)

    picker._cancel(object())

    picker.page.pop_dialog.assert_not_called()
