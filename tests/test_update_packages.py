"""Platform target, package trust, and explicit-launch tests."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from core.update_models import (
    ReleaseManifest,
    ReleasePackage,
    UpdateCandidate,
)
from platform_utils.update_packages import (
    DownloadedPackageVerifier,
    RuntimeTarget,
    detect_runtime_target,
)
from platform_utils.windows_update import (
    BURN_UPGRADE_CODE,
    MSI_UPGRADE_CODE,
    ExecutableDetails,
    MsiDetails,
    SignatureDetails,
    TRUSTED_WINDOWS_SIGNER_SHA256,
    TransientVerificationResult,
    WindowsPackageLauncher,
    WindowsPackageVerifier,
    resolve_relaunch_executable,
)


@pytest.mark.parametrize("machine,bits,architecture", [
    ("aarch64", 8, "arm64"), ("arm64-v8a", 8, "arm64"),
    ("aarch64", 4, "universal"), ("armv7l", 4, "universal"),
    ("x86_64", 8, "universal"), ("unknown", 8, "universal"),
])
def test_android_target_uses_small_apk_only_for_64_bit_arm(monkeypatch, machine, bits, architecture):
    from platform_utils import update_packages

    monkeypatch.setattr(update_packages.platform_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(update_packages.platform_utils, "IS_ANDROID", True)
    monkeypatch.setattr(update_packages.platform, "machine", lambda: machine)
    monkeypatch.setattr(update_packages.struct, "calcsize", lambda _: bits)
    assert detect_runtime_target() == RuntimeTarget("android", architecture, "sideload")


def test_compiled_windows_release_pin_is_exact_sha256_identity():
    assert TRUSTED_WINDOWS_SIGNER_SHA256 == frozenset(
        {"7E3547EE6A31325A47BE22049E238BA83CA1D90AFB8A30D053060D02678A0B3C"}
    )


def test_windows_probe_isolates_native_modules_and_passes_literal_path(
    tmp_path,
    monkeypatch,
):
    from platform_utils import windows_update

    package = tmp_path / "update [verified]; package.msi"
    package.write_bytes(b"package")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setenv("PSModulePath", r"C:\Program Files\PowerShell\7\Modules")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setattr(windows_update.subprocess, "run", fake_run)

    assert windows_update._powershell_json("probe-script", package, 10) == {
        "ok": True
    }

    assert captured["argv"][-1] == "probe-script"
    assert str(package.resolve()) not in captured["argv"]
    child_environment = captured["kwargs"]["env"]
    assert child_environment["UTHELPER_UPDATE_PACKAGE_PATH"] == str(
        package.resolve()
    )
    assert "PowerShell\\7\\Modules" not in child_environment["PSModulePath"]
    assert "WindowsPowerShell" in child_environment["PSModulePath"]
    assert "$null = $view.Execute($record)" in windows_update._MSI_SCRIPT


def _candidate(
    path: Path,
    *,
    fingerprint: str = "AB" * 32,
    signer: str = "CN=UTHelper",
    schema: int = 2,
):
    payload = path.read_bytes()
    package_type = path.suffix.lstrip(".").lower()
    channel = "msi" if package_type == "msi" else "bootstrapper"
    strategy = "launch_msi" if package_type == "msi" else "launch_bootstrapper"
    package = ReleasePackage(
        platform="windows",
        architecture="x64",
        package_type=package_type,
        install_channel=channel,
        url=(
            "https://github.com/Chouwzi/UTHelper/releases/download/"
            f"v2.2.0/{path.name}"
        ),
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        signer_identity=signer,
        certificate_fingerprint=fingerprint,
        install_strategy={"kind": strategy},
        signature_kind=(
            "self-signed-pinned" if schema == 3 else "certificate-pinned"
        ),
    )
    manifest = ReleaseManifest(
        schema_version=schema,
        release_version="2.2.0",
        minimum_supported_version="2.1.0",
        published_at=datetime(2026, 8, 4, tzinfo=UTC),
        release_notes_url=(
            "https://github.com/Chouwzi/UTHelper/releases/tag/v2.2.0"
        ),
        packages=(package,),
    )
    return UpdateCandidate(manifest, package, True, False)


def _signature(subject="CN=UTHelper", fingerprint="AB" * 32):
    return SignatureDetails("Valid", subject, fingerprint, True)


def test_windows_target_uses_registry_install_channel(monkeypatch):
    from platform_utils import windows_update

    monkeypatch.setattr(windows_update, "has_package_identity", lambda: False)
    monkeypatch.setattr(windows_update, "read_install_channel", lambda: "msi")
    monkeypatch.setattr(windows_update.platform, "machine", lambda: "AMD64")

    assert windows_update.detect_windows_runtime_target() == RuntimeTarget(
        "windows", "x64", "msi"
    )


def test_packaged_windows_target_is_msix_without_registry_guess(monkeypatch):
    from platform_utils import windows_update

    monkeypatch.setattr(windows_update, "has_package_identity", lambda: True)
    monkeypatch.setattr(
        windows_update,
        "read_install_channel",
        lambda: (_ for _ in ()).throw(AssertionError("registry must not be read")),
    )

    assert windows_update.detect_windows_runtime_target().install_channel == "msix"


def test_windows_target_falls_back_when_winreg_is_unavailable(monkeypatch):
    from core.distribution import DistributionChannel, build_distribution_profile
    from platform_utils import windows_update

    monkeypatch.setitem(sys.modules, "winreg", None)
    monkeypatch.setattr(windows_update, "has_package_identity", lambda: False)
    monkeypatch.setattr(windows_update.platform, "machine", lambda: "AMD64")

    profile = build_distribution_profile(DistributionChannel.EXE_LTS)
    assert windows_update.detect_windows_runtime_target(profile) == RuntimeTarget(
        "windows", "x64", "bootstrapper"
    )


def test_cross_platform_target_delegates_to_windows_adapter(monkeypatch):
    import platform_utils.update_packages as update_packages

    expected = RuntimeTarget("windows", "x64", "bootstrapper")
    monkeypatch.setattr(update_packages.platform_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(update_packages.platform_utils, "IS_ANDROID", False)
    monkeypatch.setattr(update_packages.platform_utils, "IS_IOS", False)
    monkeypatch.setattr(
        "platform_utils.windows_update.detect_windows_runtime_target",
        lambda: expected,
    )

    assert detect_runtime_target() == expected


def test_ios_runtime_target_is_manual_sideload(monkeypatch):
    import platform_utils.update_packages as update_packages

    monkeypatch.setattr(update_packages.platform_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(update_packages.platform_utils, "IS_ANDROID", False)
    monkeypatch.setattr(update_packages.platform_utils, "IS_IOS", True)

    assert detect_runtime_target() == RuntimeTarget("ios", "arm64", "sideload")


def test_windows_verifier_requires_chain_fingerprint_subject_and_msi_identity(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"signed-msi")
    candidate = _candidate(path)
    metadata = MsiDetails("UTHelper", "2.2.0", MSI_UPGRADE_CODE, "x64")
    verifier = WindowsPackageVerifier(
        signature_probe=lambda _path, _timeout: _signature(),
        trusted_fingerprints=frozenset({"AB" * 32}),
        msi_probe=lambda _path, _timeout: metadata,
    )

    assert verifier.verify(path, candidate).verified
    bad_subject = WindowsPackageVerifier(
        signature_probe=lambda _path, _timeout: _signature(subject="CN=Other"),
        trusted_fingerprints=frozenset({"AB" * 32}),
        msi_probe=lambda _path, _timeout: metadata,
    )
    assert not bad_subject.verify(path, candidate).verified


def test_self_signed_pinned_package_accepts_untrusted_root_with_exact_pin(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"self-signed-msi")
    candidate = _candidate(path, schema=3)
    verifier = WindowsPackageVerifier(
        signature_probe=lambda _path, _timeout: SignatureDetails(
            "UnknownError",
            "CN=UTHelper",
            "AB" * 32,
            True,
        ),
        trusted_fingerprints=frozenset({"AB" * 32}),
        msi_probe=lambda _path, _timeout: MsiDetails(
            "UTHelper",
            "2.2.0",
            MSI_UPGRADE_CODE,
            "x64",
        ),
    )

    assert verifier.verify(path, candidate).verified


@pytest.mark.parametrize("status", ["HashMismatch", "NotSigned", "NotTrusted"])
def test_self_signed_pinned_package_rejects_invalid_signature_status(
    tmp_path,
    status,
):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"self-signed-msi")
    candidate = _candidate(path, schema=3)
    verifier = WindowsPackageVerifier(
        signature_probe=lambda _path, _timeout: SignatureDetails(
            status,
            "CN=UTHelper",
            "AB" * 32,
            True,
        ),
        trusted_fingerprints=frozenset({"AB" * 32}),
        msi_probe=lambda _path, _timeout: MsiDetails(
            "UTHelper",
            "2.2.0",
            MSI_UPGRADE_CODE,
            "x64",
        ),
    )

    assert not verifier.verify(path, candidate).verified


def test_schema_two_package_still_rejects_untrusted_self_signed_root(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"legacy-msi")
    verifier = WindowsPackageVerifier(
        signature_probe=lambda _path, _timeout: SignatureDetails(
            "UnknownError",
            "CN=UTHelper",
            "AB" * 32,
            True,
        ),
        trusted_fingerprints=frozenset({"AB" * 32}),
        msi_probe=lambda _path, _timeout: MsiDetails(
            "UTHelper",
            "2.2.0",
            MSI_UPGRADE_CODE,
            "x64",
        ),
    )

    assert not verifier.verify(path, _candidate(path, schema=2)).verified


def test_tampered_manifest_and_attacker_package_cannot_redefine_windows_trust(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"attacker-msi")
    attacker = "CD" * 32
    candidate = _candidate(path, fingerprint=attacker, signer="CN=Attacker")
    verifier = WindowsPackageVerifier(
        signature_probe=lambda _path, _timeout: _signature(
            subject="CN=Attacker",
            fingerprint=attacker,
        ),
        trusted_fingerprints=frozenset({"AB" * 32}),
        msi_probe=lambda _path, _timeout: MsiDetails(
            "UTHelper", "2.2.0", MSI_UPGRADE_CODE, "x64"
        ),
    )

    assert not verifier.verify(path, candidate).verified


def test_burn_verifier_checks_version_resource_and_magic(tmp_path):
    path = tmp_path / "UTHelper-Setup-2.2.0.exe"
    path.write_bytes(b"MZ" + b"signed-burn")
    candidate = _candidate(path)
    verifier = WindowsPackageVerifier(
        signature_probe=lambda _path, _timeout: _signature(),
        trusted_fingerprints=frozenset({"AB" * 32}),
        executable_probe=lambda _path, _timeout: ExecutableDetails(
            "UTHelper", "2.2.0", BURN_UPGRADE_CODE
        ),
    )

    assert verifier.verify(path, candidate).verified


class _RunningProcess:
    def __init__(self):
        self.returncode = None

    def wait(self, timeout):
        raise subprocess.TimeoutExpired("installer", timeout)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 1

    def kill(self):
        self.returncode = 1


def test_windows_launcher_uses_msi_and_acknowledges_without_waiting_forever(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"msi")
    candidate = _candidate(path)
    created = []
    launcher = WindowsPackageLauncher(
        process_factory=lambda argv: created.append(argv) or _RunningProcess()
    )

    result = launcher.launch(path, candidate.package)

    assert result.acknowledged
    assert created == [
        ["msiexec.exe", "/i", str(path), "/passive", "/norestart"]
    ]


def test_windows_launcher_releases_successfully_acknowledged_installer(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"msi")
    process = _RunningProcess()
    launcher = WindowsPackageLauncher(process_factory=lambda _argv: process)

    assert launcher.launch(path, _candidate(path).package).acknowledged
    launcher.cancel()

    assert process.returncode is None


def test_portable_verifier_rechecks_size_hash_and_extension(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.apk"
    payload = b"PK\x03\x04apk"
    path.write_bytes(payload)
    package = ReleasePackage(
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.2.0/UTHelper-2.2.0.apk",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        signer_identity="com.uthelper.uthelper",
        certificate_fingerprint="AB" * 32,
        install_strategy={"kind": "android_package_installer"},
    )
    manifest = ReleaseManifest(
        schema_version=2,
        release_version="2.2.0",
        minimum_supported_version="2.1.0",
        published_at=datetime(2026, 8, 8, tzinfo=UTC),
        release_notes_url="https://github.com/Chouwzi/UTHelper/releases/tag/v2.2.0",
        packages=(package,),
    )
    candidate = UpdateCandidate(manifest, package, True, False)
    verifier = DownloadedPackageVerifier()

    assert verifier.verify(path, candidate).verified
    path.write_bytes(payload + b"tampered")
    assert not verifier.verify(path, candidate).verified


def test_windows_verifier_default_timeout_is_30_and_timeout_is_retryable(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"signed-msi")
    candidate = _candidate(path)

    default_verifier = WindowsPackageVerifier()
    assert default_verifier.timeout_seconds == 30.0

    def slow_probe(_path, timeout):
        raise subprocess.TimeoutExpired("powershell.exe", timeout)

    timeout_verifier = WindowsPackageVerifier(
        signature_probe=slow_probe,
        trusted_fingerprints=frozenset({"AB" * 32}),
    )
    timeout_result = timeout_verifier.verify(path, candidate)
    assert isinstance(timeout_result, TransientVerificationResult)
    assert timeout_result.verified is False
    assert timeout_result.retryable is True

    tampered = tmp_path / "UTHelper-2.2.0.msi"
    tampered.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"tampered")
    tamper_result = timeout_verifier.verify(tampered, candidate)
    assert tamper_result.verified is False
    assert getattr(tamper_result, "retryable", False) is False


def test_windows_verifier_cancels_active_popen_probe_promptly(tmp_path, monkeypatch):
    from platform_utils import windows_update

    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"signed-msi")
    candidate = _candidate(path)

    class SlowFakePopen:
        def __init__(self, *_args, **_kwargs):
            self.returncode = None
            self._killed = threading.Event()

        def communicate(self, timeout=None):
            if self._killed.wait(timeout):
                self.returncode = 1
                return ("", "")
            raise subprocess.TimeoutExpired("powershell.exe", timeout)

        def terminate(self):
            self._killed.set()

        def wait(self, timeout=None):
            self._killed.wait(timeout)
            return 1

        def kill(self):
            self._killed.set()

    monkeypatch.setattr(windows_update.subprocess, "Popen", SlowFakePopen)
    verifier = WindowsPackageVerifier(trusted_fingerprints=frozenset({"AB" * 32}))
    timer = threading.Timer(0.05, verifier.cancel)
    timer.start()
    started = time.monotonic()
    try:
        result = verifier.verify(path, candidate)
    finally:
        timer.cancel()

    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    assert result.verified is False
    assert getattr(result, "retryable", False) is True


@pytest.mark.parametrize(
    "exit_code,expected_ack,expected_status",
    [
        (0, True, "installer_launched"),
        (3010, True, "installer_launched"),
        (1223, False, "uac_cancelled"),
        (1602, False, "uac_cancelled"),
        (1603, False, "file_in_use"),
        (1618, False, "installer_busy"),
        (1, False, "installer_rejected"),
    ],
)
def test_windows_launcher_process_factory_maps_exit_codes(
    tmp_path,
    exit_code,
    expected_ack,
    expected_status,
):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"msi")

    class ExitingProcess:
        def __init__(self, code):
            self.returncode = code

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    launcher = WindowsPackageLauncher(process_factory=lambda _argv: ExitingProcess(exit_code))
    assert launcher.acknowledgement_seconds == 8.0
    result = launcher.launch(path, _candidate(path).package)
    assert result.acknowledged is expected_ack
    assert result.status == expected_status


def test_windows_launcher_uac_denial_cleans_handoff_dir(tmp_path, monkeypatch):
    from platform_utils import windows_update

    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"msi")
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(windows_update.sys, "platform", "win32")
    monkeypatch.setattr(windows_update, "_is_user_admin", lambda: False)
    monkeypatch.setattr(windows_update, "_shell_execute_runas", lambda *_args: 1223)

    launcher = WindowsPackageLauncher(relaunch_executable=tmp_path / "UTHelper.exe")
    result = launcher.launch(path, _candidate(path).package)

    assert result.acknowledged is False
    assert result.status == "uac_cancelled"
    handoff_root = local_app_data / "UTHelper" / "update_handoff"
    assert list(handoff_root.glob("handoff-*")) == []


def test_windows_launcher_uac_accept_writes_json_and_spawns_detached_relaunch(
    tmp_path,
    monkeypatch,
):
    from platform_utils import windows_update

    pkg_dir = tmp_path / "dir $name; 'test'"
    pkg_dir.mkdir()
    path = pkg_dir / "UTHelper 2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"msi")
    local_app_data = tmp_path / "LocalAppData"
    relaunch_exe = tmp_path / "UTHelper.exe"
    relaunch_exe.write_bytes(b"MZ")

    captured = {}
    popen_calls = []

    def fake_runas(file_path, params, cwd):
        captured["runas"] = (file_path, params, cwd)
        return 0

    def fake_popen(argv, **kwargs):
        popen_calls.append((argv, kwargs))
        return object()

    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(windows_update.sys, "platform", "win32")
    monkeypatch.setattr(windows_update, "_is_user_admin", lambda: False)
    monkeypatch.setattr(windows_update, "_shell_execute_runas", fake_runas)
    monkeypatch.setattr(windows_update.subprocess, "Popen", fake_popen)

    launcher = WindowsPackageLauncher(relaunch_executable=relaunch_exe)
    result = launcher.launch(path, _candidate(path).package)

    assert result.acknowledged is True
    assert result.status == "installer_launched"
    handoff_dirs = list((local_app_data / "UTHelper" / "update_handoff").glob("handoff-*"))
    assert len(handoff_dirs) == 1
    handoff_dir = handoff_dirs[0]
    config = json.loads((handoff_dir / "config.json").read_text(encoding="utf-8"))
    assert config["package_path"] == str(path.resolve())
    assert config["kind"] == "launch_msi"
    assert config["relaunch_path"] == str(relaunch_exe)
    install_script = (handoff_dir / "install_helper.ps1").read_text(encoding="utf-8")
    relaunch_script = (handoff_dir / "relaunch_helper.ps1").read_text(encoding="utf-8")
    assert "Get-FileHash" in install_script
    assert "System.Diagnostics.ProcessStartInfo" in install_script
    assert "3010" in relaunch_script
    assert len(popen_calls) == 1
    relaunch_argv, relaunch_kwargs = popen_calls[0]
    assert str(handoff_dir / "relaunch_helper.ps1") in relaunch_argv
    assert relaunch_kwargs["cwd"] != str(handoff_dir)
    assert "WindowsPowerShell" in relaunch_kwargs["env"]["PSModulePath"]


def test_windows_launcher_elevated_handoff_runner_receives_relaunch_path(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"msi")
    seen = []

    def runner(pkg_path, pkg, relaunch_path):
        seen.append((pkg_path, pkg.package_type, relaunch_path))
        return windows_update_result()

    def windows_update_result():
        from core.update_models import LaunchResult

        return LaunchResult(True, "installer acknowledged", status="installer_launched")

    relaunch = tmp_path / "UTHelper.exe"
    launcher = WindowsPackageLauncher(
        elevated_handoff_runner=runner,
        relaunch_executable=relaunch,
    )
    res = launcher.launch(path, _candidate(path).package)
    assert res.acknowledged is True
    assert seen == [(path.resolve(), "msi", relaunch)]


def test_resolve_relaunch_executable_normalizes_autostart_and_ignores_python(tmp_path):
    autostart = tmp_path / "UTHelperAutostart.exe"
    main_exe = tmp_path / "UTHelper.exe"
    autostart.write_bytes(b"MZ")
    main_exe.write_bytes(b"MZ")

    primary, fallback = resolve_relaunch_executable(autostart)
    assert primary == main_exe
    assert fallback is not None and fallback.name == "UTHelper.exe"

    assert resolve_relaunch_executable(Path(sys.executable)) == (None, None)
    assert resolve_relaunch_executable(tmp_path / "pythonw.exe") == (None, None)
    assert resolve_relaunch_executable(tmp_path / "pytest.exe") == (None, None)


def test_shell_execute_runas_configures_closehandle_types(monkeypatch):
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace
    from platform_utils import windows_update

    closed_handles = []

    class FakeCloseHandle:
        def __init__(self):
            self.argtypes = None
            self.restype = None

        def __call__(self, handle):
            closed_handles.append(handle)
            return 1

    fake_close = FakeCloseHandle()

    class FakeShell32:
        def ShellExecuteExW(self, sei_ref):
            sei = sei_ref._obj
            sei.hProcess = 0x1234
            return 1

    monkeypatch.setattr(
        windows_update.ctypes,
        "WinDLL",
        lambda name, **_kw: FakeShell32() if name == "shell32" else None,
        raising=False,
    )
    monkeypatch.setattr(
        windows_update.ctypes,
        "windll",
        SimpleNamespace(kernel32=SimpleNamespace(CloseHandle=fake_close)),
        raising=False,
    )

    rc = windows_update._shell_execute_runas("powershell.exe", "-File x.ps1", "C:\\Temp")
    assert rc == 0
    assert fake_close.argtypes == [wintypes.HANDLE]
    assert fake_close.restype == wintypes.BOOL
    assert closed_handles == [0x1234]
    assert ctypes is not None


def test_windows_launcher_fails_closed_when_localappdata_missing_or_relative(
    tmp_path, monkeypatch
):
    from platform_utils import windows_update

    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"msi")
    fake_temp = tmp_path / "shared_temp"
    fake_temp.mkdir()

    monkeypatch.setattr(windows_update.sys, "platform", "win32")
    monkeypatch.setattr(windows_update, "_is_user_admin", lambda: False)
    monkeypatch.setattr(windows_update, "_shell_execute_runas", lambda *_args: 0)
    monkeypatch.setenv("TEMP", str(fake_temp))
    monkeypatch.setenv("TMP", str(fake_temp))

    for bad_value in (None, "", "   ", "relative/localappdata"):
        if bad_value is None:
            monkeypatch.delenv("LOCALAPPDATA", raising=False)
        else:
            monkeypatch.setenv("LOCALAPPDATA", bad_value)
        launcher = WindowsPackageLauncher(relaunch_executable=tmp_path / "UTHelper.exe")
        result = launcher.launch(path, _candidate(path).package)
        assert result.acknowledged is False
        assert result.status == "launch_failed"
        assert not (fake_temp / "UTHelper").exists()


def test_windows_launcher_rejects_reparse_point_update_handoff_dir(
    tmp_path, monkeypatch
):
    from platform_utils import windows_update

    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"msi")
    local_app_data = tmp_path / "LocalAppData"
    handoff_parent = local_app_data / "UTHelper" / "update_handoff"
    handoff_parent.parent.mkdir(parents=True)
    handoff_parent.mkdir()

    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(windows_update.sys, "platform", "win32")
    monkeypatch.setattr(windows_update, "_is_user_admin", lambda: False)
    monkeypatch.setattr(windows_update, "_shell_execute_runas", lambda *_args: 0)
    monkeypatch.setattr(
        windows_update,
        "_is_reparse_or_symlink",
        lambda p: Path(p) == handoff_parent,
    )

    launcher = WindowsPackageLauncher(relaunch_executable=tmp_path / "UTHelper.exe")
    result = launcher.launch(path, _candidate(path).package)
    assert result.acknowledged is False
    assert result.status == "launch_failed"
    assert list(handoff_parent.glob("handoff-*")) == []


def test_windows_launcher_uses_system32_cwd_and_protected_dacl(
    tmp_path, monkeypatch
):
    import os
    from platform_utils import windows_update

    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"msi")
    local_app_data = tmp_path / "LocalAppData"
    relaunch_exe = tmp_path / "UTHelper.exe"
    relaunch_exe.write_bytes(b"MZ")

    captured = {}
    popen_calls = []

    def fake_runas(file_path, params, cwd):
        captured["runas"] = (file_path, params, cwd)
        return 0

    def fake_popen(argv, **kwargs):
        popen_calls.append((argv, kwargs))
        return object()

    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(windows_update.sys, "platform", "win32")
    monkeypatch.setattr(windows_update, "_is_user_admin", lambda: False)
    monkeypatch.setattr(windows_update, "_shell_execute_runas", fake_runas)
    monkeypatch.setattr(windows_update.subprocess, "Popen", fake_popen)

    launcher = WindowsPackageLauncher(relaunch_executable=relaunch_exe)
    result = launcher.launch(path, _candidate(path).package)

    assert result.acknowledged is True
    handoff_dirs = list((local_app_data / "UTHelper" / "update_handoff").glob("handoff-*"))
    assert len(handoff_dirs) == 1
    handoff_dir = handoff_dirs[0]

    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    system32_dir = Path(system_root) / "System32"
    expected_cwd = str(system32_dir) if system32_dir.is_dir() else system_root
    assert captured["runas"][2] == expected_cwd
    assert popen_calls[0][1]["cwd"] == expected_cwd

    if os.name == "nt":
        import win32api
        import win32con
        import win32security

        sd = win32security.GetNamedSecurityInfo(
            str(handoff_dir),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION,
        )
        control, _revision = sd.GetSecurityDescriptorControl()
        assert control & win32security.SE_DACL_PROTECTED
        dacl = sd.GetSecurityDescriptorDacl()
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
        )
        try:
            user_sid = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            )
        finally:
            win32api.CloseHandle(token)
        admins_sid = win32security.ConvertSidToStringSid(
            win32security.CreateWellKnownSid(
                win32security.WinBuiltinAdministratorsSid, None
            )
        )
        system_sid = win32security.ConvertSidToStringSid(
            win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)
        )
        ace_sids = {
            win32security.ConvertSidToStringSid(dacl.GetAce(idx)[2])
            for idx in range(dacl.GetAceCount())
        }
        assert ace_sids <= {user_sid, admins_sid, system_sid}
        assert user_sid in ace_sids
        assert system_sid in ace_sids
