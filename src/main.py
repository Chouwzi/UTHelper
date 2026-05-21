import logging
import os
from pathlib import Path

_APPDATA_DIR = Path(os.getenv("APPDATA", Path.home())) / "UTHElearningAlert"
_FLET_DATA_DIR = _APPDATA_DIR / "flet" / "data"
_FLET_TEMP_DIR = _APPDATA_DIR / "flet" / "temp"
_FLET_DATA_DIR.mkdir(parents=True, exist_ok=True)
_FLET_TEMP_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("FLET_APP_STORAGE_DATA", str(_FLET_DATA_DIR))
os.environ.setdefault("FLET_APP_STORAGE_TEMP", str(_FLET_TEMP_DIR))

import flet as ft
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
