import re
with open('src/gui/components/settings_view.py', 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace('if dlg.open:', 'if True: # was dlg.open')
with open('src/gui/components/settings_view.py', 'w', encoding='utf-8') as f:
    f.write(text)
print('Fixed NameError gracefully')

