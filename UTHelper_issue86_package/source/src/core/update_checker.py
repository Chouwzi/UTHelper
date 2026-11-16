"""Bounded GitHub discovery and verified atomic update downloads."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Callable, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request

from packaging.version import InvalidVersion, Version

from core.update_manifest import ManifestError, parse_manifest, select_candidate
from core.update_models import ReleasePackage, RuntimeTarget, UpdateCandidate
from platform_utils.update_packages import detect_runtime_target


logger = logging.getLogger(__name__)

_GITHUB_OWNER = "Chouwzi"
_GITHUB_REPO = "UTHelper"
_RELEASES_URL = (
    f"https://api.github.com/repos/{_GITHUB_OWNER}/{_GITHUB_REPO}/releases/latest"
)
_UA = "UTHelper-UpdateChecker/2.2"
_MANIFEST_NAME = "release-manifest.json"
_DOWNLOAD_CHUNK_SIZE = 64 * 1024
_SOCKET_TIMEOUT_SECONDS = 20
_MAX_JSON_BYTES = 1024 * 1024
_APPROVED_GITHUB_HOSTS = frozenset(
    {"github.com", "objects.githubusercontent.com"}
)
_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


class UpdateError(RuntimeError):
    """Base class for a bounded update operation failure."""


class DownloadCancelled(UpdateError):
    """The caller cooperatively cancelled an update download."""


class UpdateDiscoveryError(UpdateError):
    """The latest release could not be checked authoritatively."""


class PackageIntegrityError(UpdateError):
    """Downloaded bytes do not match the selected release package."""


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
_PACKAGE_METADATA: dict[str, ReleasePackage] = {}


def get_update_asset(url: str) -> UpdateAsset | None:
    """Return verified release metadata retained by the latest update check."""
    return _ASSET_METADATA.get(url)


def _get_update_temp_dir() -> Path:
    directory = Path(tempfile.gettempdir()) / "uthelper_update"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _request_json(url: str, timeout: int = _SOCKET_TIMEOUT_SECONDS) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _UA,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(_MAX_JSON_BYTES + 1)
    if len(payload) > _MAX_JSON_BYTES:
        raise ValueError("update response exceeds size limit")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("update response must be an object")
    return value


def _is_newer(latest: str, current: str) -> bool:
    try:
        return Version(latest.lstrip("v")) > Version(current.lstrip("v"))
    except InvalidVersion:
        logger.warning("Invalid release version")
        return False


def _approved_github_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ManifestError("release asset URL is invalid")
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ManifestError("release asset URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _APPROVED_GITHUB_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        raise ManifestError("release asset URL is not an approved GitHub URL")
    return value


class GitHubReleaseClient:
    """Discover exactly one manifest from the latest stable GitHub release."""

    def __init__(
        self,
        releases_url: str = _RELEASES_URL,
        socket_timeout_seconds: int = _SOCKET_TIMEOUT_SECONDS,
    ) -> None:
        self.releases_url = releases_url
        self.socket_timeout_seconds = int(socket_timeout_seconds)
        if self.socket_timeout_seconds <= 0 or self.socket_timeout_seconds > 60:
            raise ValueError("update socket timeout is invalid")

    def fetch_candidate(
        self,
        current_version: str,
        target: RuntimeTarget,
    ) -> UpdateCandidate | None:
        try:
            release = self._request_json_with_retry(self.releases_url)
            if release.get("draft") is True or release.get("prerelease") is True:
                return None
            tag = release.get("tag_name")
            if not isinstance(tag, str) or not tag.startswith("v"):
                return None
            release_version = tag[1:]
            try:
                parsed_version = Version(release_version)
            except InvalidVersion:
                return None
            if str(parsed_version) != release_version:
                return None
            assets = release.get("assets")
            if not isinstance(assets, list):
                return None
            manifest_assets = [
                item
                for item in assets
                if isinstance(item, dict) and item.get("name") == _MANIFEST_NAME
            ]
            if len(manifest_assets) != 1:
                return None
            manifest_url = _approved_github_url(
                manifest_assets[0].get("browser_download_url")
            )
            document = self._request_json_with_retry(manifest_url)
            manifest = parse_manifest(
                document,
                expected_release_version=release_version,
            )
            return select_candidate(
                manifest,
                current_version=current_version,
                target=target,
            )
        except UpdateDiscoveryError:
            raise
        except (
            ManifestError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            logger.warning("Update check rejected an invalid release")
            raise UpdateDiscoveryError("update release metadata is invalid") from exc

    def _request_json_with_retry(self, url: str) -> dict:
        for attempt in range(3):
            try:
                return _request_json(url, timeout=self.socket_timeout_seconds)
            except urllib.error.HTTPError as exc:
                transient = exc.code in {408, 429} or 500 <= exc.code <= 599
                if not transient:
                    raise UpdateDiscoveryError(
                        f"update service returned HTTP {exc.code}"
                    ) from exc
                error: BaseException = exc
            except (OSError, urllib.error.URLError) as exc:
                error = exc
            if attempt == 2:
                raise UpdateDiscoveryError(
                    "update service is temporarily unavailable"
                ) from error
            time.sleep(0.25 * (2**attempt))
        raise AssertionError("unreachable update discovery retry state")


class ProgressThrottler:
    """Coalesce byte progress while preserving the first and final events."""

    def __init__(
        self,
        callback: Callable[[int, int], None],
        *,
        total: int,
        clock: Callable[[], float] = time.monotonic,
        minimum_interval_seconds: float = 0.1,
        minimum_fraction: float = 0.01,
    ) -> None:
        self._callback = callback
        self._total = int(total)
        self._clock = clock
        self._minimum_interval = float(minimum_interval_seconds)
        self._minimum_bytes = max(1, math.ceil(self._total * minimum_fraction))
        self._last_done: int | None = None
        self._last_emitted_at = float("-inf")

    def __call__(self, done: int) -> None:
        bounded = min(self._total, max(0, int(done)))
        now = self._clock()
        should_emit = (
            self._last_done is None
            or bounded == self._total
            or bounded < self._last_done
            or bounded - self._last_done >= self._minimum_bytes
            or now - self._last_emitted_at >= self._minimum_interval
        )
        if not should_emit:
            return
        self._callback(bounded, self._total)
        self._last_done = bounded
        self._last_emitted_at = now


class VerifiedDownloader:
    """Download one selected package with cancellation and atomic replacement."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        total_timeout_seconds: float = 180.0,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else _get_update_temp_dir()
        timeout = float(total_timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 180:
            raise ValueError("update total timeout must be within 180 seconds")
        self.total_timeout_seconds = timeout

    @staticmethod
    def _filename(package: ReleasePackage) -> str:
        parsed = urllib.parse.urlsplit(package.url)
        filename = urllib.parse.unquote(Path(parsed.path).name)
        if (
            not filename
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or len(filename) > 255
        ):
            raise PackageIntegrityError("update filename is invalid")
        return filename

    def download(
        self,
        package: ReleasePackage,
        *,
        cancel: threading.Event,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        if not isinstance(package, ReleasePackage):
            raise TypeError("package must be ReleasePackage")
        if not isinstance(cancel, threading.Event):
            raise TypeError("cancel must be threading.Event")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if not self.cache_dir.is_dir():
            raise PackageIntegrityError("update cache is unavailable")
        destination = self.cache_dir / self._filename(package)
        partial = destination.with_suffix(destination.suffix + ".part")
        sidecar = partial.with_suffix(partial.suffix + ".json")
        expected_state = {
            "url": package.url,
            "sha256": package.sha256.lower(),
            "size": package.size,
        }
        self._prepare_partial(partial, sidecar, expected_state, package.size)
        throttled_progress = (
            ProgressThrottler(progress, total=package.size)
            if progress is not None
            else None
        )
        if throttled_progress is not None:
            throttled_progress(partial.stat().st_size if partial.exists() else 0)
        started = time.monotonic()
        try:
            if cancel.is_set():
                raise DownloadCancelled("download cancelled")
            self._write_sidecar(sidecar, expected_state)
            for attempt in range(3):
                try:
                    self._download_attempt(
                        package,
                        partial,
                        cancel=cancel,
                        started=started,
                        progress=throttled_progress,
                    )
                    break
                except (OSError, urllib.error.URLError, TimeoutError):
                    if cancel.is_set():
                        raise DownloadCancelled("download cancelled")
                    if attempt == 2:
                        raise
                    time.sleep(0.25 * (2**attempt))
            self._verify_complete_partial(partial, package)
            os.replace(partial, destination)
            sidecar.unlink(missing_ok=True)
            if throttled_progress is not None:
                throttled_progress(package.size)
            return destination
        except DownloadCancelled:
            self._remove_partial(partial, sidecar)
            raise
        except PackageIntegrityError:
            self._remove_partial(partial, sidecar)
            raise
        except BaseException:
            if not partial.exists():
                sidecar.unlink(missing_ok=True)
            raise

    @staticmethod
    def _prepare_partial(
        partial: Path,
        sidecar: Path,
        expected_state: dict,
        expected_size: int,
    ) -> None:
        if not partial.exists():
            sidecar.unlink(missing_ok=True)
            return
        try:
            state = json.loads(sidecar.read_text(encoding="utf-8"))
            reusable = (
                not partial.is_symlink()
                and partial.is_file()
                and partial.stat().st_size < expected_size
                and state == expected_state
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            reusable = False
        if not reusable:
            VerifiedDownloader._remove_partial(partial, sidecar)

    @staticmethod
    def _write_sidecar(sidecar: Path, state: dict) -> None:
        temporary = sidecar.with_suffix(sidecar.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, sidecar)

    def _download_attempt(
        self,
        package: ReleasePackage,
        partial: Path,
        *,
        cancel: threading.Event,
        started: float,
        progress: ProgressThrottler | None,
    ) -> None:
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": _UA}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(package.url, headers=headers)
        with urllib.request.urlopen(
            request,
            timeout=_SOCKET_TIMEOUT_SECONDS,
        ) as response:
            status = int(getattr(response, "status", 200))
            if offset and status == 206:
                self._validate_content_range(response, offset, package.size)
                mode = "ab"
                downloaded = offset
            elif offset and status == 200:
                mode = "wb"
                downloaded = 0
            elif not offset and status == 200:
                mode = "wb"
                downloaded = 0
            else:
                raise PackageIntegrityError("update range response is invalid")
            with partial.open(mode) as output:
                while True:
                    if cancel.is_set():
                        raise DownloadCancelled("download cancelled")
                    if time.monotonic() - started > self.total_timeout_seconds:
                        raise TimeoutError("update download exceeded total deadline")
                    chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > package.size:
                        raise PackageIntegrityError("download exceeds manifest size")
                    if progress is not None:
                        progress(downloaded)
                output.flush()
                os.fsync(output.fileno())

    @staticmethod
    def _validate_content_range(response, offset: int, total: int) -> None:
        value = str(response.headers.get("Content-Range", ""))
        match = _CONTENT_RANGE.fullmatch(value)
        if (
            match is None
            or int(match.group(1)) != offset
            or int(match.group(2)) < offset
            or int(match.group(3)) != total
        ):
            raise PackageIntegrityError("update content range is invalid")

    @staticmethod
    def _verify_complete_partial(partial: Path, package: ReleasePackage) -> None:
        if partial.stat().st_size != package.size:
            raise PackageIntegrityError("update size mismatch")
        digest = hashlib.sha256()
        with partial.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest().lower() != package.sha256.lower():
            raise PackageIntegrityError("update SHA-256 mismatch")

    @staticmethod
    def _remove_partial(partial: Path, sidecar: Path) -> None:
        for path in (partial, sidecar, sidecar.with_suffix(sidecar.suffix + ".tmp")):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _asset_for_package(package: ReleasePackage) -> UpdateAsset:
    return UpdateAsset(
        platform=package.platform,
        architecture=package.architecture,
        name=urllib.parse.unquote(Path(urllib.parse.urlsplit(package.url).path).name),
        url=package.url,
        sha256=package.sha256,
        size=package.size,
    )


def get_update_info(current_version: str) -> UpdateInfo:
    """Return a compatibility result backed only by a parsed manifest."""
    candidate = GitHubReleaseClient().fetch_candidate(
        current_version,
        detect_runtime_target(),
    )
    if candidate is None:
        return UpdateInfo(False)
    asset = None
    if candidate.automatic_install_allowed:
        asset = _asset_for_package(candidate.package)
        _ASSET_METADATA[asset.url] = asset
        _PACKAGE_METADATA[asset.url] = candidate.package
    return UpdateInfo(
        has_update=True,
        version=candidate.manifest.release_version,
        release_url=candidate.manifest.release_notes_url,
        asset=asset,
        minimum_supported_version=candidate.manifest.minimum_supported_version,
    )


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
    """Compatibility adapter over :class:`VerifiedDownloader`."""
    if not url:
        return None
    package = _PACKAGE_METADATA.get(url)
    if package is None:
        checksum = str(expected_sha256 or "").lower()
        size = int(expected_size or 0)
        if len(checksum) != 64 or size <= 0:
            return None
        package = ReleasePackage(
            platform="legacy",
            architecture="unknown",
            package_type=Path(urllib.parse.urlsplit(url).path).suffix.lstrip("."),
            install_channel="legacy",
            url=url,
            sha256=checksum,
            size=size,
            signer_identity="legacy-adapter",
            certificate_fingerprint="0" * 64,
            install_strategy={"kind": "legacy-adapter"},
        )

    def progress(downloaded: int, total: int) -> None:
        if progress_cb is not None and total > 0:
            progress_cb(min(1.0, downloaded / total))

    try:
        return VerifiedDownloader(cache_dir=_get_update_temp_dir()).download(
            package,
            cancel=threading.Event(),
            progress=progress,
        )
    except (DownloadCancelled, OSError, TimeoutError, UpdateError, ValueError) as exc:
        logger.error("Download failed: %s", type(exc).__name__)
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


__all__ = [
    "DownloadCancelled",
    "GitHubReleaseClient",
    "PackageIntegrityError",
    "ProgressThrottler",
    "UpdateDiscoveryError",
    "UpdateAsset",
    "UpdateInfo",
    "VerifiedDownloader",
    "check_for_update",
    "check_for_update_async",
    "download_update",
    "download_update_async",
    "get_update_asset",
    "get_update_info",
]
