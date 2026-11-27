from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import flet as ft
import pytest


ROOT = Path(__file__).resolve().parents[1]


class _ReadyTraySpy:
    def __init__(self, *, ready: bool = True) -> None:
        self.is_ready = ready
        self.notifications: list[tuple[str, str]] = []

    def notify(self, title: str, message: str) -> bool:
        self.notifications.append((title, message))
        return True


def _make_close_controller(*, tray, minimize_to_tray: bool, monkeypatch):
    import gui.app_controller as ac_mod

    monkeypatch.setattr(ac_mod.settings, "MINIMIZE_TO_TRAY", minimize_to_tray)
    monkeypatch.setattr(ac_mod.platform_utils, "IS_MOBILE", False)

    ctrl = ac_mod.AppController.__new__(ac_mod.AppController)
    window = SimpleNamespace(visible=True, minimized=False, maximized=False)
    updates: list[int] = []
    ctrl.page = SimpleNamespace(window=window, update=lambda: updates.append(1))
    ctrl.tray = tray
    ctrl._tray_balloon_shown = False
    ctrl.quiz_player_view = None
    destroyed: list[int] = []

    async def _fake_destroy() -> None:
        destroyed.append(1)

    ctrl._destroy_window = _fake_destroy
    return ctrl, window, updates, destroyed


def test_window_header_close_button_hides_to_tray_instead_of_destroying_window(
    monkeypatch,
):
    tray = _ReadyTraySpy(ready=True)
    ctrl, window, updates, destroyed = _make_close_controller(
        tray=tray, minimize_to_tray=True, monkeypatch=monkeypatch
    )

    asyncio.run(ctrl._exit_from_window_header())

    assert window.visible is False
    assert destroyed == []
    assert len(updates) >= 1
    assert len(tray.notifications) == 1
    assert tray.notifications[0][0] == "UTHelper"
    assert "khay hệ thống" in tray.notifications[0][1]

    # Second close to tray should not spam balloon notification again
    window.visible = True
    asyncio.run(ctrl._exit_from_window_header())
    assert window.visible is False
    assert destroyed == []
    assert len(tray.notifications) == 1


def test_window_close_exits_cleanly_when_minimize_to_tray_disabled_or_tray_unavailable(
    monkeypatch,
):
    # Case 1: MINIMIZE_TO_TRAY is False -> exits cleanly
    tray_ready = _ReadyTraySpy(ready=True)
    ctrl_off, window_off, _u1, destroyed_off = _make_close_controller(
        tray=tray_ready, minimize_to_tray=False, monkeypatch=monkeypatch
    )
    asyncio.run(ctrl_off._exit_from_window_header())
    assert destroyed_off == [1]
    assert window_off.visible is True

    # Case 2: MINIMIZE_TO_TRAY is True, but tray is unavailable (None or not ready)
    # -> MUST exit cleanly instead of hiding into an unrecoverable headless process
    for broken_tray in (None, _ReadyTraySpy(ready=False)):
        ctrl_broken, window_broken, _u2, destroyed_broken = _make_close_controller(
            tray=broken_tray, minimize_to_tray=True, monkeypatch=monkeypatch
        )
        close_event = SimpleNamespace(type=ft.WindowEventType.CLOSE, data="close")
        asyncio.run(ctrl_broken._on_window_event(close_event))
        assert destroyed_broken == [1]
        assert window_broken.visible is True


def test_tray_app_exposes_is_ready_property():
    from gui.tray import TrayApp

    tray = TrayApp()
    assert tray.is_ready is False

    tray._icon = SimpleNamespace(stop=lambda: None)
    tray._ready_event.set()
    assert tray.is_ready is True

    tray.close(timeout_seconds=0.05)
    assert tray.is_ready is False


def test_source_python_startup_never_clobbers_real_windows_autostart_registry(
    monkeypatch,
):
    import gui.app_controller as ac_mod
    import platform_utils.autostart as autostart_mod

    ctrl = ac_mod.AppController.__new__(ac_mod.AppController)
    called: list[bool] = []

    async def _fail_if_called(*_args, **_kwargs):
        called.append(True)
        raise AssertionError(
            "Source/dev python runs must never auto-write HKCU Run\\UTHelper"
        )

    monkeypatch.setattr(ac_mod, "apply_initial_autostart_default", _fail_if_called)
    monkeypatch.setattr(autostart_mod, "has_package_identity", lambda: False)
    monkeypatch.setattr(
        autostart_mod,
        "get_current_process_executable",
        lambda: Path(r"C:\Python314\pythonw.exe"),
    )

    asyncio.run(ctrl._apply_initial_windows_autostart_default())
    assert called == []


