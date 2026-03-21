import re

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\components\settings_view.py'
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

# Update init to store on_close_cb
text = text.replace('self._page    = page\n        self._on_saved = on_saved', 'self._page    = page\n        self._on_close_cb = on_close\n        self._on_saved = on_saved')

# Update back_btn on_click
text = text.replace('on_click=lambda _: on_close(),', 'on_click=self._handle_back,')

# Add new methods at the end of __init__ before _save
method_code = '''
    def load_current_settings(self):
        self._username_field.value = settings.UTH_USERNAME
        self._password_field.value = settings.UTH_PASSWORD
        self._sw_always_on_top.value = settings.ALWAYS_ON_TOP
        self._sw_submitted.value = settings.INCLUDE_SUBMITTED
        self._sw_graded.value = settings.INCLUDE_GRADED
        self._interval_field.value = str(settings.CHECK_INTERVAL_MINUTES)
        self._critical_hours_field.value = str(settings.URGENCY_CRITICAL_HOURS)
        self._warning_hours_field.value = str(settings.URGENCY_WARNING_HOURS)
        self._notify_min_field.value = str(settings.NOTIFY_MINUTES_BEFORE)
        self._workers_field.value = str(settings.PREFETCH_WORKERS)
        self._save_status.value = ""
        self.update()

    def has_changes(self):
        if self._username_field.value != settings.UTH_USERNAME: return True
        if self._password_field.value != settings.UTH_PASSWORD: return True
        if self._sw_always_on_top.value != settings.ALWAYS_ON_TOP: return True
        if self._sw_submitted.value != settings.INCLUDE_SUBMITTED: return True
        if self._sw_graded.value != settings.INCLUDE_GRADED: return True
        if self._interval_field.value != str(settings.CHECK_INTERVAL_MINUTES): return True
        if self._critical_hours_field.value != str(settings.URGENCY_CRITICAL_HOURS): return True
        if self._warning_hours_field.value != str(settings.URGENCY_WARNING_HOURS): return True
        if self._notify_min_field.value != str(settings.NOTIFY_MINUTES_BEFORE): return True
        if self._workers_field.value != str(settings.PREFETCH_WORKERS): return True
        return False

    async def _handle_back(self, e):
        if self.has_changes():
            def close_dlg(e):
                confirm_dlg.open = False
                self._page.update()
            
            def discard_and_close(e):
                confirm_dlg.open = False
                self._page.update()
                self._on_close_cb()

            async def save_and_close(e):
                confirm_dlg.open = False
                self._page.update()
                await self._save(e)
                self._on_close_cb()

            confirm_dlg = ft.AlertDialog(title=ft.Text("Chưa lưu cài đặt", size=16, weight=ft.FontWeight.BOLD), content=ft.Text("Bạn có thay đổi chưa lưu. Bạn muốn lưu lại không?", size=13), actions=[ft.TextButton("Hủy", on_click=close_dlg), ft.TextButton("Bỏ qua", on_click=discard_and_close), ft.TextButton("Lưu", on_click=save_and_close)], actions_alignment=ft.MainAxisAlignment.END)
            self._page.overlay.append(confirm_dlg)
            confirm_dlg.open = True
            self._page.update()
        else:
            self._on_close_cb()

    async def _save'''

text = text.replace('    async def _save', method_code)

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)
