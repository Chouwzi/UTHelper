"""Black-box evidence for Python and native diagnostic boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import NamedTuple

import pytest

from diagnostics.models import DiagnosticReport


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPOSITORY_ROOT / "tests" / "helpers" / "diagnostic_crash_child.py"
TEST_PYTHONPATH = os.pathsep.join(
    (
        str(REPOSITORY_ROOT / "src"),
        str(
            REPOSITORY_ROOT
            / "extensions"
            / "flet_uth_background_sync"
            / "src"
        ),
    )
)
MAX_CAPTURED_OUTPUT_BYTES = 64 * 1024
FORBIDDEN_PAYLOAD_FRAGMENTS = (
    "student@ut.edu.vn",
    "sesskey",
    "diagnostic-token-secret",
)


def test_ci_has_bounded_private_diagnostics_job_without_sentry_configuration():
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        "utf-8"
    )
    private_job = workflow.split("  private_diagnostics:\n", 1)[1]

    assert "name: Private diagnostics" in private_job
    assert "permissions:\n      contents: read" in private_job
    assert "timeout-minutes: 10" in private_job
    assert "pytest-timeout" in private_job
    assert "--timeout=60" in private_job
    assert "SENTRY_DSN" not in private_job
    for filename in (
        "test_diagnostic_redaction.py",
        "test_diagnostic_logging.py",
        "test_diagnostic_spool.py",
        "test_diagnostic_transport.py",
        "test_diagnostic_release_config.py",
        "test_diagnostic_runtime.py",
        "test_diagnostic_subprocess.py",
        "test_windows_crash_evidence.py",
        "test_flutter_diagnostics_patch.py",
    ):
        assert filename in private_job


class _ChildResult(NamedTuple):
    returncode: int
    stdout_bytes: int
    stderr_bytes: int


def _child_environment() -> dict[str, str]:
    allowed = (
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    )
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": TEST_PYTHONPATH,
            "PYTHONUTF8": "1",
        }
    )
    return environment


def _run_child(mode: str, root: Path) -> _ChildResult:
    try:
        completed = subprocess.run(
            [sys.executable, str(HELPER), mode, str(root)],
            cwd=REPOSITORY_ROOT,
            env=_child_environment(),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run() kills and waits for the exact child it created before
        # raising TimeoutExpired. The helper never creates descendant processes.
        pytest.fail(f"diagnostic child mode {mode!r} exceeded 10 seconds")
    return _ChildResult(
        returncode=int(completed.returncode),
        stdout_bytes=len(completed.stdout.encode("utf-8")),
        stderr_bytes=len(completed.stderr.encode("utf-8")),
    )


def _assert_output_is_bounded(result: _ChildResult) -> None:
    assert result.stdout_bytes <= MAX_CAPTURED_OUTPUT_BYTES
    assert result.stderr_bytes <= MAX_CAPTURED_OUTPUT_BYTES


@pytest.mark.parametrize(
    ("mode", "expected_success"),
    (
        ("main", False),
        ("thread", True),
        ("async", False),
        ("unraisable", True),
    ),
)
def test_uncaught_child_spools_one_sanitized_report(
    tmp_path: Path,
    mode: str,
    expected_success: bool,
) -> None:
    result = _run_child(mode, tmp_path)

    returncode = result.returncode
    assert (returncode == 0) is expected_success
    _assert_output_is_bounded(result)
    pending = tmp_path / "telemetry" / "pending"
    reports = sorted(pending.glob("*.json"))
    assert len(reports) == 1
    payload = reports[0].read_text("utf-8")
    report = DiagnosticReport.model_validate_json(payload)
    assert report.exception_type == "BoundarySecretError"
    assert report.phase.value == "gui"
    assert report.unclean_previous_exit is False
    assert all(fragment not in payload.lower() for fragment in FORBIDDEN_PAYLOAD_FRAGMENTS)
    marker = json.loads(
        (tmp_path / "diagnostics" / "run-state.json").read_text("utf-8")
    )
    assert marker["clean"] is False
    assert marker["phase"] == "gui"


def test_abort_leaves_only_unclean_marker_and_fault_evidence(tmp_path: Path) -> None:
    result = _run_child("abort", tmp_path)

    assert result.returncode != 0
    _assert_output_is_bounded(result)
    marker = json.loads(
        (tmp_path / "diagnostics" / "run-state.json").read_text("utf-8")
    )
    assert marker["clean"] is False
    assert marker["phase"] == "gui"
    assert list((tmp_path / "telemetry" / "pending").glob("*.json")) == []
    fault = tmp_path / "diagnostics" / "native-fault.log"
    assert 0 < fault.stat().st_size <= 256 * 1024


def test_clean_child_removes_marker_and_creates_no_report(tmp_path: Path) -> None:
    result = _run_child("clean", tmp_path)

    assert result.returncode == 0
    _assert_output_is_bounded(result)
    assert not (tmp_path / "diagnostics" / "run-state.json").exists()
    assert list((tmp_path / "telemetry" / "pending").glob("*.json")) == []
