"""Inspect native packages and measure archive reads, never install or rewrite them."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import io
import json
from pathlib import Path
import platform
import statistics
import time
import zipfile


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    entries = archive.infolist()
    names = [item.filename for item in entries]
    if len(names) != len(set(names)):
        raise ValueError("archive contains duplicate entry names")
    if sum(item.file_size for item in entries) > 2 * 1024**3:
        raise ValueError("archive exceeds the 2 GiB measurement budget")
    return entries


def inspect_package(path: Path, *, read_repeats: int = 0) -> dict:
    path = Path(path)
    record = {
        "schema_version": 1,
        "artifact": path.name,
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
        "host": {"system": platform.system(), "machine": platform.machine()},
        "scope": "archive structure and sequential reads; not installation or frame timing",
    }
    groups = defaultdict(lambda: {"stored_bytes": 0, "expanded_bytes": 0, "files": 0})
    with zipfile.ZipFile(path) as archive:
        entries = _entries(archive)
        record["expanded_bytes"] = sum(item.file_size for item in entries)
        record["entry_count"] = len(entries)
        record["native_abis"] = sorted({
            item.filename.split("/")[1] for item in entries
            if item.filename.startswith("lib/") and item.filename.endswith(".so")
        })
        record["python_archives"] = []
        for item in entries:
            parts = item.filename.split("/")
            key = "/".join(parts[:2] if path.suffix == ".apk" else parts[:4])
            groups[key]["stored_bytes"] += item.compress_size
            groups[key]["expanded_bytes"] += item.file_size
            groups[key]["files"] += 1
            if parts[-1] in {"app.zip", "sitepackages.zip", "stdlib.zip"}:
                if item.file_size > 256 * 1024**2:
                    raise ValueError("nested Python archive exceeds measurement budget")
                with zipfile.ZipFile(io.BytesIO(archive.read(item))) as nested:
                    inner = _entries(nested)
                    record["python_archives"].append({
                        "name": item.filename,
                        "stored_bytes": item.compress_size,
                        "expanded_bytes": sum(entry.file_size for entry in inner),
                        "entry_count": len(inner),
                        "cache_directory_bytes": sum(
                            entry.file_size for entry in inner
                            if "__pycache__" in entry.filename.split("/")
                        ),
                    })
        record["groups"] = dict(sorted(groups.items()))
        record["largest_files"] = [
            {"name": item.filename, "stored_bytes": item.compress_size,
             "expanded_bytes": item.file_size, "zip_method": item.compress_type}
            for item in sorted(entries, key=lambda entry: entry.compress_size, reverse=True)[:20]
        ]
        if read_repeats:
            elapsed = []
            for _ in range(read_repeats):
                start = time.perf_counter()
                for item in entries:
                    with archive.open(item) as stream:
                        while stream.read(1024 * 1024):
                            pass  # ZipExtFile verifies CRC on complete reads.
                elapsed.append(time.perf_counter() - start)
            record["archive_read"] = {
                "scope": "host sequential decompression with CRC; warm-cache, not OS install",
                "seconds": elapsed,
                "median_seconds": statistics.median(elapsed),
            }
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--read-repeats", type=int, choices=range(0, 6), default=3)
    args = parser.parse_args()
    record = inspect_package(args.package, read_repeats=args.read_repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: record[key] for key in ("artifact", "bytes", "expanded_bytes", "native_abis")}))


if __name__ == "__main__":
    main()
