from pathlib import Path

import pytest

from scripts.verify_windows_bundle import (
    BundleVerificationError,
    inspect_bundle,
    verify_bundle,
)


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


def test_source_fallbacks_are_accepted_when_compilation_is_disabled(tmp_path):
    _write_valid_bundle(tmp_path)
    (tmp_path / "Lib" / "encodings" / "__init__.pyc").rename(
        tmp_path / "Lib" / "encodings" / "__init__.py"
    )
    (tmp_path / "app" / "main.pyc").rename(tmp_path / "app" / "main.py")

    verify_bundle(tmp_path)


def test_cli_reports_all_detected_issues(tmp_path, capsys):
    from scripts.verify_windows_bundle import main

    assert main([str(tmp_path)]) == 1
    stderr = capsys.readouterr().err
    assert "UTHelper.exe" in stderr
    assert "Python runtime DLL" in stderr
    assert "filesystem encodings" in stderr
