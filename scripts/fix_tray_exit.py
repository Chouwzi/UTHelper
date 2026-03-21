import sys, os, re

path = r'src/gui/tray.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_exit = '''    def exit_app(self, icon, item):
        if self._icon:
            self._icon.stop()
        import os
        os._exit(0)'''

new_exit = '''    def exit_app(self, icon, item):
        if self._page:
            try:
                self._page.window.destroy()
            except Exception as e:
                logger.error(f"Error destroying window: {e}")
        if self._icon:
            self._icon.stop()
        import os
        os._exit(0)'''

text = text.replace(old_exit, new_exit)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("fixed tray exit")
