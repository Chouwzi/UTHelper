"""Resolve the application version from canonical build metadata."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import re
import tomllib
from collections.abc import Callable


_NUMERIC_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_PROJECT_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
_SOURCE_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
RUNTIME_VERSION_FILENAME = "release-version"


def _read_project_version(path: Path) -> str | None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        value = data["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return None
    return value if isinstance(value, str) and _NUMERIC_VERSION.fullmatch(value) else None


def _read_runtime_version(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > 64 or not raw.endswith(b"\n"):
        return None
    try:
        value = raw[:-1].decode("ascii")
    except UnicodeDecodeError:
        return None
    return value if _NUMERIC_VERSION.fullmatch(value) else None


def _default_runtime_version_path() -> Path:
    assets_dir = os.environ.get("FLET_ASSETS_DIR")
    root = Path(assets_dir) if assets_dir else _SOURCE_ASSETS_DIR
    return root / RUNTIME_VERSION_FILENAME


def get_app_version(
    *,
    pyproject_path: Path = _PROJECT_PYPROJECT,
    runtime_version_path: Path | None = None,
    installed_version_provider: Callable[[str], str] | None = None,
) -> str:
    """Return the source or packaged release version, then installed metadata."""
    source_version = _read_project_version(Path(pyproject_path))
    if source_version is not None:
        return source_version

    packaged_version = _read_runtime_version(
        Path(runtime_version_path)
        if runtime_version_path is not None
        else _default_runtime_version_path()
    )
    if packaged_version is not None:
        return packaged_version

    provider = installed_version_provider or version
    try:
        installed_version = provider("uthelper")
    except PackageNotFoundError:
        return "0.0.0"
    return (
        installed_version
        if isinstance(installed_version, str)
        and _NUMERIC_VERSION.fullmatch(installed_version)
        else "0.0.0"
    )


APP_VERSION = get_app_version()
