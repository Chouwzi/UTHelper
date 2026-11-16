"""Stage explicit cleanup settings in a disposable benchmark checkout."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import tomllib


def benchmark_configuration(source: str, *, app_cleanup: bool, help_cleanup: bool) -> str:
    original = tomllib.loads(source)
    expected = copy.deepcopy(original)
    flet = expected["tool"]["flet"]
    flet.setdefault("cleanup", {})["app"] = app_cleanup
    flet.setdefault("windows", {}).setdefault("cleanup", {})["package_files"] = (
        ["**.chm"] if help_cleanup else []
    )
    # Rewrite only the two authored cleanup tables. Reparse and compare the entire
    # document before writing, so dependencies, compilation and identity survive.
    for name, settings in (
        ("tool.flet.cleanup", flet["cleanup"]),
        ("tool.flet.windows.cleanup", flet["windows"]["cleanup"]),
    ):
        block = f"[{name}]\n" + "".join(
            f"{json.dumps(key)} = {json.dumps(value)}\n"
            for key, value in settings.items()
        )
        pattern = rf"(?ms)^\[{re.escape(name)}\][^\n]*\n.*?(?=^\[|\Z)"
        source, count = re.subn(pattern, lambda _: block + "\n", source)
        if count == 0:
            source = source.rstrip() + "\n\n" + block
    if tomllib.loads(source) != expected:
        raise ValueError("Benchmark staging changed settings outside cleanup")
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-cleanup", choices=("true", "false"), required=True)
    parser.add_argument("--help-cleanup", choices=("true", "false"), default="false")
    parser.add_argument("--project", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    source = args.project.read_text(encoding="utf-8")
    staged = benchmark_configuration(
        source,
        app_cleanup=args.app_cleanup == "true",
        help_cleanup=args.help_cleanup == "true",
    )
    args.evidence.mkdir(parents=True, exist_ok=True)
    (args.evidence / "pyproject-original.toml").write_text(source, encoding="utf-8")
    (args.evidence / "pyproject-benchmark.toml").write_text(staged, encoding="utf-8")
    args.project.write_text(staged, encoding="utf-8")


if __name__ == "__main__":
    main()
