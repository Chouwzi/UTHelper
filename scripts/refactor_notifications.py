import re

def rewrite_discord():
    with open("src/notifiers/discord.py", "r", encoding="utf-8") as f:
        content = f.read()

    replacement = '''        for task in tasks:
            is_critical = task.urgency == UrgencyLevel.CRITICAL
            color = 15158332 if is_critical else (15105570 if task.urgency == UrgencyLevel.WARNING else 3066993)

            title = getattr(task, 'title', 'Không rõ tiêu đề')
            course = getattr(task, 'course_name', getattr(task, 'course', 'Không rõ môn học'))
            
            deadline = getattr(task, 'deadline_str', '')
            if not deadline and getattr(task, 'deadline', None):
                deadline = task.deadline.strftime('%H:%M %d/%m/%Y')
            elif not deadline:
                deadline = 'Không rõ'
                
            open_time = 'Không có'
            if hasattr(task, 'details') and task.details and getattr(task.details, 'open_time', None):
                open_time = task.details.open_time.strftime('%H:%M %d/%m/%Y')
            
            # Tính thời gian còn lại
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

            url = getattr(task, 'link', getattr(task, 'url', ''))

            embed = {
                'title': title,
                'description': f'**Môn:** {course}',
                'color': color,
                'fields': [
                    {'name': '⏰ Hạn chót', 'value': f'{deadline}', 'inline': True},
                    {'name': '⏳ Còn lại', 'value': remaining, 'inline': True},
                    {'name': '🚨 Trạng thái', 'value': '🔴 Khẩn cấp' if is_critical else ('🟠 Tới hạn' if task.urgency == UrgencyLevel.WARNING else '🟢 An toàn'), 'inline': False},
                ],
                'footer': {'text': 'UTH-Elearning-Alert by Smart Agent'}
            }
            if open_time != 'Không có':
                embed['fields'].insert(0, {'name': '🗓️ Ngày mở', 'value': open_time, 'inline': True})
            if url: embed['url'] = url
            embeds.append(embed)'''

    # Pattern assumes the or task in tasks: loop down to mbeds.append(embed)
    pat = re.compile(r'        for task in tasks:.*?embeds\.append\(embed\)', re.DOTALL)
    if pat.search(content):
        content = pat.sub(replacement, content)
        with open("src/notifiers/discord.py", "w", encoding="utf-8") as f:
            f.write(content)

def rewrite_telegram():
    with open("src/notifiers/telegram.py", "r", encoding="utf-8") as f:
        content = f.read()

    replacement = '''        for a in tasks:
            is_critical = a.urgency == UrgencyLevel.CRITICAL
            icon = "🔴" if is_critical else ("🟠" if a.urgency == UrgencyLevel.WARNING else "🟢")

            title = getattr(a, "title", "Không rõ tiêu đề")
            course = getattr(a, "course_name", getattr(a, "course", "Không rõ môn"))
            
            if hasattr(a, "deadline_str") and a.deadline_str:
                deadline = a.deadline_str
            else:
                deadline = a.deadline.strftime("%H:%M %d/%m/%Y") if hasattr(a, "deadline") and a.deadline else "Không rõ hạn"
                
            open_time = None
            if hasattr(a, 'details') and a.details and getattr(a.details, 'open_time', None):
                open_time = a.details.open_time.strftime('%H:%M %d/%m/%Y')
                
            remaining = "Không rõ"
            if hasattr(a, 'deadline') and a.deadline:
                import datetime
                delta = a.deadline - datetime.datetime.now()
                days, seconds = delta.days, delta.seconds
                hours = seconds // 3600
                if days < 0:
                    remaining = "Quá hạn rồi!"
                elif days > 0:
                    remaining = f"Còn {days} ngày {hours} giờ"
                else:
                    remaining = f"Còn {hours} giờ {seconds % 3600 // 60} phút"

            url = getattr(a, "link", getattr(a, "url", ""))
            task_type = getattr(a, "type", getattr(a, "event_type", "Bài tập"))

            text += f"{icon} <b>Môn học:</b> {course}\\n"
            text += f"📝 <b>Loại:</b> {task_type}\\n"
            text += f"📌 <b>Tiêu đề:</b> {title}\\n"
            if open_time:
                text += f"🗓️ <b>Ngày mở:</b> {open_time}\\n"
            text += f"⏰ <b>Hạn chót:</b> <u>{deadline}</u> ({remaining})\\n"
            if url:
                text += f"🔗 👉 <a href='{url}'>Nhấn vào đây để xem chi tiết</a>\\n"
            text += "\\n"'''

    pat = re.compile(r'        for a in tasks:.*?text \+= "\\n"', re.DOTALL)
    if pat.search(content):
        content = pat.sub(replacement, content)
        with open("src/notifiers/telegram.py", "w", encoding="utf-8") as f:
            f.write(content)

