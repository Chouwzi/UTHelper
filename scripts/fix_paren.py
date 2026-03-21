import re
with open('src/gui/components/settings_view.py', 'r', encoding='utf-8') as f:
    text = f.read()

# We need to capture exactly. There's probably a newline inside.
import sys
text = re.sub(r'self\._test_login_btn\],\s*_setting_group\(', 'self._test_login_btn],\n                            default_open=True\n                        ),\n\n                        _setting_group(', text)

with open('src/gui/components/settings_view.py', 'w', encoding='utf-8') as f:
    f.write(text)
print("Done")
