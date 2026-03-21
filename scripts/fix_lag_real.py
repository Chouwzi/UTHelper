import re

with open('src/gui/components/settings_view.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Replace update_ui
old_update_ui = '''                  try:
                      dlg.update()
                  except Exception:
                      pass'''

new_update_ui = '''                  try:
                      self._page.update(sv_base, sv_pointer, hue_pointer, prv, hex_inp)
                  except Exception:
                      pass'''
text = text.replace(old_update_ui, new_update_ui)

# Replace apply and cancel
old_apply_cancel = '''            def _apply(e):
                container_box.bgcolor = hex_inp.value
                tb_field.value = hex_inp.value
                container_box.update()
                tb_field.update()
                dlg.open = False
                self._page.update()

            def _cancel(e):
                dlg.open = False
                self._page.update()'''

new_apply_cancel = '''            def _close_cleanup():
                dlg.open = False
                dlg.update()
                try:
                    self._page.overlay.remove(dlg)
                    self._page.update()
                except Exception:
                    pass

            def _apply(e):
                container_box.bgcolor = hex_inp.value
                tb_field.value = hex_inp.value
                try:
                    self._page.update(container_box, tb_field)
                except Exception:
                    pass
                _close_cleanup()

            def _cancel(e):
                _close_cleanup()'''

# Be careful, indentation in original file is 12 spaces.
old_ac_regex = r'            def _apply\(e\):[\s\S]*?self\._page\.update\(\)'
text = re.sub(old_ac_regex, new_apply_cancel, text)

with open('src/gui/components/settings_view.py', 'w', encoding='utf-8') as f:
    f.write(text)
print("File updated!")
