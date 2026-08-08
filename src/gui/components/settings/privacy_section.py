import flet as ft

from config import settings
from gui.core.theme import C


def init_privacy_controls(view) -> None:
    """Create the cross-platform crash-reporting consent control."""
    view._dd_crash_reporting_consent = ft.Dropdown(
        value=getattr(settings, "CRASH_REPORTING_CONSENT", "not_asked"),
        label="Gửi chẩn đoán sự cố",
        options=[
            ft.dropdown.Option("not_asked", "Chưa quyết định"),
            ft.dropdown.Option("enabled", "Cho phép"),
            ft.dropdown.Option("disabled", "Không cho phép"),
        ],
        border_color=C.BORDER,
        focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG,
        border_radius=10,
    )


def build_privacy_section(view) -> ft.Container:
    """Render consent on every platform without translating stored values."""
    return view._build_setting_group(
        "Quyền riêng tư",
        "Lựa chọn gửi chẩn đoán sự cố",
        [
            view._dd_crash_reporting_consent,
            view._build_hint("Bạn có thể thay đổi lựa chọn này bất cứ lúc nào."),
        ],
        icon=ft.Icons.PRIVACY_TIP_OUTLINED,
    )
