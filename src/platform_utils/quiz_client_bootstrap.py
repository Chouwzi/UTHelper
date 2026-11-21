"""Load the local quiz control and prepare its custom Windows Flet client."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path


_QUIZ_PACKAGE = "flet_uth_quiz"
_QUIZ_CLIENT_STAMP = ".flet-quiz-client.sha256"
_QUIZ_EXTENSION_MARKER = "flet_uth_quiz.Extension()"
_QUIZ_CLIENT_BUILD_TIMEOUT_SECONDS = 600
_MIN_BUILD_FREE_BYTES = 1_500_000_000
_GENERATED_DIRECTORIES = frozenset(
    {
        ".dart_tool",
        ".git",
        ".gradle",
        ".pytest_cache",
        ".symlinks",
        "DerivedData",
        "Pods",
        "__pycache__",
        "build",
        "ephemeral",
        "example",
        "examples",
        "test",
        "tests",
    }
)
_GENERATED_FILES = frozenset(
    {
        ".flutter-plugins",
        ".flutter-plugins-dependencies",
        ".packages",
        "GeneratedPluginRegistrant.h",
        "GeneratedPluginRegistrant.java",
        "GeneratedPluginRegistrant.m",
        "GeneratedPluginRegistrant.swift",
        "generated_plugin_registrant.cc",
        "generated_plugin_registrant.cmake",
        "generated_plugin_registrant.h",
        "pubspec.lock",
    }
)
_source_quiz_client_ready: bool | None = None
_added_source_paths: set[str] = set()


class QuizClientBootstrapError(RuntimeError):
    """The source checkout cannot start its custom quiz client."""


def _is_truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def set_source_quiz_client_ready(ready: bool) -> None:
    """Record whether this source process has a verified custom desktop client."""
    global _source_quiz_client_ready
    _source_quiz_client_ready = bool(ready)


def source_quiz_client_unavailable() -> bool:
    """Whether source startup explicitly failed to prepare the custom client."""
    return _source_quiz_client_ready is False


def add_source_quiz_package_to_path(project_root: Path) -> Path:
    """Make the checkout's Python control importable without an editable install."""
    source = project_root / "extensions" / _QUIZ_PACKAGE / "src"
    package_init = source / _QUIZ_PACKAGE / "__init__.py"
    if not package_init.is_file():
        raise QuizClientBootstrapError(
            f"The local quiz control package is missing: {package_init}"
        )

    source_text = str(source.resolve())
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
        _added_source_paths.add(source_text)
    return source


def remove_source_quiz_package_from_path(source: Path | None) -> None:
    """Roll back a ``sys.path`` entry previously added by bootstrap."""
    if source is None:
        return
    source_text = str(source.resolve())
    if source_text not in _added_source_paths:
        return
    _added_source_paths.discard(source_text)
    while source_text in sys.path:
        sys.path.remove(source_text)


