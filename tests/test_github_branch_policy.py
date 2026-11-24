from __future__ import annotations

import subprocess
from typing import Any

from scripts import github_branch_policy
from scripts.github_branch_policy import (
    REQUIRED_CHECKS,
    GhApi,
    audit_policy,
    protected_branches_ruleset,
    repository_settings,
)


def test_branch_policy_requires_quiz_flutter_check_and_unattributed_extra_approval() -> None:
    assert "📱 Quiz Flutter extension" in REQUIRED_CHECKS
    ruleset = protected_branches_ruleset(owner_actor_id=106900882)
    pr_rule = next(rule for rule in ruleset["rules"] if rule["type"] == "pull_request")
    assert pr_rule["parameters"]["require_extra_approval_for_unattributed_changes"] is True

    checks_rule = next(
        rule for rule in ruleset["rules"] if rule["type"] == "required_status_checks"
    )
    contexts = [
        entry["context"]
        for entry in checks_rule["parameters"]["required_status_checks"]
    ]
    assert "📱 Quiz Flutter extension" in contexts


def test_audit_policy_matches_live_github_ruleset_shape(monkeypatch) -> None:
    owner_id = 106900882
    expected_ruleset = {"id": 77, **protected_branches_ruleset(owner_id)}

    def fake_request(self, method: str, path: str, payload: dict[str, Any] | None = None):
        assert method == "GET"
        assert payload is None
        if path == f"repos/{self.repository}":
            return repository_settings()
        if path == f"repos/{self.repository}/rulesets":
            return [{"id": 77, "name": github_branch_policy.RULESET_NAME}]
        if path == f"repos/{self.repository}/rulesets/77":
            return expected_ruleset
        raise AssertionError(path)

    monkeypatch.setattr(GhApi, "request", fake_request)
    assert audit_policy(GhApi(), owner_id) == []


def test_gh_api_passes_devnull_stdin_when_payload_is_none(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr(github_branch_policy.subprocess, "run", fake_run)
    assert GhApi().request("GET", "repos/Chouwzi/UTHelper") == {"ok": True}
    assert captured["kwargs"]["stdin"] == subprocess.DEVNULL
