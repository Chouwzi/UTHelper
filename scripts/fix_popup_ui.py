import sys, os, re

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

# _make_filter_popup items creation:
old_popup_init = '''        popup = ft.PopupMenuButton(
            content=ft.Container(
                content=ft.Row([btn_label, ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=C.TEXT_SECONDARY)], spacing=2, tight=True),
                bgcolor=C.SURFACE, border=ft.border.all(1, C.BORDER), border_radius=10, padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            ),
            items=items,
        )'''

new_popup_init = '''        popup = ft.PopupMenuButton(
            content=ft.Container(
                content=ft.Row([btn_label, ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=C.TEXT_SECONDARY)], spacing=2, tight=True),
                bgcolor=C.SURFACE, border=ft.border.all(1, C.BORDER), border_radius=10, padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            ),
            items=items,
            menu_position=ft.PopupMenuPosition.UNDER,
            shape=ft.RoundedRectangleBorder(radius=10),
        )'''

text = text.replace(old_popup_init, new_popup_init)

# course_popup in _init_ui:
old_course_popup = '''        self.course_popup = ft.PopupMenuButton(
            content=ft.Container(
                content=ft.Row([
                    ft.Container(content=self.course_btn_label, width=60, alignment=ft.Alignment(-1, 0)),
                    ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=C.TEXT_SECONDARY)
                ], spacing=2, tight=True),
                bgcolor=C.SURFACE, border=ft.border.all(1, C.BORDER), border_radius=10, padding=ft.Padding.symmetric(horizontal=8, vertical=8),
            ),
            items=[]
        )'''

new_course_popup = '''        self.course_popup = ft.PopupMenuButton(
            content=ft.Container(
                content=ft.Row([
                    ft.Container(content=self.course_btn_label, width=60, alignment=ft.Alignment(-1, 0)),
                    ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=C.TEXT_SECONDARY)
                ], spacing=2, tight=True),
                bgcolor=C.SURFACE, border=ft.border.all(1, C.BORDER), border_radius=10, padding=ft.Padding.symmetric(horizontal=8, vertical=8),
            ),
            items=[],
            menu_position=ft.PopupMenuPosition.UNDER,
            shape=ft.RoundedRectangleBorder(radius=10),
        )'''

text = text.replace(old_course_popup, new_course_popup)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("Replaced popup UI")
