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

    def build(command, *, cwd, env, check, timeout=None):
        calls.append(
            {
                "command": command,
                "cwd": cwd,
                "env": env,
                "check": check,
                "timeout": timeout,
            }
        )
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
    monkeypatch.setattr(
        quiz_client_bootstrap.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10**11, used=10**10, free=10**10),
    )
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("PYTHONHOME", r"C:\stale-python")
    monkeypatch.setenv("UV_INTERNAL__PYTHONHOME", r"C:\stale-uv-python")
    monkeypatch.setenv("PYTHONPATH", r"C:\stale-pythonpath")
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("UTHELPER_SKIP_QUIZ_CLIENT_BUILD", raising=False)
    monkeypatch.delenv("CL", raising=False)
    monkeypatch.setenv("UTH_BUILD_ENV_SENTINEL", "kept-for-flet")

    client = quiz_client_bootstrap.ensure_windows_quiz_client(project_root)
    assert client == project_root / "build" / "windows" / "UTHelper.exe"
    assert len(calls) == 1
    assert calls[0]["command"][0] == sys.executable
    assert calls[0]["command"][1] == "-c"
    assert "os.environ.pop('PYTHONHOME', None)" in calls[0]["command"][2]
    assert "os.environ.pop('UV_INTERNAL__PYTHONHOME', None)" in calls[0]["command"][2]
    assert "sys.argv = ['flet', 'build', 'windows', '--yes']" in calls[0]["command"][2]
    assert calls[0]["cwd"] == project_root.resolve()
    assert calls[0]["check"] is False
    assert calls[0]["timeout"] == 600
    assert calls[0]["env"]["PYTHONIOENCODING"] == "utf-8:replace"
    assert calls[0]["env"]["PYTHONUTF8"] == "1"
    assert calls[0]["env"]["PYTHONNOUSERSITE"] == "1"
    assert calls[0]["env"]["PIP_DEFAULT_TIMEOUT"] == "20"
    assert calls[0]["env"]["PIP_RETRIES"] == "3"
    assert (
        calls[0]["env"]["CL"]
        == "/D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS"
    )
    assert "PYTHONHOME" not in calls[0]["env"]
    assert "UV_INTERNAL__PYTHONHOME" not in calls[0]["env"]
    assert "PYTHONPATH" not in calls[0]["env"]
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

    baseline_fingerprint = quiz_client_bootstrap._client_fingerprint(project_root)
    assert quiz_client_bootstrap._is_current_custom_client(
        project_root, baseline_fingerprint
    )

    quiz_flutter = extension_root / "flutter" / "flet_uth_quiz"
    generated_files = [
        quiz_flutter / ".flutter-plugins-dependencies",
        quiz_flutter / "pubspec.lock",
        quiz_flutter / ".flutter-plugins",
        quiz_flutter / ".packages",
        quiz_flutter / "linux" / "flutter" / "generated_plugin_registrant.cc",
    ]
    for generated_file in generated_files:
        generated_file.parent.mkdir(parents=True, exist_ok=True)
        generated_file.write_text("generated-v1\n", encoding="utf-8")
    assert quiz_client_bootstrap._client_fingerprint(project_root) == baseline_fingerprint
    assert quiz_client_bootstrap._is_current_custom_client(
        project_root, baseline_fingerprint
    )
    quiz_client_bootstrap.ensure_windows_quiz_client(project_root)
    assert len(calls) == 3

    (quiz_flutter / ".flutter-plugins-dependencies").write_text(
        '{"date_created":"2099-01-01 00:00:00"}\n', encoding="utf-8"
    )
    (quiz_flutter / "pubspec.lock").write_text("# modified lock\n", encoding="utf-8")
    assert quiz_client_bootstrap._client_fingerprint(project_root) == baseline_fingerprint
    assert quiz_client_bootstrap._is_current_custom_client(
        project_root, baseline_fingerprint
    )
    quiz_client_bootstrap.ensure_windows_quiz_client(project_root)
    assert len(calls) == 3

    for generated_file in generated_files:
        generated_file.unlink()
    assert quiz_client_bootstrap._client_fingerprint(project_root) == baseline_fingerprint
    assert quiz_client_bootstrap._is_current_custom_client(
        project_root, baseline_fingerprint
    )
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
    monkeypatch.delenv("UTHELPER_SKIP_QUIZ_CLIENT_BUILD", raising=False)

    with pytest.raises(quiz_client_bootstrap.QuizClientBootstrapError, match="build tools"):
        quiz_client_bootstrap.ensure_windows_quiz_client(tmp_path)

    assert not (tmp_path / "build" / "windows" / "UTHelper.exe").exists()