def _client_fingerprint(project_root: Path) -> str:
    project_root = project_root.resolve()
    pyproject = project_root / "pyproject.toml"
    extensions_root = project_root / "extensions"
    if not pyproject.is_file() or not extensions_root.is_dir():
        raise QuizClientBootstrapError(
            "The project build configuration or local extensions are missing."
        )

    inputs = [pyproject]
    for extension_root in sorted(
        path for path in extensions_root.iterdir() if path.is_dir()
    ):
        flutter_root = extension_root / "flutter"
        if not flutter_root.is_dir():
            continue
        extension_metadata = extension_root / "pyproject.toml"
        if extension_metadata.is_file():
            inputs.append(extension_metadata)
        for path in sorted(flutter_root.rglob("*")):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(flutter_root).parts
            if any(part in _GENERATED_DIRECTORIES for part in relative_parts):
                continue
            if path.name in _GENERATED_FILES:
                continue
            inputs.append(path)
    if len(inputs) == 1:
        raise QuizClientBootstrapError(
            "No project-local Flutter extension sources were found."
        )

    digest = hashlib.sha256()
    for path in inputs:
        digest.update(path.relative_to(project_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _custom_client_artifacts(project_root: Path) -> tuple[Path, Path, Path]:
    build_root = project_root / "build"
    return (
        build_root / "windows" / "UTHelper.exe",
        build_root / "flutter" / "lib" / "flet_generated.dart",
        build_root / _QUIZ_CLIENT_STAMP,
    )


def _flet_cli_is_installed() -> bool:
    return importlib.util.find_spec("flet_cli") is not None


def _is_current_custom_client(project_root: Path, fingerprint: str) -> bool:
    executable, generated_extensions, stamp = _custom_client_artifacts(project_root)
    try:
        return (
            executable.is_file()
            and _QUIZ_EXTENSION_MARKER in generated_extensions.read_text(
                encoding="utf-8"
            )
            and stamp.read_text(encoding="utf-8").strip() == fingerprint
        )
    except OSError:
        return False


def ensure_windows_quiz_client(project_root: Path) -> Path:
    """Build the custom Flet client when its extension source has changed.

    Flet's ordinary desktop client does not contain project-specific Flutter
    extensions. The built Windows client is selected by Flet when it exists at
    ``build/windows/UTHelper.exe``.
    """
    project_root = project_root.resolve()
    fingerprint = _client_fingerprint(project_root)
    executable, generated_extensions, stamp = _custom_client_artifacts(project_root)
    if _is_current_custom_client(project_root, fingerprint):
        return executable

    if _is_truthy_env("UTHELPER_SKIP_QUIZ_CLIENT_BUILD"):
        raise QuizClientBootstrapError(
            "Windows quiz client build was skipped by UTHELPER_SKIP_QUIZ_CLIENT_BUILD."
        )

    if not _flet_cli_is_installed():
        raise QuizClientBootstrapError(
            "Flet's Windows build tools are required for the in-app quiz view. "
            'Install the Windows app dependencies with `python -m pip install -e ".[windows]"` and retry.'
        )

    build_probe = project_root / "build"
    if not build_probe.exists():
        build_probe = project_root
    try:
        free_bytes = shutil.disk_usage(build_probe).free
    except OSError:
        free_bytes = _MIN_BUILD_FREE_BYTES
    if free_bytes < _MIN_BUILD_FREE_BYTES:
        raise QuizClientBootstrapError(
            "Insufficient free disk space to build the Windows quiz client."
        )

    print("Preparing the in-app quiz view for this source checkout...")
    # Flet's Rich progress output includes Unicode glyphs. Under captured or
    # redirected Windows stdout, the inherited console encoding may be cp1252
    # and abort the build before the custom client is generated.
    # Also, uv virtualenvs use a trampoline executable that injects PYTHONHOME
    # and UV_INTERNAL__PYTHONHOME on child startup; if inherited by serious_python's
    # bundled CPython 3.14 pip, it hangs indefinitely. Clear them inside the
    # child Python process before invoking flet_cli.
    build_env = os.environ.copy()
    build_env["PYTHONIOENCODING"] = "utf-8:replace"
    build_env["PYTHONUTF8"] = "1"
    build_env["PYTHONNOUSERSITE"] = "1"
    build_env["PIP_DEFAULT_TIMEOUT"] = "20"
    build_env["PIP_RETRIES"] = "3"
    build_env.pop("PYTHONPATH", None)
    build_env.pop("PYTHONHOME", None)
    build_env.pop("UV_INTERNAL__PYTHONHOME", None)
    flet_launcher = (
        "import os, sys; "
        "os.environ.pop('PYTHONHOME', None); "
        "os.environ.pop('UV_INTERNAL__PYTHONHOME', None); "
        "from flet_cli.cli import main; "
        "sys.argv = ['flet', 'build', 'windows', '--yes']; "
        "raise SystemExit(main())"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", flet_launcher],
            cwd=project_root,
            env=build_env,
            check=False,
            timeout=_QUIZ_CLIENT_BUILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise QuizClientBootstrapError(
            "Flet timed out while building the Windows quiz client."
        ) from exc
    except OSError as exc:
        raise QuizClientBootstrapError(
            "Flet could not start the Windows quiz client build."
        ) from exc
    if result.returncode != 0:
        raise QuizClientBootstrapError(
            f"Flet could not build the Windows quiz client (exit code {result.returncode})."
        )

    try:
        if not executable.is_file():
            raise QuizClientBootstrapError(
                "Flet finished without creating build/windows/UTHelper.exe."
            )
        if _QUIZ_EXTENSION_MARKER not in generated_extensions.read_text(
            encoding="utf-8"
        ):
            raise QuizClientBootstrapError(
                "The built Windows client does not register the quiz WebView extension."
            )
        stamp.parent.mkdir(parents=True, exist_ok=True)
        temporary_stamp = stamp.with_suffix(stamp.suffix + ".tmp")
        temporary_stamp.write_text(f"{fingerprint}\n", encoding="utf-8")
        temporary_stamp.replace(stamp)
    except OSError as exc:
        raise QuizClientBootstrapError(
            "Flet built the Windows client, but its quiz extension could not be verified."
        ) from exc

    return executable
