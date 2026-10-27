import flet as ft

from config import settings
from gui.core.theme import C
from gui.components.terms_dialog import show_terms_dialog


def init_privacy_controls(view) -> None:
    """Create the cross-platform crash-reporting consent control."""
    current = getattr(settings, "CRASH_REPORTING_CONSENT", "enabled")
    view._dd_crash_reporting_consent = ft.Dropdown(
        value="disabled" if current == "disabled" else "enabled",
        label="Gửi chẩn đoán sự cố",
        options=[
            ft.dropdown.Option("enabled", "Bật (mặc định)"),
            ft.dropdown.Option("disabled", "Tắt"),
        ],
        border_color=C.BORDER,
        focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG,
        border_radius=10,
        on_select=lambda e: getattr(view, "_on_form_field_change", lambda: None)(),
    )


def build_privacy_section(view) -> ft.Control:
    """Xây dựng nội dung chi tiết quyền riêng tư và chính sách dữ liệu."""
    terms_title = ft.Text(
        "Điều khoản sử dụng & quyền riêng tư",
        size=13,
        weight=ft.FontWeight.W_600,
        color=C.TEXT_PRIMARY,
    )
    terms_subtitle = ft.Text(
        "Xem cách ứng dụng sử dụng và bảo vệ dữ liệu",
        size=11,
        color=C.TEXT_SECONDARY,
    )
    terms_entry = ft.Container(
        content=ft.Semantics(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.DESCRIPTION_OUTLINED, size=20, color=C.ACCENT),
                    ft.Column(
                        [terms_title, terms_subtitle],
                        spacing=2,
                        expand=True,
                    ),
                    ft.Icon(
                        ft.Icons.CHEVRON_RIGHT_ROUNDED,
                        size=20,
                        color=C.TEXT_SECONDARY,
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            button=True,
            label="Mở Điều khoản sử dụng và quyền riêng tư",
            on_tap_hint_text="Mở nội dung chính sách",
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=12),
        bgcolor=C.BG,
        border=ft.Border.all(1, C.BORDER),
        border_radius=10,
        ink=True,
        on_click=lambda _event: show_terms_dialog(view._page),
    )
    view._themed_texts.append(terms_title)
    view._hint_containers.append((terms_entry, terms_subtitle))
    view._terms_policy_entry = terms_entry

    return view._build_setting_group(
        "Quyền riêng tư",
        "Chẩn đoán và chính sách dữ liệu",
        [
            view._make_themed_label("Chẩn đoán sự cố"),
            view._dd_crash_reporting_consent,
            view._build_hint("Chẩn đoán ẩn danh được bật mặc định; bạn có thể tắt bất cứ lúc nào."),
            ft.Divider(height=16, color=C.BORDER),
            view._make_themed_label("Chính sách & Pháp lý"),
            terms_entry,
        ],
        icon=ft.Icons.PRIVACY_TIP_OUTLINED,
    )
