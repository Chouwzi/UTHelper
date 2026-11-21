"""Every native application target must ship the quiz control's Flutter side."""

from pathlib import Path
import tomllib

import pytest


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
