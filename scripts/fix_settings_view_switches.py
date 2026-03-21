import sys, os, re

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\components\settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_block = '''        self._sw_graded = ft.Switch(
            value=settings.INCLUDE_GRADED, active_color=C.ACCENT,
            label="Hiển thị bài đã chấm",
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        )'''

new_block = '''        self._sw_graded = ft.Switch(
            value=settings.INCLUDE_GRADED, active_color=C.ACCENT,
            label="Hiển thị bài đã chấm",
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        )

        self._sw_start_with_windows = ft.Switch(
            value=settings.START_WITH_WINDOWS, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Khởi động cùng Windows"
        )
        self._sw_start_minimized = ft.Switch(
            value=settings.START_MINIMIZED, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Khởi động thu nhỏ (Ngầm)"
        )
        self._sw_minimize_to_tray = ft.Switch(
            value=settings.MINIMIZE_TO_TRAY, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Thu nhỏ xuống System Tray"
        )

        self._sw_discord = ft.Switch(
            value=settings.ENABLE_DISCORD, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bật gửi qua Discord (Future)"
        )'''

if "self._sw_start_with_windows = ft.Switch" not in text:
    text = text.replace(old_block, new_block)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done inserting switches")
