import hashlib
import io
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import update_checker


def _release(**overrides):
    value = {
        "tag_name": "v2.2.0",
        "html_url": "https://github.com/Chouwzi/UTHelper/releases/tag/v2.2.0",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "release-manifest.json",
                "browser_download_url": "https://example/manifest.json",
                "size": 100,
            },
            {
                "name": "UTHelper-2.2.0.apk",
                "browser_download_url": "https://example/app.apk",
                "size": 3,
                "digest": "sha256:" + "a" * 64,
            },
        ],
    }
    value.update(overrides)
    return value


def test_manifest_selects_exact_platform_asset_and_semver():
    manifest = {
        "version": "2.2.0",
        "minimum_supported_version": "2.0.0",
        "assets": {
            "android": {
                "architecture": "universal",
                "name": "UTHelper-2.2.0.apk",
                "url": "https://example/app.apk",
                "sha256": "b" * 64,
                "size": 123,
            }
        },
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


def test_manifest_version_mismatch_falls_back_to_release_digest():
    manifest = {"version": "9.9.9", "assets": {}}
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
    assert info.asset and info.asset.sha256 == "a" * 64


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
            "https://example/UTHelper.apk",
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
            "https://example/UTHelper.apk", expected_sha256="0" * 64
        ) is None
    assert not (Path(tmp_path) / "UTHelper.apk.part").exists()
