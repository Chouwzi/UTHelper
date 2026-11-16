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
        patch.object(
            update_checker,
            "detect_runtime_target",
            return_value=RuntimeTarget("android", "universal", "sideload"),
        ),
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
        patch.object(
            update_checker,
            "detect_runtime_target",
            return_value=RuntimeTarget("android", "universal", "sideload"),
        ),
        patch.object(
            update_checker,
            "_request_json",
            side_effect=[_release(), manifest],
        ),
        pytest.raises(update_checker.UpdateDiscoveryError, match="metadata is invalid"),
    ):
        update_checker.get_update_info("2.1.0")


class _Response:
    def __init__(self, data, *, status=200, headers=None):
        self._stream = io.BytesIO(data)
        self.status = status
        self.headers = {"Content-Length": str(len(data))}
        self.headers.update(headers or {})

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
        self.status = 200

    def read(self, _size=-1):
        try:
            chunk = next(self._chunks)
        except StopIteration:
            return b""
        self._read_count += 1
        if self._read_count == 1 and self._after_first:
            self._after_first()
        return chunk


class _InterruptedResponse(_Response):
    def __init__(self, first_chunk):
        super().__init__(first_chunk)
        self._returned_chunk = False

    def read(self, _size=-1):
        if not self._returned_chunk:
            self._returned_chunk = True
            return self._stream.read()
        raise ConnectionResetError("simulated connection reset")


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


def test_release_client_reports_timeout_after_three_bounded_attempts(monkeypatch):
    attempts = []
    delays = []

    def timeout(*_args, **_kwargs):
        attempts.append("timeout")
        raise TimeoutError("simulated network timeout")

    monkeypatch.setattr(update_checker, "_request_json", timeout)
    monkeypatch.setattr(update_checker.time, "sleep", delays.append)

    with pytest.raises(update_checker.UpdateDiscoveryError, match="temporarily unavailable"):
        update_checker.GitHubReleaseClient().fetch_candidate(
            "2.1.0",
            RuntimeTarget("windows", "x64", "msi"),
        )

    assert attempts == ["timeout", "timeout", "timeout"]
    assert delays == [0.25, 0.5]


def test_release_client_retries_transient_failure_then_returns_candidate(monkeypatch):
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
    responses = iter([TimeoutError("temporary"), _release(), manifest])
    delays = []

    def request(*_args, **_kwargs):
        value = next(responses)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(update_checker, "_request_json", request)
    monkeypatch.setattr(update_checker.time, "sleep", delays.append)

    candidate = update_checker.GitHubReleaseClient().fetch_candidate(
        "2.1.0",
        RuntimeTarget("android", "universal", "sideload"),
    )

    assert candidate is not None
    assert candidate.manifest.release_version == "2.2.0"
    assert delays == [0.25]


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


def test_progress_throttler_bounds_events_and_keeps_endpoints():
    total = 46_882_816
    chunk = 64 * 1024
    now = [0.0]
    events = []
    throttled = update_checker.ProgressThrottler(
        lambda done, expected: events.append((done, expected)),
        total=total,
        clock=lambda: now[0],
    )

    for done in range(0, total + chunk, chunk):
        done = min(done, total)
        now[0] = done / (3.04 * 1024 * 1024)
        throttled(done)

    assert events[0] == (0, total)
    assert events[-1] == (total, total)
    assert len(events) <= 150
    assert len(events) < 0.3 * 717


def test_downloader_resumes_after_transient_disconnect(tmp_path, monkeypatch):
    data = b"firstsecond"
    package = _release_package(data)
    requests = []
    responses = iter(
        [
            _InterruptedResponse(b"first"),
            _Response(
                b"second",
                status=206,
                headers={"Content-Range": "bytes 5-10/11"},
            ),
        ]
    )

    def open_request(request, timeout):
        requests.append((request.get_header("Range"), timeout))
        return next(responses)

    monkeypatch.setattr(update_checker.urllib.request, "urlopen", open_request)
    monkeypatch.setattr(update_checker.time, "sleep", lambda _delay: None)

    path = update_checker.VerifiedDownloader(cache_dir=tmp_path).download(
        package,
        cancel=threading.Event(),
    )

    assert path.read_bytes() == data
    assert requests == [(None, 20), ("bytes=5-", 20)]
    assert not any(tmp_path.glob("*.part"))
    assert not any(tmp_path.glob("*.json"))


def test_downloader_restarts_safely_when_server_ignores_range(tmp_path, monkeypatch):
    data = b"firstsecond"
    package = _release_package(data)
    requests = []
    responses = iter([_InterruptedResponse(b"first"), _Response(data, status=200)])

    def open_request(request, timeout):
        requests.append(request.get_header("Range"))
        return next(responses)

    monkeypatch.setattr(update_checker.urllib.request, "urlopen", open_request)
    monkeypatch.setattr(update_checker.time, "sleep", lambda _delay: None)

    path = update_checker.VerifiedDownloader(cache_dir=tmp_path).download(
        package,
        cancel=threading.Event(),
    )

    assert path.read_bytes() == data
    assert requests == [None, "bytes=5-"]


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
