from __future__ import annotations

from pathlib import Path
import os
import stat
import subprocess

import pytest

from scripts.upload_ipa_release import UploadError, upload_ipa_release


def _inputs(tmp_path: Path):
    ipa = tmp_path / "UTHelper-2.2.0.ipa"
    ipa.write_bytes(b"PK\x03\x04ipa")
    key_dir = tmp_path / "transporter" / "private_keys"
    key_dir.mkdir(parents=True)
    key = key_dir / "AuthKey_ABC123.p8"
    key.write_text(
        "-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    key.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return ipa, key_dir


def test_upload_ipa_uses_bounded_transporter_without_private_key_argument(
    tmp_path, monkeypatch
):
    ipa, key_dir = _inputs(tmp_path)
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="UPLOAD SUCCEEDED", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("APPLE_API_PRIVATE_KEY_BASE64", "must-not-reach-transporter")
    upload_ipa_release(
        ipa=ipa,
        api_issuer="00000000-0000-0000-0000-000000000000",
        api_key_id="ABC123",
        private_keys_dir=key_dir,
    )

    assert captured["command"] == [
        "xcrun",
        "iTMSTransporter",
        "-m",
        "upload",
        "-apiIssuer",
        "00000000-0000-0000-0000-000000000000",
        "-apiKey",
        "ABC123",
        "-assetFile",
        str(ipa.resolve()),
        "-v",
        "critical",
    ]
    assert captured["kwargs"]["timeout"] == 1_200
    assert captured["kwargs"]["cwd"] == key_dir.parent
    assert captured["kwargs"]["env"]["ITMS_PRIVATE_KEYS_DIR"] == str(key_dir)
    assert "APPLE_API_PRIVATE_KEY_BASE64" not in captured["kwargs"]["env"]
    assert "private" not in " ".join(captured["command"]).lower()


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr"),
    ((1, "", "rejected"), (0, "ERROR upload rejected", ""), (0, "", "error")),
)
def test_upload_ipa_rejects_transporter_failure(
    tmp_path, monkeypatch, returncode, stdout, stderr
):
    ipa, key_dir = _inputs(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, returncode, stdout=stdout, stderr=stderr
        ),
    )

    with pytest.raises(UploadError, match="rejected"):
        upload_ipa_release(
            ipa=ipa,
            api_issuer="00000000-0000-0000-0000-000000000000",
            api_key_id="ABC123",
            private_keys_dir=key_dir,
        )


@pytest.mark.skipif(os.name != "posix", reason="Windows has no POSIX key-file modes")
def test_upload_ipa_requires_exact_private_key_location_and_mode(tmp_path):
    ipa, key_dir = _inputs(tmp_path)
    (key_dir / "AuthKey_ABC123.p8").chmod(stat.S_IRUSR | stat.S_IRGRP)

    with pytest.raises(UploadError, match="0600"):
        upload_ipa_release(
            ipa=ipa,
            api_issuer="00000000-0000-0000-0000-000000000000",
            api_key_id="ABC123",
            private_keys_dir=key_dir,
        )
