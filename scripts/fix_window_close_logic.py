import sys, os, re

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_logic = '''    def _on_window_event(self, e):
        if e.data == "close":'''

new_logic = '''    def _on_window_event(self, e):
        # Từ các phiên bản mới của Flet, event close nằm trong e.type
        if getattr(e, "type", getattr(e, "data", "")) == ft.WindowEventType.CLOSE or e.data == "close":'''

text = text.replace(old_logic, new_logic)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("fixed close logic")
