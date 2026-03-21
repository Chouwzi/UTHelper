import sys, os

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_minimized_block = '''        if settings.START_MINIMIZED:
            self.page.window.visible = False
            self.page.window.minimized = True'''

new_minimized_block = '''        # Chỉ ẩn nếu có cờ --autostart được bật lên (phân biệt app khởi động cùng Win với ng dùng tự mở)
        import sys
        if settings.START_MINIMIZED and "--autostart" in sys.argv:
            self.page.window.visible = False
            # self.page.window.minimized = True  # Thay vì minimized, ẩn đi tốt hơn
        else:
            self.page.window.visible = True
            
        self.page.update()'''

text = text.replace(old_minimized_block, new_minimized_block)

old_close_block = '''    def _on_window_event(self, e):
        if e.data == "close":
            if settings.MINIMIZE_TO_TRAY:
                self.page.window.visible = False
                self.page.update()
                self.tray.notify("UTH Alert", "App đã thu nhỏ xuống khay hệ thống.")
            else:
                self.page.window.destroy()'''

new_close_block = '''    def _on_window_event(self, e):
        if e.data == "close":
            if settings.MINIMIZE_TO_TRAY:
                self.page.window.visible = False
                # Prevent default close behavior explicitly handled by prevent_close=True
                self.page.update()
            else:
                self.page.window.destroy()'''

text = text.replace(old_close_block, new_close_block)

with open(path, 'w', encoding='utf-8') as f: f.write(text)

autostart_path = r'E:\Projects\UTH-Elearning-Alert\src\core\autostart.py'
with open(autostart_path, 'r', encoding='utf-8') as f: auto_text = f.read()

old_autostart = '''        exe_path = f'"{pythonw_path}" "{script_path}"'

    try:'''

new_autostart = '''        exe_path = f'"{pythonw_path}" "{script_path}" --autostart'

    try:'''

auto_text = auto_text.replace(old_autostart, new_autostart)

with open(autostart_path, 'w', encoding='utf-8') as f: f.write(auto_text)

print("done patch")
