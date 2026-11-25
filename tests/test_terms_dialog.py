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


def test_privacy_controls_preserve_not_asked_consent_option(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "CRASH_REPORTING_CONSENT", "not_asked")
    view = Mock()
    init_privacy_controls(view)

    assert view._dd_crash_reporting_consent.value == "not_asked"
    assert [option.key for option in view._dd_crash_reporting_consent.options] == [
        "not_asked",
        "enabled",
        "disabled",
    ]


def test_terms_dialog_clamps_dimensions_on_420x560_desktop_and_360x800_mobile():
    from types import SimpleNamespace

    desktop_min_page = SimpleNamespace(
        width=420,
        height=560,
        platform=ft.PagePlatform.WINDOWS,
        pop_dialog=Mock(),
    )
    dlg_desktop = build_terms_dialog(desktop_min_page)
    assert dlg_desktop.content.width <= 340
    assert dlg_desktop.content.height <= 370

    mobile_page = SimpleNamespace(
        width=360,
        height=800,
        platform=ft.PagePlatform.ANDROID,
        pop_dialog=Mock(),
    )
    dlg_mobile = build_terms_dialog(mobile_page)
    assert dlg_mobile.content.width is None
    assert dlg_mobile.content.height <= 590

