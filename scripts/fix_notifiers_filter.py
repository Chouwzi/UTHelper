import re

# discord.py
with open("src/notifiers/discord.py", "r", encoding="utf-8") as f:
    t = f.read()
t = re.sub(r'tasks = \[a for a in assignments if a.urgency in \(UrgencyLevel\.CRITICAL, UrgencyLevel\.WARNING\)\]', 'tasks = assignments', t)
with open("src/notifiers/discord.py", "w", encoding="utf-8") as f:
    f.write(t)

# telegram.py
with open("src/notifiers/telegram.py", "r", encoding="utf-8") as f:
    t = f.read()
t = re.sub(r'tasks = \[a for a in assignments if a.urgency in \(UrgencyLevel\.CRITICAL, UrgencyLevel\.WARNING\)\]', 'tasks = assignments', t)
with open("src/notifiers/telegram.py", "w", encoding="utf-8") as f:
    f.write(t)

# email.py 
with open("src/notifiers/email.py", "r", encoding="utf-8") as f:
    t = f.read()
t = re.sub(r'tasks = \[a for a in assignments if a.urgency in \(UrgencyLevel\.CRITICAL, UrgencyLevel\.WARNING\)\]', 'tasks = assignments', t)
with open("src/notifiers/email.py", "w", encoding="utf-8") as f:
    f.write(t)

# windows.py
with open("src/notifiers/windows.py", "r", encoding="utf-8") as f:
    t = f.read()
t = re.sub(
r'critical = \[a for a in assignments if a.urgency == UrgencyLevel\.CRITICAL\]\s*warnings = \[a for a in assignments if a.urgency == UrgencyLevel\.WARNING\]\s*total = len\(critical\) \+ len\(warnings\)\s*if total == 0:\s*return\s*title = "UTHelper"\s*msg = f"Bạn có \{len\(critical\)\} bài tập cực kỳ gấp và \{len\(warnings\)\} hạn chót sắp tới\."',
r'''critical = [a for a in assignments if getattr(a, 'urgency', None) == UrgencyLevel.CRITICAL]
        warnings = [a for a in assignments if getattr(a, 'urgency', None) == UrgencyLevel.WARNING]
        safes = [a for a in assignments if getattr(a, 'urgency', None) == UrgencyLevel.SAFE]
        other_count = len(assignments) - len(critical) - len(warnings) - len(safes)
        title = "UTHelper"
        if len(assignments) == 1:
            title = getattr(assignments[0], 'title', 'Bài tập mới')
            msg = f"Môn học: {getattr(assignments[0], 'course_name', getattr(assignments[0], 'course', 'Không rõ'))}"
        else:
            msg = f"Bạn có {len(critical)} bài cực gấp, {len(warnings)} sắp tới hạn và {len(safes) + other_count} bài khác."''', t)

with open("src/notifiers/windows.py", "w", encoding="utf-8") as f:
    f.write(t)

print("Fixed notifiers")
