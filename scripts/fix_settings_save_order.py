import sys, os, re

path = r'src/gui/components/settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

# Make sure we declare attributes BEFORE calling save_settings()
old_save_block = '''            self._page.window.always_on_top = settings.ALWAYS_ON_TOP

            save_settings()

            self._save_status.value = "✅ Đã lưu cài đặt"


            settings.START_WITH_WINDOWS = self._sw_start_with_windows.value'''

new_save_block = '''            settings.START_WITH_WINDOWS = self._sw_start_with_windows.value
            settings.START_MINIMIZED = self._sw_start_minimized.value
            settings.MINIMIZE_TO_TRAY = self._sw_minimize_to_tray.value
            settings.ENABLE_DISCORD = self._sw_discord.value
            settings.ENABLE_TELEGRAM = self._sw_telegram.value
            settings.TELEGRAM_BOT_TOKEN = self._tel_token_field.value
            settings.TELEGRAM_CHAT_ID = self._tel_chat_field.value
            settings.DEBUG_MODE = self._sw_debug.value

            self._page.window.always_on_top = settings.ALWAYS_ON_TOP

            save_settings()

            self._save_status.value = "✅ Đã lưu cài đặt"
'''

# Remove duplicate setting at the bottom
text = re.sub(r'            settings\.START_WITH_WINDOWS = self\._sw_start_with_windows\.value[\s\S]+?settings\.DEBUG_MODE = self\._sw_debug\.value', '', text)
text = text.replace('''            self._page.window.always_on_top = settings.ALWAYS_ON_TOP

            save_settings()

            self._save_status.value = "✅ Đã lưu cài đặt"''', new_save_block)


with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("fixed save order")

