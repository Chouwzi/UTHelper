from importlib.metadata import PackageNotFoundError
from pathlib import Path

from core.version import get_app_version


def _missing_distribution(_name: str) -> str:
    raise PackageNotFoundError


def test_source_checkout_version_wins_over_stale_installed_metadata(tmp_path):
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "2.2.11"\n', encoding="utf-8")

    assert get_app_version(
        pyproject_path=project,
        runtime_version_path=tmp_path / "missing-release-version",
        installed_version_provider=lambda _name: "2.1.0",
    ) == "2.2.11"


def test_packaged_build_reads_generated_runtime_version_without_dist_info(tmp_path):
    runtime_version = tmp_path / "release-version"
    runtime_version.write_bytes(b"2.2.11\n")

    assert get_app_version(
        pyproject_path=tmp_path / "missing-pyproject.toml",
        runtime_version_path=runtime_version,
        installed_version_provider=_missing_distribution,
    ) == "2.2.11"


def test_malformed_runtime_version_fails_closed_to_installed_metadata(tmp_path):
    runtime_version = tmp_path / "release-version"
    runtime_version.write_bytes(b"v2.2.11\n")

    assert get_app_version(
        pyproject_path=tmp_path / "missing-pyproject.toml",
        runtime_version_path=runtime_version,
        installed_version_provider=lambda _name: "2.1.0",
    ) == "2.1.0"
