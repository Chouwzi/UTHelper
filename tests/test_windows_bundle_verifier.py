from pathlib import Path

import pytest

from scripts.verify_windows_bundle import (
    BundleVerificationError,
    inspect_bundle,
    verify_bundle,
)
from scripts.prepare_windows_bundle import prepare_windows_bundle


def _write_valid_bundle(root: Path) -> None:
    (root / "Lib" / "encodings").mkdir(parents=True)
    (root / "app").mkdir()
    (root / "app" / "assets").mkdir()
    (root / "site-packages").mkdir()
    (root / "site-packages" / "winrt").mkdir()
    (root / "site-packages" / "win32" / "lib").mkdir(parents=True)
    (root / "site-packages" / "pywin32_system32").mkdir()
    (root / "UTHelper.exe").write_bytes(b"MZ")
    (root / "UTHelperAutostart.exe").write_bytes(b"MZ")
    (root / "python314.dll").write_bytes(b"dll")
    (root / "flutter_windows.dll").write_bytes(b"dll")
    (
        root
        / "site-packages"
        / "winrt"
        / "_winrt_windows_applicationmodel.cp314-win_amd64.pyd"
    ).write_bytes(b"pyd")
    for module in ("win32api.pyd", "win32event.pyd", "win32security.pyd"):
        (root / "site-packages" / "win32" / module).write_bytes(b"pyd")
    for module in ("win32con.pyc", "winerror.pyc", "pywintypes.pyc"):
        (root / "site-packages" / "win32" / "lib" / module).write_bytes(b"pyc")
    (
        root / "site-packages" / "pywin32_system32" / "pywintypes314.dll"
    ).write_bytes(b"dll")
    (root / "Lib" / "encodings" / "__init__.pyc").write_bytes(b"pyc")
    (root / "app" / "main.pyc").write_bytes(b"pyc")
    (root / "app" / "assets" / "release-version").write_bytes(b"2.2.11\n")


def test_valid_compiled_flet_bundle_has_no_issues(tmp_path):
    _write_valid_bundle(tmp_path)

    assert inspect_bundle(tmp_path) == ()
    verify_bundle(tmp_path)


@pytest.mark.parametrize(
    ("relative_path", "message"),
    [
        ("UTHelper.exe", "UTHelper.exe"),
        ("UTHelperAutostart.exe", "UTHelperAutostart.exe"),
        ("python314.dll", "Python runtime DLL"),
        ("flutter_windows.dll", "Flutter runtime DLL"),
        ("Lib/encodings/__init__.pyc", "filesystem encodings"),
        ("app/main.pyc", "compiled application entry"),
        ("app/assets/release-version", "packaged release version"),
        (
            "site-packages/winrt/_winrt_windows_applicationmodel.cp314-win_amd64.pyd",
            "Windows.ApplicationModel projection",
        ),
        ("site-packages/win32/win32api.pyd", "win32api"),
        ("site-packages/win32/win32event.pyd", "win32event"),
        ("site-packages/win32/win32security.pyd", "win32security"),
        ("site-packages/win32/lib/win32con.pyc", "win32con"),
        ("site-packages/win32/lib/winerror.pyc", "winerror"),
        ("site-packages/win32/lib/pywintypes.pyc", "pywintypes loader"),
        ("site-packages/pywin32_system32/pywintypes314.dll", "pywintypes"),
    ],
)
def test_missing_runtime_artifact_fails_closed(tmp_path, relative_path, message):
    _write_valid_bundle(tmp_path)
    (tmp_path / relative_path).unlink()

    with pytest.raises(BundleVerificationError, match=message):
        verify_bundle(tmp_path)


def test_source_fallbacks_are_accepted_when_compilation_is_disabled(tmp_path):
    _write_valid_bundle(tmp_path)
    (tmp_path / "Lib" / "encodings" / "__init__.pyc").rename(
        tmp_path / "Lib" / "encodings" / "__init__.py"
    )
    (tmp_path / "app" / "main.pyc").rename(tmp_path / "app" / "main.py")

    verify_bundle(tmp_path)


def test_bundle_version_must_match_the_release_being_packaged(tmp_path):
    _write_valid_bundle(tmp_path)

    with pytest.raises(BundleVerificationError, match="release version mismatch"):
        verify_bundle(tmp_path, expected_version="2.2.12")


def test_cli_reports_all_detected_issues(tmp_path, capsys):
    from scripts.verify_windows_bundle import main

    assert main([str(tmp_path)]) == 1
    stderr = capsys.readouterr().err
    assert "UTHelper.exe" in stderr
    assert "Python runtime DLL" in stderr
    assert "filesystem encodings" in stderr


def test_prepare_bundle_creates_byte_identical_autostart_runner(tmp_path):
    runner = tmp_path / "UTHelper.exe"
    runner.write_bytes(b"MZ\x00embedded-flet-runner")

    alias = prepare_windows_bundle(tmp_path)

    assert alias == tmp_path / "UTHelperAutostart.exe"
    assert alias.read_bytes() == runner.read_bytes()


def test_prepare_bundle_replaces_stale_alias_idempotently(tmp_path):
    runner = tmp_path / "UTHelper.exe"
    alias = tmp_path / "UTHelperAutostart.exe"
    runner.write_bytes(b"new runner")
    alias.write_bytes(b"old runner")

    prepare_windows_bundle(tmp_path)
    prepare_windows_bundle(tmp_path)

    assert alias.read_bytes() == b"new runner"


def test_verifier_rejects_nonidentical_autostart_runner(tmp_path):
    _write_valid_bundle(tmp_path)
    (tmp_path / "UTHelperAutostart.exe").write_bytes(b"different")

    with pytest.raises(BundleVerificationError, match="byte-identical"):
        verify_bundle(tmp_path)
