# Windows Build Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a reproducible Flet Windows bundle that retains its compiled Python runtime, is verified before Inno/MSIX packaging, and passes isolated visible/hidden smoke launches.

**Architecture:** Replace destructive post-build deletion with Flet's supported compilation/cleanup behavior, then put a fail-closed verifier between bundle generation and every packager. A PowerShell E2E harness launches the built executable with an isolated `APPDATA`, observes its native window visibility, and terminates only the exact child PID.

**Tech Stack:** Python 3.14 Flet runtime, Flet 0.86.5, pytest, PowerShell 7, Inno Setup, Windows SDK MakeAppx.

## Global Constraints

- Do not recursively delete `*.pyc`, package metadata, Python DLLs, or runtime directories from a Flet bundle.
- A bundle may be packaged only after static verification succeeds.
- Local and release builds must resolve exactly `flet==0.86.5`.
- E2E tests must isolate `%APPDATA%`, avoid the production `UTHelper` Run value, and terminate exact process IDs in `finally`.
- Routine automation must not install over the user's current app or reboot/log out the workstation.
- Use `apply_patch` for source changes and commit each independently testable task on `feature/windows-startup-stability`.

---

## File map

- Create `scripts/verify_windows_bundle.py`: static bundle integrity checks and CLI exit code.
- Create `tests/test_windows_bundle_verifier.py`: synthetic bundle tests for every required artifact.
- Create `scripts/test_windows_bundle_e2e.ps1`: isolated native-process/window smoke harness.
- Delete `scripts/post_build_cleanup.py`: remove the unsafe recursive deletion implementation.
- Modify `scripts/build_installer.ps1`: build, verify, E2E-smoke, then invoke Inno.
- Modify `.github/workflows/release.yml`: verify the Windows bundle before signing and MSIX packaging.
- Modify `pyproject.toml`: pin Flet 0.86.5 consistently.
- Modify `scripts/UTHelper_Setup.iss`: uninstall only UTHelper's current and legacy startup values.
- Modify `tests/test_release_hardening.py`: lock release ordering, pinning, cleanup removal, and installer cleanup.
- Modify `docs/WINDOWS_EXE_PACKAGING.md`: document verified build/E2E commands and remove obsolete PyInstaller assumptions.
- Modify `REFAC_KNOWLEDGE.md`: record cause, boundary changes, and verified commands.

### Task 1: Fail-closed static bundle verifier

**Files:**
- Create: `scripts/verify_windows_bundle.py`
- Create: `tests/test_windows_bundle_verifier.py`

**Interfaces:**
- Produces: `BundleVerificationError(RuntimeError)`.
- Produces: `inspect_bundle(bundle_dir: Path) -> tuple[str, ...]`.
- Produces: `verify_bundle(bundle_dir: Path) -> None`.
- Produces: CLI `python scripts/verify_windows_bundle.py <bundle-dir>` returning 0 only for a complete bundle.

- [ ] **Step 1: Write failing synthetic-bundle tests**

```python
from pathlib import Path

import pytest

from scripts.verify_windows_bundle import BundleVerificationError, inspect_bundle, verify_bundle


def _write_valid_bundle(root: Path) -> None:
    (root / "Lib" / "encodings").mkdir(parents=True)
    (root / "app").mkdir()
    (root / "site-packages").mkdir()
    (root / "UTHelper.exe").write_bytes(b"MZ")
    (root / "python314.dll").write_bytes(b"dll")
    (root / "flutter_windows.dll").write_bytes(b"dll")
    (root / "Lib" / "encodings" / "__init__.pyc").write_bytes(b"pyc")
    (root / "app" / "main.pyc").write_bytes(b"pyc")


def test_valid_compiled_flet_bundle_has_no_issues(tmp_path):
    _write_valid_bundle(tmp_path)
    assert inspect_bundle(tmp_path) == ()
    verify_bundle(tmp_path)


@pytest.mark.parametrize(
    ("relative_path", "message"),
    [
        ("UTHelper.exe", "UTHelper.exe"),
        ("python314.dll", "Python runtime DLL"),
        ("flutter_windows.dll", "Flutter runtime DLL"),
        ("Lib/encodings/__init__.pyc", "filesystem encodings"),
        ("app/main.pyc", "compiled application entry"),
    ],
)
def test_missing_runtime_artifact_fails_closed(tmp_path, relative_path, message):
    _write_valid_bundle(tmp_path)
    (tmp_path / relative_path).unlink()
    with pytest.raises(BundleVerificationError, match=message):
        verify_bundle(tmp_path)
```

