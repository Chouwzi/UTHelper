import sys, os, re

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

if "from notifiers.manager import NotificationManager" not in text:
    text = text.replace("from gui.tray import TrayApp", "from gui.tray import TrayApp\nfrom notifiers.manager import NotificationManager")

# Setup tray and notifier
old_tray_setup = '''        self.tray = TrayApp(self.page)
        self.tray.setup()'''

new_tray_setup = '''        self.tray = TrayApp(self.page)
        self.tray.setup()
        self.notifier = NotificationManager(self.tray)'''

if "self.notifier =" not in text:
    text = text.replace(old_tray_setup, new_tray_setup)


# Inject dispatch into _load_data_async
old_load = '''            self.all_data = result or []

            cache = self.orchestrator._detail_cache'''

new_load = '''            self.all_data = result or []
            
            # Kích hoạt hệ thống thông báo cho dữ liệu mới lấy về
            from models import urgency_str
            urgent_tasks = [d for d in self.all_data if urgency_str(d.get("urgency")) in ("critical", "warning")]
            if urgent_tasks:
                self.notifier.dispatch(urgent_tasks)

            cache = self.orchestrator._detail_cache'''

if "self.notifier.dispatch" not in text:
    text = text.replace(old_load, new_load)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done dispatch hook")
