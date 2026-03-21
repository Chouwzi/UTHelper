import sys, os, re

path = r'src/gui/components/settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

# Replace _save_setting dependency with save_settings from config
old_save_impl = '''_ENV_FILE = str(BASE_DIR / ".env")

def _save_setting(key: str, value):
    try:
        from dotenv import set_key
        str_val = str(value).upper() if isinstance(value, bool) else str(value) 
        set_key(_ENV_FILE, key, str_val)
    except Exception as ex:
        pass'''

new_save_impl = '''from config import save_settings'''

if "_ENV_FILE =" in text:
    text = text.replace(old_save_impl, new_save_impl)


# Modify _save method to just assign to config.settings and call config.save_settings()
old_save_method = '''            settings.START_MINIMIZED = self._sw_start_minimized.value
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

# Also remove the whole old _save_setting blocks from _save
text = re.sub(r'            _save_setting\([^)]+\)\n?', '', text)


# Append save_settings() to the end
after_saving = '''            self._page.window.always_on_top = settings.ALWAYS_ON_TOP

            save_settings()

            self._save_status.value = "✅ Đã lưu cài đặt"'''
text = text.replace("            self._page.window.always_on_top = settings.ALWAYS_ON_TOP", after_saving)


with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("Refactored settings_view.py")

