import os
import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.components.settings_view import SettingsView


def test_milestone_handler_keeps_flet_updated_chip_state():
    """Chip.on_select already flips selected before invoking the callback."""
    selected_chip = SimpleNamespace(selected=False)
    other_chip = SimpleNamespace(selected=True)
    view = SimpleNamespace(
        _milestone_chips={10080: selected_chip, 4320: other_chip},
        _milestones_field=SimpleNamespace(value=""),
        _milestone_summary=SimpleNamespace(value=""),
        update=lambda: None,
    )

    handler = SettingsView._handle_milestone_toggle(view, 10080)
    handler(SimpleNamespace(control=selected_chip))

    assert selected_chip.selected is False
    assert view._milestones_field.value == "4320"
    assert view._milestone_summary.value == "Bạn sẽ nhận 1 lần nhắc cho mỗi deadline"


def test_balanced_profile_summary_lists_all_default_milestones():
    view = SimpleNamespace(
        _current_profile="balanced",
        _profile_summary=SimpleNamespace(value=""),
    )

    SettingsView._update_profile_summary(view)

    assert view._profile_summary.value == (
        "Nhắc 3 ngày, 1 ngày, 3 giờ, 1 giờ, 30 phút và 5 phút trước deadline"
    )


def test_save_dialog_stays_open_when_validation_fails():
    close = Mock()
    view = SimpleNamespace(_save=AsyncMock(return_value=False), _on_close_cb=close)

    result = asyncio.run(SettingsView._save_and_close_if_valid(view, None))

    assert result is False
    close.assert_not_called()
