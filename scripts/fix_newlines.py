import re
for f_name in ["src/notifiers/windows.py", "src/notifiers/telegram.py", "src/notifiers/discord.py", "src/notifiers/email.py"]:
    with open(f_name, "r", encoding="utf-8") as f:
        t = f.read()
    if f_name == "src/notifiers/windows.py":
        t = re.sub(r'msg = f"\{course\}\n', 'msg = f"{course}\\n', t)
    elif f_name == "src/notifiers/telegram.py":
        pass
    with open(f_name, "w", encoding="utf-8") as f:
        f.write(t)
print("Done fixing newlines")        
