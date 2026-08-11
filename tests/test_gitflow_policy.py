from scripts.github_branch_policy import (
    REQUIRED_CHECKS,
    protected_branches_ruleset,
    repository_settings,
)
from scripts.validate_gitflow_pr import validate_pull_request


def test_main_accepts_only_release_promotion_or_hotfixes():
    for head in ("develop", "release/2.3.0", "hotfix/2.2.12"):
        assert validate_pull_request("main", head) is None

    for head in ("feature/calendar", "codex/cleanup", "main", "release/"):
        assert validate_pull_request("main", head) is not None


def test_develop_accepts_topics_and_main_back_sync():
    for head in (
        "main",
        "feature/calendar",
        "bugfix/upload",
        "hotfix/2.2.12",
        "release/2.3.0",
        "codex/repo-hygiene",
        "dependabot/pip/flet-1.0",
    ):
        assert validate_pull_request("develop", head) is None

    for head in ("unknown", "feature/", "master"):
        assert validate_pull_request("develop", head) is not None


def test_repository_settings_enforce_merge_commits_and_cleanup():
    assert repository_settings() == {
        "allow_merge_commit": True,
        "allow_squash_merge": False,
        "allow_rebase_merge": False,
        "delete_branch_on_merge": True,
    }


def test_ruleset_protects_both_long_lived_branches_without_linear_history():
    policy = protected_branches_ruleset(owner_actor_id=106900882)
    rule_types = [rule["type"] for rule in policy["rules"]]

    assert policy["conditions"]["ref_name"]["include"] == [
        "refs/heads/main",
        "refs/heads/develop",
    ]
    assert "required_linear_history" not in rule_types
    assert "deletion" in rule_types
    assert "non_fast_forward" in rule_types

    pull_request = next(
        rule for rule in policy["rules"] if rule["type"] == "pull_request"
    )
    assert pull_request["parameters"]["allowed_merge_methods"] == ["merge"]
    assert pull_request["parameters"]["required_approving_review_count"] == 1
    assert pull_request["parameters"]["require_code_owner_review"] is True
    assert pull_request["parameters"]["required_review_thread_resolution"] is True

    checks = next(
        rule for rule in policy["rules"] if rule["type"] == "required_status_checks"
    )
    assert checks["parameters"]["strict_required_status_checks_policy"] is True
    assert [item["context"] for item in checks["parameters"]["required_status_checks"]] == list(REQUIRED_CHECKS)
