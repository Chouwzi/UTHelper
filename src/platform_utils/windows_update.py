"""Windows update target detection, Authenticode trust, and explicit launch."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Protocol

from core.distribution import (
    DistributionChannel,
    DistributionProfile,
    resolve_distribution_channel,
)
from core.update_manifest import normalize_fingerprint
from core.update_models import (
    LaunchResult,
    ReleasePackage,
    RuntimeTarget,
    UpdateCandidate,
    VerificationResult,
)
from platform_utils.autostart import get_current_process_executable, has_package_identity


MSI_UPGRADE_CODE = "{B1EB1032-5ACD-497D-8FD2-AB760218CBE3}"
BURN_UPGRADE_CODE = "{EECFB4A5-4CCD-4D94-A0DD-D8D346F626E0}"
INSTALL_CHANNEL_KEY = r"Software\UTHelper"
TRUSTED_WINDOWS_SIGNER_SHA256: frozenset[str] = frozenset(
    {"7E3547EE6A31325A47BE22049E238BA83CA1D90AFB8A30D053060D02678A0B3C"}
)
_MSI_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_ORIGINAL_SUBPROCESS_RUN = subprocess.run


class VerificationCancelled(RuntimeError):
    """Raised when an active Windows package verification probe is cancelled."""


@dataclass(frozen=True, slots=True)
class TransientVerificationResult(VerificationResult):
    retryable: bool = True

_SIGNATURE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$packagePath = [Environment]::GetEnvironmentVariable(
  'UTHELPER_UPDATE_PACKAGE_PATH',
  'Process'
)
if ([string]::IsNullOrWhiteSpace($packagePath)) {
  throw 'Missing update package path'
}
$sig = Get-AuthenticodeSignature -LiteralPath $packagePath
$sha256 = if ($sig.SignerCertificate) {
  $sig.SignerCertificate.GetCertHashString(
    [Security.Cryptography.HashAlgorithmName]::SHA256
  ).Replace(':', '').ToUpperInvariant()
} else { '' }
[ordered]@{
  status = [string]$sig.Status
  subject = [string]$sig.SignerCertificate.Subject
  fingerprint = $sha256
  timestamped = $null -ne $sig.TimeStamperCertificate
} | ConvertTo-Json -Compress
"""

_MSI_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$packagePath = [Environment]::GetEnvironmentVariable(
  'UTHELPER_UPDATE_PACKAGE_PATH',
  'Process'
)
if ([string]::IsNullOrWhiteSpace($packagePath)) {
  throw 'Missing update package path'
}
$installer = New-Object -ComObject WindowsInstaller.Installer
$database = $installer.OpenDatabase($packagePath, 0)
function Read-Property([string]$name) {
  $view = $database.OpenView("SELECT ``Value`` FROM ``Property`` WHERE ``Property``=?")
  $record = $installer.CreateRecord(1)
  $record.StringData(1) = $name
  $null = $view.Execute($record)
  $row = $view.Fetch()
  if ($null -eq $row) { throw "Missing MSI property" }
  return [string]$row.StringData(1)
}
[ordered]@{
  product_name = Read-Property 'ProductName'
  product_version = Read-Property 'ProductVersion'
  upgrade_code = Read-Property 'UpgradeCode'
  template = [string]$database.SummaryInformation(0).Property(7)
} | ConvertTo-Json -Compress
"""

_EXE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$packagePath = [Environment]::GetEnvironmentVariable(
  'UTHELPER_UPDATE_PACKAGE_PATH',
  'Process'
)
if ([string]::IsNullOrWhiteSpace($packagePath)) {
  throw 'Missing update package path'
}
$version = (Get-Item -LiteralPath $packagePath).VersionInfo
[ordered]@{
  product_name = [string]$version.ProductName
  product_version = [string]$version.ProductVersion
  bundle_upgrade_code = ''
} | ConvertTo-Json -Compress
"""


@dataclass(frozen=True, slots=True)
class SignatureDetails:
    status: str
    subject: str
    fingerprint: str
    timestamped: bool


@dataclass(frozen=True, slots=True)
class MsiDetails:
    product_name: str
    product_version: str
    upgrade_code: str
    template: str


@dataclass(frozen=True, slots=True)
class ExecutableDetails:
    product_name: str
    product_version: str
    bundle_upgrade_code: str = ""


