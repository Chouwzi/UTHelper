import sys, os, re

# Update SettingsView
path = r'src/gui/components/settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

# Update __init__
text = text.replace("def __init__(self, page: ft.Page, on_close, on_saved=None):", 
"def __init__(self, page: ft.Page, on_close, on_saved=None, on_test_tray=None):")
text = text.replace("self._on_saved_cb = on_saved", "self._on_saved_cb = on_saved\n        self._on_test_tray = on_test_tray")

# Add switches definition
old_switches = '''        self._sw_discord = ft.Switch(
            value=settings.ENABLE_DISCORD, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bật gửi qua Discord (Future)"
        )'''

new_switches = '''        self._sw_discord = ft.Switch(
            value=settings.ENABLE_DISCORD, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bật gửi qua Discord (Future)"
        )
        self._sw_debug = ft.Switch(
            value=settings.DEBUG_MODE, active_color=C.CRITICAL,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Chế độ Gỡ lỗi (Debug Log)",
            on_change=lambda e: self._toggle_debug_ui()
        )
        
        self._test_panel = ft.Container(
            content=ft.Column([
                ft.Text("Công cụ Debug", color=C.CRITICAL, weight=ft.FontWeight.BOLD),
                ft.ElevatedButton("Test Thông báo qua System Tray", on_click=lambda e: self._do_test_tray(), bgcolor=C.SURFACE, color=C.TEXT_PRIMARY)
            ]),
            visible=settings.DEBUG_MODE,
            padding=10, border=ft.border.all(1, C.CRITICAL), border_radius=8, margin=ft.margin.only(top=10)
        )'''

if "self._sw_debug = " not in text:
    text = text.replace(old_switches, new_switches)

# Insert the toggle functions
import textwrap
if "def _toggle_debug_ui(self):" not in text:
    new_funcs = '''
    def _toggle_debug_ui(self):
        self._test_panel.visible = self._sw_debug.value
        self._test_panel.update()
        
    def _do_test_tray(self):
        if self._on_test_tray:
            self._on_test_tray()
'''
    text = text.replace("def load_current_settings(self):", new_funcs + "\n    def load_current_settings(self):")

# Update section arrangement
old_form = '''                        self._section("Hệ thống & Khởi động", [
                            self._sw_start_with_windows,
                            self._sw_start_minimized,
                            self._sw_minimize_to_tray,
                        ]),
                        self._section("Thông báo đa nền tảng", [
                            self._sw_discord,
                        ])'''

new_form = '''                        self._section("Hệ thống & Khởi động", [
                            self._sw_start_with_windows,
                            self._sw_start_minimized,
                            self._sw_minimize_to_tray,
                        ]),
                        self._section("Thông báo đa nền tảng", [
                            self._sw_discord,
                        ]),
                        self._section("Nâng cao", [
                            self._sw_debug,
                            self._test_panel
                        ])'''

if "self._section(\"Nâng cao\"" not in text:
    text = text.replace(old_form, new_form)

# Add load_current_settings block
old_load = '''        self._sw_start_minimized.value  = settings.START_MINIMIZED
        self._sw_minimize_to_tray.value = settings.MINIMIZE_TO_TRAY
        self._sw_discord.value          = settings.ENABLE_DISCORD'''

new_load = '''        self._sw_start_minimized.value  = settings.START_MINIMIZED
        self._sw_minimize_to_tray.value = settings.MINIMIZE_TO_TRAY
        self._sw_discord.value          = settings.ENABLE_DISCORD
        self._sw_debug.value            = settings.DEBUG_MODE
        self._test_panel.visible        = settings.DEBUG_MODE'''

if "self._sw_debug.value" not in text:
    text = text.replace(old_load, new_load)


# Change has_changes block
old_chg = '''        if self._sw_start_minimized.value != settings.START_MINIMIZED: return True
        if self._sw_minimize_to_tray.value != settings.MINIMIZE_TO_TRAY: return True
        if self._sw_discord.value != settings.ENABLE_DISCORD: return True'''

new_chg = '''        if self._sw_start_minimized.value != settings.START_MINIMIZED: return True
        if self._sw_minimize_to_tray.value != settings.MINIMIZE_TO_TRAY: return True
        if self._sw_discord.value != settings.ENABLE_DISCORD: return True
        if self._sw_debug.value != settings.DEBUG_MODE: return True'''

if "settings.DEBUG_MODE: return True" not in text:
    text = text.replace(old_chg, new_chg)
    
# Change _save func
old_save = '''            _save_setting("START_MINIMIZED",   self._sw_start_minimized.value)
            _save_setting("MINIMIZE_TO_TRAY",  self._sw_minimize_to_tray.value)
            _save_setting("ENABLE_DISCORD",    self._sw_discord.value)'''
new_save = '''            _save_setting("START_MINIMIZED",   self._sw_start_minimized.value)
            _save_setting("MINIMIZE_TO_TRAY",  self._sw_minimize_to_tray.value)
            _save_setting("ENABLE_DISCORD",    self._sw_discord.value)
            _save_setting("DEBUG_MODE",        self._sw_debug.value)'''

if "_save_setting(\"DEBUG_MODE\"" not in text:
    text = text.replace(old_save, new_save)


with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done view 3")