def test_disabled_autostart_setting_cleans_up_clobbered_stale_run_key_value():
    from gui.controllers.autostart_settings import apply_initial_autostart_default
    from platform_utils.autostart import RunKeyAutostartBackend

    canonical = r'"C:\Program Files\UTHelper\UTHelperAutostart.exe"'
    registry: dict[str, str] = {
        "UTHelper": r"C:\Python314\pythonw.exe E:\temp\flet_demo_server.py --autostart",
        "UTHElearningAlert": r'"C:\Old\UTHelper.exe" --autostart',
    }
    backend = RunKeyAutostartBackend(
        command=canonical,
        reader=registry.get,
        writer=registry.__setitem__,
        deleter=lambda name: registry.pop(name, None),
    )
    settings_obj = SimpleNamespace(
        START_WITH_WINDOWS=False,
        AUTOSTART_DEFAULT_APPLIED=True,
    )

    result = asyncio.run(
        apply_initial_autostart_default(
            backend,
            settings_obj,
            lambda: False,
            startup_approved_disabled_probe=lambda _name: False,
        )
    )

    assert result is None
    assert registry == {}


def test_packaged_windows_main_normalizes_system32_working_directory(tmp_path):
    import src.main as main_mod

    install_dir = tmp_path / "Program Files" / "UTHelper"
    install_dir.mkdir(parents=True)
    exe = install_dir / "UTHelperAutostart.exe"
    exe.write_bytes(b"MZ")

    visited: list[Path] = []
    changed = main_mod._normalize_packaged_windows_cwd(
        executable=exe,
        development=False,
        test_mode=False,
        chdir_fn=lambda target: visited.append(Path(target)),
    )

    assert changed is True
    assert visited == [install_dir]

    # Dev or test mode must not force-chdir to the Python interpreter folder
    visited.clear()
    assert (
        main_mod._normalize_packaged_windows_cwd(
            executable=exe,
            development=True,
            test_mode=False,
            chdir_fn=lambda target: visited.append(Path(target)),
        )
        is False
    )
    assert visited == []


def test_windows_notifier_shortcut_reconciliation_removes_duplicate_per_user_shortcuts_and_skips_python_exe(
    tmp_path,
):
    from notifiers.windows import reconcile_windows_start_menu_shortcuts

    common_programs = tmp_path / "ProgramData" / "Programs"
    user_programs = tmp_path / "AppData" / "Programs"
    user_startup = user_programs / "Startup"
    public_desktop = tmp_path / "PublicDesktop"
    user_desktop = tmp_path / "UserDesktop"
    for d in (
        common_programs / "UTHelper",
        user_programs / "UTHelper",
        user_programs / "UTH Elearning Alert",
        user_startup,
        public_desktop,
        user_desktop,
    ):
        d.mkdir(parents=True, exist_ok=True)

    # Canonical MSI shortcuts
    machine_start_lnk = common_programs / "UTHelper" / "UTHelper.lnk"
    machine_start_lnk.write_bytes(b"MSI-LNK")
    machine_desktop_lnk = public_desktop / "UTHelper.lnk"
    machine_desktop_lnk.write_bytes(b"MSI-DESKTOP-LNK")

    # Stale / duplicate per-user shortcuts accumulated from older versions & python runs
    stale_root_lnk = user_programs / "UTHelper.lnk"
    stale_root_lnk.write_bytes(b"PYTHON-LNK")
    stale_legacy_root_lnk = user_programs / "UTH Elearning Alert.lnk"
    stale_legacy_root_lnk.write_bytes(b"LEGACY-LNK")
    stale_inno_lnk = user_programs / "UTHelper" / "UTHelper.lnk"
    stale_inno_lnk.write_bytes(b"INNO-LNK")
    stale_legacy_dir_lnk = (
        user_programs / "UTH Elearning Alert" / "UTH Elearning Alert.lnk"
    )
    stale_legacy_dir_lnk.write_bytes(b"LEGACY-DIR-LNK")
    stale_startup_lnk = user_startup / "UTHelper.lnk"
    stale_startup_lnk.write_bytes(b"INNO-STARTUP-LNK")
    stale_user_desktop_lnk = user_desktop / "UTHelper.lnk"
    stale_user_desktop_lnk.write_bytes(b"DUPLICATE-USER-DESKTOP")

    created_targets: list[tuple[Path, Path]] = []
    outcome = reconcile_windows_start_menu_shortcuts(
        executable=Path(r"C:\Program Files\UTHelper\UTHelper.exe"),
        common_programs_dir=common_programs,
        user_programs_dir=user_programs,
        public_desktop_dir=public_desktop,
        user_desktop_dir=user_desktop,
        shortcut_writer=lambda lnk, target: created_targets.append((lnk, target)),
    )

    assert outcome == "machine_shortcut_present"
    assert machine_start_lnk.exists()
    assert machine_desktop_lnk.exists()
    assert not stale_root_lnk.exists()
    assert not stale_legacy_root_lnk.exists()
    assert not stale_inno_lnk.exists()
    assert not (user_programs / "UTHelper").exists()
    assert not stale_legacy_dir_lnk.exists()
    assert not stale_startup_lnk.exists()
    assert not stale_user_desktop_lnk.exists()
    assert created_targets == []

    # Bare python interpreter without machine shortcut must NEVER write a python.exe shortcut
    stale_root_lnk.write_bytes(b"BROKEN-PYTHON-LNK")
    machine_start_lnk.unlink()
    outcome_py = reconcile_windows_start_menu_shortcuts(
        executable=Path(r"C:\Python314\python.exe"),
        common_programs_dir=common_programs,
        user_programs_dir=user_programs,
        public_desktop_dir=public_desktop,
        user_desktop_dir=user_desktop,
        shortcut_writer=lambda lnk, target: created_targets.append((lnk, target)),
    )
    assert outcome_py == "skipped_interpreter"
    assert not stale_root_lnk.exists()
    assert created_targets == []

    # Standalone UTHelperAutostart.exe resolves to sibling UTHelper.exe
    standalone_dir = tmp_path / "Portable"
    standalone_dir.mkdir()
    main_exe = standalone_dir / "UTHelper.exe"
    autostart_exe = standalone_dir / "UTHelperAutostart.exe"
    main_exe.write_bytes(b"MZ")
    autostart_exe.write_bytes(b"MZ")
    outcome_portable = reconcile_windows_start_menu_shortcuts(
        executable=autostart_exe,
        common_programs_dir=common_programs,
        user_programs_dir=user_programs,
        public_desktop_dir=public_desktop,
        user_desktop_dir=user_desktop,
        shortcut_writer=lambda lnk, target: created_targets.append((lnk, target)),
    )
    assert outcome_portable == "user_shortcut_created"
    assert created_targets == [(user_programs / "UTHelper.lnk", main_exe)]


