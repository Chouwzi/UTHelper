"""Load the local quiz control and prepare its custom Windows Flet client."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import importlib.util
import os
import re
import secrets
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


_NUMERIC_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_QUIZ_PACKAGE = "flet_uth_quiz"
_QUIZ_CLIENT_STAMP = ".flet-quiz-client.sha256"
_QUIZ_EXTENSION_MARKER = "flet_uth_quiz.Extension()"
_QUIZ_CLIENT_BUILD_TIMEOUT_SECONDS = 1800
_MIN_BUILD_FREE_BYTES = 1_500_000_000
_NUGET_VERSION = "7.3.1"
_NUGET_DOWNLOAD_URL = (
    f"https://dist.nuget.org/win-x86-commandline/v{_NUGET_VERSION}/nuget.exe"
)
_NUGET_SHA256 = "08ec6563b4a1ddd113d42531067539ffcd0c2d9632d839ad4eb58a0d5ddf1919"
_NUGET_DOWNLOAD_TIMEOUT_SECONDS = 60
_NUGET_MAX_BYTES = 20 * 1024 * 1024
_MSVC_COROUTINE_SILENCE_DEFINE = "_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS"
_MSVC_COROUTINE_SILENCE_CL_FLAG = f"/D{_MSVC_COROUTINE_SILENCE_DEFINE}"
_MSVC_COROUTINE_SILENCE_PATTERN = re.compile(
    rf"(?:^|\s)[/-]D{re.escape(_MSVC_COROUTINE_SILENCE_DEFINE)}(?:=\S*)?(?=\s|$)"
)
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


def _with_msvc_experimental_coroutine_cl_flag(existing_cl: str | None) -> str:
    """Append MSVC STL1011 suppression without clobbering existing CL flags."""
    current = (existing_cl or "").strip()
    if not current:
        return _MSVC_COROUTINE_SILENCE_CL_FLAG
    if _MSVC_COROUTINE_SILENCE_PATTERN.search(current):
        return current
    return f"{current} {_MSVC_COROUTINE_SILENCE_CL_FLAG}"


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


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject any non-HTTPS redirect while downloading build tooling."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme.lower() != "https":
            raise urllib.error.URLError("Refusing non-HTTPS redirect for NuGet CLI.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(65536):
                digest.update(chunk)
        return digest.hexdigest().lower()
    except OSError:
        return None


def _prepend_path_dir(env: dict[str, str], directory: Path) -> None:
    dir_text = str(directory.resolve())
    existing = env.get("PATH", "")
    parts = [entry for entry in existing.split(os.pathsep) if entry]
    if parts and os.path.normcase(parts[0]) == os.path.normcase(dir_text):
        return
    filtered = [
        entry for entry in parts if os.path.normcase(entry) != os.path.normcase(dir_text)
    ]
    env["PATH"] = os.pathsep.join([dir_text, *filtered])


def _nuget_cache_candidates(project_root: Path, env: Mapping[str, str]) -> list[Path]:
    candidates: list[Path] = []
    local_app_data = env.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(
            Path(local_app_data)
            / "UTHelper"
            / "build-tools"
            / "nuget"
            / f"v{_NUGET_VERSION}"
            / "nuget.exe"
        )
    candidates.append(
        project_root
        / "build"
        / ".tools"
        / "nuget"
        / f"v{_NUGET_VERSION}"
        / "nuget.exe"
    )
    return candidates


def _download_verified_nuget_bytes() -> bytes:
    parsed = urlsplit(_NUGET_DOWNLOAD_URL)
    if parsed.scheme.lower() != "https" or parsed.hostname != "dist.nuget.org":
        raise QuizClientBootstrapError("Invalid NuGet download endpoint configuration.")
    opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler())
    request = urllib.request.Request(
        _NUGET_DOWNLOAD_URL,
        headers={"User-Agent": "UTHelper-QuizClientBootstrap/1.0"},
    )
    try:
        with opener.open(request, timeout=_NUGET_DOWNLOAD_TIMEOUT_SECONDS) as response:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > _NUGET_MAX_BYTES:
                    raise QuizClientBootstrapError(
                        "Downloaded NuGet CLI exceeded maximum allowed size."
                    )
                chunks.append(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise QuizClientBootstrapError(
            "NuGet CLI (`nuget.exe`) is required by `flutter_inappwebview_windows` and "
            "could not be downloaded automatically from `https://dist.nuget.org`."
        ) from exc

    payload = b"".join(chunks)
    actual_sha256 = hashlib.sha256(payload).hexdigest().lower()
    if payload[:2] != b"MZ" or actual_sha256 != _NUGET_SHA256:
        raise QuizClientBootstrapError(
            "Downloaded NuGet CLI failed SHA-256 or PE header verification."
        )
    return payload


def _ensure_windows_nuget_executable(
    project_root: Path, env: dict[str, str]
) -> Path:
    """Ensure ``nuget.exe`` is available on ``env['PATH']`` for Windows CMake."""
    override = env.get("UTHELPER_NUGET_EXE", "").strip()
    if override:
        override_path = Path(override)
        if not override_path.is_file():
            raise QuizClientBootstrapError(
                f"UTHELPER_NUGET_EXE does not point to an existing file: {override_path}"
            )
        resolved = override_path.resolve()
        _prepend_path_dir(env, resolved.parent)
        return resolved

    search_path = env.get("PATH")
    found = shutil.which("nuget.exe", path=search_path) or shutil.which(
        "nuget", path=search_path
    )
    if found:
        found_path = Path(found)
        if found_path.is_file():
            return found_path.resolve()

    known_locations: list[Path] = []
    program_data = env.get("ProgramData", "").strip()
    if program_data:
        known_locations.append(Path(program_data) / "Chocolatey" / "bin" / "nuget.exe")
    local_app_data = env.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        known_locations.append(
            Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / "nuget.exe"
        )
    user_profile = env.get("USERPROFILE", "").strip()
    if user_profile:
        known_locations.append(Path(user_profile) / "scoop" / "shims" / "nuget.exe")
    for candidate in known_locations:
        if candidate.is_file():
            resolved = candidate.resolve()
            _prepend_path_dir(env, resolved.parent)
            return resolved

    cache_candidates = _nuget_cache_candidates(project_root, env)
    for candidate in cache_candidates:
        if candidate.is_file():
            if _sha256_file(candidate) == _NUGET_SHA256:
                resolved = candidate.resolve()
                _prepend_path_dir(env, resolved.parent)
                return resolved
            try:
                candidate.unlink()
            except OSError:
                pass

    print(f"Downloading verified NuGet CLI v{_NUGET_VERSION} for Windows WebView build...")
    payload = _download_verified_nuget_bytes()
    last_error: OSError | None = None
    for candidate in cache_candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            temp_file = candidate.with_name(f"nuget.{os.getpid()}.tmp")
            temp_file.write_bytes(payload)
            temp_file.replace(candidate)
            resolved = candidate.resolve()
            _prepend_path_dir(env, resolved.parent)
            return resolved
        except OSError as exc:
            last_error = exc
    raise QuizClientBootstrapError(
        "Could not persist verified NuGet CLI into local build-tools cache."
    ) from last_error


def _clear_stale_windows_cmake_nuget_cache(project_root: Path) -> bool:
    """Clear CMake's Windows build dir if a prior run cached ``NUGET-NOTFOUND``."""
    cmake_windows_dir = project_root / "build" / "flutter" / "build" / "windows"
    if not cmake_windows_dir.is_dir():
        return False
    stale_found = False
    for cache_file in cmake_windows_dir.rglob("CMakeCache.txt"):
        try:
            if "NUGET-NOTFOUND" in cache_file.read_text(
                encoding="utf-8", errors="ignore"
            ):
                stale_found = True
                break
        except OSError:
            continue
    if not stale_found:
        return False
    shutil.rmtree(cmake_windows_dir, ignore_errors=True)
    return True


