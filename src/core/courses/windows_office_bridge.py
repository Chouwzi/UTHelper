"""Optional local Office-to-PDF export for fixed-page previews on Windows.

Office is never required and documents never leave the device. An already open
Office session is attached cleanly without closing user documents or quitting
their Office window. A timed-out exporter is contained to its own spawned PID.
If Microsoft Office COM is unavailable or fails, an isolated headless LibreOffice
(`soffice.exe`) fallback is attempted when installed.
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional

from core.courses.office_viewer_service import is_supported_office_package

_EXPORT_LOCK = threading.Lock()
_MAX_PDF_OUTPUT_BYTES = 128 * 1024 * 1024

_EXPORT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$app = $null
$document = $null
$owned = $false
$previousLinkOption = $null
$prevAlerts = $null
$prevSec = $null
try {
    $kind = $env:UTH_OFFICE_KIND
    $progId = if ($kind -eq 'docx') { 'Word.Application' } else { 'PowerPoint.Application' }
    $processName = if ($kind -eq 'docx') { 'WINWORD' } else { 'POWERPNT' }
    $previousIds = @(Get-Process -Name $processName -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    $app = New-Object -ComObject $progId
    $initialCount = if ($kind -eq 'docx') { $app.Documents.Count } else { $app.Presentations.Count }
    $createdIds = @(Get-Process -Name $processName -ErrorAction SilentlyContinue | Where-Object { $previousIds -notcontains $_.Id } | Select-Object -ExpandProperty Id)
    $owned = ($createdIds.Count -gt 0 -and $initialCount -eq 0)
    if ($owned) {
        Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class UthOfficePid { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid); }'
        [uint32]$officePid = 0
        try { if ($null -ne $app.HWND) { [void][UthOfficePid]::GetWindowThreadProcessId([IntPtr]$app.HWND, [ref]$officePid) } } catch {}
        if ($officePid -eq 0 -and $createdIds.Count -eq 1) { $officePid = $createdIds[0] }
        if ($officePid -gt 0) { [IO.File]::WriteAllText($env:UTH_OFFICE_PID, [string]$officePid) }
    }
    try { $prevSec = $app.AutomationSecurity } catch {}
    try { $prevAlerts = $app.DisplayAlerts } catch {}
    $app.AutomationSecurity = 3
    if ($kind -eq 'docx') {
        if ($owned) { $app.Visible = $false }
        $app.DisplayAlerts = 0
        $previousLinkOption = $app.Options.UpdateLinksAtOpen
        $app.Options.UpdateLinksAtOpen = $false
        $inputName = $env:UTH_OFFICE_INPUT
        $document = $app.Documents.Open($inputName, $false, $true, $false, '', '', $false, '', '', 0, $null, $false)
        $document.ExportAsFixedFormat($env:UTH_OFFICE_OUTPUT, 17, $false)
    } else {
        $app.DisplayAlerts = 1
        $document = $app.Presentations.Open($env:UTH_OFFICE_INPUT, -1, 0, 0)
        $document.SaveAs($env:UTH_OFFICE_OUTPUT, 32)
    }
} catch { exit 3 }
finally {
    if ($null -ne $document) {
        try { if ($env:UTH_OFFICE_KIND -eq 'docx') { $document.Close(0) } else { $document.Close() } } catch {}
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($document)
    }
    if ($null -ne $app) {
        if ($null -ne $previousLinkOption) { try { $app.Options.UpdateLinksAtOpen = $previousLinkOption } catch {} }
        if ($null -ne $prevAlerts) { try { $app.DisplayAlerts = $prevAlerts } catch {} }
        if (-not $owned -and $null -ne $prevSec) { try { $app.AutomationSecurity = $prevSec } catch {} }
        if ($owned) { try { $app.Quit() } catch {} }
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app)
    }
}
"""


def _stop_owned_exporter(pid_file: Path, started_at: float, expected_name: str) -> None:
    """Terminate only the exact Office process created by this timed-out export."""
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000 | 0x0001, False, pid)
        if not handle:
            return
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(path))
            created, exited, cpu_kernel, cpu_user = (wintypes.FILETIME() for _ in range(4))
            valid_path = kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length))
            valid_time = kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(cpu_kernel), ctypes.byref(cpu_user))
            created_at = ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 10_000_000 - 11_644_473_600
            if valid_path and valid_time and Path(path.value).name.lower() == expected_name and created_at >= started_at:
                kernel.TerminateProcess(handle, 1)
                kernel.WaitForSingleObject(handle, 3000)
        finally:
            kernel.CloseHandle(handle)
    except Exception:
        # Never use a process-name-wide kill as recovery.
        pass


