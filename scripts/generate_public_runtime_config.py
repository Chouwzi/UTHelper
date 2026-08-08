"""Generate the public diagnostics config consumed by packaged builds."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPOSITORY_ROOT / "src"))

from diagnostics.release_config import PublicConfigError, generate_public_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate UTHelper's public diagnostics runtime config",
    )
    parser.add_argument("--sentry-dsn", nargs="?", const="", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        generate_public_config(args.output, args.sentry_dsn)
    except PublicConfigError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
