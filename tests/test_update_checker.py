import hashlib
import io
import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import update_checker
from core.update_models import ReleasePackage, RuntimeTarget


def _release(**overrides):
    value = {
        "tag_name": "v2.2.0",
        "html_url": "https://github.com/Chouwzi/UTHelper/releases/tag/v2.2.0",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "release-manifest.json",
                "browser_download_url": (
                    "https://github.com/Chouwzi/UTHelper/releases/download/"
                    "v2.2.0/release-manifest.json"
                ),
                "size": 100,
            },
            {
                "name": "UTHelper-2.2.0.apk",
                "browser_download_url": (
                    "https://github.com/Chouwzi/UTHelper/releases/download/"
                    "v2.2.0/UTHelper-2.2.0.apk"
                ),
                "size": 3,
                "digest": "sha256:" + "a" * 64,
            },
        ],
    }
    value.update(overrides)
    return value


def test_manifest_selects_exact_platform_asset_and_semver():
    manifest = {
        "schema_version": 2,
        "release_version": "2.2.0",
        "minimum_supported_version": "2.0.0",
        "published_at": "2026-08-04T00:00:00Z",
        "release_notes_url": (
            "https://github.com/Chouwzi/UTHelper/releases/tag/v2.2.0"
        ),
        "packages": [
            {
                "platform": "android",
                "architecture": "universal",
                "package_type": "apk",
                "install_channel": "sideload",
                "url": (
                    "https://github.com/Chouwzi/UTHelper/releases/download/"
                    "v2.2.0/UTHelper-2.2.0.apk"
                ),
                "sha256": "b" * 64,
                "size": 123,
                "signer_identity": "CN=UTHelper Android",
                "certificate_fingerprint": "c" * 64,
                "install_strategy": {"kind": "android_package_installer"},
            }
        ],
    }
    with (
        patch.object(update_checker.platform_utils, "IS_ANDROID", True),
        patch.object(update_checker.platform_utils, "IS_WINDOWS", False),
        patch.object(
            update_checker,
            "_request_json",
            side_effect=[_release(), manifest],
        ),
    ):
        info = update_checker.get_update_info("2.1.9")

    assert info.has_update
    assert info.minimum_supported_version == "2.0.0"
    assert info.asset and info.asset.platform == "android"
    assert info.asset.architecture == "universal"
    assert info.asset.sha256 == "b" * 64
    assert update_checker.get_update_asset(info.asset.url) == info.asset


def test_draft_and_prerelease_are_not_offered():
    for field in ("draft", "prerelease"):
        with patch.object(
            update_checker, "_request_json", return_value=_release(**{field: True})
        ):
            assert not update_checker.get_update_info("2.1.0").has_update


def test_manifest_version_mismatch_never_falls_back_to_release_digest():
    manifest = {"schema": 1, "version": "9.9.9", "assets": {}}
    with (
        patch.object(update_checker.platform_utils, "IS_ANDROID", True),
        patch.object(update_checker.platform_utils, "IS_WINDOWS", False),
        patch.object(
            update_checker,
            "_request_json",
            side_effect=[_release(), manifest],
        ),
    ):
        info = update_checker.get_update_info("2.1.0")
    assert not info.has_update
    assert info.asset is None


class _Response:
    def __init__(self, data):
        self._stream = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self._stream.read(size)


class _ChunkedResponse(_Response):
    def __init__(self, chunks, after_first=None):
        self._chunks = iter(chunks)
        self._after_first = after_first
        self._read_count = 0
        self.headers = {}

    def read(self, _size=-1):
        try:
            chunk = next(self._chunks)
        except StopIteration:
            return b""
        self._read_count += 1
        if self._read_count == 1 and self._after_first:
            self._after_first()
        return chunk


def _release_package(data: bytes) -> ReleasePackage:
    return ReleasePackage(
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        url=(
            "https://github.com/Chouwzi/UTHelper/releases/download/"
            "v2.2.0/UTHelper-2.2.0.apk"
        ),
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        signer_identity="CN=UTHelper Android",
        certificate_fingerprint="c" * 64,
        install_strategy={"kind": "android_package_installer"},
    )


def test_release_client_passes_finite_timeout_to_every_request(monkeypatch):
    seen = []
    monkeypatch.setattr(
        update_checker.urllib.request,
        "urlopen",
        lambda request, timeout: seen.append(timeout) or _Response(b"{}"),
    )

    assert (
        update_checker.GitHubReleaseClient().fetch_candidate(
            "2.1.0",
            RuntimeTarget("windows", "x64", "msi"),
        )
        is None
    )
    assert seen and all(value == 20 for value in seen)


def test_downloader_cancels_and_removes_partial_file(tmp_path, monkeypatch):
    cancel = threading.Event()
    response = _ChunkedResponse([b"first", b"second"], after_first=cancel.set)
    monkeypatch.setattr(
        update_checker.urllib.request,
        "urlopen",
        lambda request, timeout: response,
    )
    downloader = update_checker.VerifiedDownloader(
        cache_dir=tmp_path,
        total_timeout_seconds=180,
    )

    with pytest.raises(update_checker.DownloadCancelled):
        downloader.download(
            _release_package(b"firstsecond"),
            cancel=cancel,
        )

    assert list(tmp_path.glob("*.part")) == []
    assert list(tmp_path.glob("*.apk")) == []


def test_downloader_rejects_size_and_checksum_before_atomic_rename(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        update_checker.urllib.request,
        "urlopen",
        lambda request, timeout: _Response(b"tampered"),
    )

    with pytest.raises(update_checker.PackageIntegrityError, match="SHA-256"):
        update_checker.VerifiedDownloader(cache_dir=tmp_path).download(
            _release_package(b"expected"),
            cancel=threading.Event(),
        )

    assert not any(tmp_path.iterdir())


def test_download_update_verifies_checksum_and_uses_atomic_destination(tmp_path):
    data = b"verified apk"
    checksum = hashlib.sha256(data).hexdigest()
    with (
        patch.object(update_checker, "_get_update_temp_dir", return_value=tmp_path),
        patch.object(
            update_checker.urllib.request,
            "urlopen",
            return_value=_Response(data),
        ),
    ):
        path = update_checker.download_update(
            (
                "https://github.com/Chouwzi/UTHelper/releases/download/"
                "v2.2.0/UTHelper.apk"
            ),
            expected_sha256=checksum,
            expected_size=len(data),
        )
    assert path == Path(tmp_path) / "UTHelper.apk"
    assert path.read_bytes() == data
    assert not (Path(tmp_path) / "UTHelper.apk.part").exists()


def test_download_update_rejects_bad_checksum(tmp_path):
    with (
        patch.object(update_checker, "_get_update_temp_dir", return_value=tmp_path),
        patch.object(
            update_checker.urllib.request,
            "urlopen",
            return_value=_Response(b"tampered"),
        ),
    ):
        assert update_checker.download_update(
            (
                "https://github.com/Chouwzi/UTHelper/releases/download/"
                "v2.2.0/UTHelper.apk"
            ),
            expected_sha256="0" * 64,
            expected_size=len(b"tampered"),
        ) is None
    assert not (Path(tmp_path) / "UTHelper.apk.part").exists()
