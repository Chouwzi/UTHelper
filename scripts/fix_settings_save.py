import sys, os, re

path = r'src/gui/components/settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()


# update _save
old_save = '''            _save_setting("NOTIFY_MINUTES_BEFORE",  settings.NOTIFY_MINUTES_BEFORE)
            _save_setting("PREFETCH_WORKERS",       settings.PREFETCH_WORKERS)'''

new_save = '''            _save_setting("NOTIFY_MINUTES_BEFORE",  settings.NOTIFY_MINUTES_BEFORE)
            _save_setting("PREFETCH_WORKERS",       settings.PREFETCH_WORKERS)

            settings.START_WITH_WINDOWS = self._sw_start_with_windows.value
            settings.START_MINIMIZED = self._sw_start_minimized.value
            settings.MINIMIZE_TO_TRAY = self._sw_minimize_to_tray.value
            settings.ENABLE_DISCORD = self._sw_discord.value
            settings.ENABLE_TELEGRAM = self._sw_telegram.value
            settings.TELEGRAM_BOT_TOKEN = self._tel_token_field.value
            settings.TELEGRAM_CHAT_ID = self._tel_chat_field.value
            settings.DEBUG_MODE = self._sw_debug.value

            _save_setting("START_WITH_WINDOWS", settings.START_WITH_WINDOWS)
            _save_setting("START_MINIMIZED", settings.START_MINIMIZED)
            _save_setting("MINIMIZE_TO_TRAY", settings.MINIMIZE_TO_TRAY)
            _save_setting("ENABLE_DISCORD", settings.ENABLE_DISCORD)
            _save_setting("ENABLE_TELEGRAM", settings.ENABLE_TELEGRAM)
            _save_setting("TELEGRAM_BOT_TOKEN", settings.TELEGRAM_BOT_TOKEN)
            _save_setting("TELEGRAM_CHAT_ID", settings.TELEGRAM_CHAT_ID)
            _save_setting("DEBUG_MODE", settings.DEBUG_MODE)'''

if "_save_setting(\"START_WITH_WINDOWS\"" not in text:
    text = text.replace(old_save, new_save)
    with open(path, 'w', encoding='utf-8') as f: f.write(text)
    print("fixed save context")
else:
    print("already fixed")
