from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_release_credentials.py"

REQUIRED_INPUTS = {
    "ANDROID_KEYSTORE_BASE64": "c2lnbmluZw==",
    "ANDROID_KEYSTORE_PASSWORD": "android-store-password",
    "ANDROID_KEY_PASSWORD": "android-key-password",
    "ANDROID_KEY_ALIAS": "uthelper",
    "ANDROID_SIGNING_CERT_SHA256": "A" * 64,
    "APPLE_CERTIFICATE_P12_BASE64": "c2lnbmluZw==",
    "APPLE_CERTIFICATE_PASSWORD": "apple-password",
    "APPLE_PROVISIONING_PROFILE_BASE64": "c2lnbmluZw==",
    "APPLE_API_PRIVATE_KEY_BASE64": "c2lnbmluZw==",
    "APPLE_TEAM_ID": "A1B2C3D4E5",
    "APPLE_SIGNING_IDENTITY": "Apple Distribution: UTHelper",
    "APPLE_SIGNING_CERT_SHA256": "B" * 64,
    "APPLE_API_ISSUER_ID": "00000000-0000-0000-0000-000000000000",
    "APPLE_API_KEY_ID": "A1B2C3D4E5",
    "IOS_DISTRIBUTION_URL": "https://testflight.apple.com/join/example",
    "WINDOWS_PFX_BASE64": "c2lnbmluZw==",
    "WINDOWS_PFX_PASSWORD": "windows-password",
    "WINDOWS_SIGNING_CERT_SHA256": "C" * 64,
    "WINDOWS_SIGNER_SUBJECT": "CN=UTHelper",
    "WINDOWS_TIMESTAMP_URL": "https://timestamp.example.com",
    "WIX_EULA_ACCEPTED": "wix7",
    "SENTRY_DSN": "https://public@example.ingest.sentry.io/1",
}


def _run(overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in REQUIRED_INPUTS
    }
    env.update(REQUIRED_INPUTS)
    if overrides:
        env.update(overrides)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def test_complete_release_input_inventory_passes_preflight():
    result = _run()

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Release credential preflight passed (22 inputs).\n"
    assert result.stderr == ""


def test_missing_release_inputs_fail_without_echoing_secret_values():
    sentinel = "do-not-echo-this-secret"
    result = _run(
        {
            "ANDROID_KEYSTORE_PASSWORD": sentinel,
            "WINDOWS_PFX_PASSWORD": "",
            "APPLE_TEAM_ID": "",
        }
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == (
        "Missing required release inputs: APPLE_TEAM_ID, WINDOWS_PFX_PASSWORD\n"
    )
    assert sentinel not in result.stderr
