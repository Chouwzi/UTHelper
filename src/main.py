import logging
import flet as ft
import os
from config import settings
from gui.compact_desktop import main as app_main

logging.getLogger("flet_core").setLevel(logging.INFO)
logging.getLogger("flet").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG_MODE else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

import multiprocessing

def main():
    ft.app(target=app_main, assets_dir=os.path.abspath(os.path.join(os.path.dirname(__file__), "assets")))

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()