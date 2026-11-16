"""Pure launch-context and initial-window visibility policy."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from platform_utils.autostart import (
    AUTOSTART_RUNNER_NAME,
    get_current_process_executable,
)


def is_autostart_launch(
    *, argv: Sequence[str] | None = None, executable: Path | None = None
) -> bool:
    """Recognize the packaged alias or the source-only development flag."""
    arguments = tuple(sys.argv if argv is None else argv)
    if "--autostart" in arguments[1:]:
        return True
    process_image = Path(executable or get_current_process_executable())
    return process_image.name.casefold() == AUTOSTART_RUNNER_NAME.casefold()


def should_hide_startup_window(
    *,
    autostart_launch: bool,
    start_minimized: bool,
    tray_ready: bool,
    is_mobile: bool,
) -> bool:
    """Hide only an autostart launch with a confirmed usable tray owner."""
    return (
        not is_mobile
        and autostart_launch
        and start_minimized
        and tray_ready
    )