def _create_minimal_quiz_project(tmp_path: Path) -> None:
    extension_root = tmp_path / "extensions" / "flet_uth_quiz"
    flutter_package = extension_root / "flutter" / "flet_uth_quiz"
    flutter_package.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[tool.flet.windows]\n", encoding="utf-8")
    (extension_root / "pyproject.toml").write_text(
        "[project]\nname='flet-uth-quiz'\n", encoding="utf-8"
    )
    (flutter_package / "lib.dart").write_text("class Extension {}\n", encoding="utf-8")


def test_quiz_client_bootstrap_guards_opt_out_low_disk_and_timeout(
    tmp_path, monkeypatch
):
    _create_minimal_quiz_project(tmp_path)
    monkeypatch.setattr(quiz_client_bootstrap, "_flet_cli_is_installed", lambda: True)

    monkeypatch.setenv("UTHELPER_SKIP_QUIZ_CLIENT_BUILD", "1")
    with pytest.raises(
        quiz_client_bootstrap.QuizClientBootstrapError,
        match="UTHELPER_SKIP_QUIZ_CLIENT_BUILD",
    ):
        quiz_client_bootstrap.ensure_windows_quiz_client(tmp_path)
    monkeypatch.delenv("UTHELPER_SKIP_QUIZ_CLIENT_BUILD", raising=False)

    monkeypatch.setattr(
        quiz_client_bootstrap.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10**10, used=9 * 10**9, free=100_000_000),
    )
    with pytest.raises(
        quiz_client_bootstrap.QuizClientBootstrapError,
        match="free disk space",
    ):
        quiz_client_bootstrap.ensure_windows_quiz_client(tmp_path)

    monkeypatch.setattr(
        quiz_client_bootstrap.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10**11, used=10**10, free=10**10),
    )

    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["flet"], timeout=600)

    monkeypatch.setattr(quiz_client_bootstrap.subprocess, "run", timed_out)
    with pytest.raises(
        quiz_client_bootstrap.QuizClientBootstrapError,
        match="timed out",
    ):
        quiz_client_bootstrap.ensure_windows_quiz_client(tmp_path)


@pytest.mark.parametrize(
    ("existing_cl", "expected_cl"),
    [
        (None, "/D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS"),
        ("   ", "/D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS"),
        (
            "/Zi /DFOO=1",
            "/Zi /DFOO=1 /D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS",
        ),
        (
            "/D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS",
            "/D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS",
        ),
        (
            "-D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS=1 /W4",
            "-D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS=1 /W4",
        ),
        (
            "/D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS_OTHER",
            "/D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS_OTHER "
            "/D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS",
        ),
    ],
)
def test_windows_quiz_client_bootstrap_preserves_existing_cl_flags(
    tmp_path, monkeypatch, existing_cl, expected_cl
):
    _create_minimal_quiz_project(tmp_path)
    captured_envs: list[dict[str, str]] = []

    def build(_command, *, cwd, env, check, timeout=None):
        captured_envs.append(env)
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
    monkeypatch.setattr(
        quiz_client_bootstrap.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10**11, used=10**10, free=10**10),
    )
    monkeypatch.delenv("UTHELPER_SKIP_QUIZ_CLIENT_BUILD", raising=False)
    if existing_cl is None:
        monkeypatch.delenv("CL", raising=False)
    else:
        monkeypatch.setenv("CL", existing_cl)

    quiz_client_bootstrap.ensure_windows_quiz_client(tmp_path)

    assert len(captured_envs) == 1
    assert captured_envs[0]["CL"] == expected_cl
    if existing_cl is None:
        assert "CL" not in os.environ
    else:
        assert os.environ["CL"] == existing_cl


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
