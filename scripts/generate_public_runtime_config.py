"""Generate the public diagnostics config consumed by packaged builds."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPOSITORY_ROOT / "src"))

from diagnostics.release_config import PublicConfigError, generate_public_config

try:
    from scripts.release_metadata import (
        ReleaseMetadataError,
        write_distribution_channel,
    )
except ModuleNotFoundError:  # Direct ``python scripts/generate_public_runtime_config.py``.
    from release_metadata import (
        ReleaseMetadataError,
        write_distribution_channel,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate UTHelper's public diagnostics runtime config",
    )
    parser.add_argument("--sentry-dsn", nargs="?", const="", default="")
    parser.add_argument("--require-configured", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--distribution-channel", default="")
    parser.add_argument("--distribution-output", type=Path)
    parser.add_argument("--android-package-id", default="")
    parser.add_argument("--store-product-id", default="")
    parser.add_argument("--app-store-id", default="")
    args = parser.parse_args(argv)
    if args.require_configured and not args.sentry_dsn.strip():
        parser.error("a configured Sentry DSN is required")
    channel_value = args.distribution_channel.strip()
    if bool(channel_value) != (args.distribution_output is not None):
        parser.error(
            "--distribution-channel and --distribution-output must be provided together"
        )
    try:
        generate_public_config(args.output, args.sentry_dsn)
        if args.distribution_output is not None:
            write_distribution_channel(
                args.distribution_output,
                channel_value,
                android_package_id=args.android_package_id.strip() or None,
                store_product_id=args.store_product_id.strip() or None,
                app_store_id=args.app_store_id.strip() or None,
            )
    except (PublicConfigError, ReleaseMetadataError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
