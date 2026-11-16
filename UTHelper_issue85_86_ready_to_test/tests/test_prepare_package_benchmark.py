from pathlib import Path
import tomllib

import pytest

from scripts.prepare_package_benchmark import benchmark_configuration


@pytest.mark.parametrize("app,help_files", [(False, False), (True, False), (True, True)])
def test_benchmark_can_restore_baseline_and_candidates_without_changing_app(app, help_files):
    source = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    original = tomllib.loads(source)
    staged = tomllib.loads(
        benchmark_configuration(source, app_cleanup=app, help_cleanup=help_files)
    )
    expected = original
    expected["tool"]["flet"]["cleanup"]["app"] = app
    expected["tool"]["flet"]["windows"].setdefault("cleanup", {})["package_files"] = (
        ["**.chm"] if help_files else []
    )
    assert staged == expected
    assert staged["tool"]["flet"]["compile"] == {"app": True, "packages": True}
