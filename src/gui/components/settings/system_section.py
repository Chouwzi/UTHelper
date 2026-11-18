import flet as ft
from core.distribution import (
    DistributionChannel,
    DistributionProfile,
    resolve_distribution_channel,
)
from gui.core.theme import C
from config import settings
import platform_utils as _pu


def _get_distribution_profile(view) -> DistributionProfile:
    profile = getattr(view, "_distribution_profile", None)
    if profile is not None:
        return profile
    return resolve_distribution_channel()


def init_system_controls(view):
    """Khởi tạo các control cấu hình vòng đời ứng dụng và tần suất đồng bộ trên hệ điều hành."""
    profile = _get_distribution_profile(view)
    view._sw_auto_update = ft.Switch(
        value=getattr(settings, "AUTO_UPDATE_ENABLED", True),
        active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Tự động kiểm tra cập nhật",
        on_change=lambda e: getattr(view, "_on_form_field_change", lambda: None)(),
        visible=profile.supports_github_self_update,
    )
    view._check_update_btn = ft.Button(
        "Kiểm tra ngay",
        on_click=getattr(view, "_handle_check_update", None),
        bgcolor=C.SURFACE,
        color=C.TEXT_PRIMARY,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
        visible=profile.supports_github_self_update,
    )

    def _on_store_link_click(event=None):
        handler = getattr(view, "_handle_open_store_url", None)
        if callable(handler):
            handler(event)

    view._store_update_info_text = ft.Text(
        "",
        size=13,
        color=C.TEXT_PRIMARY,
        weight=ft.FontWeight.W_500,
        visible=not profile.supports_github_self_update,
    )
    view._store_link_btn = ft.Button(
        "",
        icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
        on_click=_on_store_link_click,
        bgcolor=C.SURFACE,
        color=C.TEXT_PRIMARY,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
        visible=False,
    )
    # Thiết lập riêng trên Desktop (Windows)
    view._sw_start_with_windows = ft.Switch(
        value=settings.START_WITH_WINDOWS, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Khởi động cùng Windows",
        on_change=view._on_autostart_toggle,
    )
    view._sw_start_minimized = ft.Switch(
        value=settings.START_MINIMIZED, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Ẩn xuống khay khi khởi động",
        disabled=not settings.START_WITH_WINDOWS,
        on_change=lambda e: getattr(view, "_on_form_field_change", lambda: None)(),
    )
    view._autostart_status = ft.Text("", size=11, color=C.TEXT_SECONDARY)
    view._sw_start_minimized_container = ft.Container(
        content=ft.Column(
            controls=[view._sw_start_minimized, view._autostart_status],
            spacing=2,
        ),
        padding=ft.Padding.only(left=24),
        visible=bool(settings.START_WITH_WINDOWS),
    )
    view._sw_minimize_to_tray = ft.Switch(
        value=settings.MINIMIZE_TO_TRAY, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Thu nhỏ vào khay hệ thống",
        on_change=lambda e: getattr(view, "_on_form_field_change", lambda: None)(),
    )
    
    # Thiết lập riêng trên Mobile (Android)
    view._sw_bg_check = ft.Switch(
        value=settings.BACKGROUND_CHECK_ANDROID, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Đồng bộ hoạt động nền (Android, best-effort)",
        on_change=lambda e: (view._toggle_bg_check_ui(), getattr(view, "_on_form_field_change", lambda: None)())
    )
    
    # Đồng bộ hóa dữ liệu Moodle chung
    interval_options = [
        ft.dropdown.Option("0", "Tắt tự động"),
        ft.dropdown.Option("60", "Mỗi 1 giờ (mặc định)"),
        ft.dropdown.Option("180", "Mỗi 3 giờ"),
        ft.dropdown.Option("360", "Mỗi 6 giờ"),
    ]
    current_interval = str(settings.CHECK_INTERVAL_MINUTES)
    if current_interval not in {"0", "60", "180", "360"}:
        interval_options.append(
            ft.dropdown.Option(current_interval, f"Tùy chỉnh cũ: {current_interval} phút")
        )
    view._interval_field = ft.Dropdown(
        value=str(settings.CHECK_INTERVAL_MINUTES),
        label="Tần suất đồng bộ hoạt động",
        options=interval_options,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
        on_select=lambda e: getattr(view, "_on_form_field_change", lambda: None)(),
    )
    # Cấu hình số lượng tháng lịch cần quét sự kiện
    view._fetch_months_field = ft.TextField(
        value=str(settings.FETCH_MONTHS),
        label="Số tháng lấy sự kiện (1-3)",
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.NumbersOnlyInputFilter(),
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
        on_change=lambda e: getattr(view, "_on_form_field_change", lambda: None)(),
    )

