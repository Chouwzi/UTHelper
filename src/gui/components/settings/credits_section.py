"""Refined About and Credits detail view for SettingsView."""

from __future__ import annotations

import flet as ft

from core.distribution import DistributionProfile, resolve_distribution_channel
from core.version import APP_VERSION
from gui.core.theme import C


FOUNDER_GITHUB_URL = "https://github.com/Chouwzi"
PROJECT_GITHUB_URL = "https://github.com/Chouwzi/UTHelper"

COMMUNITY_CONTRIBUTORS: tuple[tuple[str, str], ...] = (
    (
        "Nguyễn Trương Bảo (@Baong123)",
        "Đóng góp nhánh thử nghiệm nhắc lịch học và hồ sơ học phí",
    ),
    (
        "@nguyen2006-dev",
        "Đóng góp cấu hình chuẩn bị phân phối trên iOS",
    ),
    (
        "ThienNguyen-Dev (@ThienNguyen0210)",
        "Đóng góp nhánh thử nghiệm lộ trình học tập",
    ),
)


def _get_distribution_profile(view) -> DistributionProfile:
    profile = getattr(view, "_distribution_profile", None)
    if profile is not None:
        return profile
    return resolve_distribution_channel()


def _open_external_link(view, url: str) -> None:
    launcher = getattr(view, "_launch_external_store_url", None)
    if callable(launcher):
        launcher(url)


def refresh_credits_section_colors(view) -> None:
    """Re-apply active semantic palette tokens across all mounted credits controls."""
    refs = getattr(view, "_credits_theme_refs", None)
    if not isinstance(refs, dict):
        return

    for card_key in ("identity_card", "contributors_card"):
        card = refs.get(card_key)
        if isinstance(card, ft.Container):
            card.bgcolor = C.SURFACE
            card.border = ft.Border.all(1, C.BORDER)

    founder_card = refs.get("founder_card")
    if isinstance(founder_card, ft.Container):
        founder_card.bgcolor = C.SURFACE
        founder_card.border = ft.Border.all(
            1.2, ft.Colors.with_opacity(0.32, C.ACCENT)
        )

    for badge_key, opacity in (
        ("app_icon_box", 0.10),
        ("version_badge", 0.12),
        ("founder_avatar", 0.12),
        ("founder_handle_badge", 0.10),
    ):
        badge = refs.get(badge_key)
        if isinstance(badge, ft.Container):
            badge.bgcolor = ft.Colors.with_opacity(opacity, C.ACCENT)

    channel_badge = refs.get("channel_badge")
    if isinstance(channel_badge, ft.Container):
        channel_badge.bgcolor = C.BG
        channel_badge.border = ft.Border.all(1, C.BORDER)

    for avatar in refs.get("contributor_avatars", ()):
        if isinstance(avatar, ft.Container):
            avatar.bgcolor = C.BG
            avatar.border = ft.Border.all(1, C.BORDER)

    for icon in refs.get("accent_icons", ()):
        if isinstance(icon, ft.Icon):
            icon.color = C.ACCENT

    for icon in refs.get("secondary_icons", ()):
        if isinstance(icon, ft.Icon):
            icon.color = C.TEXT_SECONDARY

    for text in refs.get("primary_texts", ()):
        if isinstance(text, ft.Text):
            text.color = C.TEXT_PRIMARY

    for text in refs.get("secondary_texts", ()):
        if isinstance(text, ft.Text):
            text.color = C.TEXT_SECONDARY

    for text in refs.get("accent_texts", ()):
        if isinstance(text, ft.Text):
            text.color = C.ACCENT

    for divider in refs.get("dividers", ()):
        if isinstance(divider, ft.Divider):
            divider.color = C.BORDER

    for button in refs.get("accent_buttons", ()):
        if isinstance(button, ft.TextButton):
            button.style = ft.ButtonStyle(
                color=C.ACCENT,
                overlay_color=ft.Colors.with_opacity(0.08, C.ACCENT),
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            )


