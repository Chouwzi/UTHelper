import sys, os, re

path = r'src/gui/components/settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_bot = '''                        _section("TÍCH HỢP & BOT"),
                        _card(self._sw_discord),'''

new_bot = '''                        _section("TÍCH HỢP & BOT"),
                        _card(self._sw_discord),

                        _section("NÂNG CAO & GỠ LỖI"),
                        _card(self._sw_debug, self._test_panel),'''


text = text.replace(old_bot, new_bot)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("fixed debug layout")
