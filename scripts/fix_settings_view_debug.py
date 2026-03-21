import re
path = 'src/gui/components/settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

text = text.replace('def __init__(self, page: ft.Page, orchestrator, on_close, on_saved=None, on_test_tray=None):',
'def __init__(self, page: ft.Page, orchestrator, on_close, on_saved=None, on_test_tray=None, on_test_tele=None, on_test_discord=None, on_test_mail=None):')

text = text.replace('        self._on_test_tray = on_test_tray',
'''        self._on_test_tray = on_test_tray
        self._on_test_tele = on_test_tele
        self._on_test_discord = on_test_discord
        self._on_test_mail = on_test_mail''')

new_buttons = '''        self._test_panel = ft.Container(
            content=ft.Column([
                ft.Text("Công cụ Debug - Mock Test", color=C.CRITICAL, weight=ft.FontWeight.BOLD),
                ft.Row([
                    ft.ElevatedButton("Windows Tray", on_click=lambda e: self._do_test_tray(), bgcolor=C.SURFACE, color=C.TEXT_PRIMARY),
                    ft.ElevatedButton("Telegram", on_click=lambda e: self._do_test_tele(), bgcolor=C.SURFACE, color="#0088cc"),
                ], wrap=True),
                ft.Row([
                    ft.ElevatedButton("Discord", on_click=lambda e: self._do_test_discord(), bgcolor=C.SURFACE, color="#5865F2"),
                    ft.ElevatedButton("Gmail", on_click=lambda e: self._do_test_mail(), bgcolor=C.SURFACE, color="#EA4335"),
                ], wrap=True),
            ]),
            visible=settings.DEBUG_MODE,
            padding=10, border=ft.border.all(1, C.CRITICAL), border_radius=8, margin=ft.margin.only(top=10)
        )'''

text = re.sub(r'        self\._test_panel = ft\.Container\([^)]*margin=ft\.margin\.only\(top=10\)\n        \)', new_buttons, text, flags=re.DOTALL)

new_funcs = '''    def _do_test_tray(self):
        if self._on_test_tray: self._on_test_tray()

    def _do_test_tele(self):
        if hasattr(self, '_on_test_tele') and self._on_test_tele: self._on_test_tele()

    def _do_test_discord(self):
        if hasattr(self, '_on_test_discord') and self._on_test_discord: self._on_test_discord()

    def _do_test_mail(self):
        if hasattr(self, '_on_test_mail') and self._on_test_mail: self._on_test_mail()
'''

text = re.sub(r'    def _do_test_tray\(self\):\n        if self\._on_test_tray:\n            self\._on_test_tray\(\)', new_funcs, text, flags=re.DOTALL)

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)