class _Process(Protocol):
    returncode: int | None

    def wait(self, timeout: float) -> int: ...

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


def _bounded_timeout(value: float) -> float:
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 60:
        raise ValueError("Windows update timeout must be within 60 seconds")
    return timeout


def _powershell_json(
    script: str,
    path: Path,
    timeout_seconds: float,
    *,
    cancel: threading.Event | None = None,
) -> dict:
    if cancel is not None and cancel.is_set():
        raise VerificationCancelled("Windows package verification cancelled")
    resolved_path = Path(path).resolve(strict=True)
    child_environment = os.environ.copy()
    # A packaged app can inherit PowerShell 7's PSModulePath. Windows
    # PowerShell 5.1 then imports incompatible Core modules and cannot load
    # Get-AuthenticodeSignature. Give the verifier only the native module roots.
    system_root = child_environment.get("SystemRoot", r"C:\Windows")
    native_module_root = str(
        Path(system_root)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "Modules"
    )
    child_environment["PSModulePath"] = native_module_root
    # Pass the literal path out-of-band so spaces and PowerShell metacharacters
    # can never alter the command text.
    child_environment["UTHELPER_UPDATE_PACKAGE_PATH"] = str(resolved_path)
    argv = [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        script,
    ]
    bounded_timeout = _bounded_timeout(timeout_seconds)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if cancel is None or subprocess.run is not _ORIGINAL_SUBPROCESS_RUN:
        completed = subprocess.run(
            argv,
            env=child_environment,
            capture_output=True,
            text=True,
            timeout=bounded_timeout,
            check=True,
            creationflags=creationflags,
        )
        stdout = completed.stdout
    else:
        proc = subprocess.Popen(
            argv,
            env=child_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + bounded_timeout
        stdout = ""
        stderr = ""
        while True:
            if cancel.is_set():
                _terminate_process_quietly(proc)
                raise VerificationCancelled("Windows package verification cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_quietly(proc)
                raise subprocess.TimeoutExpired(argv, bounded_timeout)
            try:
                stdout, stderr = proc.communicate(timeout=min(0.05, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode,
                argv,
                output=stdout,
                stderr=stderr,
            )
    if len(stdout) > 64 * 1024:
        raise ValueError("Windows metadata output is oversized")
    value = json.loads(stdout)
    if not isinstance(value, dict):
        raise ValueError("Windows metadata output is invalid")
    return value


def _terminate_process_quietly(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
    except OSError:
        pass
    try:
        proc.communicate(timeout=0.5)
    except (OSError, subprocess.SubprocessError):
        pass


def probe_authenticode(
    path: Path,
    timeout_seconds: float,
    *,
    cancel: threading.Event | None = None,
) -> SignatureDetails:
    value = _powershell_json(_SIGNATURE_SCRIPT, path, timeout_seconds, cancel=cancel)
    if set(value) != {"status", "subject", "fingerprint", "timestamped"}:
        raise ValueError("Authenticode output fields are invalid")
    return SignatureDetails(
        status=str(value["status"]),
        subject=str(value["subject"]),
        fingerprint=normalize_fingerprint(str(value["fingerprint"])),
        timestamped=value["timestamped"] is True,
    )


def probe_msi_details(
    path: Path,
    timeout_seconds: float,
    *,
    cancel: threading.Event | None = None,
) -> MsiDetails:
    value = _powershell_json(_MSI_SCRIPT, path, timeout_seconds, cancel=cancel)
    if set(value) != {"product_name", "product_version", "upgrade_code", "template"}:
        raise ValueError("MSI metadata output fields are invalid")
    return MsiDetails(
        product_name=str(value["product_name"]),
        product_version=str(value["product_version"]),
        upgrade_code=str(value["upgrade_code"]),
        template=str(value["template"]),
    )


def probe_executable_details(
    path: Path,
    timeout_seconds: float,
    *,
    cancel: threading.Event | None = None,
) -> ExecutableDetails:
    value = _powershell_json(_EXE_SCRIPT, path, timeout_seconds, cancel=cancel)
    if set(value) != {"product_name", "product_version", "bundle_upgrade_code"}:
        raise ValueError("executable metadata output fields are invalid")
    return ExecutableDetails(
        product_name=str(value["product_name"]),
        product_version=str(value["product_version"]),
        bundle_upgrade_code=str(value["bundle_upgrade_code"]),
    )


def read_install_channel() -> str | None:
    """Read only the machine-scoped channel marker written by the MSI."""
    try:
        import winreg
    except ImportError:
        return None

    try:
        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            INSTALL_CHANNEL_KEY,
            0,
            access,
        ) as key:
            value, value_type = winreg.QueryValueEx(key, "InstallChannel")
        if value_type != winreg.REG_SZ:
            return None
        channel = str(value).strip().lower()
        return channel if channel in {"msi", "bootstrapper"} else None
    except (FileNotFoundError, OSError):
        return None


def _architecture() -> str:
    machine = platform.machine().strip().lower()
    if machine in {"amd64", "x86_64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return "unknown"


def detect_windows_runtime_target(
    distribution: DistributionProfile | None = None,
) -> RuntimeTarget:
    try:
        has_identity = has_package_identity()
    except OSError:
        has_identity = False
    if has_identity:
        return RuntimeTarget("windows", _architecture(), "msix")
    profile = distribution or resolve_distribution_channel(
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=has_package_identity,
    )
    if profile.channel is DistributionChannel.MSSTORE:
        return RuntimeTarget("windows", _architecture(), "msix")
    return RuntimeTarget(
        "windows",
        _architecture(),
        read_install_channel() or "bootstrapper",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _same_subject(left: str, right: str) -> bool:
    def normalize(value: str) -> str:
        return " ".join(value.strip().casefold().split())

    return normalize(left) == normalize(right)


def _call_probe(probe, path: Path, timeout_seconds: float, cancel: threading.Event):
    try:
        params = inspect.signature(probe).parameters
    except (TypeError, ValueError):
        params = {}
    if "cancel" in params:
        return probe(path, timeout_seconds, cancel=cancel)
    return probe(path, timeout_seconds)


class WindowsPackageVerifier:
    def __init__(
        self,
        signature_probe: Callable[[Path, float], SignatureDetails] = probe_authenticode,
        trusted_fingerprints: frozenset[str] = TRUSTED_WINDOWS_SIGNER_SHA256,
        timeout_seconds: float = 30.0,
        msi_probe: Callable[[Path, float], MsiDetails] = probe_msi_details,
        executable_probe: Callable[[Path, float], ExecutableDetails] = (
            probe_executable_details
        ),
    ) -> None:
        self.signature_probe = signature_probe
        self.trusted_fingerprints = frozenset(
            normalize_fingerprint(value) for value in trusted_fingerprints
        )
        self.timeout_seconds = _bounded_timeout(timeout_seconds)
        self.msi_probe = msi_probe
        self.executable_probe = executable_probe
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def verify(
        self,
        path: Path,
        candidate: UpdateCandidate,
    ) -> VerificationResult:
        package = candidate.package
        candidate_path = Path(path)
        try:
            if self._cancel.is_set():
                return TransientVerificationResult(
                    False,
                    "Windows package verification cancelled",
                    retryable=True,
                )
            if (
                package.platform != "windows"
                or package.package_type not in {"msi", "exe"}
                or candidate_path.is_symlink()
                or not candidate_path.is_file()
            ):
                return VerificationResult(False, "unsupported Windows package")
            if candidate_path.suffix.lower() != f".{package.package_type}":
                return VerificationResult(False, "package extension mismatch")
            if candidate_path.stat().st_size != package.size:
                return VerificationResult(False, "package size mismatch")
            if _sha256(candidate_path) != package.sha256.lower():
                return VerificationResult(False, "package SHA-256 mismatch")
            with candidate_path.open("rb") as stream:
                magic = stream.read(8)
            if package.package_type == "msi" and magic != _MSI_MAGIC:
                return VerificationResult(False, "MSI OLE header mismatch")
            if package.package_type == "exe" and not magic.startswith(b"MZ"):
                return VerificationResult(False, "Burn executable header mismatch")

            signature = _call_probe(
                self.signature_probe,
                candidate_path,
                self.timeout_seconds,
                self._cancel,
            )
            fingerprint = normalize_fingerprint(signature.fingerprint)
            self_signed_pinned = (
                candidate.manifest.schema_version == 3
                and package.signature_kind == "self-signed-pinned"
            )
            allowed_status = signature.status == "Valid" or (
                self_signed_pinned and signature.status == "UnknownError"
            )
            if not allowed_status or not signature.timestamped:
                return VerificationResult(False, "Authenticode validation failed")
            if not _same_subject(signature.subject, package.signer_identity):
                return VerificationResult(False, "signer subject mismatch")
            if fingerprint != normalize_fingerprint(package.certificate_fingerprint):
                return VerificationResult(False, "manifest signer mismatch")
            if fingerprint not in self.trusted_fingerprints:
                return VerificationResult(False, "signer is not compiled as trusted")

            if self._cancel.is_set():
                return TransientVerificationResult(
                    False,
                    "Windows package verification cancelled",
                    retryable=True,
                )

            if package.package_type == "msi":
                details = _call_probe(
                    self.msi_probe,
                    candidate_path,
                    self.timeout_seconds,
                    self._cancel,
                )
                if (
                    details.product_name != "UTHelper"
                    or details.product_version != candidate.manifest.release_version
                    or details.upgrade_code.upper() != MSI_UPGRADE_CODE
                    or "x64" not in details.template.lower()
                ):
                    return VerificationResult(False, "MSI identity mismatch")
            else:
                details = _call_probe(
                    self.executable_probe,
                    candidate_path,
                    self.timeout_seconds,
                    self._cancel,
                )
                if (
                    details.product_name != "UTHelper"
                    or details.product_version != candidate.manifest.release_version
                ):
                    return VerificationResult(False, "Burn identity mismatch")
            return VerificationResult(True)
        except VerificationCancelled:
            return TransientVerificationResult(
                False,
                "Windows package verification cancelled",
                retryable=True,
            )
        except subprocess.TimeoutExpired:
            return TransientVerificationResult(
                False,
                "Windows package verification timed out",
                retryable=True,
            )
        except (
            OSError,
            subprocess.SubprocessError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return VerificationResult(False, "Windows package verification failed")


_INSTALL_HELPER_SCRIPT = r"""
param(
  [Parameter(Mandatory=$true)][string]$ConfigPath
)
$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$exitCode = 1
try {
  $parentPid = [int]$config.parent_pid
  $waitSeconds = [double]$config.wait_timeout_seconds
  if ($waitSeconds -le 0) { $waitSeconds = 30 }
  $deadline = (Get-Date).AddSeconds($waitSeconds)
  while ((Get-Date) -lt $deadline) {
    if (-not (Get-Process -Id $parentPid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 150
  }
  Start-Sleep -Milliseconds 300

  $packagePath = [string]$config.package_path
  $actualHash = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actualHash -ne ([string]$config.expected_sha256).ToLowerInvariant()) {
    throw 'Installer SHA-256 verification failed'
  }

  $psi = New-Object System.Diagnostics.ProcessStartInfo
  if ([string]$config.kind -eq 'launch_msi') {
    $psi.FileName = (Join-Path $env:SystemRoot 'System32\msiexec.exe')
    $psi.Arguments = '/i "' + $packagePath + '" /passive /norestart'
  } elseif ([string]$config.kind -eq 'launch_bootstrapper') {
    $psi.FileName = $packagePath
    $psi.Arguments = '/passive /norestart'
  } else {
    throw 'Unsupported installer kind'
  }
  $psi.UseShellExecute = $false
  $proc = [System.Diagnostics.Process]::Start($psi)
  $proc.WaitForExit()
  $exitCode = [int]$proc.ExitCode
} catch {
  if ($exitCode -eq 0) { $exitCode = 1 }
} finally {
  $statusPath = [string]$config.status_path
  if ($statusPath) {
    $statusTmp = $statusPath + '.tmp'
    $statusJson = @{ exit_code = $exitCode } | ConvertTo-Json -Compress
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($statusTmp, $statusJson, $utf8NoBom)
    Move-Item -LiteralPath $statusTmp -Destination $statusPath -Force
  }
}
"""

_RELAUNCH_HELPER_SCRIPT = r"""
param(
  [Parameter(Mandatory=$true)][string]$ConfigPath
)
$ErrorActionPreference = 'SilentlyContinue'
$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$statusPath = [string]$config.status_path
$deadline = (Get-Date).AddSeconds(600)
$exitCode = -1
while ((Get-Date) -lt $deadline) {
  if ($statusPath -and (Test-Path -LiteralPath $statusPath)) {
    try {
      $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
      $exitCode = [int]$status.exit_code
      break
    } catch {}
  }
  Start-Sleep -Milliseconds 300
}
$parentPid = [int]$config.parent_pid
$pidDeadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $pidDeadline) {
  if (-not (Get-Process -Id $parentPid -ErrorAction SilentlyContinue)) { break }
  Start-Sleep -Milliseconds 150
}
if ($exitCode -eq 0 -or $exitCode -eq 3010) {
  $target = [string]$config.relaunch_path
  $fallback = [string]$config.fallback_relaunch_path
  $programFilesDir = [Environment]::GetFolderPath('ProgramFiles')
  $installedPrefix = if ($programFilesDir) { Join-Path $programFilesDir 'UTHelper' } else { '' }
  if ($fallback -and (Test-Path -LiteralPath $fallback)) {
    if (
      (-not $target) -or
      ($installedPrefix -and -not $target.StartsWith($installedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) -or
      (-not (Test-Path -LiteralPath $target))
    ) {
      $target = $fallback
    }
  }
  if ($target -and (Test-Path -LiteralPath $target)) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $target
    $psi.WorkingDirectory = Split-Path -Parent $target
    $psi.UseShellExecute = $true
    [void][System.Diagnostics.Process]::Start($psi)
  }
}
Set-Location -LiteralPath $env:TEMP
$handoffDir = [string]$config.handoff_dir
if ($handoffDir -and (Test-Path -LiteralPath $handoffDir)) {
  Remove-Item -LiteralPath $handoffDir -Recurse -Force -ErrorAction SilentlyContinue
}
"""

_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SEE_MASK_FLAG_NO_UI = 0x00000400
_SW_HIDE = 0
_ERROR_CANCELLED = 1223


def _build_shellexecuteinfo_struct():
    class _SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    return _SHELLEXECUTEINFOW


def _is_user_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def _shell_execute_runas(
    file_path: str,
    parameters: str,
    working_dir: str,
) -> int:
    """Invoke ShellExecuteExW('runas'); returns 0 on success or Win32 error code."""
    sei_cls = _build_shellexecuteinfo_struct()
    sei = sei_cls()
    sei.cbSize = ctypes.sizeof(sei_cls)
    sei.fMask = _SEE_MASK_NOCLOSEPROCESS | _SEE_MASK_FLAG_NO_UI
    sei.hwnd = None
    sei.lpVerb = "runas"
    sei.lpFile = file_path
    sei.lpParameters = parameters
    sei.lpDirectory = working_dir
    sei.nShow = _SW_HIDE
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ok = shell32.ShellExecuteExW(ctypes.byref(sei))
    if not ok:
        return int(ctypes.get_last_error() or 1)
    if sei.hProcess:
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(sei.hProcess)
        except OSError:
            pass
    return 0


def resolve_relaunch_executable(
    current_exe: Path | None = None,
) -> tuple[Path | None, Path | None]:
    if current_exe is None:
        try:
            current_exe = get_current_process_executable()
        except OSError:
            return (None, None)
    exe = Path(current_exe)
    stem = exe.stem.casefold()
    if stem in {"python", "pythonw", "pytest"} or stem.startswith("python3"):
        return (None, None)
    if exe.name.casefold() == "uthelperautostart.exe":
        sibling = exe.with_name("UTHelper.exe")
        primary = sibling if (sibling.is_file() or not exe.exists()) else exe
    else:
        primary = exe
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    fallback = program_files / "UTHelper" / "UTHelper.exe"
    return (primary, fallback)


def _start_process(argv: list[str]) -> _Process:
    return subprocess.Popen(argv)


class WindowsPackageLauncher:
    def __init__(
        self,
        process_factory: Callable[[list[str]], _Process] | None = None,
        acknowledgement_seconds: float = 8.0,
        *,
        elevated_handoff_runner: (
            Callable[[Path, ReleasePackage, Path | None], LaunchResult] | None
        ) = None,
        relaunch_executable: Path | None = None,
    ) -> None:
        timeout = float(acknowledgement_seconds)
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 60:
            raise ValueError("installer acknowledgement timeout is invalid")
        self._has_custom_process_factory = process_factory is not None
        self.process_factory = process_factory or _start_process
        self.acknowledgement_seconds = timeout
        self._elevated_handoff_runner = elevated_handoff_runner
        self._relaunch_executable = (
            Path(relaunch_executable) if relaunch_executable is not None else None
        )
        self._lock = threading.Lock()
        self._process: _Process | None = None
        self._active_handoff = False

    def launch(self, path: Path, package: ReleasePackage) -> LaunchResult:
        candidate_path = Path(path)
        if (
            package.platform != "windows"
            or candidate_path.is_symlink()
            or not candidate_path.is_file()
        ):
            return LaunchResult(
                False,
                "Windows installer is unavailable",
                status="installer_unavailable",
            )
        try:
            resolved_path = candidate_path.resolve(strict=True)
        except OSError:
            return LaunchResult(
                False,
                "Windows installer is unavailable",
                status="installer_unavailable",
            )
        resolved_str = str(resolved_path)
        if any(ch in resolved_str for ch in ('"', "\r", "\n", "\x00")):
            return LaunchResult(
                False,
                "Windows installer is unavailable",
                status="installer_unavailable",
            )
        kind = package.install_strategy.get("kind")
        if package.package_type == "msi" and kind == "launch_msi":
            argv = [
                "msiexec.exe",
                "/i",
                str(candidate_path),
                "/passive",
                "/norestart",
            ]
        elif package.package_type == "exe" and kind == "launch_bootstrapper":
            argv = [str(candidate_path), "/passive", "/norestart"]
        else:
            return LaunchResult(
                False,
                "installer strategy is not allowed",
                status="strategy_not_allowed",
            )
        with self._lock:
            if self._active_handoff or (
                self._process is not None and self._process.poll() is None
            ):
                return LaunchResult(
                    False,
                    "an installer is already running",
                    status="installer_already_running",
                )
            if self._has_custom_process_factory or (
                self._elevated_handoff_runner is None and sys.platform != "win32"
            ):
                try:
                    process = self.process_factory(argv)
                    self._process = process
                except OSError:
                    return LaunchResult(
                        False,
                        "installer launch failed",
                        status="launch_failed",
                    )
                process_mode = True
            else:
                self._active_handoff = True
                process_mode = False
        if process_mode:
            try:
                code = process.wait(timeout=self.acknowledgement_seconds)
            except subprocess.TimeoutExpired:
                # The installer accepted the hand-off.  It is no longer owned by
                # the app, so a later coordinator shutdown must not terminate it.
                with self._lock:
                    if self._process is process:
                        self._process = None
                return LaunchResult(
                    True,
                    "installer acknowledged",
                    status="installer_launched",
                )
            finally:
                if process.poll() is not None:
                    with self._lock:
                        if self._process is process:
                            self._process = None
            if code in (0, 3010):
                return LaunchResult(
                    True,
                    "installer exited",
                    status="installer_launched",
                )
            if code in (1223, 1602):
                return LaunchResult(
                    False,
                    "installer cancelled by user",
                    status="uac_cancelled",
                )
            if code == 1618:
                return LaunchResult(
                    False,
                    "another Windows installation is in progress",
                    status="installer_busy",
                )
            if code == 1603:
                return LaunchResult(
                    False,
                    "installer file is in use",
                    status="file_in_use",
                )
            return LaunchResult(
                False,
                "installer rejected",
                status="installer_rejected",
            )

        try:
            if self._relaunch_executable is not None:
                primary_relaunch: Path | None = self._relaunch_executable
                fallback_relaunch: Path | None = (
                    Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
                    / "UTHelper"
                    / "UTHelper.exe"
                )
            else:
                primary_relaunch, fallback_relaunch = resolve_relaunch_executable()
            if self._elevated_handoff_runner is not None:
                return self._elevated_handoff_runner(
                    resolved_path,
                    package,
                    primary_relaunch,
                )
            return self._launch_elevated_handoff(
                resolved_path,
                package,
                kind,
                primary_relaunch,
                fallback_relaunch,
            )
        finally:
            with self._lock:
                self._active_handoff = False

    def _launch_elevated_handoff(
        self,
        resolved_path: Path,
        package: ReleasePackage,
        kind: str,
        primary_relaunch: Path | None,
        fallback_relaunch: Path | None,
    ) -> LaunchResult:
        base_dir = (
            Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
            / "UTHelper"
            / "update_handoff"
        )
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
            handoff_dir = Path(tempfile.mkdtemp(prefix="handoff-", dir=str(base_dir)))
        except OSError:
            return LaunchResult(
                False,
                "installer launch failed",
                status="launch_failed",
            )

        config_path = handoff_dir / "config.json"
        status_path = handoff_dir / "status.json"
        install_ps1 = handoff_dir / "install_helper.ps1"
        relaunch_ps1 = handoff_dir / "relaunch_helper.ps1"
        config_data = {
            "package_path": str(resolved_path),
            "expected_sha256": package.sha256.lower(),
            "kind": kind,
            "parent_pid": os.getpid(),
            "status_path": str(status_path),
            "handoff_dir": str(handoff_dir),
            "wait_timeout_seconds": 30,
            "relaunch_path": (
                str(primary_relaunch) if primary_relaunch is not None else ""
            ),
            "fallback_relaunch_path": (
                str(fallback_relaunch) if fallback_relaunch is not None else ""
            ),
        }
        try:
            config_path.write_text(json.dumps(config_data), encoding="utf-8")
            install_ps1.write_text(_INSTALL_HELPER_SCRIPT, encoding="utf-8")
            relaunch_ps1.write_text(_RELAUNCH_HELPER_SCRIPT, encoding="utf-8")
        except OSError:
            shutil.rmtree(handoff_dir, ignore_errors=True)
            return LaunchResult(
                False,
                "installer launch failed",
                status="launch_failed",
            )

        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        ps_candidate = (
            Path(system_root)
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        ps_exe = str(ps_candidate) if ps_candidate.is_file() else "powershell.exe"
        safe_cwd = tempfile.gettempdir()
        child_env = os.environ.copy()
        child_env["PSModulePath"] = str(
            Path(system_root)
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "Modules"
        )
        detached_flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
        install_args = [
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(install_ps1),
            "-ConfigPath",
            str(config_path),
        ]
        if _is_user_admin():
            try:
                subprocess.Popen(
                    [ps_exe, *install_args],
                    env=child_env,
                    cwd=safe_cwd,
                    creationflags=detached_flags,
                    close_fds=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                shutil.rmtree(handoff_dir, ignore_errors=True)
                return LaunchResult(
                    False,
                    "installer launch failed",
                    status="launch_failed",
                )
        else:
            err = _shell_execute_runas(
                ps_exe,
                subprocess.list2cmdline(install_args),
                safe_cwd,
            )
            if err == _ERROR_CANCELLED:
                shutil.rmtree(handoff_dir, ignore_errors=True)
                return LaunchResult(
                    False,
                    "installer cancelled by user",
                    status="uac_cancelled",
                )
            if err != 0:
                shutil.rmtree(handoff_dir, ignore_errors=True)
                return LaunchResult(
                    False,
                    "installer launch failed",
                    status="launch_failed",
                )

        try:
            subprocess.Popen(
                [
                    ps_exe,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(relaunch_ps1),
                    "-ConfigPath",
                    str(config_path),
                ],
                env=child_env,
                cwd=safe_cwd,
                creationflags=detached_flags,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            # Elevated install helper is already running; return acknowledged so
            # the app exits and releases file locks for the upgrade.
            pass
        return LaunchResult(
            True,
            "installer acknowledged",
            status="installer_launched",
        )

    def cancel(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                return
        except OSError:
            return


__all__ = [
    "BURN_UPGRADE_CODE",
    "ExecutableDetails",
    "INSTALL_CHANNEL_KEY",
    "MSI_UPGRADE_CODE",
    "MsiDetails",
    "SignatureDetails",
    "TRUSTED_WINDOWS_SIGNER_SHA256",
    "TransientVerificationResult",
    "VerificationCancelled",
    "WindowsPackageLauncher",
    "WindowsPackageVerifier",
    "detect_windows_runtime_target",
    "probe_authenticode",
    "read_install_channel",
    "resolve_relaunch_executable",
]
