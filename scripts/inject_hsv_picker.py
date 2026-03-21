import re

with open('src/gui/components/settings_view.py', 'r', encoding='utf-8') as f:
    text = f.read()

# We want to replace the whole def open_color_picker(...) up until right BEFORE the duplicated def _color_field(label_text, default_color): or self._c_tb_critical definition

# Let's first remove the second def _color_field block that was duplicated.
# Looking at the file, the duplicated block starts at around         def _color_field(label_text, default_color): after dlg.open = True\n            self._page.update()\n

text = re.sub(
    r'\n        def _color_field\(label_text, default_color\):(.*?) btn_reset = ft\.OutlinedButton',
    r'\n        btn_reset = ft.OutlinedButton', 
    text, flags=re.DOTALL
)

hsv_picker_code = '''
        def open_color_picker(e, container_box, label, tb_field):
            import colorsys
            
            # Start state
            h, s, v = 0.0, 1.0, 1.0
            
            try:
                curr_hex = tb_field.value.strip().lstrip('#')
                if len(curr_hex) == 6:
                    r, g, b = int(curr_hex[0:2], 16)/255.0, int(curr_hex[2:4], 16)/255.0, int(curr_hex[4:6], 16)/255.0
                    h, s, v = colorsys.rgb_to_hsv(r, g, b)
            except:
                pass

            hue_color = "#FF0000"
            current_color = tb_field.value
            if not current_color.startswith('#'):
                current_color = "#FF0000"
                
            def hsv_to_hex(h, s, v):
                r, g, b = colorsys.hsv_to_rgb(h, s, v)
                return f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}"

            sv_base = ft.Container(
                width=240, height=200, border_radius=4,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment(-1, 0), end=ft.Alignment(1, 0),
                    colors=[ft.Colors.WHITE, hue_color]
                )
            )
            sv_overlay = ft.Container(
                width=240, height=200, border_radius=4,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment(0, -1), end=ft.Alignment(0, 1),
                    colors=[ft.Colors.TRANSPARENT, ft.Colors.BLACK]
                )
            )
            sv_pointer = ft.Container(
                left=s*240 - 6, top=(1-v)*200 - 6,
                width=12, height=12, border_radius=6,
                border=ft.Border(top=ft.BorderSide(2, ft.Colors.WHITE), bottom=ft.BorderSide(2, ft.Colors.WHITE), left=ft.BorderSide(2, ft.Colors.WHITE), right=ft.BorderSide(2, ft.Colors.WHITE)),
                bgcolor=ft.Colors.TRANSPARENT
            )
            
            hue_bg = ft.Container(
                width=24, height=200, border_radius=4,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment(0, -1), end=ft.Alignment(0, 1),
                    colors=["#FF0000", "#FFFF00", "#00FF00", "#00FFFF", "#0000FF", "#FF00FF", "#FF0000"]
                )
            )
            hue_pointer = ft.Container(
                left=-2, top=h*200 - 4, width=28, height=8,
                border=ft.Border(top=ft.BorderSide(2, ft.Colors.WHITE), bottom=ft.BorderSide(2, ft.Colors.WHITE), left=ft.BorderSide(2, ft.Colors.WHITE), right=ft.BorderSide(2, ft.Colors.WHITE)), border_radius=4,
                bgcolor=ft.Colors.TRANSPARENT
            )

            prv = ft.Container(width=100, height=36, bgcolor=current_color, border_radius=8, border=ft.border.all(1, C.BORDER))
            hex_inp = ft.TextField(value=current_color, text_align=ft.TextAlign.CENTER, border_radius=8, content_padding=5, text_size=13, width=120)

            def update_ui():
                nonlocal hue_color, current_color
                hue_color = hsv_to_hex(h, 1.0, 1.0)
                current_color = hsv_to_hex(h, s, v)
                
                sv_base.gradient.colors[1] = hue_color
                sv_pointer.left = max(-6, min(240-6, s * 240 - 6))
                sv_pointer.top = max(-6, min(200-6, (1 - v) * 200 - 6))
                
                hue_pointer.top = max(-4, min(200-4, h * 200 - 4))
                
                prv.bgcolor = current_color
                hex_inp.value = current_color
                
                if dlg.open:
                    sv_base.update()
                    sv_pointer.update()
                    hue_pointer.update()
                    prv.update()
                    hex_inp.update()

            def on_sv_action(e):
                nonlocal s, v
                if hasattr(e, "local_position"):
                    lx = e.local_position.x
                    ly = e.local_position.y
                else:
                    return # not trackable
                s = max(0.0, min(1.0, lx / 240.0))
                v = max(0.0, min(1.0, 1.0 - (ly / 200.0)))
                update_ui()

            def on_h_action(e):
                nonlocal h
                if hasattr(e, "local_position"):
                    ly = e.local_position.y
                else:
                    return
                h = max(0.0, min(1.0, ly / 200.0))
                update_ui()
                
            def on_hex_change(e):
                nonlocal h, s, v
                try:
                    val = hex_inp.value.strip().lstrip('#')
                    if len(val) == 6:
                        r, g, b = int(val[0:2], 16)/255.0, int(val[2:4], 16)/255.0, int(val[4:6], 16)/255.0
                        h, s, v = colorsys.rgb_to_hsv(r, g, b)
                        prv.bgcolor = f"#{val}"
                        prv.update()
                        
                        hue_color_hex = hsv_to_hex(h, 1.0, 1.0)
                        sv_base.gradient.colors[1] = hue_color_hex
                        sv_base.update()
                        
                        sv_pointer.left = max(-6, min(240-6, s * 240 - 6))
                        sv_pointer.top = max(-6, min(200-6, (1 - v) * 200 - 6))
                        sv_pointer.update()
                        
                        hue_pointer.top = max(-4, min(200-4, h * 200 - 4))
                        hue_pointer.update()
                except:
                    pass

            hex_inp.on_change = on_hex_change

            sv_stack = ft.GestureDetector(
                on_pan_update=on_sv_action,
                on_tap_down=on_sv_action,
                content=ft.Stack([sv_base, sv_overlay, sv_pointer], width=240, height=200) 
            )

            hue_stack = ft.GestureDetector(
                on_pan_update=on_h_action,
                on_tap_down=on_h_action,
                content=ft.Stack([hue_bg, hue_pointer], width=24, height=200)
            )

            def _apply(e):
                container_box.bgcolor = hex_inp.value
                tb_field.value = hex_inp.value
                container_box.update()
                tb_field.update()
                dlg.open = False
                self._page.update()

            def _cancel(e):
                dlg.open = False
                self._page.update()
                
            update_ui()

            dlg = ft.AlertDialog(
                title=ft.Text(f"Chọn màu: {label}", size=16, weight=ft.FontWeight.BOLD),
                content=ft.Container(
                    width=300,
                    content=ft.Column([
                        ft.Row([sv_stack, hue_stack], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, tight=True),
                        ft.Container(height=10),
                        ft.Row([prv, hex_inp], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ], tight=True)
                ),
                actions=[
                    ft.TextButton("Hủy", on_click=_cancel),
                    ft.ElevatedButton("Áp dụng", on_click=_apply, bgcolor=C.ACCENT, color=ft.Colors.WHITE),
                ],
                shape=ft.RoundedRectangleBorder(radius=12)
            )
            self._page.overlay.append(dlg)
            dlg.open = True
            self._page.update()
'''

text = re.sub(
    r'        def open_color_picker\(e, container_box, label, tb_field\):(.*?)self\._page\.update\(\)',
    hsv_picker_code.strip('\n'),
    text,
    flags=re.DOTALL
)

with open('src/gui/components/settings_view.py', 'w', encoding='utf-8') as f:
    f.write(text)
