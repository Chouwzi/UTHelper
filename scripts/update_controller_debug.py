import sys, os, re

path = r'src/gui/app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_settings_init = '''        self.settings_view = SettingsView(self.page, on_close=lambda: self.page.run_task(self._close_settings), on_saved=self._on_settings_saved)'''

new_settings_init = '''        self.settings_view = SettingsView(
            self.page, 
            on_close=lambda: self.page.run_task(self._close_settings), 
            on_saved=self._on_settings_saved,
            on_test_tray=self._on_test_tray
        )'''

if "on_test_tray=self._on_test_tray" not in text:
    text = text.replace(old_settings_init, new_settings_init)

import textwrap
new_func = '''
    def _on_test_tray(self):
        from models import Assignment, UrgencyLevel
        dummy = Assignment(
            id=9999, title="Bài viết kiểm thử thông báo từ hệ thống",
            course="Elearning", deadline="2026-10-10",
            timestamp=123, urgency=UrgencyLevel.CRITICAL, type="assign", 
            link="", status="", details={}
        )
        self.notifier.dispatch([dummy])
        logger.debug("Đã gửi hàm test notification lên notifier.dispatch")
'''

if "def _on_test_tray(self):" not in text:
    text = text.replace("def _on_settings_saved(self):", new_func + "\n    def _on_settings_saved(self):")
    
with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done controller update")
