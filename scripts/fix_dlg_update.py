import re
with open('src/gui/components/settings_view.py', 'r', encoding='utf-8') as f:
    text = f.read()

text = re.sub(r'                try:\n                    dlg\.update\(\)\n                except Exception:\n                    pass', '''                try:
                    self._page.update(sv_base, sv_pointer, hue_pointer, prv, hex_inp)
                except Exception:
                    pass''', text)

with open('src/gui/components/settings_view.py', 'w', encoding='utf-8') as f:
    f.write(text)
