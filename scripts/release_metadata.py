"""Canonical release version and platform build-number derivation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import tomllib


_NUMERIC_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class ReleaseMetadataError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseMetadata:
    version: str
    tag: str
    build_number: int


def release_build_number(version: str) -> int:
    match = _NUMERIC_VERSION.fullmatch(str(version))
    if match is None:
        raise ReleaseMetadataError("release version must be numeric X.Y.Z")
    components = tuple(int(value) for value in match.groups())
    if any(value > 999 for value in components):
        raise ReleaseMetadataError("version components must be in 0..999")
    major, minor, patch = components
    return major * 1_000_000 + minor * 1_000 + patch


def read_project_version(pyproject: Path) -> str:
    try:
        document = tomllib.loads(Path(pyproject).read_text(encoding="utf-8"))
        version = document["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseMetadataError("cannot read project version") from exc
    if not isinstance(version, str):
        raise ReleaseMetadataError("project version must be numeric X.Y.Z")
    release_build_number(version)
    return version


def read_release_metadata(pyproject: Path, tag: str) -> ReleaseMetadata:
    version = read_project_version(pyproject)
    expected_tag = f"v{version}"
    if tag != expected_tag:
        raise ReleaseMetadataError("release tag must exactly match project version")
    return ReleaseMetadata(version, expected_tag, release_build_number(version))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--print-version", action="store_true")
    parser.add_argument("--tag")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if args.print_version:
        if args.tag or args.github_output:
            parser.error("--print-version cannot be combined with release output options")
        print(read_project_version(args.pyproject))
        return
    if not args.tag or args.github_output is None:
        parser.error("--tag and --github-output are required together")
    metadata = read_release_metadata(args.pyproject, args.tag)
    payload = (
        f"version={metadata.version}\n"
        f"tag={metadata.tag}\n"
        f"build_number={metadata.build_number}\n"
    )
    with args.github_output.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)


if __name__ == "__main__":
    main()
