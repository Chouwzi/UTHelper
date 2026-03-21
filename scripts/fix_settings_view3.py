import sys, os, re

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\components\settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

text = text.replace("                self._warning_hours_field", "        self._warning_hours_field")

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("fixed indent")
