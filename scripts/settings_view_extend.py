import sys, os, re
path = r'E:\Projects\UTH-Elearning-Alert\src\gui\components\settings_view.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

# Add UI switches
old_switches = '''        self._sw_submitted = ft.Switch(
            value=settings.INCLUDE_SUBMITTED, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Hiện bài đã nộp"
        )
        self._sw_graded = ft.Switch(
            value=settings.INCLUDE_GRADED, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Hiện bài đã chấm"
        )'''

new_switches = '''        self._sw_submitted = ft.Switch(
            value=settings.INCLUDE_SUBMITTED, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Hiện bài đã nộp"
        )
        self._sw_graded = ft.Switch(
            value=settings.INCLUDE_GRADED, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Hiện bài đã chấm"
        )

        
        self._sw_start_with_windows = ft.Switch(
            value=settings.START_WITH_WINDOWS, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Khởi động cùng Windows"
        )
        self._sw_start_minimized = ft.Switch(
            value=settings.START_MINIMIZED, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Khởi động thu nhỏ (Ngầm)"
        )
        self._sw_minimize_to_tray = ft.Switch(
            value=settings.MINIMIZE_TO_TRAY, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Thu nhỏ xuống System Tray"
        )

        self._sw_discord = ft.Switch(
            value=settings.ENABLE_DISCORD, active_color=C.ACCENT,
            label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            label="Bật gửi qua Discord (Future)"
        )'''

if "self._sw_start_with_windows" not in text:
    text = text.replace(old_switches, new_switches)

# Add elements to the layout
old_layout = '''                        _section("BỘ LỌC HIỂN THỊ & GIAO DIỆN"),
                        _card(self._sw_submitted, self._sw_graded, self._sw_always_on_top),'''

new_layout = '''                        _section("BỘ LỌC HIỂN THỊ & GIAO DIỆN"),
                        _card(self._sw_submitted, self._sw_graded, self._sw_always_on_top),
                        
                        _section("HỆ THỐNG & KHỞI ĐỘNG"),
                        _card(self._sw_start_with_windows, self._sw_start_minimized, self._sw_minimize_to_tray),
                        
                        _section("TÍCH HỢP & BOT"),
                        _card(self._sw_discord),
'''

if "_section(\"HỆ THỐNG & KHỞI ĐỘNG\")" not in text:
    text = text.replace(old_layout, new_layout)

# Update _save method to save
old_save = '''            settings.INCLUDE_SUBMITTED = self._sw_submitted.value
            settings.INCLUDE_GRADED = self._sw_graded.value'''

new_save = '''            settings.INCLUDE_SUBMITTED = self._sw_submitted.value
            settings.INCLUDE_GRADED = self._sw_graded.value
            settings.START_WITH_WINDOWS = self._sw_start_with_windows.value
            settings.START_MINIMIZED = self._sw_start_minimized.value
            settings.MINIMIZE_TO_TRAY = self._sw_minimize_to_tray.value
            settings.ENABLE_DISCORD = self._sw_discord.value
            
            # Xử lý Start with windows (Registry)
            from core.autostart import add_to_startup, remove_from_startup
            if settings.START_WITH_WINDOWS:
                add_to_startup()
            else:
                remove_from_startup()
'''

if "settings.START_WITH_WINDOWS =" not in text:
    text = text.replace(old_save, new_save)

# Set keys
old_env = '''            set_key(env_path, "INCLUDE_SUBMITTED", str(settings.INCLUDE_SUBMITTED).lower())
            set_key(env_path, "INCLUDE_GRADED", str(settings.INCLUDE_GRADED).lower())'''

new_env = '''            set_key(env_path, "INCLUDE_SUBMITTED", str(settings.INCLUDE_SUBMITTED).lower())
            set_key(env_path, "INCLUDE_GRADED", str(settings.INCLUDE_GRADED).lower())
            set_key(env_path, "START_WITH_WINDOWS", str(settings.START_WITH_WINDOWS).lower())
            set_key(env_path, "START_MINIMIZED", str(settings.START_MINIMIZED).lower())
            set_key(env_path, "MINIMIZE_TO_TRAY", str(settings.MINIMIZE_TO_TRAY).lower())
            set_key(env_path, "ENABLE_DISCORD", str(settings.ENABLE_DISCORD).lower())'''

if 'set_key(env_path, "START_WITH_WINDOWS"' not in text:
    text = text.replace(old_env, new_env)

# load_current_settings update
old_load = '''        self._sw_always_on_top.value = settings.ALWAYS_ON_TOP
        self._sw_submitted.value = settings.INCLUDE_SUBMITTED
        self._sw_graded.value = settings.INCLUDE_GRADED'''

new_load = '''        self._sw_always_on_top.value = settings.ALWAYS_ON_TOP
        self._sw_submitted.value = settings.INCLUDE_SUBMITTED
        self._sw_graded.value = settings.INCLUDE_GRADED
        self._sw_start_with_windows.value = settings.START_WITH_WINDOWS
        self._sw_start_minimized.value = settings.START_MINIMIZED
        self._sw_minimize_to_tray.value = settings.MINIMIZE_TO_TRAY
        self._sw_discord.value = settings.ENABLE_DISCORD'''

if "self._sw_start_with_windows.value = settings.START_WITH_WINDOWS" not in text:
    text = text.replace(old_load, new_load)


old_has_changes = '''        if self._sw_always_on_top.value != settings.ALWAYS_ON_TOP: return True
        if self._sw_submitted.value != settings.INCLUDE_SUBMITTED: return True
        if self._sw_graded.value != settings.INCLUDE_GRADED: return True'''

new_has_changes = '''        if self._sw_always_on_top.value != settings.ALWAYS_ON_TOP: return True
        if self._sw_submitted.value != settings.INCLUDE_SUBMITTED: return True
        if self._sw_graded.value != settings.INCLUDE_GRADED: return True
        if self._sw_start_with_windows.value != settings.START_WITH_WINDOWS: return True
        if self._sw_start_minimized.value != settings.START_MINIMIZED: return True
        if self._sw_minimize_to_tray.value != settings.MINIMIZE_TO_TRAY: return True
        if self._sw_discord.value != settings.ENABLE_DISCORD: return True'''

if "self._sw_start_with_windows.value != settings.START_WITH_WINDOWS: return True" not in text:
    text = text.replace(old_has_changes, new_has_changes)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("done extending view")
