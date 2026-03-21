import sys, os, re

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\tray.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_show = '''    def show_app(self, icon, item):
        if self._page:
            try:
                # Phải gán qua thread an toàn hoặc update page
                self._page.window.visible = True
                self._page.window.minimized = False
                self._page.update()
            except Exception as e:
                logger.error(f"Lỗi khi phục hồi app: {e}")'''

new_show = '''    def show_app(self, icon, item):
        if self._page:
            try:
                # Phải gán qua thread an toàn hoặc update page
                self._page.window.visible = True
                self._page.window.minimized = False
                self._page.window.to_front()
                self._page.update()
            except Exception as e:
                logger.error(f"Lỗi khi phục hồi app: {e}")'''

text = text.replace(old_show, new_show)

old_exit = '''    def exit_app(self, icon, item):
        if self._icon:
            self._icon.stop()
        if self._page:
            self._page.window.destroy()
        else:'''

new_exit = '''    def exit_app(self, icon, item):
        if self._icon:
            self._icon.stop()
        if self._page:
            self._page.window.prevent_close = False
            self._page.window.destroy()
        else:'''

text = text.replace(old_exit, new_exit)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done force front")
