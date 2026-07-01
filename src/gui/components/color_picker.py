import flet as ft
from gui.core.theme import C

class ColorPicker:
    """Helper class to build and display a custom color picker dialog in Flet."""
    
    def __init__(self, page: ft.Page, container_box: ft.Container, label: str, tb_field: ft.TextField):
        self.page = page
        self.container_box = container_box
        self.label = label
        self.tb_field = tb_field
        
        # Parse initial color
        curr = self.tb_field.value.lstrip('#')
        try:
            self.r_val, self.g_val, self.b_val = int(curr[0:2], 16), int(curr[2:4], 16), int(curr[4:6], 16)
        except Exception:
            self.r_val, self.g_val, self.b_val = 0, 0, 0
            
        self.dlg = None

    def show(self):
        r_sl = ft.Slider(min=0, max=255, value=self.r_val, active_color=ft.Colors.RED_400, on_change=self._update_from_sliders, expand=True)
        g_sl = ft.Slider(min=0, max=255, value=self.g_val, active_color=ft.Colors.GREEN_400, on_change=self._update_from_sliders, expand=True)
        b_sl = ft.Slider(min=0, max=255, value=self.b_val, active_color=ft.Colors.BLUE_400, on_change=self._update_from_sliders, expand=True)
        
        self.hex_inp = ft.TextField(value=self.tb_field.value, on_change=self._update_from_hex, text_align=ft.TextAlign.CENTER, border_radius=8, content_padding=5, text_size=13, width=100)
        self.prv = ft.Container(width=100, height=40, bgcolor=self.tb_field.value, border_radius=8, border=ft.Border.all(1, C.BORDER))
        
        self.r_sl = r_sl
        self.g_sl = g_sl
        self.b_sl = b_sl

        self.dlg = ft.AlertDialog(
            title=ft.Text(f"Chọn màu: {self.label}", size=16, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                width=300,
                content=ft.Column([
                    ft.Row([self.prv, self.hex_inp], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ft.Container(height=10),
                    ft.Row([ft.Text("R", color=ft.Colors.RED_400, weight=ft.FontWeight.BOLD, width=20), r_sl]),
                    ft.Row([ft.Text("G", color=ft.Colors.GREEN_400, weight=ft.FontWeight.BOLD, width=20), g_sl]),
                    ft.Row([ft.Text("B", color=ft.Colors.BLUE_400, weight=ft.FontWeight.BOLD, width=20), b_sl]),
                ], tight=True)
            ),
            actions=[
                ft.TextButton("Hủy", on_click=self._cancel),
                ft.Button("Áp dụng", on_click=self._apply, bgcolor=C.ACCENT, color=ft.Colors.WHITE),
            ],
            shape=ft.RoundedRectangleBorder(radius=12)
        )
        self.page.overlay.append(self.dlg)
        self.dlg.open = True
        self.page.update()

    def _update_from_sliders(self, e):
        hex_val = f"#{int(self.r_sl.value):02X}{int(self.g_sl.value):02X}{int(self.b_sl.value):02X}"
        self.prv.bgcolor = hex_val
        self.prv.update()
        self.hex_inp.value = hex_val
        self.hex_inp.update()

    def _update_from_hex(self, e):
        try:
            val = self.hex_inp.value.strip().lstrip('#')
            if len(val) == 6:
                r, g, b = int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16)
                self.r_sl.value, self.g_sl.value, self.b_sl.value = r, g, b
                self.r_sl.update()
                self.g_sl.update()
                self.b_sl.update()
                self.prv.bgcolor = f"#{val}"
                self.prv.update()
        except Exception:
            pass

    def _apply(self, e):
        self.container_box.bgcolor = self.hex_inp.value
        self.tb_field.value = self.hex_inp.value
        self.container_box.update()
        self.tb_field.update()
        self.dlg.open = False
        try:
            self.page.overlay.remove(self.dlg)
        except (ValueError, AttributeError):
            pass
        self.page.update()

    def _cancel(self, e):
        self.dlg.open = False
        try:
            self.page.overlay.remove(self.dlg)
        except (ValueError, AttributeError):
            pass
        self.page.update()

def open_color_picker(page: ft.Page, container_box: ft.Container, label: str, tb_field: ft.TextField):
    """Utility function to open a custom ColorPicker dialog."""
    picker = ColorPicker(page, container_box, label, tb_field)
    picker.show()
