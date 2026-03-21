import os
with open("src/notifiers/email.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    # Look for the misplaced 'if hasattr' and its block
    if line.strip().startswith("if hasattr(task, 'details')"):
        new_lines.append("            if hasattr(task, 'details') and task.details and getattr(task.details, 'open_time', None):\n")
    elif line.strip().startswith("open_time_str = task.details.open_time"):
        new_lines.append("                open_time_str = task.details.open_time.strftime('%H:%M %d/%m/%Y')\n")
    elif line.strip().startswith("html_content += f'<div class=\"meta\"><strong>🗓️"):
        new_lines.append("                html_content += f'<div class=\"meta\"><strong>🗓️ Ngày mở:</strong> {open_time_str}</div>'\n")
    elif line.strip().startswith("# Calculate remaining time"):
        new_lines.append("            # Calculate remaining time (again for email)\n")
    elif line.strip() == "remaining = \"Không rõ\"":
        new_lines.append("            remaining = \"Không rõ\"\n")
    elif line.strip().startswith("if hasattr(task, 'deadline') and task.deadline:"):
        new_lines.append("            if hasattr(task, 'deadline') and task.deadline:\n")
    elif "import datetime" in line and "remaining =" not in line:
        new_lines.append("                import datetime\n")
    elif "delta = task.deadline - datetime.datetime.now()" in line:
        new_lines.append("                delta = task.deadline - datetime.datetime.now()\n")
    elif "days, seconds = delta.days, delta.seconds" in line:
        new_lines.append("                days, seconds = delta.days, delta.seconds\n")
    elif "hours = seconds // 3600" in line:
        new_lines.append("                hours = seconds // 3600\n")
    elif "if days < 0:" in line:
        new_lines.append("                if days < 0:\n")
    elif "remaining = \"Quá hạn rồi!\"" in line:
        new_lines.append("                    remaining = \"Quá hạn rồi!\"\n")
    elif "elif days > 0:" in line:
        new_lines.append("                elif days > 0:\n")
    elif "remaining = f\"Còn {days} ngày {hours} giờ\"" in line:
        new_lines.append("                    remaining = f\"Còn {days} ngày {hours} giờ\"\n")
    elif "else:" in line and "hours % 3600" in line:
        new_lines.append("                else:\n")
    elif "remaining = f\"Còn {hours} giờ {seconds % 3600 // 60} phút\"" in line:
        new_lines.append("                    remaining = f\"Còn {hours} giờ {seconds % 3600 // 60} phút\"\n")
    else:
        new_lines.append(line)

with open("src/notifiers/email.py", "w", encoding="utf-8") as f:
    f.writelines(new_lines)
print("done")
