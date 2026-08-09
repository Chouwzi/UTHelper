from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts.verify_android_release import AndroidVerificationError, verify_android_release


VERSION = "2.2.0"
BUILD_NUMBER = 2_002_000
PACKAGE_ID = "com.uthelper.uthelper"
FINGERPRINT = "AB" * 32
COMMIT_SHA = "1" * 40


def _sdk(tmp_path: Path) -> Path:
    sdk = tmp_path / "android-sdk"
    for path in (
        sdk / "build-tools" / "35.0.0" / ("apksigner.bat" if os.name == "nt" else "apksigner"),
        sdk / "cmdline-tools" / "latest" / "bin" / ("apkanalyzer.bat" if os.name == "nt" else "apkanalyzer"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("tool", encoding="utf-8")
    return sdk


def _fake_run(command, **kwargs):
    assert kwargs["timeout"] == 60
    assert kwargs["check"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    if "apksigner" in Path(command[0]).name:
        output = f"Signer #1 certificate SHA-256 digest: {FINGERPRINT}\n"
    elif "application-id" in command:
        output = f"{PACKAGE_ID}\n"
    elif "version-name" in command:
        output = f"{VERSION}\n"
    elif "version-code" in command:
        output = f"{BUILD_NUMBER}\n"
    else:
        receivers = "".join(
            f'<receiver android:name="com.example.{name}" />'
            for name in (
                "ScheduledNotificationReceiver",
                "ScheduledNotificationBootReceiver",
                "ActionBroadcastReceiver",
                "DeadlineAlarmReceiver",
                "RescheduleReceiver",
            )
        )
        output = (
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
            f"<application>{receivers}</application></manifest>"
        )
    return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")


def test_verify_android_release_emits_exact_native_evidence(tmp_path, monkeypatch):
    apk = tmp_path / f"UTHelper-{VERSION}.apk"
    apk.write_bytes(b"PK\x03\x04signed-apk")
    output = tmp_path / "evidence.json"
    monkeypatch.setattr(subprocess, "run", _fake_run)

    evidence = verify_android_release(
        apk=apk,
        version=VERSION,
        build_number=BUILD_NUMBER,
        package_id=PACKAGE_ID,
        certificate_sha256=FINGERPRINT.lower(),
        commit_sha=COMMIT_SHA,
        workflow_run_id="12345",
        output=output,
        android_home=_sdk(tmp_path),
    )

    assert json.loads(output.read_text(encoding="utf-8")) == evidence
    assert evidence["asset_name"] == apk.name
    assert evidence["schema_version"] == 2
    assert evidence["signature_kind"] == "apk-pinned"
    assert evidence["certificate_fingerprint"] == FINGERPRINT
    assert evidence["checks"] == [
        "apk_signature",
        "notification_receivers",
        "package_id",
        "sha256",
        "version_code",
        "version_name",
    ]


@pytest.mark.parametrize("bad_bytes", [b"", b"MZnot-an-apk", b"PK\x05\x06empty-zip"])
def test_verify_android_release_rejects_non_apk_magic(tmp_path, bad_bytes):
    apk = tmp_path / f"UTHelper-{VERSION}.apk"
    apk.write_bytes(bad_bytes)

    with pytest.raises(AndroidVerificationError, match="ZIP magic"):
        verify_android_release(
            apk=apk,
            version=VERSION,
            build_number=BUILD_NUMBER,
            package_id=PACKAGE_ID,
            certificate_sha256=FINGERPRINT,
            commit_sha=COMMIT_SHA,
            workflow_run_id="12345",
            output=tmp_path / "evidence.json",
            android_home=_sdk(tmp_path),
        )


def test_verify_android_release_rejects_wrong_signer(tmp_path, monkeypatch):
    apk = tmp_path / f"UTHelper-{VERSION}.apk"
    apk.write_bytes(b"PK\x03\x04signed-apk")

    def wrong_signer(command, **kwargs):
        result = _fake_run(command, **kwargs)
        if "apksigner" in Path(command[0]).name:
            result.stdout = f"Signer #1 certificate SHA-256 digest: {'CD' * 32}\n"
        return result

    monkeypatch.setattr(subprocess, "run", wrong_signer)
    with pytest.raises(AndroidVerificationError, match="certificate"):
        verify_android_release(
            apk=apk,
            version=VERSION,
            build_number=BUILD_NUMBER,
            package_id=PACKAGE_ID,
            certificate_sha256=FINGERPRINT,
            commit_sha=COMMIT_SHA,
            workflow_run_id="12345",
            output=tmp_path / "evidence.json",
            android_home=_sdk(tmp_path),
        )


@pytest.mark.parametrize(
    "certificate_line",
    (
        f"Signer #1 certificate SHA-256 digest: {FINGERPRINT}\n",
        f"V2 Signer: certificate SHA-256 digest: {FINGERPRINT.lower()}\n",
        f"V3.1 Signer: certificate SHA-256 digest: {FINGERPRINT}\n",
    ),
)
def test_verify_android_release_accepts_supported_apksigner_labels(
    tmp_path, monkeypatch, certificate_line
):
    apk = tmp_path / f"UTHelper-{VERSION}.apk"
    apk.write_bytes(b"PK\x03\x04signed-apk")

    def labelled_signer(command, **kwargs):
        result = _fake_run(command, **kwargs)
        if "apksigner" in Path(command[0]).name:
            result.stdout = certificate_line
        return result

    monkeypatch.setattr(subprocess, "run", labelled_signer)
    evidence = verify_android_release(
        apk=apk,
        version=VERSION,
        build_number=BUILD_NUMBER,
        package_id=PACKAGE_ID,
        certificate_sha256=FINGERPRINT,
        commit_sha=COMMIT_SHA,
        workflow_run_id="12345",
        output=tmp_path / "evidence.json",
        android_home=_sdk(tmp_path),
    )

    assert evidence["certificate_fingerprint"] == FINGERPRINT


def test_verify_android_release_rejects_any_additional_signer_identity(
    tmp_path, monkeypatch
):
    apk = tmp_path / f"UTHelper-{VERSION}.apk"
    apk.write_bytes(b"PK\x03\x04signed-apk")

    def mixed_signers(command, **kwargs):
        result = _fake_run(command, **kwargs)
        if "apksigner" in Path(command[0]).name:
            result.stdout = (
                f"V2 Signer: certificate SHA-256 digest: {FINGERPRINT}\n"
                f"V3 Signer: certificate SHA-256 digest: {'CD' * 32}\n"
            )
        return result

    monkeypatch.setattr(subprocess, "run", mixed_signers)
    with pytest.raises(AndroidVerificationError, match="certificate"):
        verify_android_release(
            apk=apk,
            version=VERSION,
            build_number=BUILD_NUMBER,
            package_id=PACKAGE_ID,
            certificate_sha256=FINGERPRINT,
            commit_sha=COMMIT_SHA,
            workflow_run_id="12345",
            output=tmp_path / "evidence.json",
            android_home=_sdk(tmp_path),
        )


def test_verify_android_release_rejects_noncanonical_build_number(tmp_path):
    apk = tmp_path / f"UTHelper-{VERSION}.apk"
    apk.write_bytes(b"PK\x03\x04signed-apk")

    with pytest.raises(AndroidVerificationError, match="canonical"):
        verify_android_release(
            apk=apk,
            version=VERSION,
            build_number=BUILD_NUMBER + 1,
            package_id=PACKAGE_ID,
            certificate_sha256=FINGERPRINT,
            commit_sha=COMMIT_SHA,
            workflow_run_id="12345",
            output=tmp_path / "evidence.json",
            android_home=_sdk(tmp_path),
        )


def test_verify_android_release_rejects_receiver_names_outside_receiver_elements(
    tmp_path, monkeypatch
):
    apk = tmp_path / f"UTHelper-{VERSION}.apk"
    apk.write_bytes(b"PK\x03\x04signed-apk")

    def misleading_manifest(command, **kwargs):
        result = _fake_run(command, **kwargs)
        if command[1:3] == ["manifest", "print"]:
            names = " ".join(
                (
                    "ScheduledNotificationReceiver",
                    "ScheduledNotificationBootReceiver",
                    "ActionBroadcastReceiver",
                    "DeadlineAlarmReceiver",
                    "RescheduleReceiver",
                )
            )
            result.stdout = (
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
                f'<application><meta-data android:value="{names}" /></application>'
                "</manifest>"
            )
        return result

    monkeypatch.setattr(subprocess, "run", misleading_manifest)
    with pytest.raises(AndroidVerificationError, match="receiver"):
        verify_android_release(
            apk=apk,
            version=VERSION,
            build_number=BUILD_NUMBER,
            package_id=PACKAGE_ID,
            certificate_sha256=FINGERPRINT,
            commit_sha=COMMIT_SHA,
            workflow_run_id="12345",
            output=tmp_path / "evidence.json",
            android_home=_sdk(tmp_path),
        )
