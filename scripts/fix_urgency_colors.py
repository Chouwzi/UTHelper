import re

# Fix discord.py
with open("src/notifiers/discord.py", "r", encoding="utf-8") as f:
    d = f.read()
d = d.replace("color = 15158332 if is_critical else (15105570 if task.urgency == UrgencyLevel.WARNING else 3066993)",
              "color = 15158332 if task.urgency == UrgencyLevel.CRITICAL else (15105570 if task.urgency == UrgencyLevel.WARNING else 3066993)")
d = d.replace("'value': '🔴 Khẩn cấp' if is_critical else ('🟠 Tới hạn' if task.urgency == UrgencyLevel.WARNING else '🟢 An toàn')",
              "'value': '🔴 Khẩn cấp' if task.urgency == UrgencyLevel.CRITICAL else ('🟠 Sắp tới hạn' if task.urgency == UrgencyLevel.WARNING else '🟢 An toàn')")
with open("src/notifiers/discord.py", "w", encoding="utf-8") as f:
    f.write(d)

# Fix email.py
with open("src/notifiers/email.py", "r", encoding="utf-8") as f:
    e = f.read()
e = e.replace("css_class = 'critical' if is_critical else 'warning'",
              "css_class = 'critical' if task.urgency == UrgencyLevel.CRITICAL else ('warning' if task.urgency == UrgencyLevel.WARNING else 'safe')")
e = e.replace("status_text = 'Khẩn cấp 🔴' if is_critical else 'Đến hạn 🟠'",
              "status_text = 'Khẩn cấp 🔴' if task.urgency == UrgencyLevel.CRITICAL else ('Sắp tới hạn 🟠' if task.urgency == UrgencyLevel.WARNING else 'An toàn 🟢')")
# Add .task-card.safe CSS
if '.task-card.safe { border-left-color: #10b981; background: #f0fdf4; }' not in e:
    e = e.replace('.task-card.warning { border-left-color: #f59e0b; background: #fffbeb; }',
                  '.task-card.warning { border-left-color: #f59e0b; background: #fffbeb; }\\n                .task-card.safe { border-left-color: #10b981; background: #f0fdf4; }')
with open("src/notifiers/email.py", "w", encoding="utf-8") as f:
    f.write(e)

# Fix telegram.py (already has icon logic but check text)
with open("src/notifiers/telegram.py", "r", encoding="utf-8") as f:
    t = f.read()
t = t.replace("icon = \"🔴\" if is_critical else (\"🟠\" if a.urgency == UrgencyLevel.WARNING else \"🟢\")",
              "icon = \"🔴\" if a.urgency == UrgencyLevel.CRITICAL else (\"🟠\" if a.urgency == UrgencyLevel.WARNING else \"🟢\")")
with open("src/notifiers/telegram.py", "w", encoding="utf-8") as f:
    f.write(t)

print("Fix completed")
