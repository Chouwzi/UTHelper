import pystray
from PIL import Image, ImageDraw
import threading
from gui.app import show_dashboard
from typing import List
from models import Assignment

class TrayApp:
    def __init__(self):
        self.assignments: List[Assignment] = []
        self.icon = None

    def _create_image(self) -> Image.Image:
        # Generate a simple blank/colored icon
        image = Image.new('RGB', (64, 64), color='white')
        d = ImageDraw.Draw(image)
        d.rectangle((16, 16, 48, 48), fill="blue")
        return image

    def update_data(self, assignments: List[Assignment]):
        self.assignments = assignments

    def on_show_clicked(self, icon, item):
        # We start the Dashboard in a new thread because CustomTkinter requires its own mainloop
        # Note: running tkinter in a sub-thread can be tricky on Windows, we'll keep it simple for now
        t = threading.Thread(target=show_dashboard, args=(self.assignments,))
        t.daemon = True
        t.start()

    def on_exit_clicked(self, icon, item):
        icon.stop()

    def run(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show Deadlines", self.on_show_clicked, default=True),
            pystray.MenuItem("Exit", self.on_exit_clicked)
        )
        self.icon = pystray.Icon("uth_alert", self._create_image(), "UTH Elearning Alert", menu)
        # Blocks until exit
        self.icon.run()
