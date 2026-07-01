import flet as ft
from gui.core.theme import C
from config import settings

def init_integration_controls(view):
    """Khởi tạo các control thiết lập kênh thông báo tích hợp bên ngoài (Gmail, Discord, Telegram)."""
    # Tích hợp Gmail
    view._sw_email = ft.Switch(
        value=settings.ENABLE_GMAIL, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Kích hoạt thông báo qua Gmail",
        on_change=lambda e: view._toggle_integration_ui()
    )
    view._gmail_addr_field = ft.TextField(
        value=getattr(settings, 'GMAIL_ADDRESS', ''),
        label="Địa chỉ Email",
        visible=settings.ENABLE_GMAIL,
        border_color=C.BORDER, focused_border_color=C.ACCENT, color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
    )
    view._gmail_pw_field = ft.TextField(
        value=getattr(settings, 'GMAIL_APP_PASSWORD', ''),
        label="Mật khẩu ứng dụng Gmail",
        password=True, can_reveal_password=True,
        visible=settings.ENABLE_GMAIL,
        border_color=C.BORDER, focused_border_color=C.ACCENT, color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
    )

    # Tích hợp Discord
    view._sw_discord = ft.Switch(
        value=settings.ENABLE_DISCORD, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Kích hoạt thông báo qua Discord",
        on_change=lambda e: view._toggle_integration_ui()
    )
    view._discord_wh_field = ft.TextField(
        value=getattr(settings, 'DISCORD_WEBHOOK_URL', ''),
        label="Discord Webhook URL",
        visible=settings.ENABLE_DISCORD,
        border_color=C.BORDER, focused_border_color=C.ACCENT, color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
    )

    # Tích hợp Telegram
    view._sw_telegram = ft.Switch(
        value=settings.ENABLE_TELEGRAM, active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Kích hoạt thông báo qua Telegram",
        on_change=lambda e: view._toggle_telegram_ui()
    )
    view._tel_token_field = ft.TextField(
        value=settings.TELEGRAM_BOT_TOKEN,
        label="Bot Token",
        text_size=13,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        visible=settings.ENABLE_TELEGRAM
    )
    view._tel_chat_field = ft.TextField(
        value=settings.TELEGRAM_CHAT_ID,
        label="Chat ID",
        text_size=13,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        visible=settings.ENABLE_TELEGRAM
    )

def build_integration_section(view) -> ft.Container:
    """Xây dựng Container chứa toàn bộ các control tích hợp thông báo bên ngoài."""
    return view._build_setting_group(
        "Tích hợp",
        "Nhắn tin qua Bot & Email",
        [
            view._sw_email,
            view._gmail_addr_field,
            view._gmail_pw_field,
            ft.Divider(height=10, color=C.BORDER),
            view._sw_discord,
            view._discord_wh_field,
            ft.Divider(height=10, color=C.BORDER),
            view._sw_telegram,
            view._tel_token_field,
            view._tel_chat_field,
        ],
        icon=ft.Icons.INTEGRATION_INSTRUCTIONS_OUTLINED,
    )
