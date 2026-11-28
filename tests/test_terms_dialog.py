import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import flet as ft

from core.distribution import DistributionChannel, build_distribution_profile
from gui.components.login_dialog import show_login_dialog
from gui.components.settings.privacy_section import (
    build_privacy_section,
    init_privacy_controls,
)
from gui.components.terms_dialog import (
    TERMS_SECTIONS,
    build_terms_dialog,
    get_terms_sections,
)


def _walk_controls(root: object) -> list[object]:
    visited: list[object] = []
    stack = [root]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        visited.append(node)
        for attr in ("content", "title", "leading", "trailing"):
            child = getattr(node, attr, None)
            if child is not None:
                stack.append(child)
        for list_attr in ("controls", "actions"):
            children = getattr(node, list_attr, None)
            if isinstance(children, (list, tuple)):
                stack.extend(reversed(children))
    return visited


def test_terms_notice_describes_current_data_boundaries():
    assert len(TERMS_SECTIONS) == 7
    assert TERMS_SECTIONS[2][0] == "3. Chẩn đoán sự cố"
    assert TERMS_SECTIONS[3][0] == "4. Dịch vụ bên thứ ba"

    text = " ".join(f"{title} {body}" for title, body in TERMS_SECTIONS).lower()

    for phrase in (
        "không phải sản phẩm chính thức",
        "moodle/portal uth",
        "bật mặc định",
        "không đưa mssv",
        "không lưu ip",
        "polyform noncommercial",
        "github security advisory",
        "posthog",
        "uuidv4",
        "uuidv7",
        "$process_person_profile: false",
    ):
        assert phrase in text


def test_terms_third_party_section_discloses_posthog_across_all_channels():
    for channel, expected_store, expect_github in (
        (DistributionChannel.APK_LTS, None, True),
        (DistributionChannel.CHPLAY, "google play", False),
        (DistributionChannel.MSSTORE, "microsoft store", False),
    ):
        sections = get_terms_sections(build_distribution_profile(channel))
        assert len(sections) == 7
        title_4, body_4 = sections[3]
        assert title_4 == "4. Dịch vụ bên thứ ba"
        assert body_4.startswith("Ứng dụng có thể kết nối")
        lowered = body_4.lower()
        assert "moodle/portal uth" in lowered
        assert "sentry" in lowered
        assert "posthog" in lowered
        assert ("github" in lowered) is expect_github
        if expected_store:
            assert expected_store in lowered


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
    assert view._dd_analytics_consent.value == "enabled"
    assert view._dd_analytics_consent.label == "Phân tích sử dụng ẩn danh"
    assert [option.key for option in view._dd_analytics_consent.options] == [
        "enabled",
        "disabled",
    ]
    assert view._dd_analytics_consent in controls
    terms_entry = controls[-1]
    terms_entry.on_click(None)
    page.show_dialog.assert_called_once()


def test_privacy_controls_preserve_not_asked_consent_option(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "CRASH_REPORTING_CONSENT", "not_asked")
    monkeypatch.setattr(settings, "ANALYTICS_CONSENT", "not_asked")
    view = Mock()
    init_privacy_controls(view)

    assert view._dd_crash_reporting_consent.value == "not_asked"
    assert [option.key for option in view._dd_crash_reporting_consent.options] == [
        "not_asked",
        "enabled",
        "disabled",
    ]
    assert view._dd_analytics_consent.value == "not_asked"
    assert [option.key for option in view._dd_analytics_consent.options] == [
        "not_asked",
        "enabled",
        "disabled",
    ]


def test_login_dialog_surfaces_compact_terms_privacy_link_without_extra_onclick():
    shown_dialogs: list[ft.AlertDialog] = []
    page = SimpleNamespace(
        width=420,
        height=720,
        platform=ft.PagePlatform.WINDOWS,
        show_dialog=shown_dialogs.append,
        pop_dialog=Mock(),
        update=Mock(),
    )
    orchestrator = SimpleNamespace(client=SimpleNamespace(login=Mock()))

    asyncio.run(show_login_dialog(page, orchestrator, AsyncMock(), allow_cancel=False))

    assert len(shown_dialogs) == 1
    login_dlg = shown_dialogs[0]
    assert login_dlg.actions == []
    nodes = _walk_controls(login_dlg)

    clickable_nodes = [
        n
        for n in nodes
        if not isinstance(n, ft.TextField) and callable(getattr(n, "on_click", None))
    ]
    assert len(clickable_nodes) == 1

    tap_nodes = [
        n
        for n in nodes
        if isinstance(n, ft.GestureDetector) and callable(getattr(n, "on_tap", None))
    ]
    assert len(tap_nodes) == 1
    link_texts = [
        n.value
        for n in _walk_controls(tap_nodes[0])
        if isinstance(n, ft.Text) and isinstance(n.value, str)
    ]
    assert "Điều khoản & Quyền riêng tư" in link_texts

    tap_nodes[0].on_tap(None)
    assert len(shown_dialogs) == 2
    assert shown_dialogs[1].title.value == "Điều khoản sử dụng & quyền riêng tư"


def test_terms_dialog_clamps_dimensions_on_420x560_desktop_and_360x800_mobile():
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
