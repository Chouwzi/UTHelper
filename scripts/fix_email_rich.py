import re

with open("src/notifiers/email.py", "r", encoding="utf-8") as f:
    e = f.read()

# Add open_time to each task card
replacement = r'''
              html_content += f"""
                      <div class="task-card {css_class}">
                          <p class="title">{title}</p>
                          <div class="meta"><strong>📚 Môn học:</strong> {course}</div>
              """
              if hasattr(task, 'details') and task.details and getattr(task.details, 'open_time', None):
                  open_time_str = task.details.open_time.strftime('%H:%M %d/%m/%Y')
                  html_content += f'<div class="meta"><strong>🗓️ Ngày mở:</strong> {open_time_str}</div>'

              # Calculate remaining time (again for email)
              remaining = "Không rõ"
              if hasattr(task, 'deadline') and task.deadline:
                  import datetime
                  delta = task.deadline - datetime.datetime.now()
                  days, seconds = delta.days, delta.seconds
                  hours = seconds // 3600
                  if days < 0:
                      remaining = "Quá hạn rồi!"
                  elif days > 0:
                      remaining = f"Còn {days} ngày {hours} giờ"
                  else:
                      remaining = f"Còn {hours} giờ {seconds % 3600 // 60} phút"

              html_content += f"""
                          <div class="meta"><strong>⏰ Hạn chót:</strong> <span style="font-weight: bold;">{deadline}</span></div>
                          <div class="meta"><strong>⏳ Còn lại:</strong> {remaining}</div>
                          <div class="meta"><strong>🚨 Trạng thái:</strong> {status_text}</div>
              """'''

# Look for the current hardcoded task-card building part
pattern = re.compile(r'html_content \+= f"""\n\s+<div class="task-card \{css_class\}">.*?Trạng thái:</strong>\s+\{status_text\}</div>\s+"""', re.DOTALL)
if pattern.search(e):
    e = pattern.sub(replacement.strip(), e)
    with open("src/notifiers/email.py", "w", encoding="utf-8") as f:
        f.write(e)
    print("Email template rich content added")
else:
    print("Pattern missed for email")
