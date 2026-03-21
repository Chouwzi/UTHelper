import sys, os, re

path = r'src/gui/components/settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

# 1. Add fields in __init__
old_discord = '''        self._sw_discord = ft.Switch(
            value=settings.ENABLE_DISCORD, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bật gửi qua Discord (Future)"
        )'''

new_discord = '''        self._sw_discord = ft.Switch(
            value=settings.ENABLE_DISCORD, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bật gửi qua Discord (Future)"
        )

        self._sw_telegram = ft.Switch(
            value=settings.ENABLE_TELEGRAM, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bật gửi qua Telegram Bot",
            on_change=lambda e: self._toggle_telegram_ui()
        )
        self._tel_token_field = ft.TextField(
            value=settings.TELEGRAM_BOT_TOKEN,
            label="Bot Token",
            text_size=13,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, bgcolor=C.SURFACE, border_radius=10,
            visible=settings.ENABLE_TELEGRAM
        )
        self._tel_chat_field = ft.TextField(
            value=settings.TELEGRAM_CHAT_ID,
            label="Chat ID",
            text_size=13,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, bgcolor=C.SURFACE, border_radius=10,
            visible=settings.ENABLE_TELEGRAM
        )'''

if "self._sw_telegram = ft.Switch" not in text:
    text = text.replace(old_discord, new_discord)

# 2. Add methods
old_toggle_debug = '''    def _toggle_debug_ui(self):'''
new_toggle = '''    def _toggle_telegram_ui(self):
        v = self._sw_telegram.value
        self._tel_token_field.visible = v
        self._tel_chat_field.visible = v
        self._tel_token_field.update()
        self._tel_chat_field.update()

    def _toggle_debug_ui(self):'''

if "def _toggle_telegram_ui" not in text:
    text = text.replace(old_toggle_debug, new_toggle)
    
# 3. Modify self.content column layout
old_layout = '''                        _section("TÍCH HỢP & BOT"),
                        _card(self._sw_discord),'''

new_layout = '''                        _section("TÍCH HỢP & BOT"),
                        _card(self._sw_discord, self._sw_telegram, self._tel_token_field, self._tel_chat_field),'''

if "self._sw_discord, self._sw_telegram" not in text:
    text = text.replace(old_layout, new_layout)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done init and layout")
