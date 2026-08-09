from pathlib import Path

import pytest

from scripts.release_metadata import (
    ReleaseMetadata,
    ReleaseMetadataError,
    read_project_version,
    read_release_metadata,
    release_build_number,
)


ROOT = Path(__file__).resolve().parents[1]


def test_project_version_is_only_authored_version(tmp_path):
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "2.2.3"\n', encoding="utf-8")

    assert read_release_metadata(project, "v2.2.3") == ReleaseMetadata(
        "2.2.3", "v2.2.3", 2_002_003
    )


def test_this_feature_release_bumps_the_single_authored_version():
    assert read_project_version(ROOT / "pyproject.toml") == "2.2.4"


@pytest.mark.parametrize("tag", ["v2.2.4", "2.2.3", "v2.2.3-rc1"])
def test_tag_must_exactly_match_numeric_project_version(tmp_path, tag):
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "2.2.3"\n', encoding="utf-8")

    with pytest.raises(ReleaseMetadataError):
        read_release_metadata(project, tag)


def test_build_number_rejects_components_over_999():
    with pytest.raises(ReleaseMetadataError, match="0..999"):
        release_build_number("2.1000.0")


@pytest.mark.parametrize("version", ["2.2", "2.2.0rc1", "v2.2.0", "02.2.0"])
def test_project_version_must_be_canonical_numeric_triplet(tmp_path, version):
    project = tmp_path / "pyproject.toml"
    project.write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")

    with pytest.raises(ReleaseMetadataError, match="numeric X.Y.Z"):
        read_project_version(project)
