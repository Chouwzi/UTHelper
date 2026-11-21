"""Optional local Office-to-PDF export for fixed-page previews on Windows.

Office is never required and documents never leave the device. An already open
Office session is left alone. A timed-out exporter is contained to its own PID.
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

from core.courses.office_viewer_service import is_supported_office_package

_EXPORT_LOCK = threading.Lock()

_EXPORT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$app = $null
$document = $null
$owned = $false
$previousLinkOption = $null
try {
    $kind = $env:UTH_OFFICE_KIND
    $progId = if ($kind -eq 'docx') { 'Word.Application' } else { 'PowerPoint.Application' }
    $processName = if ($kind -eq 'docx') { 'WINWORD' } else { 'POWERPNT' }
    $previousIds = @(Get-Process -Name $processName -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    try { $active = [Runtime.InteropServices.Marshal]::GetActiveObject($progId) } catch { $active = $null }
    if ($null -ne $active) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($active); exit 2 }
    $app = New-Object -ComObject $progId
    $count = if ($kind -eq 'docx') { $app.Documents.Count } else { $app.Presentations.Count }
    if ($count -ne 0) { exit 2 }
    $owned = $true
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class UthOfficePid { [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid); }'
    [uint32]$officePid = 0
    if ($null -ne $app.HWND) { [void][UthOfficePid]::GetWindowThreadProcessId([IntPtr]$app.HWND, [ref]$officePid) }
    if ($officePid -eq 0) {
        $createdIds = @(Get-Process -Name $processName -ErrorAction SilentlyContinue | Where-Object { $previousIds -notcontains $_.Id } | Select-Object -ExpandProperty Id)
        if ($createdIds.Count -eq 1) { $officePid = $createdIds[0] }
    }
    if ($officePid -gt 0) { [IO.File]::WriteAllText($env:UTH_OFFICE_PID, [string]$officePid) }
    $app.AutomationSecurity = 3
    if ($kind -eq 'docx') {
        $app.Visible = $false
        $app.DisplayAlerts = 0
        $previousLinkOption = $app.Options.UpdateLinksAtOpen
        $app.Options.UpdateLinksAtOpen = $false
        $inputName = $env:UTH_OFFICE_INPUT
        $document = $app.Documents.Open($inputName, $false, $true, $false)
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
        finally:
            kernel.CloseHandle(handle)
    except Exception:
        # Never use a process-name-wide kill as recovery.
        pass


def render_office_pdf(data: bytes, file_name: str, *, timeout: float = 45) -> bytes | None:
    """Use the installed Word/PowerPoint PDF exporter; return None if unavailable."""
    with _EXPORT_LOCK:
        return _render_office_pdf(data, file_name, timeout=timeout)


def _render_office_pdf(data: bytes, file_name: str, *, timeout: float) -> bytes | None:
    if sys.platform != "win32" or not is_supported_office_package(data, file_name):
        return None
    # Only desktop sessions can host Office COM; Android/web retain offline previews.
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        return None
    extension = Path(file_name).suffix.lower()
    kind = extension.lstrip(".")
    try:
        import winreg

        prog_id = "Word.Application" if kind == "docx" else "PowerPoint.Application"
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id):
            pass
    except (ImportError, OSError):
        return None
    with tempfile.TemporaryDirectory(prefix="uth-office-") as temp_dir:
        root = Path(temp_dir)
        input_path, output_path, pid_path = root / f"document{extension}", root / "preview.pdf", root / "office.pid"
        input_path.write_bytes(data)
        env = os.environ.copy()
        env.update(UTH_OFFICE_KIND=kind, UTH_OFFICE_INPUT=str(input_path), UTH_OFFICE_OUTPUT=str(output_path), UTH_OFFICE_PID=str(pid_path))
        encoded = base64.b64encode(_EXPORT_SCRIPT.encode("utf-16-le")).decode("ascii")
        started = time.time()
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
            )
            if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size > 128 * 1024 * 1024:
                return None
            pdf = output_path.read_bytes()
            return pdf if pdf.startswith(b"%PDF-") else None
        except subprocess.TimeoutExpired:
            _stop_owned_exporter(pid_path, started, "winword.exe" if kind == "docx" else "powerpnt.exe")
            return None
        except OSError:
            return None