def test_read_install_channel_prefers_bootstrapper_when_burn_bundle_is_installed(
    monkeypatch,
):
    from platform_utils import windows_update

    monkeypatch.setattr(windows_update, "_has_installed_burn_bundle", lambda: True)
    assert windows_update.read_install_channel() == "bootstrapper"


def test_wix_installer_defaults_shortcuts_checked_stamps_aumid_and_cleans_legacy_versions():
    package_wxs = (ROOT / "packaging" / "windows" / "Package.wxs").read_text(
        encoding="utf-8"
    )
    bundle_wxs = (ROOT / "packaging" / "windows" / "Bundle.wxs").read_text(
        encoding="utf-8"
    )
    theme_xml_path = ROOT / "packaging" / "windows" / "BundleTheme.xml"
    theme_wxl_path = ROOT / "packaging" / "windows" / "BundleTheme.wxl"
    cleanup_ps1_path = ROOT / "packaging" / "windows" / "cleanup_legacy_installs.ps1"

    assert theme_xml_path.is_file(), "BundleTheme.xml is missing"
    assert theme_wxl_path.is_file(), "BundleTheme.wxl is missing"
    assert cleanup_ps1_path.is_file(), "cleanup_legacy_installs.ps1 is missing"

    theme_xml = theme_xml_path.read_text(encoding="utf-8")
    cleanup_ps1 = cleanup_ps1_path.read_text(encoding="utf-8")

    # 1. Default-enabled secure MSI shortcut properties + Toast AUMID on StartMenuUTHelper
    assert 'Property Id="INSTALLSTARTMENUSHORTCUT" Value="1"' in package_wxs
    assert 'Property Id="INSTALLDESKTOPSHORTCUT" Value="1"' in package_wxs
    assert 'Key="System.AppUserModel.ID" Value="UTHelper.App"' in package_wxs
    assert "INSTALLSTARTMENUSHORTCUT" in package_wxs
    assert "INSTALLDESKTOPSHORTCUT" in package_wxs

    # 2. Default-ticked (Value="1") Burn variables + MsiProperty bindings + UI checkboxes
    assert 'Variable Name="InstallDesktopShortcut" Type="numeric" Value="1"' in bundle_wxs
    assert 'Variable Name="InstallStartMenuShortcut" Type="numeric" Value="1"' in bundle_wxs
    assert 'MsiProperty Name="INSTALLDESKTOPSHORTCUT" Value="[InstallDesktopShortcut]"' in bundle_wxs
    assert 'MsiProperty Name="INSTALLSTARTMENUSHORTCUT" Value="[InstallStartMenuShortcut]"' in bundle_wxs
    assert 'Checkbox Name="InstallDesktopShortcut"' in theme_xml
    assert 'Checkbox Name="InstallStartMenuShortcut"' in theme_xml

    # 3. Legacy Inno Setup & stale per-user shortcut cleanup
    assert "{D1A25B3C-9A5C-4F62-8E3A-7B5E2C1A8B9D}_is1" in cleanup_ps1
    assert "{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}_is1" in cleanup_ps1
    assert "After=\"InstallFinalize\"" in package_wxs
