import sys, os, re
sys.path.append(os.path.abspath('src'))

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

# 1. ADD self.active_course = 'all'
text = text.replace('self.active_type = "all"\n        self.active_search = ""', 'self.active_type = "all"\n        self.active_course = "all"\n        self.active_search = ""')

# 2. CREATE self._setup_course_popup() and replace row setup
course_setup_code = """
        self.course_btn_label = ft.Text("Môn học", size=12, color=C.TEXT_PRIMARY, weight=ft.FontWeight.W_500, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.course_popup = ft.PopupMenuButton(
            content=ft.Container(
                content=ft.Row([
                    ft.Container(content=self.course_btn_label, width=60, alignment=ft.alignment.center_left),
                    ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=C.TEXT_SECONDARY)
                ], spacing=2, tight=True),
                bgcolor=C.SURFACE, border=ft.border.all(1, C.BORDER), border_radius=10, padding=ft.Padding.symmetric(horizontal=8, vertical=8),
            ),
            items=[]
        )
"""

text = text.replace('        self._overdue_cb = ft.Checkbox(', course_setup_code + '\n        self._overdue_cb = ft.Checkbox(')
text = text.replace('content=ft.Row(controls=[self.urgency_popup, self.type_popup, self._overdue_cb], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)', 'content=ft.Row(controls=[self.urgency_popup, self.type_popup, self.course_popup, self._overdue_cb], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, scroll=ft.ScrollMode.AUTO)')

# 3. Add self._set_course method
set_course_code = """
    async def _set_course(self, course_name: str):
        self.active_course = course_name
        self.course_btn_label.value = "Môn học" if course_name == "all" else course_name
        self.course_btn_label.update()
        self._card_cache.clear()
        self._render_cards()
        self.page.update()
"""
text = text.replace('    async def _set_type(self, key: str):\n        self.active_type = key\n        self._card_cache.clear()\n        self._render_cards()\n        self.page.update()', '    async def _set_type(self, key: str):\n        self.active_type = key\n        self._card_cache.clear()\n        self._render_cards()\n        self.page.update()\n' + set_course_code)

# 4. Modify cache key
text = text.replace('cache_key = (self.active_urgency, self.active_type)', 'cache_key = (self.active_urgency, self.active_type, self.active_course)')

# 5. Apply filtering logic in _render_cards
filter_code = """
        if self.active_course != "all":
            base = [d for d in base if clean_course_name(d.get("course", "")) == self.active_course]

        if self.active_search:
"""
text = text.replace('        if self.active_search:', filter_code)

# 6. Build course popup dynamically over in _update_footer
update_footer_code = """
        type_counts = {}
        course_names = set()
        for d in self.all_data:
            course_name = clean_course_name(d.get("course", ""))
            if course_name:
                course_names.add(course_name)

            dl_str = d.get("deadline", "")"""
text = text.replace('        type_counts = {}\n        for d in self.all_data:\n            dl_str = d.get("deadline", "")', update_footer_code)


dynamic_popup_code = """
        def _on_course_select(e, c_name):
            self.page.run_task(self._set_course, c_name)

        c_items = [ft.PopupMenuItem(
            content=ft.Row([ft.Text("Tất cả môn học", size=12, color=C.TEXT_SECONDARY, expand=True), ft.Icon(ft.Icons.CHECK, size=12, color=C.TEXT_PRIMARY, visible=(self.active_course == "all"))], spacing=6, tight=True),
            on_click=lambda e: _on_course_select(e, "all")
        )]
        for c in sorted(list(course_names)):
            is_active = (self.active_course == c)
            c_items.append(ft.PopupMenuItem(
                content=ft.Row([ft.Text(c, size=12, color=C.TEXT_PRIMARY, expand=True), ft.Icon(ft.Icons.CHECK, size=12, color=C.TEXT_PRIMARY, visible=is_active)], spacing=6, tight=True),
                on_click=lambda e, name=c: _on_course_select(e, name)
            ))
        self.course_popup.items = c_items

        self.footer_critical.value = f"Cấp bách · {n_critical}" if n_critical else ""
"""

text = text.replace('        self.footer_critical.value = f"Cấp bách · {n_critical}" if n_critical else ""', dynamic_popup_code)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("Updated successfully")
