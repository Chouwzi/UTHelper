import re
with open('src/gui/components/settings_view.py', 'r', encoding='utf-8') as f:
    text = f.read()

new_fields = '''        self._sw_email = ft.Switch(
            value=settings.ENABLE_GMAIL, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bật gửi qua Gmail",
            on_change=lambda e: self._toggle_integration_ui()
        )
        self._gmail_addr_field = ft.TextField(
            value=getattr(settings, 'GMAIL_ADDRESS', ''),
            label="Địa chỉ Email",
            visible=settings.ENABLE_GMAIL,
            border_color=C.BORDER, focused_border_color=C.ACCENT, color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        )
        self._gmail_pw_field = ft.TextField(
            value=getattr(settings, 'GMAIL_APP_PASSWORD', ''),
            label="Mật khẩu ứng dụng Gmail",
            password=True, can_reveal_password=True,
            visible=settings.ENABLE_GMAIL,
            border_color=C.BORDER, focused_border_color=C.ACCENT, color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        )

        self._sw_discord = ft.Switch(
            value=settings.ENABLE_DISCORD, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bật gửi qua Discord",
            on_change=lambda e: self._toggle_integration_ui()
        )
        self._discord_wh_field = ft.TextField(
            value=getattr(settings, 'DISCORD_WEBHOOK_URL', ''),
            label="Discord Webhook URL",
            visible=settings.ENABLE_DISCORD,
            border_color=C.BORDER, focused_border_color=C.ACCENT, color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        )'''

text = re.sub(r'        self\._sw_email = ft\.Switch\(.*?label="Bật gửi qua Discord.*?\)', new_fields, text, flags=re.DOTALL)

text = text.replace('''                                self._sw_email,
                                self._sw_discord,''', '''                                self._sw_email,
                                self._gmail_addr_field,
                                self._gmail_pw_field,
                                ft.Divider(height=10, color=C.BORDER),
                                self._sw_discord,
                                self._discord_wh_field,
                                ft.Divider(height=10, color=C.BORDER),''')

text = text.replace('''    def _toggle_telegram_ui(self):
        v = self._sw_telegram.value''', '''    def _toggle_integration_ui(self):
        self._gmail_addr_field.visible = self._sw_email.value
        self._gmail_pw_field.visible = self._sw_email.value
        self._discord_wh_field.visible = self._sw_discord.value
        self._gmail_addr_field.update()
        self._gmail_pw_field.update()
        self._discord_wh_field.update()

    def _toggle_telegram_ui(self):
        v = self._sw_telegram.value''')

text = text.replace('''        self._sw_email.value = settings.ENABLE_GMAIL
        self._sw_discord.value = settings.ENABLE_DISCORD''', '''        self._sw_email.value = settings.ENABLE_GMAIL
        self._sw_discord.value = getattr(settings, 'ENABLE_DISCORD', False)
        self._gmail_addr_field.value = getattr(settings, 'GMAIL_ADDRESS', '')
        self._gmail_pw_field.value = getattr(settings, 'GMAIL_APP_PASSWORD', '')
        self._discord_wh_field.value = getattr(settings, 'DISCORD_WEBHOOK_URL', '')
        self._toggle_integration_ui()''')

text = text.replace('''        if self._sw_email.value != settings.ENABLE_GMAIL: return True
        if self._sw_discord.value != settings.ENABLE_DISCORD: return True''', '''        if self._sw_email.value != getattr(settings, 'ENABLE_GMAIL', False): return True
        if self._sw_discord.value != getattr(settings, 'ENABLE_DISCORD', False): return True
        if getattr(self, '_gmail_addr_field', None) and self._gmail_addr_field.value != getattr(settings, 'GMAIL_ADDRESS', ''): return True
        if getattr(self, '_gmail_pw_field', None) and self._gmail_pw_field.value != getattr(settings, 'GMAIL_APP_PASSWORD', ''): return True
        if getattr(self, '_discord_wh_field', None) and self._discord_wh_field.value != getattr(settings, 'DISCORD_WEBHOOK_URL', ''): return True''')

text = text.replace('''            settings.ENABLE_GMAIL            = self._sw_email.value
            settings.ENABLE_DISCORD          = self._sw_discord.value''', '''            settings.ENABLE_GMAIL            = self._sw_email.value
            settings.ENABLE_DISCORD          = self._sw_discord.value
            if hasattr(self, '_gmail_addr_field'):
                settings.GMAIL_ADDRESS = self._gmail_addr_field.value
                settings.GMAIL_APP_PASSWORD = self._gmail_pw_field.value
                settings.DISCORD_WEBHOOK_URL = self._discord_wh_field.value
                _save_setting("GMAIL_ADDRESS", settings.GMAIL_ADDRESS)
                _save_setting("GMAIL_APP_PASSWORD", settings.GMAIL_APP_PASSWORD)
                _save_setting("DISCORD_WEBHOOK_URL", settings.DISCORD_WEBHOOK_URL)''')

with open('src/gui/components/settings_view.py', 'w', encoding='utf-8') as f:
    f.write(text)
