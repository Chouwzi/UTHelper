"""Verified update discovery and download through published GitHub Releases."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
from typing import Callable, Optional, Tuple
import urllib.error
import urllib.request

from packaging.version import InvalidVersion, Version

import platform_utils

logger = logging.getLogger(__name__)

_GITHUB_OWNER = "Chouwzi"
_GITHUB_REPO = "UTHelper"
_RELEASES_URL = (
    f"https://api.github.com/repos/{_GITHUB_OWNER}/{_GITHUB_REPO}/releases/latest"
)
_UA = "UTHelper-UpdateChecker/2.2"
_MANIFEST_NAME = "release-manifest.json"
_DOWNLOAD_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class UpdateAsset:
    platform: str
    architecture: str
    name: str
    url: str
    sha256: str = ""
    size: int = 0


@dataclass(frozen=True)
class UpdateInfo:
    has_update: bool
    version: str | None = None
    release_url: str | None = None
    asset: UpdateAsset | None = None
    minimum_supported_version: str | None = None


_ASSET_METADATA: dict[str, UpdateAsset] = {}


def _platform_name() -> str:
    if platform_utils.IS_ANDROID:
        return "android"
    if platform_utils.IS_WINDOWS:
        return "windows"
    if platform_utils.IS_IOS:
        return "ios"
    return "other"


def _get_update_temp_dir() -> Path:
    directory = Path(tempfile.gettempdir()) / "uthelper_update"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _request_json(url: str, timeout: int = 10) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _UA,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    return value if isinstance(value, dict) else {}


def _is_newer(latest: str, current: str) -> bool:
    try:
        return Version(latest.lstrip("v")) > Version(current.lstrip("v"))
    except InvalidVersion:
        logger.warning("Invalid release version: latest=%r current=%r", latest, current)
        return False


def _asset_from_manifest(manifest: dict, platform_name: str) -> UpdateAsset | None:
    assets = manifest.get("assets", {})
    raw = assets.get(platform_name) if isinstance(assets, dict) else None
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url", ""))
    name = str(raw.get("name", ""))
    checksum = str(raw.get("sha256", "")).lower()
    if not url or not name or len(checksum) != 64:
        return None
    try:
        size = int(raw.get("size", 0) or 0)
    except (TypeError, ValueError):
        size = 0
    architecture = str(raw.get("architecture", ""))
    if not architecture:
        return None
    return UpdateAsset(platform_name, architecture, name, url, checksum, size)


def _asset_from_release(release_assets: list, platform_name: str) -> UpdateAsset | None:
    expected_suffixes = {
        "android": (".apk",),
        "windows": (".appinstaller", ".msix"),
        "ios": (),
    }.get(platform_name, ())
    for suffix in expected_suffixes:
        for raw in release_assets:
            name = str(raw.get("name", ""))
            if not name.lower().endswith(suffix):
                continue
            digest = str(raw.get("digest", ""))
            checksum = digest.removeprefix("sha256:") if digest else ""
            return UpdateAsset(
                platform=platform_name,
                architecture="universal" if platform_name == "android" else "x64",
                name=name,
                url=str(raw.get("browser_download_url", "")),
                sha256=checksum if len(checksum) == 64 else "",
                size=int(raw.get("size", 0) or 0),
            )
    return None


def get_update_info(current_version: str) -> UpdateInfo:
    """Return a typed update result for the current runtime platform."""
    try:
        release = _request_json(_RELEASES_URL)
        if release.get("draft") or release.get("prerelease"):
            return UpdateInfo(False)
        latest = str(release.get("tag_name", "")).lstrip("v")
        if not latest:
            return UpdateInfo(False)

        platform_name = _platform_name()
        release_assets = release.get("assets", [])
        manifest_asset = next(
            (
                item
                for item in release_assets
                if item.get("name") == _MANIFEST_NAME
            ),
            None,
        )
        manifest = {}
        if manifest_asset:
            manifest = _request_json(str(manifest_asset["browser_download_url"]))
            manifest_version = str(manifest.get("version", "")).lstrip("v")
            if manifest_version != latest:
                logger.warning(
                    "Ignoring release manifest with mismatched version %r", manifest_version
                )
                manifest = {}

        asset = _asset_from_manifest(manifest, platform_name)
        if asset is None:
            asset = _asset_from_release(release_assets, platform_name)
        if asset:
            _ASSET_METADATA[asset.url] = asset

        return UpdateInfo(
            has_update=_is_newer(latest, current_version),
            version=latest,
            release_url=str(release.get("html_url", "")),
            asset=asset,
            minimum_supported_version=(
                str(manifest.get("minimum_supported_version"))
                if manifest.get("minimum_supported_version")
                else None
            ),
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            logger.warning("Update check HTTP error: %s", exc)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Update check failed: %s", exc)
    return UpdateInfo(False)


def check_for_update(
    current_version: str,
) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """Backward-compatible tuple wrapper around :func:`get_update_info`."""
    info = get_update_info(current_version)
    return (
        info.has_update,
        info.version,
        info.release_url,
        info.asset.url if info.asset else None,
    )


def download_update(
    url: str,
    progress_cb: Callable[[float], None] | None = None,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
) -> Optional[Path]:
    """Download atomically and reject assets that fail size/checksum validation."""
    if not url:
        return None
    metadata = _ASSET_METADATA.get(url)
    expected_sha256 = (expected_sha256 or (metadata.sha256 if metadata else "")).lower()
    expected_size = expected_size or (metadata.size if metadata else 0)

    try:
        filename = url.split("/")[-1] or "update"
        destination = _get_update_temp_dir() / filename
        partial = destination.with_suffix(destination.suffix + ".part")
        partial.unlink(missing_ok=True)

        request = urllib.request.Request(url, headers={"User-Agent": _UA})
        digest = hashlib.sha256()
        downloaded = 0
        with urllib.request.urlopen(request, timeout=120) as response:
            response_size = int(response.headers.get("Content-Length", 0) or 0)
            total_size = expected_size or response_size
            with partial.open("wb") as output:
                while chunk := response.read(_DOWNLOAD_CHUNK_SIZE):
                    output.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total_size:
                        progress_cb(min(1.0, downloaded / total_size))

        if expected_size and downloaded != expected_size:
            raise ValueError(
                f"Update size mismatch: expected {expected_size}, got {downloaded}"
            )
        actual_sha256 = digest.hexdigest()
        if expected_sha256 and actual_sha256 != expected_sha256:
            raise ValueError("Update SHA-256 mismatch")

        os.replace(partial, destination)
        logger.info(
            "Downloaded verified update: %s (%d bytes, sha256=%s)",
            destination.name,
            downloaded,
            actual_sha256,
        )
        return destination
    except Exception as exc:
        logger.error("Download failed: %s", exc)
        try:
            partial.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
        return None


def check_for_update_async(current_version: str, callback) -> None:
    def _worker():
        callback(*check_for_update(current_version))

    threading.Thread(target=_worker, daemon=True, name="update-checker").start()


def download_update_async(url: str, progress_cb=None, done_cb=None) -> None:
    def _worker():
        result = download_update(url, progress_cb)
        if done_cb:
            done_cb(result)

    threading.Thread(target=_worker, daemon=True, name="update-downloader").start()
