import asyncio
import subprocess
import sys
import uuid

import pytest

from platform_utils.autostart import AutostartState, RunKeyAutostartBackend


pytestmark = pytest.mark.windows_integration


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Registry integration")
def test_unique_run_value_round_trip_uses_exact_argument_free_alias():
    import winreg

    suffix = uuid.uuid4().hex
    app_name = f"UTHelper_Test_{suffix}"
    legacy_name = f"UTHelper_Test_Legacy_{suffix}"
    command = subprocess.list2cmdline(
        [r"C:\Program Files\UTHelper Test\UTHelperAutostart.exe"]
    )
    backend = RunKeyAutostartBackend(
        command=command,
        app_name=app_name,
        legacy_app_name=legacy_name,
    )

    try:
        enabled = asyncio.run(backend.set_enabled(True))
        assert enabled.state is AutostartState.ENABLED
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        ) as key:
            value, value_type = winreg.QueryValueEx(key, app_name)
        assert value_type == winreg.REG_SZ
        assert value == command
        assert "--autostart" not in value

        disabled = asyncio.run(backend.set_enabled(False))
        assert disabled.state is AutostartState.DISABLED
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        ) as key:
            with pytest.raises(FileNotFoundError):
                winreg.QueryValueEx(key, app_name)
    finally:
        for name in (app_name, legacy_name):
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0,
                    winreg.KEY_SET_VALUE,
                ) as key:
                    winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
