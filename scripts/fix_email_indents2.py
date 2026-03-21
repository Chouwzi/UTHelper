import os
with open("src/notifiers/email.py", "r", encoding="utf-8") as f:
    t = f.read()

# Fix the specific unindent in else block
t = t.replace("                  else:\n                    remaining = ", "                else:\n                    remaining = ")

with open("src/notifiers/email.py", "w", encoding="utf-8") as f:
    f.write(t)
print("done")
