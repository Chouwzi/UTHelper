"""Fail fast when trusted-release credentials are not configured."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping


REQUIRED_RELEASE_INPUTS = (
    "ANDROID_KEYSTORE_BASE64",
    "ANDROID_KEYSTORE_PASSWORD",
    "ANDROID_KEY_PASSWORD",
    "ANDROID_KEY_ALIAS",
    "ANDROID_SIGNING_CERT_SHA256",
    "WINDOWS_PFX_BASE64",
    "WINDOWS_PFX_PASSWORD",
    "WINDOWS_SIGNING_CERT_SHA256",
    "WINDOWS_SIGNER_SUBJECT",
    "WINDOWS_TIMESTAMP_URL",
    "WIX_EULA_ACCEPTED",
    "SENTRY_DSN",
)


def missing_release_inputs(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Return required input names that are absent without inspecting values."""
    return tuple(name for name in REQUIRED_RELEASE_INPUTS if not environ.get(name))


def main() -> int:
    missing = missing_release_inputs(os.environ)
    if missing:
        print(
            f"Missing required release inputs: {', '.join(sorted(missing))}",
            file=sys.stderr,
        )
        return 2
    print(f"Release credential preflight passed ({len(REQUIRED_RELEASE_INPUTS)} inputs).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
