from pathlib import Path

import pytest

from gui.controllers.startup_visibility import (
    is_autostart_launch,
    should_hide_startup_window,
)


@pytest.mark.parametrize(
    (
        "executable",
        "argv",
        "start_minimized",
        "tray_ready",
        "is_mobile",
        "expected",
    ),
    [
        ("UTHelper.exe", ["UTHelper.exe"], True, True, False, False),
        ("UTHelperAutostart.exe", ["UTHelperAutostart.exe"], False, True, False, False),
        ("UTHelperAutostart.exe", ["UTHelperAutostart.exe"], True, True, False, True),
        ("UTHelperAutostart.exe", ["UTHelperAutostart.exe"], True, False, False, False),
        ("UTHelperAutostart.exe", ["UTHelperAutostart.exe"], True, True, True, False),
        ("python.exe", ["main.py", "--autostart"], True, True, False, True),
    ],
)
def test_startup_visibility_matrix(
    executable, argv, start_minimized, tray_ready, is_mobile, expected
):
    launch = is_autostart_launch(argv=argv, executable=Path(executable))

    assert (
        should_hide_startup_window(
            autostart_launch=launch,
            start_minimized=start_minimized,
            tray_ready=tray_ready,
            is_mobile=is_mobile,
        )
        is expected
    )


def test_flag_matching_is_exact():
    assert not is_autostart_launch(
        argv=["main.py", "--autostart-debug"], executable=Path("python.exe")
    )


def test_alias_matching_is_case_insensitive():
    assert is_autostart_launch(
        argv=["anything"], executable=Path("uthelperAUTOSTART.EXE")
    )


def test_tray_setup_failure_is_explicit(monkeypatch):
    import gui.tray as tray

    monkeypatch.setattr(tray, "_load_tray_dependencies", lambda: (_ for _ in ()).throw(ImportError("missing")))

    assert tray.TrayApp().setup(ready_timeout_seconds=0.01) is False
