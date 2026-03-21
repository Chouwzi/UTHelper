import re

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\components\settings_view.py'
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

# Make all textfields have clear, clean layout sizes.
text = re.sub(
    r'(self\._[a-zA-Z_]+_field\s*=\s*ft\.TextField\([^)]+)\)',
    lambda m: m.group(1).replace('expand=True', '') + ')',
    text
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)
