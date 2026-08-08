from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import stat
import subprocess
import sys
import warnings
import zipfile

import pytest

from scripts import prepare_flet_diagnostics_template as patcher
from scripts import verify_flutter_diagnostics as verifier


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "flutter_template"
TARGET = "build/{{cookiecutter.out_dir}}/lib/main.dart"


def _write_zip(
    path: Path,
    main_source: str,
    *,
    extra_members: tuple[tuple[str, bytes, int], ...] = (),
) -> Path:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("README.txt", b"fixture\n")
            archive.writestr(TARGET, main_source.encode("utf-8"))
            for name, payload, mode in extra_members:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = mode << 16
                archive.writestr(info, payload)
    return path


def _trust_fixture(monkeypatch: pytest.MonkeyPatch, source_zip: Path) -> None:
    monkeypatch.setattr(
        patcher,
        "OFFICIAL_TEMPLATE_SHA256",
        sha256(source_zip.read_bytes()).hexdigest(),
    )


def _read_main(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read(TARGET).decode("utf-8")


def test_patch_matches_reviewed_fixture_and_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_zip(
        tmp_path / "source.zip",
        (FIXTURES / "main.dart").read_text("utf-8"),
    )
    _trust_fixture(monkeypatch, source)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    prepared = patcher.prepare_template(source, first)
    repeated = patcher.prepare_template(source, second)

    assert prepared.official_template_sha256 == patcher.OFFICIAL_TEMPLATE_SHA256
    assert prepared.output_sha256 == repeated.output_sha256
    assert first.read_bytes() == second.read_bytes()
    assert _read_main(first) == (FIXTURES / "main.patched.dart").read_text("utf-8")


def test_flutter_handlers_flush_bridge_before_chaining(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_zip(
        tmp_path / "source.zip",
        (FIXTURES / "main.dart").read_text("utf-8"),
    )
    _trust_fixture(monkeypatch, source)
    output = tmp_path / "prepared.zip"
    patcher.prepare_template(source, output)
    main = _read_main(output)

    assert "writeAsStringSync(" in main
    assert "Future<void> _utHelperWriteFlutterError" not in main
    flutter_write = main.index(
        "_utHelperWriteFlutterError(details.exception, details.stack);"
    )
    flutter_chain = main.index("previousFlutterHandler(details);")
    platform_write = main.index("_utHelperWriteFlutterError(error, stack);")
    platform_chain = main.index("previousPlatformHandler?.call(error, stack)")
    assert flutter_write < flutter_chain
    assert platform_write < platform_chain


def test_unknown_runner_hash_fails_closed_without_replacing_output(tmp_path: Path) -> None:
    source = _write_zip(tmp_path / "unknown.zip", "void main() {}\n")
    output = tmp_path / "prepared.zip"
    output.write_bytes(b"existing")

    with pytest.raises(patcher.UnknownRunnerTemplate, match="hash"):
        patcher.prepare_template(source, output)

    assert output.read_bytes() == b"existing"


def test_anchor_drift_fails_closed_without_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_zip(
        tmp_path / "drift.zip",
        "import 'dart:async';\nvoid main() {}\n",
    )
    _trust_fixture(monkeypatch, source)
    output = tmp_path / "prepared.zip"

    with pytest.raises(patcher.UnknownRunnerTemplate, match="anchor"):
        patcher.prepare_template(source, output)

    assert not output.exists()


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        ("../outside.txt", stat.S_IFREG | 0o644),
        ("/absolute.txt", stat.S_IFREG | 0o644),
        ("unsafe-link", stat.S_IFLNK | 0o777),
    ],
)
def test_unsafe_zip_members_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    mode: int,
) -> None:
    source = _write_zip(
        tmp_path / "unsafe.zip",
        (FIXTURES / "main.dart").read_text("utf-8"),
        extra_members=((name, b"outside", mode),),
    )
    _trust_fixture(monkeypatch, source)

    with pytest.raises(patcher.UnknownRunnerTemplate, match="unsafe"):
        patcher.prepare_template(source, tmp_path / "prepared.zip")


def test_duplicate_zip_members_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_zip(
        tmp_path / "duplicate.zip",
        (FIXTURES / "main.dart").read_text("utf-8"),
        extra_members=((TARGET, b"duplicate", stat.S_IFREG | 0o644),),
    )
    _trust_fixture(monkeypatch, source)

    with pytest.raises(patcher.UnknownRunnerTemplate, match="duplicate"):
        patcher.prepare_template(source, tmp_path / "prepared.zip")


def test_verifier_requires_pinned_template_and_exact_generated_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_zip(
        tmp_path / "source.zip",
        (FIXTURES / "main.dart").read_text("utf-8"),
    )
    _trust_fixture(monkeypatch, source)
    prepared_zip = tmp_path / "prepared.zip"
    prepared = patcher.prepare_template(source, prepared_zip)
    monkeypatch.setattr(
        patcher,
        "PATCHED_TEMPLATE_SHA256",
        prepared.output_sha256,
    )
    project = tmp_path / "flutter"
    generated = project / "lib" / "main.dart"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(_read_main(prepared_zip).encode("utf-8"))

    result = verifier.verify_flutter_diagnostics(prepared_zip, project)

    assert result.output_sha256 == prepared.output_sha256
    generated.write_text("void main() {}\n", "utf-8")
    with pytest.raises(patcher.InvalidPreparedTemplate, match="generated"):
        verifier.verify_flutter_diagnostics(prepared_zip, project)


def test_prepare_cli_uses_documented_exit_codes(tmp_path: Path) -> None:
    source = _write_zip(tmp_path / "unknown.zip", "void main() {}\n")

    assert patcher.main(["--source", str(source), "--output", str(tmp_path / "o.zip")]) == 2
    assert patcher.main(["--source", str(tmp_path / "missing.zip"), "--output", str(tmp_path / "o.zip")]) == 3


def test_verifier_script_is_directly_executable_without_pythonpath() -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "scripts/verify_flutter_diagnostics.py", "--help"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert b"--template" in result.stdout
