import re

with open('src/gui/tray.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Fix window destroy coroutine issue in tray
text = text.replace('self._page.window.destroy()', 'self._page.run_task(self._page.window.destroy)')

with open('src/gui/tray.py', 'w', encoding='utf-8') as f:
    f.write(text)

with open('src/gui/components/settings_view.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Make gestures smoother by defining drag_interval
# By default Flet drag events stream over websocket as fast as possible, which might cause throttling depending on event volume.
# Setting drag_interval=10 helps throttle slightly avoiding backpressure lag, or setting it explicitly to low value
text = text.replace('''
            sv_stack = ft.GestureDetector(
                on_pan_update=on_sv_action,
                on_tap_down=on_sv_action,
                content=ft.Stack([sv_base, sv_overlay, sv_pointer], width=240, height=200) 
            )''', '''
            sv_stack = ft.GestureDetector(
                on_pan_update=on_sv_action,
                on_tap_down=on_sv_action,
                drag_interval=10,
                content=ft.Stack([sv_base, sv_overlay, sv_pointer], width=240, height=200) 
            )''')

text = text.replace('''
            hue_stack = ft.GestureDetector(
                on_pan_update=on_h_action,
                on_tap_down=on_h_action,
                content=ft.Stack([hue_bg, hue_pointer], width=24, height=200)
            )''', '''
            hue_stack = ft.GestureDetector(
                on_pan_update=on_h_action,
                on_tap_down=on_h_action,
                drag_interval=10,
                content=ft.Stack([hue_bg, hue_pointer], width=24, height=200)
            )''')

# In update_ui, Flet has a known lag when updating heavily nested complex UI on continuous events. 
# In 	est_picker.py it was directly page.update() which batches updates globally. 
# But in settings_view sv_base.update(), sv_pointer.update(), prv.update(), and hex_inp.update() are triggered sequentially.
# Sequential updates send multiple independent WS calls causing jitter.
# Instead we should batch-update them using a parent container or just call dlg.update()

text = text.replace('''
                try:
                    sv_base.update()
                    sv_pointer.update()
                    hue_pointer.update()
                    prv.update()
                    hex_inp.update()
                except Exception:
                    pass''', '''
                try:
                    dlg.update()
                except Exception:
                    pass''')


with open('src/gui/components/settings_view.py', 'w', encoding='utf-8') as f:
    f.write(text)
print("Applied lag fixes and coroutine warn fix")
