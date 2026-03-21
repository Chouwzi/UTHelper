import sys, os, re

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\components\settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

bad_block = '''        self._safe_hours_label = ft.Text(
            value=f"An toàn khi trên {settings.URGENCY_WARNING_HOURS} giờ",
            color=C.SUCCESS, size=13, weight=ft.FontWeight.W_500
        ),
            label="Sắp tới khi dưới X giờ",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, 
            bgcolor=C.SURFACE, border_radius=10,
        )'''

good_block = '''        self._safe_hours_label = ft.Text(
            value=f"An toàn khi trên {settings.URGENCY_WARNING_HOURS} giờ",
            color=C.SUCCESS, size=13, weight=ft.FontWeight.W_500
        )'''

text = text.replace(bad_block, good_block)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("fixed bad block")
