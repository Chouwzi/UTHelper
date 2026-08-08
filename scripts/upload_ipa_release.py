"""Upload one verified IPA to App Store Connect with bounded Transporter."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Sequence


_IPA_NAME = re.compile(r"^UTHelper-[0-9]+\.[0-9]+\.[0-9]+\.ipa$")
_ISSUER_ID = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
_KEY_ID = re.compile(r"^[A-Z0-9]{3,20}$")
_SENSITIVE_ENV_KEYS = frozenset(
    {
        "ANDROID_KEYSTORE_BASE64",
        "ANDROID_KEYSTORE_PASSWORD",
        "ANDROID_KEY_PASSWORD",
        "APPLE_API_PRIVATE_KEY_BASE64",
        "APPLE_CERTIFICATE_P12_BASE64",
        "APPLE_CERTIFICATE_PASSWORD",
        "APPLE_PROVISIONING_PROFILE_BASE64",
        "WINDOWS_PFX_BASE64",
        "WINDOWS_PFX_PASSWORD",
    }
)


class UploadError(RuntimeError):
    pass


def upload_ipa_release(
    *, ipa: Path, api_issuer: str, api_key_id: str, private_keys_dir: Path
) -> None:
    ipa = ipa.resolve()
    private_keys_dir = private_keys_dir.resolve()
    if not ipa.is_file() or not _IPA_NAME.fullmatch(ipa.name):
        raise UploadError("App Store upload IPA is not canonical")
    with ipa.open("rb") as stream:
        if stream.read(4) != b"PK\x03\x04":
            raise UploadError("App Store upload IPA is not a ZIP container")
    if not _ISSUER_ID.fullmatch(api_issuer) or not _KEY_ID.fullmatch(api_key_id):
        raise UploadError("App Store Connect API identity is invalid")
    if private_keys_dir.name != "private_keys" or not private_keys_dir.is_dir():
        raise UploadError("Transporter private_keys directory is invalid")
    private_key = private_keys_dir / f"AuthKey_{api_key_id}.p8"
    if not private_key.is_file():
        raise UploadError("Transporter API private key is missing")
    if sorted(private_keys_dir.glob("AuthKey_*.p8")) != [private_key]:
        raise UploadError("Transporter private_keys must contain exactly one matching key")
    key_text = private_key.read_text(encoding="ascii")
    if not (
        key_text.startswith("-----BEGIN PRIVATE KEY-----\n")
        and key_text.rstrip().endswith("-----END PRIVATE KEY-----")
    ):
        raise UploadError("Transporter API private key format is invalid")
    if os.name == "posix" and stat.S_IMODE(private_key.stat().st_mode) & 0o077:
        raise UploadError("Transporter API private key must use mode 0600")

    command = [
        "xcrun",
        "iTMSTransporter",
        "-m",
        "upload",
        "-apiIssuer",
        api_issuer,
        "-apiKey",
        api_key_id,
        "-assetFile",
        str(ipa),
        "-v",
        "critical",
    ]
    transporter_env = {
        key: value for key, value in os.environ.items() if key not in _SENSITIVE_ENV_KEYS
    }
    transporter_env["ITMS_PRIVATE_KEYS_DIR"] = str(private_keys_dir)
    try:
        completed = subprocess.run(
            command,
            cwd=private_keys_dir.parent,
            env=transporter_env,
            capture_output=True,
            text=True,
            timeout=1_200,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UploadError("App Store Connect rejected the IPA upload") from exc
    if (
        completed.returncode != 0
        or "ERROR" in completed.stdout.upper()
        or "ERROR" in completed.stderr.upper()
    ):
        raise UploadError("App Store Connect rejected the IPA upload")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ipa", type=Path, required=True)
    parser.add_argument("--api-issuer", required=True)
    parser.add_argument("--api-key-id", required=True)
    parser.add_argument("--private-keys-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    upload_ipa_release(
        ipa=args.ipa,
        api_issuer=args.api_issuer,
        api_key_id=args.api_key_id,
        private_keys_dir=args.private_keys_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