- [ ] **Step 2: Run the verifier tests and confirm the import fails**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_windows_bundle_verifier.py -q`

Expected: collection fails because `scripts.verify_windows_bundle` does not exist.

- [ ] **Step 3: Implement the minimal static verifier and CLI**

```python
class BundleVerificationError(RuntimeError):
    pass


def inspect_bundle(bundle_dir: Path) -> tuple[str, ...]:
    root = bundle_dir.resolve()
    issues: list[str] = []
    if not (root / "UTHelper.exe").is_file():
        issues.append("UTHelper.exe is missing")
    if not any(root.glob("python3*.dll")):
        issues.append("Python runtime DLL is missing")
    if not (root / "flutter_windows.dll").is_file():
        issues.append("Flutter runtime DLL is missing")
    encodings = root / "Lib" / "encodings"
    if not encodings.is_dir() or not any(encodings.glob("__init__.py*")):
        issues.append("Python filesystem encodings are missing")
    app_dir = root / "app"
    if not app_dir.is_dir() or not any(app_dir.glob("main.py*")):
        issues.append("compiled application entry is missing")
    return tuple(issues)


def verify_bundle(bundle_dir: Path) -> None:
    issues = inspect_bundle(bundle_dir)
    if issues:
        raise BundleVerificationError("; ".join(issues))
