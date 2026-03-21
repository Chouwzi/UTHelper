import sys, os, re

path = r'src/gui/components/settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

# load_current_settings
old_load = '''        self._sw_discord.value = settings.ENABLE_DISCORD'''
new_load = '''        self._sw_discord.value = settings.ENABLE_DISCORD
        self._sw_telegram.value = settings.ENABLE_TELEGRAM
        self._tel_token_field.value = settings.TELEGRAM_BOT_TOKEN
        self._tel_chat_field.value = settings.TELEGRAM_CHAT_ID
        self._toggle_telegram_ui()'''

if "self._sw_telegram.value = settings.ENABLE_TELEGRAM" not in text:
    text = text.replace(old_load, new_load)

# has_changes
old_has_changes = '''        if self._sw_discord.value != settings.ENABLE_DISCORD: return True'''
new_has_changes = '''        if self._sw_discord.value != settings.ENABLE_DISCORD: return True
        if self._sw_telegram.value != settings.ENABLE_TELEGRAM: return True
        if self._tel_token_field.value != settings.TELEGRAM_BOT_TOKEN: return True
        if self._tel_chat_field.value != settings.TELEGRAM_CHAT_ID: return True'''

if "self._sw_telegram.value != settings.ENABLE_TELEGRAM: return True" not in text:
    text = text.replace(old_has_changes, new_has_changes)

# _save_setting
old_save = '''        settings.ENABLE_DISCORD = self._sw_discord.value'''
new_save = '''        settings.ENABLE_DISCORD = self._sw_discord.value
        settings.ENABLE_TELEGRAM = self._sw_telegram.value
        settings.TELEGRAM_BOT_TOKEN = self._tel_token_field.value
        settings.TELEGRAM_CHAT_ID = self._tel_chat_field.value'''

if "settings.ENABLE_TELEGRAM = self._sw_telegram.value" not in text:
    text = text.replace(old_save, new_save)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done state handling")
