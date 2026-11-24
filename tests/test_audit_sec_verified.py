"""TDD regression tests for verified Slice 1 security audit findings (SEC-01..10a)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


# ---------------------------------------------------------------------------
# SEC-01: Windows Elevated Update Handoff / Stage LPE
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


def test_sec01_elevated_handoff_pins_script_sha256_and_blocks_tampered_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from platform_utils import windows_update

    # Include PowerShell metacharacters in LOCALAPPDATA to verify safe single-quote escaping.
    local_app_data = tmp_path / "dir $name; 'test'" / "LocalAppData"
    program_data = tmp_path / "ProgramData"
    msi_path = tmp_path / "UTHelper-2.6.0.msi"
    msi_path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"msi-payload")
    relaunch_exe = tmp_path / "UTHelper.exe"
    relaunch_exe.write_bytes(b"MZ")

    captured: dict[str, str] = {}

    def fake_runas(file_path: str, params: str, cwd: str) -> int:
        captured["file"] = file_path
        captured["params"] = params
        captured["cwd"] = cwd
        return 0

    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("ProgramData", str(program_data))
    monkeypatch.setattr(windows_update.sys, "platform", "win32")
    monkeypatch.setattr(windows_update, "_is_user_admin", lambda: False)
    monkeypatch.setattr(windows_update, "_shell_execute_runas", fake_runas)
    real_popen = subprocess.Popen
    monkeypatch.setattr(windows_update.subprocess, "Popen", lambda *_a, **_k: object())

    pkg = _make_msi_package(msi_path)
    launcher = windows_update.WindowsPackageLauncher(relaunch_executable=relaunch_exe)
    result = launcher.launch(msi_path, pkg)
    assert result.acknowledged is True
    monkeypatch.setattr(windows_update.subprocess, "Popen", real_popen)

    handoff_dir = next((local_app_data / "UTHelper" / "update_handoff").glob("handoff-*"))
    install_ps1 = handoff_dir / "install_helper.ps1"
    expected_script_sha256 = hashlib.sha256(install_ps1.read_bytes()).hexdigest().lower()

    params = captured["params"]
    # Must NOT directly invoke `-File <user-owned install_helper.ps1>` without inline SHA-256 verification.
    assert "-File" not in params
    assert "-Command" in params
    assert "-ExpectedScriptSha256" in params
    assert expected_script_sha256 in params
    assert "-ExpectedSha256" in params
    assert pkg.sha256.lower() in params
    assert "-ExpectedConfigSha256" in params

    if os.name == "nt":
        import ntsecuritycon
        import win32api
        import win32con
        import win32security

        # Verify Medium-IL user_sid no longer has DELETE or FILE_DELETE_CHILD on handoff_dir
        # (prevents direct directory rename-swap without WRITE_DAC).
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
        )
        try:
            user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            user_sid_str = win32security.ConvertSidToStringSid(user_sid)
        finally:
            win32api.CloseHandle(token)

        sd = win32security.GetNamedSecurityInfo(
            str(handoff_dir),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION,
        )
        dacl = sd.GetSecurityDescriptorDacl()
        for idx in range(dacl.GetAceCount()):
            _ace_info, mask, sid = dacl.GetAce(idx)
            if win32security.ConvertSidToStringSid(sid) == user_sid_str:
                assert (mask & ntsecuritycon.DELETE) == 0
                assert (mask & ntsecuritycon.FILE_DELETE_CHILD) == 0

        # Attack A: Simulate Medium-IL parent-dir rename-swap of handoff_dir
        # (possible on Windows because Medium-IL owns LOCALAPPDATA\UTHelper\update_handoff).
        # Even if handoff_dir is swapped with a malicious install_helper.ps1, the
        # UAC-pinned inline bootstrapper MUST reject it by SHA-256 and never run it.
        moved_dir = handoff_dir.with_name(handoff_dir.name + "-moved")
        canary_swap = tmp_path / "pwned_by_rename_swap.txt"
        os.rename(handoff_dir, moved_dir)
        try:
            handoff_dir.mkdir()
            (handoff_dir / "install_helper.ps1").write_text(
                f"Set-Content -LiteralPath '{canary_swap}' -Value 'PWNED_SWAP'\n",
                encoding="utf-8",
            )
            proc_swap = subprocess.run(
                f'"{captured["file"]}" {params}',
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert proc_swap.returncode != 0
            assert not canary_swap.exists(), "Rename-swapped install_helper.ps1 executed!"
            assert "SHA-256" in (proc_swap.stderr + proc_swap.stdout)
        finally:
            windows_update._unseal_and_remove_handoff_dir(handoff_dir)
            os.rename(moved_dir, handoff_dir)

        # Attack B: Simulate Medium-IL Owner WRITE_DAC takeover of install_helper.ps1.
        canary = tmp_path / "pwned_by_tampered_script.txt"
        full_dacl = win32security.ACL()
        full_dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION,
            win32con.GENERIC_ALL,
            user_sid,
        )
        win32security.SetNamedSecurityInfo(
            str(install_ps1),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION,
            None,
            None,
            full_dacl,
            None,
        )
        install_ps1.write_text(
            f"Set-Content -LiteralPath '{canary}' -Value 'PWNED'\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            f'"{captured["file"]}" {params}',
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert proc.returncode != 0
        assert not canary.exists(), "Tampered install_helper.ps1 executed!"
        assert "SHA-256" in (proc.stderr + proc.stdout)
        # Clean up sealed directory on Windows so pytest tmp_path teardown succeeds cleanly.
        windows_update._unseal_and_remove_handoff_dir(handoff_dir)


def test_sec01_install_helper_locks_programdata_owner_and_isolates_status_path(
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

    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("ProgramData", str(program_data))
    monkeypatch.setattr(windows_update.sys, "platform", "win32")
    monkeypatch.setattr(windows_update, "_is_user_admin", lambda: False)
    monkeypatch.setattr(windows_update, "_shell_execute_runas", lambda *_a: 0)
    monkeypatch.setattr(windows_update.subprocess, "Popen", lambda *_a, **_k: object())

    pkg = _make_msi_package(msi_path)
    launcher = windows_update.WindowsPackageLauncher(relaunch_executable=relaunch_exe)
    result = launcher.launch(msi_path, pkg)
    assert result.acknowledged is True

    handoff_dir = next((local_app_data / "UTHelper" / "update_handoff").glob("handoff-*"))
    try:
        config = json.loads((handoff_dir / "config.json").read_text(encoding="utf-8"))
        status_path = Path(config["status_path"])
        # Elevated install helper must NEVER write status.json into user-owned handoff_dir.
        assert handoff_dir not in status_path.parents
        assert status_path.parent == program_data / "UTHelper" / "update_status"

        script = (handoff_dir / "install_helper.ps1").read_text(encoding="utf-8")
        # Owner must be explicitly set to Administrators and verified after ACL application.
        assert "SetOwner(" in script
        assert "GetOwner(" in script
        assert "update_status" in script
        # Config must be read once into memory and hashed before ConvertFrom-Json (no TOCTOU).
        assert "ComputeHash($cfgBytes)" in script or "$cfgBytes" in script
    finally:
        windows_update._unseal_and_remove_handoff_dir(handoff_dir)


# ---------------------------------------------------------------------------
# SEC-02, SEC-04, SEC-08, SEC-10a: MoodleClient Concurrency & Session Isolation
# ---------------------------------------------------------------------------


def test_sec02_download_session_stream_and_login_session_never_abba_deadlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    from core.client import MoodleClient
    from config import settings

    monkeypatch.setattr(settings, "UTH_USERNAME", "alice_sec04")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "pw-alice")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    client = MoodleClient("https://courses.ut.edu.vn")
    login_entered_get = threading.Event()
    allow_login_finish = threading.Event()

    def _slow_open_html(path: str, *, data=None, timeout: int = 20):
        if data is None:
            login_entered_get.set()
            # Wait long enough for _download_session_stream to reach its lock check.
            allow_login_finish.wait(timeout=1.5)
            return (
                "https://courses.ut.edu.vn/login/index.php",
                '<form><input name="logintoken" value="tok1"></form>',
            )
        return (
            "https://courses.ut.edu.vn/my/",
            '<script>M.cfg={"sesskey":"skok123"};</script>',
        )

    monkeypatch.setattr(client, "_open_html", _slow_open_html)

    class _FakeResp:
        status = 200

        def geturl(self):
            return "https://courses.ut.edu.vn/pluginfile.php/1/doc.pdf"

        def read(self, n=-1):
            if getattr(self, "_done", False):
                return b""
            self._done = True
            return b"%PDF-1.4"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _FakeOpener:
        def open(self, req, timeout=60):
            return _FakeResp()

    monkeypatch.setattr(client, "_ensure_html_opener", lambda: _FakeOpener())

    t1_res: list[bool] = []
    t2_res: list[bytes | None] = []

    def _run_login():
        t1_res.append(client.login_session(force=True))

    def _run_download():
        assert login_entered_get.wait(timeout=2.0)
        # Allow login_session to finish shortly after _download_session_stream starts.
        threading.Timer(0.1, allow_login_finish.set).start()
        t2_res.append(
            client.download_session_file("https://courses.ut.edu.vn/pluginfile.php/1/doc.pdf")
        )

    t1 = threading.Thread(target=_run_login, daemon=True)
    t2 = threading.Thread(target=_run_download, daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)

    assert not t1.is_alive(), "login_session thread deadlocked (AB-BA)"
    assert not t2.is_alive(), "_download_session_stream thread deadlocked (AB-BA)"
    assert t1_res == [True]
    assert t2_res == [b"%PDF-1.4"]


def test_sec04_login_session_in_flight_does_not_contaminate_cookie_jar_after_clear_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    from http.cookiejar import Cookie
    from core.client import MoodleClient
    from config import settings

    monkeypatch.setattr(settings, "UTH_USERNAME", "alice_sec04")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "pw-alice")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    client = MoodleClient("https://courses.ut.edu.vn")
    get_started = threading.Event()
    release_get = threading.Event()

    class _FakeResp:
        def __init__(self, url: str, body: str):
            self._url = url
            self._body = body.encode("utf-8")

        def geturl(self):
            return self._url

        def read(self, *_args):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _build_fake_opener(*handlers):
        jar = None
        for h in handlers:
            if hasattr(h, "cookiejar"):
                jar = h.cookiejar

        class _Opener:
            def open(self, req, timeout=20):
                if req.get_method() == "GET":
                    get_started.set()
                    release_get.wait(timeout=2.0)
                    return _FakeResp(
                        "https://courses.ut.edu.vn/login/index.php",
                        '<form><input name="logintoken" value="tok_a"></form>',
                    )
                if jar is not None:
                    jar.set_cookie(
                        Cookie(
                            version=0,
                            name="MoodleSession",
                            value="ALICE_SECRET_COOKIE",
                            port=None,
                            port_specified=False,
                            domain="courses.ut.edu.vn",
                            domain_specified=True,
                            domain_initial_dot=False,
                            path="/",
                            path_specified=True,
                            secure=True,
                            expires=None,
                            discard=True,
                            comment=None,
                            comment_url=None,
                            rest={},
                            rfc2109=False,
                        )
                    )
                return _FakeResp(
                    "https://courses.ut.edu.vn/my/",
                    '<script>M.cfg={"sesskey":"alice_sk"};</script>',
                )

        return _Opener()

    monkeypatch.setattr("core.client.urllib.request.build_opener", _build_fake_opener)

    out: list[bool] = []
    t = threading.Thread(target=lambda: out.append(client.login_session(force=True)))
    t.start()
    assert get_started.wait(timeout=2.0)
    # Logout / account switch occurs while GET /login/index.php is in flight
    # (before POST /login/index.php runs).
    client.clear_session()
    release_get.set()
    t.join(timeout=2.0)

    assert out == [False]
    assert client.get_session_cookies(ensure_login=False) == []
    assert client.get_sesskey() == ""


def test_sec08_remove_assignment_submission_respects_quiz_lease_and_isolates_cookie_jar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from http.cookiejar import Cookie
    from core.client import MoodleClient
    from core.quiz_player import acquire_quiz_session_lease, quiz_session_key
    from config import settings

    monkeypatch.setattr(settings, "UTH_USERNAME", "student_s8")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "pw-s8")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")

    client = MoodleClient("https://courses.ut.edu.vn")
    opened_urls: list[str] = []

    class _FakeResp:
        def __init__(self, url: str, body: str):
            self._url = url
            self._body = body.encode("utf-8")

        def geturl(self):
            return self._url

        def read(self, *_args):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _build_fake_opener(*handlers):
        jar = None
        for h in handlers:
            if hasattr(h, "cookiejar"):
                jar = h.cookiejar

        class _Opener:
            def open(self, req, timeout=20):
                opened_urls.append(req.full_url)
                if "/login/index.php" in req.full_url and req.get_method() == "GET":
                    return _FakeResp(
                        req.full_url,
                        '<form><input name="logintoken" value="lt1"></form>',
                    )
                if "/login/index.php" in req.full_url and req.get_method() == "POST":
                    if jar is not None:
                        jar.set_cookie(
                            Cookie(
                                version=0,
                                name="MoodleSession",
                                value="REMOVE_TMP_COOKIE",
                                port=None,
                                port_specified=False,
                                domain="courses.ut.edu.vn",
                                domain_specified=True,
                                domain_initial_dot=False,
                                path="/",
                                path_specified=True,
                                secure=True,
                                expires=None,
                                discard=True,
                                comment=None,
                                comment_url=None,
                                rest={},
                                rfc2109=False,
                            )
                        )
                    return _FakeResp("https://courses.ut.edu.vn/my/", "")
                if "removesubmissionconfirm" in req.full_url:
                    return _FakeResp(
                        req.full_url,
                        '<form method="post" action="/mod/assign/view.php">'
                        '<input name="id" value="123">'
                        '<input name="action" value="removesubmission">'
                        '<input name="userid" value="42">'
                        '<input name="sesskey" value="sk123">'
                        "</form>",
                    )
                return _FakeResp("https://courses.ut.edu.vn/mod/assign/view.php", "ok")

        return _Opener()

    monkeypatch.setattr("core.client.urllib.request.build_opener", _build_fake_opener)

    # 1. While an embedded quiz WebView holds a lease, remove_assignment_submission MUST NOT
    #    hit /login/index.php (which would kick the active quiz session on Moodle).
    skey = quiz_session_key(client._moodle_site, "student_s8")
    lease = acquire_quiz_session_lease(skey)
    try:
        assert client.remove_assignment_submission(123, expected_user_id=42) is False
        assert opened_urls == []
    finally:
        lease.release()

    # 2. When no quiz lease is active, remove_assignment_submission succeeds using an isolated
    #    CookieJar without polluting client._cookie_jar.
    assert client.remove_assignment_submission(123, expected_user_id=42) is True
    assert client.get_session_cookies(ensure_login=False) == []


def test_sec10a_get_user_id_never_reuses_ownerless_cached_id_for_new_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.client import MoodleClient
    from config import settings

    client = MoodleClient("https://courses.ut.edu.vn")
    calls: list[str] = []

    def _fake_ws(fn: str, **kw):
        calls.append(fn)
        return {"userid": 111 if not settings.UTH_USERNAME else 222}

    monkeypatch.setattr(client, "call_ws_api", _fake_ws)

    # First call happens while UTH_USERNAME is empty (e.g. token-only / pre-hydration).
    monkeypatch.setattr(settings, "UTH_USERNAME", "")
    assert client.get_user_id() == 111

    # Later, "bob" logs in: ownerless cached ID 111 MUST NOT be returned for "bob".
    monkeypatch.setattr(settings, "UTH_USERNAME", "bob")
    assert client.get_user_id() == 222
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# SEC-03: Silent Mobile Vault Wipe After Transient Startup Hydration Failure
# ---------------------------------------------------------------------------


def test_sec03_lazy_recovery_preserves_existing_vault_secrets_instead_of_wiping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import asyncio
    from types import SimpleNamespace
    import config
    from platform_utils.background_sync import AndroidBackgroundBridge, AndroidSecretBackend

    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config, "_is_real_android", lambda: True)
    monkeypatch.setattr(config, "_is_real_ios", lambda: False)
    # Simulate startup where read_app_secrets failed transiently, so in-memory secrets are empty.
    monkeypatch.setattr(config.settings, "UTH_USERNAME", "student_sec03")
    monkeypatch.setattr(config.settings, "UTH_PASSWORD", "")
    monkeypatch.setattr(config.settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config.settings, "GMAIL_APP_PASSWORD", "")
    monkeypatch.setattr(config.settings, "MOODLE_WS_TOKEN", "")
    config.register_secret_backend(None)

    class _PrePopulatedFlakyVault:
        def __init__(self):
            self.read_calls = 0
            self.stored = {
                "password": "existing-vault-password",
                "telegram_bot_token": "existing-tg-token",
                "gmail_app_password": "existing-gmail-pw",
            }
            self.write_calls: list[dict[str, str]] = []

        async def read_app_secrets(self):
            self.read_calls += 1
            if self.read_calls == 1:
                raise TimeoutError("transient startup read failure")
            return dict(self.stored)

        async def write_app_secrets(self, secrets: dict[str, str]):
            self.write_calls.append(dict(secrets))
            self.stored = dict(secrets)
            return True

        async def clear_app_secrets(self):
            self.stored.clear()
            return True

    vault = _PrePopulatedFlakyVault()
    bridge = AndroidBackgroundBridge(SimpleNamespace(services=[]))
    bridge.service = vault

    async def _scenario():
        with pytest.raises(TimeoutError):
            await bridge.read_app_secrets()
        assert config._secret_backend is None

        # User changes a non-secret setting (e.g. theme) and saves settings off-loop.
        saved = await asyncio.to_thread(config.save_settings)
        assert saved is True
        assert isinstance(config._secret_backend, AndroidSecretBackend)
        # Pre-existing vault secrets MUST be preserved (and hydrated into settings), NOT wiped!
        assert vault.stored["password"] == "existing-vault-password"
        assert vault.stored["telegram_bot_token"] == "existing-tg-token"
        assert vault.stored["gmail_app_password"] == "existing-gmail-pw"
        assert config.settings.UTH_PASSWORD == "existing-vault-password"
        assert config.settings.TELEGRAM_BOT_TOKEN == "existing-tg-token"
        assert config.settings.GMAIL_APP_PASSWORD == "existing-gmail-pw"

        # Subsequent explicit clear after backend is hydrated/registered MUST still work.
        config.settings.TELEGRAM_BOT_TOKEN = ""
        saved2 = await asyncio.to_thread(config.save_settings)
        assert saved2 is True
        assert vault.stored.get("telegram_bot_token", "") == ""
        assert vault.stored["password"] == "existing-vault-password"

    try:
        asyncio.run(_scenario())
    finally:
        config.register_secret_backend(None)


# ---------------------------------------------------------------------------
# SEC-05: Cross-Account Notification Cache & History Contamination Across await
# ---------------------------------------------------------------------------


def test_sec05_dispatch_and_grade_alert_abort_cache_and_history_on_namespace_switch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import asyncio
    from datetime import datetime, timedelta
    import config
    from core.grade_monitor import GradeChange
    from notifiers.manager import NotificationManager

    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(config.settings, "NOTIFY_DND_ENABLE", False)
    monkeypatch.setattr(config.settings, "NOTIFY_MILESTONES_MINUTES", [60])
    monkeypatch.setattr(config.settings, "NOTIFY_MUTED_COURSES", [])

    mgr = NotificationManager(namespace="https://courses.ut.edu.vn|alice")
    mgr.notifiers.clear()

    notify_entered = asyncio.Event()
    release_notify = asyncio.Event()

    class _SlowAsyncNotifier:
        async def notify(self, items):
            notify_entered.set()
            await release_notify.wait()
            return True

    mgr.register(_SlowAsyncNotifier())

    due_soon = (datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    alice_assignment = [
        {
            "id": "a777",
            "title": "Alice Secret HW",
            "course": "Alice Course",
            "deadline": due_soon,
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=777",
            "type": "assignment",
            "submission_status": "new",
        }
    ]

    async def _run_dispatch_race():
        task = asyncio.create_task(mgr.dispatch(alice_assignment))
        await asyncio.wait_for(notify_entered.wait(), timeout=2.0)
        # Account switches to Bob while Alice's notifier is mid-await.
        mgr.set_namespace("https://courses.ut.edu.vn|bob")
        release_notify.set()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(_run_dispatch_race())

    # Bob's cache and history MUST NOT contain Alice's assignment!
    bob_cache = mgr._load_cache()
    assert bob_cache == {}, f"Alice leaked into Bob's notification cache: {bob_cache}"
    bob_history = mgr.history.get_all()
    assert all(
        "Alice Secret HW" not in str(entry) for entry in bob_history
    ), f"Alice leaked into Bob's notification history: {bob_history}"

    # Also verify dispatch_grade_alert() does not write Alice's grade alert into Charlie's history.
    notify_entered = asyncio.Event()
    release_notify = asyncio.Event()

    async def _run_grade_race():
        task = asyncio.create_task(
            mgr.dispatch_grade_alert(
                [
                    GradeChange(
                        course_name="Alice Private Course",
                        item_name="Midterm",
                        old_grade="7.0",
                        new_grade="9.5",
                        timestamp=datetime.now().isoformat(),
                    )
                ]
            )
        )
        await asyncio.wait_for(notify_entered.wait(), timeout=2.0)
        mgr.set_namespace("https://courses.ut.edu.vn|charlie")
        release_notify.set()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(_run_grade_race())

    charlie_history = mgr.history.get_all()
    assert all(
        "Alice Private Course" not in str(entry) for entry in charlie_history
    ), f"Alice leaked into Charlie's grade history: {charlie_history}"


# ---------------------------------------------------------------------------
# SEC-07: Mobile SoftFileLock Stale-Lock Recovery & filelock Floor
# ---------------------------------------------------------------------------


def test_sec07_pyproject_pins_filelock_floor_at_least_3_25() -> None:
    import re
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    dep_lists = [
        data["project"]["dependencies"],
        data["dependency-groups"]["dev"],
        data["tool"]["flet"]["windows"]["dependencies"],
        data["tool"]["flet"]["android"]["dependencies"],
        data["tool"]["flet"]["ios"]["dependencies"],
    ]
    for deps in dep_lists:
        matches = [d for d in deps if d.startswith("filelock")]
        assert matches, f"Missing filelock pin in {deps}"
        for dep in matches:
            m = re.match(r"^filelock>=(\d+)\.(\d+)(?:\.(\d+))?$", dep)
            assert m is not None, f"Unexpected filelock spec: {dep}"
            version = tuple(int(part or 0) for part in m.groups())
            assert version >= (3, 25, 0), f"filelock floor too old ({dep}); need >=3.25.0"


def test_sec07_mobile_soft_file_lock_recovers_stale_lock_under_pid_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import socket
    import time
    from filelock import SoftFileLock, Timeout
    from core.safe_file_io import SafeFileIO

    monkeypatch.setattr(sys, "_ANDROID_", True, raising=False)
    # Simulate POSIX EPERM from Android PID reuse where filelock treats the stale PID as alive.
    monkeypatch.setattr(SoftFileLock, "_is_process_alive", staticmethod(lambda _pid: True))

    target = tmp_path / "mobile_settings.json"
    lock_file = target.with_suffix(".lock")
    posix_lock_payload = f"99999\n{socket.gethostname()}\n"

    # 1. Stale SoftFileLock (>60s old) with reused PID -> write_json_atomic MUST recover.
    lock_file.write_text(posix_lock_payload, encoding="utf-8")
    stale_ts = time.time() - 300.0
    os.utime(lock_file, (stale_ts, stale_ts))
    assert (
        SafeFileIO.write_json_atomic(
            target, {"recovered": "write"}, max_retries=3, initial_delay=0.05
        )
        is True
    )
    assert SafeFileIO.read_json_safe(target, dict) == {"recovered": "write"}

    # 2. Stale SoftFileLock (>60s old) with reused PID -> transaction() MUST also recover.
    lock_file.write_text(posix_lock_payload, encoding="utf-8")
    os.utime(lock_file, (stale_ts, stale_ts))
    entered = False
    with SafeFileIO.transaction(target, timeout=0.15):
        entered = True
        assert SafeFileIO.write_json_atomic(target, {"recovered": "tx"}) is True
    assert entered is True
    assert SafeFileIO.read_json_safe(target, dict) == {"recovered": "tx"}

    # 3. Fresh SoftFileLock (actively held, mtime=now) MUST NOT be broken!
    lock_file.write_text(posix_lock_payload, encoding="utf-8")
    assert (
        SafeFileIO.write_json_atomic(
            target, {"should_not_overwrite": True}, max_retries=2, initial_delay=0.05
        )
        is False
    )
    assert lock_file.exists()
    with pytest.raises(Timeout):
        with SafeFileIO.transaction(target, timeout=0.1):
            pass
    assert lock_file.exists()
    lock_file.unlink(missing_ok=True)