def _sync_runtime_version_asset(project_root: Path) -> None:
    """Keep local and bundled ``release-version`` assets aligned with ``pyproject.toml``."""
    pyproject = project_root / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        version = data["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return
    if not isinstance(version, str) or not _NUMERIC_VERSION.fullmatch(version):
        return
    expected = f"{version}\n".encode("ascii")

    targets = [project_root / "src" / "assets" / "release-version"]
    bundled_assets = project_root / "build" / "windows" / "app" / "assets"
    if bundled_assets.is_dir():
        targets.append(bundled_assets / "release-version")

    for target in targets:
        try:
            if target.is_file() and target.read_bytes() == expected:
                continue
        except OSError:
            pass
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.parent / f".{target.name}.{secrets.token_hex(6)}.tmp"
            try:
                with temporary.open("xb") as stream:
                    stream.write(expected)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            continue


def ensure_windows_quiz_client(project_root: Path) -> Path:
    """Build the custom Flet client when its extension source has changed.

    Flet's ordinary desktop client does not contain project-specific Flutter
    extensions. The built Windows client is selected by Flet when it exists at
    ``build/windows/UTHelper.exe``.
    """
    project_root = project_root.resolve()
    fingerprint = _client_fingerprint(project_root)
    _sync_runtime_version_asset(project_root)
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
    build_env["CL"] = _with_msvc_experimental_coroutine_cl_flag(build_env.get("CL"))
    build_env.pop("PYTHONPATH", None)
    build_env.pop("PYTHONHOME", None)
    build_env.pop("UV_INTERNAL__PYTHONHOME", None)
    _clear_stale_windows_cmake_nuget_cache(project_root)
    if sys.platform == "win32" and not _is_truthy_env("UTH_TEST_MODE"):
        _ensure_windows_nuget_executable(project_root, build_env)
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

    _sync_runtime_version_asset(project_root)
    return executable
