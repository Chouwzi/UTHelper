"""Process-wide test isolation from user-owned runtime state."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure src and background sync extensions are on sys.path for test collection
ROOT = Path(__file__).resolve().parent.parent
_src = str(ROOT / "src")
_ext = str(ROOT / "extensions" / "flet_uth_background_sync" / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
if _ext not in sys.path:
    sys.path.insert(0, _ext)

# Set before test modules import ``src.main`` so synthetic failures never enter
# the real user's AppData log. Direct logging tests still configure tmp_path.
os.environ.setdefault("UTH_TEST_MODE", "1")
