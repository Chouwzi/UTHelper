import re

path = 'src/gui/components/settings_view.py'
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

text = re.sub(
    r'def _update_drp_options\(\).*?expand=True\n        \)',
    '''        self._muted_courses_list = ft.Column(spacing=2)
        self._muted_courses_drp = ft.ExpansionTile(
            title=ft.Text("Nhấn để mở danh sách chọn môn bỏ qua", size=13, color=C.TEXT_SECONDARY),
            controls=[
                ft.Container(
                    content=self._muted_courses_list,
                    padding=10,
                    bgcolor=C.BG,
                    border_radius=10,
                    border=ft.border.all(1, C.BORDER)
                )
            ],
            visible=False,
            collapsed_text_color=C.TEXT_PRIMARY,
            text_color=C.ACCENT
        )

        def _update_drp_options():
            if not getattr(self, "_known_courses", None): return
            current = [x.strip() for x in self._muted_courses_field.value.split(",") if x.strip()]
            
            def make_toggle(course):
                def _on_check(e):
                    curr = [x.strip() for x in self._muted_courses_field.value.split(",") if x.strip()]
                    if e.control.value and course not in curr:
                        curr.append(course)
                    elif not e.control.value and course in curr:
                        curr.remove(course)
                    self._muted_courses_field.value = ", ".join(curr)
                    self._muted_courses_field.update()
                return ft.Checkbox(label=course, value=(course in current), on_change=_on_check, fill_color=C.ACCENT)
            
            self._muted_courses_list.controls = [make_toggle(c) for c in sorted(list(self._known_courses))]
            if hasattr(self._muted_courses_list, "page") and self._muted_courses_list.page:
                self._muted_courses_list.update()''',
    text,
    flags=re.DOTALL
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)
print('Done!')
