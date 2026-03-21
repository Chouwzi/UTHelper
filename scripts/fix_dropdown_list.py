import re
with open('src/gui/components/settings_view.py', 'r', encoding='utf-8') as f:
    text = f.read()

regex = r'        # Cập nhật danh sách môn học cho dropdown.*?self\._muted_courses_drp\.visible = False'
new_block = '''        # Cập nhật danh sách môn học cho ExpansionTile
        self._known_courses = set()
        if hasattr(self, '_orchestrator') and getattr(self._orchestrator, '_detail_cache', None):
            for cached in self._orchestrator._detail_cache.values():
                c = cached.get('course')
                if c:
                    from gui.core.utils import clean_course_name
                    c_name = clean_course_name(c)
                    if c_name: self._known_courses.add(c_name)

        if getattr(self, '_known_courses', None):
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
                self._muted_courses_list.update()
                
            self._muted_courses_drp.visible = True
        else:
            self._muted_courses_drp.visible = False'''

text = re.sub(regex, new_block, text, flags=re.DOTALL)

with open('src/gui/components/settings_view.py', 'w', encoding='utf-8') as f:
    f.write(text)