```

The CLI is complete and deterministic:

```python
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a Flet Windows bundle")
    parser.add_argument("bundle_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        verify_bundle(args.bundle_dir)
    except BundleVerificationError as exc:
        print(f"Windows bundle verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Windows bundle verified: {args.bundle_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests and CLI against the known damaged bundle**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_windows_bundle_verifier.py -q`

Expected: all tests pass.

Run: `.\.venv\Scripts\python.exe scripts\verify_windows_bundle.py build\windows`

Expected: exit 1 naming missing filesystem encodings and compiled application entry.

- [ ] **Step 5: Commit the verifier**

```powershell
git add scripts/verify_windows_bundle.py tests/test_windows_bundle_verifier.py
git commit -m "test: add Windows bundle integrity gate"
```

### Task 2: Remove destructive cleanup and pin the build toolchain

**Files:**
- Modify: `tests/test_release_hardening.py`
- Modify: `pyproject.toml`
- Modify: `scripts/build_installer.ps1`
- Modify: `.github/workflows/release.yml`
- Delete: `scripts/post_build_cleanup.py`

**Interfaces:**
- Consumes: `verify_bundle` CLI from Task 1.
- Produces: local pipeline order `flet build -> verify_windows_bundle.py -> test_windows_bundle_e2e.ps1 -> ISCC`.
- Produces: CI order `flet build -> verify_windows_bundle.py -> certificate/sign/MSIX`.

- [ ] **Step 1: Add failing release-pipeline assertions**

```python
def test_windows_build_never_deletes_compiled_python_and_verifies_before_packaging():
    config = tomllib.loads(_read("pyproject.toml"))
    assert config["project"]["dependencies"].count("flet==0.86.5") == 1

    installer = _read("scripts/build_installer.ps1")
    assert "post_build_cleanup.py" not in installer
    assert "verify_windows_bundle.py" in installer
    assert installer.index("verify_windows_bundle.py") < installer.index("ISCC.exe")
    assert not (ROOT / "scripts/post_build_cleanup.py").exists()

    workflow = _read(".github/workflows/release.yml")
    assert "verify_windows_bundle.py dist\\flet-build" in workflow
    assert workflow.index("verify_windows_bundle.py") < workflow.index("Package signed MSIX")
```

- [ ] **Step 2: Run the assertions and confirm they fail for the unsafe cleanup**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_release_hardening.py -q`

Expected: failure reports the loose Flet requirement, cleanup reference/file, and missing verifier gate.

- [ ] **Step 3: Apply the release-pipeline correction**

Set both dependency resolution paths to the exact supported release:

```toml
dependencies = [
    "flet==0.86.5",
]
```

Delete `scripts/post_build_cleanup.py`. Replace its local pipeline call with:

```powershell
Write-Host "3. Kiểm tra tính toàn vẹn bundle..." -ForegroundColor Cyan
python scripts\verify_windows_bundle.py build\windows
if ($LASTEXITCODE -ne 0) { throw "Windows bundle verification failed" }
```

Add this CI step immediately after `flet build windows`:

```yaml
      - name: Verify Flet Windows bundle
        run: python scripts\verify_windows_bundle.py dist\flet-build
```

- [ ] **Step 4: Run release tests and Ruff**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_release_hardening.py tests/test_windows_bundle_verifier.py -q`

Expected: pass.

Run: `.\.venv\Scripts\python.exe -m ruff check scripts\verify_windows_bundle.py tests\test_windows_bundle_verifier.py tests\test_release_hardening.py`

Expected: pass.

- [ ] **Step 5: Commit the safe build pipeline**

```powershell
git add pyproject.toml scripts/build_installer.ps1 scripts/post_build_cleanup.py .github/workflows/release.yml tests/test_release_hardening.py
git commit -m "fix: preserve compiled runtime in Windows builds"
```

### Task 3: Isolated native-window E2E smoke harness

**Files:**
- Create: `scripts/test_windows_bundle_e2e.ps1`
- Modify: `tests/test_release_hardening.py`
- Modify: `scripts/build_installer.ps1`

**Interfaces:**
- Consumes: a verified bundle directory containing `UTHelper.exe`.
- Produces: `test_windows_bundle_e2e.ps1 -BundleDir build\windows -ObservationSeconds 8`.
- Produces: three probes: manual-visible, autostart-visible, autostart-hidden.

- [ ] **Step 1: Add failing structural tests for E2E isolation and cleanup**

```python
def test_windows_bundle_e2e_is_isolated_and_cleans_exact_processes():
    script = _read("scripts/test_windows_bundle_e2e.ps1")
    assert "$env:APPDATA = $profileRoot" in script
    assert '"START_MINIMIZED": true' in script
    assert '"START_MINIMIZED": false' in script
    assert '"UTHelperAutostart.exe"' in script
    assert "IsWindowVisible" in script
    assert "Stop-Process -Id $process.Id" in script
    assert "finally" in script

    installer = _read("scripts/build_installer.ps1")
    assert "test_windows_bundle_e2e.ps1" in installer
    assert installer.index("verify_windows_bundle.py") < installer.index("test_windows_bundle_e2e.ps1")
```

- [ ] **Step 2: Run the structural tests and verify they fail**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_release_hardening.py -q`

Expected: failure because the E2E script and pipeline call do not exist.

- [ ] **Step 3: Implement the isolated PowerShell harness**

The script resolves `BundleDir` inside the workspace and creates a unique directory
under `build/e2e-profiles`:

```powershell
$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedBundle = (Resolve-Path -LiteralPath $BundleDir).Path
if (-not $resolvedBundle.StartsWith($workspaceRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "BundleDir must be inside the workspace"
}
$profilesRoot = Join-Path $workspaceRoot "build\e2e-profiles"
New-Item -ItemType Directory -Path $profilesRoot -Force | Out-Null
$profileRoot = Join-Path $profilesRoot ([guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path (Join-Path $profileRoot "UTHelper") -Force | Out-Null
```

Use this embedded C# helper so visibility belongs to the exact launched PID:

```csharp
using System;
using System.Runtime.InteropServices;

public static class NativeWindowProbe {
    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);

    public static bool HasVisibleWindow(uint targetProcessId) {
        bool found = false;
        EnumWindows((hwnd, lParam) => {
            GetWindowThreadProcessId(hwnd, out uint owner);
            if (owner == targetProcessId && IsWindowVisible(hwnd)) found = true;
            return !found;
        }, IntPtr.Zero);
        return found;
    }
}
```

Each probe writes this complete non-secret configuration before launch:

```json
{
  "START_WITH_WINDOWS": true,
  "START_MINIMIZED": true,
  "MINIMIZE_TO_TRAY": true,
  "CHECK_INTERVAL_MINUTES": 0
}
```

For `manual-visible`, launch without arguments and require a visible window. For
`autostart-visible`, write `START_MINIMIZED=false`, launch `UTHelperAutostart.exe`
without arguments, and require a visible window. For `autostart-hidden`, write
`START_MINIMIZED=true`, launch the same alias without arguments, and require no
visible window while the process remains alive.

The launch/cleanup core must be:

```powershell
$process = Start-Process -FilePath $exe -ArgumentList $Arguments -PassThru
try {
    Start-Sleep -Seconds $ObservationSeconds
    $process.Refresh()
    if ($process.HasExited) { throw "$Name exited with code $($process.ExitCode)" }
    $actualVisible = [NativeWindowProbe]::HasVisibleWindow([uint32]$process.Id)
    if ($actualVisible -ne $ExpectedVisible) {
        throw "$Name visibility was $actualVisible; expected $ExpectedVisible"
    }
}
finally {
    if ($process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit(5000) | Out-Null
    }
}
```

Always restore the caller's `APPDATA` and remove only the resolved unique profile
directory in an outer `finally`.

- [ ] **Step 4: Wire the E2E harness after static verification**

```powershell
& "$PSScriptRoot\test_windows_bundle_e2e.ps1" `
    -BundleDir "build\windows" -ObservationSeconds 8
if ($LASTEXITCODE -ne 0) { throw "Windows bundle E2E failed" }
```

- [ ] **Step 5: Run structural tests**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_release_hardening.py -q`

Expected: pass.

- [ ] **Step 6: Commit the E2E harness**

```powershell
git add scripts/test_windows_bundle_e2e.ps1 scripts/build_installer.ps1 tests/test_release_hardening.py
git commit -m "test: add isolated Windows bundle smoke E2E"
```

### Task 4: Installer-owned startup cleanup and operator documentation

**Files:**
- Modify: `scripts/UTHelper_Setup.iss`
- Modify: `tests/test_release_hardening.py`
- Modify: `docs/WINDOWS_EXE_PACKAGING.md`

**Interfaces:**
- Produces: Inno uninstall cleanup for Run values `UTHelper` and `UTHElearningAlert` only.
- Produces: documented build, verifier, E2E, Inno, and MSIX commands.

- [ ] **Step 1: Add failing installer-scope tests**

```python
def test_inno_uninstall_cleans_only_current_and_legacy_autostart_values():
    script = _read("scripts/UTHelper_Setup.iss")
    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    assert script.count(run_key) == 2
    assert 'ValueName: "UTHelper"' in script
    assert 'ValueName: "UTHElearningAlert"' in script
    assert script.count("uninsdeletevalue") == 2
    assert "uninsdeletekey" not in script
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_release_hardening.py::test_inno_uninstall_cleans_only_current_and_legacy_autostart_values -q`

Expected: failure because no Run-value cleanup exists.

- [ ] **Step 3: Add narrowly scoped Inno cleanup**

```ini
[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "UTHelper"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "UTHElearningAlert"; Flags: uninsdeletevalue
```

Update `docs/WINDOWS_EXE_PACKAGING.md` to use the verifier and E2E scripts, explain
why compiled `.pyc` is required by Flet 0.86.5, and distinguish Run-key Inno builds
from MSIX StartupTask builds.

- [ ] **Step 4: Run release tests and compile the Inno script when ISCC is available**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_release_hardening.py -q`

Expected: pass.

Run: `& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' scripts\UTHelper_Setup.iss`

Expected: exit 0 and a versioned setup executable under `dist`.

- [ ] **Step 5: Commit installer cleanup and documentation**

```powershell
git add scripts/UTHelper_Setup.iss tests/test_release_hardening.py docs/WINDOWS_EXE_PACKAGING.md
git commit -m "fix: verify Windows installer inputs and cleanup startup values"
```

### Task 5: Build and prove the corrected bundle

**Files:**
- Modify: `REFAC_KNOWLEDGE.md`

**Interfaces:**
- Consumes: all gates from Tasks 1-4.
- Produces: recorded commands/results for the final fresh Windows bundle and installer.

- [ ] **Step 1: Install the pinned project and run focused tests**

Run: `.\.venv\Scripts\python.exe -m pip install -e ".[windows]"`

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_windows_bundle_verifier.py tests/test_release_hardening.py -q`

Expected: pass.

- [ ] **Step 2: Remove only the known build output and perform a fresh Flet build**

Run: `flet build windows --output build\windows --verbose`

Expected: exit 0 with `build/windows/UTHelper.exe` and populated `Lib/encodings` and `app` directories.

- [ ] **Step 3: Run static and process/window E2E gates**

Run: `.\.venv\Scripts\python.exe scripts\verify_windows_bundle.py build\windows`

Run: `.\scripts\test_windows_bundle_e2e.ps1 -BundleDir build\windows -ObservationSeconds 8`

Expected: verifier passes; manual-visible, autostart-visible, and autostart-hidden pass.

- [ ] **Step 4: Build installer and MSIX package gates**

Run: `& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' scripts\UTHelper_Setup.iss`

Run: `.\scripts\package_msix.ps1 -BundleDir build\windows -Version 2.1.0.0 -Publisher 'CN=UTHelper Development' -Output build\UTHelper-test.msix`

Expected: Inno exits 0. The unsigned development MSIX exercises packing and
`makeappx validate`; release signing remains covered by the existing certificate
identity and signature gates.

- [ ] **Step 5: Record authoritative results**

Append a dated `Windows build stability` section to `REFAC_KNOWLEDGE.md` listing the
root cause, deleted cleanup script, exact test counts, verifier result, E2E modes,
Inno result, and MSIX validation result. Do not record a gate as passed unless its
command actually succeeded.

- [ ] **Step 6: Commit the verification record**

```powershell
git add REFAC_KNOWLEDGE.md
git commit -m "docs: record Windows bundle stability verification"
```
