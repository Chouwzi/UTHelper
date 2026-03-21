import os
with open("src/notifiers/email.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if 'html_content += f"""' in line and 'div class="task-card' not in line:
        new_lines.append("            html_content += f\"\"\"\n")
    elif '<div class="meta"><strong>⏰ Hạn chót:</strong>' in line:
        new_lines.append("                        <div class=\"meta\"><strong>⏰ Hạn chót:</strong> <span style=\"font-weight: bold;\">{deadline}</span></div>\n")
    elif '<div class="meta"><strong>⏳ Còn lại:</strong>' in line:
        new_lines.append("                        <div class=\"meta\"><strong>⏳ Còn lại:</strong> {remaining}</div>\n")
    elif '<div class="meta"><strong>🚨 Trạng thái:</strong>' in line:
        new_lines.append("                        <div class=\"meta\"><strong>🚨 Trạng thái:</strong> {status_text}</div>\n")
    elif line.strip() == '"""' and 'div' not in line:
        new_lines.append("            \"\"\"\n")
    elif 'if url:' in line:
        new_lines.append("            if url:\n")
    elif 'html_content += f\'<a href="{url}" class="button">' in line:
        new_lines.append("                html_content += f'<a href=\"{url}\" class=\"button\">Đến Trang Nộp Bài</a>'\n")
    elif 'html_content += "</div>"' in line:
        new_lines.append("            html_content += \"</div>\"\n")
    else:
        new_lines.append(line)

with open("src/notifiers/email.py", "w", encoding="utf-8") as f:
    f.writelines(new_lines)
print("done")
