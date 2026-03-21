import sys, os, re

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

def replace_setter(method_name):
    global text
    pattern = rf'(    async def {method_name}\(self, .*?\):\n.*?)        self._render_cards\(\)'
    replacement = r'\1        self._update_footer()\n        self._render_cards()'
    text = re.sub(pattern, replacement, text, flags=re.DOTALL)

replace_setter("_set_urgency")
replace_setter("_set_type")
replace_setter("_set_course")

# Also for _toggle_overdue if needed
pattern = rf'(    async def _toggle_overdue\(self, e\):\n.*?)        self._render_cards\(\)'
replacement = r'\1        self._update_footer()\n        self._render_cards()'
text = re.sub(pattern, replacement, text, flags=re.DOTALL)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("Replaced setters")
