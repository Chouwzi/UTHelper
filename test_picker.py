import flet as ft
import colorsys

def main(page: ft.Page):
    h, s, v = 0.0, 1.0, 1.0

    hue_color = "#FF0000"
    current_color = "#FF0000"
    
    def update_colors():
        nonlocal hue_color, current_color
        r, g, b = colorsys.hsv_to_rgb(h, 1.0, 1.0)
        hue_color = f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}"
        
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        current_color = f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}"
        
        sv_base.gradient.colors[1] = hue_color
        preview.bgcolor = current_color
        hex_text.value = current_color
        
        pointer.left = s * 200 - 5
        pointer.top = (1 - v) * 200 - 5
        
        hue_pointer.top = h * 200 - 5
        
        page.update()

    def on_sv_pan(e: ft.DragUpdateEvent):
        nonlocal s, v
        s = max(0, min(1, e.local_position.x / 200))
        v = 1 - max(0, min(1, e.local_position.y / 200))
        update_colors()

    def on_sv_tap(e: ft.TapEvent):
        nonlocal s, v
        s = max(0, min(1, e.local_position.x / 200))
        v = 1 - max(0, min(1, e.local_position.y / 200))
        update_colors()

    def on_h_pan(e: ft.DragUpdateEvent):
        nonlocal h
        h = max(0, min(1, e.local_position.y / 200))
        update_colors()

    def on_h_tap(e: ft.TapEvent):
        nonlocal h
        h = max(0, min(1, e.local_position.y / 200))
        update_colors()

    sv_base = ft.Container(
        width=200, height=200,
        gradient=ft.LinearGradient(
            begin=ft.Alignment(-1, 0), end=ft.Alignment(1, 0),
            colors=[ft.Colors.WHITE, hue_color]
        )
    )
    sv_overlay = ft.Container(
        width=200, height=200,
        gradient=ft.LinearGradient(
            begin=ft.Alignment(0, -1), end=ft.Alignment(0, 1),
            colors=[ft.Colors.TRANSPARENT, ft.Colors.BLACK]
        )
    )
    pointer = ft.Container(
        left=195, top=-5,
        width=10, height=10, border_radius=5,
        border=ft.Border(top=ft.BorderSide(1.5, ft.Colors.WHITE), bottom=ft.BorderSide(1.5, ft.Colors.WHITE), left=ft.BorderSide(1.5, ft.Colors.WHITE), right=ft.BorderSide(1.5, ft.Colors.WHITE)),
        bgcolor=ft.Colors.TRANSPARENT
    )
    sv_stack = ft.GestureDetector(
        on_pan_update=on_sv_pan,
        on_tap_down=on_sv_tap,
        content=ft.Stack([sv_base, sv_overlay, pointer], width=200, height=200) 
    )

    hue_bg = ft.Container(
        width=20, height=200, border_radius=4,
        gradient=ft.LinearGradient(
            begin=ft.Alignment(0, -1), end=ft.Alignment(0, 1),
            colors=["#FF0000", "#FFFF00", "#00FF00", "#00FFFF", "#0000FF", "#FF00FF", "#FF0000"]
        )
    )
    hue_pointer = ft.Container(
        left=-2, top=-5, width=24, height=10,
        border=ft.Border(top=ft.BorderSide(2, ft.Colors.WHITE), bottom=ft.BorderSide(2, ft.Colors.WHITE), left=ft.BorderSide(2, ft.Colors.WHITE), right=ft.BorderSide(2, ft.Colors.WHITE)), border_radius=2
    )
    hue_stack = ft.GestureDetector(
        on_pan_update=on_h_pan,
        on_tap_down=on_h_tap,
        content=ft.Stack([hue_bg, hue_pointer], width=20, height=200)
    )

    preview = ft.Container(width=100, height=50, bgcolor=current_color)
    hex_text = ft.Text(value=current_color)

    page.add(ft.Row([sv_stack, hue_stack, ft.Column([preview, hex_text])]))     
    update_colors()

ft.app(target=main)
