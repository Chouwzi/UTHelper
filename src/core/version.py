"""Resolve the application version from project package metadata."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib


def get_app_version() -> str:
    """Return the installed version, with pyproject as a development fallback."""
    try:
        return version("uthelper")
    except PackageNotFoundError:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            return str(data["project"]["version"])
        except (OSError, KeyError, tomllib.TOMLDecodeError):
            return "0.0.0"


APP_VERSION = get_app_version()