def _find_soffice_executable() -> Optional[str]:
    for name in ("soffice.exe", "soffice"):
        found = shutil.which(name)
        if found and Path(found).is_file():
            return found

    candidates: list[Path] = []
    for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_key)
        if base:
            candidates.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")

    try:
        import winreg

        for subkey in (
            r"SOFTWARE\LibreOffice\UNO\InstallPath",
            r"SOFTWARE\WOW6432Node\LibreOffice\UNO\InstallPath",
        ):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
                    val, _ = winreg.QueryValueEx(key, "")
                    if isinstance(val, str) and val.strip():
                        candidates.append(Path(val.strip()) / "soffice.exe")
            except OSError:
                continue
    except ImportError:
        pass

    for cand in candidates:
        try:
            if cand.is_file():
                return str(cand)
        except OSError:
            continue
    return None


def _render_via_libreoffice(
    input_path: Path, out_dir: Path, *, timeout: float
) -> Optional[bytes]:
    soffice = _find_soffice_executable()
    if soffice is None:
        return None

    profile_dir = out_dir / "lo_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_uri = profile_dir.resolve().as_uri()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    cmd = [
        soffice,
        f"-env:UserInstallation={profile_uri}",
        "--headless",
        "--norestore",
        "--invisible",
        "--nodefault",
        "--nofirststartwizard",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(input_path),
    ]
    proc: Optional[subprocess.Popen[bytes]] = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        proc.wait(timeout=max(1.0, float(timeout)))
    except subprocess.TimeoutExpired:
        if proc is not None:
            pid = getattr(proc, "pid", None)
            try:
                proc.kill()
            except Exception:
                pass
            if pid:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        timeout=5,
                        check=False,
                    )
                except Exception:
                    pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        return None
    except OSError:
        return None

    if proc is None or proc.returncode != 0:
        return None
    output_pdf = out_dir / f"{input_path.stem}.pdf"
    try:
        if not output_pdf.is_file() or output_pdf.stat().st_size > _MAX_PDF_OUTPUT_BYTES:
            return None
        pdf = output_pdf.read_bytes()
    except OSError:
        return None
    return pdf if pdf.startswith(b"%PDF-") else None


def render_office_pdf(data: bytes, file_name: str, *, timeout: float = 45) -> bytes | None:
    """Use the installed Word/PowerPoint PDF exporter or LibreOffice fallback; return None if unavailable."""
    with _EXPORT_LOCK:
        return _render_office_pdf(data, file_name, timeout=timeout)


def _render_office_pdf(data: bytes, file_name: str, *, timeout: float) -> bytes | None:
    if sys.platform != "win32" or not is_supported_office_package(data, file_name):
        return None
    # Only desktop sessions can host Office COM / LibreOffice; Android/web retain offline previews.
    extension = Path(file_name).suffix.lower()
    kind = extension.lstrip(".")
    deadline = time.monotonic() + max(1.0, float(timeout))

    system_ps = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    powershell = str(system_ps) if system_ps.is_file() else shutil.which("powershell.exe")

    has_ms_office = False
    if powershell is not None:
        try:
            import winreg

            prog_id = "Word.Application" if kind == "docx" else "PowerPoint.Application"
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id):
                has_ms_office = True
        except (ImportError, OSError):
            has_ms_office = False

    with tempfile.TemporaryDirectory(prefix="uth-office-", ignore_cleanup_errors=True) as temp_dir:
        root = Path(temp_dir)
        input_path = root / f"document{extension}"
        output_path = root / "preview.pdf"
        pid_path = root / "office.pid"
        input_path.write_bytes(data)

        if has_ms_office and powershell is not None:
            env = os.environ.copy()
            env.update(
                UTH_OFFICE_KIND=kind,
                UTH_OFFICE_INPUT=str(input_path),
                UTH_OFFICE_OUTPUT=str(output_path),
                UTH_OFFICE_PID=str(pid_path),
            )
            encoded = base64.b64encode(_EXPORT_SCRIPT.encode("utf-16-le")).decode("ascii")
            started = time.time()
            try:
                result = subprocess.run(
                    [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=max(1.0, deadline - time.monotonic()),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=False,
                )
                if (
                    result.returncode == 0
                    and output_path.is_file()
                    and output_path.stat().st_size <= _MAX_PDF_OUTPUT_BYTES
                ):
                    pdf = output_path.read_bytes()
                    if pdf.startswith(b"%PDF-"):
                        return pdf
            except subprocess.TimeoutExpired:
                _stop_owned_exporter(
                    pid_path, started, "winword.exe" if kind == "docx" else "powerpnt.exe"
                )
            except OSError:
                pass

        remaining = max(10.0, deadline - time.monotonic())
        return _render_via_libreoffice(input_path, root, timeout=remaining)
