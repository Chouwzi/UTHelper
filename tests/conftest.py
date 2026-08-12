"""Process-wide test isolation from user-owned runtime state."""

from __future__ import annotations

import os


# Set before test modules import ``src.main`` so synthetic failures never enter
# the real user's AppData log. Direct logging tests still configure tmp_path.
os.environ.setdefault("UTH_TEST_MODE", "1")
