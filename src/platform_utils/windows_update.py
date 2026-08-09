"""Windows update target detection, Authenticode trust, and explicit launch."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import threading
from typing import Callable, Protocol

from core.update_manifest import normalize_fingerprint
from core.update_models import (
    LaunchResult,
    ReleasePackage,
    RuntimeTarget,
    UpdateCandidate,
    VerificationResult,
)
from platform_utils.autostart import has_package_identity


MSI_UPGRADE_CODE = "{B1EB1032-5ACD-497D-8FD2-AB760218CBE3}"
BURN_UPGRADE_CODE = "{EECFB4A5-4CCD-4D94-A0DD-D8D346F626E0}"
INSTALL_CHANNEL_KEY = r"Software\UTHelper"
TRUSTED_WINDOWS_SIGNER_SHA256: frozenset[str] = frozenset(
    {"7E3547EE6A31325A47BE22049E238BA83CA1D90AFB8A30D053060D02678A0B3C"}
)
_MSI_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")

_SIGNATURE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$sig = Get-AuthenticodeSignature -LiteralPath $args[0]
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
$installer = New-Object -ComObject WindowsInstaller.Installer
$database = $installer.OpenDatabase($args[0], 0)
function Read-Property([string]$name) {
  $view = $database.OpenView("SELECT ``Value`` FROM ``Property`` WHERE ``Property``=?")
  $record = $installer.CreateRecord(1)
  $record.StringData(1) = $name
  $view.Execute($record)
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
$version = (Get-Item -LiteralPath $args[0]).VersionInfo
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


def _powershell_json(script: str, path: Path, timeout_seconds: float) -> dict:
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
            str(Path(path).resolve()),
        ],
        capture_output=True,
        text=True,
        timeout=_bounded_timeout(timeout_seconds),
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if len(completed.stdout) > 64 * 1024:
        raise ValueError("Windows metadata output is oversized")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise ValueError("Windows metadata output is invalid")
    return value


def probe_authenticode(path: Path, timeout_seconds: float) -> SignatureDetails:
    value = _powershell_json(_SIGNATURE_SCRIPT, path, timeout_seconds)
    if set(value) != {"status", "subject", "fingerprint", "timestamped"}:
        raise ValueError("Authenticode output fields are invalid")
    return SignatureDetails(
        status=str(value["status"]),
        subject=str(value["subject"]),
        fingerprint=normalize_fingerprint(str(value["fingerprint"])),
        timestamped=value["timestamped"] is True,
    )


def probe_msi_details(path: Path, timeout_seconds: float) -> MsiDetails:
    value = _powershell_json(_MSI_SCRIPT, path, timeout_seconds)
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
) -> ExecutableDetails:
    value = _powershell_json(_EXE_SCRIPT, path, timeout_seconds)
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


def detect_windows_runtime_target() -> RuntimeTarget:
    if has_package_identity():
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


class WindowsPackageVerifier:
    def __init__(
        self,
        signature_probe: Callable[[Path, float], SignatureDetails] = probe_authenticode,
        trusted_fingerprints: frozenset[str] = TRUSTED_WINDOWS_SIGNER_SHA256,
        timeout_seconds: float = 15.0,
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

    def verify(
        self,
        path: Path,
        candidate: UpdateCandidate,
    ) -> VerificationResult:
        package = candidate.package
        candidate_path = Path(path)
        try:
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

            signature = self.signature_probe(candidate_path, self.timeout_seconds)
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

            if package.package_type == "msi":
                details = self.msi_probe(candidate_path, self.timeout_seconds)
                if (
                    details.product_name != "UTHelper"
                    or details.product_version != candidate.manifest.release_version
                    or details.upgrade_code.upper() != MSI_UPGRADE_CODE
                    or "x64" not in details.template.lower()
                ):
                    return VerificationResult(False, "MSI identity mismatch")
            else:
                details = self.executable_probe(
                    candidate_path,
                    self.timeout_seconds,
                )
                if (
                    details.product_name != "UTHelper"
                    or details.product_version != candidate.manifest.release_version
                ):
                    return VerificationResult(False, "Burn identity mismatch")
            return VerificationResult(True)
        except (
            OSError,
            subprocess.SubprocessError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return VerificationResult(False, "Windows package verification failed")


def _start_process(argv: list[str]) -> _Process:
    return subprocess.Popen(argv)


class WindowsPackageLauncher:
    def __init__(
        self,
        process_factory: Callable[[list[str]], _Process] = _start_process,
        acknowledgement_seconds: float = 2.0,
    ) -> None:
        timeout = float(acknowledgement_seconds)
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 10:
            raise ValueError("installer acknowledgement timeout is invalid")
        self.process_factory = process_factory
        self.acknowledgement_seconds = timeout
        self._lock = threading.Lock()
        self._process: _Process | None = None

    def launch(self, path: Path, package: ReleasePackage) -> LaunchResult:
        candidate_path = Path(path)
        if package.platform != "windows" or not candidate_path.is_file():
            return LaunchResult(False, "Windows installer is unavailable")
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
            return LaunchResult(False, "installer strategy is not allowed")
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return LaunchResult(False, "an installer is already running")
            try:
                process = self.process_factory(argv)
                self._process = process
            except OSError:
                return LaunchResult(False, "installer launch failed")
        try:
            code = process.wait(timeout=self.acknowledgement_seconds)
        except subprocess.TimeoutExpired:
            # The installer accepted the hand-off.  It is no longer owned by
            # the app, so a later coordinator shutdown must not terminate it.
            with self._lock:
                if self._process is process:
                    self._process = None
            return LaunchResult(True, "installer acknowledged")
        finally:
            if process.poll() is not None:
                with self._lock:
                    if self._process is process:
                        self._process = None
        return LaunchResult(code == 0, "installer exited" if code == 0 else "installer rejected")

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
    "WindowsPackageLauncher",
    "WindowsPackageVerifier",
    "detect_windows_runtime_target",
    "probe_authenticode",
    "read_install_channel",
]
