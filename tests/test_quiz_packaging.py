"""Source and native-client bootstrap checks for the quiz WebView extension."""

import os
import filecmp
from pathlib import Path
import subprocess
import sys
import textwrap
import tomllib
from types import SimpleNamespace

import pytest

from platform_utils import quiz_client_bootstrap


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("platform", ["windows", "android", "ios"])
def test_native_quiz_extension_is_packaged_from_repository(platform):
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    target = config["tool"]["flet"][platform]
    assert "flet-uth-quiz==0.1.0" in target["dependencies"]
    package = ROOT / target["dev_packages"]["flet-uth-quiz"]
    metadata = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["name"] == "flet-uth-quiz"
    assert metadata["project"]["version"] == "0.1.0"
    included = metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert any(
        (package / source / "lib" / "flet_uth_quiz.dart").is_file()
        for source in included
    )


def test_source_checkout_imports_quiz_control_without_editable_install():
    script = textwrap.dedent(
        f"""
        from pathlib import Path
        from platform_utils.quiz_client_bootstrap import add_source_quiz_package_to_path

        root = Path({str(ROOT)!r})
        source = add_source_quiz_package_to_path(root).resolve()
        import flet_uth_quiz
        assert Path(flet_uth_quiz.__file__).resolve().is_relative_to(source / "flet_uth_quiz")
        """
    )
    child_env = os.environ.copy()
    child_env["PYTHONPATH"] = str(ROOT / "src")
    child_env["UTH_TEST_MODE"] = "1"

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_windows_quiz_client_build_is_cached_by_flutter_sources(tmp_path, monkeypatch):
    project_root = tmp_path
    extension_root = project_root / "extensions" / "flet_uth_quiz"
    flutter_package = extension_root / "flutter" / "flet_uth_quiz"
    flutter_package.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text("[tool.flet.windows]\n", encoding="utf-8")
    (extension_root / "pyproject.toml").write_text("[project]\nname='flet-uth-quiz'\n", encoding="utf-8")
    dart_source = flutter_package / "lib.dart"
    dart_source.write_text("class Extension {}\n", encoding="utf-8")

    background_root = project_root / "extensions" / "flet_uth_background_sync"
    background_flutter = background_root / "flutter" / "flet_uth_background_sync"
    background_flutter.mkdir(parents=True)
    (background_root / "pyproject.toml").write_text(
        "[project]\nname='flet-uth-background-sync'\n", encoding="utf-8"
    )
    background_source = background_flutter / "lib.dart"
    background_source.write_text("class BackgroundExtension {}\n", encoding="utf-8")

    calls = []
    run_child = subprocess.run

    def build(command, *, cwd, env, check):
        calls.append({"command": command, "cwd": cwd, "env": env, "check": check})
        (cwd / "build" / "windows").mkdir(parents=True, exist_ok=True)
        (cwd / "build" / "windows" / "UTHelper.exe").write_bytes(b"client")
        generated = cwd / "build" / "flutter" / "lib"
        generated.mkdir(parents=True, exist_ok=True)
        (generated / "flet_generated.dart").write_text(
            "flet_uth_quiz.Extension()\n", encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(quiz_client_bootstrap, "_flet_cli_is_installed", lambda: True)
    monkeypatch.setattr(quiz_client_bootstrap.subprocess, "run", build)
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("UTH_BUILD_ENV_SENTINEL", "kept-for-flet")

    client = quiz_client_bootstrap.ensure_windows_quiz_client(project_root)
    assert client == project_root / "build" / "windows" / "UTHelper.exe"
    assert len(calls) == 1
    assert calls[0]["command"][-5:] == ["-m", "flet_cli.cli", "build", "windows", "--yes"]
    assert calls[0]["cwd"] == project_root.resolve()
    assert calls[0]["check"] is False
    assert calls[0]["env"]["PYTHONIOENCODING"] == "utf-8:replace"
    assert calls[0]["env"]["UTH_BUILD_ENV_SENTINEL"] == "kept-for-flet"
    assert os.environ["PYTHONIOENCODING"] == "cp1252"
    unicode_probe = run_child(
        [sys.executable, "-c", "print(chr(0x2705))"],
        cwd=project_root,
        env=calls[0]["env"],
        capture_output=True,
        check=False,
    )
    assert unicode_probe.returncode == 0, unicode_probe.stderr
    assert unicode_probe.stdout.decode("utf-8").strip() == "✅"

    quiz_client_bootstrap.ensure_windows_quiz_client(project_root)
    assert len(calls) == 1

    background_source.write_text(
        "class BackgroundExtension { final value = 1; }\n", encoding="utf-8"
    )
    quiz_client_bootstrap.ensure_windows_quiz_client(project_root)
    assert len(calls) == 2

    dart_source.write_text("class Extension { final value = 1; }\n", encoding="utf-8")
    quiz_client_bootstrap.ensure_windows_quiz_client(project_root)
    assert len(calls) == 3

    ignored_cache = background_flutter / ".dart_tool" / "generated.dart"
    ignored_cache.parent.mkdir()
    ignored_cache.write_text("generated cache\n", encoding="utf-8")
    quiz_client_bootstrap.ensure_windows_quiz_client(project_root)
    assert len(calls) == 3


def test_missing_flet_cli_does_not_mark_quiz_client_available(tmp_path, monkeypatch):
    extension_root = tmp_path / "extensions" / "flet_uth_quiz"
    flutter_package = extension_root / "flutter" / "flet_uth_quiz"
    flutter_package.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[tool.flet.windows]\n", encoding="utf-8")
    (extension_root / "pyproject.toml").write_text("[project]\nname='flet-uth-quiz'\n", encoding="utf-8")
    (flutter_package / "lib.dart").write_text("class Extension {}\n", encoding="utf-8")
    monkeypatch.setattr(quiz_client_bootstrap, "_flet_cli_is_installed", lambda: False)

    with pytest.raises(quiz_client_bootstrap.QuizClientBootstrapError, match="build tools"):
        quiz_client_bootstrap.ensure_windows_quiz_client(tmp_path)

    assert not (tmp_path / "build" / "windows" / "UTHelper.exe").exists()


def test_flet_desktop_selects_built_quiz_client_on_windows(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("The Flet Windows desktop resolver only runs on Windows.")

    client = ROOT / "build" / "windows" / "UTHelper.exe"
    generated = ROOT / "build" / "flutter" / "lib" / "flet_generated.dart"
    if not client.is_file() or not generated.is_file():
        pytest.skip("Run `flet build windows` before checking the native client.")
    assert "flet_uth_quiz.Extension()" in generated.read_text(encoding="utf-8")

    from flet_desktop import __locate_and_unpack_flet_view

    monkeypatch.chdir(ROOT)
    args, _environment, _pid_file = __locate_and_unpack_flet_view(
        "http://127.0.0.1:8551", None, False
    )

    selected_client = Path(args[0]).resolve()
    assert selected_client.parent == client.resolve().parent
    assert filecmp.cmp(selected_client, client, shallow=False)
