"""Canonical release version and platform build-number derivation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import tomllib


_NUMERIC_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
DEFAULT_ANDROID_PACKAGE_ID = "com.uthelper.uthelper"
VALID_DISTRIBUTION_CHANNELS = frozenset(
    {
        "chplay",
        "ios_app_store",
        "ios_lts",
        "apk_lts",
        "msstore",
        "exe_lts",
        "source",
    }
)
_ANDROID_PACKAGE_ID = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$"
)
_MSSTORE_PRODUCT_ID = re.compile(r"^[A-Za-z0-9]{8,32}$")


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


def write_runtime_version(pyproject: Path, output_path: Path) -> Path:
    """Atomically generate the version asset bundled by Flet."""
    version = read_project_version(pyproject)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.{secrets.token_hex(8)}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(f"{version}\n".encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    except OSError as exc:
        raise ReleaseMetadataError("cannot write runtime release version") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return output


def build_distribution_channel_payload(
    channel: str,
    *,
    android_package_id: str | None = None,
    store_product_id: str | None = None,
) -> dict[str, object]:
    normalized = str(channel).strip().lower()
    if normalized not in VALID_DISTRIBUTION_CHANNELS:
        raise ReleaseMetadataError(f"unsupported distribution channel: {channel!r}")

    resolved_package_id: str | None = None
    if android_package_id is not None and str(android_package_id).strip():
        candidate_pkg = str(android_package_id).strip()
        if not _ANDROID_PACKAGE_ID.fullmatch(candidate_pkg):
            raise ReleaseMetadataError(f"invalid android_package_id: {android_package_id!r}")
        resolved_package_id = candidate_pkg
    elif normalized in {"chplay", "apk_lts"}:
        resolved_package_id = DEFAULT_ANDROID_PACKAGE_ID

    resolved_product_id: str | None = None
    if store_product_id is not None and str(store_product_id).strip():
        candidate_pid = str(store_product_id).strip()
        if not _MSSTORE_PRODUCT_ID.fullmatch(candidate_pid):
            raise ReleaseMetadataError(f"invalid store_product_id: {store_product_id!r}")
        resolved_product_id = candidate_pid

    payload: dict[str, object] = {
        "schema_version": 1,
        "channel": normalized,
    }
    if resolved_package_id is not None:
        payload["android_package_id"] = resolved_package_id
    if resolved_product_id is not None:
        payload["store_product_id"] = resolved_product_id
    return payload


def write_distribution_channel(
    output_path: Path,
    channel: str,
    *,
    android_package_id: str | None = None,
    store_product_id: str | None = None,
) -> Path:
    """Atomically generate ``distribution-channel.json`` bundled by Flet/MSIX builds."""
    payload = build_distribution_channel_payload(
        channel,
        android_package_id=android_package_id,
        store_product_id=store_product_id,
    )
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.{secrets.token_hex(8)}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    except OSError as exc:
        raise ReleaseMetadataError("cannot write distribution channel config") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--print-version", action="store_true")
    parser.add_argument("--runtime-output", type=Path)
    parser.add_argument("--channel")
    parser.add_argument("--channel-output", type=Path)
    parser.add_argument("--android-package-id")
    parser.add_argument("--store-product-id")
    parser.add_argument("--tag")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if args.channel_output is not None or args.channel is not None:
        if args.channel_output is None or not args.channel:
            parser.error("--channel and --channel-output are required together")
        if args.runtime_output or args.print_version or args.tag or args.github_output:
            parser.error("--channel-output cannot be combined with other output modes")
        write_distribution_channel(
            args.channel_output,
            args.channel,
            android_package_id=args.android_package_id,
            store_product_id=args.store_product_id,
        )
        return
    if args.runtime_output is not None:
        if args.print_version or args.tag or args.github_output:
            parser.error("--runtime-output cannot be combined with other output modes")
        write_runtime_version(args.pyproject, args.runtime_output)
        return
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
