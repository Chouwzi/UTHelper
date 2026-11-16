"""Audit or apply the protected Gitflow branch policy through ``gh api``."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from typing import Any


REPOSITORY = "Chouwzi/UTHelper"
RULESET_NAME = "Protected main and develop"
REQUEST_TIMEOUT_SECONDS = 30
REQUIRED_CHECKS = (
    "🌿 Gitflow policy",
    "Private diagnostics",
    "🔍 Lint",
    "🧪 Test (Python 3.12)",
    "🧪 Test (Python 3.13)",
    "🧪 Test (Python 3.14)",
    "🔐 Dependency review",
    "🔒 Security (core)",
    "🔒 Security (android)",
    "🔒 Security (windows)",
)


class PolicyError(RuntimeError):
    """Raised when remote governance cannot be verified safely."""


@dataclass(frozen=True)
class GhApi:
    repository: str = REPOSITORY

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        command = ["gh", "api", "--method", method, path]
        encoded = None
        if payload is not None:
            command.extend(("--input", "-"))
            encoded = json.dumps(payload, separators=(",", ":"))
        try:
            result = subprocess.run(
                command,
                input=encoded,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=REQUEST_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PolicyError(f"gh api failed before receiving a response: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or "unknown gh api error"
            raise PolicyError(f"gh api {method} {path} failed: {detail}")
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)


def repository_settings() -> dict[str, bool]:
    return {
        "allow_merge_commit": True,
        "allow_squash_merge": False,
        "allow_rebase_merge": False,
        "delete_branch_on_merge": True,
    }


def protected_branches_ruleset(owner_actor_id: int) -> dict[str, Any]:
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [
            {
                "actor_id": owner_actor_id,
                "actor_type": "User",
                "bypass_mode": "pull_request",
            }
        ],
        "conditions": {
            "ref_name": {
                "exclude": [],
                "include": ["refs/heads/main", "refs/heads/develop"],
            }
        },
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 1,
                    "dismiss_stale_reviews_on_push": True,
                    "required_reviewers": [],
                    "require_code_owner_review": True,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": True,
                    "allowed_merge_methods": ["merge"],
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "do_not_enforce_on_create": True,
                    "required_status_checks": [
                        {"context": context} for context in REQUIRED_CHECKS
                    ],
                },
            },
        ],
    }


def _find_ruleset(client: GhApi) -> dict[str, Any]:
    summaries = client.request("GET", f"repos/{client.repository}/rulesets") or []
    matches = [item for item in summaries if item.get("name") == RULESET_NAME]
    if len(matches) != 1:
        raise PolicyError(f"expected one {RULESET_NAME!r} ruleset, found {len(matches)}")
    return client.request(
        "GET", f"repos/{client.repository}/rulesets/{matches[0]['id']}"
    )


def apply_policy(client: GhApi, owner_actor_id: int) -> None:
    current = _find_ruleset(client)
    client.request("PATCH", f"repos/{client.repository}", repository_settings())
    client.request(
        "PUT",
        f"repos/{client.repository}/rulesets/{current['id']}",
        protected_branches_ruleset(owner_actor_id),
    )


def audit_policy(client: GhApi, owner_actor_id: int) -> list[str]:
    failures: list[str] = []
    repository = client.request("GET", f"repos/{client.repository}")
    for key, expected in repository_settings().items():
        if repository.get(key) != expected:
            failures.append(f"repository setting {key} is {repository.get(key)!r}")

    actual = _find_ruleset(client)
    expected = protected_branches_ruleset(owner_actor_id)
    for key in ("name", "target", "enforcement", "bypass_actors", "conditions", "rules"):
        if actual.get(key) != expected[key]:
            failures.append(f"ruleset field {key} differs from policy")
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("audit", "apply"))
    parser.add_argument("--owner-actor-id", type=int, required=True)
    parser.add_argument("--confirm-repository")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = GhApi()
    if args.mode == "apply":
        if args.confirm_repository != REPOSITORY:
            raise PolicyError(
                f"apply requires --confirm-repository {REPOSITORY}"
            )
        apply_policy(client, args.owner_actor_id)

    failures = audit_policy(client, args.owner_actor_id)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print(f"Gitflow repository policy matches {REPOSITORY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
