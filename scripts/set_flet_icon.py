import sys, os, re

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_init = '''        self.page.window.always_on_top = settings.ALWAYS_ON_TOP
        self.page.window.resizable    = False
        self.page.title               = "UTH Alert"'''

new_init = '''        self.page.window.always_on_top = settings.ALWAYS_ON_TOP
        self.page.window.resizable    = False
        self.page.window.icon         = "icon.png"  # Sử dụng icon.png trong thư mục assets
        self.page.title               = "UTH Alert"'''

if "self.page.window.icon" not in text:
    text = text.replace(old_init, new_init)
with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done app_controller icon")

path_main = r'E:\Projects\UTH-Elearning-Alert\src\main.py'
with open(path_main, 'r', encoding='utf-8') as f: text2 = f.read()
text2 = text2.replace("ft.run(app_main)", "ft.app(target=app_main, assets_dir=r\"../assets\")")
# if it was already updated to ft.app, skip
if "ft.run(app_main)" not in text2:
    pass
with open(path_main, 'w', encoding='utf-8') as f: f.write(text2)

path_desktop = r'E:\Projects\UTH-Elearning-Alert\src\gui\compact_desktop.py'
with open(path_desktop, 'r', encoding='utf-8') as f: text3 = f.read()
text3 = text3.replace("ft.run(main)", "ft.app(target=main, assets_dir=os.path.join(project_root, \"assets\"))")
with open(path_desktop, 'w', encoding='utf-8') as f: f.write(text3)
print("done main.py and compact_desktop.py assets_dir")
