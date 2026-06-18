import logging
import os
import sys
from pathlib import Path

__version__ = "2.1.0"

# ── Platform-aware data directories ──
# On Windows: APPDATA/UTHElearningAlert
# On Android: FLET_APP_STORAGE_DATA is set by Flet runtime
# On other: ~/.uthelper
if sys.platform == 'win32':
    _APPDATA_DIR = Path(os.getenv("APPDATA", Path.home())) / "UTHElearningAlert"
elif os.environ.get('FLET_APP_STORAGE_DATA'):
    _APPDATA_DIR = Path(os.environ['FLET_APP_STORAGE_DATA'])
else:
    _APPDATA_DIR = Path.home() / ".uthelper"

_FLET_DATA_DIR = _APPDATA_DIR / "flet" / "data"
_FLET_TEMP_DIR = _APPDATA_DIR / "flet" / "temp"
_FLET_DATA_DIR.mkdir(parents=True, exist_ok=True)
_FLET_TEMP_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("FLET_APP_STORAGE_DATA", str(_FLET_DATA_DIR))
os.environ.setdefault("FLET_APP_STORAGE_TEMP", str(_FLET_TEMP_DIR))

import flet as ft
from config import settings
from gui.compact_desktop import main as app_main

# ── Console encoding fix (Windows-only, harmless on other platforms) ──
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.getLogger("flet_core").setLevel(logging.INFO)
logging.getLogger("flet").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG_MODE else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# ── Persistent log file for production debugging ──
try:
    from logging.handlers import RotatingFileHandler
    _LOG_DIR = _APPDATA_DIR / "logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _file_handler = RotatingFileHandler(
        _LOG_DIR / "app.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.getLogger().addHandler(_file_handler)
except Exception:
    pass  # Không để log setup lỗi crash app


def main():
    # On Android, Flet bundles assets automatically and sets FLET_ASSETS_DIR
    _assets = os.environ.get("FLET_ASSETS_DIR") or os.path.abspath(
        os.path.join(os.path.dirname(__file__), "assets")
    )
    ft.app(target=app_main, assets_dir=_assets)

if __name__ == "__main__":
    # multiprocessing.freeze_support() chỉ cần cho Windows PyInstaller builds
    if sys.platform == 'win32':
        import multiprocessing
        multiprocessing.freeze_support()
    main()