def build_credits_section(view) -> ft.Control:
    """Build the subtle, Apple-HIG inspired About detail view."""
    profile = _get_distribution_profile(view)

    primary_texts: list[ft.Text] = []
    secondary_texts: list[ft.Text] = []
    accent_texts: list[ft.Text] = []
    accent_icons: list[ft.Icon] = []
    secondary_icons: list[ft.Icon] = []
    contributor_avatars: list[ft.Container] = []
    dividers: list[ft.Divider] = []
    accent_buttons: list[ft.TextButton] = []

    # 1. Compact App Identity Banner
    app_icon_box = ft.Container(
        content=ft.Image(
            src="icon.png",
            width=24,
            height=24,
            fit=ft.BoxFit.CONTAIN,
        ),
        width=38,
        height=38,
        border_radius=10,
        bgcolor=ft.Colors.with_opacity(0.10, C.ACCENT),
        alignment=ft.Alignment(0, 0),
    )
    app_name_text = ft.Text(
        "UTHelper",
        size=16,
        weight=ft.FontWeight.W_700,
        color=C.TEXT_PRIMARY,
    )
    primary_texts.append(app_name_text)

    version_badge_text = ft.Text(
        f"v{APP_VERSION}",
        size=11,
        weight=ft.FontWeight.W_600,
        color=C.ACCENT,
    )
    accent_texts.append(version_badge_text)
    version_badge = ft.Container(
        content=version_badge_text,
        bgcolor=ft.Colors.with_opacity(0.12, C.ACCENT),
        border_radius=999,
        padding=ft.Padding.symmetric(horizontal=8, vertical=3),
    )

    identity_header_controls: list[ft.Control] = [app_name_text, version_badge]
    channel_badge: ft.Container | None = None
    if not profile.supports_github_self_update and profile.store_display_name:
        channel_text = ft.Text(
            profile.store_display_name,
            size=11,
            weight=ft.FontWeight.W_500,
            color=C.TEXT_SECONDARY,
        )
        secondary_texts.append(channel_text)
        channel_badge = ft.Container(
            content=channel_text,
            bgcolor=C.BG,
            border=ft.Border.all(1, C.BORDER),
            border_radius=999,
            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
        )
        identity_header_controls.append(channel_badge)

    app_description_text = ft.Text(
        "Ứng dụng độc lập hỗ trợ sinh viên UTH theo dõi bài tập, lịch học, tài liệu môn học và kết quả học tập.",
        size=12,
        color=C.TEXT_SECONDARY,
    )
    secondary_texts.append(app_description_text)

    identity_card = ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        app_icon_box,
                        ft.Column(
                            controls=[
                                ft.Row(
                                    controls=identity_header_controls,
                                    spacing=8,
                                    run_spacing=4,
                                    wrap=True,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                app_description_text,
            ],
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        bgcolor=C.SURFACE,
        border=ft.Border.all(1, C.BORDER),
        border_radius=14,
        padding=16,
    )

    # 2. Prominent Primary Card - Founder and Lead Architect
    founder_section_label = ft.Text(
        "Tác giả chính",
        size=11.5,
        weight=ft.FontWeight.W_600,
        color=C.ACCENT,
    )
    accent_texts.append(founder_section_label)

    founder_icon = ft.Icon(
        ft.Icons.WORKSPACE_PREMIUM_ROUNDED,
        size=22,
        color=C.ACCENT,
    )
    accent_icons.append(founder_icon)
    founder_avatar = ft.Container(
        content=founder_icon,
        width=44,
        height=44,
        border_radius=12,
        bgcolor=ft.Colors.with_opacity(0.12, C.ACCENT),
        alignment=ft.Alignment(0, 0),
    )

    founder_name = ft.Text(
        "Nguyễn Trần Đình Chương",
        size=14.5,
        weight=ft.FontWeight.W_700,
        color=C.TEXT_PRIMARY,
    )
    primary_texts.append(founder_name)

    founder_handle = ft.Text(
        "@Chouwzi",
        size=11.5,
        weight=ft.FontWeight.W_600,
        color=C.ACCENT,
    )
    accent_texts.append(founder_handle)
    founder_handle_badge = ft.Container(
        content=founder_handle,
        bgcolor=ft.Colors.with_opacity(0.10, C.ACCENT),
        border_radius=6,
        padding=ft.Padding.symmetric(horizontal=7, vertical=2),
    )

    founder_role = ft.Text(
        "Khởi xướng dự án - Kiến trúc hệ thống và phát triển chính",
        size=12,
        weight=ft.FontWeight.W_600,
        color=C.TEXT_PRIMARY,
    )
    primary_texts.append(founder_role)

    founder_scope = ft.Text(
        "Xây dựng và phát triển xuyên suốt nền tảng lõi, giao diện đa thiết bị (Windows, Android, iOS), trình đọc tài liệu, hệ thống đồng bộ, bảo mật và đóng gói phát hành từ tháng 03/2026.",
        size=11.5,
        color=C.TEXT_SECONDARY,
    )
    secondary_texts.append(founder_scope)

    founder_divider = ft.Divider(height=1, color=C.BORDER)
    dividers.append(founder_divider)

    founder_github_btn = ft.TextButton(
        "Trang GitHub tác giả (@Chouwzi)",
        icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
        on_click=lambda _: _open_external_link(view, FOUNDER_GITHUB_URL),
        style=ft.ButtonStyle(
            color=C.ACCENT,
            overlay_color=ft.Colors.with_opacity(0.08, C.ACCENT),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        ),
    )
    accent_buttons.append(founder_github_btn)

    founder_card = ft.Container(
        content=ft.Column(
            controls=[
                founder_section_label,
                ft.Row(
                    controls=[
                        founder_avatar,
                        ft.Column(
                            controls=[
                                ft.Row(
                                    controls=[founder_name, founder_handle_badge],
                                    spacing=8,
                                    run_spacing=4,
                                    wrap=True,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                founder_role,
                                founder_scope,
                            ],
                            spacing=4,
                            expand=True,
                        ),
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                founder_divider,
                ft.Row(
                    controls=[founder_github_btn],
                    alignment=ft.MainAxisAlignment.START,
                ),
            ],
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        bgcolor=C.SURFACE,
        border=ft.Border.all(1.2, ft.Colors.with_opacity(0.32, C.ACCENT)),
        border_radius=14,
        padding=16,
    )

    # 3. Subordinate Secondary Card - Community Feature Contributors
    contributors_title = ft.Text(
        "Đóng góp cộng đồng",
        size=12.5,
        weight=ft.FontWeight.W_600,
        color=C.TEXT_PRIMARY,
    )
    primary_texts.append(contributors_title)

    contributors_caption = ft.Text(
        "Đóng góp tính năng thử nghiệm",
        size=11.5,
        weight=ft.FontWeight.W_600,
        color=C.TEXT_SECONDARY,
    )
    secondary_texts.append(contributors_caption)

    contributor_controls: list[ft.Control] = [
        ft.Column(
            controls=[contributors_title, contributors_caption],
            spacing=2,
        )
    ]

    for index, (display_name, scope_note) in enumerate(COMMUNITY_CONTRIBUTORS):
        if index > 0:
            row_divider = ft.Divider(height=1, color=C.BORDER)
            dividers.append(row_divider)
            contributor_controls.append(row_divider)

        person_icon = ft.Icon(
            ft.Icons.PERSON_OUTLINE_ROUNDED,
            size=16,
            color=C.TEXT_SECONDARY,
        )
        secondary_icons.append(person_icon)

        person_avatar = ft.Container(
            content=person_icon,
            width=30,
            height=30,
            border_radius=15,
            bgcolor=C.BG,
            border=ft.Border.all(1, C.BORDER),
            alignment=ft.Alignment(0, 0),
        )
        contributor_avatars.append(person_avatar)

        name_text = ft.Text(
            display_name,
            size=12.5,
            weight=ft.FontWeight.W_600,
            color=C.TEXT_PRIMARY,
        )
        primary_texts.append(name_text)

        note_text = ft.Text(
            scope_note,
            size=11.5,
            color=C.TEXT_SECONDARY,
        )
        secondary_texts.append(note_text)

        contributor_controls.append(
            ft.Row(
                controls=[
                    person_avatar,
                    ft.Column(
                        controls=[name_text, note_text],
                        spacing=2,
                        expand=True,
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        )

    contributors_card = ft.Container(
        content=ft.Column(
            controls=contributor_controls,
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        bgcolor=C.SURFACE,
        border=ft.Border.all(1, C.BORDER),
        border_radius=14,
        padding=ft.Padding.symmetric(horizontal=14, vertical=12),
    )

    # 4. Quiet Project and License Footer
    project_github_btn = ft.TextButton(
        "Mã nguồn dự án trên GitHub",
        icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
        on_click=lambda _: _open_external_link(view, PROJECT_GITHUB_URL),
        style=ft.ButtonStyle(
            color=C.ACCENT,
            overlay_color=ft.Colors.with_opacity(0.08, C.ACCENT),
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        ),
    )
    accent_buttons.append(project_github_btn)

    disclaimer_text = ft.Text(
        "Dự án độc lập vì sinh viên, không đại diện cho cổng thông tin chính thức của Trường ĐH Giao thông vận tải TP.HCM.",
        size=11,
        color=C.TEXT_SECONDARY,
        text_align=ft.TextAlign.CENTER,
    )
    secondary_texts.append(disclaimer_text)

    footer_block = ft.Container(
        content=ft.Column(
            controls=[
                project_github_btn,
                disclaimer_text,
            ],
            spacing=4,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.only(top=2, bottom=10, left=8, right=8),
        alignment=ft.Alignment(0, 0),
    )

    # Expose semantic refs for deterministic live theme refresh and test verification.
    view._credits_identity_card = identity_card
    view._credits_founder_card = founder_card
    view._credits_contributors_card = contributors_card
    view._credits_version_badge = version_badge
    view._credits_version_badge_text = version_badge_text
    view._credits_founder_name = founder_name
    view._credits_founder_handle = founder_handle
    view._credits_disclaimer_text = disclaimer_text
    view._credits_theme_refs = {
        "identity_card": identity_card,
        "founder_card": founder_card,
        "contributors_card": contributors_card,
        "app_icon_box": app_icon_box,
        "version_badge": version_badge,
        "channel_badge": channel_badge,
        "founder_avatar": founder_avatar,
        "founder_handle_badge": founder_handle_badge,
        "contributor_avatars": contributor_avatars,
        "accent_icons": accent_icons,
        "secondary_icons": secondary_icons,
        "primary_texts": primary_texts,
        "secondary_texts": secondary_texts,
        "accent_texts": accent_texts,
        "dividers": dividers,
        "accent_buttons": accent_buttons,
    }

    return ft.Column(
        controls=[
            identity_card,
            founder_card,
            contributors_card,
            footer_block,
        ],
        spacing=12,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