def rewrite_windows():
    with open("src/notifiers/windows.py", "r", encoding="utf-8") as f:
        content = f.read()

    replacement = '''        critical = [a for a in assignments if getattr(a, 'urgency', None) == UrgencyLevel.CRITICAL]
        warnings = [a for a in assignments if getattr(a, 'urgency', None) == UrgencyLevel.WARNING]
        safes = [a for a in assignments if getattr(a, 'urgency', None) == UrgencyLevel.SAFE]
        other_count = len(assignments) - len(critical) - len(warnings) - len(safes)
        title = "UTHelper - Thông báo"
        if len(assignments) == 1:
            a = assignments[0]
            title = getattr(a, 'title', 'Bài tập mới')
            course = getattr(a, 'course_name', getattr(a, 'course', 'Không rõ môn'))
            
            remaining = "Không rõ"
            if hasattr(a, 'deadline') and a.deadline:
                import datetime
                delta = a.deadline - datetime.datetime.now()
                d, s = delta.days, delta.seconds
                if d < 0:
                    remaining = "Quá hạn!"
                elif d > 0:
                    remaining = f"Còn {d} ngày {s//3600}h"
                else:
                    remaining = f"Còn {s//3600}h {(s%3600)//60}p"
            
            msg = f"{course}\\n⏰ {remaining} | {getattr(a, 'urgency_str', getattr(a, 'urgency', '...'))}"
        else:
            msg = f"Bạn có {len(critical)} bài cực gấp, {len(warnings)} sắp tới hạn và {len(safes) + other_count} bài khác."'''

    pat = re.compile(r'        critical = \[a for a in assignments if getattr\(a, \'urgency\', None\) == UrgencyLevel\.CRITICAL\].*?msg = f"Bạn có \{len\(critical\)\} bài cực gấp, \{len\(warnings\)\} sắp tới hạn và \{len\(safes\) \+ other_count\} bài khác\."', re.DOTALL)
    if pat.search(content):
        content = pat.sub(replacement, content)
        with open("src/notifiers/windows.py", "w", encoding="utf-8") as f:
            f.write(content)
            
def rewrite_email():
    with open("src/notifiers/email.py", "r", encoding="utf-8") as f:
        content = f.read()

    # Find the HTML generation part for tasks
    replacement = '''            for a in tasks:
                is_critical = a.urgency == UrgencyLevel.CRITICAL
                color_bg = "#FFEAEA" if is_critical else ("#FFF4E5" if a.urgency == UrgencyLevel.WARNING else "#E8F5E9")
                color_border = "#FF4444" if is_critical else ("#FFA000" if a.urgency == UrgencyLevel.WARNING else "#4CAF50")
                title = getattr(a, "title", "Không rõ tiêu đề")
                course = getattr(a, "course_name", getattr(a, "course", "Không rõ môn"))
                
                if hasattr(a, "deadline_str") and a.deadline_str:
                    deadline = a.deadline_str
                else:
                    deadline = a.deadline.strftime("%H:%M %d/%m/%Y") if hasattr(a, "deadline") and a.deadline else "Không rõ hạn"
                
                open_time_str = ""
                if hasattr(a, 'details') and a.details and getattr(a.details, 'open_time', None):
                    open_time_str = f"""<tr>
                            <td style='padding: 8px 0; color: #666;'><strong>Ngày mở:</strong></td>
                            <td style='padding: 8px 0;'>{a.details.open_time.strftime('%H:%M %d/%m/%Y')}</td>
                        </tr>"""
                        
                remaining = "Không rõ"
                if hasattr(a, 'deadline') and a.deadline:
                    import datetime
                    delta = a.deadline - datetime.datetime.now()
                    days, seconds = delta.days, delta.seconds
                    hours = seconds // 3600
                    if days < 0:
                        remaining = "Quá hạn rồi!"
                    elif days > 0:
                        remaining = f"Còn {days} ngày {hours} giờ"
                    else:
                        remaining = f"Còn {hours} giờ {seconds % 3600 // 60} phút"

                url = getattr(a, "link", getattr(a, "url", ""))
                
                html += f"""
                <div style='background-color: {color_bg}; border-left: 4px solid {color_border}; padding: 15px; margin-bottom: 15px; border-radius: 4px;'>
                    <h3 style='margin: 0 0 10px 0; color: #333;'>{course}</h3>
                    <p style='margin: 0 0 10px 0; font-size: 16px;'><strong>{title}</strong></p>
                    <table style='width: 100%; border-collapse: collapse;'>
                        {open_time_str}
                        <tr>
                            <td style='padding: 8px 0; color: #666; width: 100px;'><strong>Hạn chót:</strong></td>
                            <td style='padding: 8px 0;'>{deadline} <span style='color: {color_border}; font-weight: bold;'>({remaining})</span></td>
                        </tr>
                    </table>
                    <div style='margin-top: 15px;'>
                        <a href='{url}' style='background-color: {color_border}; color: white; text-decoration: none; padding: 10px 20px; border-radius: 4px; display: inline-block; font-weight: bold;'>Xem chi tiết</a>
                    </div>
                </div>
                """'''

    pat = re.compile(r'            for a in tasks:.*?</div>\s+"""', re.DOTALL)
    if pat.search(content):
        content = pat.sub(replacement, content)
        with open("src/notifiers/email.py", "w", encoding="utf-8") as f:
            f.write(content)

rewrite_discord()
rewrite_telegram()
rewrite_windows()
rewrite_email()
print("Rewrite complete")
