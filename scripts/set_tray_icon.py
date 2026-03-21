import sys, os, re

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\tray.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

# Add imports
if "from config import BASE_DIR" not in text:
    text = text.replace("import logging", "import logging\nimport os\nfrom config import BASE_DIR")

# Change tray icon setup
old_setup = '''            if self._icon is None:
                # icon mặc định
                img = Image.new("RGB", (64, 64), color=(59, 130, 246))
                
                menu = pystray.Menu('''

new_setup = '''            if self._icon is None:
                icon_path = os.path.join(BASE_DIR, "assets", "icon.png")
                try:
                    img = Image.open(icon_path)
                except Exception as e:
                    logger.warning(f"Không nạp được icon.png, dùng icon mặc định: {e}")
                    img = Image.new("RGB", (64, 64), color=(59, 130, 246))
                
                menu = pystray.Menu('''

text = text.replace(old_setup, new_setup)
with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done tray icon")
