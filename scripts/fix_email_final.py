import os
with open("src/notifiers/email.py", "r", encoding="utf-8") as f:
    t = f.read()

# Fix docstring and method indents
t = t.replace('class EmailNotifier(BaseNotifier):\n            """', 'class EmailNotifier(BaseNotifier):\n    """')
t = t.replace('            """\n    def notify', '    """\n    def notify')

with open("src/notifiers/email.py", "w", encoding="utf-8") as f:
    f.write(t)
print("done")
