import sys, os, re

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

text = text.replace('self.page.window.icon         = "icon.png"', 'self.page.window.icon         = "icon.ico"')

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("fixed icon ext")
