import sys, os, re
sys.path.append(os.path.abspath('src'))

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\components\settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

# Make sure we add _safe_hours_label after _warning_hours_field
old_warn = '''        self._warning_hours_field = ft.TextField(
            value=str(settings.URGENCY_WARNING_HOURS),
            label="Sắp tới khi dưới X giờ",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, 
            bgcolor=C.SURFACE, border_radius=10,
        )'''

new_warn = '''        self._warning_hours_field = ft.TextField(
            value=str(settings.URGENCY_WARNING_HOURS),
            label="Sắp tới khi dưới X giờ",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, 
            bgcolor=C.SURFACE, border_radius=10,
            on_change=lambda e: self._update_safe_label()
        )
        self._safe_hours_label = ft.Text(
            value=f"An toàn khi trên {settings.URGENCY_WARNING_HOURS} giờ",
            color=C.SUCCESS, size=13, weight=ft.FontWeight.W_500
        )'''

if "self._safe_hours_label = ft.Text" not in text:
    text = re.sub(r'self\._warning_hours_field\s*=\s*ft\.TextField\([^)]+\)', new_warn, text)

# add the method if not exists
if "def _update_safe_label" not in text:
    old_method = '''    def load_current_settings(self):'''
    new_method = '''    def _update_safe_label(self):
        val = self._warning_hours_field.value
        if val and val.isdigit():
            self._safe_hours_label.value = f"An toàn khi trên {val} giờ"
            self._safe_hours_label.update()

    def load_current_settings(self):'''
    text = text.replace(old_method, new_method)

# and update load_current_settings
old_load = '''        self._warning_hours_field.value = str(settings.URGENCY_WARNING_HOURS)'''
new_load = '''        self._warning_hours_field.value = str(settings.URGENCY_WARNING_HOURS)
        self._update_safe_label()'''
if "self._update_safe_label()" not in text.split("load_current_settings")[1]:
    text = text.replace(old_load, new_load)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done view 2")
