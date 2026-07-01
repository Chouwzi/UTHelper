import flet as ft
from gui.core.theme import C
from config import settings
import platform_utils as _pu

def init_display_controls(view):
    view._sw_always_on_top = ft.Switch(
        value=settings.ALWAYS_ON_TOP, active_color=C.ACCENT,
        label="Luôn ở trên cùng",
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
    )
    view._sw_submitted = ft.Switch(
        value=settings.INCLUDE_SUBMITTED, active_color=C.ACCENT,
        label="Hiển thị bài đã nộp",
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
    )
    view._sw_graded = ft.Switch(
        value=settings.INCLUDE_GRADED, active_color=C.ACCENT,
        label="Hiển thị bài đã chấm",
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
    )

def build_display_section(view) -> ft.Container:
    controls = [view._sw_submitted, view._sw_graded]
    if not _pu.IS_MOBILE:
        controls.append(view._sw_always_on_top)
    
    return view._build_setting_group(
        "Hiển thị",
        "Cách hiển thị trên màn hình",
        controls,
        icon=ft.Icons.VISIBILITY_OUTLINED,
    )
