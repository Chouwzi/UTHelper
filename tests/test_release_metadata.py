from pathlib import Path

import pytest

from scripts.release_metadata import (
    DEFAULT_ANDROID_PACKAGE_ID,
    ReleaseMetadata,
    ReleaseMetadataError,
    read_project_version,
    read_release_metadata,
    release_build_number,
    write_runtime_version,
)


ROOT = Path(__file__).resolve().parents[1]


def test_project_version_is_only_authored_version(tmp_path):
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "2.2.3"\n', encoding="utf-8")

    assert read_release_metadata(project, "v2.2.3") == ReleaseMetadata(
        "2.2.3", "v2.2.3", 2_002_003
    )


def test_hotfix_version_can_upgrade_the_affected_release():
    version = read_project_version(ROOT / "pyproject.toml")
    assert version == "2.6.1"
    assert release_build_number(version) == 2_006_001
    assert release_build_number(version) > release_build_number("2.6.0")


def test_runtime_version_is_generated_from_the_single_authored_version(tmp_path):
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "2.2.11"\n', encoding="utf-8")
    output = tmp_path / "assets" / "release-version"

    assert write_runtime_version(project, output) == output
    assert output.read_bytes() == b"2.2.11\n"


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


def test_distribution_channel_asset_contract_is_deterministic(tmp_path):
    from scripts.release_metadata import (
        build_distribution_channel_payload,
        write_distribution_channel,
    )

    assert DEFAULT_ANDROID_PACKAGE_ID == "io.github.chouwzi.uthelper"
    target = write_distribution_channel(tmp_path / "assets" / "distribution-channel.json", "apk_lts")
    assert target.read_text(encoding="utf-8") == (
        '{\n  "android_package_id": "io.github.chouwzi.uthelper",\n'
        '  "channel": "apk_lts",\n  "schema_version": 1\n}\n'
    )
    assert build_distribution_channel_payload("EXE_LTS") == {
        "schema_version": 1,
        "channel": "exe_lts",
    }


def test_release_metadata_cli_writes_distribution_channel(tmp_path):
    import json
    import subprocess
    import sys

    output = tmp_path / "distribution-channel.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release_metadata.py"),
            "--channel",
            "chplay",
            "--channel-output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["channel"] == "chplay"

    rejected = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release_metadata.py"), "--channel", "chplay"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert rejected.returncode != 0
