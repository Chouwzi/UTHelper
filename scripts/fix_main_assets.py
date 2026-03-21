import sys, os

path = r'E:\Projects\UTH-Elearning-Alert\src\main.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

import textwrap
if "import os" not in text:
    text = text.replace("import flet as ft", "import flet as ft\nimport os")

old_app = 'ft.app(target=app_main, assets_dir=r"../assets")'
new_app = 'ft.app(target=app_main, assets_dir=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets")))'

text = text.replace(old_app, new_app)
with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("fixed main assets")
