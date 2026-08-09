import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_e2e_harness_establishes_app_readiness_before_manual_handoff():
    script = (ROOT / "scripts" / "test_windows_single_instance_e2e.ps1").read_text(
        encoding="utf-8"
    )

    readiness = script.index("App started successfully")
    manual_handoff = script.index("# 2. A manual second launch")

    assert readiness < manual_handoff


def test_e2e_harness_rejects_missing_executable_without_waiting():
    test_root = ROOT / "build" / "test-harness"
    test_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=test_root) as bundle_dir:
        result = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-File",
                str(ROOT / "scripts" / "test_windows_bundle_e2e.ps1"),
                "-BundleDir",
                bundle_dir,
                "-ObservationSeconds",
                "2",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    assert result.returncode != 0
    assert "UTHelper.exe" in f"{result.stdout}\n{result.stderr}"
