"""TDD remediation tests for Slice 1 mobile & platform security findings.

Covers:
- SEC-C01: PEP 730 iOS (`sys.platform == "ios"` without `sys._IOS_`) runtime detection,
  UI override isolation, native bridge activation, and secret persistence without lockout.
- SEC-C02: `EmailNotifier` `smtplib.SMTP_SSL` verified TLS context (`CERT_REQUIRED`,
  `check_hostname=True`) and app-password log redaction on failure.
- SEC-C04: Windows elevated update handoff (`handoff-<hex>`) and `update_status/*.status.json`
  post-completion cleanup without weakening `SEC-01` pre-execution sealed read-only DACL.
- SEC-C06: Android `AppSecretVault.readAll()` corrupted `AndroidKeyStore` key alias eviction
  on `GeneralSecurityException` (exception-safe via `runCatching`).
- DEMO-05: iOS `UthBackgroundSyncPlugin.swift` `configure` skips notification permission prompt
  when `enabled == false` (Demo Mode).
- DEMO-06: `AndroidBackgroundBridge.sync_now()` and `schedule_class_sessions()` defense-in-depth
  Demo Mode guards (no native sync; clears residual class alarms via `([], False)`).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
import shutil
import smtplib
import socket
import ssl
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from filelock import SoftFileLock as _PreloadedSoftFileLock
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ = _PreloadedSoftFileLock.__name__
SWIFT_PLUGIN_PATH = (
    PROJECT_ROOT
    / "extensions"
    / "flet_uth_background_sync"
    / "flutter"
    / "flet_uth_background_sync"
    / "ios"
    / "Classes"
    / "UthBackgroundSyncPlugin.swift"
)
KOTLIN_VAULT_PATH = (
    PROJECT_ROOT
    / "extensions"
    / "flet_uth_background_sync"
    / "flutter"
    / "flet_uth_background_sync"
    / "android"
    / "src"
    / "main"
    / "kotlin"
    / "com"
    / "uthelper"
    / "backgroundsync"
    / "security"
    / "CredentialVault.kt"
)


# ---------------------------------------------------------------------------
# SEC-C01: PEP 730 iOS (`sys.platform == "ios"`) Detection & Secret Persistence
# ---------------------------------------------------------------------------


def test_sec_c01_pep730_ios_runtime_detection_and_ui_override_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import platform_utils

    # 1. Direct helper contract across PEP 730 and legacy runtimes.
    detect_fn = getattr(platform_utils, "_detect_is_ios", None)
    assert callable(detect_fn), "Expected _detect_is_ios(sys_mod) helper in platform_utils"
    assert detect_fn(SimpleNamespace(platform="ios")) is True
    assert detect_fn(SimpleNamespace(platform="darwin", _IOS_=True)) is True
    assert detect_fn(SimpleNamespace(platform="darwin", _IOS_=False)) is False
    assert detect_fn(SimpleNamespace(platform="win32")) is False

    # 2. Full module reload under PEP 730 (`sys.platform == "ios"` with NO `sys._IOS_`).
    orig_platform = sys.platform
    had_ios_attr = hasattr(sys, "_IOS_")
    orig_ios_attr = getattr(sys, "_IOS_", None)
    had_android_attr = hasattr(sys, "_ANDROID_")
    orig_android_attr = getattr(sys, "_ANDROID_", None)
    try:
        monkeypatch.delenv("UTHELPER_UI_PLATFORM", raising=False)
        sys.platform = "ios"
        if hasattr(sys, "_IOS_"):
            delattr(sys, "_IOS_")
        if hasattr(sys, "_ANDROID_"):
            delattr(sys, "_ANDROID_")

        importlib.reload(platform_utils)
        assert not hasattr(sys, "_IOS_")
        assert platform_utils.IS_IOS is True
        assert platform_utils.IS_ANDROID is False
        assert platform_utils.IS_MOBILE is True
        assert platform_utils.IS_DESKTOP is False
        assert platform_utils.UI_PLATFORM == "ios"
        assert platform_utils.UI_IS_IOS is True
        assert platform_utils.UI_IS_MOBILE is True

        # Desktop UI override must change presentation ONLY without mutating IS_IOS / IS_MOBILE.
        monkeypatch.setenv("UTHELPER_UI_PLATFORM", "desktop")
        platform_utils.detect_platform(None)
        assert platform_utils.UI_PLATFORM == "desktop"
        assert platform_utils.UI_IS_DESKTOP is True
        assert platform_utils.UI_IS_IOS is False
        assert platform_utils.IS_IOS is True
        assert platform_utils.IS_MOBILE is True
        assert platform_utils.IS_DESKTOP is False
    finally:
        sys.platform = orig_platform
        if had_ios_attr:
            setattr(sys, "_IOS_", orig_ios_attr)
        elif hasattr(sys, "_IOS_"):
            delattr(sys, "_IOS_")
        if had_android_attr:
            setattr(sys, "_ANDROID_", orig_android_attr)
        elif hasattr(sys, "_ANDROID_"):
            delattr(sys, "_ANDROID_")
        monkeypatch.delenv("UTHELPER_UI_PLATFORM", raising=False)
        importlib.reload(platform_utils)

    # 3. Cold-interpreter verification: fresh Python process with sys.platform == "ios"
    #    and no sys._IOS_ BEFORE platform_utils is imported for the first time.
    cold_probe = (
        "import sys; "
        f"sys.path.insert(0, {str(PROJECT_ROOT / 'src')!r}); "
        "sys.platform = 'ios'; "
        "hasattr(sys, '_IOS_') and delattr(sys, '_IOS_'); "
        "import platform_utils; "
        "assert platform_utils.IS_IOS is True; "
        "assert platform_utils.IS_MOBILE is True; "
        "assert platform_utils.IS_DESKTOP is False; "
        "assert platform_utils.UI_PLATFORM == 'ios'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", cold_probe],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr


def test_sec_c01_pep730_ios_activates_native_bridge_and_persists_secrets_without_lockout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import config
    import flet_uth_background_sync
    import platform_utils
    from platform_utils.background_sync import create_mobile_background_bridge

    class _FakeIosKeychainService:
        def __init__(self) -> None:
            self.keychain: dict[str, str] = {}

        async def read_app_secrets(self) -> dict[str, str]:
            return dict(self.keychain)

        async def write_app_secrets(self, secrets: dict[str, str]) -> bool:
            self.keychain = dict(secrets)
            return True

        async def clear_app_secrets(self) -> bool:
            self.keychain.clear()
            return True

    service_instance = _FakeIosKeychainService()
    monkeypatch.setattr(
        flet_uth_background_sync,
        "AndroidBackgroundSync",
        lambda: service_instance,
    )
    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config.settings, "UTH_USERNAME", "student_pep730")
    monkeypatch.setattr(config.settings, "UTH_PASSWORD", "pep730-keychain-pw")
    config.register_secret_backend(None)

    orig_platform = sys.platform
    had_ios_attr = hasattr(sys, "_IOS_")
    orig_ios_attr = getattr(sys, "_IOS_", None)
    had_android_attr = hasattr(sys, "_ANDROID_")
    orig_android_attr = getattr(sys, "_ANDROID_", None)
    try:
        sys.platform = "ios"
        if hasattr(sys, "_IOS_"):
            delattr(sys, "_IOS_")
        if hasattr(sys, "_ANDROID_"):
            delattr(sys, "_ANDROID_")

        importlib.reload(platform_utils)
        assert config._is_real_ios() is True
        assert config._requires_native_secret_backend() is True

        page = SimpleNamespace(services=[])
        bridge = create_mobile_background_bridge(page)
        assert bridge is not None, "create_mobile_background_bridge returned None on PEP 730 iOS"
        assert bridge.available is True

        async def _run_ios_flow() -> None:
            initial = await bridge.read_app_secrets()
            assert initial == {}
            saved = await asyncio.to_thread(config.save_settings)
            assert saved is True, "save_settings() locked out on PEP 730 iOS"
            assert service_instance.keychain.get("password") == "pep730-keychain-pw"
            disk_text = (tmp_path / "settings.json").read_text(encoding="utf-8")
            assert "pep730-keychain-pw" not in disk_text

        asyncio.run(_run_ios_flow())
    finally:
        config.register_secret_backend(None)
        sys.platform = orig_platform
        if had_ios_attr:
            setattr(sys, "_IOS_", orig_ios_attr)
        elif hasattr(sys, "_IOS_"):
            delattr(sys, "_IOS_")
        if had_android_attr:
            setattr(sys, "_ANDROID_", orig_android_attr)
        elif hasattr(sys, "_ANDROID_"):
            delattr(sys, "_ANDROID_")
        importlib.reload(platform_utils)


# ---------------------------------------------------------------------------
# SEC-C02: EmailNotifier Enforces Verified TLS Context on SMTP_SSL
# ---------------------------------------------------------------------------


def test_sec_c02_email_notifier_enforces_tls_cert_and_hostname_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from notifiers import email as email_module
    from notifiers.email import EmailNotifier

    app_pw = "sec-c02-gmail-app-password"
    monkeypatch.setattr(email_module.settings, "ENABLE_GMAIL", True)
    monkeypatch.setattr(email_module.settings, "GMAIL_ADDRESS", "student@ut.edu.vn")
    monkeypatch.setattr(email_module.settings, "GMAIL_APP_PASSWORD", app_pw)

    captured_calls: list[dict[str, object]] = []

    class _RecordingSMTPSSL:
        def __init__(self, host: str = "", port: int = 0, **kwargs: object) -> None:
            self.record: dict[str, object] = {
                "host": host,
                "port": port,
                "kwargs": dict(kwargs),
                "logged_in": False,
                "sent": False,
            }
            captured_calls.append(self.record)

        def __enter__(self) -> _RecordingSMTPSSL:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def login(self, user: str, password: str) -> None:
            assert user == "student@ut.edu.vn"
            assert password == app_pw
            self.record["logged_in"] = True

        def send_message(self, _msg: object) -> None:
            self.record["sent"] = True

    monkeypatch.setattr(smtplib, "SMTP_SSL", _RecordingSMTPSSL)

    task = {
        "title": "Bài tập lớn Bảo mật",
        "course_name": "An toàn thông tin",
        "deadline": datetime.now() + timedelta(hours=5),
        "urgency": "critical",
        "type": "assignment",
    }
    assert EmailNotifier().notify([task]) is True
    assert len(captured_calls) == 1
    call = captured_calls[0]
    assert call["host"] == "smtp.gmail.com"
    assert call["port"] == 465
    kwargs = call["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("timeout") == 30

    ctx = kwargs.get("context")
    assert isinstance(ctx, ssl.SSLContext), "SMTP_SSL must receive an explicit ssl.SSLContext"
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert call["logged_in"] is True
    assert call["sent"] is True


def test_sec_c02_email_notifier_aborts_before_login_on_tls_error_and_redacts_password(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from notifiers import email as email_module
    from notifiers.email import EmailNotifier

    app_pw = "TOP_SECRET_GMAIL_APP_PW_999"
    monkeypatch.setattr(email_module.settings, "ENABLE_GMAIL", True)
    monkeypatch.setattr(email_module.settings, "GMAIL_ADDRESS", "student@ut.edu.vn")
    monkeypatch.setattr(email_module.settings, "GMAIL_APP_PASSWORD", app_pw)

    login_attempts = 0
    seen_contexts: list[object] = []

    class _FailingTlsSMTPSSL:
        def __init__(self, host: str = "", port: int = 0, **kwargs: object) -> None:
            seen_contexts.append(kwargs.get("context"))
            raise ssl.SSLCertVerificationError(
                1, f"certificate verify failed: self-signed cert (secret={app_pw})"
            )

        def login(self, *_args: object) -> None:
            nonlocal login_attempts
            login_attempts += 1

    monkeypatch.setattr(smtplib, "SMTP_SSL", _FailingTlsSMTPSSL)
    task = {
        "title": "Lab TLS",
        "course_name": "Mạng máy tính",
        "deadline": datetime.now() + timedelta(hours=3),
        "urgency": "warning",
        "type": "assignment",
    }
    with caplog.at_level(logging.ERROR):
        assert EmailNotifier().notify([task]) is False

    assert login_attempts == 0
    assert app_pw not in caplog.text
    assert len(seen_contexts) == 1
    assert isinstance(seen_contexts[0], ssl.SSLContext)
    assert seen_contexts[0].verify_mode == ssl.CERT_REQUIRED
    assert seen_contexts[0].check_hostname is True

    # Real unmocked smtplib.SMTP_SSL over loopback TCP: an untrusted / non-TLS MITM listener
    # must fail handshake before transmitting GMAIL_APP_PASSWORD.
    received_wire_bytes = bytearray()
    ready = threading.Event()

    def _rogue_listener(srv: socket.socket) -> None:
        ready.set()
        try:
            conn, _addr = srv.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(1.0)
            try:
                # Send plaintext banner / garbage instead of a valid CA-signed TLS cert
                conn.sendall(b"220 rogue.mitm ESMTP\r\n")
                chunk = conn.recv(4096)
                if chunk:
                    received_wire_bytes.extend(chunk)
            except OSError:
                pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        port = server_sock.getsockname()[1]
        listener_thread = threading.Thread(
            target=_rogue_listener, args=(server_sock,), daemon=True
        )
        listener_thread.start()
        assert ready.wait(timeout=2.0)

        monkeypatch.undo()
        monkeypatch.setattr(email_module.settings, "ENABLE_GMAIL", True)
        monkeypatch.setattr(email_module.settings, "GMAIL_ADDRESS", "student@ut.edu.vn")
        monkeypatch.setattr(email_module.settings, "GMAIL_APP_PASSWORD", app_pw)
        real_create_conn = socket.create_connection

        def _redirect_smtp(address: tuple[str, int], *args: object, **kwargs: object):
            host, _port = address
            assert host == "smtp.gmail.com"
            return real_create_conn(("127.0.0.1", port), *args, **kwargs)

        monkeypatch.setattr(socket, "create_connection", _redirect_smtp)
        assert EmailNotifier().notify([task]) is False
        listener_thread.join(timeout=2.0)

    assert app_pw.encode("utf-8") not in bytes(received_wire_bytes)


# ---------------------------------------------------------------------------
# SEC-C04: Post-Update Handoff & Status Cleanup Without Weakening SEC-01
# ---------------------------------------------------------------------------


def _make_msi_package(path: Path):
    from core.update_models import ReleasePackage

    data = path.read_bytes()
    return ReleasePackage(
        platform="windows",
        architecture="x64",
        package_type="msi",
        install_channel="msi",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.msi",
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        signer_identity="CN=UTHelper",
        certificate_fingerprint="0" * 64,
        install_strategy={"kind": "launch_msi"},
    )


def test_sec_c04_handoff_and_status_cleanup_after_completion_while_preserving_sec01_sealed_dacl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platform_utils import windows_update

    local_app_data = tmp_path / "LocalAppData"
    program_data = tmp_path / "ProgramData"
    msi_path = tmp_path / "UTHelper-2.6.0.msi"
    msi_path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"msi-payload")
    relaunch_exe = tmp_path / "UTHelper.exe"
    relaunch_exe.write_bytes(b"MZ")

    captured_runas: dict[str, str] = {}
    popen_calls: list[tuple[list[str], dict[str, object]]] = []
    real_popen = subprocess.Popen

    def _fake_runas(file_path: str, params: str, cwd: str) -> int:
        captured_runas["file"] = file_path
        captured_runas["params"] = params
        captured_runas["cwd"] = cwd
        return 0

    def _fake_popen(argv: list[str], **kwargs: object) -> object:
        popen_calls.append((list(argv), dict(kwargs)))
        return object()

    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("ProgramData", str(program_data))
    monkeypatch.setattr(windows_update.sys, "platform", "win32")
    monkeypatch.setattr(windows_update.os, "getpid", lambda: 999_999_999)
    monkeypatch.setattr(windows_update, "_is_user_admin", lambda: False)
    monkeypatch.setattr(windows_update, "_shell_execute_runas", _fake_runas)
    monkeypatch.setattr(windows_update.subprocess, "Popen", _fake_popen)

    pkg = _make_msi_package(msi_path)
    launcher = windows_update.WindowsPackageLauncher(relaunch_executable=relaunch_exe)
    result = launcher.launch(msi_path, pkg)
    assert result.acknowledged is True
    monkeypatch.setattr(windows_update.subprocess, "Popen", real_popen)

    handoff_dir = next((local_app_data / "UTHelper" / "update_handoff").glob("handoff-*"))
    config_path = handoff_dir / "config.json"
    install_ps1 = handoff_dir / "install_helper.ps1"
    relaunch_ps1 = handoff_dir / "relaunch_helper.ps1"

    try:
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        status_path = Path(config_data["status_path"])
        install_script_text = install_ps1.read_text(encoding="utf-8")

        if os.name == "nt" and shutil.which("powershell.exe"):
            import ntsecuritycon
            import win32api
            import win32security

            token = win32security.OpenProcessToken(
                win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
            )
            try:
                user_sid = win32security.GetTokenInformation(
                    token, win32security.TokenUser
                )[0]
                user_sid_str = win32security.ConvertSidToStringSid(user_sid)
            finally:
                win32api.CloseHandle(token)

            def _assert_sec01_sealed(target: Path) -> None:
                sd = win32security.GetNamedSecurityInfo(
                    str(target),
                    win32security.SE_FILE_OBJECT,
                    win32security.DACL_SECURITY_INFORMATION,
                )
                control, _rev = sd.GetSecurityDescriptorControl()
                assert (control & win32security.SE_DACL_PROTECTED) != 0
                dacl = sd.GetSecurityDescriptorDacl()
                found_user = False
                for idx in range(dacl.GetAceCount()):
                    _ace_info, mask, sid = dacl.GetAce(idx)
                    if win32security.ConvertSidToStringSid(sid) == user_sid_str:
                        found_user = True
                        assert (mask & ntsecuritycon.DELETE) == 0
                        assert (mask & ntsecuritycon.FILE_DELETE_CHILD) == 0
                        assert (mask & ntsecuritycon.FILE_WRITE_DATA) == 0
                        assert (mask & ntsecuritycon.FILE_ADD_FILE) == 0
                assert found_user is True

            # 1. Pre-execution SEC-01 invariant on handoff_dir and all handoff files.
            for item in (handoff_dir, config_path, install_ps1, relaunch_ps1):
                _assert_sec01_sealed(item)

            # 2. Launch REAL Medium-IL relaunch_helper.ps1 BEFORE status_path exists.
            assert len(popen_calls) == 1
            relaunch_argv, relaunch_kwargs = popen_calls[0]
            clean_kwargs = {
                k: v
                for k, v in relaunch_kwargs.items()
                if k in {"env", "cwd", "stdin", "stdout", "stderr"}
            }
            proc = real_popen(relaunch_argv, **clean_kwargs)
            try:
                # Give relaunch_helper.ps1 time to enter its status polling loop and prove
                # SEC-01 DACL remains sealed while waiting for elevated installer completion.
                time.sleep(0.6)
                assert proc.poll() is None, "relaunch_helper.ps1 exited before status_path appeared"
                for item in (handoff_dir, config_path, install_ps1, relaunch_ps1):
                    _assert_sec01_sealed(item)

                # 3. Create status_path with Read+Delete on the status file ONLY while locking
                #    status_parent to ReadAndExecute ONLY (no FILE_ADD_FILE / FILE_DELETE_CHILD),
                #    matching elevated _INSTALL_HELPER_SCRIPT's exact ProgramData security model.
                status_parent = status_path.parent
                status_parent.mkdir(parents=True, exist_ok=True)
                status_path.write_text(json.dumps({"exit_code": 1602}), encoding="utf-8")

                inherit_flags = (
                    win32security.CONTAINER_INHERIT_ACE | win32security.OBJECT_INHERIT_ACE
                )
                admins_sid = win32security.CreateWellKnownSid(
                    win32security.WinBuiltinAdministratorsSid, None
                )
                system_sid = win32security.CreateWellKnownSid(
                    win32security.WinLocalSystemSid, None
                )

                file_dacl = win32security.ACL()
                file_dacl.AddAccessAllowedAce(
                    win32security.ACL_REVISION, ntsecuritycon.GENERIC_ALL, admins_sid
                )
                file_dacl.AddAccessAllowedAce(
                    win32security.ACL_REVISION, ntsecuritycon.GENERIC_ALL, system_sid
                )
                file_dacl.AddAccessAllowedAce(
                    win32security.ACL_REVISION,
                    ntsecuritycon.FILE_GENERIC_READ
                    | ntsecuritycon.SYNCHRONIZE
                    | ntsecuritycon.DELETE,
                    user_sid,
                )
                win32security.SetNamedSecurityInfo(
                    str(status_path),
                    win32security.SE_FILE_OBJECT,
                    win32security.DACL_SECURITY_INFORMATION
                    | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    file_dacl,
                    None,
                )

                parent_dacl = win32security.ACL()
                parent_dacl.AddAccessAllowedAceEx(
                    win32security.ACL_REVISION,
                    inherit_flags,
                    ntsecuritycon.GENERIC_ALL,
                    admins_sid,
                )
                parent_dacl.AddAccessAllowedAceEx(
                    win32security.ACL_REVISION,
                    inherit_flags,
                    ntsecuritycon.GENERIC_ALL,
                    system_sid,
                )
                parent_dacl.AddAccessAllowedAceEx(
                    win32security.ACL_REVISION,
                    inherit_flags,
                    ntsecuritycon.FILE_GENERIC_READ | ntsecuritycon.FILE_GENERIC_EXECUTE,
                    user_sid,
                )
                win32security.SetNamedSecurityInfo(
                    str(status_parent),
                    win32security.SE_FILE_OBJECT,
                    win32security.DACL_SECURITY_INFORMATION
                    | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    parent_dacl,
                    None,
                )

                rc = proc.wait(timeout=15)
                assert rc == 0
                assert not status_path.exists(), "Orphaned *.status.json was not cleaned up"
                assert not handoff_dir.exists(), "Orphaned handoff-<hex> dir was not cleaned up"
                # Elevated install script must preserve Admin ownership of status dir while
                # granting Delete on the status file to the handoff owner (never deleting %LOCALAPPDATA%).
                assert "SetOwner($adminOwnerSid)" in install_script_text
                assert "Delete" in install_script_text
                assert "$handoffDir" not in install_script_text
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=2)
                # Restore tmp_path parent DACL so pytest cleanup can remove status_parent cleanly.
                status_parent = status_path.parent
                if status_parent.exists():
                    cleanup_dacl = win32security.ACL()
                    cleanup_dacl.AddAccessAllowedAceEx(
                        win32security.ACL_REVISION,
                        win32security.CONTAINER_INHERIT_ACE
                        | win32security.OBJECT_INHERIT_ACE,
                        ntsecuritycon.GENERIC_ALL,
                        user_sid,
                    )
                    win32security.SetNamedSecurityInfo(
                        str(status_parent),
                        win32security.SE_FILE_OBJECT,
                        win32security.DACL_SECURITY_INFORMATION,
                        None,
                        None,
                        cleanup_dacl,
                        None,
                    )
        else:
            assert (handoff_dir.stat().st_mode & 0o777) == 0o500
            for child in (config_path, install_ps1, relaunch_ps1):
                assert (child.stat().st_mode & 0o777) == 0o400
    finally:
        if handoff_dir.exists():
            windows_update._unseal_and_remove_handoff_dir(handoff_dir)


# ---------------------------------------------------------------------------
# SEC-C06: Android AppSecretVault Evicts Corrupted Keystore Alias
# ---------------------------------------------------------------------------


def test_sec_c06_android_app_secret_vault_evicts_corrupt_key_alias_on_general_security_exception() -> None:
    vault_src = KOTLIN_VAULT_PATH.read_text(encoding="utf-8")
    app_vault_block = vault_src.split("class AppSecretVault", 1)[1]
    read_all_block = app_vault_block.split("fun readAll(): Map<String, String>", 1)[1].split(
        "fun writeAll(", 1
    )[0]

    # Transient I/O failure must still rethrow without deleting file or key alias.
    io_branch = read_all_block.split("catch (ioError: IOException)", 1)[1].split(
        "catch (_: GeneralSecurityException)", 1
    )[0]
    assert "throw ioError" in io_branch
    assert "deleteKey" not in io_branch

    # Permanent keystore / AEAD failure must delete corrupt file AND safely evict KEY_ALIAS.
    gse_branch = read_all_block.split("catch (_: GeneralSecurityException)", 1)[1].split(
        "catch (_: JSONException)", 1
    )[0]
    assert "secretsFile.delete()" in gse_branch
    assert "runCatching { keyProvider.deleteKey(KEY_ALIAS) }" in gse_branch
    assert gse_branch.index("secretsFile.delete()") < gse_branch.index(
        "runCatching { keyProvider.deleteKey(KEY_ALIAS) }"
    )


# ---------------------------------------------------------------------------
# DEMO-05 & DEMO-06: Mobile Demo Mode Notification & Bridge Isolation
# ---------------------------------------------------------------------------


def test_demo_05_ios_plugin_skips_notification_authorization_prompt_when_disabled() -> None:
    swift_src = SWIFT_PLUGIN_PATH.read_text(encoding="utf-8")

    # Verify balanced delimiters across the entire Swift source file.
    assert swift_src.count("{") == swift_src.count("}")
    assert swift_src.count("(") == swift_src.count(")")
    assert swift_src.count("[") == swift_src.count("]")

    configure_block = swift_src.split('case "configure":', 1)[1].split(
        'case "set_credentials":', 1
    )[0]
    assert "guard enabled else {" in configure_block
    guard_branch, auth_branch = configure_block.split(
        "center.requestAuthorization(options: [.alert, .badge, .sound])", 1
    )
    # Disabled/Demo branch must finish early with enabled=false & permission_granted=false
    # BEFORE reaching UNUserNotificationCenter.requestAuthorization.
    assert '"enabled": false' in guard_branch
    assert '"permission_granted": false' in guard_branch
    assert "return" in guard_branch
    assert '"permission_granted": granted' in auth_branch


def test_demo_06_background_bridge_blocks_sync_now_and_clears_class_alarms_in_demo_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config import settings
    from platform_utils.background_sync import AndroidBackgroundBridge

    calls: list[tuple[str, object]] = []

    class _RecordingService:
        async def sync_now(self, force: bool = False) -> str:
            calls.append(("sync_now", force))
            return "enqueued"

        async def schedule_class_sessions(
            self, sessions: list[dict[str, object]], enabled: bool
        ) -> dict[str, int]:
            calls.append(("schedule_class_sessions", (list(sessions), bool(enabled))))
            return {"desired": len(sessions), "scheduled": len(sessions) if enabled else 0, "cancelled": 2}

    bridge = AndroidBackgroundBridge.__new__(AndroidBackgroundBridge)
    bridge.service = _RecordingService()

    sample_sessions = [
        {
            "reminder_id": "rem-1",
            "subject": "An toàn thông tin",
            "scheduled_at_epoch_seconds": 1_800_000_000,
            "start_at_epoch_seconds": 1_800_003_600,
        }
    ]

    # 1. Demo Mode (including whitespace/case variants):
    #    - sync_now() returns "" without touching native channel
    #    - schedule_class_sessions() forces ([], False) so residual real alarms are cleared!
    monkeypatch.setattr(settings, "UTH_USERNAME", "  UTHelper_Demo  ")
    assert asyncio.run(bridge.sync_now(force=True)) == ""
    sched_res = asyncio.run(
        bridge.schedule_class_sessions(sample_sessions, enabled=True)
    )
    assert sched_res == {"desired": 0, "scheduled": 0, "cancelled": 2}
    assert calls == [("schedule_class_sessions", ([], False))]

    # 2. Real Student Account: both methods forward normally.
    calls.clear()
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    assert asyncio.run(bridge.sync_now(force=True)) == "enqueued"
    real_res = asyncio.run(
        bridge.schedule_class_sessions(sample_sessions, enabled=True)
    )
    assert real_res == {"desired": 1, "scheduled": 1, "cancelled": 2}
    assert calls == [
        ("sync_now", True),
        ("schedule_class_sessions", (sample_sessions, True)),
    ]
