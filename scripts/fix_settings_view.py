import sys, os, re
sys.path.append(os.path.abspath('src'))

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\components\settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_fields = '''        self._warning_hours_field = ft.TextField(
            value=str(settings.URGENCY_WARNING_HOURS),
            label="Sắp tới khi dưới X giờ",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, 
            bgcolor=C.SURFACE, border_radius=10,
        )'''

new_fields = '''        self._warning_hours_field = ft.TextField(
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
        )
        
    def _update_safe_label(self):
        val = self._warning_hours_field.value
        if val and val.isdigit():
            self._safe_hours_label.value = f"An toàn khi trên {val} giờ"
            self._safe_hours_label.update()'''

text = text.replace(old_fields, new_fields)

old_ui = '''                        _section("NGƯỠNG MỨC ĐỘ"),
                        self._critical_hours_field,
                        self._warning_hours_field,
                        _hint("Ví dụ: cấp bách < 24 giờ, sắp tới < 72 giờ."),'''

new_ui = '''                        _section("NGƯỠNG MỨC ĐỘ"),
                        self._critical_hours_field,
                        self._warning_hours_field,
                        ft.Container(content=self._safe_hours_label, padding=ft.Padding(left=10, top=0, right=0, bottom=0)),
                        _hint("Mức độ An toàn tự động được tính khi thời gian còn lại lớn hơn thời gian cấu hình Sắp tới."),'''

text = text.replace(old_ui, new_ui)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done view")
