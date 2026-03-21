import sys, os, re
sys.path.append(os.path.abspath('src'))

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

# Add tray import
if "from gui.tray import TrayApp" not in text:
    text = text.replace("import asyncio\nimport logging", "import asyncio\nimport logging\nfrom gui.tray import TrayApp")

# Change window close event handling
old_init_window = '''    def _init_window(self):
        self.page.window.width        = 420
        self.page.window.height       = 720
        self.page.window.max_width    = 420
        self.page.window.min_width    = 420
        self.page.window.always_on_top = settings.ALWAYS_ON_TOP
        self.page.window.resizable    = False
        self.page.title               = "UTH Alert"
        self.page.bgcolor             = C.BG
        self.page.padding             = 0
        self.page.spacing             = 0
        self.page.theme_mode          = ft.ThemeMode.DARK'''

new_init_window = '''    def _init_window(self):
        self.page.window.width        = 420
        self.page.window.height       = 720
        self.page.window.max_width    = 420
        self.page.window.min_width    = 420
        self.page.window.always_on_top = settings.ALWAYS_ON_TOP
        self.page.window.resizable    = False
        self.page.title               = "UTH Alert"
        self.page.bgcolor             = C.BG
        self.page.padding             = 0
        self.page.spacing             = 0
        self.page.theme_mode          = ft.ThemeMode.DARK
        self.page.window.prevent_close = True
        self.page.on_window_event = self._on_window_event
        
        self.tray = TrayApp(self.page)
        self.tray.setup()
        
        if settings.START_MINIMIZED:
            self.page.window.visible = False
            self.page.window.minimized = True

    def _on_window_event(self, e):
        if e.data == "close":
            if settings.MINIMIZE_TO_TRAY:
                self.page.window.visible = False
                self.page.update()
                self.tray.notify("UTH Alert", "App đã thu nhỏ xuống khay hệ thống.")
            else:
                self.page.window.destroy()'''

if "def _on_window_event" not in text:
    text = text.replace(old_init_window, new_init_window)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done hook loop")
