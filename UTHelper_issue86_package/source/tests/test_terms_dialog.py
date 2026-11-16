from unittest.mock import Mock

import flet as ft

from gui.components.settings.privacy_section import (
    build_privacy_section,
    init_privacy_controls,
)
from gui.components.terms_dialog import TERMS_SECTIONS, build_terms_dialog


def test_terms_notice_describes_current_data_boundaries():
    text = " ".join(f"{title} {body}" for title, body in TERMS_SECTIONS).lower()

    for phrase in (
        "không phải sản phẩm chính thức",
        "moodle/portal uth",
        "bật mặc định",
        "không đưa mssv",
        "không lưu ip",
        "polyform noncommercial",
        "github security advisory",
    ):
        assert phrase in text


def test_terms_dialog_is_scrollable_and_closes_through_page():
    page = Mock(spec=ft.Page)
    dialog = build_terms_dialog(page)

    assert dialog.modal is True
    assert dialog.content.content.scroll == ft.ScrollMode.AUTO
    dialog.actions[0].on_click(None)
    page.pop_dialog.assert_called_once_with()


def test_privacy_settings_default_to_enabled_and_include_terms_entry(monkeypatch):
    page = Mock(spec=ft.Page)
    view = Mock()
    view._page = page
    view._build_hint.side_effect = lambda value: ft.Text(value)
    view._build_setting_group.side_effect = lambda *_args, **_kwargs: _args[2]

    init_privacy_controls(view)
    controls = build_privacy_section(view)

    assert view._dd_crash_reporting_consent.value == "enabled"
    assert [option.key for option in view._dd_crash_reporting_consent.options] == [
        "enabled",
        "disabled",
    ]
    terms_entry = controls[-1]
    terms_entry.on_click(None)
    page.show_dialog.assert_called_once()
