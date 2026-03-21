import sys, os, re

path = r'src/gui/app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_dummy = '''        from models import Assignment, UrgencyLevel
        import random
        dummy = Assignment(
            id=random.randint(10000, 99999), title="Bài vi?t ki?m th? thông báo t? h? th?ng",
            course="Elearning", deadline="2026-10-10",
            timestamp=123, urgency=UrgencyLevel.CRITICAL, type="assign",
            link="", status="", details={}
        )'''

new_dummy = '''        import random
        import time
        from models import UrgencyLevel
        dummy = {
            "id": str(random.randint(10000, 99999)),
            "title": "BÀI VIẾT KIỂM THỬ THÔNG BÁO TỪ HỆ THỐNG",
            "course_name": "Elearning",
            "deadline_str": "Test Event",
            "urgency": UrgencyLevel.CRITICAL.value,
        }'''

# Handle decoding issues
import re
text = re.sub(r'from models import Assignment, UrgencyLevel\s*import random\s*dummy = Assignment\([^)]+\)', new_dummy, text, flags=re.MULTILINE|re.DOTALL)


with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("fixed dummy")
