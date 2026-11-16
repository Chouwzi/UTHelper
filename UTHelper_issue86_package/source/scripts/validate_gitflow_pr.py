"""Validate pull-request branch direction for UTHelper's Gitflow policy."""

from __future__ import annotations

import argparse


DEVELOP_PREFIXES = (
    "feature/",
    "bugfix/",
    "fix/",
    "hotfix/",
    "release/",
    "docs/",
    "chore/",
    "refactor/",
    "perf/",
    "test/",
    "ci/",
    "build/",
    "codex/",
    "dependabot/",
)
MAIN_PREFIXES = ("release/", "hotfix/")


def validate_pull_request(base: str, head: str) -> str | None:
    """Return a failure reason, or ``None`` when the direction is allowed."""
    if not base or not head:
        return "pull-request base and head must be non-empty"

    if base == "main":
        if head == "develop" or any(
            head.startswith(prefix) and len(head) > len(prefix)
            for prefix in MAIN_PREFIXES
        ):
            return None
        return "main accepts only develop, release/*, or hotfix/*"

    if base == "develop":
        if head == "main" or any(
            head.startswith(prefix) and len(head) > len(prefix)
            for prefix in DEVELOP_PREFIXES
        ):
            return None
        return "develop accepts main back-sync or an approved topic branch prefix"

    return f"unsupported protected base branch: {base}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.event_name != "pull_request":
        print("Gitflow direction check skipped for non-PR event")
        return 0

    failure = validate_pull_request(args.base, args.head)
    if failure:
        print(f"Gitflow policy rejected {args.head!r} -> {args.base!r}: {failure}")
        return 1

    print(f"Gitflow policy accepted {args.head!r} -> {args.base!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
