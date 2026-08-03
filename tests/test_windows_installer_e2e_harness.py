import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_installer_harness_rejects_missing_installer_without_waiting():
    test_root = ROOT / "build" / "test-installer-harness"
    test_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=test_root) as install_dir:
        started = time.monotonic()
        result = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-File",
                str(ROOT / "scripts" / "test_windows_installer_e2e.ps1"),
                "-InstallerPath",
                str(ROOT / "build" / "missing-installer.exe"),
                "-InstallDir",
                install_dir,
                "-ObservationSeconds",
                "2",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        elapsed = time.monotonic() - started

    assert result.returncode != 0
    assert "Installer was not found" in f"{result.stdout}\n{result.stderr}"
    assert elapsed < 10


def test_installer_harness_has_bounded_install_uninstall_and_pid_cleanup():
    script = (ROOT / "scripts" / "test_windows_installer_e2e.ps1").read_text(
        encoding="utf-8"
    )

    assert "WaitForExit($TimeoutSeconds * 1000)" in script
    assert "Stop-Process -Id $process.Id" in script
    assert "test_windows_bundle_e2e.ps1" in script
    assert "verify_windows_bundle.py" in script
    assert "finally" in script
