from PIL import Image
import os
base = r"E:\Projects\UTH-Elearning-Alert"
png_path = os.path.join(base, "assets", "icon.png")
ico_path = os.path.join(base, "assets", "icon.ico")
if os.path.exists(png_path):
    img = Image.open(png_path)
    img.save(ico_path, format="ICO", sizes=[(64,64)])
    print("Created icon.ico")
