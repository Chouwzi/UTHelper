import os
with open("src/notifiers/windows.py", "r", encoding="utf-8") as f:
    t = f.read()

t = t.replace('msg = f"{course}\n', 'msg = f"{course}\\n')

with open("src/notifiers/windows.py", "w", encoding="utf-8") as f:
    f.write(t)
    
with open("src/notifiers/telegram.py", "r", encoding="utf-8") as f:
    t = f.read()
    
# Replace literal newlines in strings
t = t.replace('{course}\n', '{course}\\n')
t = t.replace('{task_type}\n', '{task_type}\\n')
t = t.replace('{title}\n', '{title}\\n')
t = t.replace('{open_time}\n', '{open_time}\\n')
t = t.replace('})\n', '})\\n')
t = t.replace('</a>\n', '</a>\\n')
t = t.replace('text += "\n"', 'text += "\\n"')

with open("src/notifiers/telegram.py", "w", encoding="utf-8") as f:
    f.write(t)
print("Done")