def build_system_section(view) -> ft.Control:
    """Xây dựng nội dung chi tiết cấu hình hệ thống."""
    profile = _get_distribution_profile(view)
    if hasattr(view, "_sw_auto_update"):
        view._sw_auto_update.visible = profile.supports_github_self_update
    if hasattr(view, "_check_update_btn"):
        view._check_update_btn.visible = profile.supports_github_self_update

    controls = []
    if not _pu.UI_IS_MOBILE:
        controls.extend([
            view._make_themed_label("Khởi động"),
            view._sw_start_with_windows,
            view._sw_start_minimized_container,
            view._sw_minimize_to_tray,
            ft.Divider(height=16, color=C.BORDER),
        ])
    else:
        controls.extend([
            view._make_themed_label("Hoạt động nền"),
            view._sw_bg_check,
            view._build_hint("Android dùng chu kỳ đồng bộ khi worker native khả dụng."),
            ft.Divider(height=16, color=C.BORDER),
        ])

    controls.append(view._make_themed_label("Cập nhật"))
    if profile.supports_github_self_update:
        controls.extend([
            view._sw_auto_update,
            ft.Row([view._check_update_btn], alignment=ft.MainAxisAlignment.START),
        ])
        update_status_group = getattr(view, "_update_status_group", None)
        if update_status_group is not None:
            controls.append(update_status_group)
    else:
        store_info_text = getattr(view, "_store_update_info_text", None)
        if store_info_text is None:
            store_info_text = ft.Text(
                "",
                size=13,
                color=C.TEXT_PRIMARY,
                weight=ft.FontWeight.W_500,
            )
            view._store_update_info_text = store_info_text
        store_link_btn = getattr(view, "_store_link_btn", None)
        if store_link_btn is None:
            store_link_btn = ft.Button(
                "",
                icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                on_click=lambda e=None: (
                    getattr(view, "_handle_open_store_url", None)(e)
                    if callable(getattr(view, "_handle_open_store_url", None))
                    else None
                ),
                bgcolor=C.SURFACE,
                color=C.TEXT_PRIMARY,
                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
                visible=False,
            )
            view._store_link_btn = store_link_btn
        store_info_text.visible = True

        if profile.channel is DistributionChannel.CHPLAY:
            store_info_text.value = "Cập nhật được quản lý bởi Google Play"
            hint = view._build_hint(
                "Google Play sẽ tự động quản lý và cập nhật ứng dụng khi có phiên bản mới."
            )
            controls.extend([store_info_text, hint])
            if profile.store_url:
                store_link_btn.content = "Mở trên Google Play"
                store_link_btn.visible = True
                controls.append(
                    ft.Row([store_link_btn], alignment=ft.MainAxisAlignment.START)
                )
            else:
                store_link_btn.visible = False
        else:
            store_name = profile.store_display_name or "Microsoft Store"
            store_info_text.value = f"Cập nhật được quản lý bởi {store_name}"
            controls.append(store_info_text)
            if profile.store_url:
                controls.append(
                    view._build_hint(
                        f"{store_name} sẽ tự động quản lý và cập nhật ứng dụng này."
                    )
                )
                store_link_btn.content = f"Mở trong {store_name}"
                store_link_btn.visible = True
                controls.append(
                    ft.Row([store_link_btn], alignment=ft.MainAxisAlignment.START)
                )
            else:
                controls.append(
                    view._build_hint(
                        f"Bạn có thể kiểm tra cập nhật trong ứng dụng {store_name} (Thư viện)."
                    )
                )
                store_link_btn.visible = False
    controls.extend([
        ft.Divider(height=16, color=C.BORDER),
        view._make_themed_label("Đồng bộ dữ liệu"),
        view._interval_field,
        view._build_hint("Áp dụng cho ứng dụng, khay hệ thống và đồng bộ nền."),
        view._fetch_months_field,
        view._build_hint("Số tháng cần lấy sự kiện (1-3). (Mặc định 1)"),
    ])

    return ft.Column(
        controls=controls,
        spacing=10,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
