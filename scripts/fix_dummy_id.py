import sys, os, re

path = r'src/gui/app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_dummy = '''        dummy = Assignment(
            id=9999, title="Bài viết kiểm thử thông báo từ hệ thống",'''

new_dummy = '''        import random
        dummy = Assignment(
            id=random.randint(10000, 99999), title="Bài viết kiểm thử thông báo từ hệ thống",'''

text = text.replace(old_dummy, new_dummy)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("fixed dummy id")
