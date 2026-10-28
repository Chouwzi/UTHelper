import io
import zipfile

import pytest

from scripts.measure_package import inspect_package


def test_package_report_accounts_for_abis_nested_cache_and_reads(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr("main.pyc", b"compiled")
        archive.writestr("__pycache__/main.cpython-314.pyc", b"redundant")
    package = tmp_path / "fixture.apk"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("lib/arm64-v8a/libpython.so", b"runtime" * 100)
        archive.writestr("lib/x86_64/libpython.so", b"runtime" * 100)
        archive.writestr("assets/app.zip", inner.getvalue())

    report = inspect_package(package, read_repeats=2)

    assert report["bytes"] == package.stat().st_size
    assert report["native_abis"] == ["arm64-v8a", "x86_64"]
    assert report["groups"]["lib/arm64-v8a"]["expanded_bytes"] == 700
    assert report["python_archives"][0]["cache_directory_bytes"] == 9
    assert len(report["archive_read"]["seconds"]) == 2
    assert "not OS install" in report["archive_read"]["scope"]
    assert not (tmp_path / "main.pyc").exists()


def test_duplicate_archive_paths_do_not_silently_double_count(tmp_path):
    package = tmp_path / "duplicate.apk"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("classes.dex", b"first")
        with pytest.warns(UserWarning):
            archive.writestr("classes.dex", b"second")
    with pytest.raises(ValueError, match="duplicate"):
        inspect_package(package)
